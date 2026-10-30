import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tooling/src'))
from yunxia.common import CatalogError, json_bytes, canonical_sha256, read_json, safe_path
from yunxia.catalog import Catalog


class SharedDataChecks(unittest.TestCase):
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
