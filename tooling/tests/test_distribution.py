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
from support import copy_catalog


class Distribution(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.root=copy_catalog(self.base/'repo')
        for folder in ('skills','tooling/src'):
            shutil.copytree(ROOT/folder,self.root/folder,dirs_exist_ok=True)

    def old_skill_marker(self,target):
        (target/'SKILL.md').write_text('---\nname: yunxia\ndescription: Old test installation\n---\n')

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
        self.assertEqual(len(json.loads(p.stdout)['versions']),len(Catalog(ROOT).entries))
        self.assertFalse((package/'assets/agreements').exists())
        self.assertFalse((package/'history').exists())
        self.assertFalse((package/'scripts/yunxia/adapters/support.json').exists())
        self.assertTrue((package/'assets/catalog/adapters.json').is_file())

    def test_hashes_cover_nested_build_named_files(self):
        d.build(self.root);p=self.root/'dist/skills/yunxia'
        (p/'assets/BUILD.json').write_text('unexpected payload')
        with self.assertRaises(CatalogError) as cm:d.validate(p)
        self.assertEqual(cm.exception.code,'DISTRIBUTION_HASH_MISMATCH')

    def test_install_preserves_previous_tree_and_matches_build(self):
        d.build(self.root)
        target=self.base/'skills/yunxia';target.mkdir(parents=True);(target/'old.txt').write_text('original installation')
        self.old_skill_marker(target)
        result=d.install(self.root,target,True)
        self.assertEqual((Path(result['previous_install_backup'])/'old.txt').read_text(),'original installation')
        self.assertEqual(d.inventory(target),d.inventory(self.root/'dist/skills/yunxia'))

    def test_stale_or_tampered_build_cannot_replace_installation(self):
        d.build(self.root);target=self.base/'skills/yunxia';target.mkdir(parents=True);(target/'keep').write_text('keep')
        self.old_skill_marker(target)
        source=self.root/'skills/yunxia/SKILL.md';source.write_text(source.read_text()+'\nchanged\n')
        with self.assertRaises(CatalogError) as cm:d.install(self.root,target,True)
        self.assertEqual(cm.exception.code,'BUILD_REQUIRED')
        self.assertEqual((target/'keep').read_text(),'keep')

    def test_install_activation_failure_restores_old_tree(self):
        d.build(self.root);target=self.base/'skills/yunxia';target.mkdir(parents=True);(target/'keep').write_text('keep')
        self.old_skill_marker(target)
        real=Path.rename
        def fail(p,to):
            if p.name=='next' and '.yunxia-install-' in p.parent.name:raise OSError('fixture activation failure')
            return real(p,to)
        with patch.object(Path,'rename',fail),self.assertRaises(OSError):d.install(self.root,target,True)
        self.assertEqual((target/'keep').read_text(),'keep')

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

    def test_failed_install_quarantine_error_reports_retained_backup_and_target(self):
        d.build(self.root)
        target=self.base/'skills/yunxia';target.mkdir(parents=True)
        (target/'keep').write_text('original installation');self.old_skill_marker(target)
        original=d.inventory(target)
        real_validate=d.validate;real_tempfile=d.tempfile.mkdtemp
        def fail_validation(path):
            if path==target:raise CatalogError('DISTRIBUTION_HASH_MISMATCH')
            return real_validate(path)
        def fail_quarantine(*args,**kwargs):
            if kwargs.get('prefix')=='yunxia-failed-':raise OSError('Cannot create quarantine directory')
            return real_tempfile(*args,**kwargs)
        with patch.object(d,'validate',side_effect=fail_validation),patch.object(d.tempfile,'mkdtemp',side_effect=fail_quarantine):
            with self.assertRaises(CatalogError) as cm:d.install(self.root,target,True)
        self.assertEqual(cm.exception.code,'INSTALL_RECOVERY_REQUIRED')
        self.assertEqual(cm.exception.paths[1],str(target))
        self.assertEqual(d.inventory(Path(cm.exception.paths[0])),original)
        self.assertTrue((target/'BUILD.json').is_file())

    def test_first_install_quarantine_error_reports_target_without_fictional_backup(self):
        d.build(self.root);target=self.base/'skills/yunxia'
        real_validate=d.validate;real_tempfile=d.tempfile.mkdtemp
        def fail_validation(path):
            if path==target:raise CatalogError('DISTRIBUTION_HASH_MISMATCH')
            return real_validate(path)
        def fail_quarantine(*args,**kwargs):
            if kwargs.get('prefix')=='yunxia-failed-':raise OSError('Cannot create quarantine directory')
            return real_tempfile(*args,**kwargs)
        with patch.object(d,'validate',side_effect=fail_validation),patch.object(d.tempfile,'mkdtemp',side_effect=fail_quarantine):
            with self.assertRaises(CatalogError) as cm:d.install(self.root,target)
        self.assertEqual(cm.exception.code,'INSTALL_RECOVERY_REQUIRED')
        self.assertEqual(cm.exception.paths,[str(target)])
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

    def test_deleting_supported_snapshot_cannot_silently_prune_installation(self):
        d.build(self.root);before=d.inventory(self.root/'dist/skills/yunxia')
        (self.root/'registry/yunxia-license/0.9.2-draft.1.json').unlink()
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
            path=self.root/'registry/yunxia-license/0.9.3-draft.2.json'
            data=json.loads(path.read_text());data['version']='test-added-snapshot'
            (path.parent/'test-added-snapshot.json').write_bytes(json_bytes(data))
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
