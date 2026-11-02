"""Read-only validation for distinct tool and protocol repositories."""
import re
import subprocess
import sys
from urllib.parse import unquote
from .catalog import Catalog
from .common import CatalogError, read_json
from .lifecycle import inspect
from . import gitstore


def check(root, run_tests=False, *, scope='tool'):
    gitstore.require_root(root)
    errors=[];links=0;agreements=[]
    if scope == 'protocol':
        catalog=Catalog(root)
        for rel, metadata in catalog.agreements.items():
            result=inspect(root,metadata['agreement_id'])
            agreements.append({'id':metadata['agreement_id'],'stage':result['agreement']['stage'],
                               'review_current_and_approved':result['review_current_and_approved']})
        directories=('docs','agreements','registry','provenance')
        root_files=('README.md','AGENTS.md','repository.json','toolchain.lock.json')
        lock=root/'toolchain.lock.json'
        if lock.exists():
            data=read_json(lock)
            if (not isinstance(data,dict) or set(data)!={'format_version','repository','commit'}
                    or type(data['format_version']) is not int or data['format_version']!=1
                    or not isinstance(data['repository'],str) or not data['repository'].strip()
                    or not isinstance(data['commit'],str) or not gitstore.OID.fullmatch(data['commit'])):
                errors.append({'code':'TOOLCHAIN_LOCK_INVALID'})
    elif scope == 'tool':
        from .sources import load_catalog
        catalog=load_catalog(root)
        directories=('docs','tooling','skills')
        root_files=('README.md','AGENTS.md','CHANGELOG.md','sources.lock.json')
        tracked=gitstore.run(root,['ls-files','--','repositories']).stdout.decode().splitlines()
        if tracked:errors.append({'code':'NESTED_REPOSITORY_TRACKED','paths':tracked})
        for forbidden in ('agreements','registry','repository.json'):
            if (root/forbidden).exists():errors.append({'code':'PROTOCOL_CONTENT_IN_TOOL_REPOSITORY','path':forbidden})
    else:
        raise CatalogError('CHECK_SCOPE_INVALID')
    paths=[root/name for name in root_files]
    for directory in directories:
        for p in (root/directory).rglob('*'):
            if '__pycache__' in p.parts or p.name=='.DS_Store':continue
            if p.is_symlink():errors.append({'code':'SOURCE_SYMLINK','path':str(p.relative_to(root))});continue
            if p.is_file():paths.append(p)
    for p in paths:
        if not p.is_file():continue
        if p.suffix=='.json':
            try:read_json(p)
            except CatalogError:errors.append({'code':'INVALID_JSON','path':str(p.relative_to(root))})
        if p.suffix!='.md':continue
        content=re.sub(r'```[\s\S]*?```','',p.read_text())
        for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)',content):
            target=target.strip('<>').split('#')[0]
            if not target or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:',target):continue
            resolved=(p.parent/unquote(target)).resolve()
            if not resolved.exists():errors.append({'code':'BROKEN_LINK','path':str(p.relative_to(root)),'target':target})
            links+=1
    code=None
    if run_tests:
        code=0
        for suite in ('tooling/tests','tooling/integration'):
            result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',suite,'-v'],cwd=root,capture_output=True,text=True)
            sys.stderr.write(result.stderr)
            if result.returncode:
                code=result.returncode;errors.append({'code':'TEST_FAILURE','suite':suite})
    return {'valid':not errors,'scope':scope,'agreements':agreements,'fixed_snapshots':len(catalog.entries),
            'formal_releases':sum(e['status']=='released' for e in catalog.entries),
            'local_links_checked':links,'tests_exit_code':code,'errors':errors}
