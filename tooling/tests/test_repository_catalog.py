"""Independent protocol roots, pinned snapshots and preserved migration history."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia.catalog import Catalog, MIGRATION_PATH, reference
from yunxia.common import CatalogError, json_bytes, sha256
from yunxia import gitstore, lifecycle as life, publication


class ProtocolRepositoryCatalog(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'protocols'
        self.root.mkdir()
        self.git('init','-q','-b','main')
        self.git('config','user.name','Fixture maintainer')
        self.git('config','user.email','fixture@example.test')
        self.git('config','commit.gpgsign','false')
        self.git('config','core.hooksPath','/dev/null')
        self.marker={'format_version':1,'repository_id':'fixture-repository','migration':None}
        self.write('repository.json',self.marker)
        life.create(self.root,'fixture','Fixture terms','Fictional protocol only',b'Fixture terms.\n','1.0.0')
        info=life.inspect(self.root,'fixture')
        life.review(self.root,'fixture',{'source_sha256':info['source_sha256'],
                    'reviewer':'Fixture maintainer','date':'2026-11-02','decision':'approved',
                    'basis':'Fictional review for tests','open_issues':[]})
        self.source_commit=self.commit('Reviewed fixture')
        life.freeze(self.root,'fixture','1.0.0-rc.1','Fixture notes\n')
        self.cutoff=self.commit('Fixed candidate')
        self.ref=reference(Catalog(self.root).select('fixture','1.0.0-rc.1'))

    def git(self,*args,root=None):
        return subprocess.check_output(['git','-C',str(root or self.root),*args],stderr=subprocess.PIPE).decode().strip()

    def commit(self,message):
        self.git('add','--all')
        self.git('commit','-q','--no-verify','--allow-empty','-m',message)
        return self.git('rev-parse','HEAD')

    def write(self,relative,value):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(json_bytes(value))

    def reject(self,code,operation=None):
        with self.assertRaises(CatalogError) as caught:
            (operation or (lambda:Catalog(self.root)))()
        self.assertEqual(caught.exception.code,code)

    def split(self):
        self.cutoff=self.git('rev-parse','HEAD')
        files={path:gitstore.read_blob(self.root,self.cutoff,path)
               for path in gitstore.list_tree(self.root,self.cutoff)
               if path.startswith(('agreements/','registry/'))}
        hashes={path:sha256(content) for path,content in files.items() if path.startswith('registry/')}
        payloads=[]
        for path in sorted(hashes):
            record=json.loads(files[path]);source=record['source']
            payloads.append({'agreement':reference(record),'files':{
                name:{'commit':source['commit'],'path':original,'sha256':record['files'][name]}
                for name,original in source['files'].items()}})
        self.manifest={'format_version':1,'origin':{'repository':'https://example.test/old.git','cutoff_commit':self.cutoff},
                       'retained_history':{'ref':'refs/heads/history/pre-split','commit':self.cutoff},
                       'registry_files':hashes,'payloads':payloads}
        self.git('branch','history/pre-split',self.cutoff)
        self.git('checkout','--orphan','protocol-main')
        self.git('rm','-rf','.')
        for path,content in files.items():
            target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        self.marker['migration']=MIGRATION_PATH
        self.write('repository.json',self.marker)
        self.write(MIGRATION_PATH,self.manifest)
        return self.commit('Independent protocol main')

    def test_nested_directory_is_not_a_protocol_repository(self):
        nested=self.root/'repositories/missing';nested.mkdir(parents=True)
        self.reject('GIT_REPOSITORY_ROOT_REQUIRED',lambda:Catalog(nested))
        self.reject('GIT_REPOSITORY_ROOT_REQUIRED',lambda:life.create(nested,'nested','Name','Purpose',b'Text','1'))
        self.assertEqual(list(nested.iterdir()),[])

    def test_protocol_repository_marker_is_required(self):
        (self.root/'repository.json').unlink()
        self.reject('PROTOCOL_REPOSITORY_REQUIRED')

    def test_fixed_snapshot_does_not_read_dirty_working_tree(self):
        original=Catalog(self.root,commit=self.cutoff)
        (self.root/'repository.json').write_text('broken')
        (self.root/'registry/fixture/1.0.0-rc.1.json').unlink()
        (self.root/'agreements/fixture/agreement.json').write_text('broken')
        fixed=Catalog(self.root,commit=self.cutoff)
        self.assertEqual(fixed.export_files(),original.export_files())
        self.assertEqual(fixed.resolve(self.ref)['_text'],b'Fixture terms.\n')

    def test_adapter_capabilities_are_explicit_and_export_offline(self):
        catalog=Catalog(self.root)
        self.assertEqual(catalog.resolve(self.ref)['capabilities'],['inspect'])
        support={'format_version':1,'supported':[{'agreement':self.ref,'adapter':'scope-license-v1','output_name':'LICENSE.fixture.txt'}]}
        catalog.apply_supports(support)
        self.assertIn('adopt',catalog.resolve(self.ref)['capabilities'])
        target=Path(self.temp.name)/'bundle'
        for relative,content in catalog.export_files().items():
            path=target/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        self.assertEqual(Catalog(target).export_files(),catalog.export_files())

    def test_split_preserves_registry_bytes_sources_and_independent_main(self):
        original=(self.root/'registry/fixture/1.0.0-rc.1.json').read_bytes()
        split=self.split();catalog=Catalog(self.root)
        self.assertEqual((self.root/'registry/fixture/1.0.0-rc.1.json').read_bytes(),original)
        self.assertEqual(catalog.resolve(self.ref)['source']['commit'],self.source_commit)
        self.assertEqual(set(catalog.history_roots),{split,self.cutoff})
        self.assertEqual(self.git('rev-list','--count','HEAD'),'1')
        self.assertIn(MIGRATION_PATH,catalog.inputs)
        self.assertNotIn(MIGRATION_PATH,catalog.export_files())

    def test_full_clone_can_resolve_preserved_history(self):
        self.split()
        clone=Path(self.temp.name)/'clone'
        subprocess.run(['git','clone','-q','--no-hardlinks',str(self.root),str(clone)],check=True,capture_output=True)
        self.assertEqual(Catalog(clone).resolve(self.ref)['_text'],b'Fixture terms.\n')

    def test_migration_control_files_cannot_be_changed_or_removed(self):
        self.split()
        for rel in ('repository.json',MIGRATION_PATH):
            with self.subTest(path=rel):
                path=self.root/rel;before=path.read_bytes();value=json.loads(before)
                if rel=='repository.json':value['repository_id']='replacement'
                else:value['origin']['repository']='https://example.test/replacement.git'
                self.write(rel,value)
                self.reject('REPOSITORY_HISTORY_CHANGED')
                path.write_bytes(before)
                path.unlink()
                self.reject('PROTOCOL_REPOSITORY_REQUIRED' if rel=='repository.json' else 'MIGRATION_REQUIRED')
                path.write_bytes(before)

    def test_merged_change_to_migration_root_cannot_be_hidden_by_restore(self):
        self.split();original=(self.root/MIGRATION_PATH).read_bytes()
        self.git('checkout','-qb','changed-side')
        changed=copy.deepcopy(self.manifest);changed['origin']['repository']='https://example.test/replacement.git'
        self.write(MIGRATION_PATH,changed);self.commit('Invalid provenance rewrite')
        (self.root/MIGRATION_PATH).write_bytes(original);self.commit('Restore before merge')
        self.git('checkout','-q','protocol-main');self.git('merge','--no-ff','-qm','Merge restored source','changed-side')
        self.reject('REPOSITORY_HISTORY_CHANGED')

    def test_pre_split_intermediate_identity_change_is_still_rejected(self):
        path=self.root/'registry/fixture/1.0.0-rc.1.json';original=path.read_bytes()
        record=json.loads(original);record['display_name']='Invalid historical replacement'
        path.write_bytes(json_bytes(record));self.commit('Change fixed identity')
        path.write_bytes(original);self.commit('Restore fixed identity')
        self.split()
        self.reject('VERSION_IDENTITY_CHANGED')

    def test_pre_split_deleted_registry_record_remains_required(self):
        (self.root/'registry/fixture/1.0.0-rc.1.json').unlink()
        self.commit('Delete fixed candidate')
        self.split()
        self.reject('REGISTRY_RECORD_MISSING')

    def test_retained_history_reference_cannot_move(self):
        current=self.split()
        self.git('branch','-f','history/pre-split',current)
        self.reject('MIGRATION_HISTORY_REF_INVALID')

    def test_pinned_catalog_ignores_dirty_migration_manifest(self):
        commit=self.split();original=Catalog(self.root,commit=commit).export_files()
        (self.root/MIGRATION_PATH).write_text('{}')
        self.assertEqual(Catalog(self.root,commit=commit).export_files(),original)

    def test_publish_requires_committed_control_files_and_verifies_history_remote(self):
        self.split()
        candidate=Catalog(self.root).resolve(self.ref)
        decision={'date':'2026-11-02','confirmed_by':'Fixture maintainer','basis':'Fictional test only',
                  'candidate_sha256':candidate['_package_sha256']}
        record,_=publication.prepare(self.root,'fixture','1.0.0-rc.1','1.0.0','github-release','fixture/repository',decision)
        commit=self.commit('Prepared fictional publication')
        backend=Mock();backend.tag_commit.return_value=self.source_commit
        self.assertEqual(publication._committed_registry(self.root,record,backend),commit)
        paths={call.args[1] for call in backend.verify_registry.call_args_list}
        self.assertTrue({'repository.json',MIGRATION_PATH}<=paths)
        backend.verify_source.assert_any_call(self.cutoff)

    def test_rename_changes_current_metadata_only_and_clears_review(self):
        text=(self.root/'agreements/fixture/text.txt').read_bytes()
        fixed=(self.root/'registry/fixture/1.0.0-rc.1.json').read_bytes()
        result=life.rename(self.root,'fixture','Renamed fixture','Clarify the public name')
        self.assertTrue(result['changed'])
        current=life.metadata(self.root,'fixture')
        self.assertEqual(current['agreement_id'],'fixture')
        self.assertEqual(current['stage'],'draft');self.assertIsNone(current['review'])
        self.assertEqual(current['name_history'][0]['from'],'Fixture terms')
        self.assertEqual((self.root/'agreements/fixture/text.txt').read_bytes(),text)
        self.assertEqual((self.root/'registry/fixture/1.0.0-rc.1.json').read_bytes(),fixed)
        before=(self.root/'agreements/fixture/agreement.json').read_bytes()
        self.assertFalse(life.rename(self.root,'fixture','Renamed fixture','Same name')['changed'])
        self.assertEqual((self.root/'agreements/fixture/agreement.json').read_bytes(),before)
        self.assertEqual(Catalog(self.root).resolve(self.ref)['display_name'],'Fixture terms')


if __name__=='__main__':unittest.main()
