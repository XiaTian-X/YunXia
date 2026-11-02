#!/usr/bin/env python3
"""Local YunXia material workflow. Python 3.11+; no network or target execution."""
import argparse
import base64
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tomllib
import zipfile

from ..catalog import Catalog, reference, validate_reference, default_root
from ..common import CatalogError, sha256 as digest, json_bytes as encoded, canonical_sha256 as fingerprint
from ..common import DuplicateJSONKey, parse_json, valid_relative_path, atomic_write, atomic_create, exclusive_guard
from .. import VERSION

STATE_VERSION = 3
UNIT = 'main'
OUTPUT_BASE = ''
DRAFT_BASE = '.yunxia/draft/'
STATE = '.yunxia/state.json'
JOURNAL = '.yunxia/transaction.json'
IGNORE_DIRS = {'.git', '.yunxia', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache'}
PRIVATE_DIRS = {'.ssh', '.aws', '.kube', '.gnupg'}
SECRET_PATTERNS = ('.env*', '*.pem', '*.key', '*.p12', '*.pfx', '*.jks', '*.keystore',
                   'id_rsa', 'id_ecdsa', 'id_ed25519', '.npmrc', '.pypirc', '.netrc',
                   'credentials.json', 'credentials-*.json', 'client_secret*.json', 'service-account*.json')
OUTPUT_NAMES = {'LICENSE.txt', 'PROJECT-RIGHTS.md', 'THIRD-PARTY.md', 'AI-USAGE.md'}
MAX_FILE = 64 * 1024 * 1024
MAX_TEXT = 2 * 1024 * 1024
MAX_FILES = 20000
LEGAL_LIMIT = '仅检查观察、用户确认记录和文件一致性；不认证权属、许可兼容或法律合规。'


class WorkflowError(Exception):
    def __init__(self, code, paths=None):
        self.code, self.paths = code, paths or []
        super().__init__(code)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_json(path):
    try:
        return parse_json(path.read_text(encoding='utf-8'))
    except DuplicateJSONKey:
        raise WorkflowError('DUPLICATE_JSON_KEY') from None
    except (ValueError, OSError):
        raise WorkflowError('INVALID_JSON') from None


def relative(value):
    if not valid_relative_path(value):
        raise WorkflowError('UNSAFE_PATH')
    return value


def local(root, rel):
    relative(rel)
    path = root
    for component in PurePosixPath(rel).parts:
        path = path / component
        if path.is_symlink():
            raise WorkflowError('SYMLINK_PATH', [rel])
    return path


def current_bytes(root, rel):
    path = local(root, rel)
    if not path.exists():
        return None
    if not path.is_file():
        raise WorkflowError('NOT_REGULAR_FILE', [rel])
    if path.stat().st_size > MAX_FILE:
        raise WorkflowError('FILE_TOO_LARGE', [rel])
    return path.read_bytes()


def atomic(path, content, private=False, mode=None):
    if mode is None:
        mode = 0o600 if private else 0o644
    atomic_write(path, content, mode=mode)


def current_image(root, rel):
    path = local(root, rel)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise WorkflowError('NOT_REGULAR_FILE', [rel])
        if before.st_size > MAX_FILE:
            raise WorkflowError('FILE_TOO_LARGE', [rel])
        content = stream.read(MAX_FILE + 1)
        after = os.fstat(stream.fileno())
    if len(content) > MAX_FILE:
        raise WorkflowError('FILE_TOO_LARGE', [rel])
    if (before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise WorkflowError('CONCURRENT_EDIT', [rel])
    return content, stat.S_IMODE(after.st_mode)


def private_dir(root):
    path = local(root, '.yunxia')
    path.mkdir(mode=0o700, exist_ok=True)
    # The directory is private working state, never a public artifact.
    os.chmod(path, 0o700)
    ignore = local(root, '.yunxia/.gitignore')
    if not ignore.exists():
        atomic(ignore, b'*\n', private=True)


def output_path(rel):
    relative(rel)
    name = rel.removeprefix(DRAFT_BASE)
    if name == rel and OUTPUT_BASE:
        if not rel.startswith(OUTPUT_BASE):
            return False
        name = rel.removeprefix(OUTPUT_BASE)
    return name in OUTPUT_NAMES or bool(re.fullmatch(r'LICENSE\.[A-Za-z0-9][A-Za-z0-9._-]*\.txt', name))


def catalog():
    return Catalog(default_root())


def selected(state, capability=None):
    ref = state.get('selected_agreement')
    if ref is None:
        raise WorkflowError('AGREEMENT_NOT_SELECTED')
    entry = catalog().resolve(ref)
    if capability and (capability not in entry['capabilities'] or entry.get('renderer') != 'scope-license-v1' or entry['kind'] != 'license'):
        raise WorkflowError('UNSUPPORTED_AGREEMENT_WORKFLOW')
    return entry


def adoption_valid(state):
    a = state.get('adoption', {})
    return (state.get('selected_agreement') is not None and a.get('agreement') == state['selected_agreement']
            and a.get('snapshot_id') == fingerprint(state['snapshot']) and a.get('context_id') == context_id(state)
            and (a.get('accept_draft') is True or selected(state)['status'] == 'released'))


@contextmanager
def lock(root, recovery=False):
    private_dir(root)
    path = local(root, '.yunxia/lock.json')
    with exclusive_guard(local(root, '.yunxia/lock.guard'), error_type=WorkflowError,
                         code='LOCKED', paths=['.yunxia/lock.json']):
        if recovery and path.exists():
            old = read_json(path)
            if not isinstance(old, dict) or type(old.get('pid')) is not int or old['pid'] <= 0:
                raise WorkflowError('INVALID_LOCK')
            try:
                os.kill(old['pid'], 0)
            except ProcessLookupError:
                path.unlink()
            except PermissionError:
                pass
        try:
            atomic_create(path, encoded({'pid': os.getpid()}), mode=0o600)
        except FileExistsError:
            raise WorkflowError('LOCKED', ['.yunxia/lock.json']) from None
        try:
            yield
        finally:
            path.unlink(missing_ok=True)


def restore(root):
    journal_path = local(root, JOURNAL)
    if not journal_path.exists():
        return False
    journal = read_json(journal_path)
    if not isinstance(journal, dict) or not isinstance(journal.get('entries'), list):
        raise WorkflowError('INVALID_JOURNAL')
    def valid_mode(value):
        return type(value) is int and 0 <= value <= 0o7777
    entries, seen = [], set()
    for item in journal['entries']:
        fields = {'path', 'before', 'after'}
        if not isinstance(item, dict) or set(item) not in (fields, fields | {'before_mode', 'after_mode'}):
            raise WorkflowError('INVALID_JOURNAL')
        rel = relative(item['path'])
        if rel in seen or (rel != STATE and not output_path(rel)):
            raise WorkflowError('INVALID_JOURNAL')
        seen.add(rel)
        try:
            before = None if item['before'] is None else base64.b64decode(item['before'], validate=True)
            after = base64.b64decode(item['after'], validate=True)
        except (ValueError, TypeError):
            raise WorkflowError('INVALID_JOURNAL') from None
        modes = 'before_mode' in item
        before_mode, after_mode = item.get('before_mode'), item.get('after_mode')
        if modes:
            valid_before = before_mode is None if before is None else valid_mode(before_mode)
            if not valid_before or not valid_mode(after_mode):
                raise WorkflowError('INVALID_JOURNAL')
        entries.append((rel, before, after, before_mode, after_mode, modes))

    def checked_image(entry):
        rel, before, after, before_mode, after_mode, modes = entry
        try:
            current = current_image(root, rel)
        except WorkflowError as error:
            if error.code == 'CONCURRENT_EDIT':
                raise WorkflowError('RECOVERY_CONFLICT', [rel]) from None
            raise
        matches = current in ((before, before_mode), (after, after_mode)) if modes else current[0] in (before, after)
        if not matches:
            raise WorkflowError('RECOVERY_CONFLICT', [rel])
        return current

    for entry in entries:
        checked_image(entry)
    for entry in reversed(entries):
        rel, before, after, before_mode, after_mode, modes = entry
        current = checked_image(entry)
        if current[0] == before and (not modes or current[1] == before_mode):
            continue
        path = local(root, rel)
        if before is None:
            path.unlink(missing_ok=True)
        else:
            atomic(path, before, private=rel.startswith('.yunxia/'), mode=before_mode)
    journal_path.unlink()
    return True


def transaction(root, updates, expected=None):
    if expected is not None:
        for rel, before in expected.items():
            if current_bytes(root, rel) != before:
                raise WorkflowError('CONCURRENT_EDIT', [rel])
    entries = []
    for rel, content in updates.items():
        before, before_mode = current_image(root, rel)
        if expected is not None and rel in expected and before != expected[rel]:
            raise WorkflowError('CONCURRENT_EDIT', [rel])
        if before == content:
            continue
        entries.append({'path': rel,
                        'before': None if before is None else base64.b64encode(before).decode(),
                        'after': base64.b64encode(content).decode(),
                        'before_mode': before_mode,
                        'after_mode': 0o600 if rel.startswith('.yunxia/') else 0o644})
    if not entries:
        return []
    atomic(local(root, JOURNAL), encoded({'entries': entries}), private=True)
    try:
        for item in entries:
            before = None if item['before'] is None else base64.b64decode(item['before'])
            if current_image(root, item['path']) != (before, item['before_mode']):
                raise WorkflowError('CONCURRENT_EDIT', [item['path']])
            atomic(local(root, item['path']), base64.b64decode(item['after']),
                   private=item['path'].startswith('.yunxia/'))
    except Exception:
        restore(root)
        raise
    local(root, JOURNAL).unlink()
    return [item['path'] for item in entries]


def fresh():
    return {'state_version': STATE_VERSION, 'unit_id': UNIT, 'tool_version': VERSION, 'snapshot': None,
            'answers': {}, 'profile': {}, 'history': [], 'generations': {}, 'deferred': {},
            'selected_agreement': None, 'managed_outputs': {}}


def configure_unit(unit):
    global UNIT, STATE, JOURNAL, OUTPUT_BASE, DRAFT_BASE
    if not isinstance(unit, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', unit):
        raise WorkflowError('UNIT_ID_INVALID')
    UNIT = unit
    base = '.yunxia/' if unit == 'main' else f'.yunxia/units/{unit}/'
    STATE, JOURNAL = base + 'state.json', base + 'transaction.json'
    OUTPUT_BASE = '' if unit == 'main' else f'licenses/{unit}/'
    DRAFT_BASE = base + 'draft/'


@contextmanager
def using_unit(unit):
    previous = UNIT
    configure_unit(unit)
    try:
        yield
    finally:
        configure_unit(previous)


def validate_scope(paths):
    if not isinstance(paths, list) or not paths or not all(isinstance(p,str) for p in paths) or len(set(paths)) != len(paths):
        raise WorkflowError('SCOPE_FORMAT')
    for p in paths:
        if p == '.':
            continue
        relative(p)
        if any(part.startswith('.') or part in IGNORE_DIRS or part in PRIVATE_DIRS for part in PurePosixPath(p).parts) or any(c in p for c in '*?['):
            raise WorkflowError('SCOPE_PATH_INVALID')


def unit_ids(root):
    ids = ['main'] if local(root, '.yunxia/state.json').exists() else []
    directory = local(root, '.yunxia/units')
    if directory.exists():
        for p in sorted(directory.iterdir()):
            if p.is_symlink():
                raise WorkflowError('SYMLINK_PATH')
            if p.is_dir() and (p/'state.json').exists():
                ids.append(p.name)
    return ids


def other_states(root):
    current = UNIT
    result = []
    for unit in unit_ids(root):
        if unit != current:
            with using_unit(unit):
                result.append(load_state(root))
    return result


def list_units(root):
    units = []
    for unit in unit_ids(root):
        with using_unit(unit):
            state = load_state(root)
            units.append({'id': unit, 'scope': state.get('scope', ['.'] if unit == 'main' else []),
                          'agreement': state['selected_agreement'], 'outputs': sorted(state['managed_outputs'])})
    return {'units': units}


def set_scope(root, paths):
    validate_scope(paths)
    state, before = load_state_with_bytes(root)
    if state.get('scope') == paths:
        return {'changed': False, 'scope': paths}
    state['history'].append({'event':'scope_changed','previous':state.get('scope'),'scope':paths,'at':now()})
    state['scope'] = paths
    transaction(root,{STATE:encoded(state)}, expected={STATE: before})
    return {'changed':True,'scope':paths,'scan_required':True}


def load_state(root):
    return load_state_with_bytes(root)[0]


def load_state_with_bytes(root):
    """Bind edits to the exact state image that was parsed and validated."""
    if local(root, JOURNAL).exists():
        raise WorkflowError('RECOVERY_REQUIRED', [JOURNAL])
    content = current_bytes(root, STATE)
    if content is None:
        return fresh(), None
    try:
        state = parse_json(content.decode('utf-8'))
    except DuplicateJSONKey:
        raise WorkflowError('DUPLICATE_JSON_KEY') from None
    except ValueError:
        raise WorkflowError('INVALID_JSON') from None
    return validate_state(state), content


def validate_state(state):
    if not isinstance(state, dict) or type(state.get('state_version')) is not int or state.get('state_version') != STATE_VERSION:
        raise WorkflowError('UNSUPPORTED_STATE_VERSION')
    if 'scope' in state:
        validate_scope(state['scope'])
    if state.get('unit_id') != UNIT:
        raise WorkflowError('UNIT_STATE_MISMATCH')
    if not {'snapshot', 'tool_version', 'answers', 'profile', 'history', 'generations'} <= state.keys() or not nonempty(state['tool_version']):
        raise WorkflowError('INVALID_STATE')
    if 'selected_agreement' not in state or not isinstance(state.get('managed_outputs'), dict):
        raise WorkflowError('INVALID_STATE')
    if state['selected_agreement'] is not None:
        validate_reference(state['selected_agreement'])
    for rel, value in state['managed_outputs'].items():
        if not output_path(rel) or not isinstance(value, str):
            raise WorkflowError('INVALID_STATE')
    for key in ('answers', 'profile', 'generations'):
        if not isinstance(state.get(key), dict):
            raise WorkflowError('INVALID_STATE')
    if not isinstance(state.get('history'), list):
        raise WorkflowError('INVALID_STATE')
    if not isinstance(state.get('deferred', {}), dict) or set(state.get('deferred', {})) - {'identity', 'delivery', 'adoption'}:
        raise WorkflowError('INVALID_STATE')
    if state.get('snapshot') is not None:
        snap = state['snapshot']
        if not isinstance(snap, dict) or not isinstance(snap.get('files'), dict) or not all(isinstance(snap.get(k), list) for k in ('skipped', 'dependencies', 'coverage_issues')):
            raise WorkflowError('INVALID_STATE')
        if any(not isinstance(v, dict) or not isinstance(v.get('sha256'), str) or v.get('kind') not in ('file', 'license-or-notice', 'dependency-manifest', 'media-or-font', 'opaque-container') or not isinstance(v.get('observations'), list) for v in snap['files'].values()):
            raise WorkflowError('INVALID_STATE')
        for rel in snap['files']:
            relative(rel)
    for key, fields in (('identity', {'project_name', 'licensor', 'role'}), ('delivery', {'audience', 'description'})):
        if key in state['profile']:
            value = state['profile'][key]
            if not isinstance(value, dict) or set(value) != fields or not all(nonempty(v) for v in value.values()):
                raise WorkflowError('INVALID_STATE')
    if state['profile'].get('public_ai_statement') is not None and not nonempty(state['profile']['public_ai_statement']):
        raise WorkflowError('INVALID_STATE')
    for rel, answer in state['answers'].items():
        relative(rel)
        if not isinstance(answer, dict) or not isinstance(answer.get('sha256'), str) or answer.get('disposition') not in ('selected', 'preserve', 'exclude', 'pending') or not isinstance(answer.get('notice_hashes'), dict):
            raise WorkflowError('INVALID_STATE')
        for notice, value in answer['notice_hashes'].items():
            relative(notice)
            if not isinstance(value, str):
                raise WorkflowError('INVALID_STATE')
    for mode, generation in state['generations'].items():
        if mode not in ('draft', 'adopt') or not isinstance(generation, dict) or not isinstance(generation.get('files'), dict):
            raise WorkflowError('INVALID_STATE')
        if not all(nonempty(generation.get(k)) for k in ('snapshot_id', 'context_id', 'tool_version', 'license_sha256')):
            raise WorkflowError('INVALID_STATE')
        for rel, value in generation['files'].items():
            relative(rel)
            prefix = DRAFT_BASE if mode == 'draft' else OUTPUT_BASE
            if not rel.startswith(prefix) or not output_path(rel) or not isinstance(value, str):
                raise WorkflowError('INVALID_STATE')
        if 'agreement' not in generation:
            raise WorkflowError('INVALID_STATE')
        validate_reference(generation['agreement'])
        adapter = generation.get('adapter')
        if (not isinstance(adapter, dict) or set(adapter) != {'id', 'tool_version', 'sha256'}
                or adapter['id'] != 'scope-license-v1' or not nonempty(adapter['tool_version'])
                or not isinstance(adapter['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', adapter['sha256'])):
            raise WorkflowError('INVALID_STATE')
        prefix = DRAFT_BASE if mode == 'draft' else OUTPUT_BASE
        licenses = [p for p in generation['files'] if p.removeprefix(prefix).startswith('LICENSE')]
        if prefix + 'PROJECT-RIGHTS.md' not in generation['files'] or len(licenses) != 1 or generation['files'][licenses[0]] != generation['license_sha256']:
            raise WorkflowError('INVALID_STATE')
        if any(state['managed_outputs'].get(p) != h for p, h in generation['files'].items()):
            raise WorkflowError('INVALID_STATE')
    if 'adoption' in state:
        value = state['adoption']
        if not isinstance(value, dict) or value.get('authorized') is not True or type(value.get('accept_draft')) is not bool or not all(nonempty(value.get(k)) for k in ('date', 'snapshot_id', 'context_id')):
            raise WorkflowError('INVALID_STATE')
        validate_reference(value.get('agreement'))
    if state['snapshot'] is None and (state['answers'] or state['generations'] or state.get('adoption')):
        raise WorkflowError('INVALID_STATE')
    return state


def select_agreement(root, agreement_id, version):
    state, before = load_state_with_bytes(root)
    entry = catalog().select(agreement_id, version)
    ref = reference(entry)
    changed = state['selected_agreement'] != ref
    if changed:
        state['history'].append({'event': 'agreement_selection', 'at': now(),
                                 'previous': state['selected_agreement'], 'selected': ref})
        state['selected_agreement'] = ref
        transaction(root, {STATE: encoded(state)}, expected={STATE: before})
    return {'selected_agreement': ref, 'changed': changed, 'capabilities': entry['capabilities'],
            'adoption_valid': adoption_valid(state) if state['snapshot'] is not None else False}


def is_license(path):
    name = path.name.lower()
    return name.startswith(('license', 'licence', 'copying', 'notice', 'copyright'))


def kind(path):
    if is_license(path):
        return 'license-or-notice'
    if path.name in ('package.json', 'package-lock.json', 'pyproject.toml', 'Cargo.toml', 'go.mod', 'pom.xml', 'build.gradle', 'build.gradle.kts') or path.name.startswith('requirements'):
        return 'dependency-manifest'
    if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ttf', '.woff', '.woff2', '.mp3', '.mp4'}:
        return 'media-or-font'
    if path.suffix.lower() in {'.zip', '.tar', '.gz', '.jar', '.whl', '.docx', '.pdf'}:
        return 'opaque-container'
    return 'file'


def dependency_hints(rel, content):
    name = PurePosixPath(rel).name
    hints, errors = [], []
    try:
        if name in ('package.json', 'package-lock.json'):
            obj = parse_json(content)
            if not isinstance(obj, dict):
                raise ValueError()
            groups = ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies')
            if name == 'package.json':
                for group in groups:
                    for dep, spec in obj.get(group, {}).items():
                        hints.append({'manifest': rel, 'name': metadata_hint(dep), 'version_hint': metadata_hint(spec), 'group': group, 'evidence_kind': 'declared'})
            else:
                for package, item in obj.get('packages', {}).items():
                    if package:
                        hints.append({'manifest': rel, 'name': metadata_hint(package), 'version_hint': metadata_hint(item.get('version')),
                                      'license_hint': metadata_hint(item.get('license')), 'evidence_kind': 'lockfile_metadata'})
                if not obj.get('packages'):
                    errors.append({'path': rel, 'reason': 'unsupported_lockfile_layout'})
        elif name == 'pyproject.toml':
            obj = tomllib.loads(content)
            project = obj.get('project', {})
            deps = list(project.get('dependencies', []))
            for group in project.get('optional-dependencies', {}).values():
                deps.extend(group)
            for dep in deps:
                hints.append({'manifest': rel, 'requirement_hint': metadata_hint(dep), 'evidence_kind': 'declared'})
            if obj.get('tool', {}).get('poetry') or project.get('dynamic'):
                errors.append({'path': rel, 'reason': 'dynamic_or_tool_specific_dependencies_not_resolved'})
        elif name.startswith('requirements') and name.endswith('.txt'):
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # URLs and package-manager options can contain credentials; do not echo them.
                if re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~]+[A-Za-z0-9.*+_-]+(?:,[<>=!~]+[A-Za-z0-9.*+_-]+)*)?', line):
                    hints.append({'manifest': rel, 'requirement_hint': line, 'evidence_kind': 'declared'})
                else:
                    errors.append({'path': rel, 'reason': 'requirement_not_parsed'})
        else:
            errors.append({'path': rel, 'reason': 'unsupported_dependency_manifest'})
    except (ValueError, TypeError, AttributeError, WorkflowError):
        errors.append({'path': rel, 'reason': 'invalid_dependency_manifest'})
    # Dependency metadata is internal, never exported to public notices automatically.
    return hints, errors


def metadata_hint(value):
    # Only expose package-like metadata; URLs/options may carry authentication.
    if isinstance(value, str) and len(value) <= 200 and re.fullmatch(r'[A-Za-z0-9@_./+*<>=!~^| (),;\[\]-]+', value) and '://' not in value:
        return value
    return None


def scan_tree(root, state):
    files, skipped, dependencies, coverage = {}, [], [], []
    scope = state.get('scope', ['.'] if UNIT == 'main' else None)
    if scope is None:
        raise WorkflowError('UNIT_SCOPE_REQUIRED')
    validate_scope(scope)
    generated = set(state.get('managed_outputs', {})) | {p for g in state['generations'].values() for p in g['files']}
    for other in other_states(root):
        generated.update(other.get('managed_outputs', {}))
    def included(rel):
        return any(p == '.' or rel == p or rel.startswith(p + '/') for p in scope)
    def walk_error(error):
        raise WorkflowError('SCAN_READ_ERROR') from error
    for directory, dirs, names in os.walk(root, topdown=True, followlinks=False, onerror=walk_error):
        parent = Path(directory)
        for name in sorted(dirs[:]):
            p = parent / name
            rel = p.relative_to(root).as_posix()
            if not any(pat == '.' or rel == pat or rel.startswith(pat + '/') or pat.startswith(rel + '/') for pat in scope):
                dirs.remove(name)
                continue
            if p.is_symlink() or name in IGNORE_DIRS or name in PRIVATE_DIRS:
                dirs.remove(name)
                if name != '.yunxia':
                    skipped.append({'path': rel, 'reason': 'symlink' if p.is_symlink() else 'potential_secret' if name in PRIVATE_DIRS else 'environment_or_metadata'})
        dirs.sort()
        for name in sorted(names):
            p = parent / name
            rel = p.relative_to(root).as_posix()
            if rel in generated or not included(rel):
                continue
            if len(files) + len(skipped) >= MAX_FILES:
                raise WorkflowError('SCAN_LIMIT_EXCEEDED')
            reason = None
            if p.is_symlink():
                reason = 'symlink'
            elif name == '.DS_Store' or p.suffix in ('.pyc', '.pyo'):
                reason = 'system_metadata'
            elif any(fnmatch.fnmatch(name.lower(), pattern) for pattern in SECRET_PATTERNS) or rel == '.codex/auth.json':
                reason = 'potential_secret'
            elif not stat.S_ISREG(p.stat().st_mode):
                reason = 'non_regular_file'
            elif p.stat().st_size > MAX_FILE:
                reason = 'file_too_large'
            if reason:
                skipped.append({'path': rel, 'reason': reason})
                continue
            relative(rel)
            data = p.read_bytes()
            item = {'sha256': digest(data), 'size': len(data), 'kind': kind(p), 'observations': []}
            content = None
            if len(data) <= MAX_TEXT and b'\0' not in data:
                try:
                    content = data.decode('utf-8')
                except UnicodeError:
                    content = None
                if content is not None:
                    for number, line in enumerate(content.splitlines(), 1):
                        m = re.search(r'SPDX-License-Identifier:\s*([A-Za-z0-9.()+ -]+)', line)
                        if m:
                            item['observations'].append({'kind': 'spdx_hint', 'line': number, 'value': m.group(1).strip()[:160]})
                        if re.search(r'copyright|版权所有', line, re.I):
                            item['observations'].append({'kind': 'copyright_location', 'line': number})
                    if item['kind'] == 'dependency-manifest':
                        hints, errors = dependency_hints(rel, content)
                        dependencies.extend(hints)
                        coverage.extend(errors)
            if item['kind'] == 'dependency-manifest' and content is None:
                coverage.append({'path': rel, 'reason': 'manifest_text_not_parsed'})
            if item['kind'] == 'opaque-container':
                coverage.append({'path': rel, 'reason': 'container_contents_not_inspected'})
            files[rel] = item
    return {'files': files, 'skipped': sorted(skipped, key=lambda x: x['path']),
            'dependencies': dependencies, 'coverage_issues': coverage}


def delta(old, new):
    before = {} if old is None else old['files']
    after = new['files']
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in before.keys() & after.keys() if before[p]['sha256'] != after[p]['sha256'])
    renamed = []
    for p in added:
        matches = [q for q in removed if before[q]['sha256'] == after[p]['sha256']]
        if len(matches) == 1 and sum(after[q]['sha256'] == after[p]['sha256'] for q in added) == 1:
            renamed.append({'from': matches[0], 'to': p, 'status': 'candidate_requires_scope_confirmation'})
    return {'added': added, 'removed': removed, 'changed': changed, 'rename_candidates': renamed,
            'coverage_changed': old is not None and old.get('skipped') != new['skipped']}


def scan(root, persist=True):
    state, before = load_state_with_bytes(root)
    snapshot = scan_tree(root, state)
    changes = delta(state['snapshot'], snapshot)
    if persist and snapshot != state['snapshot']:
        state['history'].append({'event': 'scan', 'at': now(), 'changes': changes})
        state['snapshot'] = snapshot
        transaction(root, {STATE: encoded(state)}, expected={STATE: before})
    return {'snapshot_id': fingerprint(snapshot), 'files': snapshot['files'], 'skipped': snapshot['skipped'],
            'dependency_hints': snapshot['dependencies'], 'coverage_issues': snapshot['coverage_issues'],
            'changes': changes, 'persisted': persist, 'selected_agreement': state['selected_agreement'],
            'legal_assessment': 'not_performed', 'limit': LEGAL_LIMIT}


def require_snapshot(root, state):
    if state['snapshot'] is None:
        raise WorkflowError('SCAN_REQUIRED')
    if scan_tree(root, state) != state['snapshot']:
        raise WorkflowError('SCAN_STALE')


def active_answer(state, rel):
    answer = state['answers'].get(rel, {})
    observed = state['snapshot']['files'].get(rel, {}) if state['snapshot'] else {}
    return answer if answer.get('sha256') == observed.get('sha256') else {}


def context_id(state):
    return fingerprint({'answers': {p: active_answer(state, p) for p in state['snapshot']['files']},
                        'profile': state['profile'], 'agreement': state.get('selected_agreement')})


def blockers(state):
    result = []
    if state.get('selected_agreement') is None:
        result.append({'code': 'AGREEMENT_NOT_SELECTED', 'paths': []})
    if not state['profile'].get('identity'):
        result.append({'code': 'IDENTITY_MISSING', 'paths': []})
    if not state['profile'].get('delivery'):
        result.append({'code': 'DELIVERY_MISSING', 'paths': []})
    unknown = [p for p in state['snapshot']['files'] if active_answer(state, p).get('disposition') not in ('selected', 'preserve', 'exclude')]
    if unknown:
        result.append({'code': 'ASSET_FACTS_MISSING_OR_STALE', 'paths': unknown})
    covered = [p for p in state['snapshot']['files'] if active_answer(state, p).get('disposition') == 'selected']
    if not covered:
        result.append({'code': 'NO_CONFIRMED_SCOPE', 'paths': []})
    for rel in state['snapshot']['files']:
        answer = active_answer(state, rel)
        for notice, expected in answer.get('notice_hashes', {}).items():
            if state['snapshot']['files'].get(notice, {}).get('sha256') != expected:
                result.append({'code': 'UPSTREAM_NOTICE_CHANGED', 'paths': [rel, notice]})
    return result


def questions(root):
    state = load_state(root)
    require_snapshot(root, state)
    if state['selected_agreement'] is not None:
        selected(state, 'draft')
    issues = blockers(state)
    prompts = []
    deferred = []
    deferred_keys = {'IDENTITY_MISSING': 'identity', 'DELIVERY_MISSING': 'delivery'}
    labels = {'AGREEMENT_NOT_SELECTED': '本次选择哪份协议、确切版本？请先查看catalog并明确选择。',
              'IDENTITY_MISSING': '项目名称、实际许可主体及其身份是什么？',
              'DELIVERY_MISSING': '这次向谁交付哪些形式的内容？',
              'ASSET_FACTS_MISSING_OR_STALE': '这些文件的来源及拟用许可范围是什么？可分别保留原许可、排除或保持待核。',
              'UPSTREAM_NOTICE_CHANGED': '上游声明已变化；原范围确认是否仍适用？'}
    for issue in issues:
        paths = issue['paths']
        if state.get('deferred', {}).get(deferred_keys.get(issue['code'])):
            deferred.append(issue)
            continue
        if issue['code'] == 'ASSET_FACTS_MISSING_OR_STALE':
            pending = [p for p in paths if active_answer(state, p).get('disposition') == 'pending']
            if pending:
                deferred.append({'code': 'USER_REPORTED_UNKNOWN', 'paths': pending})
            paths = [p for p in paths if p not in pending]
            if not paths:
                continue
        if issue['code'] in labels:
            prompts.append({'id': issue['code'], 'question': labels[issue['code']],
                            'paths': paths, 'unknown_allowed': True})
    valid = adoption_valid(state)
    if not issues and not valid:
        if state.get('deferred', {}).get('adoption'):
            deferred.append({'code': 'ADOPTION_NOT_CONFIRMED', 'paths': []})
        else:
            prompts.append({'id': 'ADOPTION_NOT_CONFIRMED', 'question': '是否已明确授权按所列范围采用所选协议的确切版本和摘要？沿用已有明确任务授权；缺失时才询问采用行为和实际日期。', 'paths': [], 'unknown_allowed': True})
    return {'questions': prompts[:3], 'remaining_question_groups': max(0, len(prompts) - 3),
            'blockers': issues, 'deferred': deferred, 'snapshot_id': fingerprint(state['snapshot']),
            'selected_agreement': state['selected_agreement'],
            'observations_are_not_confirmations': True}


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def answer(root, payload):
    state, before = load_state_with_bytes(root)
    require_snapshot(root, state)
    allowed = {'confirmed_by', 'basis', 'identity', 'delivery', 'assets', 'adoption', 'public_ai_statement', 'defer'}
    if not isinstance(payload, dict) or set(payload) - allowed or not all(nonempty(payload.get(k)) for k in ('confirmed_by', 'basis')):
        raise WorkflowError('ANSWER_FORMAT')
    original = encoded(state)
    confirmation = {k: payload[k] for k in ('confirmed_by', 'basis')}
    defer = payload.get('defer', [])
    if not isinstance(defer, list) or any(not isinstance(k, str) or k not in ('identity', 'delivery', 'adoption') or k in payload for k in defer):
        raise WorkflowError('ANSWER_FORMAT')
    for key in defer:
        state.setdefault('deferred', {})[key] = confirmation
    for key, fields in (('identity', {'project_name', 'licensor', 'role'}), ('delivery', {'audience', 'description'})):
        if key in payload:
            obj = payload[key]
            if not isinstance(obj, dict) or set(obj) != fields or not all(nonempty(v) for v in obj.values()):
                raise WorkflowError('ANSWER_FORMAT')
            if key in state['profile'] and state['profile'][key] != obj:
                state['history'].append({'event': 'superseded_profile', 'field': key, 'value': state['profile'][key],
                                         'confirmation': state['profile'].get(key + '_confirmation'), 'at': now()})
            state['profile'][key] = obj
            state['profile'][key + '_confirmation'] = confirmation
            state.get('deferred', {}).pop(key, None)
    if 'public_ai_statement' in payload:
        text = payload['public_ai_statement']
        if text is not None and not nonempty(text):
            raise WorkflowError('ANSWER_FORMAT')
        state['profile']['public_ai_statement'] = text
    if not isinstance(payload.get('assets', []), list):
        raise WorkflowError('ANSWER_FORMAT')
    seen = set()
    for obj in payload.get('assets', []):
        keys = {'paths', 'disposition', 'origin', 'basis', 'public_notice', 'license_files', 'replace_existing_license'}
        if not isinstance(obj, dict) or set(obj) - keys or not isinstance(obj.get('paths'), list) or not obj['paths']:
            raise WorkflowError('ANSWER_FORMAT')
        disposition = obj.get('disposition')
        if disposition not in ('selected', 'preserve', 'exclude', 'pending') or not nonempty(obj.get('basis')):
            raise WorkflowError('ANSWER_FORMAT')
        if obj.get('origin') not in ('human', 'ai', 'mixed', 'third_party', 'unknown'):
            raise WorkflowError('ANSWER_FORMAT')
        if disposition == 'selected' and obj['origin'] == 'unknown':
            raise WorkflowError('UNKNOWN_ORIGIN_IN_SCOPE')
        if 'public_notice' in obj and not nonempty(obj['public_notice']):
            raise WorkflowError('ANSWER_FORMAT')
        if 'replace_existing_license' in obj and type(obj['replace_existing_license']) is not bool:
            raise WorkflowError('ANSWER_FORMAT')
        notices = obj.get('license_files', [])
        if not isinstance(notices, list) or (disposition == 'preserve' and not notices):
            raise WorkflowError('UPSTREAM_NOTICE_REQUIRED')
        hashes = {}
        for notice in notices:
            relative(notice)
            if notice not in state['snapshot']['files']:
                raise WorkflowError('UPSTREAM_NOTICE_MISSING', [notice])
            hashes[notice] = state['snapshot']['files'][notice]['sha256']
        for rel in obj['paths']:
            relative(rel)
            if rel in seen or rel not in state['snapshot']['files']:
                raise WorkflowError('ANSWER_SCOPE_INVALID', [rel])
            seen.add(rel)
            observed = state['snapshot']['files'][rel]
            if disposition == 'selected' and (observed['kind'] == 'license-or-notice' or observed['observations']) and obj.get('replace_existing_license') is not True:
                raise WorkflowError('EXISTING_NOTICE_REQUIRES_EXPLICIT_SCOPE_DECISION', [rel])
            if disposition == 'selected':
                for other in other_states(root):
                    if active_answer(other, rel).get('disposition') == 'selected':
                        raise WorkflowError('ADOPTION_SCOPE_OVERLAP', [rel])
            entry = {k: v for k, v in obj.items() if k != 'paths'}
            entry.update(sha256=observed['sha256'], confirmation=confirmation, notice_hashes=hashes)
            if state['answers'].get(rel) != entry:
                if rel in state['answers']:
                    state['history'].append({'event': 'superseded_answer', 'path': rel, 'answer': state['answers'][rel], 'at': now()})
                state['answers'][rel] = entry
    if 'adoption' in payload:
        obj = payload['adoption']
        if not isinstance(obj, dict) or set(obj) != {'authorized', 'accept_draft', 'date', 'snapshot_id', 'agreement'} or obj['authorized'] is not True or type(obj['accept_draft']) is not bool:
            raise WorkflowError('ADOPTION_FORMAT')
        validate_reference(obj['agreement'])
        entry = selected(state, 'adopt')
        if entry['status'] != 'released' and obj['accept_draft'] is not True:
            raise WorkflowError('ADOPTION_FORMAT')
        if obj['agreement'] != state['selected_agreement']:
            raise WorkflowError('ADOPTION_AGREEMENT_MISMATCH')
        try:
            date.fromisoformat(obj['date'])
        except (ValueError, TypeError):
            raise WorkflowError('ADOPTION_DATE_REQUIRED') from None
        if obj['snapshot_id'] != fingerprint(state['snapshot']):
            raise WorkflowError('ADOPTION_SNAPSHOT_STALE')
        pending = blockers(state)
        if pending:
            raise WorkflowError('ADOPTION_BLOCKED', sorted({p for issue in pending for p in issue['paths']}))
        value = dict(obj, context_id=context_id(state), confirmation=confirmation)
        if state.get('adoption') != value:
            if state.get('adoption'):
                state['history'].append({'event': 'superseded_adoption', 'value': state['adoption'], 'at': now()})
            state['adoption'] = value
        state.get('deferred', {}).pop('adoption', None)
    if encoded(state) != original:
        state['history'].append({'event': 'answer', 'at': now(), 'confirmed_by': payload['confirmed_by'], 'paths': sorted(seen)})
        transaction(root, {STATE: encoded(state)}, expected={STATE: before})
    return {'saved': encoded(state) != original, 'blockers': blockers(state), 'evidence_status': 'user_attested_not_independently_verified'}


def md(value):
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '\\|').replace('`', '&#96;').replace('\n', '<br>')


