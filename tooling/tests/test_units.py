import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling/src'))
from yunxia.project import workflow as w
from yunxia.catalog import Catalog, reference


class Units(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'project';self.root.mkdir()
        for path,content in [('src/a.py','print(1)\n'),('docs/a.md','A document\n')]:
            p=self.root/path;p.parent.mkdir();p.write_text(content)
        w.configure_unit('main');self.addCleanup(w.configure_unit,'main')
        p=patch.object(w,'catalog',lambda:Catalog(ROOT));p.start();self.addCleanup(p.stop)

    def adopt(self,unit,scope,path,version='0.9.3-draft.2'):
        with w.using_unit(unit),w.lock(self.root):
            w.set_scope(self.root,[scope]);snapshot=w.scan(self.root)
            ref=reference(Catalog(ROOT).select('yunxia-license',version))
            w.select_agreement(self.root,'yunxia-license',version)
            answer={'confirmed_by':'fixture','basis':'private evidence','identity':{'project_name':'fixture','licensor':'fixture author','role':'author'},'delivery':{'audience':'users','description':'source directory'},'assets':[{'paths':[path] if isinstance(path,str) else path,'origin':'human','disposition':'selected','basis':'fixture source'}],'adoption':{'authorized':True,'accept_draft':True,'date':'2026-10-25','snapshot_id':snapshot['snapshot_id'],'agreement':ref}}
            w.answer(self.root,answer);w.generate(self.root,'adopt',True)

    def test_two_units_have_separate_scopes_outputs_and_version_bindings(self):
        self.adopt('code','src','src/a.py')
        self.adopt('docs','docs','docs/a.md','0.9.2-draft.1')
        self.assertTrue((self.root/'licenses/code/PROJECT-RIGHTS.md').exists())
        self.assertTrue((self.root/'licenses/docs/PROJECT-RIGHTS.md').exists())
        self.assertNotEqual((self.root/'licenses/code/LICENSE.txt').read_bytes(),(self.root/'licenses/docs/LICENSE.txt').read_bytes())
        self.assertTrue(w.verify_all(self.root)['structure_valid'])

    def test_other_scope_changes_do_not_invalidate_untouched_unit(self):
        self.adopt('code','src','src/a.py');self.adopt('docs','docs','docs/a.md')
        original=(self.root/'.yunxia/units/code/state.json').read_bytes()
        (self.root/'docs/a.md').write_text('changed')
        with w.using_unit('code'):
            self.assertTrue(w.verify(self.root)['structure_valid'])
            w.scan(self.root);w.generate(self.root,'adopt',True)
        self.assertEqual((self.root/'.yunxia/units/code/state.json').read_bytes(),original)
        with w.using_unit('docs'):
            with self.assertRaises(w.WorkflowError) as cm:w.verify(self.root)
            self.assertEqual(cm.exception.code,'SCAN_STALE')

    def test_overlapping_selected_content_is_rejected_without_state_write(self):
        self.adopt('code','src','src/a.py')
        with self.assertRaises(w.WorkflowError) as cm:self.adopt('other','src','src/a.py')
        self.assertEqual(cm.exception.code,'ADOPTION_SCOPE_OVERLAP')
        self.assertFalse((self.root/'licenses/other/PROJECT-RIGHTS.md').exists())
        with w.using_unit('other'):self.assertFalse(w.load_state(self.root)['answers'])

    def test_scope_must_be_explicit_and_private_paths_are_rejected(self):
        with w.using_unit('code'):
            with self.assertRaises(w.WorkflowError) as cm:w.scan(self.root)
            self.assertEqual(cm.exception.code,'UNIT_SCOPE_REQUIRED')
            for path in ['../outside','.yunxia','src/*']:
                with self.assertRaises(w.WorkflowError):w.set_scope(self.root,[path])

    def test_combined_delivery_checks_all_units_and_private_state(self):
        self.adopt('code','src','src/a.py');self.adopt('docs','docs','docs/a.md')
        dest=Path(self.temp.name)/'delivery';shutil.copytree(self.root,dest,ignore=shutil.ignore_patterns('.yunxia'))
        self.assertTrue(w.verify_all(self.root,dest)['structure_valid'])
        (dest/'.yunxia').mkdir();(dest/'.yunxia/state.json').write_text('private')
        result=w.verify_all(self.root,dest)
        self.assertIn('PRIVATE_STATE_INCLUDED',{x['code'] for x in result['issues']})

    def test_other_units_notice_is_not_silently_licensed(self):
        self.adopt('code','src','src/a.py')
        with w.using_unit('docs'):
            w.set_scope(self.root,['docs']);result=w.scan(self.root)
            self.assertEqual(set(result['files']),{'docs/a.md'})

    def test_named_unit_manual_edits_and_recovery_are_protected(self):
        self.adopt('code','src','src/a.py')
        p=self.root/'licenses/code/PROJECT-RIGHTS.md';p.write_text('manual')
        with w.using_unit('code'):
            with self.assertRaises(w.WorkflowError) as cm:w.generate(self.root,'adopt',True)
            self.assertEqual(cm.exception.code,'MANUAL_EDIT_CONFLICT')
        self.assertEqual(p.read_text(),'manual')

    def test_main_keeps_existing_root_material_layout(self):
        self.adopt('main','src','src/a.py')
        self.assertTrue((self.root/'PROJECT-RIGHTS.md').exists())
        self.assertTrue((self.root/'.yunxia/state.json').exists())
        self.assertTrue(w.verify(self.root)['structure_valid'])

    def test_scope_transfer_keeps_history_without_reserving_removed_paths(self):
        (self.root/'src/b.py').write_text('print(2)\n')
        self.adopt('code','src',['src/a.py','src/b.py'])
        self.adopt('code','src/a.py','src/a.py')
        with w.using_unit('code'):
            state=w.load_state(self.root)
            self.assertEqual(state['answers']['src/b.py']['disposition'],'selected')
            self.assertEqual(w.active_answer(state,'src/b.py'),{})
        self.adopt('moved','src/b.py','src/b.py')
        self.assertTrue(w.verify_all(self.root)['structure_valid'])

    def test_transfer_cannot_make_outdated_previous_public_material_pass(self):
        (self.root/'src/b.py').write_text('print(2)\n')
        self.adopt('code','src',['src/a.py','src/b.py'])
        with w.using_unit('code'):
            w.set_scope(self.root,['src/a.py']);w.scan(self.root)
        self.adopt('moved','src/b.py','src/b.py')
        result=w.verify_all(self.root)
        self.assertFalse(result['structure_valid'])
        self.assertIn('GENERATION_STALE',{x['code'] for x in result['issues']})
        self.adopt('code','src/a.py','src/a.py')
        self.assertTrue(w.verify_all(self.root)['structure_valid'])

    def test_planned_units_are_reported_separately_from_generated_adoptions(self):
        self.adopt('code','src','src/a.py')
        with w.using_unit('planned'):
            w.set_scope(self.root,['docs'])
        for phase in ('configured','scanned','draft-generated'):
            with self.subTest(phase=phase):
                with w.using_unit('planned'):
                    if phase=='scanned':w.scan(self.root)
                    if phase=='draft-generated':
                        w.select_agreement(self.root,'yunxia-license','0.9.3-draft.2')
                        w.generate(self.root,'draft',True)
                result=w.verify_all(self.root)
                self.assertTrue(result['structure_valid'])
                self.assertFalse(result['all_units_ready'])
                self.assertEqual(result['pending_units'][0]['id'],'planned')
                self.assertEqual(set(result['units']),{'code'})

    def test_pending_unit_does_not_hide_unadopted_delivery_files(self):
        self.adopt('code','src','src/a.py')
        with w.using_unit('planned'):
            w.set_scope(self.root,['docs']);w.scan(self.root)
        dest=Path(self.temp.name)/'delivery'
        shutil.copytree(self.root,dest,ignore=shutil.ignore_patterns('.yunxia'))
        result=w.verify_all(self.root,dest)
        self.assertFalse(result['structure_valid'])
        self.assertIn({'code':'UNSCANNED_ARTIFACT_FILE','paths':['docs/a.md']},result['issues'])

    def test_all_planned_is_not_a_vacuous_success(self):
        with w.using_unit('planned'):w.set_scope(self.root,['docs'])
        result=w.verify_all(self.root)
        self.assertFalse(result['structure_valid'])
        self.assertIn('NO_ADOPTED_GENERATIONS',{x['code'] for x in result['issues']})

    def test_aggregate_reports_stale_unit_and_still_checks_other_units(self):
        self.adopt('code','src','src/a.py');self.adopt('docs','docs','docs/a.md')
        (self.root/'src/a.py').write_text('changed')
        result=w.verify_all(self.root)
        self.assertFalse(result['structure_valid'])
        self.assertEqual(set(result['units']),{'code','docs'})
        self.assertTrue(result['units']['docs']['structure_valid'])
        self.assertIn('SCAN_STALE',{x['code'] for x in result['issues']})

    def test_named_unit_interrupted_write_restores_files_and_state(self):
        self.adopt('code','src','src/a.py')
        with w.using_unit('code'):
            before_state=(self.root/w.STATE).read_bytes()
            output='licenses/code/PROJECT-RIGHTS.md'
            before_output=(self.root/output).read_bytes()
            real=w.atomic
            def interrupted(path,content,private=False):
                if path==self.root/w.STATE:raise KeyboardInterrupt()
                return real(path,content,private)
            with w.lock(self.root),patch.object(w,'atomic',side_effect=interrupted),self.assertRaises(KeyboardInterrupt):
                w.transaction(self.root,{output:b'partial new content',w.STATE:b'partial state'})
            self.assertTrue((self.root/w.JOURNAL).exists())
            with self.assertRaises(w.WorkflowError) as cm:w.load_state(self.root)
            self.assertEqual(cm.exception.code,'RECOVERY_REQUIRED')
            with w.lock(self.root,recovery=True):self.assertTrue(w.restore(self.root))
            self.assertEqual((self.root/w.STATE).read_bytes(),before_state)
            self.assertEqual((self.root/output).read_bytes(),before_output)
            self.assertTrue(w.verify(self.root)['structure_valid'])


if __name__=='__main__':unittest.main()
