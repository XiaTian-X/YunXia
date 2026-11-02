"""Reproducible standalone Skill builds and recoverable local installation."""
from pathlib import Path
import os
import shutil
import stat
import tempfile
import re
from .catalog import Catalog
from .common import CatalogError, read_json, safe_path, sha256, json_bytes as encoded, canonical_sha256, valid_relative_path, atomic_write, exclusive_guard
from .sources import load_catalog
from . import VERSION


def source_snapshot(root):
    """Capture the exact current inputs and Git-resolved catalog before copying."""
    catalog = load_catalog(root)
    inputs = dict(catalog.source_inputs)
    inputs['tool/sources.lock.json'] = catalog.lock_bytes
    payload = {}

    def add_input(rel, content):
        if rel in inputs and inputs[rel] != content:
            raise CatalogError('BUILD_SOURCE_CHANGED', [rel])
        inputs[rel] = content

    def add_payload(rel, content):
        if rel in payload:
            raise CatalogError('DISTRIBUTION_PATH_CONFLICT', [rel])
        payload[rel] = content

    for folder, destination in (('skills/yunxia', ''), ('tooling/src/yunxia', 'scripts/yunxia/')):
        source = safe_path(root, folder)
        if not source.is_dir():
            raise CatalogError('DISTRIBUTION_SOURCE_MISSING', [folder])
        for p in sorted(source.rglob('*')):
            if '__pycache__' in p.parts or p.name == '.DS_Store' or p.suffix in ('.pyc', '.pyo'):
                continue
            if p.is_symlink():
                raise CatalogError('DISTRIBUTION_SYMLINK', [str(p)])
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                content = safe_path(root, rel).read_bytes()
                add_input('tool/' + rel, content)
                # The offline catalog owns the single bundled adapter registry.
                if rel != 'tooling/src/yunxia/adapters/support.json':
                    add_payload(destination + p.relative_to(source).as_posix(), content)
    add_payload('scripts/yunxia.py', b'import sys\nfrom yunxia.cli import main\nif __name__ == "__main__":\n    sys.exit(main())\n')
    if 'SKILL.md' not in payload or 'scripts/yunxia/cli.py' not in payload:
        raise CatalogError('DISTRIBUTION_SOURCE_MISSING')
    for rel, content in catalog.export_files().items():
        add_payload('assets/catalog/' + rel, content)
    add_payload('assets/SOURCES.json', encoded(catalog.source_pins))
    input_hashes = {rel: sha256(content) for rel, content in sorted(inputs.items())}
    output_hashes = {rel: sha256(content) for rel, content in sorted(payload.items())}
    return {'inputs': input_hashes, 'files': payload, 'sources':catalog.source_pins,
            'source_sha256': canonical_sha256({'inputs': input_hashes, 'outputs': output_hashes})}


def source_signature(root):
    return source_snapshot(root)['source_sha256']


def write_files(root, files):
    """Write only captured bytes; never re-read a mutable source during copying."""
    for rel, content in sorted(files.items()):
        path = safe_path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def inventory(root):
    result = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink(): raise CatalogError('DISTRIBUTION_SYMLINK',[str(p)])
        if p.is_file() and p != root/'BUILD.json':
            result[p.relative_to(root).as_posix()] = sha256(p.read_bytes())
    return result


