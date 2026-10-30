"""Read-only repository validation; expected hashes are never refreshed here."""
from pathlib import Path
import json
import re
import subprocess
import sys
from urllib.parse import unquote
from .catalog import Catalog, supports
from .common import CatalogError, read_json
from .lifecycle import inspect


def check(root, run_tests=False):
    errors=[];links=0
    catalog=Catalog(root)
    agreements=[]
    for p in sorted((root/'agreements').glob('*/agreement.json')):
        result=inspect(root,p.parent.name)
        agreements.append({'id':p.parent.name,'stage':result['agreement']['stage'],
                           'review_current_and_approved':result['review_current_and_approved']})
    for row in supports(root/'tooling/src/yunxia/adapters/support.json'):
        catalog.resolve(row['agreement'])
    source_dirs=('docs','agreements','releases','tooling','skills')
    paths=[root/'README.md',root/'CHANGELOG.md']
    for directory in source_dirs:
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
        # Code examples and historical source paths written as inline code are not links.
        content=re.sub(r'```[\s\S]*?```','',p.read_text())
        for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)',content):
            target=target.strip('<>').split('#')[0]
            if not target or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:',target):continue
            # The built Skill's assets are deliberately absent from the instruction source.
            resolved=(p.parent/unquote(target)).resolve()
            if not resolved.exists():errors.append({'code':'BROKEN_LINK','path':str(p.relative_to(root)),'target':target})
            links+=1
    code=None
    if run_tests:
        result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tooling/tests','-v'],cwd=root,capture_output=True,text=True)
        sys.stderr.write(result.stderr)
        code=result.returncode
        if code:errors.append({'code':'TEST_FAILURE'})
    return {'valid':not errors,'agreements':agreements,'fixed_snapshots':len(catalog.entries),
            'formal_releases':sum(e['status']=='released' for e in catalog.entries),
            'local_links_checked':links,'tests_exit_code':code,'errors':errors}
