"""Publication intents and receipts; retries verify existing side effects."""
import copy
from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import zipfile
from . import VERSION, gitstore
from .catalog import Catalog, reference
from .common import CatalogError, canonical_sha256, json_bytes, parse_json, read_json, sha256


def _record(entry):
    return {k:copy.deepcopy(v) for k,v in entry.items()
            if not k.startswith('_') and k not in ('renderer','capabilities','output_name')}


def _files(candidate, version, builder, decision):
    files = {k:v for k,v in candidate['_payload'].items() if k != 'release.json'}
    source=copy.deepcopy(candidate['source']);source['tag']=f'{candidate["agreement_id"]}/v{version}'
    manifest = {'format_version':1, 'agreement':dict(reference(candidate),version=version),
                'display_name':candidate['display_name'], 'kind':candidate['kind'],
                'source':source, 'builder':builder,
                'publication_decision':decision, 'files':{k:sha256(v) for k,v in sorted(files.items())}}
    files['manifest.json'] = json_bytes(manifest)
    return files


def _archive(files):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:
        for name,content in sorted(files.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            info.create_system=3;info.external_attr=0o100644 << 16
            archive.writestr(info,content)
    return stream.getvalue()


def _decision(decision):
    if not isinstance(decision,dict) or set(decision) != {'date','confirmed_by','basis','candidate_sha256'} or not all(isinstance(x,str) and x.strip() for x in decision.values()):
        raise CatalogError('PUBLICATION_DECISION_REQUIRED')
    try: date.fromisoformat(decision['date'])
    except ValueError: raise CatalogError('PUBLICATION_DECISION_REQUIRED') from None


def prepare(root, aid, candidate, version, method, channel, decision):
    from . import lifecycle as life
    life.valid_id(version);_decision(decision)
    if life.metadata(root,aid)['maintenance'] != 'active': raise CatalogError('MAINTENANCE_INACTIVE')
    entry=Catalog(root).select(aid,candidate)
    if entry['status'] != 'candidate': raise CatalogError('CANDIDATE_REQUIRED')
    if entry['_package_sha256'] != decision['candidate_sha256']: raise CatalogError('PUBLICATION_CANDIDATE_CHANGED')
    if version != entry['source']['target_version']: raise CatalogError('PUBLICATION_VERSION_MISMATCH')
    if method == 'verified-local-directory':
        destination=Path(channel)
        if destination.is_symlink(): raise CatalogError('PUBLICATION_PATH_INVALID')
        destination=destination.parent.resolve()/destination.name
        if destination==root.resolve() or root.resolve() in destination.parents:
            raise CatalogError('PUBLICATION_MUST_BE_OUTSIDE_REPOSITORY')
        channel=str(destination)
    elif method == 'github-release':
        from .github import GitHub
        GitHub(channel)  # Validate the explicit repository name without connecting.
    else: raise CatalogError('PUBLICATION_CHANNEL_INVALID')
    path=life.record_path(root,aid,version)
    previous=read_json(path) if path.exists() else None
    builder={'format_version':1,'tool_version':VERSION,
             'implementation_sha256':canonical_sha256({name:sha256((Path(__file__).parent/name).read_bytes()) for name in ('publication.py','common.py')})}
    if previous:
        if previous.get('withdrawal') is not None: raise CatalogError('PUBLICATION_WITHDRAWN')
        builder=previous.get('publication',{}).get('builder',builder)
    files=_files(entry,version,builder,decision)
    manifest_sha=sha256(files['manifest.json'])
    if method=='github-release':
        files={f'{aid}-{version}.zip':_archive(files)}
    assets={k:sha256(v) for k,v in files.items()}
    record=_record(entry)
    record.update(version=version,status='prepared',publication={
        'state':'prepared','method':method,'channel':channel,'decision':decision,
        'builder':builder,'manifest_sha256':manifest_sha,'assets':assets})
    record['source']['tag']=f'{aid}/v{version}'
    if previous:
        comparable=copy.deepcopy(previous)
        comparable.pop('withdrawal',None)
        comparable['status']='prepared'
        comparable['publication']['state']='prepared'
        comparable['publication'].pop('receipt',None)
        if comparable != record: raise CatalogError('PUBLICATION_INTENT_CONFLICT',[str(path)])
        return previous,files
    # Reserve this version before the first external effect. The tag also pins its source.
    gitstore.ensure_tag(root,record['source']['tag'],record['source']['commit'])
    life.write(path,json_bytes(record))
    return record,files


def _receipt(record, assets, release_id, url):
    if (not isinstance(url,str) or not url.strip()
            or (record['publication']['method']=='github-release' and (type(release_id) is not int or release_id<=0))
            or (record['publication']['method']=='verified-local-directory' and release_id is not None)):
        raise CatalogError('PUBLICATION_RECEIPT_INVALID')
    return {'verified_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'release_id':release_id,'url':url,'tag_commit':record['source']['commit'],'assets':assets}


def _complete(root, record, receipt):
    from . import lifecycle as life
    path=life.record_path(root,record['agreement_id'],record['version'])
    current=read_json(path)
    if current != record: raise CatalogError('PUBLICATION_RECORD_CONFLICT',[str(path)])
    if record['status']=='released':
        return record
    final=copy.deepcopy(record);final['status']='released'
    final['publication'].update(state='published',receipt=receipt)
    try:life.write(path,json_bytes(final))
    except (OSError,KeyboardInterrupt):
        raise CatalogError('PUBLICATION_RECOVERY_REQUIRED',[str(path)]) from None
    if read_json(path) != final:raise CatalogError('PUBLICATION_RECOVERY_REQUIRED',[str(path)])
    return final


def publish_local(root, aid, candidate, version, destination, decision):
    from . import lifecycle as life
    record,files=prepare(root,aid,candidate,version,'verified-local-directory',str(destination),decision)
    target=Path(record['publication']['channel'])
    if target.exists() and not life.tree_matches(target,files):raise CatalogError('FIXED_VERSION_EXISTS',[str(target)])
    if not target.exists():life.put_new_tree(target,files)
    if not life.tree_matches(target,files):raise CatalogError('PUBLICATION_DELIVERY_CHANGED',[str(target)])
    _complete(root,record,_receipt(record,record['publication']['assets'],None,str(target)))
    return {'released':reference(record),'delivery_verified':True,'channel':str(target)}


def _remote_assets(backend, release, expected, allow_missing=False):
    from .github import validate_asset
    assets=backend.assets(release)
    if not isinstance(assets,list):raise CatalogError('GITHUB_RESPONSE_INVALID')
    by_name={}
    for asset in assets:
        validate_asset(asset)
        name=asset.get('name')
        if name in by_name or name not in expected:raise CatalogError('PUBLICATION_ASSET_CONFLICT')
        by_name[name]=asset
    missing=set(expected)-set(by_name)
    if missing and not allow_missing:raise CatalogError('PUBLICATION_ASSET_MISSING',sorted(missing))
    for name,asset in by_name.items():
        content=backend.download(asset)
        if not isinstance(content,bytes):raise CatalogError('GITHUB_RESPONSE_INVALID')
        if sha256(content)!=expected[name]:raise CatalogError('PUBLICATION_ASSET_CONFLICT',[name])
    return missing


def _committed_registry(root, record, backend):
    catalog=Catalog(root);commit=gitstore.head(root)
    selected=catalog.select(record['agreement_id'],record['version'])
    if _record(selected)!=record:raise CatalogError('PUBLICATION_RECORD_CONFLICT',[selected['_manifest']])
    pending=[selected];seen=set();families=set();contents={};tags={};untagged_sources=set()
    while pending:
        entry=pending.pop();rel=entry['_manifest']
        if rel in seen:continue
        seen.add(rel)
        content=catalog.inputs[rel]
        try:committed=gitstore.read_blob(root,commit,rel)
        except CatalogError:raise CatalogError('PUBLICATION_REGISTRY_NOT_COMMITTED',[rel]) from None
        if committed!=content:raise CatalogError('PUBLICATION_REGISTRY_NOT_COMMITTED',[rel])
        contents[rel]=content
        # Preserved candidates and revision bases must be usable after cloning
        # the published registry, including their independently pinned tags.
        if rel!=selected['_manifest'] and entry['source']['tag'] is not None:
            tags[entry['source']['tag']]=entry['source']['commit']
        if entry['source']['tag'] is None:untagged_sources.add(entry['source']['commit'])
        base=entry['source'].get('snapshot',{}).get('agreement',{}).get('base')
        if base is not None:pending.append(catalog.resolve(base))
        publication=entry.get('publication')
        if publication is not None:
            pending.extend(e for e in catalog.entries if e['agreement_id']==entry['agreement_id']
                           and e['status']=='candidate' and e['_package_sha256']==publication['decision']['candidate_sha256'])
        aid=entry['agreement_id']
        if aid not in families:
            families.add(aid);metadata_path=f'agreements/{aid}/agreement.json'
            try:
                metadata_content=gitstore.read_blob(root,commit,metadata_path)
                metadata=parse_json(metadata_content)
            except (CatalogError,ValueError,UnicodeError):
                raise CatalogError('PUBLICATION_REGISTRY_NOT_COMMITTED',[metadata_path]) from None
            if (not isinstance(metadata,dict) or metadata.get('agreement_id')!=aid
                    or metadata_content!=catalog.inputs.get(metadata_path)):
                raise CatalogError('PUBLICATION_REGISTRY_NOT_COMMITTED',[metadata_path])
            contents[metadata_path]=metadata_content
            for key in ('base','recommended'):
                if metadata.get(key) is not None:pending.append(catalog.resolve(metadata[key]))
    for rel,content in sorted(contents.items()):backend.verify_registry(commit,rel,content)
    for source_commit in sorted(untagged_sources):backend.verify_source(source_commit)
    for tag,expected in sorted(tags.items()):
        actual=backend.tag_commit(tag)
        if actual is None:raise CatalogError('PUBLICATION_DEPENDENCY_TAG_NOT_PUSHED',[tag])
        if actual!=expected:raise CatalogError('PUBLICATION_TAG_CONFLICT',[tag])
    return commit


def publish_github(root, aid, candidate, version, repository, decision, backend=None):
    from .github import GitHub, validate_release
    backend=backend or GitHub(repository)
    record,files=prepare(root,aid,candidate,version,'github-release',repository,decision)
    _committed_registry(root,record,backend)
    tag,commit=record['source']['tag'],record['source']['commit']
    backend.ensure_tag(tag,commit)
    release=backend.release(tag)
    if release is None and record['status']=='released':
        raise CatalogError('PUBLICATION_NOT_PUBLIC')
    if release is None:
        release=backend.create_draft(tag,commit,f'{record["display_name"]} {version}',record.get('notes',''))
    validate_release(release)
    if release.get('tag_name')!=tag:raise CatalogError('PUBLICATION_RELEASE_CONFLICT')
    # If a published release disappears, a retry must not create a new publication silently.
    if record['status']=='released':
        expected_id=record['publication']['receipt']['release_id']
        if release.get('id')!=expected_id or release.get('draft') is not False:
            raise CatalogError('PUBLICATION_RELEASE_CONFLICT')
    expected=record['publication']['assets']
    if release.get('draft') is True:
        missing=_remote_assets(backend,release,expected,allow_missing=True)
        for name in sorted(missing):backend.upload(tag,name,files[name])
        _remote_assets(backend,release,expected)
        validate_release(backend.publish(release))
    release_id=release['id']
    release=backend.release(tag)
    if release is None:raise CatalogError('PUBLICATION_NOT_PUBLIC')
    validate_release(release)
    if release['draft'] is not False:raise CatalogError('PUBLICATION_NOT_PUBLIC')
    if release['tag_name']!=tag or release['id']!=release_id:raise CatalogError('PUBLICATION_RELEASE_CONFLICT')
    if backend.tag_commit(tag)!=commit:raise CatalogError('PUBLICATION_TAG_CONFLICT')
    _remote_assets(backend,release,expected)
    receipt=_receipt(record,expected,release['id'],release.get('html_url',''))
    _complete(root,record,receipt)
    return {'released':reference(record),'delivery_verified':True,'channel':repository,
            'registry_sync_required':record['status']!='released',
            'release_id':release['id'],'url':release.get('html_url','')}


def verify_publication(root, aid, version, backend=None):
    from .github import GitHub, validate_release
    from . import lifecycle as life
    entry=Catalog(root).select(aid,version);record=_record(entry)
    if record['status']!='released':raise CatalogError('PUBLICATION_NOT_COMPLETE')
    pub=record['publication']
    if pub['method']=='verified-local-directory':
        # Reconstruct from the preserved candidate selected by the publication decision.
        candidates=[e for e in Catalog(root).entries if e['agreement_id']==aid and e['status']=='candidate'
                    and e['_package_sha256']==pub['decision']['candidate_sha256']]
        if len(candidates)!=1:raise CatalogError('PUBLICATION_CANDIDATE_MISSING')
        files=_files(candidates[0],version,pub['builder'],pub['decision'])
        if not life.tree_matches(Path(pub['channel']),files):raise CatalogError('PUBLICATION_DELIVERY_CHANGED')
        return {'publication_verified':True,'channel':pub['channel']}
    backend=backend or GitHub(pub['channel'])
    _committed_registry(root,record,backend)
    tag=record['source']['tag'];release=backend.release(tag)
    if release is None:raise CatalogError('PUBLICATION_NOT_PUBLIC')
    validate_release(release)
    if release['tag_name']!=tag or release.get('id')!=pub['receipt']['release_id'] or release.get('draft') is not False:
        raise CatalogError('PUBLICATION_NOT_PUBLIC')
    if backend.tag_commit(tag)!=record['source']['commit']:raise CatalogError('PUBLICATION_TAG_CONFLICT')
    _remote_assets(backend,release,pub['assets'])
    return {'publication_verified':True,'registry_sync_verified':True,'channel':pub['channel']}
