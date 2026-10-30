"""Current-format test data, created in temporary repositories only."""
import json
from pathlib import Path
import shutil
from yunxia import lifecycle as life
from yunxia.catalog import Catalog, reference
from yunxia.common import json_bytes

ROOT = Path(__file__).resolve().parents[2]
CURRENT_VERSION = '0.9.3-draft.2'


def copy_catalog(target):
    for folder in ('agreements', 'releases'):
        shutil.copytree(ROOT/folder, target/folder, ignore=shutil.ignore_patterns('.DS_Store'))
    registry = target/'tooling/src/yunxia/adapters/support.json'
    registry.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/'tooling/src/yunxia/adapters/support.json', registry)
    return target


def approve(root, agreement_id):
    info = life.inspect(root, agreement_id)
    life.review(root, agreement_id, {'source_sha256': info['source_sha256'],
                'reviewer': 'fixture reviewer', 'date': '2026-10-30', 'decision': 'approved',
                'basis': 'fictional test evidence', 'open_issues': []})


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
