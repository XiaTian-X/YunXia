import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tooling/src'))
from yunxia.common import CatalogError, json_bytes, canonical_sha256, read_json, safe_path, atomic_write, atomic_create, exclusive_guard
from yunxia.catalog import Catalog
from yunxia.project import workflow


class SharedDataChecks(unittest.TestCase):
    def test_guard_excludes_other_processes_and_survives_owner_death(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'guard'
            script="""
import sys
from pathlib import Path
from yunxia.common import exclusive_guard
with exclusive_guard(Path(sys.argv[1])):
    print('acquired',flush=True)
    sys.stdin.read()
"""
            env=dict(os.environ,PYTHONPATH=str(ROOT/'tooling/src'),PYTHONDONTWRITEBYTECODE='1')
            process=subprocess.Popen([sys.executable,'-B','-c',script,str(path)],stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            try:
                self.assertEqual(process.stdout.readline().strip(),'acquired')
                inode=path.stat().st_ino
                with self.assertRaises(CatalogError) as cm:
                    with exclusive_guard(path):pass
                self.assertEqual(cm.exception.code,'LOCKED')
                process.kill();process.communicate(timeout=10)
                with exclusive_guard(path):
                    self.assertEqual(path.stat().st_ino,inode)
                self.assertTrue(path.exists())
            finally:
                if process.poll() is None:process.kill()
                process.communicate(timeout=10)

    def test_atomic_replace_failure_preserves_original_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'record.json'
            path.write_bytes(b'original')
            with patch('yunxia.common.os.replace', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                atomic_write(path, b'new')
            self.assertEqual(path.read_bytes(), b'original')
            self.assertEqual(list(Path(temp).iterdir()), [path])

    def test_atomic_write_keeps_private_and_public_permissions_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'nested/record.json'
            for mode in (0o600, 0o644):
                atomic_write(path, b'record', mode=mode)
                self.assertEqual(path.stat().st_mode & 0o777, mode)
                self.assertEqual(path.read_bytes(), b'record')

    def test_atomic_create_publishes_complete_bytes_without_replacing_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'lock.json'
            atomic_create(path, b'complete record')
            self.assertEqual(path.read_bytes(), b'complete record')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                atomic_create(path, b'replacement')
            self.assertEqual(path.read_bytes(), b'complete record')
            self.assertEqual(list(Path(temp).iterdir()), [path])

    def test_atomic_create_failure_before_publication_leaves_no_partial_record(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'lock.json'
            def interrupted(source, destination):
                self.assertEqual(Path(source).read_bytes(), b'complete record')
                self.assertEqual(destination, path)
                self.assertFalse(path.exists())
                raise OSError('interrupted before publication')
            with patch('yunxia.common.os.link', side_effect=interrupted), self.assertRaises(OSError):
                atomic_create(path, b'complete record')
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_strict_json_preserves_each_interfaces_error_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'input.json'
            for raw, code in ((b'{"x":1,"x":2}', 'DUPLICATE_JSON_KEY'),
                              (b'{"x":NaN}', 'INVALID_JSON'), (b'\xff', 'INVALID_JSON')):
                path.write_bytes(raw)
                with self.assertRaises(CatalogError) as cm: read_json(path)
                self.assertEqual(cm.exception.code, 'CATALOG_FORMAT')
                with self.assertRaises(workflow.WorkflowError) as cm: workflow.read_json(path)
                self.assertEqual(cm.exception.code, code)
                self.assertEqual(path.read_bytes(), raw)

    def test_json_hash_is_stable_for_equivalent_key_order(self):
        first = {'中文': '允夏', 'fields': {'b': 2, 'a': 1}}
        second = {'fields': {'a': 1, 'b': 2}, '中文': '允夏'}
        self.assertEqual(json_bytes(first), json_bytes(second))
        self.assertEqual(canonical_sha256(first), canonical_sha256(second))
        self.assertEqual(json.loads(json_bytes(first)), first)
        self.assertNotEqual(canonical_sha256(first), canonical_sha256({'中文': 'changed'}))

    def test_strict_json_rejects_duplicate_nonfinite_and_invalid_encoding(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/'input.json'
            for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'\xff'):
                p.write_bytes(raw)
                with self.assertRaises(CatalogError) as cm: read_json(p)
                self.assertEqual(cm.exception.code, 'CATALOG_FORMAT')
                self.assertEqual(p.read_bytes(), raw)

    def test_paths_reject_escape_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in ('../outside', '/absolute', 'a/../b', r'a\b', 'a\nb', './a'):
                with self.assertRaises(CatalogError): safe_path(root, path)
            (root/'outside').write_text('private fixture')
            (root/'link').symlink_to(root/'outside')
            with self.assertRaises(CatalogError): safe_path(root, 'link')
            self.assertEqual(safe_path(root, 'safe/file.txt'), root/'safe/file.txt')

    def test_obsolete_catalog_is_rejected_without_loading_its_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'catalog.json').write_text('{"catalog_version":1,"agreements":["../outside"]}')
            with self.assertRaises(CatalogError) as cm: Catalog(root)
            self.assertEqual(cm.exception.code, 'UNSUPPORTED_CATALOG_FORMAT')


if __name__ == '__main__':
    unittest.main()