def license_bytes(ref):
    return catalog().resolve(ref)['_text']


def history_issues(state):
    issues = []
    for index, event in enumerate(state['history']):
        if not isinstance(event, dict) or event.get('event') != 'superseded_generation':
            continue
        location = f'{STATE}:history[{index}]'
        generation, content = event.get('generation'), event.get('content_base64')
        files = generation.get('files') if isinstance(generation, dict) else None
        if not isinstance(files, dict) or not files or not isinstance(content, dict) or set(files) != set(content):
            issues.append({'code': 'HISTORICAL_GENERATION_INVALID', 'paths': [location]})
            continue
        for path, expected in files.items():
            try:
                if not output_path(path) or not isinstance(expected, str):
                    raise ValueError()
                archived = base64.b64decode(content[path], validate=True)
            except (ValueError, TypeError, WorkflowError):
                issues.append({'code': 'HISTORICAL_GENERATION_INVALID', 'paths': [location]})
                break
            if digest(archived) != expected:
                issues.append({'code': 'HISTORICAL_CONTENT_MISMATCH', 'paths': [location + ':' + path]})
    return issues


def render(root, state, mode):
    from ..adapters.scope_license import render as scope_render
    return scope_render(sys.modules[__name__], root, state, mode)


def adapter_binding():
    from ..adapters import scope_license
    return {'id': 'scope-license-v1', 'tool_version': VERSION,
            'sha256': digest(Path(scope_license.__file__).read_bytes())}


