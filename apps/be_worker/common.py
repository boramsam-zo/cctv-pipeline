"""Bounded IO, private receipts and one-writer job locking (macOS/Linux)."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.parse

VERSION = 'be-runpod-v1'
MAX_VIDEO = 200 * 1024 * 1024
JSON_LIMIT = 2 * 1024 * 1024


class WorkerError(Exception):
    """Only application-generated codes, never raw HTTP bodies or credentials."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse(data):
    def invalid(_):
        raise WorkerError('invalid_json')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WorkerError('invalid_json')
            result[key] = value
        return result
    try:
        return json.loads(data, parse_constant=invalid, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise WorkerError('invalid_json') from exc


def read_json(path):
    with Path(path).open('rb') as stream:
        data = stream.read(JSON_LIMIT + 1)
    if len(data) > JSON_LIMIT:
        raise WorkerError('json_too_large')
    return parse(data)


def save(path, value):
    """Atomic, fsynced receipt replacement; originals are separate immutable inputs."""
    path = Path(path)
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    fd, temp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def locked(directory):
    with (Path(directory) / '.lock').open('a') as stream:
        os.chmod(stream.name, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkerError('job_already_locked') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def checked_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise WorkerError('invalid_provider_job_id')
    return value


def checked_url(value):
    if not isinstance(value, str):
        raise WorkerError('invalid_runpod_url')
    url = urllib.parse.urlsplit(value)
    if (url.username or url.password or url.query or url.fragment or not url.hostname
            or url.path not in ('', '/') or any(c.isspace() for c in value)
            or not (url.scheme == 'https' or
                    (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost')))):
        raise WorkerError('invalid_runpod_url')
    return value.rstrip('/')


def config_checked(config):
    if not isinstance(config, dict) or set(config) != {'runpod_url', 's3_bucket', 's3_region'}:
        raise WorkerError('invalid_config_fields')
    bucket = config['s3_bucket']
    if not isinstance(bucket, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
        raise WorkerError('invalid_bucket')
    if config['s3_region'] != 'ap-northeast-2':
        raise WorkerError('expected_seoul_region')
    return dict(config, runpod_url=checked_url(config['runpod_url']))
