"""Read fixed releases; development metadata never grants executable capabilities."""
from pathlib import Path
import os
import re
from datetime import date
from ..common import CatalogError, safe_path, sha256, segment, canonical_sha256, valid_relative_path, parse_json, json_bytes
from .. import gitstore

REFERENCE_KEYS = {'agreement_id', 'version', 'sha256'}
MIGRATION_PATH = 'provenance/repository-migration.json'


def validate_repository(data):
    if (not isinstance(data, dict) or set(data) != {'format_version', 'repository_id', 'migration'}
            or type(data['format_version']) is not int or data['format_version'] != 1
            or not segment(data['repository_id'])
            or data['migration'] not in (None, MIGRATION_PATH)):
        raise CatalogError('PROTOCOL_REPOSITORY_FORMAT', ['repository.json'])
    return data


def require_repository(root):
    gitstore.require_root(root)
    try:
        data = parse_json(safe_path(root, 'repository.json').read_bytes().decode('utf-8'))
    except (OSError, ValueError):
        raise CatalogError('PROTOCOL_REPOSITORY_REQUIRED', ['repository.json']) from None
    return validate_repository(data)


def validate_review_dependencies(value):
    if not isinstance(value,list) or any(not isinstance(p,str) for p in value) or len(set(value))!=len(value):raise CatalogError('REVIEW_DEPENDENCIES_INVALID')
    for path in value:
        parts=path.split('/')
        lower=parts[-1].lower()
        if (not valid_relative_path(path) or len(parts)<2 or parts[0] not in ('docs','agreements')
                or any(p.startswith('.') or p.lower() in ('private','secrets','credentials') for p in parts)
                or lower in ('agreement.json','review.json','auth.json','token.json','tokens.json')
                or lower.startswith(('credentials','client_secret','service-account','service_account'))
                or Path(lower).suffix in ('.pem','.key','.p8','.p12','.pfx','.jks','.keystore')):
            raise CatalogError('REVIEW_DEPENDENCIES_INVALID',[path])
    return value


def validate_review_record(review):
    required = {'source_sha256', 'source_snapshot', 'reviewer', 'date', 'decision', 'basis', 'open_issues'}
    if not isinstance(review, dict) or not required <= review.keys():
        raise CatalogError('REVIEW_FORMAT')
    if not all(isinstance(review[k], str) and review[k].strip() for k in ('reviewer', 'basis', 'date')):
        raise CatalogError('REVIEW_FORMAT')
    try:
        date.fromisoformat(review['date'])
    except ValueError:
        raise CatalogError('REVIEW_FORMAT') from None
    issues = review['open_issues']
    if review['decision'] not in ('approved', 'changes-required') or not isinstance(issues, list) or not all(isinstance(x, str) and x.strip() for x in issues):
        raise CatalogError('REVIEW_FORMAT')
    if review['decision'] == 'approved' and issues:
        raise CatalogError('REVIEW_HAS_OPEN_ISSUES')
    snapshot = review['source_snapshot']
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('agreement'), dict) or not isinstance(snapshot.get('files'), dict):
        raise CatalogError('REVIEW_FORMAT')
    if type(snapshot.get('format_version')) is not int or snapshot['format_version']!=1 or snapshot.keys()-{'format_version','agreement','files','dependencies'}:
        raise CatalogError('REVIEW_FORMAT')
    dependencies=snapshot.get('dependencies',{})
    if not isinstance(dependencies,dict):raise CatalogError('REVIEW_FORMAT')
    validate_review_dependencies(list(dependencies))
    if any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in dependencies.values()):raise CatalogError('REVIEW_FORMAT')
    agreement, files = snapshot['agreement'], snapshot['files']
    if not all(isinstance(agreement.get(k), str) and agreement[k].strip() for k in ('agreement_id', 'name', 'kind', 'purpose', 'target_version')):
        raise CatalogError('REVIEW_FORMAT')
    if not segment(agreement['agreement_id']) or not segment(agreement['target_version']):
        raise CatalogError('REVIEW_FORMAT')
    if agreement.get('base') is not None:
        validate_reference(agreement['base'])
        if agreement['base']['agreement_id'] != agreement['agreement_id']:
            raise CatalogError('REVIEW_FORMAT')
    if not {'text.txt', 'README.md', 'design.md', 'review.md', 'adoption.md'} <= files.keys():
        raise CatalogError('REVIEW_FORMAT')
    if any(not valid_relative_path(k) or not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for k, v in files.items()):
        raise CatalogError('REVIEW_FORMAT')
    if review['source_sha256'] != canonical_sha256(snapshot):
        raise CatalogError('REVIEW_FORMAT')
    return review


