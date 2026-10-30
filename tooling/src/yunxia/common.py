"""Shared data and filesystem primitives; independent of catalog layouts."""
import hashlib
import json
from pathlib import PurePosixPath
import re


class CatalogError(Exception):
    def __init__(self, code, paths=None):
        self.code, self.paths = code, paths or []
        super().__init__(code)


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


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CatalogError('CATALOG_FORMAT')
            result[key] = value
        return result

    def invalid_constant(_):
        raise CatalogError('CATALOG_FORMAT')

    try:
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique,
                          parse_constant=invalid_constant)
    except (OSError, ValueError):
        raise CatalogError('CATALOG_FORMAT') from None