def generate(root, mode='draft', write=False):
    state, before_state = load_state_with_bytes(root)
    require_snapshot(root, state)
    past_issues = history_issues(state)
    if past_issues:
        raise WorkflowError('HISTORY_INTEGRITY_ERROR', [p for item in past_issues for p in item['paths']])
    outputs = render(root, state, mode)
    previous = state['generations'].get(mode, {}).get('files', {})
    plan, conflicts = [], []
    before_images = {STATE: before_state}
    previous_bytes = {}
    for rel, expected in previous.items():
        content = current_bytes(root, rel)
        before_images[rel] = content
        if content is None or digest(content) != expected:
            conflicts.append(rel)
        else:
            previous_bytes[rel] = content
    for rel, content in outputs.items():
        before = before_images[rel] if rel in before_images else current_bytes(root, rel)
        before_images[rel] = before
        expected = previous.get(rel)
        if before is not None and before != content and (expected is None or digest(before) != expected):
            conflicts.append(rel)
        plan.append({'path': rel, 'action': 'unchanged' if before == content else 'create' if before is None else 'update',
                     'before_sha256': None if before is None else digest(before), 'after_sha256': digest(content)})
    conflicts = sorted(set(conflicts))
    if write and conflicts:
        raise WorkflowError('MANUAL_EDIT_CONFLICT', conflicts)
    if write:
        require_snapshot(root, state)
        generation = {'files': {p: digest(v) for p, v in outputs.items()},
                      'snapshot_id': fingerprint(state['snapshot']), 'context_id': context_id(state),
                      'tool_version': VERSION, 'license_sha256': state['selected_agreement']['sha256'],
                      'agreement': dict(state['selected_agreement']), 'adapter': adapter_binding()}
        previous_generation = state['generations'].get(mode)
        if previous_generation is not None and previous_generation != generation:
            state['history'].append({'event': 'superseded_generation', 'at': now(), 'mode': mode,
                                     'generation': previous_generation,
                                     'content_base64': {p: base64.b64encode(content).decode() for p, content in previous_bytes.items()}})
        state['generations'][mode] = generation
        state['managed_outputs'].update(generation['files'])
        transaction(root, dict(outputs, **{STATE: encoded(state)}), expected=before_images)
    return {'mode': mode, 'written': write, 'changes': plan, 'conflicts': conflicts,
            'agreement': state['selected_agreement'],
            'structure_valid': not conflicts, 'authority_status': 'user_attested' if mode == 'adopt' else 'pending',
            'legal_assessment': 'not_performed', 'limit': LEGAL_LIMIT}


