import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia import lifecycle as life
from yunxia.catalog import Catalog, CatalogError, reference, sha256


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=(Path(self.temp.name)/'repository').resolve();self.root.mkdir()
        life.create(self.root,'fixture','Fixture','Only a test protocol',b'Test terms version 1.0.0\n','1.0.0')

    def approve(self):
        info=life.inspect(self.root,'fixture')
        decision={'source_sha256':info['source_sha256'],'reviewer':'fixture maintainer','date':'2026-10-25','decision':'approved','basis':'test-only review evidence','open_issues':[]}
        life.review(self.root,'fixture',decision)
        return decision

    def freeze(self):
        self.approve();return life.freeze(self.root,'fixture','1.0.0-rc.1','# Test-only release notes\n')

    def decision(self):
        entry=Catalog(self.root).select('fixture','1.0.0-rc.1')
        return {'candidate_sha256':entry['_package_sha256'],'confirmed_by':'fixture maintainer','date':'2026-10-25','basis':'test-only publication decision'}

    def publish(self):
        self.freeze()
        return life.publish(self.root,'fixture','1.0.0-rc.1','1.0.0',Path(self.temp.name)/'delivery',self.decision())

    def error(self,code,fn,*a,**kw):
        with self.assertRaises(CatalogError) as cm:fn(*a,**kw)
        self.assertEqual(cm.exception.code,code)

    def test_create_is_not_release_and_cannot_replace_existing_agreement(self):
        self.assertEqual(Catalog(self.root).entries,[])
        self.error('AGREEMENT_EXISTS',life.create,self.root,'fixture','New','Purpose',b'Other text','2')

    def test_review_requires_exact_source_and_zero_open_issues(self):
        d=self.approve();d['source_sha256']='0'*64
        self.error('REVIEW_SOURCE_CHANGED',life.review,self.root,'fixture',d)
        d=self.approve();d['open_issues']=['unresolved']
        self.error('REVIEW_HAS_OPEN_ISSUES',life.review,self.root,'fixture',d)

    def test_freeze_requires_review_and_stale_text_cannot_reuse_it(self):
        self.error('REVIEW_REQUIRED_OR_STALE',life.freeze,self.root,'fixture','1-rc','notes')
        self.approve();(self.root/'agreements/fixture/text.txt').write_bytes(b'Changed terms')
        self.error('REVIEW_REQUIRED_OR_STALE',life.freeze,self.root,'fixture','1-rc','notes')

    def test_changed_adoption_guide_invalidates_source_review(self):
        self.approve();(self.root/'agreements/fixture/adoption.md').write_text('Changed adoption rules')
        self.assertFalse(life.inspect(self.root,'fixture')['review_current_and_approved'])

    def test_candidate_is_immutable_and_does_not_enable_adapter(self):
        self.freeze();entry=Catalog(self.root).select('fixture','1.0.0-rc.1')
        self.assertEqual(entry['status'],'candidate');self.assertEqual(entry['capabilities'],['inspect'])
        self.assertIsNone(entry['publication'])
        self.error('FIXED_VERSION_EXISTS',life.freeze,self.root,'fixture','1.0.0-rc.1','other notes')

    def test_publication_requires_matching_decision_and_target_version(self):
        self.freeze();d=self.decision();d['candidate_sha256']='0'*64
        target=Path(self.temp.name)/'delivery'
        self.error('PUBLICATION_CANDIDATE_CHANGED',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',target,d)
        self.assertFalse(target.exists())
        self.error('PUBLICATION_VERSION_MISMATCH',life.publish,self.root,'fixture','1.0.0-rc.1','9.0.0',target,self.decision())

    def test_publish_verifies_bytes_and_is_idempotent(self):
        result=self.publish();self.assertTrue(result['delivery_verified'])
        published=Catalog(self.root).select('fixture','1.0.0')
        self.assertEqual(published['_text'],b'Test terms version 1.0.0\n')
        self.assertEqual(published['status'],'released')
        again=life.publish(self.root,'fixture','1.0.0-rc.1','1.0.0',Path(self.temp.name)/'delivery',self.decision())
        self.assertEqual(result,again)
        for p in (self.root/'releases/fixture/1.0.0').iterdir():
            self.assertEqual(p.read_bytes(),(Path(self.temp.name)/'delivery'/p.name).read_bytes())

    def test_publish_refuses_other_destination_contents_and_symlinks(self):
        self.freeze();target=Path(self.temp.name)/'delivery';target.mkdir();(target/'keep').write_text('keep')
        self.error('FIXED_VERSION_EXISTS',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',target,self.decision())
        self.assertEqual((target/'keep').read_text(),'keep')
        link=Path(self.temp.name)/'link';link.symlink_to(target,target_is_directory=True)
        self.error('PUBLICATION_PATH_INVALID',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',link,self.decision())

    def test_delivery_survives_recording_failure_and_retry_recovers(self):
        self.freeze();real=life.put_new_tree
        def fail_record(path,files):
            if self.root in path.parents:raise OSError('fixture disk failure')
            return real(path,files)
        target=Path(self.temp.name)/'delivery'
        with patch.object(life,'put_new_tree',side_effect=fail_record):
            self.error('PUBLICATION_RECOVERY_REQUIRED',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',target,self.decision())
        self.assertTrue((target/'text.txt').is_file());self.assertFalse((self.root/'releases/fixture/1.0.0').exists())
        life.publish(self.root,'fixture','1.0.0-rc.1','1.0.0',target,self.decision())
        self.assertEqual(Catalog(self.root).select('fixture','1.0.0')['status'],'released')

    def test_recommendation_requires_formal_release_and_can_be_removed(self):
        self.freeze();self.error('RECOMMENDATION_REQUIRES_RELEASE',life.recommend,self.root,'fixture','1.0.0-rc.1','test')
        life.publish(self.root,'fixture','1.0.0-rc.1','1.0.0',Path(self.temp.name)/'delivery',self.decision())
        life.recommend(self.root,'fixture','1.0.0','tested')
        self.assertEqual(Catalog(self.root).recommended_by_agreement['fixture']['version'],'1.0.0')
        life.recommend(self.root,'fixture',None,'stopped recommending')
        self.assertIsNone(Catalog(self.root).recommended_by_agreement['fixture'])
        self.assertEqual(Catalog(self.root).select('fixture','1.0.0')['status'],'released')

    def test_revision_preserves_releases_and_never_overwrites_working_changes(self):
        self.publish();before=(self.root/'releases/fixture/1.0.0/text.txt').read_bytes()
        life.revise(self.root,'fixture','1.0.0','2.0.0')
        self.assertIsNone(life.metadata(self.root,'fixture')['review'])
        (self.root/'agreements/fixture/text.txt').write_bytes(b'New draft')
        self.error('WORKING_TEXT_DIFFERS_FROM_BASE',life.revise,self.root,'fixture','1.0.0','3.0.0')
        self.assertEqual((self.root/'releases/fixture/1.0.0/text.txt').read_bytes(),before)

    def test_release_payload_tampering_or_extra_files_is_detected(self):
        self.freeze();p=self.root/'releases/fixture/1.0.0-rc.1/notes.md';p.write_text('changed')
        self.error('RELEASE_FILE_HASH_MISMATCH',Catalog,self.root)
        p.write_text('# Test-only release notes\n');(p.parent/'unexpected').write_text('extra')
        self.error('RELEASE_FILE_SET_MISMATCH',Catalog,self.root)

    def test_traversal_cannot_create_outside_repository(self):
        self.error('INVALID_ID',life.create,self.root,'../escape','Name','Purpose',b'text','1')
        self.assertFalse((Path(self.temp.name)/'escape').exists())

    def test_paused_and_retired_maintenance_need_explicit_resume(self):
        self.freeze()
        life.maintain(self.root,'fixture','paused','fixture pause')
        self.error('MAINTENANCE_INACTIVE',life.freeze,self.root,'fixture','another-rc','notes')
        self.error('MAINTENANCE_INACTIVE',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',Path(self.temp.name)/'delivery',self.decision())
        life.maintain(self.root,'fixture','active','fixture resume')
        life.publish(self.root,'fixture','1.0.0-rc.1','1.0.0',Path(self.temp.name)/'delivery',self.decision())
        life.maintain(self.root,'fixture','retired','fixture retire')
        self.assertEqual(Catalog(self.root).select('fixture','1.0.0')['status'],'released')
        self.error('MAINTENANCE_INACTIVE',life.recommend,self.root,'fixture','1.0.0','test')

    def test_adapter_registration_connects_new_protocol_without_releasing_it(self):
        self.freeze()
        p=subprocess.run([sys.executable,'-B',str(ROOT/'tooling/yunxia.py'),'adapter','--repository',str(self.root),'--agreement','fixture','--version','1.0.0-rc.1','--output-name','LICENSE.fixture-1.txt'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        entry=Catalog(self.root).select('fixture','1.0.0-rc.1')
        self.assertIn('adopt',entry['capabilities'])
        self.assertEqual(entry['status'],'candidate')

    def test_maintenance_lock_blocks_other_writer(self):
        with life.locked(self.root):
            with self.assertRaises(CatalogError) as cm:
                with life.locked(self.root):pass
            self.assertEqual(cm.exception.code,'MAINTENANCE_LOCKED')
            self.error('MAINTENANCE_LOCKED',life.recover_lock,self.root)

    def test_only_dead_maintenance_lock_can_be_recovered(self):
        (self.root/'.yunxia-maintenance.lock').write_bytes(life.encoded({'pid':123456789}))
        with patch.object(life.os,'kill',side_effect=ProcessLookupError):
            self.assertTrue(life.recover_lock(self.root)['lock_recovered'])
        self.assertFalse((self.root/'.yunxia-maintenance.lock').exists())

    def test_cli_full_lifecycle_commands_do_not_need_skill_install(self):
        p=subprocess.run([sys.executable,'-B',str(ROOT/'tooling/yunxia.py'),'agreement','--repository',str(self.root),'inspect','--id','fixture'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertFalse(json.loads(p.stdout)['review_current_and_approved'])

    def test_cli_help_lists_project_and_agreement_arguments(self):
        for args,expected in [(['--help'],'project'),(['agreement','create','--help'],'--text'),(['agreement','create','--help'],'--id')]:
            p=subprocess.run([sys.executable,'-B',str(ROOT/'tooling/yunxia.py'),*args],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertIn(expected,p.stdout)

    def test_build_rejects_unknown_arguments_before_mutation(self):
        p=subprocess.run([sys.executable,'-B',str(ROOT/'tooling/yunxia.py'),'build','--repository',str(self.root),'--bogus'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.root/'dist').exists())

    def test_incoherent_review_cannot_be_declared_a_valid_candidate(self):
        self.freeze()
        p=self.root/'releases/fixture/1.0.0-rc.1/release.json'
        data=json.loads(p.read_text());data['source']['snapshot']['files']['text.txt']='0'*64
        p.write_bytes(life.encoded(data))
        self.error('RELEASE_REVIEW_INVALID',Catalog,self.root)

    def test_data_cannot_claim_adapter_capabilities_in_release_record(self):
        self.freeze();p=self.root/'releases/fixture/1.0.0-rc.1/release.json'
        data=json.loads(p.read_text());data['renderer']='scope-license-v1';p.write_bytes(life.encoded(data))
        self.error('RELEASE_CONTAINS_ADAPTER_CONFIG',Catalog,self.root)

    def test_candidate_cannot_consume_its_final_version_name(self):
        self.approve()
        self.error('CANDIDATE_VERSION_EQUALS_TARGET',life.freeze,self.root,'fixture','1.0.0','notes')
        self.assertFalse((self.root/'releases/fixture/1.0.0').exists())

    def test_existing_final_version_cannot_be_reused_for_a_new_revision(self):
        self.publish()
        before=(self.root/'agreements/fixture/agreement.json').read_bytes()
        self.error('TARGET_VERSION_EXISTS',life.revise,self.root,'fixture','1.0.0','1.0.0')
        self.assertEqual((self.root/'agreements/fixture/agreement.json').read_bytes(),before)
        self.error('TARGET_VERSION_EXISTS',life.freeze,self.root,'fixture','another-rc','notes')

    def test_scenarios_and_templates_are_bound_to_review(self):
        folder=self.root/'agreements/fixture'
        (folder/'cases.json').write_text('{"cases": []}')
        (folder/'templates').mkdir();(folder/'templates/notice.md').write_text('Original template')
        self.approve()
        (folder/'cases.json').write_text('{"cases": ["changed"]}')
        self.assertFalse(life.inspect(self.root,'fixture')['review_current_and_approved'])
        self.approve();(folder/'templates/notice.md').write_text('Changed template')
        self.assertFalse(life.inspect(self.root,'fixture')['review_current_and_approved'])
        self.approve();(folder/'templates/extra.md').write_text('New template')
        self.error('REVIEW_REQUIRED_OR_STALE',life.freeze,self.root,'fixture','rc1','notes')

    def test_revision_review_records_the_exact_base(self):
        self.publish();life.revise(self.root,'fixture','1.0.0','2.0.0')
        snapshot=life.inspect(self.root,'fixture')['source_snapshot']
        self.assertEqual(snapshot['agreement']['base'],reference(Catalog(self.root).select('fixture','1.0.0')))

    def test_malformed_review_cannot_create_an_unreadable_fixed_candidate(self):
        m=life.metadata(self.root,'fixture')
        m['review']={'decision':'approved','open_issues':[],'source_sha256':life.inspect(self.root,'fixture')['source_sha256']}
        (self.root/'agreements/fixture/agreement.json').write_bytes(life.encoded(m))
        self.error('REVIEW_FORMAT',life.freeze,self.root,'fixture','rc1','notes')
        self.assertFalse((self.root/'releases/fixture/rc1').exists())

    def test_missing_fixed_record_is_reported_instead_of_hidden(self):
        self.freeze();(self.root/'releases/fixture/1.0.0-rc.1/release.json').unlink()
        self.error('RELEASE_RECORD_MISSING',Catalog,self.root)

    def test_incomplete_agreement_directory_is_reported(self):
        (self.root/'agreements/fixture/agreement.json').unlink()
        self.error('AGREEMENT_RECORD_MISSING',Catalog,self.root)

    def test_recommendation_cannot_cross_protocol_identity(self):
        self.publish();life.create(self.root,'other','Other','Other fixture',b'Other test terms\n','1.0.0')
        m=life.metadata(self.root,'other');m['recommended']=reference(Catalog(self.root).select('fixture','1.0.0'))
        (self.root/'agreements/other/agreement.json').write_bytes(life.encoded(m))
        self.error('RECOMMENDATION_AGREEMENT_MISMATCH',Catalog,self.root)

    def test_source_metadata_and_template_symlinks_are_not_followed(self):
        p=self.root/'agreements/fixture/agreement.json'
        external=Path(self.temp.name)/'external.json';external.write_bytes(p.read_bytes());p.unlink();p.symlink_to(external)
        self.error('CATALOG_PATH_INVALID',life.inspect,self.root,'fixture')

    def test_publication_reads_back_delivery_before_recording_success(self):
        self.freeze();target=Path(self.temp.name)/'delivery';real=life.put_new_tree
        def changed(path,files):
            real(path,files)
            if path==target.resolve():(path/'text.txt').write_bytes(b'Changed after delivery write')
        with patch.object(life,'put_new_tree',side_effect=changed):
            self.error('PUBLICATION_DELIVERY_CHANGED',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',target,self.decision())
        self.assertFalse((self.root/'releases/fixture/1.0.0').exists())

    def test_existing_destination_is_rejected_without_reading_unrelated_files(self):
        self.freeze();target=(Path(self.temp.name)/'delivery').resolve();target.mkdir()
        secret=target/'unrelated-private.txt';secret.write_text('not a release file')
        original=Path.read_bytes
        def guarded(path):
            if path==secret:raise AssertionError('unrelated content must not be read')
            return original(path)
        with patch.object(Path,'read_bytes',guarded):
            self.error('FIXED_VERSION_EXISTS',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',target,self.decision())

    def test_imported_drafts_and_legacy_references_remain_exact(self):
        c=Catalog(ROOT)
        for e in c.entries:
            for old in e['legacy_references']:
                self.assertEqual(e['status'],'imported-draft')
                resolved=c.resolve(old)
                self.assertEqual(reference(resolved),old)
                self.assertEqual(sha256(resolved['_text']),old['sha256'])


if __name__=='__main__':unittest.main()
