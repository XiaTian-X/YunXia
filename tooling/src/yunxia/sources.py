"""Explicit, pinned protocol sources. Fetching and selecting a new pin are separate."""
import copy
from pathlib import Path
import re
from urllib.parse import urlsplit

from . import gitstore
from .catalog import Catalog, validate_repository
from .common import (CatalogError, atomic_write, json_bytes, parse_json, safe_path,
                     segment)


LOCK = 'sources.lock.json'


def valid_repository(value):
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        return False
    if re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9._/-]+', value):
        return '..' not in value.split(':', 1)[1].split('/')
    try:
        url = urlsplit(value)
        return (url.scheme in ('https', 'ssh') and bool(url.hostname)
                and url.password is None and (url.username is None or url.scheme == 'ssh')
                and bool(url.path.strip('/')) and not url.query and not url.fragment
                and '..' not in url.path.split('/'))
    except ValueError:
        return False


def _lock_snapshot(root):
    path = safe_path(Path(root), LOCK)
    try:
        raw = path.read_bytes()
        data = parse_json(raw.decode('utf-8'))
    except (OSError, ValueError):
        raise CatalogError('SOURCE_LOCK_INVALID', [LOCK]) from None
    if (not isinstance(data, dict) or set(data) != {'format_version', 'sources'}
            or type(data['format_version']) is not int or data['format_version'] != 1
            or not isinstance(data['sources'], list)):
        raise CatalogError('SOURCE_LOCK_INVALID', [LOCK])
    seen, agreements = set(), set()
    for row in data['sources']:
        required = {'id', 'repository', 'commit', 'agreements'}
        if (not isinstance(row, dict) or not required <= set(row)
                or set(row) - required - {'repository_id'}
                or not segment(row['id']) or row['id'] in seen
                or not valid_repository(row['repository'])
                or not isinstance(row['commit'], str) or not gitstore.OID.fullmatch(row['commit'])
                or not isinstance(row['agreements'], list) or not row['agreements']
                or any(not segment(aid) for aid in row['agreements'])
                or len(set(row['agreements'])) != len(row['agreements'])
                or ('repository_id' in row and not segment(row['repository_id']))):
            raise CatalogError('SOURCE_LOCK_INVALID', [LOCK])
        if agreements.intersection(row['agreements']):
            raise CatalogError('SOURCE_AGREEMENT_CONFLICT')
        agreements.update(row['agreements']); seen.add(row['id'])
    return data, raw


def read_lock(root):
    return _lock_snapshot(root)[0]


def source_path(root, source_id, locations=None):
    root = Path(root).resolve()
    if not segment(source_id):
        raise CatalogError('SOURCE_ID_INVALID')
    if locations and source_id in locations:
        candidate = Path(locations[source_id]).absolute()
        if candidate.is_symlink():
            raise CatalogError('SOURCE_PATH_INVALID', [str(candidate)])
        target = candidate.resolve()
    else:
        target = safe_path(root, 'repositories/' + source_id)
    if target == root or target in root.parents:
        raise CatalogError('SOURCE_PATH_INVALID', [str(target)])
    return target


def _source(root, row, locations=None):
    path = source_path(root, row['id'], locations)
    if not path.is_dir():
        raise CatalogError('SOURCE_REPOSITORY_MISSING', [row['id'], str(path)])
    gitstore.require_root(path)
    remote = gitstore.run(path, ['remote', 'get-url', 'origin'], False)
    if remote.returncode or remote.stdout.decode().strip() != row['repository']:
        raise CatalogError('SOURCE_REMOTE_MISMATCH', [row['id']])
    return path


def _catalog(root, row, locations=None):
    path = _source(root, row, locations)
    catalog = Catalog(path, commit=row['commit'])
    if 'repository_id' in row:
        marker = parse_json(catalog.inputs['repository.json'])
        if marker.get('repository_id') != row['repository_id']:
            raise CatalogError('SOURCE_IDENTITY_MISMATCH', [row['id']])
    actual = {data['agreement_id'] for data in catalog.agreements.values()}
    if not set(row['agreements']) <= actual:
        raise CatalogError('SOURCE_AGREEMENT_MISSING', sorted(set(row['agreements']) - actual))
    return catalog


