import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia.project import workflow as w
from yunxia.catalog import Catalog, reference
from yunxia.common import CatalogError


class CurrentState(unittest.TestCase):
    def test_unsupported_state_versions_are_not_reinterpreted_or_rewritten(self):
        with tempfile.TemporaryDirectory() as temp, w.using_unit('main'):
            root=Path(temp);(root/'.yunxia').mkdir()
            for version in (1,2,999,True):
                state=w.fresh();state.update(state_version=version,scope='unsupported shape')
                original=w.encoded(state);(root/w.STATE).write_bytes(original)
                with self.assertRaises(w.WorkflowError) as cm:w.load_state(root)
                self.assertEqual(cm.exception.code,'UNSUPPORTED_STATE_VERSION')
                self.assertEqual((root/w.STATE).read_bytes(),original)

    def test_reference_requires_exactly_three_fields(self):
        with tempfile.TemporaryDirectory() as temp, w.using_unit('main'):
            root=Path(temp);(root/'.yunxia').mkdir()
            state=w.fresh()
            state['selected_agreement']=reference(Catalog(ROOT).entries[0])
            state['selected_agreement']['text_revision']='unsupported-field'
            original=w.encoded(state);(root/w.STATE).write_bytes(original)
            with self.assertRaises(CatalogError) as cm:w.load_state(root)
            self.assertEqual(cm.exception.code,'AGREEMENT_REFERENCE_FORMAT')
            self.assertEqual((root/w.STATE).read_bytes(),original)

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
        release=json.loads((ROOT/'tooling/schemas/registry-entry.schema.json').read_text())
        for p in (ROOT/'agreements').glob('*/agreement.json'):
            Draft202012Validator(meta).validate(json.loads(p.read_text()))
        for p in (ROOT/'registry').glob('*/*.json'):
            Draft202012Validator(release).validate(json.loads(p.read_text()))

    def test_reference_schema_rejects_extra_fields(self):
        from jsonschema import Draft202012Validator
        schema=json.loads((ROOT/'tooling/schemas/agreement-reference.schema.json').read_text())
        ref=reference(Catalog(ROOT).entries[0])
        validator=Draft202012Validator(schema)
        self.assertTrue(validator.is_valid(ref))
        self.assertFalse(validator.is_valid(dict(ref,text_revision='unexpected')))

    def test_metadata_reference_fields_share_the_exact_reference_contract(self):
        from jsonschema import Draft202012Validator
        schema=json.loads((ROOT/'tooling/schemas/agreement.schema.json').read_text())
        validator=Draft202012Validator(schema)
        metadata=json.loads((ROOT/'agreements/yunxia-license/agreement.json').read_text())
        ref=reference(Catalog(ROOT).entries[0])
        for key in ('base','recommended'):
            for value in (None,ref):
                self.assertTrue(validator.is_valid(dict(metadata,**{key:value})))
            for value in (dict(ref,extra=True),dict(ref,sha256='bad'),{'agreement_id':ref['agreement_id']}):
                self.assertFalse(validator.is_valid(dict(metadata,**{key:value})))


if __name__=='__main__':unittest.main()
