import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tooling/src'))
from yunxia import sources, gitstore, distribution
from yunxia.catalog import Catalog, reference
from yunxia.common import CatalogError, json_bytes, sha256
from support import copy_tool, copy_catalog, git_commit, add_protocol


class Sources(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = copy_tool(self.base/'tool')
        self.protocol = self.root/'repositories/Fixture-Agreements'
        self.lock = self.root/'sources.lock.json'

    def write_lock(self, data):
        self.lock.write_bytes(json_bytes(data))

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(CatalogError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_locked_git_snapshot_ignores_protocol_working_changes(self):
        before = distribution.source_signature(self.root)
        records = sources.load_catalog(self.root).describe()
        path = self.protocol/'registry/yunxia-license/0.9.3-draft.2.json'
        path.write_bytes(b'deliberately invalid uncommitted metadata')
        (self.protocol/'agreements/yunxia-license/text.txt').write_bytes(b'uncommitted source')
        self.assertEqual(sources.load_catalog(self.root).describe(), records)
        self.assertEqual(distribution.source_signature(self.root), before)
        self.assertEqual(path.read_bytes(), b'deliberately invalid uncommitted metadata')

    def test_source_pin_changes_only_after_explicit_update(self):
        before = self.lock.read_bytes()
        original_signature = distribution.source_signature(self.root)
        (self.protocol/'README.md').write_text('Protocol documentation update\n')
        commit = git_commit(self.protocol, 'Advance protocol directory')
        self.assertEqual(distribution.source_signature(self.root), original_signature)
        self.assertEqual(self.lock.read_bytes(), before)
        result = sources.pin(self.root, 'Fixture-Agreements', commit)
        self.assertEqual(result['added_versions'], [])
        self.assertEqual(result['commit'], commit)
        self.assertNotEqual(distribution.source_signature(self.root), original_signature)
        self.assertEqual(sources.load_catalog(self.root).source_pins['sources'][0]['commit'], commit)

    def test_missing_nested_repository_cannot_fall_back_to_parent(self):
        shutil.rmtree(self.protocol/'.git')
        self.error('GIT_REPOSITORY_ROOT_REQUIRED', sources.load_catalog, self.root)

    def test_wrong_remote_is_not_accepted_or_rewritten(self):
        gitstore.run(self.protocol, ['remote', 'set-url', 'origin', 'https://example.test/Other.git'])
        before = self.lock.read_bytes()
        self.error('SOURCE_REMOTE_MISMATCH', sources.load_catalog, self.root)
        self.assertEqual(self.lock.read_bytes(), before)

    def test_missing_source_is_reported_without_creating_it(self):
        shutil.rmtree(self.protocol)
        self.error('SOURCE_REPOSITORY_MISSING', sources.load_catalog, self.root)
        self.assertFalse(self.protocol.exists())

    def test_conflicting_protocol_authorities_are_rejected(self):
        data = sources.read_lock(self.root)
        row = dict(data['sources'][0], id='Another-Agreements')
        data['sources'].append(row); self.write_lock(data)
        self.error('SOURCE_AGREEMENT_CONFLICT', sources.load_catalog, self.root)

    def test_lock_rejects_credentials_paths_and_nonfixed_refs(self):
        original = sources.read_lock(self.root)
        for fields in ({'repository':'https://user:password@example.test/repo.git'},
                       {'repository':'/private/local/checkout'}, {'id':'../outside'},
                       {'commit':'main'}, {'agreements':['yunxia-license','yunxia-license']}):
            with self.subTest(fields=fields):
                data = copy.deepcopy(original); data['sources'][0].update(fields)
                self.write_lock(data)
                self.error('SOURCE_LOCK_INVALID', sources.read_lock, self.root)

    def test_optional_repository_identity_is_checked_at_the_pin(self):
        data=sources.read_lock(self.root);data['sources'][0]['repository_id']='other-identity'
        self.write_lock(data)
        self.error('SOURCE_IDENTITY_MISMATCH',sources.load_catalog,self.root)

    def test_missing_object_does_not_fall_back_to_head(self):
        data=sources.read_lock(self.root);data['sources'][0]['commit']='1'*40
        self.write_lock(data);before=self.lock.read_bytes()
        with self.assertRaises(CatalogError):sources.load_catalog(self.root)
        self.assertEqual(self.lock.read_bytes(),before)

    def test_local_override_does_not_change_shareable_lock(self):
        moved=self.base/'independent-protocol';self.protocol.rename(moved)
        before=self.lock.read_bytes()
        catalog=sources.load_catalog(self.root,locations={'Fixture-Agreements':moved})
        self.assertEqual(len(catalog.entries),3)
        self.assertEqual(self.lock.read_bytes(),before)
        self.error('SOURCE_PATH_INVALID',sources.load_catalog,self.root,
                   locations={'Fixture-Agreements':self.root})

    def test_backward_pin_is_rejected(self):
        original=sources.read_lock(self.root)['sources'][0]['commit']
        (self.protocol/'README.md').write_text('new directory\n')
        newer=git_commit(self.protocol)
        sources.pin(self.root,'Fixture-Agreements',newer)
        self.error('SOURCE_NONFASTFORWARD',sources.pin,self.root,'Fixture-Agreements',original)
        self.assertEqual(sources.read_lock(self.root)['sources'][0]['commit'],newer)

    def test_pin_rechecks_lock_before_writing(self):
        (self.protocol/'README.md').write_text('new directory\n');newer=git_commit(self.protocol)
        real=sources.SourceCatalog
        changed=None
        def concurrent(*args,**kwargs):
            nonlocal changed
            result=real(*args,**kwargs)
            changed=self.lock.read_bytes()+b'\n'
            self.lock.write_bytes(changed)
            return result
        with patch.object(sources,'SourceCatalog',side_effect=concurrent):
            self.error('SOURCE_LOCK_CHANGED',sources.pin,self.root,'Fixture-Agreements',newer)
        self.assertEqual(self.lock.read_bytes(),changed)

    def test_multiple_selected_sources_export_one_valid_offline_catalog(self):
        other=copy_catalog(self.root/'repositories/Other-Agreements')
        add_protocol(other)
        commit=git_commit(other)
        remote='https://example.test/Other-Agreements.git'
        gitstore.run(other,['remote','add','origin',remote])
        data=sources.read_lock(self.root)
        data['sources'].append({'id':'Other-Agreements','repository':remote,'commit':commit,
                                'agreements':['fixture']})
        self.write_lock(data)
        adapters=self.root/'tooling/src/yunxia/adapters/support.json'
        supports=json.loads(adapters.read_text())
        supports['supported'].extend(row for row in json.loads((other/'adapters.json').read_text())['supported']
                                     if row['agreement']['agreement_id']=='fixture')
        adapters.write_bytes(json_bytes(supports))
        catalog=sources.load_catalog(self.root)
        self.assertEqual({e['agreement_id'] for e in catalog.entries},{'yunxia-license','fixture'})
        bundle=self.base/'offline';bundle.mkdir()
        distribution.write_files(bundle,catalog.export_files())
        self.assertEqual(len(Catalog(bundle).entries),len(catalog.entries))
        self.assertEqual(Catalog(bundle).select('fixture','1.0.0-rc.1')['renderer'],'scope-license-v1')

    def test_fetch_existing_repository_preserves_dirty_tree_and_pin(self):
        path=self.protocol/'agreements/yunxia-license/text.txt';path.write_bytes(b'work in progress')
        before=self.lock.read_bytes();real=gitstore.run;operations=[]
        def offline_fetch(root,args,check=True):
            operations.append(args)
            if args[0]=='fetch':return subprocess.CompletedProcess(args,0,b'',b'')
            return real(root,args,check)
        with patch.object(gitstore,'run',side_effect=offline_fetch):
            result=sources.fetch(self.root)
        self.assertFalse(result['lock_changed'])
        self.assertEqual(path.read_bytes(),b'work in progress')
        self.assertEqual(self.lock.read_bytes(),before)
        self.assertFalse(any(args[0] in ('checkout','reset','clean') for args in operations))

    def test_initial_fetch_keeps_normal_branch_while_build_uses_older_pin(self):
        before=self.lock.read_bytes()
        original=distribution.source_signature(self.root)
        remote_source=self.base/'fixture-origin';self.protocol.rename(remote_source)
        (remote_source/'README.md').write_text('Newer default branch\n')
        latest=git_commit(remote_source)
        real=gitstore.run
        def local_transport(root,args,check=True):
            if args[0]=='clone':
                local_args=list(args);local_args[-2]=str(remote_source)
                result=real(root,local_args,check)
                real(self.protocol,['remote','set-url','origin','https://example.test/Fixture-Agreements.git'])
                return result
            return real(root,args,check)
        with patch.object(gitstore,'run',side_effect=local_transport):
            result=sources.fetch(self.root)
        self.assertTrue(result['fetched'][0]['created'])
        self.assertEqual(result['fetched'][0]['working_branch'],'main')
        self.assertEqual(result['fetched'][0]['working_head'],latest)
        self.assertNotEqual(result['fetched'][0]['commit'],latest)
        self.assertEqual(self.lock.read_bytes(),before)
        self.assertEqual(distribution.source_signature(self.root),original)

    def retained_history_remote(self):
        """A real bare remote whose independent main needs a retained old branch."""
        cutoff=gitstore.head(self.protocol)
        files={rel:gitstore.read_blob(self.protocol,cutoff,rel)
               for rel in gitstore.list_tree(self.protocol,cutoff)
               if rel.startswith(('agreements/','registry/'))}
        hashes={rel:sha256(content) for rel,content in files.items() if rel.startswith('registry/')}
        payloads=[]
        for rel in sorted(hashes):
            record=json.loads(files[rel])
            payloads.append({'agreement':reference(record),'files':{
                name:{'commit':record['source']['commit'],'path':path,'sha256':record['files'][name]}
                for name,path in record['source']['files'].items()}})
        gitstore.run(self.protocol,['branch','history/pre-split',cutoff])
        gitstore.run(self.protocol,['checkout','--orphan','split-main'])
        gitstore.run(self.protocol,['rm','-rf','.'])
        distribution.write_files(self.protocol,files)
        manifest={'format_version':1,'origin':{'repository':'https://example.test/old.git','cutoff_commit':cutoff},
                  'retained_history':{'ref':'refs/heads/history/pre-split','commit':cutoff},
                  'registry_files':hashes,'payloads':payloads}
        migration=self.protocol/'provenance/repository-migration.json';migration.parent.mkdir()
        migration.write_bytes(json_bytes(manifest))
        (self.protocol/'repository.json').write_bytes(json_bytes({'format_version':1,
            'repository_id':'fixture-repository','migration':'provenance/repository-migration.json'}))
        (self.protocol/'.gitignore').write_text('.yunxia-*\n')
        pin=git_commit(self.protocol,'Independent protocol main')
        gitstore.run(self.protocol,['branch','-D','main'])
        gitstore.run(self.protocol,['branch','-m','main'])
        bare=self.base/'protocol-origin.git'
        subprocess.run(['git','clone','--bare','--no-hardlinks',str(self.protocol),str(bare)],
                       check=True,capture_output=True)
        gitstore.run(self.protocol,['fetch',bare.as_uri(),'+refs/heads/*:refs/remotes/origin/*'])
        lock=sources.read_lock(self.root);lock['sources'][0]['commit']=pin;self.write_lock(lock)
        return bare,cutoff,pin

    def transport_to(self,bare):
        real=gitstore.run
        def local_transport(root,args,check=True):
            if args[0] in ('fetch','ls-remote'):
                args=[bare.as_uri() if arg in ('origin','https://example.test/Fixture-Agreements.git') else arg
                      for arg in args]
            return real(root,args,check)
        return local_transport

    def test_fetch_checks_actual_remote_history_even_with_both_cached_refs(self):
        bare,cutoff,pin=self.retained_history_remote()
        before=self.lock.read_bytes();signature=distribution.source_signature(self.root)
        with patch.object(gitstore,'run',side_effect=self.transport_to(bare)):
            self.assertFalse(sources.fetch(self.root)['lock_changed'])
        gitstore.run(bare,['update-ref','-d','refs/heads/history/pre-split'])
        with patch.object(gitstore,'run',side_effect=self.transport_to(bare)):
            self.error('SOURCE_HISTORY_REMOTE_MISSING',sources.fetch,self.root)
        for ref in ('refs/heads/history/pre-split','refs/remotes/origin/history/pre-split'):
            self.assertEqual(gitstore.run(self.protocol,['rev-parse',ref]).stdout.decode().strip(),cutoff)
        self.assertEqual(self.lock.read_bytes(),before)
        # Fixed, previously obtained objects remain usable without contacting a remote.
        self.assertEqual(distribution.source_signature(self.root),signature)
        fresh=self.base/'fresh-clone'
        subprocess.run(['git','clone',bare.as_uri(),str(fresh)],check=True,capture_output=True)
        with self.assertRaises(CatalogError):Catalog(fresh,commit=pin)

    def test_fetch_rejects_remote_retained_history_rebinding(self):
        bare,cutoff,pin=self.retained_history_remote()
        gitstore.run(bare,['update-ref','refs/heads/history/pre-split',pin])
        before=self.lock.read_bytes()
        with patch.object(gitstore,'run',side_effect=self.transport_to(bare)):
            self.error('SOURCE_HISTORY_REMOTE_CHANGED',sources.fetch,self.root)
        self.assertEqual(self.lock.read_bytes(),before)


if __name__=='__main__':unittest.main()
