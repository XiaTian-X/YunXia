"""Exact Git object reads. No checkout, filters, historical code or rewritten ancestry."""
import os
import re
import subprocess
from .common import CatalogError, valid_relative_path, sha256

OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
MAX_BLOB = 64 * 1024 * 1024


def run(root, args, check=True):
    env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', GIT_GRAFT_FILE=os.devnull)
    result = subprocess.run(['git','--no-replace-objects','--literal-pathspecs','-C',str(root),*args],
                            capture_output=True, env=env)
    if check and result.returncode:
        raise CatalogError('GIT_OPERATION_FAILED', [args[0]])
    return result


def head(root):
    value = run(root, ['rev-parse','--verify','HEAD^{commit}']).stdout.decode().strip()
    if not OID.fullmatch(value): raise CatalogError('GIT_COMMIT_INVALID')
    return value


def list_tree(root, commit, prefix=None):
    if not isinstance(commit,str) or not OID.fullmatch(commit):raise CatalogError('GIT_COMMIT_INVALID')
    args=['ls-tree','-rz',commit]
    if prefix is not None:
        if not valid_relative_path(prefix):raise CatalogError('GIT_PATH_INVALID')
        args+=['--',prefix]
    result={}
    for row in run(root,args).stdout.split(b'\0'):
        if not row:continue
        meta,path=row.split(b'\t',1)
        mode,kind,oid=meta.decode().split()
        result[path.decode('utf-8')]=(mode,kind,oid)
    return result


def read_blob(root, commit, path, expected=None):
    if not isinstance(commit,str) or not OID.fullmatch(commit): raise CatalogError('GIT_COMMIT_INVALID')
    if not valid_relative_path(path): raise CatalogError('GIT_PATH_INVALID', [str(path)])
    result = run(root, ['cat-file','-t',commit], False)
    if result.returncode or result.stdout.strip()!=b'commit':
        raise CatalogError('GIT_SOURCE_UNAVAILABLE', [commit])
    # Git's tree path lookup never follows symlinks or gitlinks. Literal pathspecs
    # prevent wildcard interpretation; -l supplies blob size without another call.
    rows = run(root, ['ls-tree','-lz',commit,'--',path]).stdout.split(b'\0')
    matches=[]
    for row in rows:
        if not row:continue
        meta,name=row.split(b'\t',1)
        if name.decode('utf-8')==path:matches.append(meta.decode().split())
    if len(matches)!=1:raise CatalogError('GIT_SOURCE_UNAVAILABLE',[commit,path])
    mode,kind,oid,size=matches[0]
    if mode not in ('100644','100755') or kind!='blob':raise CatalogError('GIT_SOURCE_NOT_REGULAR',[path])
    if int(size)>MAX_BLOB:raise CatalogError('GIT_SOURCE_TOO_LARGE',[path])
    content = run(root,['cat-file','blob',oid]).stdout
    if content.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
        raise CatalogError('GIT_LFS_UNSUPPORTED',[path])
    if expected is not None and sha256(content)!=expected:
        raise CatalogError('GIT_SOURCE_HASH_MISMATCH',[path])
    return content


def validate_tag(tag):
    if not isinstance(tag,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*/v[A-Za-z0-9][A-Za-z0-9._-]*',tag):
        raise CatalogError('GIT_TAG_INVALID')


def verify_tag(root, tag, commit):
    validate_tag(tag)
    if not isinstance(commit,str) or not OID.fullmatch(commit): raise CatalogError('GIT_COMMIT_INVALID')
    result=run(root,['rev-parse','--verify','refs/tags/'+tag+'^{commit}'],False)
    if result.returncode: raise CatalogError('GIT_TAG_MISSING',[tag])
    if result.stdout.decode().strip()!=commit: raise CatalogError('GIT_TAG_CONFLICT',[tag])
    return True


def ensure_tag(root, tag, commit):
    validate_tag(tag)
    if not isinstance(commit,str) or not OID.fullmatch(commit): raise CatalogError('GIT_COMMIT_INVALID')
    exists=run(root,['show-ref','--verify','--quiet','refs/tags/'+tag],False).returncode==0
    if not exists:
        run(root,['-c','tag.gpgSign=false','tag','-a',tag,commit,'-m','Fixed protocol version '+tag])
    verify_tag(root,tag,commit)
    return {'tag':tag,'commit':commit,'created':not exists}