def validate(package):
    manifest = read_json(safe_path(package,'BUILD.json'))
    if not isinstance(manifest,dict) or type(manifest.get('format_version')) is not int or manifest['format_version'] != 2 or not isinstance(manifest.get('tool_version'), str) or not manifest['tool_version'].strip() or not isinstance(manifest.get('source_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', manifest['source_sha256']):
        raise CatalogError('DISTRIBUTION_FORMAT')
    for key in ('inputs', 'files'):
        if not isinstance(manifest.get(key), dict) or any(not valid_relative_path(p) or not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for p, h in manifest[key].items()):
            raise CatalogError('DISTRIBUTION_FORMAT')
    if manifest.get('files') != inventory(package):
        raise CatalogError('DISTRIBUTION_HASH_MISMATCH')
    if manifest['source_sha256'] != canonical_sha256({'inputs': manifest['inputs'], 'outputs': manifest['files']}):
        raise CatalogError('DISTRIBUTION_SOURCE_MISMATCH')
    if manifest.get('sources') != read_json(safe_path(package,'assets/SOURCES.json')):
        raise CatalogError('DISTRIBUTION_SOURCE_MISMATCH')
    Catalog(safe_path(package,'assets/catalog'))
    return manifest


def activate(stage, target):
    if target.is_symlink(): raise CatalogError('DISTRIBUTION_SYMLINK')
    previous = stage.parent/'previous'
    try:
        if target.exists(): target.rename(previous)
        stage.rename(target)
    except BaseException:
        if previous.exists():
            if target.exists():
                raise CatalogError('BUILD_RECOVERY_REQUIRED',[str(previous),str(target)]) from None
            try: previous.rename(target)
            except BaseException:
                raise CatalogError('BUILD_RECOVERY_REQUIRED',[str(previous)]) from None
        raise
    return previous


def build(root):
    snapshot = source_snapshot(root)
    target = safe_path(root, 'dist/skills/yunxia')
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.yunxia-build-', dir=target.parent))
    stage = work/'next'; stage.mkdir()
    preserve = False
    try:
        write_files(stage, snapshot['files'])
        expected = {rel: sha256(content) for rel, content in snapshot['files'].items()}
        if inventory(stage) != expected:
            raise CatalogError('BUILD_OUTPUT_MISMATCH')
        Catalog(safe_path(stage, 'assets/catalog'))
        manifest = {'format_version': 2, 'tool_version': VERSION,
                    'source_sha256': snapshot['source_sha256'],
                    'sources':snapshot['sources'],
                    'inputs': snapshot['inputs'], 'files': expected}
        (stage/'BUILD.json').write_bytes(encoded(manifest))
        validate(stage)
        if snapshot['source_sha256'] != source_signature(root):
            raise CatalogError('BUILD_SOURCE_CHANGED')
        if target.exists() and inventory(target) == expected and (target/'BUILD.json').read_bytes() == encoded(manifest):
            return {'built': str(target), 'changed': False, 'tool_version': VERSION}
        try:
            activate(stage, target)
        except CatalogError as error:
            preserve = error.code == 'BUILD_RECOVERY_REQUIRED'
            raise
        return {'built': str(target), 'changed': True, 'tool_version': VERSION}
    finally:
        if not preserve:
            shutil.rmtree(work)


def _installation_image(root):
    """Capture an installed tree without following links outside that tree."""
    if not root.exists() and not root.is_symlink():
        return None
    if root.is_symlink() or not root.is_dir():
        raise CatalogError('INSTALL_PATH_INVALID', [str(root)])
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        before = path.lstat()
        mode = stat.S_IMODE(before.st_mode)
        if stat.S_ISLNK(before.st_mode):
            value = ('link', mode, os.readlink(path))
        elif stat.S_ISREG(before.st_mode):
            flags=os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0)
            try:
                fd=os.open(path,flags)
                with os.fdopen(fd,'rb') as stream:
                    opened=os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):
                        raise CatalogError('INSTALL_TARGET_CHANGED',[str(root)])
                    content=stream.read()
                    finished=os.fstat(stream.fileno())
                if (opened.st_size,opened.st_mode,opened.st_mtime_ns)!=(finished.st_size,finished.st_mode,finished.st_mtime_ns):
                    raise CatalogError('INSTALL_TARGET_CHANGED',[str(root)])
            except OSError:
                raise CatalogError('INSTALL_TARGET_CHANGED',[str(root)]) from None
            value = ('file', mode, sha256(content))
        elif stat.S_ISDIR(before.st_mode):
            value = ('directory', mode)
        else:
            raise CatalogError('INSTALL_TARGET_NOT_SKILL', [str(path)])
        after = path.lstat()
        if ((before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns)):
            raise CatalogError('INSTALL_TARGET_CHANGED', [str(root)])
        result[path.relative_to(root).as_posix()] = value
    return result