def artifact_files(artifact, prefix=''):
    if prefix:
        prefix = relative(prefix).rstrip('/') + '/'
    entries = {}
    if artifact.is_dir():
        base = artifact
        if base.is_symlink():
            raise WorkflowError('ARTIFACT_SYMLINK')
        for component in PurePosixPath(prefix).parts:
            base = base / component
            if base.is_symlink():
                raise WorkflowError('ARTIFACT_SYMLINK')
        if not base.is_dir():
            raise WorkflowError('ARTIFACT_PREFIX_MISSING')
        total = 0
        def walk_error(error):
            raise WorkflowError('ARTIFACT_READ_ERROR') from error
        for directory, dirs, names in os.walk(artifact, followlinks=False, onerror=walk_error):
            for name in dirs + names:
                p = Path(directory) / name
                if p.is_symlink():
                    raise WorkflowError('ARTIFACT_SYMLINK')
            for name in names:
                p = Path(directory) / name
                full_rel = p.relative_to(artifact).as_posix()
                if not full_rel.startswith(prefix):
                    raise WorkflowError('ARTIFACT_OUTSIDE_PREFIX')
                rel = relative(full_rel[len(prefix):])
                if not p.is_file() or p.stat().st_size > MAX_FILE:
                    raise WorkflowError('ARTIFACT_UNSUPPORTED_FILE', [rel])
                total += p.stat().st_size
                if len(entries) >= MAX_FILES or total > 256 * 1024 * 1024:
                    raise WorkflowError('ARTIFACT_LIMIT_EXCEEDED')
                entries[relative(rel)] = digest(p.read_bytes())
    else:
        with zipfile.ZipFile(artifact) as archive:
            if len(archive.infolist()) > MAX_FILES:
                raise WorkflowError('ARTIFACT_LIMIT_EXCEEDED')
            total = 0
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16) or info.file_size > MAX_FILE or info.flag_bits & 1:
                    raise WorkflowError('ARTIFACT_UNSUPPORTED_FILE')
                if not info.filename.startswith(prefix):
                    raise WorkflowError('ARTIFACT_OUTSIDE_PREFIX')
                rel = relative(info.filename[len(prefix):])
                if rel in entries:
                    raise WorkflowError('ARTIFACT_DUPLICATE_PATH', [rel])
                total += info.file_size
                if total > 256 * 1024 * 1024:
                    raise WorkflowError('ARTIFACT_LIMIT_EXCEEDED')
                entries[rel] = digest(archive.read(info))
    if len(entries) > MAX_FILES:
        raise WorkflowError('ARTIFACT_LIMIT_EXCEEDED')
    return entries


