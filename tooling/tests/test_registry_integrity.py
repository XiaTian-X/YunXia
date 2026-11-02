"""Registry identity, publication provenance and historical review invariants."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tooling/src'))
from yunxia.catalog import Catalog
from yunxia.common import CatalogError, json_bytes
from yunxia import gitstore, lifecycle, publication
from support import copy_catalog, git_commit, add_protocol


class RegistryIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = copy_catalog(Path(self.temp.name)/'repo')

    def reject(self, code):
        with self.assertRaises(CatalogError) as error:
            Catalog(self.root)
        self.assertEqual(error.exception.code, code)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True).stdout

    def rebind_imported_version(self):
        path = self.root/'registry/yunxia-license/0.9.2-draft.1.json'
        record = json.loads(path.read_text())
        donor = json.loads((path.parent/'0.9.3-draft.1.json').read_text())
        record['sha256'] = donor['sha256']
        record['files']['text.txt'] = donor['sha256']
        record['source']['files']['text.txt'] = donor['source']['files']['text.txt']
        path.write_bytes(json_bytes(record))
        support = self.root/'adapters.json'
        data = json.loads(support.read_text())
        for row in data['supported']:
            if row['agreement']['version'] == '0.9.2-draft.1':
                row['agreement']['sha256'] = donor['sha256']
        support.write_bytes(json_bytes(data))
        return path

    def test_uncommitted_version_rebinding_is_rejected(self):
        git_commit(self.root, 'Initial version identities')
        path = self.rebind_imported_version()
        original = path.read_bytes()
        self.reject('VERSION_IDENTITY_CHANGED')
        self.assertEqual(path.read_bytes(), original)

    def test_committed_version_rebinding_is_rejected_against_first_identity(self):
        git_commit(self.root, 'Initial version identities')
        path = self.rebind_imported_version()
        git_commit(self.root, 'Invalid replacement of a fixed identity')
        original = path.read_bytes()
        self.reject('VERSION_IDENTITY_CHANGED')
        self.assertEqual(path.read_bytes(), original)

    def test_merged_branch_cannot_hide_intermediate_version_rebinding(self):
        git_commit(self.root, 'Initial version identities')
        path = self.root/'registry/yunxia-license/0.9.2-draft.1.json'
        original = path.read_bytes()
        self.git('checkout', '-qb', 'rebound-side')
        record = json.loads(original)
        record['display_name'] = 'Invalid replacement of a fixed name'
        path.write_bytes(json_bytes(record))
        git_commit(self.root, 'Invalid identity on a side branch')
        path.write_bytes(original)
        git_commit(self.root, 'Restore identity before merging')
        self.git('checkout', '-q', '-')
        self.git('merge', '--no-ff', '-qm', 'Merge restored side branch', 'rebound-side')
        self.reject('VERSION_IDENTITY_CHANGED')

    def test_local_grafts_cannot_hide_committed_version_rebinding(self):
        self.rebind_imported_version()
        commit = git_commit(self.root, 'Invalid replacement of a fixed identity')
        graft = self.root/'.git/info/grafts'
        graft.write_text(commit+'\n')
        self.reject('VERSION_IDENTITY_CHANGED')
        self.assertEqual(graft.read_text(), commit+'\n')

    def test_committed_fixed_version_cannot_disappear_with_its_adapter(self):
        git_commit(self.root, 'Initial version identities')
        (self.root/'registry/yunxia-license/0.9.2-draft.1.json').unlink()
        path = self.root/'adapters.json'
        data = json.loads(path.read_text())
        data['supported'] = [row for row in data['supported']
                             if row['agreement']['version'] != '0.9.2-draft.1']
        path.write_bytes(json_bytes(data))
        git_commit(self.root, 'Invalid removal of a historical version')
        self.reject('REGISTRY_RECORD_MISSING')

    def test_merged_branch_cannot_hide_a_removed_registry_record(self):
        git_commit(self.root, 'Initial version identities')
        self.git('checkout', '-qb', 'removed-side')
        path = self.root/'registry/yunxia-license/9.0.0-draft.1.json'
        record = json.loads((path.parent/'0.9.2-draft.1.json').read_text())
        record['version'] = path.stem
        path.write_bytes(json_bytes(record))
        git_commit(self.root, 'Add fixed identity on a side branch')
        path.unlink()
        git_commit(self.root, 'Remove fixed identity before merging')
        self.git('checkout', '-q', '-')
        self.git('merge', '--no-ff', '-qm', 'Merge removed record', 'removed-side')
        self.reject('REGISTRY_RECORD_MISSING')

    def test_registry_record_created_by_merge_cannot_disappear(self):
        git_commit(self.root, 'Initial version identities')
        self.git('checkout', '-qb', 'merge-side')
        git_commit(self.root, 'Side branch snapshot')
        self.git('checkout', '-q', '-')
        git_commit(self.root, 'Main branch snapshot')
        self.git('merge', '--no-ff', '--no-commit', 'merge-side')
        path = self.root/'registry/yunxia-license/9.0.0-draft.1.json'
        record = json.loads((path.parent/'0.9.2-draft.1.json').read_text())
        record['version'] = path.stem
        path.write_bytes(json_bytes(record))
        git_commit(self.root, 'Create fixed identity in merge commit')
        path.unlink()
        git_commit(self.root, 'Remove merge-created fixed identity')
        self.reject('REGISTRY_RECORD_MISSING')

    def test_shallow_history_is_rejected_even_with_an_empty_current_registry(self):
        for folder in ('registry', 'agreements'):
            shutil.rmtree(self.root/folder)
        (self.root/'adapters.json').write_bytes(
            json_bytes({'format_version': 1, 'supported': []}))
        git_commit(self.root, 'Remove all current version records')
        target = Path(self.temp.name)/'shallow'
        subprocess.run(['git', 'clone', '--quiet', '--depth', '1', self.root.as_uri(),
                        str(target)], check=True, capture_output=True)
        with self.assertRaises(CatalogError) as error:
            Catalog(target)
        self.assertEqual(error.exception.code, 'GIT_HISTORY_INCOMPLETE')

    def test_publication_digests_must_describe_the_actual_candidate_and_assets(self):
        add_protocol(self.root, released=True)
        path = self.root/'registry/fixture/1.0.0.json'
        original = json.loads(path.read_text())
        for field, code in (
            ('candidate', 'PUBLICATION_CANDIDATE_MISSING'),
            ('manifest', 'PUBLICATION_MANIFEST_MISMATCH'),
            ('assets', 'PUBLICATION_ASSET_CONFLICT'),
        ):
            with self.subTest(field=field):
                record = copy.deepcopy(original)
                pub = record['publication']
                if field == 'candidate':
                    pub['decision']['candidate_sha256'] = '0'*64
                elif field == 'manifest':
                    pub['manifest_sha256'] = '0'*64
                else:
                    pub['assets'] = {'unpublished.zip': '0'*64}
                    pub['receipt']['assets'] = dict(pub['assets'])
                raw = json_bytes(record)
                path.write_bytes(raw)
                self.reject(code)
                self.assertEqual(path.read_bytes(), raw)
        path.write_bytes(json_bytes(original))
        self.assertEqual(Catalog(self.root).select('fixture', '1.0.0')['status'], 'released')

    def test_published_version_requires_its_preserved_candidate(self):
        add_protocol(self.root, released=True)
        (self.root/'registry/fixture/1.0.0-rc.1.json').unlink()
        self.reject('PUBLICATION_CANDIDATE_MISSING')

    def test_publication_receipt_and_builder_have_strict_fields(self):
        add_protocol(self.root, released=True)
        path = self.root/'registry/fixture/1.0.0.json'
        original = json.loads(path.read_text())
        for field in ('release_id', 'url', 'verified_at', 'tag_commit', 'assets'):
            with self.subTest(receipt_field=field):
                record = copy.deepcopy(original)
                del record['publication']['receipt'][field]
                path.write_bytes(json_bytes(record))
                self.reject('RELEASE_PUBLICATION_REQUIRED')
        for field, value in (('format_version', True), ('tool_version', ''),
                             ('implementation_sha256', 'invalid')):
            with self.subTest(builder_field=field):
                record = copy.deepcopy(original)
                record['publication']['builder'][field] = value
                path.write_bytes(json_bytes(record))
                self.reject('RELEASE_PUBLICATION_REQUIRED')

    def test_committed_publication_cannot_be_reverted_to_prepared(self):
        add_protocol(self.root, released=True)
        git_commit(self.root, 'Completed publication receipt')
        path = self.root/'registry/fixture/1.0.0.json'
        record = json.loads(path.read_text())
        record['status'] = 'prepared'
        record['publication']['state'] = 'prepared'
        del record['publication']['receipt']
        path.write_bytes(json_bytes(record))
        self.reject('VERSION_STATE_CHANGED')

    def test_new_candidate_cannot_omit_existing_template_from_review_scope(self):
        add_protocol(self.root, supported=False)
        original = json.loads((self.root/'registry/fixture/1.0.0-rc.1.json').read_text())
        path = self.root/'agreements/fixture/templates/new.md'
        path.parent.mkdir()
        path.write_text('New adoption instructions not covered by the approval.\n')
        commit = git_commit(self.root, 'Unreviewed added template')
        record = copy.deepcopy(original)
        record['version'] = '1.0.0-rc.2'
        record['source']['commit'] = commit
        record['source']['tag'] = 'fixture/v1.0.0-rc.2'
        gitstore.ensure_tag(self.root, record['source']['tag'], commit)
        (self.root/'registry/fixture/1.0.0-rc.2.json').write_bytes(json_bytes(record))
        self.reject('RELEASE_REVIEW_INVALID')

    def test_committed_intermediate_publication_receipt_cannot_disappear(self):
        add_protocol(self.root, supported=False)
        candidate = Catalog(self.root).select('fixture', '1.0.0-rc.1')
        decision = {'date': '2026-11-02', 'confirmed_by': 'Fixture maintainer',
                    'basis': 'Fictional local delivery',
                    'candidate_sha256': candidate['_package_sha256']}
        destination = Path(self.temp.name)/'delivery'
        publication.prepare(self.root, 'fixture', '1.0.0-rc.1', '1.0.0',
                            'verified-local-directory', str(destination), decision)
        git_commit(self.root, 'Prepared publication intent')
        lifecycle.publish(self.root, 'fixture', '1.0.0-rc.1', '1.0.0', destination, decision)
        git_commit(self.root, 'Published receipt')
        path = self.root/'registry/fixture/1.0.0.json'
        record = json.loads(path.read_text())
        record['status'] = 'prepared'
        record['publication']['state'] = 'prepared'
        del record['publication']['receipt']
        path.write_bytes(json_bytes(record))
        git_commit(self.root, 'Invalid removal of an intermediate published receipt')
        with self.assertRaises(CatalogError) as error:
            Catalog(self.root)
        self.assertIn(error.exception.code, ('VERSION_STATE_CHANGED', 'PUBLICATION_RECORD_CONFLICT'))

    def test_committed_intermediate_withdrawal_cannot_disappear(self):
        add_protocol(self.root, released=True)
        git_commit(self.root, 'Published fixture')
        lifecycle.withdraw(self.root, 'fixture', '1.0.0',
                           {'date': '2026-11-02', 'confirmed_by': 'Fixture maintainer',
                            'reason': 'Fictional distribution withdrawal'})
        git_commit(self.root, 'Withdrawn fixture')
        path = self.root/'registry/fixture/1.0.0.json'
        record = json.loads(path.read_text())
        del record['withdrawal']
        path.write_bytes(json_bytes(record))
        git_commit(self.root, 'Invalid deletion of an intermediate withdrawal')
        self.reject('WITHDRAWAL_RECORD_CHANGED')

    def test_merged_branch_cannot_hide_a_committed_withdrawal(self):
        add_protocol(self.root, released=True)
        git_commit(self.root, 'Published fixture')
        path = self.root/'registry/fixture/1.0.0.json'
        original = path.read_bytes()
        self.git('checkout', '-qb', 'withdrawn-side')
        lifecycle.withdraw(self.root, 'fixture', '1.0.0',
                           {'date': '2026-11-02', 'confirmed_by': 'Fixture maintainer',
                            'reason': 'Fictional withdrawal on a side branch'})
        git_commit(self.root, 'Withdraw fixture on a side branch')
        path.write_bytes(original)
        git_commit(self.root, 'Remove withdrawal before merging')
        self.git('checkout', '-q', '-')
        self.git('merge', '--no-ff', '-qm', 'Merge hidden withdrawal', 'withdrawn-side')
        self.reject('WITHDRAWAL_RECORD_CHANGED')

    def test_malformed_historical_publication_is_a_structured_error(self):
        path = self.root/'registry/yunxia-license/0.9.2-draft.1.json'
        original = path.read_bytes()
        record = json.loads(original)
        record['publication'] = 'Malformed historical publication'
        path.write_bytes(json_bytes(record))
        git_commit(self.root, 'Malformed historical registry value')
        path.write_bytes(original)
        self.reject('REGISTRY_BASELINE_INVALID')


if __name__ == '__main__':
    unittest.main()
