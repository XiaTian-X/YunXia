"""Repository checks never traverse document links into external file contents."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia.validation import check
from support import copy_tool


class CheckIsolation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve()
        self.root=copy_tool(self.base/'tool')
        self.outside=self.base/'external';self.outside.mkdir()
        (self.outside/'private.md').write_text('Fixture outside the checked repository.\n')
        (self.outside/'private.json').write_text('not a public toolchain document')
        original=Path.read_text
        def restricted_read(path,*args,**kwargs):
            if path.resolve().is_relative_to(self.outside):
                raise AssertionError('External file was read through a repository symlink')
            return original(path,*args,**kwargs)
        guard=patch.object(Path,'read_text',restricted_read)
        guard.start();self.addCleanup(guard.stop)

    def assert_rejected(self,root,name,scope):
        result=check(root,scope=scope)
        self.assertFalse(result['valid'])
        self.assertIn({'code':'SOURCE_SYMLINK','path':name},result['errors'])

    def test_root_documents_are_rejected_without_reading_external_targets(self):
        for name in ('README.md','AGENTS.md','CHANGELOG.md'):
            with self.subTest(name=name):
                path=self.root/name;path.symlink_to(self.outside/'private.md')
                self.assert_rejected(self.root,name,'tool')
                path.unlink()

    def test_document_directory_is_rejected_before_external_traversal(self):
        for root,scope in ((self.root,'tool'),(self.root/'repositories/Fixture-Agreements','protocol')):
            with self.subTest(scope=scope):
                (root/'docs').symlink_to(self.outside,target_is_directory=True)
                self.assert_rejected(root,'docs',scope)

    def test_toolchain_link_is_rejected_before_parsing_target(self):
        root=self.root/'repositories/Fixture-Agreements'
        (root/'toolchain.lock.json').symlink_to(self.outside/'private.json')
        self.assert_rejected(root,'toolchain.lock.json','protocol')

    def test_protocol_workspace_must_be_ignored_before_any_files_are_staged(self):
        self.assertTrue(check(self.root)['valid'])
        (self.root/'.gitignore').write_text('/dist/\n.yunxia-*\n')
        result=check(self.root)
        self.assertFalse(result['valid'])
        self.assertIn({'code':'NESTED_REPOSITORY_NOT_IGNORED','path':'repositories/'},result['errors'])


if __name__=='__main__':unittest.main()
