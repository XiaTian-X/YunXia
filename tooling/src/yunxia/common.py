"""Shared data and filesystem primitives; independent of catalog layouts."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import PurePosixPath
import re
import tempfile


class CatalogError(Exception):
    def __init__(self, code, paths=None):
        self.code, self.paths = code, paths or []
        super().__init__(code)


@contextmanager
def exclusive_guard(path, *, error_type=CatalogError, code='LOCKED', paths=None):
    """Hold a nonblocking OS lock on a stable file for the whole operation.

    The guard must never be removed: PID records may be recovered or replaced,
    while this inode serializes both recovery and writers. Importing read-only
    catalog operations remains possible on platforms without POSIX flock.
    """
    try:
        import fcntl
    except ImportError:
        raise error_type('FILE_LOCK_UNSUPPORTED', paths) from None
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise error_type(code, paths) from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def canonical_sha256(value):
    return sha256(json_bytes(value))


def segment(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value)) and value not in ('.', '..')


def valid_relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value):
        return False
    p = PurePosixPath(value)
    return not p.is_absolute() and '..' not in p.parts and p.as_posix() == value and value != '.'


def safe_path(root, rel):
    if not valid_relative_path(rel) or root.is_symlink():
        raise CatalogError('CATALOG_PATH_INVALID')
    target = root
    for part in PurePosixPath(rel).parts:
        target = target / part
        if target.is_symlink():
            raise CatalogError('CATALOG_PATH_INVALID')
    return target


class DuplicateJSONKey(ValueError):
    pass


def parse_json(content):
    """Strict JSON parsing, with errors translated by each caller's interface."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJSONKey('Duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError('Non-finite JSON number')

    return json.loads(content, object_pairs_hook=unique, parse_constant=invalid_constant)


def read_json(path):
    try:
        return parse_json(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise CatalogError('CATALOG_FORMAT') from None


def _atomic_file(path, content, mode, *, replace):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.yunxia-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(name, path)
        else:
            os.link(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_write(path, content, mode=0o600):
    """Replace a file only after its complete contents have reached the filesystem."""
    _atomic_file(path, content, mode, replace=True)


def atomic_create(path, content, mode=0o600):
    """Publish a complete file atomically, without replacing an existing path."""
    _atomic_file(path, content, mode, replace=False)
