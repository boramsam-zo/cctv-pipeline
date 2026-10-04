"""Private-model cache tests with synthetic bytes and a fake S3 client."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from apps.runpod_image import model_artifact


class S3:
    def __init__(self, content, declared=None):
        self.content, self.declared, self.calls = content, declared, []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        self.body = io.BytesIO(self.content)
        return {'Body': self.body, 'ContentLength': len(self.content) if self.declared is None else self.declared}


class ModelArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'cache'
        self.data = b'synthetic model bytes, not a checkpoint'
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.client = S3(self.data)

    def ensure(self, **kwargs):
        settings = dict(bucket='private-test-models', key='models/r2/epoch7.pt',
                        sha256=self.sha, size=len(self.data), cache_root=self.root)
        settings.update(kwargs)
        return model_artifact.ensure_cached_artifact(self.client, **settings)

    def test_content_verified_once_and_reused_without_network(self):
        path = self.ensure(version_id='synthetic-version')
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        self.assertEqual(self.ensure(), path)
        self.assertEqual(self.client.calls, [{'Bucket': 'private-test-models',
            'Key': 'models/r2/epoch7.pt', 'VersionId': 'synthetic-version'}])
        self.assertTrue(self.client.body.closed)
        self.assertFalse(list(self.root.glob('*.partial')))

    def test_wrong_hash_or_short_or_oversize_never_publishes(self):
        for content in (b'x' * len(self.data), self.data[:-1], self.data + b'x'):
            with self.subTest(content_length=len(content)):
                self.client = S3(content, declared=len(self.data))
                with self.assertRaises(ValueError):
                    self.ensure()
                self.assertFalse((self.root / (self.sha + '.pt')).exists())
                self.assertFalse(list(self.root.glob('*.partial')))
                self.assertTrue(self.client.body.closed)

    def test_existing_conflicting_cache_preserved(self):
        self.root.mkdir()
        target = self.root / (self.sha + '.pt')
        target.write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError, 'size_mismatch'):
            self.ensure()
        self.assertEqual(target.read_bytes(), b'broken')
        self.assertFalse(self.client.calls)

    def test_bad_location_and_symlinks_rejected(self):
        with self.assertRaises(ValueError):
            self.ensure(key='../weights.pt')
        self.root.mkdir()
        (self.root / (self.sha + '.pt')).symlink_to(self.root / 'not-present')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.ensure()
        self.assertFalse(self.client.calls)

    def test_declared_length_failure_closes_body(self):
        self.client = S3(self.data, declared=1)
        with self.assertRaisesRegex(ValueError, 's3_size'):
            self.ensure()
        self.assertTrue(self.client.body.closed)


if __name__ == '__main__':
    unittest.main()
