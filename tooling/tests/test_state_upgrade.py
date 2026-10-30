import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia.project import workflow as w
from yunxia.catalog import Catalog


class StateUpgrade(unittest.TestCase):
    def test_actual_021_state_migrates_without_new_adoption_or_public_edits(self):
        fixture=json.loads((ROOT/'tooling/tests/fixtures/state-0.2.1.json').read_text())
        with tempfile.TemporaryDirectory() as temp,patch.object(w,'catalog',lambda:Catalog(ROOT)):
            root=Path(temp);w.configure_unit('main')
            for path,text in fixture['materials'].items():
                p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
            (root/fixture['license_material']).write_bytes(Catalog(ROOT).resolve(fixture['license_reference'])['_text'])
            (root/'.yunxia').mkdir();original=w.encoded(fixture['state']);(root/w.STATE).write_bytes(original)
            public={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file() and '.yunxia' not in p.parts}
            with self.assertRaises(w.WorkflowError) as cm:w.scan(root,False)
            self.assertEqual(cm.exception.code,'STATE_MIGRATION_REQUIRED')
            with w.lock(root):result=w.migrate_state(root)
            self.assertEqual(result['state_version'],3);self.assertFalse(result['new_adoption_binding_required'])
            state=w.load_state(root)
            self.assertEqual(state['adoption'],fixture['state']['adoption'])
            self.assertEqual(state['selected_agreement'],fixture['state']['selected_agreement'])
            self.assertEqual(state['answers'],fixture['state']['answers'])
            self.assertEqual(base64.b64decode(state['history'][-1]['original_state_base64']),original)
            self.assertEqual(w.questions(root)['questions'],[])
            self.assertTrue(w.verify(root)['structure_valid'])
            self.assertEqual(w.verify(root)['adapter_record_status'],'legacy-unrecorded')
            self.assertEqual(public,{p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file() and '.yunxia' not in p.parts})
            before=(root/w.STATE).read_bytes();self.assertFalse(w.migrate_state(root)['migrated'])
            self.assertEqual((root/w.STATE).read_bytes(),before)

    def test_state_cannot_be_transplanted_to_another_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with w.using_unit('code'):
                state=w.fresh();state['unit_id']='docs'
                p=root/w.STATE;p.parent.mkdir(parents=True);p.write_bytes(w.encoded(state))
                with self.assertRaises(w.WorkflowError) as cm:w.load_state(root)
                self.assertEqual(cm.exception.code,'UNIT_STATE_MISMATCH')

    def test_released_metadata_matches_documented_schemas(self):
        from jsonschema import Draft202012Validator
        meta=json.loads((ROOT/'tooling/schemas/agreement.schema.json').read_text())
        release=json.loads((ROOT/'tooling/schemas/release.schema.json').read_text())
        for p in (ROOT/'agreements').glob('*/agreement.json'):
            Draft202012Validator(meta).validate(json.loads(p.read_text()))
        for p in (ROOT/'releases').glob('*/*/release.json'):
            Draft202012Validator(release).validate(json.loads(p.read_text()))


if __name__=='__main__':unittest.main()
