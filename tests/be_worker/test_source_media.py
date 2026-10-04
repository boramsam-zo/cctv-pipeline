"""Durable upload reads: synthetic bytes, no queue writes or provider calls."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import source_media, worker
from apps.be_worker.common import WorkerError, save


class SourceMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / ('b' * 32)
        self.raw = b'0000ftyp-synthetic-full-upload' + b'0' * (source_media.BLOCK_BYTES + 100)
        original = self.root / 'uploaded.mp4'
        original.write_bytes(self.raw)
        self.state = worker.create_job(original, self.directory,
            {'runpod_url': 'https://example.invalid', 's3_bucket': 'test-bucket',
             's3_region': 'ap-northeast-2'})
        self.row = {'worker_job_id': self.state['worker_job_id'],
                    'directory_id': self.directory.name, 'state': copy.deepcopy(self.state)}
        self.store = Mock()
        self.store.rows.return_value = [self.row]

    def read(self):
        with source_media.open_source(self.store, self.root, self.state['worker_job_id']) as (stream, size):
            self.assertEqual(size, len(self.raw))
            return stream.read()

    def test_exact_original_bounded_hash_and_no_receipt_or_queue_write(self):
        before = (self.directory / 'state.json').read_bytes()
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('No full-file byte buffer')):
            self.assertEqual(self.read(), self.raw)
        self.assertEqual((self.directory / 'state.json').read_bytes(), before)
        self.assertEqual(self.row['state'], self.state)
        self.store.write.assert_not_called()
        self.store.connection.execute.assert_not_called()

    def test_unknown_job_and_missing_upload_are_explicit(self):
        with self.assertRaisesRegex(WorkerError, 'queue_job_not_found'):
            with source_media.open_source(self.store, self.root, 'a' * 32):
                pass
        (self.directory / 'input.mp4').unlink()
        with self.assertRaisesRegex(WorkerError, 'source_media_not_found'):
            self.read()

    def test_source_tampering_same_size_is_rejected(self):
        (self.directory / 'input.mp4').write_bytes(self.raw[:-1] + b'1')
        with self.assertRaisesRegex(WorkerError, 'source_media_identity_mismatch'):
            self.read()

    def test_source_size_and_saved_queue_identity_are_checked(self):
        self.row['state']['input_sha256'] = '0' * 64
        with self.assertRaisesRegex(WorkerError, 'source_media_identity_mismatch'):
            self.read()
        self.row['state'] = copy.deepcopy(self.state)
        (self.directory / 'input.mp4').write_bytes(self.raw + b'0')
        with self.assertRaisesRegex(WorkerError, 'source_media_identity_mismatch'):
            self.read()

    def test_invalid_mp4_cannot_be_returned_even_with_matching_hash(self):
        raw = b'\xff\xd8\xff-image-is-not-source-mp4'
        self.state.update(input_bytes=len(raw), input_sha256=hashlib.sha256(raw).hexdigest())
        self.row['state'] = copy.deepcopy(self.state)
        save(self.directory / 'state.json', self.state)
        (self.directory / 'input.mp4').write_bytes(raw)
        with self.assertRaisesRegex(WorkerError, 'source_media_invalid_mp4'):
            self.read()

    def test_input_and_receipt_symlinks_are_rejected(self):
        for name in ('input.mp4', 'state.json'):
            with self.subTest(name=name):
                path = self.directory / name
                target = self.root / ('outside-' + name)
                path.rename(target)
                path.symlink_to(target)
                try:
                    with self.assertRaisesRegex(WorkerError, 'source_media_unsafe_path'):
                        self.read()
                finally:
                    path.unlink()
                    target.rename(path)

    def test_directory_symlink_and_directory_traversal_are_rejected(self):
        target = self.root / 'outside'
        self.directory.rename(target)
        self.directory.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(WorkerError, 'source_media_unsafe_path'):
            self.read()
        self.row['directory_id'] = '../outside'
        with self.assertRaisesRegex(WorkerError, 'invalid_provider_job_id'):
            self.read()
        with self.assertRaisesRegex(WorkerError, 'invalid_provider_job_id'):
            with source_media.open_source(self.store, self.root, '../outside'):
                pass


if __name__ == '__main__':
    unittest.main()
