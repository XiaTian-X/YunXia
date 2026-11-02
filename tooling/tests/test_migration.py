"""The three imported drafts retain their original Git-recorded identities and bytes."""
import json
import tempfile
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia.catalog import Catalog, reference
from yunxia.gitstore import read_blob
from yunxia.common import CatalogError, json_bytes
from support import copy_catalog


class HistoricalDraftMigration(unittest.TestCase):
    def test_required_null_publication_is_not_silently_defaulted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=copy_catalog(Path(temp)/'repo')
            p=root/'registry/yunxia-license/0.9.2-draft.1.json'
            data=json.loads(p.read_text());del data['publication'];p.write_bytes(json_bytes(data))
            with self.assertRaises(CatalogError) as error:Catalog(root)
            self.assertEqual(error.exception.code,'RELEASE_FORMAT')

    def test_original_draft_records_and_payloads_are_preserved(self):
        catalog=Catalog(ROOT)
        for version in ('0.9.2-draft.1','0.9.3-draft.1','0.9.3-draft.2'):
            with self.subTest(version=version):
                entry=catalog.select('yunxia-license',version)
                source=entry['source'];folder=Path(source['files']['text.txt']).parent
                original=json.loads(read_blob(ROOT,source['commit'],(folder/'release.json').as_posix()))
                self.assertEqual(reference(entry),reference(original))
                for key in ('display_name','kind','status','publication','files'):
                    self.assertEqual(entry[key],original[key])
                self.assertEqual(entry['status'],'imported-draft')
                self.assertIsNone(entry['publication'])
                self.assertEqual(source['provenance'],original['source'])
                self.assertNotIn('review',entry)
                for name,digest in original['files'].items():
                    self.assertEqual(entry['_payload'][name],read_blob(ROOT,source['commit'],(folder/name).as_posix(),digest))


if __name__=='__main__':unittest.main()
