import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('check_disclosure', ROOT/'tooling/check_disclosure.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class RecordChecks(unittest.TestCase):
    def setUp(self):
        self.blank = json.loads((ROOT/'tooling/tests/fixtures/record-v1.json').read_text(encoding='utf-8'))
        self.data = json.loads((ROOT/'tooling/tests/fixtures/record-example-v1.json').read_text(encoding='utf-8'))

    def codes(self, data=None):
        return {x['code'] for x in checker.check(self.data if data is None else data)['errors']}

    def evidence(self):
        self.data['evidence_records'] = [{
            'evidence_id':'V-test','kind':'fictional','reference':'fictional-test-only',
            'access':'restricted','purpose':'测试记录完整性；不是权属证据',
            'retention_basis':None,'retention_review_at':None,'note':None,
        }]

    def triggered(self):
        self.evidence()
        o = self.data['obligations'][0]
        for key in ('source_title','source_locator','clause','actor','trigger','applicability_reason'):
            o[key] = '测试假设：' + key
        o['applicability'] = 'triggered'
        o['basis_evidence_ids'] = ['V-test']
        return o

    def test_empty_is_not_release_clearance(self):
        result=checker.check(self.blank)
        self.assertTrue(result['record_structure_valid'])
        self.assertEqual(result['release_assessment'],'not_performed')
        self.assertIn('EMPTY_ASSET_REGISTER',{x['code'] for x in result['notes']})

    def test_example_keeps_unknowns_and_pending_state(self):
        before=copy.deepcopy(self.data)
        result=checker.check(self.data)
        self.assertTrue(result['record_structure_valid'])
        self.assertEqual(result['counts_from_input']['asset_paths_pending'],1)
        self.assertEqual(self.data,before)
        self.assertIsNone(self.data['adopted_license_version'])

    def test_license_version_cannot_be_used_as_record_version(self):
        self.data['schema_version']='0.9.3'
        self.assertIn('SCHEMA',self.codes())

    def test_duplicate_ids_across_groups(self):
        self.data['runtime_services'][0]['service_id']='A-demo'
        self.assertIn('DUPLICATE_ID',self.codes())

    def test_dangling_evidence_reference(self):
        self.data['assets'][0]['authority_evidence_ids']=['V-missing']
        self.assertIn('DANGLING_REFERENCE',self.codes())

    def test_event_asset_scope_mismatch(self):
        self.data['ai_events'][0]['asset_ids']=[]
        self.assertIn('SCOPE_LINK_MISMATCH',self.codes())

    def test_obligation_service_scope_mismatch(self):
        self.data['obligations'][0]['service_ids']=[]
        self.assertIn('SCOPE_LINK_MISMATCH',self.codes())

    def test_checked_without_evidence(self):
        self.data['assets'][0]['review']['status']='checked'
        self.assertIn('CHECKED_WITHOUT_RECORD',self.codes())

    def test_conditional_use_requires_recorded_conditions(self):
        p=self.data['assets'][0]['use_path']
        p['decision']='conditional'
        self.assertIn('CONDITIONS_MISSING',self.codes())
        self.assertIn('DECISION_WITHOUT_BASIS',self.codes())

    def test_unknown_model_cannot_be_silently_filled(self):
        self.data['ai_events'][0]['unknown_reason']=None
        self.assertIn('UNKNOWN_REASON_MISSING',self.codes())

    def test_triggered_obligation_requires_basis(self):
        self.data['obligations'][0]['applicability']='triggered'
        self.assertIn('OBLIGATION_BASIS_MISSING',self.codes())

    def test_voluntary_information_is_not_downstream_condition(self):
        o=self.data['obligations'][0]
        o['source_kind']='voluntary'
        o['downstream_requirement_basis']='测试假设：强制所有接收者保留工具名单'
        self.assertIn('VOLUNTARY_AS_DOWNSTREAM_RULE',self.codes())

    def test_untriggered_status_requires_matching_assessment(self):
        self.data['obligations'][0]['fulfillment']['status']='not_triggered'
        self.assertIn('TRIGGER_STATUS_MISMATCH',self.codes())

    def test_fulfilled_cannot_rely_on_pending_trigger(self):
        self.data['obligations'][0]['fulfillment']['status']='fulfilled'
        self.assertIn('TRIGGER_STATUS_MISMATCH',self.codes())

    def test_fulfilled_requires_delivery_evidence(self):
        o=self.triggered()
        o['fulfillment']['status']='fulfilled'
        self.assertIn('FULFILLMENT_RECORD_MISSING',self.codes())
        self.assertIn('DELIVERY_LOCATION_MISSING',self.codes())

    def test_fully_filled_fake_evidence_still_not_certified(self):
        o=self.triggered()
        o['fulfillment']={'status':'fulfilled','checked_by':'test-role','checked_at':'2026-10-23',
                         'evidence_ids':['V-test'],'result':'仅测试结构'}
        o['audience']='public';o['delivery_locations']=['测试位置，不是真实产品']
        result=checker.check(self.data)
        self.assertTrue(result['record_structure_valid'])
        self.assertEqual(result['legal_assessment'],'not_performed')
        self.assertEqual(result['release_assessment'],'not_performed')

    def test_published_statement_requires_location_and_review(self):
        self.data['assets'][0]['public_statements'][0]['status']='published'
        self.assertIn('PUBLICATION_RECORD_MISSING',self.codes())

    def test_mandatory_statement_requires_obligation(self):
        self.data['assets'][0]['public_statements'][0]['kind']='law'
        self.assertIn('STATEMENT_BASIS_MISSING',self.codes())

    def test_statement_does_not_relabel_statutory_basis_as_voluntary(self):
        self.data['assets'][0]['public_statements'][0]['obligation_ids']=['O-demo']
        self.assertIn('STATEMENT_KIND_MISMATCH',self.codes())

    def test_adoption_date_does_not_create_permission(self):
        self.data['adoption_date']='2026-10-23'
        self.assertIn('ADOPTION_PAIR_INCOMPLETE',self.codes())

    def test_raw_secret_field_rejected_without_echoing_value(self):
        self.data['runtime_services'][0]['api_key']='TEST-SECRET-MUST-NOT-APPEAR'
        result=checker.check(self.data)
        self.assertFalse(result['record_structure_valid'])
        self.assertNotIn('TEST-SECRET-MUST-NOT-APPEAR',json.dumps(result))

    def test_invalid_type_does_not_crash(self):
        self.data['assets']=True
        self.assertIn('SCHEMA',self.codes())

    def test_whitespace_does_not_fill_required_fact(self):
        self.data['assets'][0]['scope']='   '
        result=checker.check(self.data)
        self.assertFalse(result['record_structure_valid'])
        self.assertEqual(result['release_assessment'],'not_performed')

    def test_legacy_private_evidence_is_not_exported_or_evaluated(self):
        self.data['assets'][0]['legacy_record']={'private_text':'PRIVATE-TEST-VALUE'}
        result=checker.check(self.data)
        self.assertTrue(result['record_structure_valid'])
        self.assertNotIn('PRIVATE-TEST-VALUE',json.dumps(result))

    def test_cli_duplicate_json_keys_fail_without_echo(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'bad.json'
            path.write_text('{"key": "PRIVATE-TEST-VALUE", "key": 2}',encoding='utf-8')
            p=subprocess.run([sys.executable,str(ROOT/'tooling/check_disclosure.py'),str(path)],capture_output=True,text=True)
        self.assertEqual(p.returncode,1)
        self.assertNotIn('PRIVATE-TEST-VALUE',p.stdout+p.stderr)

    def test_actual_template_has_no_adoption_or_assets(self):
        self.assertEqual(self.blank['assets'],[])
        self.assertEqual(self.blank['runtime_services'],[])
        self.assertEqual(self.blank['ai_events'],[])
        self.assertIsNone(self.blank['adopted_license_version'])
        self.assertIsNone(self.blank['adoption_date'])

if __name__=='__main__':
    unittest.main()
