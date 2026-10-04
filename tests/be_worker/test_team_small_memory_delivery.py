"""Recovery delivery preserves prior rollout state; only local temporary files."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / 'artifacts/be-worker/teammate-rag-integration-20261003-01'
spec = importlib.util.spec_from_file_location('memory_recovery_delivery', HERE / 'resume-small-rollout-01.py')
RECOVERY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RECOVERY)


def sha(value):
    return hashlib.sha256(value).hexdigest()


class MemoryRecoveryDeliveryTests(unittest.TestCase):
    def layout(self, root, *, unsafe=False):
        release = root / 'release'
        release.mkdir()
        base = b'{"files":{}}'
        (release / 'manifest.json').write_bytes(base)
        previous = release / 'small-memory-01'
        previous.mkdir()
        files = {}
        for index in range(8):
            name = 'helper-' + str(index) + '.py'
            raw = b'# immutable helper\n'
            (previous / name).write_bytes(raw)
            files[name] = {'bytes': len(raw), 'sha256': sha(raw)}
        old_raw = (json.dumps({'base_manifest_sha256': sha(base), 'files': files}) + '\n').encode()
        (previous / 'manifest.json').write_bytes(old_raw)
        new_files = dict(files)
        if unsafe:
            new_files['../outside.py'] = new_files.pop('helper-0.py')
        manifest = {'revision': 'small-memory-02', 'base_manifest_sha256': sha(base),
                    'previous_overlay_manifest_sha256': sha(old_raw), 'files': new_files}
        archive = root / 'recovery.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('manifest.json', json.dumps(manifest))
            for name in new_files:
                z.writestr(name, b'# immutable helper\n')
        return release, previous, archive, sha(old_raw)

    def extract(self, release, archive, previous_sha):
        code = RECOVERY.extraction_code(str(archive), str(release), sha(archive.read_bytes()), previous_sha)
        with mock.patch('os.geteuid', return_value=0), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(code, '<memory-recovery-extract>', 'exec'), {})

    def test_new_revision_and_identical_reuse_preserve_original_and_schema_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            release, old, archive, previous_sha = self.layout(Path(raw).resolve())
            schema = release / 'small-saved-schema-applied-01.json'
            schema.write_text('{"transaction_committed":true}')
            before = {path.name: path.read_bytes() for path in old.iterdir()}
            self.extract(release, archive, previous_sha)
            new = release / 'small-memory-02'
            times = {path.name: path.stat().st_mtime_ns for path in new.iterdir()}
            self.extract(release, archive, previous_sha)
            self.assertEqual(before, {path.name: path.read_bytes() for path in old.iterdir()})
            self.assertEqual(times, {path.name: path.stat().st_mtime_ns for path in new.iterdir()})
            self.assertEqual(schema.read_text(), '{"transaction_committed":true}')

    def test_prior_attempt_or_rollback_is_preserved_without_creating_revision(self):
        for name in ('small-saved-rollout-attempt-01.json', 'small-saved-installed-01.json',
                     'small-saved.compose.json', 'small-saved-rollout-failure-01.json',
                     'small-saved-verification-failure-01.json', 'rollback-state-01.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                release, old, archive, previous_sha = self.layout(Path(raw).resolve())
                path = (release if name == 'rollback-state-01.json' else old) / name
                path.write_text('preserve user execution evidence')
                with self.assertRaisesRegex(AssertionError, 'preserved_inspect_first'):
                    self.extract(release, archive, previous_sha)
                self.assertFalse((release / 'small-memory-02').exists())
                self.assertEqual(path.read_text(), 'preserve user execution evidence')

    def test_changed_original_or_existing_revision_is_preserved(self):
        for changed in ('original', 'new'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as raw:
                release, old, archive, previous_sha = self.layout(Path(raw).resolve())
                if changed == 'new':
                    self.extract(release, archive, previous_sha)
                path = (old if changed == 'original' else release / 'small-memory-02') / 'helper-1.py'
                path.write_text('user change')
                with self.assertRaisesRegex(AssertionError, 'changed|preserved'):
                    self.extract(release, archive, previous_sha)
                self.assertEqual(path.read_text(), 'user change')

    def test_traversal_and_symlink_refuse_write(self):
        with tempfile.TemporaryDirectory() as raw:
            release, old, archive, previous_sha = self.layout(Path(raw).resolve(), unsafe=True)
            with self.assertRaisesRegex(AssertionError, 'unsafe_member'):
                self.extract(release, archive, previous_sha)
            self.assertFalse((release / 'small-memory-02').exists())
            self.assertFalse((release / 'outside.py').exists())
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            release, old, archive, previous_sha = self.layout(root)
            outside = root / 'outside'
            outside.mkdir()
            (release / 'small-memory-02').symlink_to(outside)
            with self.assertRaisesRegex(AssertionError, 'symlink_preserved'):
                self.extract(release, archive, previous_sha)
            self.assertEqual(list(outside.iterdir()), [])

    def test_invalid_cli_arguments_fail_before_ssh_or_delivery_read(self):
        with mock.patch.object(RECOVERY, 'load_transfer') as transfer, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                RECOVERY.main(['--reset-budget'])
            self.assertEqual(result.exception.code, 2)
            transfer.assert_not_called()

    def mac_layout(self, root):
        release, previous, archive, previous_sha = self.layout(root)
        local_archive = release / 'recovery.zip'
        local_archive.write_bytes(archive.read_bytes())
        receipt = {'archive': local_archive.name, 'bytes': local_archive.stat().st_size,
                   'sha256': sha(local_archive.read_bytes()),
                   'previous_overlay_manifest_sha256': previous_sha}
        (release / 'small-rollout-memory-delivery-receipt-01.json').write_text(json.dumps(receipt))
        return release, receipt

    def test_mac_only_sends_fixed_revision_rollout_and_explicit_apply(self):
        for args in ([], ['--apply']):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as raw:
                release, receipt = self.mac_layout(Path(raw).resolve())
                transfer = mock.Mock()
                transfer.upload_code.return_value = 'fixed upload command'
                with mock.patch.object(RECOVERY, 'HERE', release), \
                        mock.patch.object(RECOVERY, 'load_transfer', return_value=transfer):
                    RECOVERY.main(args)
                self.assertEqual(transfer.remote.call_count, 3)
                command = transfer.remote.call_args.args[0]
                self.assertEqual(command, 'sudo python3 -B /opt/cctv-be-team-rag-20261003-01/'
                                         'small-memory-02/small-step-runner-01.py rollout' +
                                         (' --apply' if args else ''))
                transfer.upload_code.assert_called_once_with(RECOVERY.TARGET, receipt['sha256'])

    def test_invalid_hash_or_archive_stops_before_remote_session(self):
        for corrupt in ('hash', 'archive'):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as raw:
                release, receipt = self.mac_layout(Path(raw).resolve())
                if corrupt == 'hash':
                    receipt['sha256'] = 'invalid shell text'
                    (release / 'small-rollout-memory-delivery-receipt-01.json').write_text(json.dumps(receipt))
                else:
                    (release / receipt['archive']).write_bytes(b'changed archive')
                with mock.patch.object(RECOVERY, 'HERE', release), \
                        mock.patch.object(RECOVERY, 'load_transfer') as transfer:
                    with self.assertRaisesRegex(RuntimeError, 'hash_invalid|delivery_changed'):
                        RECOVERY.main(['--apply'])
                    transfer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
