"""Hash-pinned private S3 model cache; no SDK import or network on import.

The service resolves a trusted model catalog before calling this module. Model
jobs consume the returned local path with network access disabled. A failed or
interrupted download cannot be mistaken for a model, and existing conflicting
bytes are preserved for inspection rather than overwritten.
"""
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile


def _plain(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('model_cache_requires_absolute_path')
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('model_cache_symlink_rejected')
    return path


def _identity(sha256, size):
    if (not isinstance(sha256, str) or not re.fullmatch('[a-f0-9]{64}', sha256)
            or type(size) is not int or not 0 < size <= 1024 ** 3):
        raise ValueError('model_artifact_identity_invalid')


def verify_file(path, sha256, size):
    _identity(sha256, size)
    path = _plain(path)
    if not path.is_file() or path.stat().st_size != size:
        raise ValueError('model_artifact_size_mismatch')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != sha256:
        raise ValueError('model_artifact_sha256_mismatch')
    return path


def ensure_cached_artifact(client, *, bucket, key, sha256, size, cache_root, version_id=None):
    """Read one explicit S3 object, bound its bytes, then atomically publish it.

    ``client`` is injected (normally boto3 S3), with its timeout/retry policy
    owned by the caller. No bucket listing, public URL, fallback, automatic
    model execution, or cleanup of another run's partial files is performed.
    """
    _identity(sha256, size)
    if (not isinstance(bucket, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)
            or not isinstance(key, str) or not key or len(key) > 1024
            or PurePosixPath(key).is_absolute() or '..' in PurePosixPath(key).parts
            or '\\' in key or any(ord(c) < 32 for c in key)):
        raise ValueError('model_s3_location_invalid')
    if version_id is not None and (not isinstance(version_id, str) or not version_id
                                   or len(version_id) > 1024):
        raise ValueError('model_s3_version_invalid')
    root = _plain(cache_root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _plain(root)
    target = root / (sha256 + '.pt')
    lock_path = _plain(root / (sha256 + '.lock'))
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    temporary = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if target.exists() or target.is_symlink():
            return verify_file(target, sha256, size)
        kwargs = {'Bucket': bucket, 'Key': key}
        if version_id is not None:
            kwargs['VersionId'] = version_id
        response = client.get_object(**kwargs)
        body = response['Body']
        try:
            if response.get('ContentLength') != size:
                raise ValueError('model_s3_size_mismatch')
            fd, name = tempfile.mkstemp(prefix=sha256 + '.', suffix='.partial', dir=root)
            temporary = Path(name)
            digest, received = hashlib.sha256(), 0
            with os.fdopen(fd, 'wb') as output:
                while True:
                    block = body.read(min(1024 * 1024, size - received + 1))
                    if not block:
                        break
                    received += len(block)
                    if received > size:
                        raise ValueError('model_s3_body_exceeds_identity')
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if received != size or digest.hexdigest() != sha256:
                raise ValueError('model_s3_content_mismatch')
            temporary.chmod(0o400)
            # No-replace publication: even a noncooperating concurrent writer
            # cannot have its target overwritten by this operation.
            try:
                os.link(temporary, target)
            except FileExistsError:
                verify_file(target, sha256, size)
            root_fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(root_fd)
            finally:
                os.close(root_fd)
        finally:
            body.close()
        return verify_file(target, sha256, size)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        os.close(descriptor)
