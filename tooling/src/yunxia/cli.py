"""Maintainer and adopter interfaces share data contracts, not release authority."""
import argparse
import json
import os
from pathlib import Path
import sys
from . import VERSION
from .catalog import Catalog, reference
from .common import CatalogError, read_json, safe_path
from . import lifecycle as life


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--version':
        print(VERSION); return 0
    if argv and argv[0] == 'project':
        from .project.workflow import main as project_main
        return project_main(argv[1:])
    commands=['project','agreement','build','install','check','adapter','recover-maintenance']
    nested_help=any(word in commands for word in argv)
    parser=argparse.ArgumentParser(description=__doc__,add_help=not nested_help)
    parser.add_argument('command',choices=commands)
    parser.add_argument('--repository',type=Path,default=Path.cwd())
    args,tail=parser.parse_known_args(argv)
    root=args.repository.resolve()
    try:
        if args.command=='recover-maintenance':
            argparse.ArgumentParser(prog='yunxia recover-maintenance').parse_args(tail)
            result=life.recover_lock(root)
        elif args.command=='build':
            argparse.ArgumentParser(prog='yunxia build').parse_args(tail)
            from .distribution import build
            with life.locked(root):result=build(root)
        elif args.command=='install':
            from .distribution import install
            p=argparse.ArgumentParser(prog='yunxia install');p.add_argument('--destination',type=Path,default=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'skills/yunxia');p.add_argument('--update',action='store_true')
            a=p.parse_args(tail)
            with life.locked(root):result=install(root,a.destination.absolute(),a.update)
        elif args.command=='check':
            from .validation import check
            p=argparse.ArgumentParser(prog='yunxia check');p.add_argument('--test',action='store_true');a=p.parse_args(tail)
            result=check(root,a.test)
        elif args.command=='adapter':
            p=argparse.ArgumentParser(prog='yunxia adapter');p.add_argument('--agreement',required=True);p.add_argument('--version',required=True);p.add_argument('--output-name',required=True)
            a=p.parse_args(tail)
            target=safe_path(root,'tooling/src/yunxia/adapters/support.json')
            import re
            if not re.fullmatch(r'LICENSE\.[A-Za-z0-9][A-Za-z0-9._-]*\.txt',a.output_name):raise CatalogError('OUTPUT_NAME_INVALID')
            with life.locked(root):
                entry=Catalog(root).select(a.agreement,a.version)
                if entry['kind']!='license':raise CatalogError('UNSUPPORTED_AGREEMENT_WORKFLOW')
                data=read_json(target) if target.exists() else {'format_version':1,'supported':[]}
                ref=reference(entry)
                row={'agreement':ref,'adapter':'scope-license-v1','output_name':a.output_name}
                data['supported']=[x for x in data['supported'] if x['agreement']!=ref]+[row]
                life.write(target,life.encoded(data))
            result={'support_registered':ref,'adapter':'scope-license-v1','testing_required':True}
        else:
            p=argparse.ArgumentParser(prog='yunxia agreement')
            p.add_argument('action',choices=['create','inspect','review','freeze','publish','revise','recommend','retire','maintain'])
            p.add_argument('--id',required=True);p.add_argument('--name');p.add_argument('--purpose');p.add_argument('--kind',default='license')
            p.add_argument('--text',type=Path);p.add_argument('--version');p.add_argument('--input',type=Path);p.add_argument('--notes',type=Path)
            p.add_argument('--candidate');p.add_argument('--base');p.add_argument('--destination',type=Path);p.add_argument('--reason')
            p.add_argument('--state',choices=['active','paused','retired'])
            a=p.parse_args(tail)
            def run():
                if a.action=='inspect':
                    result=life.inspect(root,a.id)
                    result['fixed_versions']=[dict(reference(x),package_sha256=x.get('_package_sha256'),status=x['status']) for x in Catalog(root).entries if x['agreement_id']==a.id]
                    return result
                if a.action=='create':
                    if a.text is None:raise CatalogError('TEXT_REQUIRED')
                    return life.create(root,a.id,a.name,a.purpose,a.text.read_bytes(),a.version,a.kind)
                if a.action=='review':
                    if a.input is None:raise CatalogError('REVIEW_INPUT_REQUIRED')
                    return life.review(root,a.id,read_json(a.input))
                if a.action=='freeze':
                    if a.notes is None:raise CatalogError('RELEASE_NOTES_REQUIRED')
                    return life.freeze(root,a.id,a.version,a.notes.read_text())
                if a.action=='publish':
                    if a.destination is None or a.input is None:raise CatalogError('PUBLICATION_DECISION_REQUIRED')
                    return life.publish(root,a.id,a.candidate,a.version,a.destination,read_json(a.input))
                if a.action=='revise':return life.revise(root,a.id,a.base,a.version)
                if a.action=='recommend':return life.recommend(root,a.id,a.version,a.reason)
                return life.maintain(root,a.id,'retired' if a.action=='retire' else a.state,a.reason)
            if a.action=='inspect':result=run()
            else:
                with life.locked(root):result=run()
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 2 if result.get('valid') is False else 0
    except CatalogError as e:
        print(json.dumps({'error':e.code,'paths':e.paths},ensure_ascii=False));return 2
    except (OSError,ValueError,TypeError):
        print(json.dumps({'error':'IO_OR_FORMAT_ERROR'}));return 2


if __name__=='__main__':sys.exit(main())