class SourceCatalog(Catalog):
    """Compose validated protocols while reusing the ordinary offline bundle contract."""
    def __init__(self, root, lock, adapter_data, locations=None):
        self.root = Path(root)
        self.bundled = False
        self.inputs, self.blobs, self.entries, self.agreements = {}, {}, [], {}
        self.recommended_by_agreement = {}
        self.source_inputs = {}
        self.source_pins = copy.deepcopy(lock)
        for row in lock['sources']:
            current = _catalog(root, row, locations)
            chosen = set(row['agreements'])
            for rel, content in current.inputs.items():
                self.source_inputs['sources/' + row['id'] + '/' + rel] = content
            for rel, metadata in current.agreements.items():
                if metadata['agreement_id'] in chosen:
                    if rel in self.agreements:
                        raise CatalogError('SOURCE_AGREEMENT_CONFLICT', [metadata['agreement_id']])
                    self.agreements[rel] = metadata
                    self.recommended_by_agreement[metadata['agreement_id']] = metadata['recommended']
            for entry in current.entries:
                if entry['agreement_id'] in chosen:
                    self.entries.append(copy.deepcopy(entry))
                    self.inputs[entry['_manifest']] = current.inputs[entry['_manifest']]
            for digest, content in current.blobs.items():
                if digest in self.blobs and self.blobs[digest] != content:
                    raise CatalogError('SOURCE_OBJECT_CONFLICT', [digest])
                self.blobs[digest] = content
        # Avoid bundling unselected protocols' objects. Only reviewed/declared
        # objects reachable through selected entries are needed for offline reads.
        used = set()
        for entry in self.entries:
            used.update(entry['files'].values())
            source = entry['source']
            if source.get('metadata_sha256'):
                used.add(source['metadata_sha256'])
            snapshot = source.get('snapshot', {})
            used.update(snapshot.get('files', {}).values())
            used.update(snapshot.get('dependencies', {}).values())
        self.blobs = {digest:content for digest,content in self.blobs.items() if digest in used}
        self.apply_supports(adapter_data)

    def describe(self):
        result = super().describe()
        result['snapshot_scope'] = 'locked-sources'
        result['sources'] = copy.deepcopy(self.source_pins['sources'])
        return result


def _adapters(root):
    rel = 'tooling/src/yunxia/adapters/support.json'
    try:
        return parse_json(safe_path(root, rel).read_bytes())
    except (OSError, ValueError):
        raise CatalogError('ADAPTER_REGISTRY_FORMAT', [rel]) from None


def load_catalog(root, locations=None, adapter_data=None):
    root = Path(root)
    lock, raw = _lock_snapshot(root)
    if adapter_data is None:
        adapter_data = _adapters(root)
    catalog = SourceCatalog(root, lock, adapter_data, locations)
    catalog.lock_bytes = raw
    return catalog


def _verify_remote_history(path, row):
    """An explicit fetch must check retention on the remote, not cached refs."""
    marker = validate_repository(parse_json(gitstore.read_blob(path, row['commit'], 'repository.json')))
    if marker['migration'] is None:
        return
    migration = parse_json(gitstore.read_blob(path, row['commit'], marker['migration']))
    retained = migration.get('retained_history') if isinstance(migration, dict) else None
    if (not isinstance(retained, dict) or set(retained) != {'ref', 'commit'}
            or not isinstance(retained['ref'], str) or not retained['ref'].startswith('refs/heads/history/')
            or gitstore.run(path, ['check-ref-format', retained['ref']], False).returncode
            or not isinstance(retained['commit'], str) or not gitstore.OID.fullmatch(retained['commit'])):
        raise CatalogError('MIGRATION_FORMAT', [marker['migration']])
    result = gitstore.run(path, ['ls-remote', '--refs', '--', row['repository'], retained['ref']], False)
    if result.returncode:
        raise CatalogError('SOURCE_HISTORY_REMOTE_UNAVAILABLE', [row['id'], retained['ref']])
    matches = [fields[0] for line in result.stdout.decode().splitlines()
               if len(fields := line.split()) == 2 and fields[1] == retained['ref']]
    if not matches:
        raise CatalogError('SOURCE_HISTORY_REMOTE_MISSING', [row['id'], retained['ref']])
    if matches != [retained['commit']]:
        raise CatalogError('SOURCE_HISTORY_REMOTE_CHANGED', [row['id'], retained['ref']])


