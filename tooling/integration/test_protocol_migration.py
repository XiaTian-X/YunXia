"""Required integration acceptance against the explicitly locked real protocol source."""
import json
import subprocess
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia.catalog import Catalog, reference
from yunxia.gitstore import read_blob
from yunxia.sources import load_catalog, read_lock, source_path


class RealProtocolMigration(unittest.TestCase):
    def setUp(self):
        self.lock=read_lock(ROOT)
        self.source=next(row for row in self.lock['sources'] if 'yunxia-license' in row['agreements'])
        self.protocol=source_path(ROOT,self.source['id'])

    def test_original_draft_records_and_payloads_are_preserved(self):
        catalog=Catalog(self.protocol,commit=self.source['commit'])
        for version in ('0.9.2-draft.1','0.9.3-draft.1','0.9.3-draft.2'):
            with self.subTest(version=version):
                entry=catalog.select('yunxia-license',version)
                source=entry['source'];folder=Path(source['files']['text.txt']).parent
                original=json.loads(read_blob(self.protocol,source['commit'],(folder/'release.json').as_posix()))
                self.assertEqual(reference(entry),reference(original))
                for key in ('display_name','kind','status','publication','files'):
                    self.assertEqual(entry[key],original[key])
                self.assertEqual(entry['status'],'imported-draft')
                self.assertIsNone(entry['publication'])
                self.assertEqual(source['provenance'],original['source'])
                self.assertNotIn('review',entry)
                for name,digest in original['files'].items():
                    self.assertEqual(entry['_payload'][name],read_blob(self.protocol,source['commit'],(folder/name).as_posix(),digest))

    def test_parent_index_excludes_protocol_repository(self):
        self.assertEqual(subprocess.check_output(['git','ls-files','--','repositories'],cwd=ROOT),b'')
        self.assertFalse((ROOT/'agreements').exists())
        self.assertFalse((ROOT/'registry').exists())

    def test_source_main_is_independent_from_retained_history(self):
        migration=json.loads(read_blob(self.protocol,self.source['commit'],'provenance/repository-migration.json'))
        cutoff=migration['origin']['cutoff_commit']
        result=subprocess.run(['git','merge-base',self.source['commit'],cutoff],cwd=self.protocol,capture_output=True)
        self.assertEqual(result.returncode,1)
        for rel,expected in migration['registry_files'].items():
            self.assertEqual(read_blob(self.protocol,self.source['commit'],rel,expected),read_blob(self.protocol,cutoff,rel,expected))
        paths=subprocess.check_output(['git','ls-tree','-r','--name-only',self.source['commit']],cwd=self.protocol,text=True).splitlines()
        self.assertFalse(any(p.startswith(('tooling/','skills/')) for p in paths))

    def test_composed_real_catalog_preserves_all_three_field_references(self):
        composed=load_catalog(ROOT)
        protocol=Catalog(self.protocol,commit=self.source['commit'])
        for entry in protocol.entries:
            self.assertEqual(composed.resolve(reference(entry))['_text'],entry['_text'])


if __name__=='__main__':unittest.main()
