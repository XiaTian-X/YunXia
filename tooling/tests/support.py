"""Current-format test data, created in temporary repositories only."""
import json
from pathlib import Path
import shutil
import subprocess
from yunxia import lifecycle as life
from yunxia.catalog import Catalog, reference
from yunxia.common import json_bytes, sha256

ROOT = Path(__file__).resolve().parents[2]
CURRENT_VERSION = '0.9.3-draft.2'


def copy_catalog(target):
    """Build fictional protocols without depending on the real source repository."""
    target.mkdir(parents=True)
    subprocess.run(['git', 'init', '--quiet', '-b', 'main', str(target)], check=True)
    for key, value in (('user.name', 'Fixture maintainer'), ('user.email', 'fixture@example.test'),
                       ('commit.gpgsign', 'false'), ('core.hooksPath', '/dev/null')):
        subprocess.run(['git', '-C', str(target), 'config', key, value], check=True)
    write_repository(target)
    (target/'.gitignore').write_text('.yunxia-*\n')
    versions = ('0.9.2-draft.1', '0.9.3-draft.1', CURRENT_VERSION)
    payloads = {}
    for version in versions:
        folder = target/'fixture-payload'/version
        folder.mkdir(parents=True)
        files = {'text.txt': f'Fictional test license {version}.\n'.encode(),
                 'notes.md': b'Fictional test notes only.\n'}
        for name, content in files.items():
            (folder/name).write_bytes(content)
        payloads[version] = files
    commit = git_commit(target, 'Fictional fixed source materials')
    supported = []
    for version, files in payloads.items():
        record = {'format_version': 2, 'agreement_id': 'yunxia-license', 'version': version,
                  'sha256': sha256(files['text.txt']), 'display_name': 'Fictional test license',
                  'kind': 'license', 'status': 'imported-draft', 'publication': None,
                  'files': {name: sha256(content) for name, content in files.items()},
                  'source': {'commit': commit, 'tag': None,
                             'files': {name: f'fixture-payload/{version}/{name}' for name in files}}}
        path = target/f'registry/yunxia-license/{version}.json'
        path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(json_bytes(record))
        supported.append({'agreement': reference(record), 'adapter': 'scope-license-v1',
                          'output_name': 'LICENSE.yunxia-0.9.2.txt' if version.startswith('0.9.2') else 'LICENSE.yunxia-0.9.3.txt'})
    life.create(target, 'yunxia-license', 'Fictional test license', 'Fictional tests only',
                payloads[CURRENT_VERSION]['text.txt'], '0.9.3')
    metadata = target/'agreements/yunxia-license/agreement.json'
    data = json.loads(metadata.read_text());data['base'] = supported[-1]['agreement']
    metadata.write_bytes(json_bytes(data))
    (target/'adapters.json').write_bytes(json_bytes({'format_version': 1, 'supported': supported}))
    git_commit(target, 'Fictional draft registry')
    return target


def write_repository(root):
    (root/'repository.json').write_bytes(json_bytes({'format_version': 1,
        'repository_id': 'fixture-repository', 'migration': None}))


def supported_catalog(root):
    return Catalog(root, adapter_data=json.loads((root/'adapters.json').read_text()))


def copy_tool(target):
    """Create independent tool/protocol repositories for integration fixtures."""
    target.mkdir(parents=True)
    subprocess.run(['git', 'init', '--quiet', '-b', 'main', str(target)], check=True)
    for key, value in (('user.name', 'Fixture'), ('user.email', 'fixture@example.test'),
                       ('commit.gpgsign', 'false'), ('core.hooksPath', '/dev/null')):
        subprocess.run(['git', '-C', str(target), 'config', key, value], check=True)
    (target/'.gitignore').write_text('/repositories/\n/dist/\n.yunxia-*\n')
    for folder in ('skills', 'tooling/src', 'tooling/schemas'):
        shutil.copytree(ROOT/folder, target/folder, ignore=shutil.ignore_patterns('__pycache__'))
    protocol = copy_catalog(target/'repositories/Fixture-Agreements')
    remote = 'https://example.test/Fixture-Agreements.git'
    subprocess.run(['git', '-C', str(protocol), 'remote', 'add', 'origin', remote], check=True)
    commit = subprocess.check_output(['git', '-C', str(protocol), 'rev-parse', 'HEAD'], text=True).strip()
    (target/'sources.lock.json').write_bytes(json_bytes({'format_version': 1, 'sources': [
        {'id': 'Fixture-Agreements', 'repository': remote, 'commit': commit,
         'agreements': ['yunxia-license']}]}))
    shutil.copy2(protocol/'adapters.json', target/'tooling/src/yunxia/adapters/support.json')
    git_commit(target, 'Fictional tool inputs')
    return target


def git_commit(root, message='Fixture source snapshot'):
    """Commit isolated test inputs, never the real maintenance repository."""
    if root.resolve() == ROOT.resolve() or ROOT.resolve() in root.resolve().parents:
        raise ValueError('Tests cannot commit the source repository')
    subprocess.run(['git', '-C', str(root), 'add', '--all'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), 'commit', '--quiet', '--allow-empty',
                    '--no-verify', '-m', message], check=True, capture_output=True)
    return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()


def pin_payload(root, record, payload):
    """Create real Git blobs for intentional corruption/reference tests."""
    paths = {}
    for logical, content in payload.items():
        if logical not in record['files']:
            continue
        rel = 'fixture-payload/' + logical
        path = root/rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        paths[logical] = rel
    record['source']['commit'] = git_commit(root, 'Fixture payload snapshot')
    record['source']['files'] = paths
    return record


def approve(root, agreement_id):
    info = life.inspect(root, agreement_id)
    life.review(root, agreement_id, {'source_sha256': info['source_sha256'],
                'reviewer': 'fixture reviewer', 'date': '2026-10-30', 'decision': 'approved',
                'basis': 'fictional test evidence', 'open_issues': []})
    git_commit(root, 'Fixture reviewed source')


def publish(root, agreement_id, candidate='1.0.0-rc.1', version='1.0.0'):
    entry = Catalog(root).select(agreement_id, candidate)
    destination = root.parent/(root.name+'-delivery-'+agreement_id+'-'+version)
    life.publish(root, agreement_id, candidate, version, destination,
                 {'candidate_sha256': entry['_package_sha256'], 'confirmed_by': 'fixture maintainer',
                  'date': '2026-10-30', 'basis': 'fictional publication decision'})
    return reference(Catalog(root).select(agreement_id, version))


def add_protocol(root, kind='license', supported=True, released=False):
    life.create(root, 'fixture', 'Fixture protocol', 'Fictional test protocol only',
                b'Fictional test protocol only.\n', '1.0.0', kind)
    approve(root, 'fixture')
    result = life.freeze(root, 'fixture', '1.0.0-rc.1', '# Test notes\n')
    ref = publish(root, 'fixture') if released else result['frozen']
    if supported:
        registry = root/'adapters.json'
        data = json.loads(registry.read_text())
        data['supported'].append({'agreement': ref, 'adapter': 'scope-license-v1',
                                  'output_name': 'LICENSE.fixture-1.txt'})
        registry.write_bytes(json_bytes(data))
    return ref