def fetch(root, source_id=None, locations=None):
    """Explicit network step; preserve existing working trees and lock bytes."""
    root = Path(root).resolve()
    lock = read_lock(root)
    rows = [row for row in lock['sources'] if source_id is None or row['id'] == source_id]
    if source_id is not None and not rows:
        raise CatalogError('SOURCE_NOT_REGISTERED', [source_id])
    results = []
    for row in rows:
        path = source_path(root, row['id'], locations)
        created = not path.exists()
        if created:
            path.parent.mkdir(parents=True, exist_ok=True)
            gitstore.run(root, ['clone', '--no-hardlinks', '--no-recurse-submodules', '--', row['repository'], str(path)])
        else:
            _source(root, row, locations)
            args = ['fetch', '--no-recurse-submodules', '--tags']
            if gitstore.run(path, ['rev-parse', '--is-shallow-repository']).stdout.strip() == b'true':
                args.append('--unshallow')
            gitstore.run(path, args + ['origin', '+refs/heads/*:refs/remotes/origin/*'])
        _verify_remote_history(path, row)
        _catalog(root, row, locations)
        branch = gitstore.run(path, ['symbolic-ref', '--quiet', '--short', 'HEAD'], False)
        results.append({'id':row['id'], 'path':str(path), 'commit':row['commit'], 'created':created,
                        'working_head':gitstore.head(path),
                        'working_branch':branch.stdout.decode().strip() if branch.returncode == 0 else None})
    return {'fetched':results, 'lock_changed':False}


def pin(root, source_id, commit, locations=None):
    """Validate an explicit forward snapshot before changing the source selection."""
    root = Path(root)
    lock_path = safe_path(root, LOCK)
    lock, before = _lock_snapshot(root)
    rows = [row for row in lock['sources'] if row['id'] == source_id]
    if len(rows) != 1:
        raise CatalogError('SOURCE_NOT_REGISTERED', [source_id])
    if not isinstance(commit, str) or not gitstore.OID.fullmatch(commit):
        raise CatalogError('GIT_COMMIT_INVALID')
    row = rows[0]
    previous_commit = row['commit']
    old = _catalog(root, row, locations)
    candidate = dict(row, commit=commit)
    new = _catalog(root, candidate, locations)
    path = source_path(root, source_id, locations)
    if gitstore.run(path, ['merge-base', '--is-ancestor', row['commit'], commit], False).returncode:
        raise CatalogError('SOURCE_NONFASTFORWARD', [source_id])
    old_versions = {(e['agreement_id'], e['version']):e for e in old.entries}
    new_versions = {(e['agreement_id'], e['version']):e for e in new.entries}
    if not old_versions.keys() <= new_versions.keys():
        raise CatalogError('SOURCE_VERSION_MISSING', [source_id])
    for key, previous in old_versions.items():
        if previous['sha256'] != new_versions[key]['sha256']:
            raise CatalogError('SOURCE_VERSION_CHANGED', [source_id, *key])
    row['commit'] = commit
    # Validate the complete composition, including cross-source adapter references.
    SourceCatalog(root, lock, _adapters(root), locations)
    if lock_path.read_bytes() != before:
        raise CatalogError('SOURCE_LOCK_CHANGED', [LOCK])
    if commit != previous_commit:
        atomic_write(lock_path, json_bytes(lock), mode=0o644)
    added = sorted(set(new_versions) - set(old_versions))
    changed = sorted(key for key in old_versions if
                     any(old_versions[key].get(field) != new_versions[key].get(field)
                         for field in ('status', 'publication', 'withdrawal')))
    return {'source':source_id, 'commit':commit, 'added_versions':[list(x) for x in added],
            'changed_versions':[list(x) for x in changed],
            'recommendations_changed':old.recommended_by_agreement != new.recommended_by_agreement}