def verify(root, mode='adopt', artifact=None, prefix=''):
    state = load_state(root)
    require_snapshot(root, state)
    agreement = selected(state, 'verify')
    generation = state['generations'].get(mode)
    if generation is None:
        raise WorkflowError('GENERATION_REQUIRED')
    issues = history_issues(state)
    if generation['snapshot_id'] != fingerprint(state['snapshot']) or generation['context_id'] != context_id(state):
        issues.append({'code': 'GENERATION_STALE', 'paths': []})
    if generation.get('agreement') != state['selected_agreement'] or generation['license_sha256'] != agreement['sha256']:
        issues.append({'code': 'GENERATION_AGREEMENT_MISMATCH', 'paths': []})
    for rel, expected in generation['files'].items():
        actual = current_bytes(root, rel)
        if actual is None or digest(actual) != expected:
            issues.append({'code': 'GENERATED_FILE_MISSING_OR_EDITED', 'paths': [rel]})
    if mode == 'adopt':
        issues.extend(blockers(state))
        if not adoption_valid(state):
            issues.append({'code': 'ADOPTION_NOT_CONFIRMED', 'paths': []})
    if artifact is not None:
        if mode != 'adopt':
            raise WorkflowError('DRAFT_IS_NOT_RELEASE_MATERIAL')
        entries = artifact_files(artifact, prefix)
        expected = dict(generation['files'])
        for rel in state['snapshot']['files']:
            a = active_answer(state, rel)
            if a.get('disposition') in ('selected', 'preserve'):
                expected[rel] = state['snapshot']['files'][rel]['sha256']
                expected.update(a.get('notice_hashes', {}))
        for rel, wanted in expected.items():
            if entries.get(rel) != wanted:
                issues.append({'code': 'ARTIFACT_MISSING_OR_CHANGED', 'paths': [rel]})
        known = set(generation['files']) | set(state['snapshot']['files']) | set(state['managed_outputs'])
        for rel in entries:
            if '.yunxia' in PurePosixPath(rel).parts:
                issues.append({'code': 'PRIVATE_STATE_INCLUDED', 'paths': [rel]})
            elif rel not in known:
                issues.append({'code': 'UNSCANNED_ARTIFACT_FILE', 'paths': [rel]})
            elif rel in state['managed_outputs'] and entries[rel] != state['managed_outputs'][rel]:
                issues.append({'code': 'ARTIFACT_CONTENT_CHANGED', 'paths': [rel]})
            elif rel in state['snapshot']['files'] and entries[rel] != state['snapshot']['files'][rel]['sha256']:
                issues.append({'code': 'ARTIFACT_CONTENT_CHANGED', 'paths': [rel]})
    return {'structure_valid': not issues, 'mode': mode, 'issues': issues,
            'agreement': state['selected_agreement'],
            'artifact_checked': artifact is not None, 'legal_assessment': 'not_performed', 'limit': LEGAL_LIMIT,
            'adapter': generation['adapter'],
            'adapter_update_available': generation['adapter'] != adapter_binding()}


