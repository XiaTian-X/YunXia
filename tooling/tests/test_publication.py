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
from yunxia import lifecycle as life, gitstore
from yunxia.catalog import Catalog
from yunxia.common import CatalogError, sha256
from support import git_commit, write_repository


class FakeGitHub:
    """A remote store, independent of local intent and completion writes."""
    def __init__(self):
        self.pushed={};self.tags={};self.sources=set();self.current=None;self.contents={}
        self.created=0;self.uploaded=0;self.published=0;self.fail_upload=False

    def verify_registry(self, commit, path, expected):
        if self.pushed.get((commit,path))!=expected:raise CatalogError('PUBLICATION_REGISTRY_NOT_PUSHED')

    def ensure_tag(self, tag, commit):
        if tag in self.tags and self.tags[tag]!=commit:raise CatalogError('PUBLICATION_TAG_CONFLICT')
        self.tags[tag]=commit

    def tag_commit(self, tag):return self.tags.get(tag)
    def verify_source(self, commit):
        if commit not in self.sources:raise CatalogError('PUBLICATION_SOURCE_NOT_PUSHED')
    def release(self, tag):return copy.deepcopy(self.current)

    def create_draft(self, tag, commit, title, notes):
        self.created+=1
        self.current={'id':7,'tag_name':tag,'draft':True,'html_url':'https://example.test/release/7'}
        return copy.deepcopy(self.current)

    def assets(self, release):return [{'id':i,'name':name} for i,name in enumerate(self.contents,1)]
    def download(self, asset):return self.contents[list(self.contents)[asset['id']-1]]

    def upload(self, tag, name, content):
        if name in self.contents:raise AssertionError('An existing asset must never be replaced')
        self.contents[name]=content;self.uploaded+=1
        if self.fail_upload:
            self.fail_upload=False
            raise CatalogError('GITHUB_UPLOAD_UNCERTAIN')

    def publish(self, release):
        self.published+=1;self.current['draft']=False
        return copy.deepcopy(self.current)


class PublicationRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'repo';self.root.mkdir()
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        for key,value in [('user.name','Fixture'),('user.email','fixture@example.test'),('commit.gpgsign','false'),('core.hooksPath','/dev/null')]:
            subprocess.run(['git','-C',str(self.root),'config',key,value],check=True)
        write_repository(self.root)
        life.create(self.root,'fixture','Fixture','Test only',b'Exact terms\n','1.0.0')
        info=life.inspect(self.root,'fixture')
        life.review(self.root,'fixture',{'source_sha256':info['source_sha256'],'reviewer':'Fixture','date':'2026-11-02','decision':'approved','basis':'Test only','open_issues':[]})
        git_commit(self.root)
        life.freeze(self.root,'fixture','1.0.0-rc.1','# Fixed notes\n')
        entry=Catalog(self.root).select('fixture','1.0.0-rc.1')
        self.decision={'date':'2026-11-02','confirmed_by':'Fixture','basis':'Test only','candidate_sha256':entry['_package_sha256']}
        self.backend=FakeGitHub()
        self.path=self.root/'registry/fixture/1.0.0.json'

    def prepare(self):
        return life.prepare_publication(self.root,'fixture','1.0.0-rc.1','1.0.0',None,self.decision,'fixture/repository')

    def push(self):
        commit=git_commit(self.root)
        catalog=Catalog(self.root)
        for rel in catalog.inputs:
            self.backend.pushed[(commit,rel)]=gitstore.read_blob(self.root,commit,rel)
        for entry in catalog.entries:
            self.backend.sources.add(entry['source']['commit'])
            if entry['source']['tag'] is not None:
                self.backend.tags[entry['source']['tag']]=entry['source']['commit']

    def publish(self):
        return life.publish_github(self.root,'fixture','1.0.0-rc.1','1.0.0','fixture/repository',self.decision,self.backend)

    def error(self,code,call):
        with self.assertRaises(CatalogError) as cm:call()
        self.assertEqual(cm.exception.code,code)

    def test_prepare_requires_commit_and_push_before_remote_side_effects(self):
        self.prepare()
        self.assertEqual(Catalog(self.root).select('fixture','1.0.0')['status'],'prepared')
        self.error('PUBLICATION_REGISTRY_NOT_COMMITTED',self.publish)
        git_commit(self.root)
        self.error('PUBLICATION_REGISTRY_NOT_PUSHED',self.publish)
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(0,0,0))

    def test_remote_publication_is_idempotent_and_final_receipt_must_be_pushed(self):
        self.prepare();self.push();result=self.publish()
        self.assertTrue(result['registry_sync_required'])
        self.error('PUBLICATION_REGISTRY_NOT_COMMITTED',lambda:life.verify_publication(self.root,'fixture','1.0.0',self.backend))
        self.push()
        again=self.publish()
        self.assertFalse(again['registry_sync_required'])
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(1,1,1))
        self.assertTrue(life.verify_publication(self.root,'fixture','1.0.0',self.backend)['registry_sync_verified'])

    def test_upload_timeout_queries_existing_asset_on_retry(self):
        self.prepare();self.push();self.backend.fail_upload=True
        self.error('GITHUB_UPLOAD_UNCERTAIN',self.publish)
        self.assertTrue(self.backend.current['draft'])
        self.assertEqual(Catalog(self.root).select('fixture','1.0.0')['status'],'prepared')
        self.publish()
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(1,1,1))

    def test_public_release_survives_completion_record_failure(self):
        self.prepare();self.push();real=life.write
        def fail_receipt(path,content):
            if path==self.path and json.loads(content).get('status')=='released':raise OSError('Disk unavailable')
            return real(path,content)
        with patch.object(life,'write',side_effect=fail_receipt):
            self.error('PUBLICATION_RECOVERY_REQUIRED',self.publish)
        self.assertFalse(self.backend.current['draft'])
        self.assertEqual(json.loads(self.path.read_bytes())['status'],'prepared')
        self.publish()
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(1,1,1))

    def test_remote_tag_conflict_is_not_rebound(self):
        self.prepare();self.push();self.backend.tags['fixture/v1.0.0']='0'*40
        self.error('PUBLICATION_TAG_CONFLICT',self.publish)
        self.assertEqual(self.backend.created,0)

    def test_same_version_with_different_publication_intent_is_rejected(self):
        self.prepare();before=self.path.read_bytes();self.decision['basis']='Different authorization'
        self.error('PUBLICATION_INTENT_CONFLICT',self.prepare)
        self.assertEqual(self.path.read_bytes(),before)

    def test_remote_unknown_asset_is_not_overwritten_or_ignored(self):
        self.prepare();self.push();self.backend.contents['unexpected.txt']=b'unrelated'
        self.error('PUBLICATION_ASSET_CONFLICT',self.publish)
        self.assertTrue(self.backend.current['draft'])
        self.assertEqual(self.backend.published,0)
        self.assertEqual(self.backend.contents['unexpected.txt'],b'unrelated')

    def test_publication_fact_does_not_regress_after_remote_loss(self):
        self.prepare();self.push();self.publish();self.push()
        before=self.path.read_bytes();self.backend.current=None
        self.error('PUBLICATION_NOT_PUBLIC',self.publish)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.backend.created,1)

    def test_artifact_tampering_does_not_rewrite_publication_receipt(self):
        self.prepare();self.push();self.publish();self.push();before=self.path.read_bytes()
        name=next(iter(self.backend.contents));self.backend.contents[name]=b'Changed remote artifact'
        self.error('PUBLICATION_ASSET_CONFLICT',lambda:life.verify_publication(self.root,'fixture','1.0.0',self.backend))
        self.assertEqual(self.path.read_bytes(),before)

    def test_withdrawal_preserves_history_and_removes_recommendation(self):
        self.prepare();self.push();self.publish()
        life.recommend(self.root,'fixture','1.0.0','Fixture recommendation')
        old=Catalog(self.root).select('fixture','1.0.0')
        life.withdraw(self.root,'fixture','1.0.0',{'date':'2026-11-02','confirmed_by':'Fixture','reason':'Fixture withdrawal'})
        current=Catalog(self.root).select('fixture','1.0.0')
        self.assertEqual(current['publication'],old['publication'])
        self.assertEqual(current['_text'],old['_text'])
        self.assertIsNone(life.metadata(self.root,'fixture')['recommended'])
        self.error('RECOMMENDATION_REQUIRES_RELEASE',lambda:life.recommend(self.root,'fixture','1.0.0','No'))
        self.error('PUBLICATION_WITHDRAWN',self.publish)

    def test_archive_is_exactly_reproducible_across_tool_version_changes(self):
        self.prepare();self.push();self.backend.fail_upload=True
        self.error('GITHUB_UPLOAD_UNCERTAIN',self.publish)
        original=copy.deepcopy(self.backend.contents)
        with patch('yunxia.publication.VERSION','future-tool'):
            self.publish()
        self.assertEqual(self.backend.contents,original)

    def test_preserved_candidate_must_be_committed_before_remote_effects(self):
        self.prepare()
        subprocess.run(['git','-C',str(self.root),'add','registry/fixture/1.0.0.json'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.root),'commit','-q','-m','Only prepared record'],check=True,capture_output=True)
        self.error('PUBLICATION_REGISTRY_NOT_COMMITTED',self.publish)
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(0,0,0))

    def test_uncommitted_metadata_repair_cannot_hide_broken_published_registry(self):
        self.prepare()
        path=self.root/'agreements/fixture/agreement.json';valid=path.read_bytes()
        invalid=json.loads(valid);invalid.pop('name');path.write_bytes(life.encoded(invalid))
        git_commit(self.root);path.write_bytes(valid)
        self.error('PUBLICATION_REGISTRY_NOT_COMMITTED',self.publish)
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(0,0,0))

    def test_preserved_candidate_record_and_tag_must_be_pushed(self):
        self.prepare();self.push()
        key=(gitstore.head(self.root),'registry/fixture/1.0.0-rc.1.json')
        saved=self.backend.pushed.pop(key)
        self.error('PUBLICATION_REGISTRY_NOT_PUSHED',self.publish)
        self.backend.pushed[key]=saved
        self.backend.tags.pop('fixture/v1.0.0-rc.1')
        self.error('PUBLICATION_DEPENDENCY_TAG_NOT_PUSHED',self.publish)
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(0,0,0))
        self.backend.tags['fixture/v1.0.0-rc.1']=gitstore.head(self.root)
        self.error('PUBLICATION_TAG_CONFLICT',self.publish)

    def test_revision_requires_pushed_base_and_its_preserved_candidate(self):
        self.prepare();self.push();self.publish();self.push()
        life.revise(self.root,'fixture','1.0.0','2.0.0')
        info=life.inspect(self.root,'fixture')
        life.review(self.root,'fixture',{'source_sha256':info['source_sha256'],'reviewer':'Fixture',
                    'date':'2026-11-02','decision':'approved','basis':'Test only','open_issues':[]})
        git_commit(self.root);life.freeze(self.root,'fixture','2.0.0-rc.1','Revision notes')
        entry=Catalog(self.root).select('fixture','2.0.0-rc.1')
        decision=dict(self.decision,candidate_sha256=entry['_package_sha256'])
        life.prepare_publication(self.root,'fixture','2.0.0-rc.1','2.0.0',None,decision,'fixture/repository')
        self.backend=FakeGitHub();self.push()
        publish=lambda:life.publish_github(self.root,'fixture','2.0.0-rc.1','2.0.0','fixture/repository',decision,self.backend)
        key=(gitstore.head(self.root),'registry/fixture/1.0.0-rc.1.json')
        saved=self.backend.pushed.pop(key)
        self.error('PUBLICATION_REGISTRY_NOT_PUSHED',publish)
        self.backend.pushed[key]=saved
        self.backend.tags.pop('fixture/v1.0.0')
        self.error('PUBLICATION_DEPENDENCY_TAG_NOT_PUSHED',publish)
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(0,0,0))

    def test_invalid_remote_release_fields_preserve_prepared_and_allow_retry(self):
        self.prepare();self.push();before=self.path.read_bytes()
        record=json.loads(before);tag=record['source']['tag']
        valid=self.backend.create_draft(tag,record['source']['commit'],'Fixture','Notes')
        for key,value in [('id',None),('id',True),('id','7'),('id',0),('tag_name',None),
                          ('draft',None),('html_url',None),('html_url',''),('html_url','  ')]:
            with self.subTest(key=key,value=value):
                self.backend.current=dict(valid,**{key:value})
                self.error('GITHUB_RESPONSE_INVALID',self.publish)
                self.assertEqual(self.path.read_bytes(),before)
                self.assertEqual((self.backend.uploaded,self.backend.published),(0,0))
        self.backend.current=valid
        self.assertTrue(self.publish()['delivery_verified'])
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(1,1,1))

    def test_malformed_response_after_publication_is_recoverable(self):
        self.prepare();self.push();before=self.path.read_bytes()
        original=self.backend.release
        def invalid_after_publish(tag):
            return [] if self.backend.published else original(tag)
        with patch.object(self.backend,'release',side_effect=invalid_after_publish):
            self.error('GITHUB_RESPONSE_INVALID',self.publish)
        self.assertFalse(self.backend.current['draft'])
        self.assertEqual(self.path.read_bytes(),before)
        self.assertTrue(self.publish()['delivery_verified'])
        self.assertEqual((self.backend.created,self.backend.uploaded,self.backend.published),(1,1,1))

    def test_release_replaced_during_publication_is_not_recorded(self):
        self.prepare();self.push();before=self.path.read_bytes()
        original=self.backend.release
        def replaced_after_publish(tag):
            value=original(tag)
            if self.backend.published:value['id']=99
            return value
        with patch.object(self.backend,'release',side_effect=replaced_after_publish):
            self.error('PUBLICATION_RELEASE_CONFLICT',self.publish)
        self.assertEqual(self.path.read_bytes(),before)

    def test_malformed_assets_do_not_publish_or_corrupt_registry(self):
        self.prepare();self.push();before=self.path.read_bytes()
        for assets in (None,{},[None],[{'id':True,'name':'fixture-1.0.0.zip'}],
                       [{'id':1,'name':[]}],[{'name':'fixture-1.0.0.zip'}]):
            with self.subTest(assets=assets),patch.object(self.backend,'assets',return_value=assets):
                self.error('GITHUB_RESPONSE_INVALID',self.publish)
                self.assertEqual(self.path.read_bytes(),before)
                self.assertEqual(self.backend.published,0)
        self.assertTrue(self.publish()['delivery_verified'])

    def test_withdrawal_interruption_after_clearing_recommendation_can_retry(self):
        self.prepare();self.push();self.publish()
        life.recommend(self.root,'fixture','1.0.0','Fixture recommendation')
        decision={'date':'2026-11-02','confirmed_by':'Fixture','reason':'Fixture withdrawal'}
        original=life.write
        def interrupt(path,content):
            if path==self.path and 'withdrawal' in json.loads(content):raise OSError('Disk unavailable')
            return original(path,content)
        with patch.object(life,'write',side_effect=interrupt),self.assertRaises(OSError):
            life.withdraw(self.root,'fixture','1.0.0',decision)
        self.assertIsNone(life.metadata(self.root,'fixture')['recommended'])
        self.assertIsNone(Catalog(self.root).select('fixture','1.0.0').get('withdrawal'))
        life.withdraw(self.root,'fixture','1.0.0',decision)
        self.assertEqual(Catalog(self.root).select('fixture','1.0.0')['withdrawal'],decision)

