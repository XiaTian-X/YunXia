"""CLI repository selection must never turn a nested folder into its parent."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia import cli
from support import copy_tool


class WorkspaceCLI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'tool'
        copy_tool(self.root)

    def call(self,*args):
        output=io.StringIO()
        with redirect_stdout(output):code=cli.main(list(args))
        return code,json.loads(output.getvalue())

    def test_nested_folder_without_own_git_cannot_initialize_or_create(self):
        nested=self.root/'repositories/missing';nested.mkdir()
        code,data=self.call('repository','--repository',str(nested),'init','--id','fixture')
        self.assertEqual((code,data['error']),(2,'GIT_REPOSITORY_ROOT_REQUIRED'))
        self.assertEqual(list(nested.iterdir()),[])
        self.assertFalse((self.root/'repository.json').exists())

    def test_initialize_new_protocol_repo_and_check_without_tool_sources(self):
        nested=self.root/'repositories/new'
        subprocess.run(['git','init','-q','-b','main',str(nested)],check=True)
        code,data=self.call('repository','--repository',str(nested),'init','--id','new-protocols')
        self.assertEqual(code,0,data)
        self.assertEqual(json.loads((nested/'repository.json').read_text())['repository_id'],'new-protocols')
        code,data=self.call('check','--repository',str(nested))
        self.assertEqual(code,0,data);self.assertEqual(data['scope'],'protocol')
        self.assertFalse((nested/'tooling').exists())
        code,data=self.call('repository','--repository',str(nested),'init','--id','replacement')
        self.assertEqual((code,data['error']),(2,'REPOSITORY_ALREADY_INITIALIZED'))

    def test_tool_check_does_not_inspect_unregistered_nested_workspace(self):
        extra=self.root/'repositories/unregistered';extra.mkdir()
        (extra/'private.json').write_text('invalid and unrelated fixture data')
        code,data=self.call('check','--repository',str(self.root),'--scope','tool')
        self.assertEqual(code,0,data)
        self.assertFalse((self.root/'agreements').exists())

    def test_init_rejects_external_ignore_before_publishing_identity(self):
        nested=self.root/'repositories/new'
        subprocess.run(['git','init','-q','-b','main',str(nested)],check=True)
        outside=Path(self.temp.name)/'private-ignore';outside.write_text('preserve')
        (nested/'.gitignore').symlink_to(outside)
        code,data=self.call('repository','--repository',str(nested),'init','--id','new-protocols')
        self.assertEqual((code,data['error']),(2,'CATALOG_PATH_INVALID'))
        self.assertFalse((nested/'repository.json').exists())
        self.assertEqual(outside.read_text(),'preserve')

    def test_init_can_retry_after_preparation_interruption(self):
        nested=self.root/'repositories/new'
        subprocess.run(['git','init','-q','-b','main',str(nested)],check=True)
        with patch('yunxia.common.atomic_create',side_effect=OSError('interrupt before identity')):
            code,data=self.call('repository','--repository',str(nested),'init','--id','new-protocols')
        self.assertEqual(code,2)
        self.assertFalse((nested/'repository.json').exists())
        code,data=self.call('repository','--repository',str(nested),'init','--id','new-protocols')
        self.assertEqual(code,0,data)
        self.assertEqual((nested/'.gitignore').read_text().count('.yunxia-*'),1)

    def test_init_does_not_overwrite_concurrently_created_identity(self):
        nested=self.root/'repositories/new'
        subprocess.run(['git','init','-q','-b','main',str(nested)],check=True)
        original=b'{"do_not_overwrite":"concurrent user data"}\n'
        from yunxia.common import atomic_create
        def concurrent(path,*args,**kwargs):
            if path.name=='repository.json':path.write_bytes(original)
            return atomic_create(path,*args,**kwargs)
        with patch('yunxia.common.atomic_create',side_effect=concurrent):
            code,data=self.call('repository','--repository',str(nested),'init','--id','new-protocols')
        self.assertEqual((code,data['error']),(2,'REPOSITORY_ALREADY_INITIALIZED'))
        self.assertEqual((nested/'repository.json').read_bytes(),original)

    def test_protocol_operation_cannot_create_content_in_tool_repository(self):
        body=Path(self.temp.name)/'body.txt';body.write_text('Fictional test terms')
        code,data=self.call('agreement','--repository',str(self.root),'create','--id','bad',
                            '--name','Bad','--purpose','Test only','--version','1','--text',str(body))
        self.assertEqual((code,data['error']),(2,'PROTOCOL_REPOSITORY_REQUIRED'))
        self.assertFalse((self.root/'agreements').exists())


if __name__=='__main__':unittest.main()
