import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('record_versions', ROOT / 'tooling/check_disclosure.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class RecordVersions(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / 'tooling/examples/asset-register.json').read_text())

    def test_generic_record_accepts_another_protocol_reference(self):
        self.data['agreement_reference'].update(agreement_id='fixture', version='1', text_revision='test')
        result = checker.check(self.data)
        self.assertTrue(result['record_structure_valid'], result)
        self.assertEqual(result['legal_assessment'], 'not_performed')

    def test_adoption_pair_includes_exact_protocol_identity(self):
        self.data['adopted_agreement'] = copy.deepcopy(self.data['agreement_reference'])
        self.assertIn('ADOPTION_PAIR_INCOMPLETE', {x['code'] for x in checker.check(self.data)['errors']})
        self.data['adoption_date'] = '2026-10-24'
        self.assertTrue(checker.check(self.data)['record_structure_valid'])
        self.data['adopted_agreement'].pop('sha256')
        self.assertIn('SCHEMA', {x['code'] for x in checker.check(self.data)['errors']})

    def test_original_format_remains_readable(self):
        old = json.loads((ROOT / 'tooling/tests/fixtures/record-v1.json').read_text())
        self.assertEqual(old['schema_version'], '1.0.0')
        self.assertTrue(checker.check(old)['record_structure_valid'])


if __name__ == '__main__':
    unittest.main()
