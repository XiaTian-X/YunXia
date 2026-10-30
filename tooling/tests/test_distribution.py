import json
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
from yunxia.catalog import Catalog,CatalogError
from yunxia import cli, lifecycle


class Distribution(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.root=self.base/'repo';self.root.mkdir()
        for folder in ('agreements','releases','skills','tooling/src'):
            shutil.copytree(ROOT/folder,self.root/folder)

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
        p=subprocess.run([sys.executable,'-B',str(package/'scripts/yunxia.py'),'project','catalog'],cwd=self.base,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(len(json.loads(p.stdout)['versions']),len(Catalog(ROOT).entries))
        self.assertFalse((package/'assets/agreements').exists())
        self.assertFalse((package/'history').exists())

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
        shutil.rmtree(self.root/'releases/yunxia-license/0.9.2-draft.1')
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

    def test_catalog_loaded_before_signature_cannot_omit_a_new_snapshot(self):
        real=d.source_signature;calls=[]
        def changed(root):
            if not calls:
                calls.append(True)
                source=root/'releases/yunxia-license/0.9.3-draft.2'
                new=root/'releases/yunxia-license/test-added-snapshot'
                shutil.copytree(source,new)
                p=new/'release.json';data=json.loads(p.read_text())
                data.update(version='test-added-snapshot',legacy_references=[])
                p.write_text(json.dumps(data))
            return real(root)
        with patch.object(d,'source_signature',side_effect=changed):
            d.build(self.root)
        package=self.root/'dist/skills/yunxia/assets/catalog'
        self.assertEqual(Catalog(package).select('yunxia-license','test-added-snapshot')['version'],'test-added-snapshot')

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
        real=shutil.copytree
        def changed(*args,**kwargs):
            result=real(*args,**kwargs)
            if Path(args[1]).name=='next':
                p=self.root/'skills/yunxia/SKILL.md';p.write_text(p.read_text()+'\nconcurrent source change\n')
            return result
        with patch.object(shutil,'copytree',side_effect=changed),self.assertRaises(CatalogError) as cm:
            d.install(self.root,target,True)
        self.assertEqual(cm.exception.code,'INSTALL_SOURCE_CHANGED')
        self.assertEqual((target/'keep').read_text(),'keep')


if __name__=='__main__':unittest.main()
