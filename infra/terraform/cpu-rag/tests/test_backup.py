"""Synthetic backup artifacts only; never connects to a database or AWS."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('cpu_rag_backup', Path(__file__).parents[1] / 'scripts/backup.py')
backup = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'boto3': types.SimpleNamespace(client=Mock())}):
    spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.backups = root / 'backups'
        self.backups.mkdir()
        self.bucket = root / 'bucket'
        self.bucket.write_text('test-fixture-bucket')
        self.compose = root / 'compose.yaml'
        self.compose.write_text('services: {} # synthetic fixture')
        self.packages = root / 'packages'
        self.packages.write_text('synthetic fixture; no installed PostgreSQL')
        self.args = dict(backup_dir=self.backups, bucket_file=self.bucket,
                         compose_file=self.compose, packages_file=self.packages)
        self.client = Mock()
        self.aws = patch.object(backup.boto3, 'client', return_value=self.client)
        self.aws.start()
        self.addCleanup(self.aws.stop)

    @staticmethod
    def dump(command, *, stdout, check):
        assert command[:3] == ['docker', 'compose', '-f'] and check
        assert command[4:9] == ['exec', '-T', '--user', 'postgres', 'db']
        assert command[-1] == '--no-password'
        stdout.write(b'synthetic dump fixture')

    def test_success_uploads_complete_archive_and_cleans_only_current_run(self):
        prior = self.backups / 'previous-failed-run'
        prior.mkdir()
        (prior / 'cctv.dump').write_text('preserve this')
        uploaded = []
        def upload(**kwargs):
            self.assertEqual(kwargs['ServerSideEncryption'], 'AES256')
            self.assertEqual(kwargs['ChecksumAlgorithm'], 'SHA256')
            with tarfile.open(fileobj=kwargs['Body'], mode='r:gz') as archive:
                uploaded.extend(archive.getnames())
        self.client.put_object.side_effect = upload
        with patch.object(backup.subprocess, 'run', side_effect=self.dump):
            backup.main(**self.args)
        self.assertIn('cctv.dump', uploaded)
        self.assertIn('roles.sql', uploaded)
        self.assertIn('compose.yaml', uploaded)
        self.assertEqual(list(self.backups.iterdir()), [prior])
        self.assertEqual((prior / 'cctv.dump').read_text(), 'preserve this')

    def test_upload_failure_preserves_dump_and_archive(self):
        self.client.put_object.side_effect = RuntimeError('synthetic S3 failure')
        with patch.object(backup.subprocess, 'run', side_effect=self.dump):
            with self.assertRaisesRegex(RuntimeError, 'synthetic S3 failure'):
                backup.main(**self.args)
        run = next(self.backups.iterdir())
        self.assertTrue((run / 'cctv.dump').is_file())
        self.assertTrue((run / 'backup.tar.gz').is_file())
        self.assertEqual(run.stat().st_mode & 0o777, 0o700)

    def test_dump_failure_never_uploads_an_incomplete_backup(self):
        with patch.object(backup.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'fixture')):
            with self.assertRaises(subprocess.CalledProcessError):
                backup.main(**self.args)
        self.client.put_object.assert_not_called()


if __name__ == '__main__':
    unittest.main()