class GitHubTransport(unittest.TestCase):
    def test_transport_pins_host_and_rejects_invalid_response_shapes(self):
        from yunxia.github import GitHub
        backend=GitHub('fixture/repository')
        for body in (b'null',b'"unexpected"',b'{"id":1,"id":2}',b'{"id":NaN}'):
            result=subprocess.CompletedProcess([],0,stdout=body,stderr=b'')
            with patch('yunxia.github.subprocess.run',return_value=result) as run:
                with self.assertRaises(CatalogError) as cm:backend.api('test')
                self.assertEqual(cm.exception.code,'GITHUB_RESPONSE_INVALID')
                command=run.call_args.args[0]
                self.assertEqual(command[command.index('--hostname')+1],'github.com')

    def test_only_confirmed_404_is_treated_as_missing(self):
        from yunxia.github import GitHub
        backend=GitHub('fixture/repository')
        for response,expected in [(b'{"status":"404"}',None),(b'{"message":"Bad credentials","status":"401"}','GITHUB_REQUEST_FAILED')]:
            result=subprocess.CompletedProcess([],1,stdout=response,stderr=b'private transport details')
            with patch('yunxia.github.subprocess.run',return_value=result):
                if expected is None:self.assertIsNone(backend.api('test',missing=True))
                else:
                    with self.assertRaises(CatalogError) as cm:backend.api('test',missing=True)
                    self.assertEqual(cm.exception.code,expected)

    def test_upload_does_not_request_clobber(self):
        from yunxia.github import GitHub
        captured=[]
        def run(command,**kw):
            captured.append(command)
            self.assertEqual(Path(command[4]).read_bytes(),b'fixed')
            return subprocess.CompletedProcess(command,0,stdout=b'',stderr=b'')
        with patch('yunxia.github.subprocess.run',side_effect=run):
            GitHub('fixture/repository').upload('fixture/v1.0.0','fixture.zip',b'fixed')
        self.assertNotIn('--clobber',captured[0])
        self.assertEqual(captured[0][-1],'github.com/fixture/repository')

    def test_repository_is_validated_before_any_process(self):
        from yunxia.github import GitHub
        with patch('yunxia.github.subprocess.run') as run:
            with self.assertRaises(CatalogError):GitHub('--repo other')
            run.assert_not_called()

    def test_endpoint_responses_validate_release_and_asset_fields(self):
        from yunxia.github import GitHub
        backend=GitHub('fixture/repository')
        valid={'id':1,'tag_name':'fixture/v1.0.0','draft':True,'html_url':'https://example.test/release/1'}
        for malformed in ([],{},dict(valid,id=True),dict(valid,html_url=None)):
            with self.subTest(response=malformed),patch.object(backend,'api',return_value=malformed):
                with self.assertRaises(CatalogError) as cm:backend.create_draft('tag','commit','title','notes')
                self.assertEqual(cm.exception.code,'GITHUB_RESPONSE_INVALID')
        with patch.object(backend,'api',return_value=[{'name':'archive.zip','id':[]}]),self.assertRaises(CatalogError) as cm:
            backend.assets(valid)
        self.assertEqual(cm.exception.code,'GITHUB_RESPONSE_INVALID')

    def test_source_presence_requires_the_requested_commit(self):
        from yunxia.github import GitHub
        backend=GitHub('fixture/repository');commit='a'*40
        for response,code in [(None,'PUBLICATION_SOURCE_NOT_PUSHED'),({},'GITHUB_RESPONSE_INVALID'),
                              ({'sha':'b'*40},'GITHUB_RESPONSE_INVALID')]:
            with self.subTest(response=response),patch.object(backend,'api',return_value=response),self.assertRaises(CatalogError) as cm:
                backend.verify_source(commit)
            self.assertEqual(cm.exception.code,code)
        with patch.object(backend,'api',return_value={'sha':commit}):backend.verify_source(commit)


if __name__=='__main__':unittest.main()