def install(root, destination, update=False):
    if destination.is_symlink():
        raise CatalogError('INSTALL_PATH_INVALID')
    destination=destination.parent.resolve()/destination.name
    root=root.resolve()
    if destination == root or destination in root.parents or root in destination.parents:
        raise CatalogError('INSTALL_SOURCE_OVERLAP')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # A source-repository lock cannot serialize installs from another checkout.
    guard=safe_path(destination.parent,'.yunxia-install.guard')
    with exclusive_guard(guard, code='INSTALL_LOCKED', paths=[str(destination)]):
        return _install(root,destination,update)


def _install(root, destination, update):
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
    before_target=_installation_image(destination)
    package=safe_path(root,'dist/skills/yunxia');manifest=validate(package)
    snapshot=source_snapshot(root)
    if manifest['source_sha256']!=snapshot['source_sha256'] or manifest['inputs']!=snapshot['inputs'] or manifest['files']!={rel:sha256(content) for rel,content in snapshot['files'].items()}:
        raise CatalogError('BUILD_REQUIRED')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.yunxia-install-',dir=destination.parent))
    previous=None
    preserve=False
    try:
        candidate=temp/'next'; candidate.mkdir()
        # Pin the original manifest, including its own bytes. A replacement package
        # during installation must not be accepted just because it is self-consistent.
        files = {rel: safe_path(package, rel).read_bytes() for rel in manifest['files']}
        if any(sha256(content) != manifest['files'][rel] for rel, content in files.items()):
            raise CatalogError('DISTRIBUTION_HASH_MISMATCH')
        files['BUILD.json'] = encoded(manifest)
        write_files(candidate, files)
        if validate(candidate) != manifest:
            raise CatalogError('DISTRIBUTION_HASH_MISMATCH')
        if manifest['source_sha256'] != source_signature(root):
            raise CatalogError('INSTALL_SOURCE_CHANGED')
        if _installation_image(destination) != before_target:
            raise CatalogError('INSTALL_TARGET_CHANGED',[str(destination)])
        had_previous=destination.exists()
        previous=temp/'previous' if had_previous else None
        atomic_write(temp/'transaction.json', encoded({'format_version':1,
                     'destination':str(destination), 'had_previous':had_previous,
                     'source_sha256':manifest['source_sha256']}))
        # From the first filesystem mutation onwards, an asynchronous exception
        # must retain the only original tree until restoration is confirmed.
        preserve=True
        try:
            if previous is not None:
                destination.rename(previous)
                if _installation_image(previous) != before_target:
                    raise CatalogError('INSTALL_TARGET_CHANGED',[str(destination)])
            elif destination.exists() or destination.is_symlink():
                raise CatalogError('INSTALL_TARGET_CHANGED',[str(destination)])
            candidate.rename(destination)
            validate(destination)
            if previous is not None and _installation_image(previous) != before_target:
                raise CatalogError('INSTALL_TARGET_CHANGED',[str(previous)])
        except BaseException as error:
            failed=None
            # A rename may have completed and then raised (e.g. SIGINT). Inspect
            # paths rather than assuming a Python flag was assigned afterwards.
            if not candidate.exists():
                try:
                    failed=temp/'failed'
                    destination.rename(failed)
                except BaseException:
                    paths=([str(previous)] if previous is not None and previous.exists() else [])+[str(destination),str(temp)]
                    raise CatalogError('INSTALL_RECOVERY_REQUIRED',paths) from None
            if previous is not None and previous.exists():
                try:
                    if destination.exists():raise OSError('Installation target changed during rollback')
                    previous.rename(destination)
                except BaseException:
                    raise CatalogError('INSTALL_RECOVERY_REQUIRED',[str(previous),str(destination),str(temp)]) from None
            if had_previous and not destination.exists():
                raise CatalogError('INSTALL_RECOVERY_REQUIRED',[str(destination),str(temp)]) from None
            if failed:
                if isinstance(error,CatalogError) and error.code=='INSTALL_TARGET_CHANGED':
                    raise CatalogError('INSTALL_TARGET_CHANGED',[str(destination),str(failed)]) from None
                raise CatalogError('INSTALL_VERIFICATION_FAILED',[str(failed)]) from None
            preserve=False
            raise
        preserve=False
        return {'installed':str(destination),'transaction_cleaned':True,'tool_version':VERSION}
    finally:
        if not preserve:
            shutil.rmtree(temp)
