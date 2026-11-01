import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia import gitstore as g
from yunxia.catalog import Catalog
from yunxia.common import CatalogError,sha256


class GitObjects(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.git('init','-q');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.test')
        (self.root/'nested').mkdir();(self.root/'nested/text.txt').write_bytes(b'fixed\r\nbytes\n')
        (self.root/'.gitattributes').write_text('*.txt -text\n')
        self.git('add','.');self.git('commit','-qm','fixture')
        self.commit=g.head(self.root)

    def git(self,*args):
        return subprocess.run(['git','-C',str(self.root),*args],check=True,capture_output=True).stdout

    def test_raw_committed_bytes_ignore_worktree_and_checkout_filters(self):
        (self.root/'nested/text.txt').write_bytes(b'changed')
        self.git('config','core.autocrlf','true')
        self.assertEqual(g.read_blob(self.root,self.commit,'nested/text.txt'),b'fixed\r\nbytes\n')

    def test_replace_refs_cannot_change_pinned_content(self):
        oid=self.git('rev-parse',self.commit+':nested/text.txt').decode().strip()
        (self.root/'replacement').write_bytes(b'wrong')
        other=self.git('hash-object','-w','replacement').decode().strip()
        self.git('replace',oid,other)
        self.assertEqual(g.read_blob(self.root,self.commit,'nested/text.txt'),b'fixed\r\nbytes\n')

    def test_paths_and_symlinks_cannot_escape_git_tree(self):
        (self.root/'link').symlink_to('/etc/passwd')
        self.git('add','link');self.git('commit','-qm','symlink fixture')
        commit=g.head(self.root)
        for path in ('../outside','/etc/passwd','nested/../text.txt'):
            with self.assertRaises(CatalogError):g.read_blob(self.root,commit,path)
        with self.assertRaises(CatalogError) as cm:g.read_blob(self.root,commit,'link')
        self.assertEqual(cm.exception.code,'GIT_SOURCE_NOT_REGULAR')
        with self.assertRaises(CatalogError):g.read_blob(self.root,commit,'link/child')

    def test_missing_objects_and_wrong_digest_never_fall_back(self):
        for commit,path,expected,code in [('0'*40,'nested/text.txt',None,'GIT_SOURCE_UNAVAILABLE'),
                                          (self.commit,'absent',None,'GIT_SOURCE_UNAVAILABLE'),
                                          (self.commit,'nested/text.txt','0'*64,'GIT_SOURCE_HASH_MISMATCH')]:
            with self.assertRaises(CatalogError) as cm:g.read_blob(self.root,commit,path,expected)
            self.assertEqual(cm.exception.code,code)

    def test_full_object_id_and_plain_file_required(self):
        with self.assertRaises(CatalogError):g.read_blob(self.root,'HEAD','nested/text.txt')
        with self.assertRaises(CatalogError):g.read_blob(self.root,self.commit[:7],'nested/text.txt')
        with self.assertRaises(CatalogError):g.read_blob(self.root,self.commit,'nested')
        (self.root/'pointer').write_text('version https://git-lfs.github.com/spec/v1\noid sha256:fake\nsize 1\n')
        self.git('add','pointer');self.git('commit','-qm','LFS pointer fixture')
        with self.assertRaises(CatalogError) as cm:g.read_blob(self.root,g.head(self.root),'pointer')
        self.assertEqual(cm.exception.code,'GIT_LFS_UNSUPPORTED')

    def test_tag_idempotence_and_conflicts_preserve_existing_tag(self):
        self.assertTrue(g.ensure_tag(self.root,'fixture/v1.0.0',self.commit)['created'])
        self.assertFalse(g.ensure_tag(self.root,'fixture/v1.0.0',self.commit)['created'])
        self.git('commit','--allow-empty','-qm','next')
        with self.assertRaises(CatalogError) as cm:g.ensure_tag(self.root,'fixture/v1.0.0',g.head(self.root))
        self.assertEqual(cm.exception.code,'GIT_TAG_CONFLICT')
        self.assertTrue(g.verify_tag(self.root,'fixture/v1.0.0',self.commit))

    def test_git_environment_cannot_redirect_repository(self):
        with patch.dict(os.environ,{'GIT_DIR':'/missing','GIT_WORK_TREE':'/missing','GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'core.bare','GIT_CONFIG_VALUE_0':'true'}):
            self.assertEqual(g.read_blob(self.root,self.commit,'nested/text.txt'),b'fixed\r\nbytes\n')

    def test_offline_export_needs_no_git_and_detects_tampering(self):
        c=Catalog(ROOT);files=c.export_files();target=self.root/'bundle';target.mkdir()
        for rel,content in files.items():
            p=target/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
        with patch('yunxia.gitstore.run',side_effect=AssertionError('offline Git call')):
            offline=Catalog(target)
            self.assertEqual([e['_text'] for e in offline.entries],[e['_text'] for e in c.entries])
            p=target/'objects'/c.entries[0]['sha256'];p.write_bytes(b'changed')
            with self.assertRaises(CatalogError):Catalog(target)


if __name__=='__main__':unittest.main()
