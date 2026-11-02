"""Reviewed Git sources, fixed registry identities and recoverable publication."""
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import os
import shutil
import tempfile
from ..catalog import Catalog, reference, validate_reference, validate_review_record, validate_review_dependencies, validate_committed_review_files
from .. import gitstore
from ..common import CatalogError, read_json, safe_path, segment, sha256, json_bytes as encoded, atomic_write, atomic_create, canonical_sha256, parse_json, exclusive_guard


def write(path, content):
    atomic_write(path, content)


@contextmanager
def locked(root):
    p = safe_path(root, '.yunxia-maintenance.lock')
    with exclusive_guard(safe_path(root, '.yunxia-maintenance.guard'), code='MAINTENANCE_LOCKED'):
        try:
            atomic_create(p, encoded({'pid':os.getpid()}))
        except FileExistsError:
            raise CatalogError('MAINTENANCE_LOCKED') from None
        try:
            yield
        finally:
            p.unlink()


def recover_lock(root):
    p=safe_path(root,'.yunxia-maintenance.lock')
    with exclusive_guard(safe_path(root, '.yunxia-maintenance.guard'), code='MAINTENANCE_LOCKED'):
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
    validate_review_dependencies(m.get('review_dependencies', []))
    if m['review'] is not None:
        validate_review_record(m['review'])
    return m


def source_snapshot(root, aid):
    m = metadata(root, aid)
    if m['base'] is not None:
        Catalog(root).resolve(m['base'])
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
    snapshot = {'format_version':1, 'agreement': agreement, 'files': files}
    dependencies = {}
    for rel in validate_review_dependencies(m.get('review_dependencies', [])):
        dependency = safe_path(root, rel)
        if not dependency.is_file(): raise CatalogError('REVIEW_DEPENDENCY_MISSING', [rel])
        content = dependency.read_bytes()
        if not content.strip(): raise CatalogError('REVIEW_DEPENDENCY_MISSING', [rel])
        dependencies[rel] = sha256(content)
    if dependencies: snapshot['dependencies'] = dependencies
    return snapshot


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
    if decision['source_sha256'] != canonical_sha256(snapshot):
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
    digest = canonical_sha256(snapshot)
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


def record_path(root, aid, version):
    return safe_path(root, f'registry/{valid_id(aid)}/{valid_id(version)}.json')


def freeze(root, aid, version, notes):
    valid_id(version)
    if not isinstance(notes, str) or not notes.strip(): raise CatalogError('RELEASE_NOTES_REQUIRED')
    info = inspect(root, aid); m = info['agreement']
    if m['maintenance'] != 'active': raise CatalogError('MAINTENANCE_INACTIVE')
    if not info['review_current_and_approved']: raise CatalogError('REVIEW_REQUIRED_OR_STALE')
    if version == m['target_version']: raise CatalogError('CANDIDATE_VERSION_EQUALS_TARGET')
    target = record_path(root, aid, version)
    if target.exists(): raise CatalogError('FIXED_VERSION_EXISTS', [str(target)])
    if record_path(root, aid, m['target_version']).exists(): raise CatalogError('TARGET_VERSION_EXISTS')
    commit = gitstore.head(root)
    metadata_bytes = gitstore.read_blob(root, commit, f'agreements/{aid}/agreement.json')
    try: committed = parse_json(metadata_bytes.decode('utf-8'))
    except (ValueError,UnicodeError): raise CatalogError('REVIEW_NOT_COMMITTED') from None
    if not isinstance(committed,dict): raise CatalogError('REVIEW_NOT_COMMITTED')
    if committed.get('review') != m['review']:
        raise CatalogError('REVIEW_NOT_COMMITTED')
    source_agreement = {k: committed.get(k) for k in ('agreement_id','name','kind','purpose','target_version')}
    if committed.get('base') is not None: source_agreement['base'] = committed['base']
    if source_agreement != info['source_snapshot']['agreement']:
        raise CatalogError('REVIEW_SOURCE_CHANGED')
    if sorted(validate_review_dependencies(committed.get('review_dependencies', []))) != sorted(m.get('review_dependencies', [])):
        raise CatalogError('REVIEW_SOURCE_CHANGED')
    for rel, expected in info['source_snapshot'].get('dependencies', {}).items():
        gitstore.read_blob(root, commit, rel, expected)
    validate_committed_review_files(root, commit, aid, info['source_snapshot']['files'],
                                    code='REVIEW_SOURCE_CHANGED')
    for rel, expected in info['source_snapshot']['files'].items():
        gitstore.read_blob(root, commit, f'agreements/{aid}/{rel}', expected)
    source_files = {'text.txt': f'agreements/{aid}/text.txt', 'adoption.md': f'agreements/{aid}/adoption.md'}
    payload = {name: gitstore.read_blob(root, commit, path) for name,path in source_files.items()}
    payload.update({'notes.md': notes.encode(), 'review.json': encoded(m['review'])})
    tag = f'{aid}/v{version}'
    record = {'format_version':2, 'agreement_id':aid, 'version':version,
              'sha256':sha256(payload['text.txt']), 'display_name':m['name'], 'kind':m['kind'],
              'status':'candidate', 'publication':None,
              'source':{'commit':commit, 'tag':tag, 'files':source_files, 'metadata_sha256':sha256(metadata_bytes),
                        'source_sha256':info['source_sha256'], 'snapshot':info['source_snapshot'],
                        'target_version':m['target_version']},
              'files':{k:sha256(v) for k,v in payload.items()}, 'notes':notes, 'review':m['review']}
    if source_snapshot(root,aid) != info['source_snapshot']: raise CatalogError('REVIEW_SOURCE_CHANGED')
    gitstore.ensure_tag(root,tag,commit)
    write(target, encoded(record))
    return {'frozen':reference(record), 'status':'candidate', 'publication':None,
            'source_commit':commit, 'tag':tag, 'registry':str(target)}


