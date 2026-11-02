"""Behavioral tests against temporary target projects, never the source package."""
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'tooling/yunxia.py'
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia.project import workflow as yx
from yunxia.catalog import Catalog
from yunxia import lifecycle
from support import copy_catalog, add_protocol, approve, publish, git_commit, pin_payload, CURRENT_VERSION



class YunXiaWorkflow(unittest.TestCase):
    def setUp(self):
        yx.configure_unit('main')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.catalog_root = copy_catalog(Path(self.temp.name)/'catalog')
        self.ref = yx.reference(Catalog(self.catalog_root).select('yunxia-license', CURRENT_VERSION))
        environment = patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(self.catalog_root))
        environment.start(); self.addCleanup(environment.stop)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.write('src/main.py', 'print("hello")\n')

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')

    def scan(self):
        with yx.lock(self.root):
            result = yx.scan(self.root)
            if yx.load_state(self.root)['selected_agreement'] is None:
                ref = self.ref
                yx.select_agreement(self.root, ref['agreement_id'], ref['version'])
                result['selected_agreement'] = ref
            return result

    def answer(self, payload):
        payload = copy.deepcopy(payload)
        if 'adoption' in payload:
            payload['adoption'].setdefault('agreement', yx.load_state(self.root)['selected_agreement'])
        with yx.lock(self.root):
            return yx.answer(self.root, payload)

    def facts(self, paths=None):
        return {'confirmed_by': 'fixture user', 'basis': 'PRIVATE-AUTHORITY-EVIDENCE',
                'identity': {'project_name': 'Sample', 'licensor': 'Sample author', 'role': 'author'},
                'delivery': {'audience': 'recipients', 'description': 'source archive'},
                'assets': [{'paths': paths or ['src/main.py'], 'disposition': 'selected',
                            'origin': 'human', 'basis': 'PRIVATE-SOURCE-EVIDENCE'}]}

    def adopt(self):
        snapshot = self.scan()['snapshot_id']
        payload = self.facts()
        payload['adoption'] = {'authorized': True, 'accept_draft': True,
                               'date': '2026-10-24', 'snapshot_id': snapshot}
        self.answer(payload)
        return self.generate('adopt')

    def generate(self, mode='draft', write=True):
        with yx.lock(self.root):
            return yx.generate(self.root, mode, write)

    def assert_error(self, code, fn, *args, **kwargs):
        with self.assertRaises((yx.WorkflowError, yx.CatalogError)) as result:
            fn(*args, **kwargs)
        self.assertEqual(result.exception.code, code)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(SCRIPT), 'project', '--root', str(self.root), *args],
                              capture_output=True, text=True, env=dict(os.environ, YUNXIA_CATALOG_ROOT=str(self.catalog_root)))

    def test_read_only_scan_does_not_create_state(self):
        result = yx.scan(self.root, persist=False)
        self.assertIn('src/main.py', result['files'])
        self.assertFalse((self.root / '.yunxia').exists())

    def test_questions_are_bounded_and_missing_facts_generate_draft(self):
        self.scan()
        result = yx.questions(self.root)
        self.assertEqual(len(result['questions']), 3)
        self.assertTrue(all(q['unknown_allowed'] for q in result['questions']))
        self.generate()
        self.assertFalse((self.root / 'PROJECT-RIGHTS.md').exists())
        self.assertTrue((self.root / '.yunxia/draft/PROJECT-RIGHTS.md').is_file())
        self.assertTrue(yx.verify(self.root, mode='draft')['structure_valid'])
        self.assert_error('ADOPTION_BLOCKED', self.generate, 'adopt')

    def test_complete_local_adoption_cli_and_license_bytes(self):
        scan = json.loads(self.cli('scan').stdout)
        ref = self.ref
        chosen = self.cli('select', '--agreement', ref['agreement_id'], '--version', ref['version'])
        self.assertEqual(chosen.returncode, 0, chosen.stdout)
        payload = self.facts()
        payload['adoption'] = {'authorized': True, 'accept_draft': True, 'date': '2026-10-24', 'snapshot_id': scan['snapshot_id'], 'agreement': ref}
        p = Path(self.temp.name) / 'answers.json'
        p.write_bytes(yx.encoded(payload))
        result = self.cli('answer', '--input', str(p))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.cli('generate', '--mode', 'adopt', '--write')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.cli('verify')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / 'LICENSE.txt').read_bytes(), yx.license_bytes(self.ref))
        self.assertEqual(json.loads(result.stdout)['legal_assessment'], 'not_performed')

    def test_adoption_requires_explicit_scope_snapshot_and_draft_acceptance(self):
        self.scan()
        self.answer(self.facts())
        self.assert_error('ADOPTION_NOT_CONFIRMED', self.generate, 'adopt')
        payload = self.facts()
        payload['adoption'] = {'authorized': True, 'accept_draft': False, 'date': '2026-10-24',
                               'snapshot_id': self.scan()['snapshot_id']}
        self.assert_error('ADOPTION_FORMAT', self.answer, payload)
        payload['adoption']['accept_draft'] = True
        payload['adoption']['snapshot_id'] = 'outdated'
        self.assert_error('ADOPTION_SNAPSHOT_STALE', self.answer, payload)

    def test_repeat_run_preserves_state_and_material_bytes(self):
        self.adopt()
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.scan()
        result = self.generate('adopt')
        self.assertTrue(all(c['action'] == 'unchanged' for c in result['changes']))
        after = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(yx.questions(self.root)['questions'], [])

    def test_change_invalidates_only_changed_asset_and_adoption(self):
        self.write('src/other.py', 'pass\n')
        self.scan()
        self.answer(self.facts(['src/main.py', 'src/other.py']))
        self.write('src/main.py', 'print("changed")\n')
        self.assert_error('SCAN_STALE', self.generate)
        result = self.scan()
        self.assertEqual(result['changes']['changed'], ['src/main.py'])
        state = yx.load_state(self.root)
        self.assertTrue(yx.active_answer(state, 'src/other.py'))
        self.assertFalse(yx.active_answer(state, 'src/main.py'))

    def test_rename_provides_hint_and_retains_previous_evidence(self):
        self.scan()
        self.answer(self.facts())
        (self.root / 'src/main.py').rename(self.root / 'src/renamed.py')
        result = self.scan()
        self.assertEqual(result['changes']['rename_candidates'][0]['from'], 'src/main.py')
        state = yx.load_state(self.root)
        self.assertEqual(state['answers']['src/main.py']['basis'], 'PRIVATE-SOURCE-EVIDENCE')
        self.assertFalse(yx.active_answer(state, 'src/renamed.py'))

    def test_existing_license_and_notices_are_preserved(self):
        original = 'Existing upstream license\n'
        self.write('LICENSE', original)
        scan = self.scan()
        payload = self.facts()
        payload['assets'].append({'paths': ['LICENSE'], 'disposition': 'preserve', 'origin': 'third_party',
                                  'basis': 'existing notice', 'license_files': ['LICENSE'],
                                  'public_notice': 'Original notice retained.'})
        payload['adoption'] = {'authorized': True, 'accept_draft': True, 'date': '2026-10-24', 'snapshot_id': scan['snapshot_id']}
        self.answer(payload)
        self.generate('adopt')
        self.assertEqual((self.root / 'LICENSE').read_text(), original)
        self.assertEqual((self.root / 'LICENSE.yunxia-0.9.3.txt').read_bytes(), yx.license_bytes(self.ref))
        self.assertTrue((self.root / 'THIRD-PARTY.md').exists())

    def test_changed_notice_invalidates_linked_assets(self):
        self.write('vendor/lib.py', 'pass\n')
        self.write('vendor/LICENSE', 'upstream v1\n')
        self.scan()
        payload = self.facts()
        payload['assets'].append({'paths': ['vendor/lib.py', 'vendor/LICENSE'], 'disposition': 'preserve',
                                  'origin': 'third_party', 'basis': 'upstream', 'license_files': ['vendor/LICENSE']})
        self.answer(payload)
        self.write('vendor/LICENSE', 'upstream v2\n')
        self.scan()
        self.assertIn('UPSTREAM_NOTICE_CHANGED', {i['code'] for i in yx.questions(self.root)['blockers']})

    def test_source_notice_prevents_implicit_relicensing(self):
        self.write('src/main.py', '# SPDX-License-Identifier: MIT\npass\n')
        self.scan()
        self.assert_error('EXISTING_NOTICE_REQUIRES_EXPLICIT_SCOPE_DECISION', self.answer, self.facts())

    def test_private_evidence_is_never_exported(self):
        self.adopt()
        for rel in ('PROJECT-RIGHTS.md', 'LICENSE.txt'):
            self.assertNotIn('PRIVATE-', (self.root / rel).read_text())
        self.assertEqual((self.root / '.yunxia').stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / yx.STATE).stat().st_mode & 0o777, 0o600)

    def test_existing_manual_file_is_not_overwritten(self):
        self.write('PROJECT-RIGHTS.md', 'Human-maintained declaration\n')
        self.scan()
        payload = self.facts(['src/main.py', 'PROJECT-RIGHTS.md'])
        payload['adoption'] = {'authorized': True, 'accept_draft': True, 'date': '2026-10-24', 'snapshot_id': self.scan()['snapshot_id']}
        self.answer(payload)
        preview = self.generate('adopt', write=False)
        self.assertIn('PROJECT-RIGHTS.md', preview['conflicts'])
        self.assert_error('MANUAL_EDIT_CONFLICT', self.generate, 'adopt')
        self.assertEqual((self.root / 'PROJECT-RIGHTS.md').read_text(), 'Human-maintained declaration\n')
        self.assertFalse((self.root / 'LICENSE.txt').exists())

    def test_modified_generated_file_is_not_overwritten(self):
        self.adopt()
        self.write('PROJECT-RIGHTS.md', 'Human edit\n')
        self.assert_error('MANUAL_EDIT_CONFLICT', self.generate, 'adopt')
        self.assertFalse(yx.verify(self.root)['structure_valid'])
        self.assertEqual((self.root / 'PROJECT-RIGHTS.md').read_text(), 'Human edit\n')

    def test_profile_change_invalidates_adoption(self):
        self.adopt()
        payload = self.facts()
        payload['delivery']['description'] = 'new distribution'
        self.answer(payload)
        self.assert_error('ADOPTION_NOT_CONFIRMED', self.generate, 'adopt')

    def test_verify_cannot_replace_missing_adoption_record_with_generated_files(self):
        self.adopt()
        state = yx.load_state(self.root)
        del state['adoption']
        self.write(yx.STATE, json.dumps(state))
        result = yx.verify(self.root)
        self.assertFalse(result['structure_valid'])
        self.assertIn('ADOPTION_NOT_CONFIRMED', {i['code'] for i in result['issues']})

    def test_scan_skips_secrets_and_symlinks_without_following_them(self):
        self.write('.env', 'TOKEN=DO-NOT-ECHO')
        self.write('private.pem', 'DO-NOT-ECHO')
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('DO-NOT-ECHO')
        (self.root / 'link.txt').symlink_to(outside)
        result = self.scan()
        self.assertNotIn('DO-NOT-ECHO', json.dumps(result))
        self.assertEqual({x['path'] for x in result['skipped']}, {'.env', 'private.pem', 'link.txt'})

    def test_state_symlink_and_path_traversal_rejected(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (self.root / '.yunxia').symlink_to(outside, target_is_directory=True)
        self.assert_error('SYMLINK_PATH', self.scan)
        (self.root / '.yunxia').unlink()
        self.scan()
        p = self.facts(['../outside'])
        self.assert_error('UNSAFE_PATH', self.answer, p)

    def test_dependency_and_coverage_hints_do_not_grant_rights(self):
        self.write('package.json', json.dumps({'dependencies': {'demo': '^1.0'}}))
        self.write('package-lock.json', json.dumps({'packages': {'node_modules/demo': {'version': '1.2', 'license': 'MIT'}}}))
        self.write('requirements.txt', 'sample==1.0\n-r other.txt\n')
        self.write('Cargo.toml', '[package]\nname="example"\n')
        self.write('assets.zip', 'opaque')
        result = self.scan()
        self.assertEqual(len(result['dependency_hints']), 3)
        reasons = {x['reason'] for x in result['coverage_issues']}
        self.assertTrue({'requirement_not_parsed', 'unsupported_dependency_manifest', 'container_contents_not_inspected'} <= reasons)
        self.assertFalse(yx.load_state(self.root)['answers'])

    def test_unknown_facts_stay_pending(self):
        self.scan()
        payload = self.facts()
        payload['assets'][0].update(disposition='pending', origin='unknown')
        self.answer(payload)
        result = yx.questions(self.root)
        self.assertEqual(result['questions'], [])
        self.assertTrue(result['blockers'])
        self.assertEqual(result['deferred'][0]['paths'], ['src/main.py'])
        self.scan()
        self.assertEqual(yx.questions(self.root), result)
        self.assert_error('ADOPTION_BLOCKED', self.generate, 'adopt')

    def test_explicit_deferral_does_not_reask_or_authorize(self):
        self.scan()
        self.answer({'confirmed_by': 'user', 'basis': 'user cannot confirm yet',
                     'defer': ['identity', 'delivery', 'adoption'],
                     'assets': [{'paths': ['src/main.py'], 'disposition': 'pending',
                                 'origin': 'unknown', 'basis': 'unknown'}]})
        self.assertEqual(yx.questions(self.root)['questions'], [])
        self.assertEqual(len(yx.questions(self.root)['deferred']), 3)
        self.assert_error('ADOPTION_BLOCKED', self.generate, 'adopt')
        self.answer(self.facts())
        self.assertEqual(yx.questions(self.root)['deferred'][0]['code'], 'ADOPTION_NOT_CONFIRMED')
        self.assert_error('ADOPTION_NOT_CONFIRMED', self.generate, 'adopt')

    def test_changed_previously_unknown_file_is_asked_again(self):
        self.scan()
        payload = self.facts()
        payload['assets'][0].update(disposition='pending', origin='unknown')
        self.answer(payload)
        self.write('src/main.py', 'changed\n')
        self.scan()
        self.assertEqual(yx.questions(self.root)['questions'][0]['paths'], ['src/main.py'])

    def test_absent_optional_deferrals_do_not_create_spurious_answer_event(self):
        self.scan()
        self.answer(self.facts())
        state = yx.load_state(self.root)
        state.pop('deferred')
        self.write(yx.STATE, json.dumps(state))
        before = (self.root / yx.STATE).read_bytes()
        self.assertFalse(self.answer(self.facts())['saved'])
        self.assertEqual((self.root / yx.STATE).read_bytes(), before)

    def test_atomic_failure_rolls_back_existing_and_new_files(self):
        self.adopt()
        old = (self.root / 'PROJECT-RIGHTS.md').read_bytes()
        (self.root / 'PROJECT-RIGHTS.md').chmod(0o600)
        real_atomic = yx.atomic
        def one_failure(path, content, private=False, mode=None):
            if path.name == 'AI-USAGE.md':
                raise OSError('simulated disk failure')
            return real_atomic(path, content, private, mode)
        with yx.lock(self.root), patch.object(yx, 'atomic', side_effect=one_failure):
            with self.assertRaises(OSError):
                yx.transaction(self.root, {'PROJECT-RIGHTS.md': b'new', 'AI-USAGE.md': b'new'})
        self.assertEqual((self.root / 'PROJECT-RIGHTS.md').read_bytes(), old)
        self.assertEqual(stat.S_IMODE((self.root / 'PROJECT-RIGHTS.md').stat().st_mode), 0o600)
        self.assertFalse((self.root / 'AI-USAGE.md').exists())
        self.assertFalse((self.root / yx.JOURNAL).exists())

    def test_interrupted_write_requires_recovery_and_restores_state(self):
        self.scan()
        old = (self.root / yx.STATE).read_bytes()
        real_atomic = yx.atomic
        def crash_after_output(path, content, private=False):
            real_atomic(path, content, private)
            if path.name == 'PROJECT-RIGHTS.md':
                raise KeyboardInterrupt()
        with yx.lock(self.root), patch.object(yx, 'atomic', side_effect=crash_after_output):
            with self.assertRaises(KeyboardInterrupt):
                yx.generate(self.root, write=True)
        self.assert_error('RECOVERY_REQUIRED', yx.load_state, self.root)
        result = self.cli('recover')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.root / yx.STATE).read_bytes(), old)
        self.assertFalse((self.root / '.yunxia/draft/PROJECT-RIGHTS.md').exists())

    def test_recovery_does_not_overwrite_intervening_manual_change(self):
        self.scan()
        rel = '.yunxia/draft/PROJECT-RIGHTS.md'
        self.write(rel, 'manual edit')
        journal = {'entries': [{'path': rel, 'before': None, 'after': base64.b64encode(b'generated').decode()}]}
        self.write(yx.JOURNAL, json.dumps(journal))
        self.assert_error('RECOVERY_CONFLICT', yx.restore, self.root)
        self.assertEqual((self.root / rel).read_text(), 'manual edit')

    def test_partial_recovery_preserves_a_new_manual_edit_and_can_retry(self):
        self.adopt()
        rights = self.root / 'PROJECT-RIGHTS.md'
        rights.chmod(0o600)
        before_rights = rights.read_bytes()
        state_path = self.root / yx.STATE
        before_state = state_path.read_bytes()
        real = yx.atomic
        def interrupted(path, content, private=False, mode=None):
            real(path, content, private, mode)
            if path == state_path:
                raise KeyboardInterrupt()
        with yx.lock(self.root), patch.object(yx, 'atomic', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                yx.transaction(self.root, {'PROJECT-RIGHTS.md': b'new rights', yx.STATE: b'new state'})
        journal = (self.root / yx.JOURNAL).read_bytes()
        def manual_edit_during_restore(path, content, private=False, mode=None):
            real(path, content, private, mode)
            if path == state_path:
                rights.write_bytes(b'concurrent manual edit')
        with yx.lock(self.root, recovery=True), patch.object(yx, 'atomic', side_effect=manual_edit_during_restore):
            self.assert_error('RECOVERY_CONFLICT', yx.restore, self.root)
        self.assertEqual(state_path.read_bytes(), before_state)
        self.assertEqual(rights.read_bytes(), b'concurrent manual edit')
        self.assertEqual((self.root / yx.JOURNAL).read_bytes(), journal)
        # Restore the recorded interrupted image after resolving the human edit.
        rights.write_bytes(b'new rights'); rights.chmod(0o644)
        with yx.lock(self.root, recovery=True):
            self.assertTrue(yx.restore(self.root))
        self.assertEqual(rights.read_bytes(), before_rights)
        self.assertEqual(stat.S_IMODE(rights.stat().st_mode), 0o600)
        self.assertFalse((self.root / yx.JOURNAL).exists())
        self.assertTrue(yx.verify(self.root)['structure_valid'])

    def test_recovery_preserves_mode_only_edits_and_original_permissions(self):
        self.adopt()
        rights = self.root / 'PROJECT-RIGHTS.md'
        rights.chmod(0o640)
        original = rights.read_bytes()
        real = yx.atomic
        def interrupted(path, content, private=False, mode=None):
            real(path, content, private, mode)
            if path == rights:
                raise KeyboardInterrupt()
        with yx.lock(self.root), patch.object(yx, 'atomic', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                yx.transaction(self.root, {'PROJECT-RIGHTS.md': b'new rights'})
        journal = (self.root / yx.JOURNAL).read_bytes()
        rights.chmod(0o600)
        with yx.lock(self.root, recovery=True):
            self.assert_error('RECOVERY_CONFLICT', yx.restore, self.root)
        self.assertEqual(rights.read_bytes(), b'new rights')
        self.assertEqual(stat.S_IMODE(rights.stat().st_mode), 0o600)
        self.assertEqual((self.root / yx.JOURNAL).read_bytes(), journal)
        rights.chmod(0o644)
        with yx.lock(self.root, recovery=True):
            self.assertTrue(yx.restore(self.root))
        self.assertEqual(rights.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(rights.stat().st_mode), 0o640)

    def test_legacy_journal_without_permissions_still_recovers(self):
        self.scan()
        rel = '.yunxia/draft/PROJECT-RIGHTS.md'
        self.write(rel, 'interrupted generation')
        journal = {'entries': [{'path': rel, 'before': base64.b64encode(b'old draft').decode(),
                                'after': base64.b64encode(b'interrupted generation').decode()}]}
        self.write(yx.JOURNAL, json.dumps(journal))
        with yx.lock(self.root, recovery=True):
            self.assertTrue(yx.restore(self.root))
        self.assertEqual((self.root / rel).read_bytes(), b'old draft')
        self.assertEqual(stat.S_IMODE((self.root / rel).stat().st_mode), 0o600)

    def test_invalid_journal_modes_cannot_overwrite_files(self):
        self.scan()
        self.write('PROJECT-RIGHTS.md', 'new rights')
        for before_mode, after_mode in ((True, 0o644), (0o600, -1), (0o600, 0o10000), (None, 0o644)):
            with self.subTest(before_mode=before_mode, after_mode=after_mode):
                journal = {'entries': [{'path': 'PROJECT-RIGHTS.md',
                                        'before': base64.b64encode(b'old rights').decode(),
                                        'after': base64.b64encode(b'new rights').decode(),
                                        'before_mode': before_mode, 'after_mode': after_mode}]}
                self.write(yx.JOURNAL, json.dumps(journal))
                self.assert_error('INVALID_JOURNAL', yx.restore, self.root)
                self.assertEqual((self.root / 'PROJECT-RIGHTS.md').read_text(), 'new rights')

    def test_lock_prevents_concurrent_writer(self):
        with yx.lock(self.root):
            result = self.cli('scan')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['error'], 'LOCKED')

    def test_project_lock_interrupt_before_commit_leaves_no_pid_record(self):
        with patch('yunxia.common.os.link', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            with yx.lock(self.root):
                self.fail('The lock must not be acquired before its PID record is committed')
        self.assertFalse((self.root / '.yunxia/lock.json').exists())
        with yx.lock(self.root, recovery=True):
            self.assertEqual(yx.read_json(self.root / '.yunxia/lock.json'), {'pid': os.getpid()})

    def test_project_lock_interrupt_after_commit_leaves_recoverable_pid(self):
        real_link = os.link
        def interrupt_after_commit(*args, **kwargs):
            real_link(*args, **kwargs)
            raise KeyboardInterrupt()
        with patch('yunxia.common.os.link', side_effect=interrupt_after_commit), self.assertRaises(KeyboardInterrupt):
            with yx.lock(self.root):
                self.fail('The injected interruption must precede the protected operation')
        lock_path = self.root / '.yunxia/lock.json'
        self.assertEqual(yx.read_json(lock_path), {'pid': os.getpid()})
        with patch.object(yx.os, 'kill', side_effect=ProcessLookupError), yx.lock(self.root, recovery=True):
            self.assertEqual(yx.read_json(lock_path), {'pid': os.getpid()})
        self.assertFalse(lock_path.exists())

    def test_project_recovery_preserves_malformed_existing_lock(self):
        yx.private_dir(self.root)
        lock_path = self.root / '.yunxia/lock.json'
        for content, expected in ((b'', 'INVALID_JSON'), (b'{}\n', 'INVALID_LOCK')):
            with self.subTest(content=content):
                lock_path.write_bytes(content)
                with self.assertRaises(yx.WorkflowError) as result:
                    with yx.lock(self.root, recovery=True):
                        self.fail('A malformed lock is not evidence of a dead owner')
                self.assertEqual(result.exception.code, expected)
                self.assertEqual(lock_path.read_bytes(), content)

    def test_recovery_guard_blocks_a_second_process_before_pid_probe(self):
        yx.private_dir(self.root)
        lock_path = self.root / '.yunxia/lock.json'
        lock_path.write_bytes(yx.encoded({'pid': 99999999}))
        calls = []
        def dead_owner(pid, signal):
            contender = self.cli('recover')
            calls.append(contender)
            raise ProcessLookupError()
        with patch.object(yx.os, 'kill', side_effect=dead_owner):
            with yx.lock(self.root, recovery=True):
                self.assertEqual(json.loads(lock_path.read_text())['pid'], os.getpid())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].returncode, 2)
        self.assertEqual(json.loads(calls[0].stdout)['error'], 'LOCKED')
        self.assertFalse(lock_path.exists())
        self.assertTrue((self.root / '.yunxia/lock.guard').is_file())

    def test_artifact_detects_missing_license_and_private_state(self):
        self.adopt()
        artifact = Path(self.temp.name) / 'release.zip'
        with zipfile.ZipFile(artifact, 'w') as z:
            z.write(self.root / 'src/main.py', 'src/main.py')
            z.write(self.root / 'PROJECT-RIGHTS.md', 'PROJECT-RIGHTS.md')
            z.writestr('.yunxia/answers.json', 'PRIVATE')
        result = yx.verify(self.root, artifact=artifact)
        codes = {i['code'] for i in result['issues']}
        self.assertTrue({'ARTIFACT_MISSING_OR_CHANGED', 'PRIVATE_STATE_INCLUDED'} <= codes)

    def test_complete_source_artifact_passes(self):
        self.adopt()
        artifact = Path(self.temp.name) / 'release.zip'
        with zipfile.ZipFile(artifact, 'w') as z:
            for rel in ('src/main.py', 'PROJECT-RIGHTS.md', 'LICENSE.txt'):
                z.write(self.root / rel, 'release/' + rel)
        self.assertTrue(yx.verify(self.root, artifact=artifact, prefix='release')['structure_valid'])

    def test_artifact_traversal_and_unknown_content_fail(self):
        self.adopt()
        artifact = Path(self.temp.name) / 'release.zip'
        with zipfile.ZipFile(artifact, 'w') as z:
            z.writestr('../evil', 'x')
        self.assert_error('UNSAFE_PATH', yx.verify, self.root, artifact=artifact)
        with zipfile.ZipFile(artifact, 'w') as z:
            z.writestr('unknown.py', 'x')
        result = yx.verify(self.root, artifact=artifact)
        self.assertIn('UNSCANNED_ARTIFACT_FILE', {x['code'] for x in result['issues']})

    def test_invalid_json_state_and_cli_errors_are_structured(self):
        self.scan()
        for value in ([], None, {'state_version': 999}):
            self.write(yx.STATE, json.dumps(value))
            result = self.cli('questions')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, '')
            self.assertIn('error', json.loads(result.stdout))

    def test_invalid_nested_state_returns_json_not_traceback(self):
        self.adopt()
        original = yx.load_state(self.root)
        mutations = [(['profile', 'identity'], []), (['answers', 'src/main.py', 'notice_hashes'], []),
                     (['generations', 'adopt', 'files'], []), (['adoption'], None)]
        for path, value in mutations:
            with self.subTest(path=path):
                state = copy.deepcopy(original)
                cursor = state
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value
                self.write(yx.STATE, json.dumps(state))
                result = self.cli('verify')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, '')
                self.assertEqual(json.loads(result.stdout)['error'], 'INVALID_STATE')

    def test_credentials_and_urls_are_not_read_or_echoed(self):
        self.write('.aws/credentials', 'SECRET-PASSWORD')
        self.write('service-account-demo.json', '{"private_key":"SECRET-PASSWORD"}')
        self.write('package.json', json.dumps({'dependencies': {'demo': 'https://user:SECRET-PASSWORD@example.test/pkg'}}))
        self.write('pyproject.toml', '[project]\ndependencies=["demo @ https://user:SECRET-PASSWORD@example.test/pkg"]\n')
        result = self.scan()
        self.assertNotIn('SECRET-PASSWORD', json.dumps(result))
        self.assertNotIn('service-account-demo.json', result['files'])
        self.assertNotIn('.aws/credentials', result['files'])

    def test_bundled_license_tampering_is_rejected(self):
        self.scan()
        fake = self.catalog_copy()
        path = fake/'registry/yunxia-license/0.9.3-draft.2.json'
        entry = Catalog(fake).select('yunxia-license', CURRENT_VERSION)
        record = json.loads(path.read_text())
        pin_payload(fake, record, dict(entry['_payload'], **{'text.txt': b'changed'}))
        path.write_bytes(yx.encoded(record))
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            self.assert_error('AGREEMENT_TEXT_HASH_MISMATCH', self.generate)

    def test_generated_file_symlink_does_not_write_outside_project(self):
        self.adopt()
        outside = Path(self.temp.name) / 'private.txt'
        outside.write_text('outside')
        (self.root / 'PROJECT-RIGHTS.md').unlink()
        (self.root / 'PROJECT-RIGHTS.md').symlink_to(outside)
        self.assert_error('SYMLINK_PATH', self.generate, 'adopt')
        self.assertEqual(outside.read_text(), 'outside')

    def test_malformed_dependency_record_is_a_coverage_issue(self):
        self.write('package.json', '{"dependencies":[]}')
        result = self.scan()
        self.assertEqual(result['coverage_issues'][0]['reason'], 'invalid_dependency_manifest')

    def test_unreadable_dependency_text_reports_coverage_without_echoing_contents(self):
        path = self.root / 'package.json'
        for raw in (b'\xff\xfePRIVATE-CONTENT', b'\0PRIVATE-CONTENT', b'PRIVATE-CONTENT' * 4):
            with self.subTest(raw=raw), patch.object(yx, 'MAX_TEXT', 32):
                path.write_bytes(raw)
                result = yx.scan(self.root, persist=False)
                self.assertEqual(result['coverage_issues'], [
                    {'path': 'package.json', 'reason': 'manifest_text_not_parsed'}])
                self.assertEqual(result['dependency_hints'], [])
                self.assertEqual(result['files']['package.json']['sha256'], yx.digest(raw))
                self.assertNotIn('PRIVATE-CONTENT', json.dumps(result))
                self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((self.root / '.yunxia').exists())

    def test_private_directory_is_not_allowed_in_artifact_prefix(self):
        self.adopt()
        dest = Path(self.temp.name) / 'artifact'
        dest.mkdir()
        (dest / 'link').symlink_to(self.root, target_is_directory=True)
        self.assert_error('ARTIFACT_SYMLINK', yx.verify, self.root, artifact=dest, prefix='link')

    def select(self, ref):
        with yx.lock(self.root):
            return yx.select_agreement(self.root, ref['agreement_id'], ref['version'])

    def catalog_copy(self):
        fake = Path(self.temp.name) / 'catalog-copy'
        shutil.copytree(self.catalog_root, fake)
        return fake

    def test_adoption_without_protocol_binding_is_rejected(self):
        snapshot = self.scan()
        payload = self.facts()
        payload['adoption'] = {'authorized': True, 'accept_draft': True, 'date': '2026-10-24', 'snapshot_id': snapshot['snapshot_id']}
        with yx.lock(self.root):
            self.assert_error('ADOPTION_FORMAT', yx.answer, self.root, payload)

    def test_selection_is_explicit_and_does_not_grant_adoption(self):
        with yx.lock(self.root):
            yx.scan(self.root)
        self.assertEqual(yx.questions(self.root)['questions'][0]['id'], 'AGREEMENT_NOT_SELECTED')
        self.assert_error('AGREEMENT_NOT_SELECTED', self.generate)
        ref = self.ref
        self.select(ref)
        self.assertNotIn('adoption', yx.load_state(self.root))

    def test_new_catalog_recommendation_does_not_upgrade_existing_project(self):
        self.adopt()
        original = (self.root / yx.STATE).read_bytes()
        license_before = (self.root / 'LICENSE.txt').read_bytes()
        fake = self.catalog_copy()
        lifecycle.revise(fake, 'yunxia-license', CURRENT_VERSION, '1.0.0')
        approve(fake, 'yunxia-license')
        lifecycle.freeze(fake, 'yunxia-license', '1.0.0-rc.1', 'Fictional release test')
        publish(fake, 'yunxia-license')
        lifecycle.recommend(fake, 'yunxia-license', '1.0.0', 'Fixture recommendation')
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            self.assertEqual(yx.questions(self.root)['questions'], [])
            self.generate('adopt')
            self.assertTrue(yx.verify(self.root)['structure_valid'])
        self.assertEqual((self.root / yx.STATE).read_bytes(), original)
        self.assertEqual((self.root / 'LICENSE.txt').read_bytes(), license_before)

    def test_same_version_replaced_text_cannot_reuse_old_confirmation(self):
        self.adopt()
        before = (self.root / yx.STATE).read_bytes()
        fake = self.catalog_copy()
        path = fake/'registry/yunxia-license/0.9.3-draft.2.json'
        entry = Catalog(fake).select('yunxia-license', CURRENT_VERSION)
        body = b'TEST ONLY: changed terms\n'
        manifest = json.loads(path.read_text())
        pin_payload(fake, manifest, dict(entry['_payload'], **{'text.txt': body}))
        manifest['sha256'] = yx.digest(body)
        manifest['files']['text.txt'] = manifest['sha256']
        path.write_bytes(yx.encoded(manifest))
        registry = fake/'tooling/src/yunxia/adapters/support.json'
        data = json.loads(registry.read_text())
        for row in data['supported']:
            if row['agreement'] == self.ref:
                row['agreement'] = yx.reference(manifest)
        registry.write_bytes(yx.encoded(data))
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            self.assert_error('VERSION_IDENTITY_CHANGED', self.generate, 'adopt')
            self.assert_error('VERSION_IDENTITY_CHANGED', yx.verify, self.root)
        self.assertEqual((self.root / yx.STATE).read_bytes(), before)

    def test_generation_keeps_first_output_image_when_file_is_deleted_during_planning(self):
        self.adopt()
        state_before=(self.root/yx.STATE).read_bytes()
        target=self.root/'PROJECT-RIGHTS.md'
        actual=yx.current_bytes;calls=0
        def delete_before_second_read(root,rel):
            nonlocal calls
            if rel=='PROJECT-RIGHTS.md':
                calls+=1
                if calls==2:target.unlink()
            return actual(root,rel)
        with patch.object(yx,'current_bytes',side_effect=delete_before_second_read):
            self.assert_error('CONCURRENT_EDIT',self.generate,'adopt')
        self.assertFalse(target.exists())
        self.assertEqual((self.root/yx.STATE).read_bytes(),state_before)
        self.assertFalse((self.root/yx.JOURNAL).exists())

    def test_different_version_requires_its_own_adoption(self):
        self.adopt()
        old_adoption = copy.deepcopy(yx.load_state(self.root)['adoption'])
        old = yx.catalog().select('yunxia-license', '0.9.3-draft.1')
        self.select(yx.reference(old))
        self.assert_error('ADOPTION_NOT_CONFIRMED', self.generate, 'adopt')
        self.assertIn('GENERATION_AGREEMENT_MISMATCH', {i['code'] for i in yx.verify(self.root)['issues']})
        payload = self.facts()
        payload['adoption'] = old_adoption
        payload['adoption'].pop('context_id'); payload['adoption'].pop('confirmation')
        self.assert_error('ADOPTION_AGREEMENT_MISMATCH', self.answer, payload)

    def test_historical_version_can_be_selected_and_verified(self):
        snap = self.scan()
        old = yx.catalog().select('yunxia-license', '0.9.2-draft.1')
        self.select(yx.reference(old))
        payload = self.facts()
        payload['adoption'] = {'authorized': True, 'accept_draft': True, 'date': '2026-10-24', 'snapshot_id': snap['snapshot_id']}
        self.answer(payload); self.generate('adopt')
        self.assertEqual((self.root / 'LICENSE.txt').read_bytes(), old['_text'])
        self.assertTrue(yx.verify(self.root)['structure_valid'])

    def test_switch_and_confirm_archives_previous_adoption_and_material(self):
        self.adopt()
        old = (self.root / 'PROJECT-RIGHTS.md').read_bytes()
        entry = yx.catalog().select('yunxia-license', '0.9.2-draft.1')
        self.select(yx.reference(entry))
        payload = self.facts()
        payload['adoption'] = {'authorized': True, 'accept_draft': True, 'date': '2026-10-24', 'snapshot_id': self.scan()['snapshot_id']}
        self.answer(payload); self.generate('adopt')
        history = yx.load_state(self.root)['history']
        self.assertTrue(any(h['event'] == 'superseded_adoption' for h in history))
        entry = next(h for h in history if h['event'] == 'superseded_generation')
        self.assertEqual(base64.b64decode(entry['content_base64']['PROJECT-RIGHTS.md']), old)
        self.assertTrue(yx.verify(self.root)['structure_valid'])


    def add_fixture_protocol(self, fake, kind='license', renderer='scope-license-v1', released=False):
        return add_protocol(fake, kind=kind, supported=renderer is not None, released=released)

    def test_another_license_uses_catalog_data_without_changing_code(self):
        fake = self.catalog_copy()
        ref = self.add_fixture_protocol(fake)
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            snap = self.scan()
            self.select(ref)
            payload = self.facts(); payload['assets'][0]['disposition'] = 'selected'
            payload['adoption'] = {'authorized': True, 'accept_draft': True, 'date': '2026-10-24', 'snapshot_id': snap['snapshot_id']}
            self.answer(payload); self.generate('adopt')
            self.assertTrue(yx.verify(self.root)['structure_valid'])
            self.assertEqual((self.root / 'LICENSE.txt').read_bytes(), b'Fictional test protocol only.\n')
            self.assertIn('Fixture protocol', (self.root / 'PROJECT-RIGHTS.md').read_text())

    def test_unsupported_type_is_inspectable_but_not_generated_as_license(self):
        fake = self.catalog_copy()
        ref = self.add_fixture_protocol(fake, kind='nda', renderer=None)
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            self.scan(); self.select(ref)
            self.assertEqual(yx.selected(yx.load_state(self.root))['kind'], 'nda')
            self.assert_error('UNSUPPORTED_AGREEMENT_WORKFLOW', self.generate)

    def test_catalog_identity_and_symlinks_are_rejected(self):
        fake = self.catalog_copy()
        p = fake / 'registry/yunxia-license/0.9.3-draft.2.json'
        data = json.loads(p.read_text()); data['version'] = 'wrong-version'
        p.write_bytes(yx.encoded(data))
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            self.assert_error('RELEASE_IDENTITY_MISMATCH', yx.catalog)
        data['version'] = CURRENT_VERSION; p.write_bytes(yx.encoded(data))
        body = p.read_bytes(); p.unlink()
        outside = Path(self.temp.name)/'outside.json'; outside.write_bytes(body); p.symlink_to(outside)
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            self.assert_error('CATALOG_PATH_INVALID', yx.catalog)

    def test_tampered_generation_binding_is_not_accepted(self):
        self.adopt()
        state = yx.load_state(self.root)
        state['generations']['adopt']['agreement'] = yx.reference(yx.catalog().select('yunxia-license', '0.9.2-draft.1'))
        self.write(yx.STATE, json.dumps(state))
        self.assertIn('GENERATION_AGREEMENT_MISMATCH', {i['code'] for i in yx.verify(self.root)['issues']})

    def test_inspect_only_protocol_needs_no_license_output_filename(self):
        fake = self.catalog_copy()
        ref = self.add_fixture_protocol(fake, kind='nda', renderer=None)
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            self.scan(); self.select(ref)
            self.assertEqual(yx.catalog().resolve(ref)['_text'], b'Fictional test protocol only.\n')
            self.assertIsNone(yx.catalog().resolve(ref)['output_name'])
            self.assert_error('UNSUPPORTED_AGREEMENT_WORKFLOW', self.generate)

    def test_metadata_cannot_advertise_unimplemented_generation(self):
        fake = self.catalog_copy()
        ref = self.add_fixture_protocol(fake, kind='nda', renderer=None)
        p = fake / 'registry/fixture/1.0.0-rc.1.json'
        manifest = json.loads(p.read_text()); manifest['capabilities'] = ['adopt']
        p.write_bytes(yx.encoded(manifest))
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            self.assert_error('RELEASE_CONTAINS_ADAPTER_CONFIG', yx.catalog)

    def test_nonstandard_catalog_json_is_rejected(self):
        fake = self.catalog_copy()
        p = fake / 'registry/yunxia-license/0.9.3-draft.2.json'
        original = p.read_bytes()
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            data = json.loads(original); data['format_version'] = True
            p.write_bytes(yx.encoded(data))
            self.assert_error('RELEASE_FORMAT', yx.catalog)
            data = json.loads(original); data['unexpected'] = float('nan')
            p.write_text(json.dumps(data))
            self.assert_error('CATALOG_FORMAT', yx.catalog)

    def test_missing_required_state_fields_fail_without_traceback_or_write(self):
        self.adopt()
        original = yx.load_state(self.root)
        for field in ('snapshot', 'tool_version', 'answers', 'profile', 'history', 'generations'):
            with self.subTest(field=field):
                state = copy.deepcopy(original); state.pop(field)
                self.write(yx.STATE, json.dumps(state)); before = (self.root / yx.STATE).read_bytes()
                process = self.cli('questions')
                self.assertEqual(process.returncode, 2)
                self.assertEqual(process.stderr, '')
                self.assertEqual(json.loads(process.stdout)['error'], 'INVALID_STATE')
                self.assertEqual((self.root / yx.STATE).read_bytes(), before)

    def test_empty_generation_cannot_be_verified_as_complete(self):
        self.adopt()
        state = yx.load_state(self.root); state['generations']['adopt']['files'] = {}
        self.write(yx.STATE, json.dumps(state))
        self.assert_error('INVALID_STATE', yx.verify, self.root)


    def test_switch_cannot_archive_edited_or_missing_previous_full_text(self):
        self.write('LICENSE', 'Existing upstream fixture\n')
        snap = self.scan()
        payload = self.facts()
        payload['assets'].append({'paths': ['LICENSE'], 'disposition': 'preserve', 'origin': 'third_party',
                                  'basis': 'fixture', 'license_files': ['LICENSE']})
        payload['adoption'] = {'authorized': True, 'accept_draft': True, 'date': '2026-10-24', 'snapshot_id': snap['snapshot_id']}
        self.answer(payload); self.generate('adopt')
        old_path = self.root / 'LICENSE.yunxia-0.9.3.txt'
        original = old_path.read_bytes()
        entry = yx.catalog().select('yunxia-license', '0.9.2-draft.1')
        self.select(yx.reference(entry))
        self.answer(payload)
        before = (self.root / yx.STATE).read_bytes()
        for content in (b'human change', None):
            with self.subTest(content=content):
                if content is None:
                    old_path.unlink()
                else:
                    old_path.write_bytes(content)
                self.assert_error('MANUAL_EDIT_CONFLICT', self.generate, 'adopt')
                self.assertEqual((self.root / yx.STATE).read_bytes(), before)
                self.assertFalse((self.root / 'LICENSE.yunxia-0.9.2.txt').exists())
        old_path.write_bytes(original)
        self.generate('adopt')
        history = yx.load_state(self.root)['history']
        old = next(h for h in history if h['event'] == 'superseded_generation')
        archived = base64.b64decode(old['content_base64']['LICENSE.yunxia-0.9.3.txt'])
        self.assertEqual(archived, original)
        self.assertEqual(yx.digest(archived), old['generation']['files']['LICENSE.yunxia-0.9.3.txt'])

    def test_concurrent_edit_after_planning_is_preserved(self):
        self.adopt()
        state_before = (self.root / yx.STATE).read_bytes()
        real_transaction = yx.transaction
        def race(root, updates, expected=None):
            self.write('PROJECT-RIGHTS.md', 'concurrent human edit')
            return real_transaction(root, updates, expected=expected)
        with patch.object(yx, 'transaction', side_effect=race):
            self.assert_error('CONCURRENT_EDIT', self.generate, 'adopt')
        self.assertEqual((self.root / 'PROJECT-RIGHTS.md').read_text(), 'concurrent human edit')
        self.assertEqual((self.root / yx.STATE).read_bytes(), state_before)

    def test_mutations_keep_the_state_image_from_the_initial_load(self):
        self.scan()
        original = (self.root / yx.STATE).read_bytes()
        concurrent = json.loads(original)
        concurrent['history'].append({'event': 'concurrent_evidence', 'basis': 'KEEP THIS EVIDENCE'})
        concurrent_bytes = yx.encoded(concurrent)
        real_validate = yx.validate_state
        operations = {
            'scope': lambda: yx.set_scope(self.root, ['src']),
            'select': lambda: yx.select_agreement(self.root, 'yunxia-license', '0.9.2-draft.1'),
            'scan': lambda: yx.scan(self.root),
            'answer': lambda: yx.answer(self.root, self.facts()),
            'generate': lambda: yx.generate(self.root, 'draft', True),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                (self.root / yx.STATE).write_bytes(original)
                added = self.root / 'src/new.py'
                added.unlink(missing_ok=True)
                if name == 'scan':
                    added.write_text('print(2)\n')
                edited = False
                def edit_after_validation(state):
                    nonlocal edited
                    result = real_validate(state)
                    if not edited:
                        (self.root / yx.STATE).write_bytes(concurrent_bytes)
                        edited = True
                    return result
                with yx.lock(self.root), patch.object(yx, 'validate_state', side_effect=edit_after_validation):
                    self.assert_error('CONCURRENT_EDIT', operation)
                self.assertEqual((self.root / yx.STATE).read_bytes(), concurrent_bytes)
                self.assertFalse((self.root / yx.JOURNAL).exists())
                self.assertFalse((self.root / '.yunxia/draft/PROJECT-RIGHTS.md').exists())

    def test_first_state_write_preserves_concurrent_initialization(self):
        real_fresh = yx.fresh
        concurrent = real_fresh()
        concurrent['history'].append({'event': 'concurrent_initialization'})
        concurrent_bytes = yx.encoded(concurrent)
        def initialize_after_read():
            state = real_fresh()
            (self.root / yx.STATE).write_bytes(concurrent_bytes)
            return state
        with yx.lock(self.root), patch.object(yx, 'fresh', side_effect=initialize_after_read):
            self.assert_error('CONCURRENT_EDIT', yx.scan, self.root)
        self.assertEqual((self.root / yx.STATE).read_bytes(), concurrent_bytes)
        self.assertFalse((self.root / yx.JOURNAL).exists())

    def test_source_change_during_render_blocks_generation(self):
        self.adopt()
        state_before = (self.root / yx.STATE).read_bytes()
        rights_before = (self.root / 'PROJECT-RIGHTS.md').read_bytes()
        real_render = yx.render
        def edit_after_render(*args):
            outputs = real_render(*args)
            self.write('src/main.py', 'print("concurrent source edit")\n')
            return outputs
        with patch.object(yx, 'render', side_effect=edit_after_render):
            self.assert_error('SCAN_STALE', self.generate, 'adopt')
        self.assertEqual((self.root / yx.STATE).read_bytes(), state_before)
        self.assertEqual((self.root / 'PROJECT-RIGHTS.md').read_bytes(), rights_before)
        self.assertEqual((self.root / 'src/main.py').read_text(), 'print("concurrent source edit")\n')
        self.assertFalse((self.root / yx.JOURNAL).exists())

    def test_concurrent_edit_after_expected_check_is_not_adopted_as_before_image(self):
        self.adopt()
        rights = self.root / 'PROJECT-RIGHTS.md'
        original = rights.read_bytes()
        real = yx.current_bytes
        reads = 0
        def edit_after_check(root, rel):
            nonlocal reads
            content = real(root, rel)
            reads += 1
            if reads == 1:
                rights.write_bytes(b'concurrent human edit')
            return content
        with yx.lock(self.root), patch.object(yx, 'current_bytes', side_effect=edit_after_check):
            self.assert_error('CONCURRENT_EDIT', yx.transaction, self.root,
                              {'PROJECT-RIGHTS.md': b'new output'}, {'PROJECT-RIGHTS.md': original})
        self.assertEqual(rights.read_bytes(), b'concurrent human edit')
        self.assertFalse((self.root / yx.JOURNAL).exists())

    def test_before_image_keeps_bytes_and_mode_from_the_same_open_file(self):
        self.write('PROJECT-RIGHTS.md', 'old content')
        path = self.root / 'PROJECT-RIGHTS.md'; path.chmod(0o600)
        replacement = self.root / 'replacement'; replacement.write_bytes(b'new content'); replacement.chmod(0o644)
        real = os.open
        def replace_after_open(target, flags, *args, **kwargs):
            fd = real(target, flags, *args, **kwargs)
            if Path(target) == path:
                replacement.replace(path)
            return fd
        with patch.object(yx.os, 'open', side_effect=replace_after_open):
            content, mode = yx.current_image(self.root, 'PROJECT-RIGHTS.md')
        self.assertEqual((content, mode), (b'old content', 0o600))
        self.assertEqual(path.read_bytes(), b'new content')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_directory_and_zip_reject_content_outside_prefix_consistently(self):
        self.adopt()
        artifact = Path(self.temp.name) / 'release'
        for rel in ('LICENSE.txt', 'PROJECT-RIGHTS.md', 'src/main.py'):
            p = artifact / 'package' / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes((self.root / rel).read_bytes())
        self.assertTrue(yx.verify(self.root, artifact=artifact, prefix='package')['structure_valid'])
        (artifact / '.yunxia').mkdir()
        (artifact / '.yunxia/state.json').write_text('PRIVATE-FIXTURE')
        self.assert_error('ARTIFACT_OUTSIDE_PREFIX', yx.verify, self.root, artifact=artifact, prefix='package')
        archive = Path(self.temp.name) / 'release.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for p in artifact.rglob('*'):
                if p.is_file(): z.write(p, p.relative_to(artifact).as_posix())
        self.assert_error('ARTIFACT_OUTSIDE_PREFIX', yx.verify, self.root, artifact=archive, prefix='package')

    def test_released_protocol_does_not_require_accepting_it_as_a_draft(self):
        fake = self.catalog_copy()
        ref = self.add_fixture_protocol(fake, released=True)
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            snap = self.scan(); self.select(ref)
            payload = self.facts()
            payload['adoption'] = {'authorized': True, 'accept_draft': False, 'date': '2026-10-30', 'snapshot_id': snap['snapshot_id']}
            self.answer(payload); self.generate('adopt')
            self.assertFalse(yx.load_state(self.root)['adoption']['accept_draft'])
            self.assertTrue(yx.verify(self.root)['structure_valid'])

    def test_withdrawn_version_keeps_old_adoption_verifiable_without_new_generation(self):
        fake = self.catalog_copy()
        ref = self.add_fixture_protocol(fake, released=True)
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            snap = self.scan(); self.select(ref)
            payload = self.facts()
            payload['adoption'] = {'authorized': True, 'accept_draft': False,
                                  'date': '2026-11-02', 'snapshot_id': snap['snapshot_id']}
            self.answer(payload); self.generate('adopt')
            before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
            lifecycle.withdraw(fake, 'fixture', '1.0.0',
                               {'date': '2026-11-02', 'confirmed_by': 'fixture maintainer',
                                'reason': 'Fictional withdrawal test'})
            self.assertTrue(yx.verify(self.root)['structure_valid'])
            self.assert_error('UNSUPPORTED_AGREEMENT_WORKFLOW', self.generate, 'adopt')
            self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_prepared_publication_cannot_be_adopted_even_with_registered_adapter(self):
        fake = self.catalog_copy()
        self.add_fixture_protocol(fake)
        candidate = Catalog(fake).select('fixture', '1.0.0-rc.1')
        result = lifecycle.prepare_publication(fake, 'fixture', '1.0.0-rc.1', '1.0.0',
                    Path(self.temp.name)/'unpublished-delivery',
                    {'date': '2026-11-02', 'confirmed_by': 'fixture maintainer',
                     'basis': 'Fictional preparation test', 'candidate_sha256': candidate['_package_sha256']})
        ref = result['prepared']
        path = fake/'tooling/src/yunxia/adapters/support.json'
        support = json.loads(path.read_text())
        support['supported'].append({'agreement': ref, 'adapter': 'scope-license-v1',
                                     'output_name': 'LICENSE.fixture-1.txt'})
        path.write_bytes(yx.encoded(support))
        with patch.dict(os.environ, YUNXIA_CATALOG_ROOT=str(fake)):
            self.scan(); self.select(ref)
            self.assertEqual(yx.selected(yx.load_state(self.root))['capabilities'], ['inspect'])
            self.assert_error('UNSUPPORTED_AGREEMENT_WORKFLOW', self.generate)
            self.assertFalse((self.root/'LICENSE.txt').exists())

    def test_draft_acceptance_is_rechecked_in_saved_adoption(self):
        self.adopt()
        state = yx.load_state(self.root); state['adoption']['accept_draft'] = False
        self.write(yx.STATE, json.dumps(state))
        self.assert_error('ADOPTION_NOT_CONFIRMED', self.generate, 'adopt')
        self.assertIn('ADOPTION_NOT_CONFIRMED', {i['code'] for i in yx.verify(self.root)['issues']})

    def test_existing_corrupted_history_is_reported_without_rewriting_it(self):
        self.adopt()
        state = yx.load_state(self.root)
        generation = copy.deepcopy(state['generations']['adopt'])
        content = {p: base64.b64encode((self.root / p).read_bytes()).decode() for p in generation['files']}
        content['LICENSE.txt'] = base64.b64encode(b'old manual change archived under wrong hash').decode()
        state['history'].append({'event': 'superseded_generation', 'generation': generation, 'content_base64': content})
        self.write(yx.STATE, json.dumps(state)); before = (self.root / yx.STATE).read_bytes()
        result = yx.verify(self.root)
        self.assertFalse(result['structure_valid'])
        self.assertIn('HISTORICAL_CONTENT_MISMATCH', {i['code'] for i in result['issues']})
        self.assert_error('HISTORY_INTEGRITY_ERROR', self.generate, 'adopt')
        self.assertEqual((self.root / yx.STATE).read_bytes(), before)

    def test_missing_historical_bytes_do_not_count_as_preserved_history(self):
        self.adopt()
        state = yx.load_state(self.root)
        state['history'].append({'event': 'superseded_generation', 'generation': copy.deepcopy(state['generations']['adopt']), 'content_base64': {}})
        self.write(yx.STATE, json.dumps(state))
        self.assertIn('HISTORICAL_GENERATION_INVALID', {i['code'] for i in yx.verify(self.root)['issues']})

    def test_current_generation_requires_adapter_provenance(self):
        self.adopt()
        state=yx.load_state(self.root);state['generations']['adopt'].pop('adapter')
        self.write(yx.STATE,json.dumps(state));before=(self.root/yx.STATE).read_bytes()
        self.assert_error('INVALID_STATE',yx.verify,self.root)
        self.assertEqual((self.root/yx.STATE).read_bytes(),before)

    def test_current_state_survives_tool_version_update_without_new_authorization(self):
        self.adopt();before=(self.root/yx.STATE).read_bytes()
        with patch.object(yx,'VERSION','next-tool-test'):
            result=yx.verify(self.root)
            self.assertTrue(result['structure_valid'])
            self.assertTrue(result['adapter_update_available'])
            self.assertEqual(yx.questions(self.root)['questions'],[])
        self.assertEqual((self.root/yx.STATE).read_bytes(),before)

    def test_removed_migration_command_cannot_change_project_state(self):
        self.scan();before=(self.root/yx.STATE).read_bytes()
        result=self.cli('migrate-state')
        self.assertEqual(result.returncode,2)
        self.assertEqual((self.root/yx.STATE).read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
