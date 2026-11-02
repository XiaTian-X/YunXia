import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import io
from contextlib import contextmanager, redirect_stdout
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia import distribution as d
from yunxia.catalog import Catalog
from yunxia.common import CatalogError, canonical_sha256, json_bytes, sha256
from yunxia import cli, lifecycle
from support import copy_tool, git_commit


class Distribution(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.root=copy_tool(self.base/'repo')
        self.protocol=self.root/'repositories/Fixture-Agreements'

    def old_skill_marker(self,target):
        (target/'SKILL.md').write_text('---\nname: yunxia\ndescription: Old test installation\n---\n')

    def test_empty_catalog_bundle_is_discoverable_without_source_repository(self):
        (self.root/'sources.lock.json').write_bytes(json_bytes({'format_version':1,'sources':[]}))
        (self.root/'tooling/src/yunxia/adapters/support.json').write_bytes(json_bytes({'format_version':1,'supported':[]}))
        d.build(self.root)
        package=self.base/'standalone-empty'
        shutil.copytree(self.root/'dist/skills/yunxia',package)
        shutil.rmtree(self.root)
        env=dict(os.environ,PATH='')
        env.pop('YUNXIA_CATALOG_ROOT',None);env.pop('YUNXIA_ADAPTERS_PATH',None)
        result=subprocess.run([sys.executable,'-B',str(package/'scripts/yunxia.py'),'project','catalog'],
                              cwd=self.base,capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['versions'],[])

    def test_build_is_reproducible_and_does_not_edit_source(self):
        before=d.source_signature(self.root)
        first=d.build(self.root);second=d.build(self.root)
        self.assertTrue(first['changed']);self.assertFalse(second['changed'])
        self.assertEqual(d.source_signature(self.root),before)
        d.validate(self.root/'dist/skills/yunxia')

    def test_built_skill_works_outside_repository_and_contains_no_old_tree(self):
        d.build(self.root)
        package=self.base/'standalone';shutil.copytree(self.root/'dist/skills/yunxia',package)
        shutil.rmtree(self.root)
        env=dict(os.environ,PATH='');env.pop('YUNXIA_CATALOG_ROOT',None)
        p=subprocess.run([sys.executable,'-B',str(package/'scripts/yunxia.py'),'project','catalog'],cwd=self.base,capture_output=True,text=True,env=env)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(len(json.loads(p.stdout)['versions']),3)
        self.assertFalse((package/'assets/agreements').exists())
        self.assertFalse((package/'history').exists())
        self.assertFalse((package/'scripts/yunxia/adapters/support.json').exists())
        self.assertTrue((package/'assets/catalog/adapters.json').is_file())

    def test_hashes_cover_nested_build_named_files(self):
        d.build(self.root);p=self.root/'dist/skills/yunxia'
        (p/'assets/BUILD.json').write_text('unexpected payload')
        with self.assertRaises(CatalogError) as cm:d.validate(p)
        self.assertEqual(cm.exception.code,'DISTRIBUTION_HASH_MISMATCH')

    def test_install_discards_completed_transaction_and_matches_build(self):
        d.build(self.root)
        target=self.base/'skills/yunxia';target.mkdir(parents=True);(target/'old.txt').write_text('original installation')
        self.old_skill_marker(target)
        result=d.install(self.root,target,True)
        self.assertTrue(result['transaction_cleaned'])
        self.assertFalse((self.base/'skill-backups').exists())
        self.assertEqual(list(target.parent.glob('.yunxia-install-*')), [])
        self.assertEqual(d.inventory(target),d.inventory(self.root/'dist/skills/yunxia'))

    def test_stale_or_tampered_build_cannot_replace_installation(self):
        d.build(self.root);target=self.base/'skills/yunxia';target.mkdir(parents=True);(target/'keep').write_text('keep')
        self.old_skill_marker(target)
        source=self.root/'skills/yunxia/SKILL.md';source.write_text(source.read_text()+'\nchanged\n')
        with self.assertRaises(CatalogError) as cm:d.install(self.root,target,True)
        self.assertEqual(cm.exception.code,'BUILD_REQUIRED')
        self.assertEqual((target/'keep').read_text(),'keep')

    def test_install_preserves_target_edits_during_staging(self):
        d.build(self.root)
        for change in ('added','content','mode','manifest'):
            with self.subTest(change=change):
                target=self.base/change/'yunxia';target.mkdir(parents=True)
                self.old_skill_marker(target);(target/'keep').write_bytes(b'original')
                (target/'keep').chmod(0o600)
                original=d.write_files
                def edited(destination,files):
                    original(destination,files)
                    if destination.name=='next':
                        if change=='added':(target/'human-edit').write_bytes(b'keep human edit')
                        elif change=='content':(target/'keep').write_bytes(b'changed by human')
                        elif change=='mode':(target/'keep').chmod(0o640)
                        else:(target/'BUILD.json').write_text('new human manifest')
                with patch.object(d,'write_files',side_effect=edited),self.assertRaises(CatalogError) as caught:
                    d.install(self.root,target,True)
                self.assertEqual(caught.exception.code,'INSTALL_TARGET_CHANGED')
                self.assertIn('Old test installation',(target/'SKILL.md').read_text())
                if change=='added':self.assertEqual((target/'human-edit').read_bytes(),b'keep human edit')
                elif change=='content':self.assertEqual((target/'keep').read_bytes(),b'changed by human')
                elif change=='mode':self.assertEqual((target/'keep').stat().st_mode & 0o777,0o640)
                else:self.assertEqual((target/'BUILD.json').read_text(),'new human manifest')
                self.assertEqual(list(target.parent.glob('.yunxia-install-*')),[])

    def test_install_rechecks_moved_original_and_restores_concurrent_edit(self):
        d.build(self.root)
        target=self.base/'skills/yunxia';target.mkdir(parents=True);self.old_skill_marker(target)
        original=Path.rename
        def edited(path,destination):
            result=original(path,destination)
            if path==target and Path(destination).name=='previous':
                (Path(destination)/'human-edit').write_bytes(b'preserve edit after rename')
            return result
        with patch.object(Path,'rename',edited),self.assertRaises(CatalogError) as caught:
            d.install(self.root,target,True)
        self.assertEqual(caught.exception.code,'INSTALL_TARGET_CHANGED')
        self.assertEqual((target/'human-edit').read_bytes(),b'preserve edit after rename')
        self.assertIn('Old test installation',(target/'SKILL.md').read_text())
        self.assertEqual(list(target.parent.glob('.yunxia-install-*')),[])

    def test_install_preserves_late_edits_to_previous_tree(self):
        d.build(self.root)
        target=self.base/'skills/yunxia';target.mkdir(parents=True);self.old_skill_marker(target)
        original=d.validate
        def edited(path):
            result=original(path)
            if path==target:
                previous=next(target.parent.glob('.yunxia-install-*/previous'))
                (previous/'human-edit').write_bytes(b'preserve late edit')
            return result
        with patch.object(d,'validate',side_effect=edited),self.assertRaises(CatalogError) as caught:
            d.install(self.root,target,True)
        self.assertEqual(caught.exception.code,'INSTALL_TARGET_CHANGED')
        self.assertEqual((target/'human-edit').read_bytes(),b'preserve late edit')
        self.assertIn('Old test installation',(target/'SKILL.md').read_text())
        self.assertTrue(Path(caught.exception.paths[1]).is_dir())

    def test_install_does_not_read_symlinked_external_target_files(self):
        d.build(self.root)
        target=self.base/'skills/yunxia';target.mkdir(parents=True);self.old_skill_marker(target)
        external=self.base/'private-external';external.mkdir()
        private=external/'private.txt';private.write_bytes(b'not part of this installation')
        (target/'external').symlink_to(external,target_is_directory=True)
        original=Path.read_bytes
        def protected(path):
            if path.resolve()==private:raise AssertionError('External target content must not be read')
            return original(path)
        with patch.object(Path,'read_bytes',protected):d.install(self.root,target,True)
        self.assertEqual(private.read_bytes(),b'not part of this installation')

    def test_install_rejects_target_file_replaced_by_symlink_during_snapshot(self):
        d.build(self.root)
        target=self.base/'skills/yunxia';target.mkdir(parents=True);self.old_skill_marker(target)
        current=target/'keep';current.write_bytes(b'original')
        external=self.base/'private-external';external.write_bytes(b'private external content')
        original=os.open;changed=False
        def swapped(path,flags,*args,**kwargs):
            nonlocal changed
            if Path(path)==current and not changed:
                changed=True;current.unlink();current.symlink_to(external)
            return original(path,flags,*args,**kwargs)
        with patch.object(os,'open',swapped),self.assertRaises(CatalogError) as caught:
            d.install(self.root,target,True)
        self.assertEqual(caught.exception.code,'INSTALL_TARGET_CHANGED')
        self.assertTrue(current.is_symlink())
        self.assertEqual(external.read_bytes(),b'private external content')

    def test_different_tool_checkouts_cannot_install_same_target_concurrently(self):
        d.build(self.root)
        other=copy_tool(self.base/'other-tool');d.build(other)
        target=self.base/'skills/yunxia';target.mkdir(parents=True);self.old_skill_marker(target)
        script="""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from yunxia import distribution
original = distribution.write_files
def paused(destination, files):
    original(destination, files)
    if destination.name == 'next':
        print('ready', flush=True)
        sys.stdin.read()
distribution.write_files = paused
distribution.install(Path(sys.argv[2]), Path(sys.argv[3]), True)
"""
        process=subprocess.Popen([sys.executable,'-B','-c',script,str(ROOT/'tooling/src'),str(self.root),str(target)],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'ready')
            with self.assertRaises(CatalogError) as caught:d.install(other,target,True)
            self.assertEqual(caught.exception.code,'INSTALL_LOCKED')
            self.assertIn('Old test installation',(target/'SKILL.md').read_text())
            stdout,stderr=process.communicate(timeout=20)
            self.assertEqual(process.returncode,0,stdout+stderr)
            self.assertEqual(d.inventory(target),d.inventory(self.root/'dist/skills/yunxia'))
        finally:
            if process.poll() is None:process.kill()
            process.communicate(timeout=10)

    def test_install_activation_failure_restores_old_tree(self):
        d.build(self.root);target=self.base/'skills/yunxia';target.mkdir(parents=True);(target/'keep').write_text('keep')
        self.old_skill_marker(target)
        real=Path.rename
        def fail(p,to):
            if p.name=='next' and '.yunxia-install-' in p.parent.name:raise OSError('fixture activation failure')
            return real(p,to)
        with patch.object(Path,'rename',fail),self.assertRaises(OSError):d.install(self.root,target,True)
        self.assertEqual((target/'keep').read_text(),'keep')

    def test_interrupt_after_moving_old_install_restores_original(self):
        d.build(self.root);target=self.base/'skills/yunxia';target.mkdir(parents=True)
        self.old_skill_marker(target);(target/'keep').write_bytes(b'original installation')
        real=Path.rename
        def interrupt_after_effect(path,to):
            result=real(path,to)
            if path==target and Path(to).name=='previous':raise KeyboardInterrupt()
            return result
        with patch.object(Path,'rename',interrupt_after_effect),self.assertRaises(KeyboardInterrupt):
            d.install(self.root,target,True)
        self.assertEqual((target/'keep').read_bytes(),b'original installation')
        self.assertEqual(list(target.parent.glob('.yunxia-install-*')),[])

    def test_interrupt_after_install_activation_restores_original(self):
        d.build(self.root);target=self.base/'skills/yunxia';target.mkdir(parents=True)
        self.old_skill_marker(target);(target/'keep').write_bytes(b'original installation')
        real=Path.rename
        def interrupt_after_effect(path,to):
            result=real(path,to)
            if path.name=='next' and Path(to)==target:raise KeyboardInterrupt()
            return result
        with patch.object(Path,'rename',interrupt_after_effect),self.assertRaises(CatalogError) as caught:
            d.install(self.root,target,True)
        self.assertEqual(caught.exception.code,'INSTALL_VERIFICATION_FAILED')
        self.assertEqual((target/'keep').read_bytes(),b'original installation')
        self.assertTrue(Path(caught.exception.paths[0]).is_dir())

    def test_post_install_validation_failure_restores_old_tree(self):
        d.build(self.root);target=self.base/'skills/yunxia';target.mkdir(parents=True);(target/'keep').write_text('keep')
        self.old_skill_marker(target)
        real=d.validate
        def fail(p):
            if p==target:raise CatalogError('DISTRIBUTION_HASH_MISMATCH')
            return real(p)
        with patch.object(d,'validate',side_effect=fail),self.assertRaises(CatalogError) as cm:d.install(self.root,target,True)
        self.assertEqual(cm.exception.code,'INSTALL_VERIFICATION_FAILED')
        self.assertEqual((target/'keep').read_text(),'keep')
        self.assertTrue(Path(cm.exception.paths[0]).exists())

    def test_install_restore_failure_keeps_original_transaction_tree(self):
        d.build(self.root);target=self.base/'skills/yunxia';target.mkdir(parents=True)
        self.old_skill_marker(target);(target/'keep').write_text('original')
        real=Path.rename
        def fail(path,to):
            if path.name in ('next','previous') and '.yunxia-install-' in path.parent.name:
                raise OSError('activation and restoration fail')
            return real(path,to)
        with patch.object(Path,'rename',fail),self.assertRaises(CatalogError) as caught:
            d.install(self.root,target,True)
        self.assertEqual(caught.exception.code,'INSTALL_RECOVERY_REQUIRED')
        previous=Path(caught.exception.paths[0])
        self.assertEqual((previous/'keep').read_text(),'original')
        self.assertEqual(json.loads((previous.parent/'transaction.json').read_text())['destination'],str(target))
        self.assertFalse((self.base/'skill-backups').exists())

    def test_process_interruption_preserves_installation_recovery_materials(self):
        d.build(self.root)
        script="""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from yunxia import distribution
original = Path.rename
def interrupted(path, target):
    result = original(path, target)
    if (sys.argv[4] == 'previous' and Path(target).name == 'previous') or (sys.argv[4] == 'activated' and path.name == 'next'):
        print('interrupted', flush=True)
        sys.stdin.read()
    return result
Path.rename = interrupted
distribution.install(Path(sys.argv[2]), Path(sys.argv[3]), True)
"""
        for phase in ('previous','activated'):
            with self.subTest(phase=phase):
                target=self.base/phase/'yunxia';target.mkdir(parents=True)
                self.old_skill_marker(target);(target/'keep').write_bytes(b'original private installation')
                process=subprocess.Popen([sys.executable,'-B','-c',script,str(ROOT/'tooling/src'),str(self.root),str(target),phase],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    self.assertEqual(process.stdout.readline().strip(),'interrupted')
                    process.kill();process.communicate(timeout=10)
                    transactions=list(target.parent.glob('.yunxia-install-*'))
                    self.assertEqual(len(transactions),1)
                    work=transactions[0]
                    self.assertEqual((work/'previous/keep').read_bytes(),b'original private installation')
                    self.assertEqual(json.loads((work/'transaction.json').read_text())['destination'],str(target))
                    self.assertEqual(target.exists(),phase=='activated')
                    self.assertFalse((self.base/'skill-backups').exists())
                finally:
                    if process.poll() is None:process.kill()
                    process.communicate(timeout=10)

    def test_failed_install_quarantine_error_reports_retained_backup_and_target(self):
        d.build(self.root)
        target=self.base/'skills/yunxia';target.mkdir(parents=True)
        (target/'keep').write_text('original installation');self.old_skill_marker(target)
        original=d.inventory(target)
        real_validate=d.validate;real_rename=Path.rename
        def fail_validation(path):
            if path==target:raise CatalogError('DISTRIBUTION_HASH_MISMATCH')
            return real_validate(path)
        def fail_quarantine(path,target_path):
            if Path(target_path).name=='failed':raise OSError('Cannot quarantine failed installation')
            return real_rename(path,target_path)
        with patch.object(d,'validate',side_effect=fail_validation),patch.object(Path,'rename',fail_quarantine):
            with self.assertRaises(CatalogError) as cm:d.install(self.root,target,True)
        self.assertEqual(cm.exception.code,'INSTALL_RECOVERY_REQUIRED')
        self.assertEqual(cm.exception.paths[1],str(target))
        self.assertEqual(d.inventory(Path(cm.exception.paths[0])),original)
        self.assertTrue((target/'BUILD.json').is_file())

    def test_first_install_quarantine_error_reports_target_without_fictional_backup(self):
        d.build(self.root);target=self.base/'skills/yunxia'
        real_validate=d.validate;real_rename=Path.rename
        def fail_validation(path):
            if path==target:raise CatalogError('DISTRIBUTION_HASH_MISMATCH')
            return real_validate(path)
        def fail_quarantine(path,target_path):
            if Path(target_path).name=='failed':raise OSError('Cannot quarantine failed installation')
            return real_rename(path,target_path)
        with patch.object(d,'validate',side_effect=fail_validation),patch.object(Path,'rename',fail_quarantine):
            with self.assertRaises(CatalogError) as cm:d.install(self.root,target)
        self.assertEqual(cm.exception.code,'INSTALL_RECOVERY_REQUIRED')
        self.assertEqual(cm.exception.paths[0],str(target))
        self.assertTrue(Path(cm.exception.paths[1]).is_dir())
        self.assertTrue((target/'BUILD.json').is_file())

    def test_build_rollback_failure_preserves_unique_backup(self):
        d.build(self.root);target=self.root/'dist/skills/yunxia'
        old=d.inventory(target)
        source=self.root/'skills/yunxia/SKILL.md';source.write_text(source.read_text()+'\nchanged\n')
        real=Path.rename
        def fail(p,to):
            if p.name in ('next','previous') and '.yunxia-build-' in p.parent.name:raise OSError('fixture rename failure')
            return real(p,to)
        with patch.object(Path,'rename',fail),self.assertRaises(CatalogError) as cm:d.build(self.root)
        self.assertEqual(cm.exception.code,'BUILD_RECOVERY_REQUIRED')
        self.assertEqual(d.inventory(Path(cm.exception.paths[0])),old)

    def test_interrupt_after_moving_old_build_restores_original(self):
        d.build(self.root);target=self.root/'dist/skills/yunxia';original=d.inventory(target)
        path=self.root/'skills/yunxia/SKILL.md';path.write_bytes(path.read_bytes()+b'\nnew draft\n')
        real=Path.rename
        def interrupt_after_effect(path,to):
            result=real(path,to)
            if path==target and Path(to).name=='previous':raise KeyboardInterrupt()
            return result
        with patch.object(Path,'rename',interrupt_after_effect),self.assertRaises(KeyboardInterrupt):d.build(self.root)
        self.assertEqual(d.inventory(target),original)

    def test_interrupt_after_build_activation_retains_previous_tree(self):
        d.build(self.root);target=self.root/'dist/skills/yunxia';original=d.inventory(target)
        path=self.root/'skills/yunxia/SKILL.md';path.write_bytes(path.read_bytes()+b'\nnew draft\n')
        real=Path.rename
        def interrupt_after_effect(path,to):
            result=real(path,to)
            if path.name=='next' and Path(to)==target:raise KeyboardInterrupt()
            return result
        with patch.object(Path,'rename',interrupt_after_effect),self.assertRaises(CatalogError) as caught:d.build(self.root)
        self.assertEqual(caught.exception.code,'BUILD_RECOVERY_REQUIRED')
        self.assertEqual(d.inventory(Path(caught.exception.paths[0])),original)
        self.assertTrue(target.exists())

    def test_deleting_supported_snapshot_cannot_silently_prune_installation(self):
        d.build(self.root);before=d.inventory(self.root/'dist/skills/yunxia')
        (self.protocol/'registry/yunxia-license/0.9.2-draft.1.json').unlink()
        commit=git_commit(self.protocol,'Delete a fixed registry record')
        lock=self.root/'sources.lock.json';data=json.loads(lock.read_text())
        data['sources'][0]['commit']=commit;lock.write_bytes(json_bytes(data))
        with self.assertRaises(CatalogError):d.build(self.root)
        self.assertEqual(d.inventory(self.root/'dist/skills/yunxia'),before)

    def test_payload_symlink_is_rejected(self):
        d.build(self.root);package=self.root/'dist/skills/yunxia'
        (package/'link').symlink_to(self.root/'README.md')
        with self.assertRaises(CatalogError) as cm:d.validate(package)
        self.assertEqual(cm.exception.code,'DISTRIBUTION_SYMLINK')

    def test_install_cannot_replace_source_ancestor_or_source_child(self):
        d.build(self.root)
        before=d.source_signature(self.root)
        for target in (self.root,self.root.parent,self.root/'skills/yunxia',self.root/'unused-install'):
            with self.subTest(target=target),self.assertRaises(CatalogError) as cm:
                d.install(self.root,target,True)
            self.assertEqual(cm.exception.code,'INSTALL_SOURCE_OVERLAP')
        self.assertEqual(d.source_signature(self.root),before)

    def test_update_refuses_unrelated_directories_and_other_skills(self):
        d.build(self.root)
        target=self.base/'unrelated';target.mkdir();(target/'important.txt').write_text('keep')
        for marker in (None,'---\nname: other-skill\ndescription: fixture\n---\n'):
            if marker:(target/'SKILL.md').write_text(marker)
            with self.assertRaises(CatalogError) as cm:d.install(self.root,target,True)
            self.assertEqual(cm.exception.code,'INSTALL_TARGET_NOT_SKILL')
            self.assertEqual((target/'important.txt').read_text(),'keep')

    def test_invalid_distribution_format_is_rejected(self):
        d.build(self.root);package=self.root/'dist/skills/yunxia';p=package/'BUILD.json'
        original=json.loads(p.read_text())
        for value in (True,999,None):
            data=dict(original,format_version=value);p.write_text(json.dumps(data))
            with self.assertRaises(CatalogError) as cm:d.validate(package)
            self.assertEqual(cm.exception.code,'DISTRIBUTION_FORMAT')

    def test_registry_change_during_build_does_not_activate_partial_catalog(self):
        d.build(self.root);before=d.inventory(self.root/'dist/skills/yunxia')
        real=d.write_files
        def changed(destination,files):
            real(destination,files)
            path=self.protocol/'README.md';path.write_text('Concurrent protocol catalog documentation update')
            commit=git_commit(self.protocol,'Concurrent directory snapshot')
            lock=self.root/'sources.lock.json';data=json.loads(lock.read_text())
            data['sources'][0]['commit']=commit;lock.write_bytes(json_bytes(data))
        with patch.object(d,'write_files',side_effect=changed),self.assertRaises(CatalogError) as cm:
            d.build(self.root)
        self.assertEqual(cm.exception.code,'BUILD_SOURCE_CHANGED')
        self.assertEqual(d.inventory(self.root/'dist/skills/yunxia'),before)

    def test_restored_source_does_not_hide_mixed_stage_bytes(self):
        d.build(self.root);before=d.inventory(self.root/'dist/skills/yunxia')
        source=self.root/'skills/yunxia/SKILL.md';original=source.read_bytes()
        real=d.write_files
        def mixed(destination,files):
            source.write_bytes(original+b'\ntransient change\n')
            try:
                mixed_files=dict(files);mixed_files['SKILL.md']=source.read_bytes()
                real(destination,mixed_files)
            finally:source.write_bytes(original)
        with patch.object(d,'write_files',side_effect=mixed),self.assertRaises(CatalogError) as cm:
            d.build(self.root)
        self.assertEqual(cm.exception.code,'BUILD_OUTPUT_MISMATCH')
        self.assertEqual(source.read_bytes(),original)
        self.assertEqual(d.inventory(self.root/'dist/skills/yunxia'),before)

    def test_self_consistent_tampered_package_cannot_claim_current_source(self):
        d.build(self.root);package=self.root/'dist/skills/yunxia'
        path=package/'SKILL.md';path.write_bytes(path.read_bytes()+b'\nunexpected instruction\n')
        manifest=json.loads((package/'BUILD.json').read_text())
        manifest['files']['SKILL.md']=sha256(path.read_bytes())
        (package/'BUILD.json').write_bytes(json_bytes(manifest))
        with self.assertRaises(CatalogError) as cm:d.validate(package)
        self.assertEqual(cm.exception.code,'DISTRIBUTION_SOURCE_MISMATCH')
        manifest['source_sha256']=canonical_sha256({'inputs':manifest['inputs'],'outputs':manifest['files']})
        (package/'BUILD.json').write_bytes(json_bytes(manifest))
        d.validate(package)
        target=self.base/'skills/yunxia';target.mkdir(parents=True);self.old_skill_marker(target)
        with self.assertRaises(CatalogError) as cm:d.install(self.root,target,True)
        self.assertEqual(cm.exception.code,'BUILD_REQUIRED')
        self.assertIn('Old test installation',(target/'SKILL.md').read_text())

    def test_adapter_reads_registry_after_acquiring_maintenance_lock(self):
        registry=self.root/'tooling/src/yunxia/adapters/support.json'
        real=lifecycle.locked
        @contextmanager
        def completed_other_writer(root):
            data=json.loads(registry.read_text())
            data['supported'][0]['output_name']='LICENSE.concurrent-older.txt'
            registry.write_text(json.dumps(data))
            with real(root):yield
        with patch.object(lifecycle,'locked',completed_other_writer),redirect_stdout(io.StringIO()):
            code=cli.main(['adapter','--repository',str(self.root),'--agreement','yunxia-license','--version','0.9.3-draft.2','--output-name','LICENSE.updated-current.txt'])
        self.assertEqual(code,0)
        data=json.loads(registry.read_text())
        self.assertEqual(data['supported'][0]['output_name'],'LICENSE.concurrent-older.txt')
        self.assertEqual(data['supported'][-1]['output_name'],'LICENSE.updated-current.txt')

    def test_source_change_while_installing_does_not_replace_previous_install(self):
        d.build(self.root);target=self.base/'skills/yunxia';target.mkdir(parents=True)
        self.old_skill_marker(target);(target/'keep').write_text('keep')
        real=d.write_files
        def changed(destination,files):
            real(destination,files)
            if destination.name=='next':
                p=self.root/'skills/yunxia/SKILL.md';p.write_text(p.read_text()+'\nconcurrent source change\n')
        with patch.object(d,'write_files',side_effect=changed),self.assertRaises(CatalogError) as cm:
            d.install(self.root,target,True)
        self.assertEqual(cm.exception.code,'INSTALL_SOURCE_CHANGED')
        self.assertEqual((target/'keep').read_text(),'keep')


if __name__=='__main__':unittest.main()