def verify_all(root, artifact=None, prefix=''):
    results, issues, expected, known, claimed = {}, [], {}, {}, {}
    pending_units = []
    ids = unit_ids(root)
    if not ids:
        raise WorkflowError('NO_ADOPTION_UNITS')
    for unit in ids:
        with using_unit(unit):
            try:
                state = load_state(root)
                if 'adopt' not in state['generations']:
                    pending_units.append({'id': unit, 'scope': state.get('scope', ['.'] if unit == 'main' else []),
                                          'reason': 'ADOPTED_GENERATION_MISSING',
                                          'adoption_record_present': bool(state.get('adoption'))})
                    continue
                result = verify(root)
            except (WorkflowError, CatalogError) as error:
                issue = {'code': error.code, 'paths': error.paths, 'unit': unit}
                issues.append(issue)
                results[unit] = {'structure_valid': False, 'issues': [issue]}
                continue
            results[unit] = result
            issues.extend(dict(x, unit=unit) for x in result['issues'])
            for path, value in state['managed_outputs'].items():
                if path in known and known[path] != value:
                    issues.append({'code':'UNIT_OUTPUT_CONFLICT','paths':[path]})
                known[path] = value
            expected.update(state['generations']['adopt']['files'])
            for path, item in state['snapshot']['files'].items():
                known[path] = item['sha256']
                answer = active_answer(state,path)
                if answer.get('disposition') == 'selected':
                    if path in claimed:
                        issues.append({'code':'ADOPTION_SCOPE_OVERLAP','paths':[path]})
                    claimed[path] = unit
                if answer.get('disposition') in ('selected','preserve'):
                    expected[path] = item['sha256']
                    expected.update(answer.get('notice_hashes',{}))
    if not results:
        issues.append({'code': 'NO_ADOPTED_GENERATIONS', 'paths': []})
    if artifact is not None:
        entries = artifact_files(artifact,prefix)
        for path,wanted in expected.items():
            if entries.get(path)!=wanted:
                issues.append({'code':'ARTIFACT_MISSING_OR_CHANGED','paths':[path]})
        for path,actual in entries.items():
            if '.yunxia' in PurePosixPath(path).parts:
                issues.append({'code':'PRIVATE_STATE_INCLUDED','paths':[path]})
            elif path not in known:
                issues.append({'code':'UNSCANNED_ARTIFACT_FILE','paths':[path]})
            elif actual!=known[path]:
                issues.append({'code':'ARTIFACT_CONTENT_CHANGED','paths':[path]})
    return {'structure_valid':not issues,'units':results,'pending_units':pending_units,
            'all_units_ready':not issues and not pending_units,'verification_scope':'generated_adoptions',
            'issues':issues,'artifact_checked':artifact is not None,'legal_assessment':'not_performed'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit', default='main', help='采用单元；默认main')
    parser.add_argument('--root', type=Path, help='目标项目根目录；catalog命令可省略')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('catalog')
    cmd = sub.add_parser('select')
    cmd.add_argument('--agreement', required=True)
    cmd.add_argument('--version', required=True)
    cmd = sub.add_parser('scan')
    cmd.add_argument('--read-only', action='store_true')
    sub.add_parser('questions')
    cmd = sub.add_parser('scope')
    cmd.add_argument('--path', action='append', required=True)
    sub.add_parser('units')
    cmd = sub.add_parser('answer')
    cmd.add_argument('--input', required=True, type=Path)
    cmd = sub.add_parser('generate')
    cmd.add_argument('--mode', choices=['draft', 'adopt'], default='draft')
    cmd.add_argument('--write', action='store_true')
    cmd = sub.add_parser('verify')
    cmd.add_argument('--mode', choices=['draft', 'adopt'], default='adopt')
    cmd.add_argument('--artifact', type=Path)
    cmd.add_argument('--prefix', default='')
    cmd = sub.add_parser('verify-all')
    cmd.add_argument('--artifact', type=Path)
    cmd.add_argument('--prefix', default='')
    sub.add_parser('recover')
    args = parser.parse_args(argv)
    try:
        configure_unit(args.unit)
        if args.command == 'catalog':
            print(json.dumps(catalog().describe(), ensure_ascii=False, indent=2))
            return 0
        if args.root is None:
            raise WorkflowError('ROOT_REQUIRED')
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise WorkflowError('ROOT_NOT_DIRECTORY')
        mutating = args.command in ('answer', 'recover', 'select', 'scope') or (args.command == 'scan' and not args.read_only) or (args.command == 'generate' and args.write)
        def run():
            if args.command == 'scope':
                return set_scope(root, args.path)
            if args.command == 'units':
                return list_units(root)
            if args.command == 'select':
                return select_agreement(root, args.agreement, args.version)
            if args.command == 'scan':
                return scan(root, persist=not args.read_only)
            if args.command == 'questions':
                return questions(root)
            if args.command == 'answer':
                return answer(root, read_json(args.input))
            if args.command == 'generate':
                return generate(root, args.mode, args.write)
            if args.command == 'verify':
                return verify(root, args.mode, args.artifact, args.prefix)
            if args.command == 'verify-all':
                return verify_all(root, args.artifact, args.prefix)
            return {'recovered': restore(root)}
        if mutating:
            with lock(root, recovery=args.command == 'recover'):
                result = run()
        else:
            result = run()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('structure_valid') is False or result.get('conflicts') else 0
    except (WorkflowError, CatalogError) as error:
        print(json.dumps({'error': error.code, 'paths': error.paths, 'legal_assessment': 'not_performed'}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, zipfile.BadZipFile):
        print(json.dumps({'error': 'IO_OR_FORMAT_ERROR', 'legal_assessment': 'not_performed'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
