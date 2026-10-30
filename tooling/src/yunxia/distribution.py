"""Reproducible standalone Skill builds and recoverable local installation."""
from pathlib import Path
import os
import shutil
import tempfile
import re
from .catalog import Catalog, CatalogError, read_json, safe_path, sha256
from .lifecycle import encoded
from . import VERSION


def source_signature(root):
    paths=set(Catalog(root).paths)
    for folder in ('skills/yunxia','tooling/src/yunxia'):
        for p in safe_path(root,folder).rglob('*'):
            if '__pycache__' in p.parts or p.name=='.DS_Store' or p.suffix in ('.pyc','.pyo'):continue
            if p.is_symlink():raise CatalogError('DISTRIBUTION_SYMLINK',[str(p)])
            if p.is_file():paths.add(p.relative_to(root).as_posix())
    return sha256(encoded({p:sha256(safe_path(root,p).read_bytes()) for p in sorted(paths)}))


def inventory(root):
    result = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink(): raise CatalogError('DISTRIBUTION_SYMLINK',[str(p)])
        if p.is_file() and p != root/'BUILD.json':
            result[p.relative_to(root).as_posix()] = sha256(p.read_bytes())
    return result


def validate(package):
    manifest = read_json(safe_path(package,'BUILD.json'))
    if not isinstance(manifest,dict) or type(manifest.get('format_version')) is not int or manifest['format_version'] != 1 or not isinstance(manifest.get('tool_version'), str) or not manifest['tool_version'].strip() or not isinstance(manifest.get('source_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', manifest['source_sha256']):
        raise CatalogError('DISTRIBUTION_FORMAT')
    if manifest.get('files') != inventory(package):
        raise CatalogError('DISTRIBUTION_HASH_MISMATCH')
    Catalog(safe_path(package,'assets/catalog'))
    return manifest


def activate(stage, target):
    if target.is_symlink(): raise CatalogError('DISTRIBUTION_SYMLINK')
    backup = stage.parent/'previous'
    if target.exists(): target.rename(backup)
    try:
        stage.rename(target)
    except BaseException:
        if backup.exists():
            try: backup.rename(target)
            except BaseException:
                raise CatalogError('BUILD_RECOVERY_REQUIRED',[str(backup)]) from None
        raise
    return backup


def build(root):
    before=source_signature(root)
    catalog = Catalog(root)
    target = safe_path(root,'dist/skills/yunxia')
    target.parent.mkdir(parents=True,exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.yunxia-build-',dir=target.parent))
    stage = work/'next';stage.mkdir()
    preserve = False
    try:
        shutil.copytree(root/'skills/yunxia',stage,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
        scripts=stage/'scripts';scripts.mkdir(exist_ok=True)
        shutil.copytree(root/'tooling/src/yunxia',scripts/'yunxia',ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
        (scripts/'yunxia.py').write_text('import sys\nfrom yunxia.cli import main\nif __name__ == "__main__":\n    sys.exit(main())\n')
        data=stage/'assets/catalog';data.mkdir(parents=True)
        for rel in catalog.paths:
            path=data/rel;path.parent.mkdir(parents=True,exist_ok=True)
            if rel.startswith('agreements/'):
                m=read_json(safe_path(root,rel))
                path.write_bytes(encoded({k:m[k] for k in ('agreement_id','name','kind','recommended')}))
            else:
                shutil.copy2(safe_path(root,rel),path)
        Catalog(data)
        (stage/'BUILD.json').write_bytes(encoded({'format_version':1,'tool_version':VERSION,'source_sha256':before,'files':inventory(stage)}))
        validate(stage)
        if before!=source_signature(root):raise CatalogError('BUILD_SOURCE_CHANGED')
        if target.exists() and inventory(target)==inventory(stage) and (target/'BUILD.json').read_bytes()==(stage/'BUILD.json').read_bytes():
            return {'built':str(target),'changed':False,'tool_version':VERSION}
        try: activate(stage,target)
        except CatalogError as error:
            preserve=error.code=='BUILD_RECOVERY_REQUIRED'
            raise
        return {'built':str(target),'changed':True,'tool_version':VERSION}
    finally:
        if not preserve: shutil.rmtree(work)


def install(root, destination, update=False):
    if destination.is_symlink():
        raise CatalogError('INSTALL_PATH_INVALID')
    destination=destination.parent.resolve()/destination.name
    root=root.resolve()
    if destination == root or destination in root.parents or root in destination.parents:
        raise CatalogError('INSTALL_SOURCE_OVERLAP')
    if destination.exists() and not update:raise CatalogError('INSTALL_EXISTS_USE_UPDATE')
    if destination.exists():
        if not destination.is_dir():
            raise CatalogError('INSTALL_TARGET_NOT_SKILL')
        if any(destination.iterdir()):
            instruction=safe_path(destination,'SKILL.md')
            if not instruction.is_file() or instruction.stat().st_size > 256*1024:
                raise CatalogError('INSTALL_TARGET_NOT_SKILL')
            frontmatter=instruction.read_text(encoding='utf-8').split('---',2)
            if len(frontmatter)<3 or frontmatter[0].strip() or not re.search(r'''(?m)^name:\s*["']?yunxia["']?\s*$''',frontmatter[1]):
                raise CatalogError('INSTALL_TARGET_NOT_SKILL')
    package=safe_path(root,'dist/skills/yunxia');manifest=validate(package)
    if manifest.get('source_sha256')!=source_signature(root):raise CatalogError('BUILD_REQUIRED')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.yunxia-install-',dir=destination.parent))
    backup=None
    try:
        candidate=temp/'next';shutil.copytree(package,candidate);validate(candidate)
        if manifest['source_sha256'] != source_signature(root):
            raise CatalogError('INSTALL_SOURCE_CHANGED')
        if destination.exists():
            backup_root=safe_path(destination.parent.parent,'skill-backups');backup_root.mkdir(exist_ok=True)
            backup=Path(tempfile.mkdtemp(prefix='yunxia-',dir=backup_root))/'yunxia'
            destination.rename(backup)
        activated=False
        try:
            candidate.rename(destination)
            activated=True
            validate(destination)
        except BaseException:
            failed=None
            if activated:
                backup_root=safe_path(destination.parent.parent,'skill-backups');backup_root.mkdir(exist_ok=True)
                failed=Path(tempfile.mkdtemp(prefix='yunxia-failed-',dir=backup_root))/'yunxia'
                try:destination.rename(failed)
                except BaseException:
                    raise CatalogError('INSTALL_RECOVERY_REQUIRED',[str(backup),str(destination)]) from None
            if backup:
                try:backup.rename(destination)
                except BaseException:raise CatalogError('INSTALL_RECOVERY_REQUIRED',[str(backup)]) from None
            if failed:raise CatalogError('INSTALL_VERIFICATION_FAILED',[str(failed)]) from None
            raise
        return {'installed':str(destination),'previous_install_backup':str(backup) if backup else None,'tool_version':VERSION}
    finally:
        shutil.rmtree(temp)