def validate_committed_review_files(root, commit, agreement_id, files, *,
                                    code='RELEASE_REVIEW_INVALID', paths=None):
    """Require the review to cover the complete material set in the fixed tree."""
    prefix = f'agreements/{agreement_id}/'
    tree = gitstore.list_tree(root, commit, prefix.rstrip('/'))
    names = {'text.txt', 'README.md', 'design.md', 'review.md', 'adoption.md'}
    if prefix+'cases.json' in tree:
        names.add('cases.json')
    for path, (mode, kind, oid) in tree.items():
        if path == prefix+'templates' or path.startswith(prefix+'templates/'):
            if mode not in ('100644', '100755') or kind != 'blob':
                raise CatalogError(code, paths)
            names.add(path[len(prefix):])
    if names != set(files):
        raise CatalogError(code, paths)


def child_directories(root, relative):
    """Do not silently hide an incomplete record or follow a directory symlink."""
    parent = safe_path(root, relative)
    if not parent.exists():
        return []
    if not parent.is_dir():
        raise CatalogError('CATALOG_DIRECTORY_INVALID', [relative])
    result = []
    for p in sorted(parent.iterdir()):
        rel = p.relative_to(root).as_posix()
        if p.name == '.DS_Store' or p.name.startswith(('.new-agreement-', '.yunxia-stage-')):
            continue
        safe_path(root, rel)
        if p.is_file() and p.name == 'README.md':
            continue
        if not p.is_dir() or not segment(p.name):
            raise CatalogError('CATALOG_DIRECTORY_INVALID', [rel])
        result.append(p)
    return result


