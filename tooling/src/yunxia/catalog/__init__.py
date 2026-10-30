"""Read fixed releases; development metadata never grants executable capabilities."""
from pathlib import Path, PurePosixPath
import os
import re
import json
from datetime import date
from .legacy import Catalog as LegacyCatalog, CatalogError, read_json, safe_path, sha256, segment

REFERENCE_KEYS = {'agreement_id', 'version', 'sha256'}


def canonical_sha256(value):
    return sha256((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode())


def valid_relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value):
        return False
    p = PurePosixPath(value)
    return not p.is_absolute() and '..' not in p.parts and p.as_posix() == value and value != '.'


def validate_review_record(review):
    required = {'source_sha256', 'source_snapshot', 'reviewer', 'date', 'decision', 'basis', 'open_issues'}
    if not isinstance(review, dict) or not required <= review.keys():
        raise CatalogError('REVIEW_FORMAT')
    if not all(isinstance(review[k], str) and review[k].strip() for k in ('reviewer', 'basis', 'date')):
        raise CatalogError('REVIEW_FORMAT')
    try:
        date.fromisoformat(review['date'])
    except ValueError:
        raise CatalogError('REVIEW_FORMAT') from None
    issues = review['open_issues']
    if review['decision'] not in ('approved', 'changes-required') or not isinstance(issues, list) or not all(isinstance(x, str) and x.strip() for x in issues):
        raise CatalogError('REVIEW_FORMAT')
    if review['decision'] == 'approved' and issues:
        raise CatalogError('REVIEW_HAS_OPEN_ISSUES')
    snapshot = review['source_snapshot']
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('agreement'), dict) or not isinstance(snapshot.get('files'), dict):
        raise CatalogError('REVIEW_FORMAT')
    agreement, files = snapshot['agreement'], snapshot['files']
    if not all(isinstance(agreement.get(k), str) and agreement[k].strip() for k in ('agreement_id', 'name', 'kind', 'purpose', 'target_version')):
        raise CatalogError('REVIEW_FORMAT')
    if not segment(agreement['agreement_id']) or not segment(agreement['target_version']):
        raise CatalogError('REVIEW_FORMAT')
    if agreement.get('base') is not None:
        validate_reference(agreement['base'])
        if agreement['base']['agreement_id'] != agreement['agreement_id']:
            raise CatalogError('REVIEW_FORMAT')
    if not {'text.txt', 'README.md', 'design.md', 'review.md', 'adoption.md'} <= files.keys():
        raise CatalogError('REVIEW_FORMAT')
    if any(not valid_relative_path(k) or not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for k, v in files.items()):
        raise CatalogError('REVIEW_FORMAT')
    if review['source_sha256'] != canonical_sha256(snapshot):
        raise CatalogError('REVIEW_FORMAT')
    return review


def child_directories(root, relative):
    """Do not silently hide an incomplete record or follow a directory symlink."""
    parent = safe_path(root, relative)
    if not parent.exists():
        return []
    if not parent.is_dir():
        raise CatalogError('CATALOG_DIRECTORY_INVALID', [relative])
    result = []
    for p in sorted(parent.iterdir()):
        rel = p.relative_to(root).as_posix()
        if p.name == '.DS_Store' or p.name.startswith(('.new-agreement-', '.yunxia-stage-')):
            continue
        safe_path(root, rel)
        if p.is_file() and p.name == 'README.md':
            continue
        if not p.is_dir() or not segment(p.name):
            raise CatalogError('CATALOG_DIRECTORY_INVALID', [rel])
        result.append(p)
    return result


