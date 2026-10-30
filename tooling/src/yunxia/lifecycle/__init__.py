"""Local protocol lifecycle. Publishing is explicit, exact-byte filesystem delivery."""
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import json
import os
import shutil
import tempfile
from ..catalog import Catalog, CatalogError, read_json, safe_path, segment, sha256, reference, validate_reference, validate_review_record


def encoded(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.yunxia-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


@contextmanager
def locked(root):
    p = safe_path(root, '.yunxia-maintenance.lock')
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CatalogError('MAINTENANCE_LOCKED') from None
    with os.fdopen(fd,'wb') as stream:
        stream.write(encoded({'pid':os.getpid()}));stream.flush();os.fsync(stream.fileno())
    try:
        yield
    finally:
        p.unlink()


def recover_lock(root):
    p=safe_path(root,'.yunxia-maintenance.lock')
    if not p.exists():return {'lock_recovered':False}
    value=read_json(p)
    if not isinstance(value,dict) or type(value.get('pid')) is not int or value['pid']<=0:
        raise CatalogError('MAINTENANCE_LOCK_INVALID',[str(p)])
    try:os.kill(value['pid'],0)
    except ProcessLookupError:
        p.unlink();return {'lock_recovered':True}
    except PermissionError:pass
    raise CatalogError('MAINTENANCE_LOCKED',[str(p)])


def valid_id(value):
    if not segment(value): raise CatalogError('INVALID_ID')
    return value


def folder(root, aid):
    return safe_path(root, 'agreements/' + valid_id(aid))


def metadata(root, aid):
    m = read_json(safe_path(folder(root, aid), 'agreement.json'))
    if not isinstance(m, dict) or type(m.get('format_version')) is not int or m['format_version'] != 1 or m.get('agreement_id') != aid:
        raise CatalogError('AGREEMENT_FORMAT')
    if not all(isinstance(m.get(k), str) and m[k].strip() for k in ('name', 'purpose', 'kind', 'target_version')) or not segment(m['target_version']) or m.get('stage') not in ('draft', 'review', 'candidate'):
        raise CatalogError('AGREEMENT_FORMAT')
    if m.get('maintenance') not in ('active', 'paused', 'retired'):
        raise CatalogError('AGREEMENT_FORMAT')
    if not {'recommended', 'review', 'base'} <= m.keys():
        raise CatalogError('AGREEMENT_FORMAT')
    for key in ('recommended', 'base'):
        if m[key] is not None:
            validate_reference(m[key])
            if m[key]['agreement_id'] != aid:
                raise CatalogError('AGREEMENT_REFERENCE_MISMATCH', [key])
    if m['review'] is not None:
        validate_review_record(m['review'])
    return m


def source_snapshot(root, aid):
    m = metadata(root, aid)
    p = folder(root, aid)
    files = {}
    names = ['text.txt', 'README.md', 'design.md', 'review.md', 'adoption.md']
    if safe_path(p, 'cases.json').exists():
        names.append('cases.json')
    templates = safe_path(p, 'templates')
    if templates.exists():
        if not templates.is_dir():
            raise CatalogError('WORKING_DOCUMENT_INVALID', ['templates'])
        for template in sorted(templates.rglob('*')):
            if template.name == '.DS_Store':
                continue
            rel = template.relative_to(p).as_posix()
            safe_path(p, rel)
            if template.is_file():
                names.append(rel)
    for name in names:
        f = safe_path(p, name)
        if not f.is_file() or not f.read_bytes().strip():
            raise CatalogError('WORKING_DOCUMENT_MISSING', [str(f)])
        files[name] = sha256(f.read_bytes())
    agreement = {k: m[k] for k in ('agreement_id', 'name', 'kind', 'purpose', 'target_version')}
    if m['base'] is not None:
        agreement['base'] = m['base']
    return {'agreement': agreement, 'files': files}


def create(root, aid, name, purpose, body, target, kind='license'):
    valid_id(aid); valid_id(target)
    if not all(isinstance(x, str) and x.strip() for x in (name, purpose, kind)) or not body.strip():
        raise CatalogError('AGREEMENT_INPUT_REQUIRED')
    body.decode('utf-8')
    p = folder(root, aid)
    if p.exists(): raise CatalogError('AGREEMENT_EXISTS')
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.new-agreement-', dir=p.parent) as temp:
        stage = Path(temp)/aid; stage.mkdir()
        (stage/'text.txt').write_bytes(body)
        (stage/'agreement.json').write_bytes(encoded({'format_version':1,'agreement_id':aid,'name':name,'purpose':purpose,'kind':kind,'target_version':target,'stage':'draft','maintenance':'active','recommended':None,'review':None,'base':None}))
        for file, content in {'README.md':f'# {name}\n\n{purpose}\n', 'design.md':'# 设计依据\n\n尚待维护者填写具体设计决定与来源。\n', 'review.md':'# 审阅\n\n尚未完成审阅；未决事项待逐项记录。\n', 'adoption.md':'# 采用指南\n\n尚未完成采用路径核对；工具支持需单独登记并测试。\n'}.items():
            (stage/file).write_text(content)
        stage.rename(p)
    return {'created':aid,'stage':'draft','formal_release_created':False}


def review(root, aid, decision):
    m = metadata(root, aid)
    if m['maintenance']!='active':raise CatalogError('MAINTENANCE_INACTIVE')
    snapshot = source_snapshot(root, aid)
    if not isinstance(decision, dict) or set(decision) != {'source_sha256','reviewer','date','decision','basis','open_issues'}:
        raise CatalogError('REVIEW_FORMAT')
    if decision['source_sha256'] != sha256(encoded(snapshot)):
        raise CatalogError('REVIEW_SOURCE_CHANGED')
    if not all(isinstance(decision[k], str) and decision[k].strip() for k in ('reviewer','basis','date')) or decision['decision'] not in ('approved','changes-required') or not isinstance(decision['open_issues'], list) or not all(isinstance(x, str) and x.strip() for x in decision['open_issues']):
        raise CatalogError('REVIEW_FORMAT')
    date.fromisoformat(decision['date'])
    if decision['decision'] == 'approved' and decision['open_issues']:
        raise CatalogError('REVIEW_HAS_OPEN_ISSUES')
    m['stage'] = 'review'
    m['review'] = dict(decision, source_snapshot=snapshot)
    write(folder(root,aid)/'agreement.json', encoded(m))
    return {'review_recorded':True,'decision':decision['decision']}


def inspect(root, aid):
    m = metadata(root, aid); snapshot = source_snapshot(root, aid)
    digest = sha256(encoded(snapshot))
    r = m.get('review')
    approved = isinstance(r, dict) and r.get('decision') == 'approved' and not r.get('open_issues') and r.get('source_sha256') == digest
    return {'agreement':m,'source_snapshot':snapshot,'source_sha256':digest,'review_current_and_approved':approved}


def put_new_tree(destination, files):
    if destination.exists() or destination.is_symlink(): raise CatalogError('FIXED_VERSION_EXISTS', [str(destination)])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.yunxia-stage-', dir=destination.parent) as temp:
        stage = Path(temp)/'next';stage.mkdir()
        for rel, data in files.items():
            target = safe_path(stage, rel); target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(data)
        stage.rename(destination)


def tree_matches(directory, files):
    """Compare a known package without opening unrelated destination contents."""
    expected = {}
    for rel, content in files.items():
        safe_path(directory, rel)
        parts = Path(rel).parts
        node = expected
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                return False
        if parts[-1] in node:
            return False
        node[parts[-1]] = content

    def match(parent, children):
        if parent.is_symlink() or not parent.is_dir():
            return False
        entries = {p.name: p for p in parent.iterdir()}
        if set(entries) != set(children):
            return False
        for name, content in children.items():
            p = entries[name]
            if p.is_symlink():
                return False
            if isinstance(content, dict):
                if not match(p, content):
                    return False
            elif not p.is_file() or p.stat().st_size != len(content) or p.read_bytes() != content:
                return False
        return True

    return match(directory, expected)


def freeze(root, aid, version, notes):
    valid_id(version)
    if not isinstance(notes, str) or not notes.strip(): raise CatalogError('RELEASE_NOTES_REQUIRED')
    info = inspect(root, aid); m = info['agreement']
    if m['maintenance']!='active':raise CatalogError('MAINTENANCE_INACTIVE')
    if not info['review_current_and_approved']: raise CatalogError('REVIEW_REQUIRED_OR_STALE')
    if version == m['target_version']:
        raise CatalogError('CANDIDATE_VERSION_EQUALS_TARGET')
    if safe_path(root, f"releases/{aid}/{m['target_version']}").exists():
        raise CatalogError('TARGET_VERSION_EXISTS')
    p = folder(root, aid)
    body = safe_path(p,'text.txt').read_bytes()
    files = {'text.txt':body,'notes.md':notes.encode(),'review.json':encoded(m['review']), 'adoption.md':(p/'adoption.md').read_bytes()}
    if sha256(body)!=info['source_snapshot']['files']['text.txt'] or sha256(files['adoption.md'])!=info['source_snapshot']['files']['adoption.md']:
        raise CatalogError('REVIEW_SOURCE_CHANGED')
    record = {'format_version':1,'agreement_id':aid,'version':version,'sha256':sha256(body),'display_name':m['name'],'kind':m['kind'],'status':'candidate','publication':None,'legacy_references':[], 'source':{'source_sha256':info['source_sha256'],'snapshot':info['source_snapshot'],'target_version':m['target_version']},'files':{k:sha256(v) for k,v in files.items()}}
    files['release.json'] = encoded(record)
    # Re-read after staging the plan; review cannot authorize a concurrent edit.
    if source_snapshot(root,aid) != info['source_snapshot']: raise CatalogError('REVIEW_SOURCE_CHANGED')
    target = safe_path(root, f'releases/{aid}/{version}')
    put_new_tree(target,files)
    return {'frozen':reference(record),'status':'candidate','publication':None}


def publish(root, aid, candidate, version, destination, decision):
    valid_id(version)
    if metadata(root,aid)['maintenance']!='active':raise CatalogError('MAINTENANCE_INACTIVE')
    entry = Catalog(root).select(aid,candidate)
    if entry['status'] != 'candidate': raise CatalogError('CANDIDATE_REQUIRED')
    if not isinstance(decision, dict) or set(decision) != {'date','confirmed_by','basis','candidate_sha256'} or not all(isinstance(x,str) and x.strip() for x in decision.values()):
        raise CatalogError('PUBLICATION_DECISION_REQUIRED')
    source_dir = safe_path(root, f'releases/{aid}/{candidate}')
    package_sha = sha256(encoded({p.name:sha256(p.read_bytes()) for p in sorted(source_dir.iterdir()) if p.is_file()}))
    if decision['candidate_sha256'] != package_sha: raise CatalogError('PUBLICATION_CANDIDATE_CHANGED')
    date.fromisoformat(decision['date'])
    if version != entry['source']['target_version']: raise CatalogError('PUBLICATION_VERSION_MISMATCH')
    if destination.is_symlink(): raise CatalogError('PUBLICATION_PATH_INVALID')
    destination = destination.parent.resolve() / destination.name
    root = root.resolve()
    if destination == root or root in destination.parents: raise CatalogError('PUBLICATION_MUST_BE_OUTSIDE_REPOSITORY')
    record = {k:v for k,v in entry.items() if not k.startswith('_') and k not in ('renderer','capabilities','output_name')}
    record.update(version=version,status='released',publication={**decision,'channel':str(destination),'method':'verified-local-directory'})
    files = {rel:safe_path(source_dir,rel).read_bytes() for rel in entry['files']}
    if any(sha256(content)!=entry['files'][rel] for rel,content in files.items()):
        raise CatalogError('PUBLICATION_CANDIDATE_CHANGED')
    files['release.json'] = encoded(record)
    target = safe_path(root, f'releases/{aid}/{version}')
    for location in (destination,target):
        if location.exists() and not tree_matches(location, files):
            raise CatalogError('FIXED_VERSION_EXISTS',[str(location)])
    if not destination.exists(): put_new_tree(destination,files)
    if not tree_matches(destination, files):
        raise CatalogError('PUBLICATION_DELIVERY_CHANGED', [str(destination)])
    try:
        if not target.exists(): put_new_tree(target,files)
    except BaseException:
        raise CatalogError('PUBLICATION_RECOVERY_REQUIRED',[str(destination)]) from None
    if not tree_matches(target, files) or not tree_matches(destination, files):
        raise CatalogError('PUBLICATION_RECOVERY_REQUIRED', [str(destination), str(target)])
    return {'released':reference(record),'delivery_verified':True,'channel':str(destination)}


def revise(root, aid, base, target):
    valid_id(target)
    m = metadata(root,aid); entry = Catalog(root).select(aid,base)
    if m['maintenance']!='active':raise CatalogError('MAINTENANCE_INACTIVE')
    if safe_path(root,f'releases/{aid}/{target}').exists():raise CatalogError('TARGET_VERSION_EXISTS')
    body = safe_path(folder(root,aid),'text.txt').read_bytes()
    if body != entry['_text']: raise CatalogError('WORKING_TEXT_DIFFERS_FROM_BASE')
    m.update(target_version=target,stage='draft',review=None,base=reference(entry))
    write(folder(root,aid)/'agreement.json',encoded(m))
    return {'target_version':target,'base':reference(entry),'text_unchanged':True}


def recommend(root, aid, version, reason):
    if not isinstance(reason,str) or not reason.strip(): raise CatalogError('RECOMMENDATION_REASON_REQUIRED')
    m=metadata(root,aid)
    if version is not None and m['maintenance']=='retired':raise CatalogError('MAINTENANCE_INACTIVE')
    ref=None if version is None else reference(Catalog(root).select(aid,version))
    if ref and Catalog(root).resolve(ref)['status']!='released': raise CatalogError('RECOMMENDATION_REQUIRES_RELEASE')
    m['recommended']=ref; m['recommendation_reason']=reason
    write(folder(root,aid)/'agreement.json',encoded(m))
    return {'recommended':ref,'existing_project_adoptions_changed':False}


def maintain(root,aid,status,reason):
    if status not in ('active','paused','retired') or not isinstance(reason,str) or not reason.strip():
        raise CatalogError('MAINTENANCE_REASON_REQUIRED')
    m=metadata(root,aid)
    m.update(maintenance=status,maintenance_reason=reason)
    if status=='retired':m['recommended']=None
    write(folder(root,aid)/'agreement.json',encoded(m))
    return {'maintenance':status,'fixed_versions_preserved':True}
