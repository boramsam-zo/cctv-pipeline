"""Read the exact uploaded MP4 from the durable spool without advancing a job.

This module deliberately avoids Coordinator.load: recovery there can update
receipts. Source reads validate the queue identity and hash in bounded blocks.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat

from .common import MAX_VIDEO, WorkerError, checked_id, read_json

BLOCK_BYTES = 1024 * 1024


@contextmanager
def open_source(store, spool, identifier):
    identifier = checked_id(identifier)
    row = next((item for item in store.rows() if item['worker_job_id'] == identifier), None)
    if row is None:
        raise WorkerError('queue_job_not_found')
    directory = Path(spool) / checked_id(row['directory_id'])
    state_path, source_path = directory / 'state.json', directory / 'input.mp4'
    if directory.is_symlink() or state_path.is_symlink() or source_path.is_symlink():
        raise WorkerError('source_media_unsafe_path')
    if not directory.is_dir() or not state_path.is_file() or not source_path.is_file():
        raise WorkerError('source_media_not_found')
    state, saved = read_json(state_path), row['state']
    if (state.get('worker_job_id') != identifier or
            any(state.get(key) != saved.get(key) for key in
                ('worker_job_id', 'input_sha256', 'input_bytes'))):
        raise WorkerError('source_media_identity_mismatch')
    expected_size, expected_hash = state.get('input_bytes'), state.get('input_sha256')
    if (type(expected_size) is not int or not 0 < expected_size <= MAX_VIDEO or
            not isinstance(expected_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_hash)):
        raise WorkerError('source_media_identity_mismatch')
    try:
        descriptor = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise WorkerError('source_media_not_found') from None
    except OSError:
        raise WorkerError('source_media_unsafe_path') from None
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise WorkerError('source_media_identity_mismatch')
        sha, count = hashlib.sha256(), 0
        block = stream.read(BLOCK_BYTES)
        if block[4:8] != b'ftyp':
            raise WorkerError('source_media_invalid_mp4')
        while block:
            count += len(block)
            if count > expected_size:
                raise WorkerError('source_media_identity_mismatch')
            sha.update(block)
            block = stream.read(BLOCK_BYTES)
        after = os.fstat(stream.fileno())
        if (sha.hexdigest() != expected_hash or
                (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
            raise WorkerError('source_media_identity_mismatch')
        stream.seek(0)
        yield stream, expected_size