def validate_reference(ref):
    if not isinstance(ref, dict) or set(ref) != REFERENCE_KEYS:
        raise CatalogError('AGREEMENT_REFERENCE_FORMAT')
    if not all(segment(ref[k]) for k in set(ref) - {'sha256'}) or not isinstance(ref['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', ref['sha256']):
        raise CatalogError('AGREEMENT_REFERENCE_FORMAT')
    return ref


def reference(entry):
    return validate_reference({k: entry.get(k) for k in sorted(REFERENCE_KEYS)})


def default_root():
    if os.environ.get('YUNXIA_CATALOG_ROOT'):
        return Path(os.environ['YUNXIA_CATALOG_ROOT'])
    for parent in Path(__file__).resolve().parents:
        if (parent / 'assets/catalog/BUNDLE.json').is_file():
            return parent / 'assets/catalog'
        if (parent / 'sources.lock.json').is_file():
            return parent
        if (parent / 'repository.json').is_file():
            return parent
    raise CatalogError('CATALOG_NOT_FOUND')


def validate_supports(data):
    if not isinstance(data, dict) or type(data.get('format_version')) is not int or data['format_version'] != 1 or not isinstance(data.get('supported'), list):
        raise CatalogError('ADAPTER_REGISTRY_FORMAT')
    seen = set()
    for row in data['supported']:
        if not isinstance(row, dict) or set(row) != {'agreement', 'adapter', 'output_name'}:
            raise CatalogError('ADAPTER_REGISTRY_FORMAT')
        ref = validate_reference(row['agreement'])
        key = tuple(sorted(ref.items()))
        if key in seen or row['adapter'] != 'scope-license-v1' or not isinstance(row['output_name'], str) or not re.fullmatch(r'LICENSE\.[A-Za-z0-9][A-Za-z0-9._-]*\.txt', row['output_name']):
            raise CatalogError('ADAPTER_REGISTRY_FORMAT')
        seen.add(key)
    return data['supported']


class Catalog:
    def __init__(self, root=None, *, commit=None, adapter_data=None):
        self.root = Path(root) if root is not None else default_root()
        self.inputs, self.blobs, self.entries, self.recommended_by_agreement = {}, {}, [], {}
        self._blob_cache = {}
        self._head = None
        self._commit = commit
        self._tree = None
        self.history_roots = []
        self.repository = None
        self.migration = None
        if not self.root.is_dir() or self.root.is_symlink(): raise CatalogError('CATALOG_PATH_INVALID')
        self.bundled = commit is None and (self.root/'BUNDLE.json').exists()
        if not self.bundled:
            gitstore.require_root(self.root)
            if commit is not None:
                if not isinstance(commit, str) or not gitstore.OID.fullmatch(commit): raise CatalogError('GIT_COMMIT_INVALID')
                self._tree = gitstore.list_tree(self.root, commit)
                self._head = commit
            elif gitstore.run(self.root,['rev-parse','--verify','HEAD^{commit}'],False).returncode==0:
                self._head = gitstore.head(self.root)
            if gitstore.run(self.root,['rev-parse','--is-shallow-repository']).stdout.strip()!=b'false':
                raise CatalogError('GIT_HISTORY_INCOMPLETE')
            if self._head is not None: self.history_roots.append(self._head)
        if self._exists('catalog.json'): raise CatalogError('UNSUPPORTED_CATALOG_FORMAT')
        if self.bundled:
            marker=self._json('BUNDLE.json')
            if not isinstance(marker,dict) or type(marker.get('format_version')) is not int or marker['format_version']!=1:
                raise CatalogError('BUNDLE_FORMAT')
            bundle_snapshot=marker.get('snapshot_sha256')
            if not isinstance(bundle_snapshot,str) or not re.fullmatch('[0-9a-f]{64}',bundle_snapshot): raise CatalogError('BUNDLE_FORMAT')
            adapter_data=self._json('adapters.json')
        else:
            if not self._is_file('repository.json'):
                raise CatalogError('PROTOCOL_REPOSITORY_REQUIRED', ['repository.json'])
            self.repository = validate_repository(self._json('repository.json'))
            self._control_history('repository.json', self.repository)
            self._migration_history()
            if adapter_data is None: adapter_data={'format_version':1,'supported':[]}
        bases=[]
        for family in self._directories('registry'):
            for p in self._children(family.relative_to(self.root).as_posix()):
                if p.name=='.DS_Store': continue
                rel=p.relative_to(self.root).as_posix()
                if not self._is_file(rel) or p.suffix!='.json' or not segment(p.stem): raise CatalogError('REGISTRY_PATH_INVALID',[rel])
                m=self._json(rel)
                self._add(m,rel,p.parent.name,p.stem,bases)
        identities=set()
        self.agreements={}
        for folder in self._directories('agreements'):
            rel=(folder/'agreement.json').relative_to(self.root).as_posix()
            if not self._is_file(rel): raise CatalogError('AGREEMENT_RECORD_MISSING',[rel])
            m=self._json(rel)
            if not isinstance(m,dict) or not {'agreement_id','name','kind','recommended'}<=m.keys() or m['agreement_id']!=folder.name or not all(isinstance(m[k],str) and m[k].strip() for k in ('name','kind')):
                raise CatalogError('AGREEMENT_FORMAT',[rel])
            identities.add(m['agreement_id']);self.agreements[rel]=m
            if m.get('base') is not None:
                validate_reference(m['base'])
                if m['base']['agreement_id']!=m['agreement_id']:raise CatalogError('AGREEMENT_REFERENCE_MISMATCH',[rel])
                bases.append((m['base'],rel))
            ref=m['recommended']
            if ref is not None:
                validate_reference(ref)
                if ref['agreement_id']!=m['agreement_id']:raise CatalogError('RECOMMENDATION_AGREEMENT_MISMATCH',[rel])
                e=self.resolve(ref)
                if e['status']!='released' or e.get('withdrawal'):raise CatalogError('RECOMMENDATION_REQUIRES_RELEASE')
                if m.get('maintenance')=='retired':raise CatalogError('RETIRED_AGREEMENT_RECOMMENDED',[rel])
            self.recommended_by_agreement[m['agreement_id']]=ref
        if any(e['agreement_id'] not in identities for e in self.entries):raise CatalogError('RELEASE_AGREEMENT_NOT_REGISTERED')
        for ref,rel in bases:
            try:self.resolve(ref)
            except CatalogError as error:raise CatalogError(error.code,[rel]) from None
        self.apply_supports(adapter_data)
        self._publication_bindings()
        if not self.bundled and self.history_roots:
            # Follow every parent and include merge-created records, even if later removed.
            historical=gitstore.run(self.root,['log','--full-history','-m','--no-renames','--format=','--name-only','--diff-filter=A',*self.history_roots,'--','registry']).stdout.decode().splitlines()
            for rel in set(historical):
                if re.fullmatch(r'registry/[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\.json',rel) and rel not in self.inputs:
                    raise CatalogError('REGISTRY_RECORD_MISSING',[rel])
        if self.bundled:
            expected=self.export_files()
            actual={p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file()}
            if actual!=set(expected):raise CatalogError('BUNDLE_FILE_SET_MISMATCH')
            for rel,content in expected.items():
                if safe_path(self.root,rel).read_bytes()!=content:raise CatalogError('BUNDLE_HASH_MISMATCH',[rel])

    def _read(self, rel):
        if self._commit is not None:
            return gitstore.read_blob(self.root, self._commit, rel)
        return safe_path(self.root, rel).read_bytes()

    def _exists(self, rel):
        if self._tree is not None:
            return rel in self._tree or any(p.startswith(rel+'/') for p in self._tree)
        return safe_path(self.root, rel).exists()

    def _is_file(self, rel):
        if self._tree is not None:
            item = self._tree.get(rel)
            return item is not None and item[0] in ('100644', '100755') and item[1] == 'blob'
        return safe_path(self.root, rel).is_file()

    def _children(self, rel):
        if self._tree is None:
            return sorted(safe_path(self.root, rel).iterdir())
        prefix=rel+'/'
        names={p[len(prefix):].split('/')[0] for p in self._tree if p.startswith(prefix)}
        return [self.root/rel/name for name in sorted(names)]

    def _directories(self, rel):
        if self._tree is None:
            return child_directories(self.root, rel)
        if rel in self._tree:
            raise CatalogError('CATALOG_DIRECTORY_INVALID', [rel])
        result=[]
        for path in self._children(rel):
            name=path.name
            if name=='.DS_Store' or name.startswith(('.new-agreement-', '.yunxia-stage-')):
                continue
            child=path.relative_to(self.root).as_posix()
            if name=='README.md' and self._is_file(child):
                continue
            if not segment(name) or child in self._tree:
                raise CatalogError('CATALOG_DIRECTORY_INVALID', [child])
            result.append(path)
        return result

    def _history_commits(self, rel, roots):
        if not roots:
            return []
        return gitstore.run(self.root, ['log', '--full-history', '--format=%H',
                                       *roots, '--', rel]).stdout.decode().splitlines()

    def _control_history(self, rel, current):
        """The root marker and migration cannot be erased, even on a merged branch."""
        roots=[self._head] if self._head is not None else []
        for commit in self._history_commits(rel, roots):
            try:
                before=parse_json(gitstore.read_blob(self.root,commit,rel).decode('utf-8'))
            except (CatalogError, ValueError):
                raise CatalogError('REPOSITORY_HISTORY_CHANGED', [rel]) from None
            if before!=current:
                raise CatalogError('REPOSITORY_HISTORY_CHANGED', [rel])

    def _migration_history(self):
        path=self.repository['migration']
        if path is None:
            if self._exists(MIGRATION_PATH) or self._history_commits(MIGRATION_PATH, self.history_roots):
                raise CatalogError('REPOSITORY_HISTORY_CHANGED', [MIGRATION_PATH])
            return
        if not self._is_file(path):
            raise CatalogError('MIGRATION_REQUIRED', [path])
        data=self._json(path)
        if (not isinstance(data,dict) or set(data)!={'format_version','origin','retained_history','registry_files','payloads'}
                or type(data.get('format_version')) is not int or data['format_version']!=1):
            raise CatalogError('MIGRATION_FORMAT', [path])
        origin, retained=data['origin'],data['retained_history']
        if (not isinstance(origin,dict) or set(origin)!={'repository','cutoff_commit'}
                or not isinstance(origin['repository'],str) or not origin['repository'].strip()
                or not isinstance(origin['cutoff_commit'],str) or not gitstore.OID.fullmatch(origin['cutoff_commit'])
                or not isinstance(retained,dict) or set(retained)!={'ref','commit'}
                or retained['commit']!=origin['cutoff_commit'] or not isinstance(retained['ref'],str)
                or not retained['ref'].startswith('refs/heads/history/')
                or gitstore.run(self.root,['check-ref-format',retained['ref']],False).returncode):
            raise CatalogError('MIGRATION_FORMAT', [path])
        self._control_history(path,data)
        commit=origin['cutoff_commit']
        tree=gitstore.list_tree(self.root,commit,'registry')
        registry_paths={p for p in tree if re.fullmatch(r'registry/[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\.json',p)}
        hashes=data['registry_files']
        if (not isinstance(hashes,dict) or set(hashes)!=registry_paths
                or any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in hashes.values())):
            raise CatalogError('MIGRATION_BASELINE_CHANGED', [path])
        payloads=[]
        for rel in sorted(registry_paths):
            content=gitstore.read_blob(self.root,commit,rel,hashes[rel])
            try:
                record=parse_json(content.decode('utf-8'))
                ref=reference(record)
                files={name:{'commit':record['source']['commit'], 'path':source,
                             'sha256':record['files'][name]}
                       for name,source in record['source']['files'].items()}
            except (ValueError, KeyError, TypeError, AttributeError):
                raise CatalogError('MIGRATION_FORMAT', [rel]) from None
            payloads.append({'agreement':ref,'files':files})
            for item in files.values():
                gitstore.read_blob(self.root,item['commit'],item['path'],item['sha256'])
        if data['payloads']!=payloads:
            raise CatalogError('MIGRATION_BASELINE_CHANGED', [path])
        # Branches retain the actual source objects; full commit IDs remain the identity.
        branch=retained['ref'][len('refs/heads/'):]
        names=gitstore.run(self.root,['for-each-ref','--format=%(refname) %(objectname)',
                                     retained['ref'],'refs/remotes']).stdout.decode().splitlines()
        matches=[row.split(' ',1)[1] for row in names
                 if row.split(' ',1)[0]==retained['ref']
                 or row.split(' ',1)[0].startswith('refs/remotes/')
                 and row.split(' ',1)[0].split('/',3)[-1]==branch]
        if not matches or any(oid!=commit for oid in matches):
            raise CatalogError('MIGRATION_HISTORY_REF_INVALID', [retained['ref']])
        self.migration=data
        if commit not in self.history_roots:self.history_roots.append(commit)

    def apply_supports(self, adapter_data):
        """Combine independently maintained adapter capabilities with protocol facts."""
        supported=validate_supports(adapter_data)
        for row in supported:self.resolve(row['agreement'])
        self.adapter_data=parse_json(json_bytes(adapter_data))
        for entry in self.entries:
            match=next((row for row in supported if row['agreement']==reference(entry)),None)
            if match and entry['kind']!='license':raise CatalogError('CATALOG_WORKFLOW_UNSUPPORTED')
            capabilities=['inspect']
            if match and entry['status']!='prepared':
                capabilities+=['verify'] if entry.get('withdrawal') else ['draft','adopt','verify']
            entry.update(renderer=match['adapter'] if match else None, capabilities=capabilities,
                         output_name=match['output_name'] if match else None)
        return self

    def _json(self,rel):
        try:
            content=self._read(rel)
            result=parse_json(content.decode('utf-8'))
        except (ValueError,OSError):raise CatalogError('CATALOG_FORMAT',[rel]) from None
        self.inputs[rel]=content
        return result

    def _blob(self,commit,path,expected):
        key=(commit,path,expected)
        if key in self._blob_cache:return self._blob_cache[key]
        if not valid_relative_path(path) or not isinstance(commit,str) or not gitstore.OID.fullmatch(commit):raise CatalogError('GIT_SOURCE_INVALID')
        if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise CatalogError('RELEASE_FORMAT')
        if self.bundled:
            p=safe_path(self.root,'objects/'+expected)
            try:content=p.read_bytes()
            except OSError:raise CatalogError('BUNDLE_OBJECT_MISSING',[expected]) from None
            if sha256(content)!=expected:raise CatalogError('BUNDLE_HASH_MISMATCH',[expected])
        else:content=gitstore.read_blob(self.root,commit,path,expected)
        self.blobs[expected]=content;self._blob_cache[key]=content
        return content

    def _add(self,m,rel,aid,version,bases):
        allowed={'format_version','agreement_id','version','sha256','display_name','kind','status','publication','files','source','notes','review','withdrawal'}
        if not isinstance(m,dict) or type(m.get('format_version')) is not int or m['format_version']!=2:raise CatalogError('RELEASE_FORMAT',[rel])
        required=allowed-{'notes','review','withdrawal'}
        if not required<=m.keys():raise CatalogError('RELEASE_FORMAT',[rel])
        if {'renderer','capabilities','output_name'}&m.keys():raise CatalogError('RELEASE_CONTAINS_ADAPTER_CONFIG',[rel])
        if m.keys()-allowed:raise CatalogError('RELEASE_FORMAT',[rel])
        ref=reference(m)
        if (aid,version)!=(ref['agreement_id'],ref['version']):raise CatalogError('RELEASE_IDENTITY_MISMATCH',[rel])
        if any(reference(e)==ref for e in self.entries):raise CatalogError('DUPLICATE_AGREEMENT_VERSION')
        if not all(isinstance(m.get(k),str) and m[k].strip() for k in ('display_name','kind')) or m.get('status') not in ('imported-draft','candidate','prepared','released'):
            raise CatalogError('RELEASE_FORMAT',[rel])
        source=m.get('source');files=m.get('files')
        if not isinstance(source,dict) or not {'commit','tag','files'}<=source.keys() or source.keys()-{'commit','tag','files','snapshot','source_sha256','target_version','provenance','metadata_sha256'}:
            raise CatalogError('RELEASE_FORMAT',[rel])
        if not isinstance(source['commit'],str) or not gitstore.OID.fullmatch(source['commit']):raise CatalogError('GIT_COMMIT_INVALID',[rel])
        if not isinstance(source['files'],dict) or not isinstance(files,dict) or files.get('text.txt')!=m['sha256']:
            raise CatalogError('RELEASE_FORMAT',[rel])
        if any(not valid_relative_path(k) or '/' in k or k=='release.json' or not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for k,v in files.items()):raise CatalogError('RELEASE_FORMAT',[rel])
        if source['tag'] is not None:
            if source['tag']!=f'{aid}/v{version}':raise CatalogError('GIT_TAG_INVALID',[rel])
            if not self.bundled:gitstore.verify_tag(self.root,source['tag'],source['commit'])
        elif m['status']!='imported-draft':raise CatalogError('GIT_TAG_MISSING',[rel])
        payload={}
        for name,path in source['files'].items():
            if name not in files:raise CatalogError('RELEASE_FILE_SET_MISMATCH',[rel])
            try:payload[name]=self._blob(source['commit'],path,files[name])
            except CatalogError as e:
                if e.code=='GIT_SOURCE_HASH_MISMATCH':raise CatalogError('AGREEMENT_TEXT_HASH_MISMATCH' if name=='text.txt' else 'RELEASE_FILE_HASH_MISMATCH',[rel]) from None
                raise
        if 'notes' in m:
            if not isinstance(m['notes'],str) or not m['notes'].strip() or 'notes.md' in payload:raise CatalogError('RELEASE_FORMAT',[rel])
            payload['notes.md']=m['notes'].encode('utf-8')
        if 'review' in m:
            if not isinstance(m['review'],dict) or 'review.json' in payload:raise CatalogError('RELEASE_FORMAT',[rel])
            payload['review.json']=json_bytes(m['review'])
        if set(payload)!=set(files):raise CatalogError('RELEASE_FILE_SET_MISMATCH',[rel])
        for name,content in payload.items():
            if sha256(content)!=files[name]:raise CatalogError('AGREEMENT_TEXT_HASH_MISMATCH' if name=='text.txt' else 'RELEASE_FILE_HASH_MISMATCH',[rel])
        body=payload['text.txt']
        try:body.decode('utf-8')
        except UnicodeError:raise CatalogError('CATALOG_TEXT_INVALID') from None
        if not body.strip():raise CatalogError('CATALOG_TEXT_INVALID')
        if m['status']!='imported-draft':
            self._review(m,rel,bases)
        elif any(k in m for k in ('review','notes')) or m.get('publication') is not None:
            raise CatalogError('RELEASE_FORMAT',[rel])
        self._publication(m,rel)
        withdrawal=m.get('withdrawal')
        if withdrawal is not None:
            if m['status']!='released' or not isinstance(withdrawal,dict) or set(withdrawal)!={'date','reason','confirmed_by'} or not all(isinstance(x,str) and x.strip() for x in withdrawal.values()):raise CatalogError('WITHDRAWAL_FORMAT',[rel])
            try:date.fromisoformat(withdrawal['date'])
            except ValueError:raise CatalogError('WITHDRAWAL_FORMAT',[rel]) from None
        payload['release.json']=json_bytes(m)
        self.entries.append(dict(m,_text=body,_payload=payload,_manifest=rel,
            _package_sha256=canonical_sha256({k:sha256(v) for k,v in payload.items()})))
        if not self.bundled:self._frozen_record(m,rel)

    def _frozen_record(self,m,rel):
        """Preserve every reachable committed fact, including merged side branches."""
        commits=self._history_commits(rel, self.history_roots)
        fixed={'format_version','agreement_id','version','sha256','display_name','kind','source','files','notes','review'}
        transitions={'imported-draft':{'imported-draft'},'candidate':{'candidate'},'prepared':{'prepared','released'},'released':{'released'}}
        for commit in commits:
            try:baseline=parse_json(gitstore.read_blob(self.root,commit,rel).decode('utf-8'))
            except CatalogError as error:
                if error.code=='GIT_SOURCE_UNAVAILABLE':continue  # A deletion has no record; earlier facts remain binding.
                raise
            except ValueError:raise CatalogError('REGISTRY_BASELINE_INVALID',[rel]) from None
            if not isinstance(baseline,dict) or baseline.get('format_version')!=2:raise CatalogError('REGISTRY_BASELINE_INVALID',[rel])
            if baseline.get('publication') is not None and not isinstance(baseline['publication'],dict):raise CatalogError('REGISTRY_BASELINE_INVALID',[rel])
            if any(baseline.get(k)!=m.get(k) for k in fixed):raise CatalogError('VERSION_IDENTITY_CHANGED',[rel])
            if m['status'] not in transitions.get(baseline.get('status'),set()):raise CatalogError('VERSION_STATE_CHANGED',[rel])
            if baseline.get('publication') is not None:
                before={k:v for k,v in baseline['publication'].items() if k not in ('state','receipt')}
                after={k:v for k,v in (m.get('publication') or {}).items() if k not in ('state','receipt')}
                if before!=after or ('receipt' in baseline['publication'] and baseline['publication']['receipt']!=m['publication'].get('receipt')):
                    raise CatalogError('PUBLICATION_RECORD_CONFLICT',[rel])
            if baseline.get('withdrawal') is not None and baseline['withdrawal']!=m.get('withdrawal'):raise CatalogError('WITHDRAWAL_RECORD_CHANGED',[rel])

    def _publication_bindings(self):
        from ..publication import _files, _archive
        for entry in self.entries:
            if entry['status'] not in ('prepared','released'):continue
            pub=entry['publication']
            candidates=[e for e in self.entries if e['agreement_id']==entry['agreement_id'] and e['status']=='candidate' and e['_package_sha256']==pub['decision']['candidate_sha256']]
            if len(candidates)!=1:raise CatalogError('PUBLICATION_CANDIDATE_MISSING',[entry['_manifest']])
            candidate=candidates[0]
            for key in ('sha256','display_name','kind','files','notes','review'):
                if candidate.get(key)!=entry.get(key):raise CatalogError('PUBLICATION_CANDIDATE_CHANGED',[entry['_manifest']])
            if {k:v for k,v in candidate['source'].items() if k!='tag'}!={k:v for k,v in entry['source'].items() if k!='tag'}:
                raise CatalogError('PUBLICATION_CANDIDATE_CHANGED',[entry['_manifest']])
            files=_files(candidate,entry['version'],pub['builder'],pub['decision'])
            if sha256(files['manifest.json'])!=pub['manifest_sha256']:raise CatalogError('PUBLICATION_MANIFEST_MISMATCH',[entry['_manifest']])
            if pub['method']=='github-release':files={f"{entry['agreement_id']}-{entry['version']}.zip":_archive(files)}
            if {k:sha256(v) for k,v in files.items()}!=pub['assets']:raise CatalogError('PUBLICATION_ASSET_CONFLICT',[entry['_manifest']])

    def _review(self,m,rel,bases):
        s=m['source'];r=m.get('review');snapshot=s.get('snapshot')
        if not {'review.json','adoption.md','notes.md'}<=m['files'].keys():raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
        try:validate_review_record(r)
        except CatalogError:raise CatalogError('RELEASE_REVIEW_INVALID',[rel]) from None
        if r['decision']!='approved' or r['open_issues'] or r['source_snapshot']!=snapshot or s.get('source_sha256')!=canonical_sha256(snapshot):raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
        a=snapshot['agreement'];fs=snapshot['files']
        if (a['agreement_id'],a['name'],a['kind'],a['target_version'])!=(m['agreement_id'],m['display_name'],m['kind'],s.get('target_version')):raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
        if m['status'] in ('prepared','released') and m['version']!=s['target_version']:raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
        if any(fs.get(k)!=m['files'][k] for k in ('text.txt','adoption.md')):raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
        prefix=f"agreements/{m['agreement_id']}/"
        if s['files']!={k:prefix+k for k in ('text.txt','adoption.md')}:raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
        try:
            metadata=parse_json(self._blob(s['commit'],prefix+'agreement.json',s.get('metadata_sha256')).decode('utf-8'))
        except (ValueError,UnicodeError):raise CatalogError('RELEASE_REVIEW_INVALID',[rel]) from None
        if not isinstance(metadata,dict) or metadata.get('review')!=r or any(metadata.get(k)!=a[k] for k in ('agreement_id','name','kind','purpose','target_version')) or metadata.get('base')!=a.get('base'):
            raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
        dependencies=snapshot.get('dependencies',{})
        if set(validate_review_dependencies(metadata.get('review_dependencies',[])))!=set(dependencies):raise CatalogError('RELEASE_REVIEW_INVALID',[rel])
        for path,expected in dependencies.items():self._blob(s['commit'],path,expected)
        if not self.bundled:
            validate_committed_review_files(self.root, s['commit'], m['agreement_id'], fs, paths=[rel])
        for name,expected in fs.items():self._blob(s['commit'],f"agreements/{m['agreement_id']}/{name}",expected)
        if a.get('base') is not None:bases.append((a['base'],rel))

    def _publication(self,m,rel):
        p=m.get('publication')
        if m['status'] not in ('prepared','released'):
            if p is not None:raise CatalogError('RELEASE_FORMAT',[rel])
            return
        if not isinstance(p,dict) or not {'state','method','channel','decision','builder','manifest_sha256','assets'}<=p.keys():raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
        if p.keys()-{'state','method','channel','decision','builder','manifest_sha256','assets','receipt'}:raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
        if p['state']!=('published' if m['status']=='released' else 'prepared') or p['method'] not in ('verified-local-directory','github-release') or not isinstance(p['channel'],str) or not p['channel'].strip():raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
        decision=p['decision']
        if not isinstance(decision,dict) or set(decision)!={'date','confirmed_by','basis','candidate_sha256'} or not all(isinstance(v,str) and v.strip() for v in decision.values()):raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
        try:date.fromisoformat(decision['date'])
        except ValueError:raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel]) from None
        if not re.fullmatch('[0-9a-f]{64}',decision['candidate_sha256']) or not isinstance(p['manifest_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',p['manifest_sha256']):raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
        if not isinstance(p['assets'],dict) or not p['assets'] or any(not valid_relative_path(k) or '/' in k or not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for k,v in p['assets'].items()):raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
        builder=p['builder']
        if not isinstance(builder,dict) or set(builder)!={'format_version','tool_version','implementation_sha256'} or type(builder.get('format_version')) is not int or builder['format_version']!=1 or not isinstance(builder.get('tool_version'),str) or not builder['tool_version'].strip() or not isinstance(builder.get('implementation_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',builder['implementation_sha256']):raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
        if m['status']=='released':
            receipt=p.get('receipt')
            if not isinstance(receipt,dict) or set(receipt)!={'verified_at','release_id','url','tag_commit','assets'} or receipt.get('tag_commit')!=m['source']['commit'] or receipt.get('assets')!=p['assets'] or not isinstance(receipt.get('verified_at'),str) or not receipt['verified_at'].strip() or not isinstance(receipt.get('url'),str) or not receipt['url'].strip():raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
            if p['method']=='github-release' and (type(receipt['release_id']) is not int or receipt['release_id']<=0):raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
            if p['method']=='verified-local-directory' and receipt['release_id'] is not None:raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])
        elif 'receipt' in p:raise CatalogError('RELEASE_PUBLICATION_REQUIRED',[rel])

    def export_files(self):
        files={e['_manifest']:self.inputs[e['_manifest']] for e in self.entries}
        for rel,m in self.agreements.items():
            files[rel]=json_bytes({k:m[k] for k in ('agreement_id','name','kind','recommended')})
        files['adapters.json']=json_bytes(self.adapter_data)
        files.update({'objects/'+sha:content for sha,content in self.blobs.items()})
        snapshot=canonical_sha256({p:sha256(content) for p,content in files.items()})
        files['BUNDLE.json']=json_bytes({'format_version':1,'snapshot_sha256':snapshot})
        return files

    def select(self, agreement_id, version):
        matches=[e for e in self.entries if (e['agreement_id'],e['version'])==(agreement_id,version)]
        if len(matches)!=1:raise CatalogError('AGREEMENT_NOT_AVAILABLE')
        return matches[0]

    def resolve(self,ref):
        validate_reference(ref);entry=self.select(ref['agreement_id'],ref['version'])
        if entry['sha256']!=ref['sha256']:raise CatalogError('AGREEMENT_PIN_MISMATCH')
        return entry

    def describe(self):
        return {'catalog_version':3,'recommended_by_agreement':self.recommended_by_agreement,
                'snapshot_scope':'bundled' if self.bundled else 'local-registry',
                'versions':[dict({k:v for k,v in e.items() if not k.startswith('_')},package_sha256=e['_package_sha256']) for e in self.entries]}
