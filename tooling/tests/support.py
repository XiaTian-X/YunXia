"""Current-format test data, created in temporary repositories only."""
import json
from pathlib import Path
import shutil
import subprocess
from yunxia import lifecycle as life
from yunxia.catalog import Catalog, reference
from yunxia.common import json_bytes

ROOT = Path(__file__).resolve().parents[2]
CURRENT_VERSION = '0.9.3-draft.2'


def copy_catalog(target):
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout',
                    str(ROOT), str(target)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(target), 'remote', 'remove', 'origin'], check=True,
                   capture_output=True)
    for key, value in (('user.name', 'Fixture maintainer'), ('user.email', 'fixture@example.test'),
                       ('commit.gpgsign', 'false'), ('core.hooksPath', '/dev/null')):
        subprocess.run(['git', '-C', str(target), 'config', key, value], check=True)
    for folder in ('agreements', 'registry'):
        shutil.copytree(ROOT/folder, target/folder, ignore=shutil.ignore_patterns('.DS_Store'))
    registry = target/'tooling/src/yunxia/adapters/support.json'
    registry.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/'tooling/src/yunxia/adapters/support.json', registry)
    return target


def git_commit(root, message='Fixture source snapshot'):
    """Commit isolated test inputs, never the real maintenance repository."""
    if root.resolve() == ROOT.resolve():
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
        registry = root/'tooling/src/yunxia/adapters/support.json'
        data = json.loads(registry.read_text())
        data['supported'].append({'agreement': ref, 'adapter': 'scope-license-v1',
                                  'output_name': 'LICENSE.fixture-1.txt'})
        registry.write_bytes(json_bytes(data))
    return ref
