"""Explicit GitHub release transport. No implicit tag creation or asset replacement."""
import json
import base64
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote
from .common import CatalogError


class GitHub:
    def __init__(self, repository):
        if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise CatalogError('GITHUB_REPOSITORY_INVALID')
        self.repository = repository
        self.base = 'repos/' + repository

    def api(self, endpoint, method='GET', payload=None, missing=False, binary=False):
        command = ['gh', 'api', '--hostname', 'github.com', '--method', method, endpoint]
        if binary:
            command += ['-H', 'Accept: application/octet-stream']
        data = None
        if payload is not None:
            command += ['--input', '-']
            data = json.dumps(payload).encode()
        result = subprocess.run(command, input=data, capture_output=True)
        if result.returncode:
            try:
                error = json.loads(result.stdout)
            except (ValueError, UnicodeError):
                error = {}
            if missing and isinstance(error,dict) and str(error.get('status')) == '404':
                return None
            raise CatalogError('GITHUB_REQUEST_FAILED', [endpoint])
        if binary:
            return result.stdout
        try:
            response=json.loads(result.stdout)
            if not isinstance(response,(dict,list)):raise ValueError()
            return response
        except (ValueError, UnicodeError):
            raise CatalogError('GITHUB_RESPONSE_INVALID', [endpoint]) from None

    def verify_registry(self, commit, path, expected):
        record = self.api(self.base + '/contents/' + quote(path, safe='/') + '?ref=' + commit, missing=True)
        try:
            content = base64.b64decode(record['content'], validate=False) if isinstance(record,dict) and record.get('encoding') == 'base64' else None
        except (KeyError, ValueError, TypeError):
            content = None
        if content != expected:
            raise CatalogError('PUBLICATION_REGISTRY_NOT_PUSHED', [path])

    def tag_commit(self, tag):
        result = self.api(self.base + '/git/ref/tags/' + quote(tag, safe='/'), missing=True)
        if result is None:
            return None
        if not isinstance(result,dict):raise CatalogError('GITHUB_RESPONSE_INVALID')
        obj = result.get('object', {})
        for _ in range(8):
            if not isinstance(obj,dict):raise CatalogError('GITHUB_RESPONSE_INVALID')
            if obj.get('type') == 'commit':
                return obj.get('sha')
            if obj.get('type') != 'tag' or not re.fullmatch(r'[0-9a-f]{40,64}', str(obj.get('sha'))):
                break
            record=self.api(self.base + '/git/tags/' + obj['sha'])
            if not isinstance(record,dict):raise CatalogError('GITHUB_RESPONSE_INVALID')
            obj=record.get('object', {})
        raise CatalogError('GITHUB_TAG_INVALID')

    def ensure_tag(self, tag, commit):
        actual = self.tag_commit(tag)
        if actual is None:
            # The source object must already have been pushed by the maintainer.
            if self.api(self.base + '/git/commits/' + commit, missing=True) is None:
                raise CatalogError('PUBLICATION_SOURCE_NOT_PUSHED')
            self.api(self.base + '/git/refs', 'POST', {'ref': 'refs/tags/' + tag, 'sha': commit})
            actual = self.tag_commit(tag)
        if actual != commit:
            raise CatalogError('PUBLICATION_TAG_CONFLICT')

    def release(self, tag):
        # Listing also sees draft releases, which the tags endpoint may omit.
        page = 1
        while True:
            entries = self.api(self.base + f'/releases?per_page=100&page={page}')
            if not isinstance(entries, list) or any(not isinstance(row,dict) for row in entries):
                raise CatalogError('GITHUB_RESPONSE_INVALID')
            matches = [row for row in entries if row.get('tag_name') == tag]
            if len(matches) > 1:
                raise CatalogError('PUBLICATION_RELEASE_CONFLICT')
            if matches:
                return matches[0]
            if len(entries) < 100:
                return None
            page += 1

    def create_draft(self, tag, commit, title, notes):
        return self.api(self.base + '/releases', 'POST', {
            'tag_name': tag, 'target_commitish': commit, 'name': title,
            'body': notes, 'draft': True, 'prerelease': False, 'make_latest': 'false'})

    def assets(self, release):
        result = []
        page = 1
        while True:
            rows = self.api(self.base + f"/releases/{release['id']}/assets?per_page=100&page={page}")
            if not isinstance(rows, list) or any(not isinstance(row,dict) for row in rows):
                raise CatalogError('GITHUB_RESPONSE_INVALID')
            result.extend(rows)
            if len(rows) < 100:
                return result
            page += 1

    def download(self, asset):
        return self.api(self.base + f"/releases/assets/{asset['id']}", binary=True)

    def upload(self, tag, name, content):
        with tempfile.TemporaryDirectory(prefix='yunxia-upload-') as folder:
            path = Path(folder)/name
            path.write_bytes(content)
            result = subprocess.run(['gh', 'release', 'upload', tag, str(path), '--repo', 'github.com/'+self.repository], capture_output=True)
            if result.returncode:
                # An upload may already have succeeded. The caller must query on retry.
                raise CatalogError('GITHUB_UPLOAD_UNCERTAIN', [name])

    def publish(self, release):
        return self.api(self.base + f"/releases/{release['id']}", 'PATCH', {'draft': False, 'make_latest': 'false'})
