import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia import lifecycle as life
from yunxia.catalog import Catalog, reference
from yunxia.common import CatalogError, sha256


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=(Path(self.temp.name)/'repository').resolve();self.root.mkdir()
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        for key,value in (('user.name','Fixture'),('user.email','fixture@example.test'),('commit.gpgsign','false'),('core.hooksPath','/dev/null')):
            subprocess.run(['git','-C',str(self.root),'config',key,value],check=True)
        life.create(self.root,'fixture','Fixture','Only a test protocol',b'Test terms version 1.0.0\n','1.0.0')

    def commit(self):
        subprocess.run(['git','-C',str(self.root),'add','--all'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.root),'commit','-q','--allow-empty','--no-verify','-m','Fixture snapshot'],check=True,capture_output=True)

    def approve(self):
        info=life.inspect(self.root,'fixture')
        decision={'source_sha256':info['source_sha256'],'reviewer':'fixture maintainer','date':'2026-10-25','decision':'approved','basis':'test-only review evidence','open_issues':[]}
        life.review(self.root,'fixture',decision)
        self.commit()
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

    def test_freeze_requires_committed_review_and_source(self):
        self.approve()
        m=life.metadata(self.root,'fixture');m['review']['basis']='Uncommitted changed decision'
        (self.root/'agreements/fixture/agreement.json').write_bytes(life.encoded(m))
        self.error('REVIEW_NOT_COMMITTED',life.freeze,self.root,'fixture','rc1','notes')
        self.assertFalse((self.root/'registry/fixture/rc1.json').exists())

    def test_unrelated_commit_does_not_change_reviewed_materials(self):
        self.approve();original=life.inspect(self.root,'fixture')['source_sha256']
        (self.root/'unrelated.txt').write_text('Another protocol or tooling change')
        self.commit()
        self.assertEqual(life.inspect(self.root,'fixture')['source_sha256'],original)
        life.freeze(self.root,'fixture','rc1','notes')
        self.assertEqual(Catalog(self.root).select('fixture','rc1')['_text'],b'Test terms version 1.0.0\n')

    def check_uncommitted_review_file_deletion(self, relative, content):
        material=self.root/'agreements/fixture'/relative
        material.parent.mkdir(parents=True,exist_ok=True)
        material.write_text(content)
        self.commit()
        material.unlink()
        info=life.inspect(self.root,'fixture')
        life.review(self.root,'fixture',{'source_sha256':info['source_sha256'],
                    'reviewer':'fixture maintainer','date':'2026-11-02','decision':'approved',
                    'basis':'test-only review after removing material','open_issues':[]})
        subprocess.run(['git','-C',str(self.root),'add','agreements/fixture/agreement.json'],
                       check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.root),'commit','-q','--no-verify','-m','Approval only'],
                       check=True,capture_output=True)
        self.error('REVIEW_SOURCE_CHANGED',life.freeze,self.root,'fixture','rc1','notes')
        self.assertFalse((self.root/'registry/fixture/rc1.json').exists())
        tag=subprocess.run(['git','-C',str(self.root),'show-ref','--verify','--quiet',
                            'refs/tags/fixture/vrc1'],capture_output=True)
        self.assertEqual(tag.returncode,1)
        self.commit()
        result=life.freeze(self.root,'fixture','rc1','notes')
        self.assertEqual(result['status'],'candidate')
        fixed=Catalog(self.root).select('fixture','rc1')
        self.assertNotIn(relative,fixed['source']['snapshot']['files'])

    def test_freeze_requires_case_deletion_to_be_committed(self):
        self.check_uncommitted_review_file_deletion('cases.json','{"cases": []}\n')

    def test_freeze_requires_template_deletion_to_be_committed(self):
        self.check_uncommitted_review_file_deletion('templates/notice.md','Old template\n')

    def test_shared_review_dependencies_are_bound_to_frozen_git_materials(self):
        shared=self.root/'docs/shared.md';shared.parent.mkdir();shared.write_bytes(b'Original shared rule\n')
        path=self.root/'agreements/fixture/agreement.json'
        metadata=life.metadata(self.root,'fixture');metadata['review_dependencies']=['docs/shared.md']
        path.write_bytes(life.encoded(metadata))
        self.approve();life.freeze(self.root,'fixture','rc1','notes')
        before=Catalog(self.root).select('fixture','rc1')
        digest=sha256(b'Original shared rule\n')
        self.assertEqual(before['source']['snapshot']['format_version'],1)
        self.assertEqual(before['source']['snapshot']['dependencies'],{'docs/shared.md':digest})
        shared.write_bytes(b'Changed shared rule\n')
        self.assertFalse(life.inspect(self.root,'fixture')['review_current_and_approved'])
        fixed=Catalog(self.root)
        self.assertEqual(fixed.blobs[digest],b'Original shared rule\n')
        self.assertEqual(fixed.select('fixture','rc1')['_package_sha256'],before['_package_sha256'])

    def test_review_dependency_declaration_change_invalidates_approval(self):
        shared=self.root/'docs/shared.md';shared.parent.mkdir();shared.write_text('Shared rule')
        path=self.root/'agreements/fixture/agreement.json'
        metadata=life.metadata(self.root,'fixture');metadata['review_dependencies']=['docs/shared.md']
        path.write_bytes(life.encoded(metadata));self.approve()
        metadata=life.metadata(self.root,'fixture');metadata['review_dependencies']=[]
        path.write_bytes(life.encoded(metadata))
        self.assertFalse(life.inspect(self.root,'fixture')['review_current_and_approved'])
        self.error('REVIEW_REQUIRED_OR_STALE',life.freeze,self.root,'fixture','rc1','notes')

    def test_private_recursive_and_out_of_scope_review_dependencies_are_rejected(self):
        path=self.root/'agreements/fixture/agreement.json'
        metadata=life.metadata(self.root,'fixture')
        for rel in ('../outside.md','registry/fixture/1.json','agreements/fixture/agreement.json','docs/.private/secret.md','docs/private.key'):
            data=dict(metadata,review_dependencies=[rel]);path.write_bytes(life.encoded(data))
            with self.subTest(path=rel),self.assertRaises(CatalogError):life.inspect(self.root,'fixture')
        shared=self.root/'docs/shared.md';shared.parent.mkdir(exist_ok=True)
        external=Path(self.temp.name)/'outside.md';external.write_text('Private outside input')
        shared.symlink_to(external)
        path.write_bytes(life.encoded(dict(metadata,review_dependencies=['docs/shared.md'])))
        self.error('CATALOG_PATH_INVALID',life.inspect,self.root,'fixture')

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
        for name,digest in published['publication']['assets'].items():
            self.assertEqual(sha256((Path(self.temp.name)/'delivery'/name).read_bytes()),digest)
        self.assertTrue(life.verify_publication(self.root,'fixture','1.0.0')['publication_verified'])

    def test_publish_refuses_other_destination_contents_and_symlinks(self):
        self.freeze();target=Path(self.temp.name)/'delivery';target.mkdir();(target/'keep').write_text('keep')
        self.error('FIXED_VERSION_EXISTS',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',target,self.decision())
        self.assertEqual((target/'keep').read_text(),'keep')
        link=Path(self.temp.name)/'link';link.symlink_to(target,target_is_directory=True)
        self.error('PUBLICATION_PATH_INVALID',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',link,self.decision())

    def test_delivery_survives_recording_failure_and_retry_recovers(self):
        self.freeze();real=life.write
        def fail_record(path,content):
            if path==self.root/'registry/fixture/1.0.0.json' and json.loads(content).get('status')=='released':raise OSError('fixture disk failure')
            return real(path,content)
        target=Path(self.temp.name)/'delivery'
        with patch.object(life,'write',side_effect=fail_record):
            self.error('PUBLICATION_RECOVERY_REQUIRED',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',target,self.decision())
        self.assertTrue((target/'text.txt').is_file());self.assertEqual(Catalog(self.root).select('fixture','1.0.0')['status'],'prepared')
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
        self.publish();before=Catalog(self.root).select('fixture','1.0.0')['_text']
        life.revise(self.root,'fixture','1.0.0','2.0.0')
        self.assertIsNone(life.metadata(self.root,'fixture')['review'])
        (self.root/'agreements/fixture/text.txt').write_bytes(b'New draft')
        self.error('WORKING_TEXT_DIFFERS_FROM_BASE',life.revise,self.root,'fixture','1.0.0','3.0.0')
        self.assertEqual(Catalog(self.root).select('fixture','1.0.0')['_text'],before)

    def test_release_payload_tampering_or_extra_files_is_detected(self):
        self.freeze();p=self.root/'registry/fixture/1.0.0-rc.1.json'
        data=json.loads(p.read_bytes());data['notes']='changed';p.write_bytes(life.encoded(data))
        self.error('RELEASE_FILE_HASH_MISMATCH',Catalog,self.root)
        data['notes']='# Test-only release notes\n';data['files']['unexpected']='0'*64;p.write_bytes(life.encoded(data))
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

    def test_interrupted_pid_creation_is_absent_or_recoverable(self):
        script="""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from yunxia import common, lifecycle
original = common.os.link
def interrupted(source, destination):
    if sys.argv[3] == 'after':
        original(source, destination)
    print('interrupted', flush=True)
    sys.stdin.read()
    if sys.argv[3] == 'before':
        original(source, destination)
common.os.link = interrupted
with lifecycle.locked(Path(sys.argv[1])):
    pass
"""
        path=self.root/'.yunxia-maintenance.lock'
        for phase in ('before','after'):
            with self.subTest(phase=phase):
                process=subprocess.Popen([sys.executable,'-B','-c',script,str(self.root),
                    str(ROOT/'tooling/src'),phase],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,text=True)
                try:
                    self.assertEqual(process.stdout.readline().strip(),'interrupted')
                    process.kill();process.communicate(timeout=10)
                    if phase=='after':
                        self.assertEqual(json.loads(path.read_bytes()),{'pid':process.pid})
                    else:
                        self.assertFalse(path.exists())
                    self.assertEqual(life.recover_lock(self.root)['lock_recovered'],phase=='after')
                    with life.locked(self.root):
                        self.assertTrue(path.is_file())
                    self.assertFalse(path.exists())
                finally:
                    if process.poll() is None:process.kill()
                    process.communicate(timeout=10)

    def test_recovery_preserves_preexisting_malformed_pid_records(self):
        path=self.root/'.yunxia-maintenance.lock'
        for content,code in ((b'','CATALOG_FORMAT'),(b'{"pid":true}','MAINTENANCE_LOCK_INVALID')):
            with self.subTest(content=content):
                path.write_bytes(content)
                self.error(code,life.recover_lock,self.root)
                self.assertEqual(path.read_bytes(),content)
        path.unlink()

    def test_only_dead_maintenance_lock_can_be_recovered(self):
        (self.root/'.yunxia-maintenance.lock').write_bytes(life.encoded({'pid':123456789}))
        with patch.object(life.os,'kill',side_effect=ProcessLookupError):
            self.assertTrue(life.recover_lock(self.root)['lock_recovered'])
        self.assertFalse((self.root/'.yunxia-maintenance.lock').exists())

    def test_recovery_serializes_pid_check_and_removal_with_all_other_owners(self):
        path=self.root/'.yunxia-maintenance.lock'
        path.write_bytes(life.encoded({'pid':123456789}))
        def dead_pid(pid,signal):
            self.error('MAINTENANCE_LOCKED',life.recover_lock,self.root)
            with self.assertRaises(CatalogError) as cm:
                with life.locked(self.root):pass
            self.assertEqual(cm.exception.code,'MAINTENANCE_LOCKED')
            raise ProcessLookupError
        with patch.object(life.os,'kill',side_effect=dead_pid):
            self.assertTrue(life.recover_lock(self.root)['lock_recovered'])
        guard=self.root/'.yunxia-maintenance.guard'
        inode=guard.stat().st_ino
        with life.locked(self.root):
            self.error('MAINTENANCE_LOCKED',life.recover_lock,self.root)
            self.assertTrue(path.exists())
        self.assertEqual(guard.stat().st_ino,inode)
        self.assertFalse(path.exists())

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
        p=self.root/'registry/fixture/1.0.0-rc.1.json'
        data=json.loads(p.read_text());data['source']['snapshot']['files']['text.txt']='0'*64
        p.write_bytes(life.encoded(data))
        self.error('RELEASE_REVIEW_INVALID',Catalog,self.root)

    def test_data_cannot_claim_adapter_capabilities_in_release_record(self):
        self.freeze();p=self.root/'registry/fixture/1.0.0-rc.1.json'
        data=json.loads(p.read_text());data['renderer']='scope-license-v1';p.write_bytes(life.encoded(data))
        self.error('RELEASE_CONTAINS_ADAPTER_CONFIG',Catalog,self.root)

    def test_candidate_cannot_consume_its_final_version_name(self):
        self.approve()
        self.error('CANDIDATE_VERSION_EQUALS_TARGET',life.freeze,self.root,'fixture','1.0.0','notes')
        self.assertFalse((self.root/'registry/fixture/1.0.0.json').exists())

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

    def test_working_revision_base_must_exist_and_match_fixed_bytes(self):
        self.publish();life.revise(self.root,'fixture','1.0.0','2.0.0')
        path=self.root/'agreements/fixture/agreement.json'
        original=path.read_bytes()
        for field,value,code in [('version','missing','AGREEMENT_NOT_AVAILABLE'),
                                 ('sha256','0'*64,'AGREEMENT_PIN_MISMATCH')]:
            metadata=json.loads(original);metadata['base'][field]=value
            path.write_bytes(life.encoded(metadata))
            self.error(code,Catalog,self.root)
            self.error(code,life.inspect,self.root,'fixture')
            self.assertEqual(path.read_bytes(),life.encoded(metadata))
        path.write_bytes(original)
        self.assertEqual(Catalog(self.root).resolve(life.metadata(self.root,'fixture')['base'])['version'],'1.0.0')

    def test_historical_revision_chain_is_checked_after_working_base_moves_on(self):
        self.publish();life.revise(self.root,'fixture','1.0.0','2.0.0')
        self.approve();life.freeze(self.root,'fixture','2.0.0-rc.1','Version 2 notes')
        candidate=Catalog(self.root).select('fixture','2.0.0-rc.1')
        decision=dict(self.decision(),candidate_sha256=candidate['_package_sha256'])
        life.publish(self.root,'fixture','2.0.0-rc.1','2.0.0',Path(self.temp.name)/'delivery-2',decision)
        life.revise(self.root,'fixture','2.0.0','3.0.0')
        self.assertEqual(life.metadata(self.root,'fixture')['base']['version'],'2.0.0')
        (self.root/'registry/fixture/1.0.0.json').unlink()
        with self.assertRaises(CatalogError) as cm:Catalog(self.root)
        self.assertEqual(cm.exception.code,'AGREEMENT_NOT_AVAILABLE')
        self.assertTrue(set(cm.exception.paths)&{'registry/fixture/2.0.0.json','registry/fixture/2.0.0-rc.1.json'})

    def test_fixed_revision_cannot_rewrite_committed_review(self):
        self.publish();life.revise(self.root,'fixture','1.0.0','2.0.0')
        self.approve();life.freeze(self.root,'fixture','2.0.0-rc.1','Version 2 notes')
        path=self.root/'registry/fixture/2.0.0-rc.1.json'
        metadata=json.loads(path.read_bytes())
        snapshot=metadata['source']['snapshot']
        snapshot['agreement']['base']['sha256']='0'*64
        snapshot_sha=sha256(life.encoded(snapshot))
        metadata['source']['source_sha256']=snapshot_sha
        metadata['review'].update(source_snapshot=snapshot,source_sha256=snapshot_sha)
        metadata['files']['review.json']=sha256(life.encoded(metadata['review']))
        path.write_bytes(life.encoded(metadata))
        self.error('RELEASE_REVIEW_INVALID',Catalog,self.root)

    def test_malformed_review_cannot_create_an_unreadable_fixed_candidate(self):
        m=life.metadata(self.root,'fixture')
        m['review']={'decision':'approved','open_issues':[],'source_sha256':life.inspect(self.root,'fixture')['source_sha256']}
        (self.root/'agreements/fixture/agreement.json').write_bytes(life.encoded(m))
        self.error('REVIEW_FORMAT',life.freeze,self.root,'fixture','rc1','notes')
        self.assertFalse((self.root/'registry/fixture/rc1.json').exists())

    def test_registered_git_source_cannot_fall_back_to_working_text(self):
        self.freeze();path=self.root/'registry/fixture/1.0.0-rc.1.json'
        data=json.loads(path.read_bytes());data['source']['files']['text.txt']='missing.txt'
        path.write_bytes(life.encoded(data))
        with self.assertRaises(CatalogError):Catalog(self.root)

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
        self.assertEqual(Catalog(self.root).select('fixture','1.0.0')['status'],'prepared')

    def test_existing_destination_is_rejected_without_reading_unrelated_files(self):
        self.freeze();target=(Path(self.temp.name)/'delivery').resolve();target.mkdir()
        secret=target/'unrelated-private.txt';secret.write_text('not a release file')
        original=Path.read_bytes
        def guarded(path):
            if path==secret:raise AssertionError('unrelated content must not be read')
            return original(path)
        with patch.object(Path,'read_bytes',guarded):
            self.error('FIXED_VERSION_EXISTS',life.publish,self.root,'fixture','1.0.0-rc.1','1.0.0',target,self.decision())

    def test_fixed_drafts_resolve_by_current_reference(self):
        c=Catalog(ROOT)
        for e in c.entries:
            ref=reference(e)
            self.assertEqual(set(ref),{'agreement_id','version','sha256'})
            self.assertEqual(sha256(c.resolve(ref)['_text']),ref['sha256'])


if __name__=='__main__':unittest.main()
