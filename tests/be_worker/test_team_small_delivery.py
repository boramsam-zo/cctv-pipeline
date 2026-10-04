"""Small saved-case delivery boundaries; temp files and fixed subprocess doubles."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / 'artifacts/be-worker/teammate-rag-integration-20261003-01'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load('small_runner_delivery_test', HERE / 'small-memory-01/small-step-runner-01.py')
prepare = load('small_prepare_delivery_test', HERE / 'prepare-small-saved-01.py')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SmallDelivery(unittest.TestCase):
    def layout(self, root):
        release = root / 'release'
        release.mkdir()
        (release / 'old.py').write_text('# immutable prior source\n')
        base = json.dumps({'files': {'old.py': sha((release / 'old.py').read_bytes())}}).encode()
        (release / 'manifest.json').write_bytes(base)
        overlay = release / 'small-memory-01'
        overlay.mkdir()
        payload = b'# fixed new helper\n'
        helper = overlay / 'small-isolated-postgres-01.py'
        helper.write_bytes(payload)
        manifest = {'base_manifest_sha256': sha(base),
                    'files': {helper.name: {'bytes': len(payload), 'sha256': sha(payload)}}}
        (overlay / 'manifest.json').write_text(json.dumps(manifest))
        return release, overlay, manifest

    def test_postgres_apply_reaches_fixed_helper_with_apply(self):
        with tempfile.TemporaryDirectory() as raw:
            release, overlay, _ = self.layout(Path(raw).resolve())
            steps = dict(runner.STEPS, postgres=('small-isolated-postgres-01.py', True, overlay))
            with mock.patch.object(runner, 'RELEASE', release), mock.patch.object(runner, 'OVERLAY', overlay), \
                    mock.patch.object(runner, 'STEPS', steps), mock.patch.object(runner.os, 'geteuid', return_value=0), \
                    mock.patch.object(sys, 'argv', ['runner', 'postgres', '--apply']), \
                    mock.patch.object(runner.runpy, 'run_path') as run:
                runner.main()
                self.assertEqual(sys.argv, [str(overlay / 'small-isolated-postgres-01.py'), '--apply'])
                run.assert_called_once_with(str(overlay / 'small-isolated-postgres-01.py'), run_name='__main__')
        self.assertTrue(runner.STEPS['postgres'][1])

    def test_readonly_step_refuses_apply_before_read_or_execution(self):
        with mock.patch.object(sys, 'argv', ['runner', 'status', '--apply']), \
                mock.patch.object(runner.runpy, 'run_path') as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                runner.main()
            self.assertEqual(result.exception.code, 2)
            run.assert_not_called()

    def test_changed_base_and_changed_helper_stop_before_execution(self):
        for name in ('old.py', 'small-memory-01/small-isolated-postgres-01.py'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                release, overlay, _ = self.layout(Path(raw).resolve())
                (release / name).write_text('# changed')
                with self.assertRaisesRegex(RuntimeError, 'release_or_small_helper_changed'):
                    runner.verify_manifest(release if name == 'old.py' else overlay,
                                           json.loads((release if name == 'old.py' else overlay).joinpath('manifest.json').read_text()))

    def test_manifest_parent_path_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            outside = root / 'outside.py'
            outside.write_text('# user file')
            with self.assertRaisesRegex(RuntimeError, 'manifest_path_invalid'):
                runner.verify_manifest(root, {'files': {'../outside.py': sha(outside.read_bytes())}})
            link = root / 'link.py'
            link.symlink_to(outside)
            with self.assertRaisesRegex(RuntimeError, 'release_or_small_helper_changed'):
                runner.verify_manifest(root, {'files': {'link.py': sha(outside.read_bytes())}})
            self.assertEqual(outside.read_text(), '# user file')

    def package(self, root, name='helper.py', corrupt=False):
        release = root / 'release'
        release.mkdir()
        base = b'{"files":{}}'
        (release / 'manifest.json').write_bytes(base)
        payload = b'# helper bytes'
        manifest = {'base_manifest_sha256': sha(base),
                    'files': {name: {'bytes': len(payload), 'sha256': sha(payload)}}}
        archive = root / 'helpers.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('manifest.json', json.dumps(manifest))
            z.writestr(name, b'# corrupt bytes' if corrupt else payload)
        return release, archive, sha(archive.read_bytes()), payload

    def extract(self, release, archive, digest):
        code = prepare.extract_code(str(archive), str(release), digest)
        with mock.patch.object(prepare.os if hasattr(prepare, 'os') else runner.os, 'geteuid', return_value=0), \
                contextlib.redirect_stdout(io.StringIO()):
            exec(compile(code, '<small-extraction-test>', 'exec'), {})

    def test_valid_extract_and_identical_reuse_preserve_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            release, archive, digest, payload = self.package(Path(raw).resolve())
            self.extract(release, archive, digest)
            target = release / 'small-memory-01/helper.py'
            before = target.stat().st_mtime_ns
            self.extract(release, archive, digest)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(target.stat().st_mtime_ns, before)

    def test_changed_existing_helper_is_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            release, archive, digest, _ = self.package(Path(raw).resolve())
            self.extract(release, archive, digest)
            target = release / 'small-memory-01/helper.py'
            target.write_text('# user modification')
            with self.assertRaisesRegex(AssertionError, 'existing_small_helpers_preserved'):
                self.extract(release, archive, digest)
            self.assertEqual(target.read_text(), '# user modification')

    def test_zip_corruption_and_traversal_do_not_create_target(self):
        for name, corrupt in (('helper.py', True), ('../outside.py', False)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                release, archive, digest, _ = self.package(Path(raw).resolve(), name, corrupt)
                with self.assertRaises(AssertionError):
                    self.extract(release, archive, digest)
                self.assertFalse((release / 'small-memory-01').exists())
                self.assertFalse((release.parent / 'outside.py').exists())

    def test_mac_wrapper_allows_postgres_apply_but_rejects_arbitrary_command(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            wrapper = root / 'artifacts/be-worker/release/small-memory-01/run-step-command-01.sh'
            wrapper.parent.mkdir(parents=True)
            wrapper.write_bytes((HERE / 'small-memory-01/run-step-command-01.sh').read_bytes())
            ssh = root / 'artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh'
            ssh.parent.mkdir(parents=True)
            log = root / 'fake-ssh-call.txt'
            ssh.write_text('printf "%s" "$1" > ' + str(log) + '\n')
            result = subprocess.run(['bash', str(wrapper), 'postgres', '--apply'], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text(), 'sudo python3 -B /opt/cctv-be-team-rag-20261003-01/small-memory-01/small-step-runner-01.py postgres --apply')
            before = log.read_bytes()
            result = subprocess.run(['bash', str(wrapper), 'status; echo unsafe'], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(log.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
