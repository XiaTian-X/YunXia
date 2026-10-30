"""Validated, offline agreement catalog. Data never selects executable Python."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

REFERENCE_KEYS = {'agreement_id', 'version', 'text_revision', 'sha256'}
CAPABILITIES = {'inspect', 'draft', 'adopt', 'verify'}


class CatalogError(Exception):
    def __init__(self, code, paths=None):
        self.code, self.paths = code, paths or []
        super().__init__(code)


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def segment(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value)) and value not in ('.', '..')


def validate_reference(ref):
    if not isinstance(ref, dict) or set(ref) != REFERENCE_KEYS:
        raise CatalogError('AGREEMENT_REFERENCE_FORMAT')
    if not all(segment(ref.get(k)) for k in ('agreement_id', 'version', 'text_revision')) or not isinstance(ref.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', ref['sha256']):
        raise CatalogError('AGREEMENT_REFERENCE_FORMAT')
    return ref


def reference(manifest):
    return validate_reference({k: manifest.get(k) for k in REFERENCE_KEYS})


def safe_path(root, rel):
    if not isinstance(rel, str) or not rel or '\\' in rel or any(ord(c) < 32 for c in rel):
        raise CatalogError('CATALOG_PATH_INVALID')
    path = PurePosixPath(rel)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != rel or rel == '.':
        raise CatalogError('CATALOG_PATH_INVALID')
    target = root
    if root.is_symlink():
        raise CatalogError('CATALOG_PATH_INVALID')
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise CatalogError('CATALOG_PATH_INVALID')
    return target


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CatalogError('CATALOG_FORMAT')
            result[key] = value
        return result
    try:
        def invalid_constant(_):
            raise CatalogError('CATALOG_FORMAT')
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique, parse_constant=invalid_constant)
    except (OSError, ValueError):
        raise CatalogError('CATALOG_FORMAT') from None


class Catalog:
    def __init__(self, root):
        self.root = Path(root)
        self.entries = []
        self.paths = {'catalog.json'}
        index = read_json(safe_path(self.root, 'catalog.json'))
        if not isinstance(index, dict) or type(index.get('catalog_version')) is not int or index['catalog_version'] != 1 or not isinstance(index.get('agreements'), list):
            raise CatalogError('CATALOG_FORMAT')
        ids, keys = set(), set()
        self.recommended = index.get('recommended')
        for family_path in index['agreements']:
            family = read_json(safe_path(self.root, family_path))
            self.paths.add(family_path)
            if not isinstance(family, dict) or not segment(family.get('agreement_id')) or family['agreement_id'] in ids or not isinstance(family.get('versions'), list) or not family['versions']:
                raise CatalogError('CATALOG_FORMAT')
            ids.add(family['agreement_id'])
            for manifest_path in family['versions']:
                path = safe_path(self.root, manifest_path)
                manifest = read_json(path)
                self.paths.add(manifest_path)
                if not isinstance(manifest, dict):
                    raise CatalogError('CATALOG_FORMAT')
                ref = reference(manifest)
                key = tuple(ref[k] for k in ('agreement_id', 'version', 'text_revision'))
                if key in keys or ref['agreement_id'] != family['agreement_id']:
                    raise CatalogError('DUPLICATE_AGREEMENT_VERSION')
                keys.add(key)
                if type(manifest.get('manifest_version')) is not int or manifest['manifest_version'] != 1 or not all(isinstance(manifest.get(k), str) and manifest[k].strip() for k in ('display_name', 'kind', 'status')):
                    raise CatalogError('CATALOG_FORMAT')
                capabilities = manifest.get('capabilities')
                if not isinstance(capabilities, list) or 'inspect' not in capabilities or any(not isinstance(c, str) or c not in CAPABILITIES for c in capabilities) or len(set(capabilities)) != len(capabilities):
                    raise CatalogError('CATALOG_FORMAT')
                renderer = manifest.get('renderer')
                if renderer is not None and not isinstance(renderer, str):
                    raise CatalogError('CATALOG_FORMAT')
                executable = set(capabilities) - {'inspect'}
                if executable and (renderer != 'scope-license-v1' or manifest['kind'] != 'license'):
                    raise CatalogError('CATALOG_WORKFLOW_UNSUPPORTED')
                output = manifest.get('output_name')
                if (executable or output is not None) and (not isinstance(output, str) or not re.fullmatch(r'LICENSE\.[A-Za-z0-9][A-Za-z0-9._-]*\.txt', output)):
                    raise CatalogError('CATALOG_OUTPUT_INVALID')
                text_path = safe_path(path.parent, manifest.get('text_path'))
                try:
                    body = text_path.read_bytes()
                    body.decode('utf-8')
                except (OSError, UnicodeError):
                    raise CatalogError('CATALOG_TEXT_INVALID') from None
                if not body or sha256(body) != ref['sha256']:
                    raise CatalogError('AGREEMENT_TEXT_HASH_MISMATCH')
                self.paths.add(text_path.relative_to(self.root).as_posix())
                self.entries.append(dict(manifest, _text=body, _manifest=manifest_path,
                                         _text_path=text_path.relative_to(self.root).as_posix()))
        if self.recommended is not None:
            self.resolve(validate_reference(self.recommended))

    def select(self, agreement_id, version, revision):
        matches = [m for m in self.entries if (m['agreement_id'], m['version'], m['text_revision']) == (agreement_id, version, revision)]
        if len(matches) != 1:
            raise CatalogError('AGREEMENT_NOT_AVAILABLE')
        return matches[0]

    def resolve(self, ref):
        validate_reference(ref)
        entry = self.select(ref['agreement_id'], ref['version'], ref['text_revision'])
        if entry['sha256'] != ref['sha256']:
            raise CatalogError('AGREEMENT_PIN_MISMATCH')
        return entry

    def describe(self):
        return {'catalog_version': 1, 'recommended': self.recommended,
                'versions': [{k: v for k, v in m.items() if not k.startswith('_')} for m in self.entries]}