def validate_reference(ref):
    if not isinstance(ref, dict) or set(ref) not in (REFERENCE_KEYS, REFERENCE_KEYS | {'text_revision'}):
        raise CatalogError('AGREEMENT_REFERENCE_FORMAT')
    if not all(segment(ref[k]) for k in set(ref) - {'sha256'}) or not isinstance(ref['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', ref['sha256']):
        raise CatalogError('AGREEMENT_REFERENCE_FORMAT')
    return ref


def reference(entry):
    keys = REFERENCE_KEYS | ({'text_revision'} if 'text_revision' in entry else set())
    return {k: entry[k] for k in sorted(keys)}


def default_root():
    if os.environ.get('YUNXIA_CATALOG_ROOT'):
        return Path(os.environ['YUNXIA_CATALOG_ROOT'])
    for parent in Path(__file__).resolve().parents:
        if (parent / 'assets/catalog/releases').is_dir():
            return parent / 'assets/catalog'
        if (parent / 'agreements').is_dir() and (parent / 'releases').is_dir():
            return parent
    raise CatalogError('CATALOG_NOT_FOUND')


def support_file():
    return Path(__file__).resolve().parents[1] / 'adapters/support.json'


def supports(path=None):
    data = read_json(path or support_file())
    if not isinstance(data, dict) or type(data.get('format_version')) is not int or data['format_version'] != 1 or not isinstance(data.get('supported'), list):
        raise CatalogError('ADAPTER_REGISTRY_FORMAT')
    seen = set()
    for row in data['supported']:
        if not isinstance(row, dict) or set(row) != {'agreement', 'adapter', 'output_name'}:
            raise CatalogError('ADAPTER_REGISTRY_FORMAT')
        ref = validate_reference(row['agreement'])
        key = tuple(sorted(ref.items()))
        if key in seen or row['adapter'] != 'scope-license-v1' or not isinstance(row['output_name'], str) or not re.fullmatch(r'LICENSE\.[A-Za-z0-9][A-Za-z0-9._-]*\.txt', row['output_name']):
            raise CatalogError('ADAPTER_REGISTRY_FORMAT')
        seen.add(key)
    return data['supported']


class Catalog:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else default_root()
        self.paths, self.entries, self.recommended_by_agreement = set(), [], {}
        self.recommended = None  # Only a legacy catalog may define a global recommendation.
        self.legacy = None
        if (self.root / 'catalog.json').is_file():
            self.legacy = LegacyCatalog(self.root)
            self.entries, self.paths, self.recommended = self.legacy.entries, self.legacy.paths, self.legacy.recommended
            return
        if not self.root.is_dir() or self.root.is_symlink():
            raise CatalogError('CATALOG_PATH_INVALID')
        identities, aliases = set(), set()
        registry_path = safe_path(self.root, 'tooling/src/yunxia/adapters/support.json')
        bundled_registry = self.root.parent.parent / 'scripts/yunxia/adapters/support.json'
        if registry_path.exists():
            registry = supports(registry_path)
        elif self.root.name == 'catalog' and bundled_registry.exists():
            registry = supports(bundled_registry)
        else:
            registry = []
        records = []
        for family in child_directories(self.root, 'releases'):
            for version in child_directories(self.root, family.relative_to(self.root).as_posix()):
                record = safe_path(self.root, (version/'release.json').relative_to(self.root).as_posix())
                if not record.is_file():
                    raise CatalogError('RELEASE_RECORD_MISSING', [record.relative_to(self.root).as_posix()])
                records.append(record)
        for p in records:
            rel = p.relative_to(self.root).as_posix()
            m = read_json(safe_path(self.root, rel))
            if not isinstance(m, dict) or type(m.get('format_version')) is not int or m['format_version'] != 1:
                raise CatalogError('RELEASE_FORMAT', [rel])
            ref = validate_reference({k: m.get(k) for k in REFERENCE_KEYS})
            if p.parent.name != ref['version'] or p.parent.parent.name != ref['agreement_id']:
                raise CatalogError('RELEASE_IDENTITY_MISMATCH', [rel])
            identity = (ref['agreement_id'], ref['version'])
            if identity in identities:
                raise CatalogError('DUPLICATE_AGREEMENT_VERSION')
            identities.add(identity)
            if not all(isinstance(m.get(k), str) and m[k].strip() for k in ('display_name', 'kind')) or m.get('status') not in ('imported-draft', 'candidate', 'released'):
                raise CatalogError('RELEASE_FORMAT', [rel])
            if {'renderer','capabilities','output_name'} & set(m):
                raise CatalogError('RELEASE_CONTAINS_ADAPTER_CONFIG',[rel])
            if m['status'] == 'released':
                publication = m.get('publication')
                if not isinstance(publication, dict) or not all(isinstance(publication.get(k), str) and publication[k].strip() for k in ('date', 'channel', 'confirmed_by', 'basis')):
                    raise CatalogError('RELEASE_PUBLICATION_REQUIRED', [rel])
                try:date.fromisoformat(publication['date'])
                except ValueError:raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel]) from None
            elif m.get('publication') is not None:
                raise CatalogError('RELEASE_FORMAT',[rel])
            files = m.get('files')
            if not isinstance(files, dict) or files.get('text.txt') != ref['sha256']:
                raise CatalogError('RELEASE_FORMAT', [rel])
            actual = set()
            for f in p.parent.rglob('*'):
                safe_path(self.root, f.relative_to(self.root).as_posix())
                if f.is_file() and f != p:
                    actual.add(f.relative_to(p.parent).as_posix())
            if actual != set(files):
                raise CatalogError('RELEASE_FILE_SET_MISMATCH', [rel])
            for name, expected in files.items():
                path = safe_path(self.root, (p.parent / name).relative_to(self.root).as_posix())
                if not isinstance(expected, str) or sha256(path.read_bytes()) != expected:
                    raise CatalogError('AGREEMENT_TEXT_HASH_MISMATCH' if name == 'text.txt' else 'RELEASE_FILE_HASH_MISMATCH', [rel])
                self.paths.add(path.relative_to(self.root).as_posix())
            body = (p.parent / 'text.txt').read_bytes()
            try:
                body.decode('utf-8')
            except UnicodeError:
                raise CatalogError('CATALOG_TEXT_INVALID') from None
            if not body.strip():
                raise CatalogError('CATALOG_TEXT_INVALID')
            if m['status'] in ('candidate','released'):
                source=m.get('source')
                if not {'review.json','adoption.md','notes.md'}<=set(files) or not isinstance(source,dict) or not isinstance(source.get('snapshot'),dict):
                    raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
                review=read_json(safe_path(p.parent,'review.json'))
                try:
                    validate_review_record(review)
                except CatalogError:
                    raise CatalogError('RELEASE_REVIEW_INVALID',[rel]) from None
                snapshot=source['snapshot']
                snapshot_sha=sha256((json.dumps(snapshot,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode())
                if not isinstance(review,dict) or review.get('decision')!='approved' or review.get('open_issues')!=[] or review.get('source_snapshot')!=snapshot or review.get('source_sha256')!=snapshot_sha or source.get('source_sha256')!=snapshot_sha:
                    raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
                if not all(isinstance(review.get(k),str) and review[k].strip() for k in ('reviewer','basis','date')):
                    raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
                source_agreement=snapshot.get('agreement');source_files=snapshot.get('files')
                if not isinstance(source_agreement,dict) or not isinstance(source_files,dict) or any(source_files.get(k)!=files[k] for k in ('text.txt','adoption.md')):
                    raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
                if source_agreement.get('agreement_id')!=ref['agreement_id'] or source_agreement.get('kind')!=m['kind'] or source_agreement.get('name')!=m['display_name'] or not segment(source.get('target_version')) or source_agreement.get('target_version')!=source['target_version']:
                    raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
                if m['status']=='released' and ref['version']!=source['target_version']:
                    raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
            self.paths.add(rel)
            old_refs = m.get('legacy_references', [])
            if not isinstance(old_refs, list):
                raise CatalogError('RELEASE_FORMAT')
            for old in old_refs:
                validate_reference(old)
                key = (old['agreement_id'],old['version'],old.get('text_revision'))
                if 'text_revision' not in old or old['sha256'] != ref['sha256'] or old['agreement_id'] != ref['agreement_id'] or key in aliases:
                    raise CatalogError('LEGACY_REFERENCE_INVALID')
                aliases.add(key)
            match = next((x for x in registry if x['agreement'] == ref), None)
            if match and m['kind'] != 'license':
                raise CatalogError('CATALOG_WORKFLOW_UNSUPPORTED')
            package_sha = sha256((json.dumps({f.name:sha256(f.read_bytes()) for f in sorted(p.parent.iterdir()) if f.is_file()},ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode())
            self.entries.append(dict(m, _text=body, _manifest=rel, _package_sha256=package_sha, _text_path=(p.parent/'text.txt').relative_to(self.root).as_posix(),
                                     renderer=match['adapter'] if match else None,
                                     capabilities=['inspect', 'draft', 'adopt', 'verify'] if match else ['inspect'],
                                     output_name=match['output_name'] if match else None))
        agreement_ids = set()
        for family in child_directories(self.root, 'agreements'):
            p = safe_path(self.root, (family/'agreement.json').relative_to(self.root).as_posix())
            if not p.is_file():
                raise CatalogError('AGREEMENT_RECORD_MISSING', [p.relative_to(self.root).as_posix()])
            rel = p.relative_to(self.root).as_posix()
            meta = read_json(safe_path(self.root, rel))
            if not isinstance(meta, dict) or not {'agreement_id','name','kind','recommended'} <= meta.keys() or meta.get('agreement_id') != p.parent.name or not all(isinstance(meta.get(k),str) and meta[k].strip() for k in ('name','kind')):
                raise CatalogError('AGREEMENT_FORMAT', [rel])
            agreement_ids.add(meta['agreement_id'])
            recommendation = meta.get('recommended')
            if recommendation is not None:
                validate_reference(recommendation)
                if recommendation['agreement_id'] != meta['agreement_id']:
                    raise CatalogError('RECOMMENDATION_AGREEMENT_MISMATCH', [rel])
            if recommendation is not None and self.resolve(recommendation)['status'] != 'released':
                raise CatalogError('RECOMMENDATION_REQUIRES_RELEASE')
            if meta.get('maintenance') == 'retired' and recommendation is not None:
                raise CatalogError('RETIRED_AGREEMENT_RECOMMENDED', [rel])
            self.recommended_by_agreement[p.parent.name] = recommendation
            self.paths.add(rel)
        if any(e['agreement_id'] not in agreement_ids for e in self.entries):
            raise CatalogError('RELEASE_AGREEMENT_NOT_REGISTERED')
        for supported in registry:
            self.resolve(supported['agreement'])

    def select(self, agreement_id, version, revision=None):
        if self.legacy:
            return self.legacy.select(agreement_id, version, revision)
        matches = []
        for e in self.entries:
            if revision is None and (e['agreement_id'], e['version']) == (agreement_id, version):
                matches.append(e)
            if revision is not None:
                for old in e.get('legacy_references', []):
                    if (old['agreement_id'], old['version'], old.get('text_revision')) == (agreement_id, version, revision):
                        matches.append(dict(e, **old))
        if len(matches) != 1:
            raise CatalogError('AGREEMENT_NOT_AVAILABLE')
        return matches[0]

    def resolve(self, ref):
        validate_reference(ref)
        entry = self.select(ref['agreement_id'], ref['version'], ref.get('text_revision'))
        if entry['sha256'] != ref['sha256']:
            raise CatalogError('AGREEMENT_PIN_MISMATCH')
        return entry

    def describe(self):
        return {'catalog_version': 2, 'recommended_by_agreement': self.recommended_by_agreement,
                'versions': [dict({k: v for k, v in e.items() if not k.startswith('_')}, package_sha256=e.get('_package_sha256')) for e in self.entries]}