def publish(root, aid, candidate, version, destination, decision):
    from ..publication import publish_local
    return publish_local(root, aid, candidate, version, destination, decision)


def prepare_publication(root, aid, candidate, version, destination, decision, github=None):
    from ..publication import prepare
    method = 'github-release' if github else 'verified-local-directory'
    channel = github if github else str(destination)
    record, files = prepare(root, aid, candidate, version, method, channel, decision)
    return {'prepared':reference(record), 'status':record['status'],
            'registry':str(record_path(root,aid,version)), 'assets':record['publication']['assets'],
            'registry_commit_required':True}


def publish_github(root, aid, candidate, version, repository, decision, backend=None):
    from ..publication import publish_github as run
    return run(root, aid, candidate, version, repository, decision, backend)


def verify_publication(root, aid, version, backend=None):
    from ..publication import verify_publication as run
    return run(root, aid, version, backend)


def withdraw(root, aid, version, decision):
    if not isinstance(decision,dict) or set(decision) != {'date','reason','confirmed_by'} or not all(isinstance(x,str) and x.strip() for x in decision.values()):
        raise CatalogError('WITHDRAWAL_FORMAT')
    date.fromisoformat(decision['date'])
    entry = Catalog(root).select(aid,version)
    if entry['status'] != 'released': raise CatalogError('WITHDRAWAL_REQUIRES_RELEASE')
    path = record_path(root,aid,version)
    record = read_json(path)
    if record.get('withdrawal') is not None and record['withdrawal'] != decision:
        raise CatalogError('WITHDRAWAL_ALREADY_RECORDED')
    record['withdrawal'] = decision
    m=metadata(root,aid)
    if m['recommended'] == reference(record):
        m['recommended']=None
        write(folder(root,aid)/'agreement.json',encoded(m))
    write(path,encoded(record))
    return {'withdrawn':reference(record),'existing_adoptions_changed':False}


def revise(root, aid, base, target):
    valid_id(target)
    m = metadata(root,aid); entry = Catalog(root).select(aid,base)
    if m['maintenance']!='active':raise CatalogError('MAINTENANCE_INACTIVE')
    if record_path(root,aid,target).exists():raise CatalogError('TARGET_VERSION_EXISTS')
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
    if ref:
        entry = Catalog(root).resolve(ref)
        if entry['status'] != 'released' or entry.get('withdrawal') is not None:
            raise CatalogError('RECOMMENDATION_REQUIRES_RELEASE')
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
