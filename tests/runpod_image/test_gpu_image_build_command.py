"""Frozen context plus fake Docker only. Never execute Docker, models, or network."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('gpu_image_builder', ROOT / 'scripts/build_verify_runpod_gpu_image.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
RELEASE = ROOT / 'artifacts/runpod/service-cuda-portable-20261003-01'
ID = 'sha256:' + '0123456789abcdef' * 4
OTHER_ID = 'sha256:' + 'abcdef0123456789' * 4


class FakeDocker:
    def __init__(self, identity, *, existing=False):
        self.identity = identity
        self.image_id = ID if existing else None
        self.calls = []
        self.build_error = False
        self.daemon_error = False
        self.inspect_error = False
        self.probe_error = False
        self.wrong_config = False
        self.probe_mutation = None
        self.after_probe = None

    def result(self, returncode=0, stdout='', stderr=''):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[:3] == ['docker', 'image', 'ls']:
            if self.daemon_error:
                return self.result(1, stderr='synthetic-private-daemon-diagnostic')
            return self.result(stdout=(self.image_id + '\n') if self.image_id else '')
        if args[:3] == ['docker', 'buildx', 'build']:
            if self.build_error:
                return self.result(1, stdout='synthetic build output', stderr='synthetic build failed')
            self.image_id = ID
            return self.result(stdout='synthetic build output', stderr='synthetic cached layer progress')
        if args[:3] == ['docker', 'image', 'inspect']:
            if self.inspect_error:
                return self.result(1, stderr='synthetic inspect failure')
            return self.result(stdout=json.dumps({'image_id': args[-1], 'os': 'linux',
                'architecture': 'arm64' if self.wrong_config else 'amd64',
                'entrypoint': ['/opt/cctv-image/entrypoint.sh'], 'cmd': ['pod']}))
        if args[:2] == ['docker', 'run']:
            if self.probe_error:
                return self.result(1, stderr='synthetic failed assertion')
            supplied = json.loads(args[-1])
            if supplied != self.identity:
                raise AssertionError('Wrong probe identity')
            value = {'status': 'cuda_portable_image_contents_verified', 'image_files_verified': 53,
                'service_files_verified': 17, 'image_manifest_sha256': self.identity['image_manifest_sha256'],
                'service_manifest_sha256': self.identity['service_manifest_sha256'],
                'environment_matches': True, 'deployment': helper.DEPLOYMENT, 'gpu_contract': helper.GPU,
                'expected_gpu_setting': 'auto',
                'model_libraries_imported': False, 'model_calls': 0, 'gpu_calls': 0,
                'service_started': False, 'gpu_inference_verified': False,
                'profiles': {version: {'profile_sha256': helper.sha(version.encode()),
                    'image_manifest_sha256': self.identity['image_manifest_sha256'],
                    'execution_adapter_sha256': self.identity['execution_adapter_sha256'],
                    'full_manifest_links_verified': True} for version in ('A', 'B')}}
            if self.probe_mutation:
                self.probe_mutation(value)
            if self.after_probe:
                self.after_probe()
            return self.result(stdout=json.dumps(value))
        raise AssertionError('Unexpected external operation: ' + str(args[:3]))


class GPUImageBuildCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cctv-image-build-stub-')
        self.addCleanup(temporary.cleanup)
        self.release = Path(temporary.name).resolve() / 'release'
        self.release.mkdir()
        shutil.copytree(RELEASE / 'context', self.release / 'context')
        for name in (helper.ARCHIVE, 'preparation.json'):
            shutil.copyfile(RELEASE / name, self.release / name)
        prepared = json.loads((self.release / 'preparation.json').read_text())
        self.args = (self.release, prepared['sha256'], prepared['image_manifest_sha256'],
                     prepared['service_manifest_sha256'])
        self.identity = helper.verify_release(*self.args)
        self.fake = FakeDocker(self.identity)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for context in (patch.object(helper.sys, 'platform', 'darwin'),
                        patch.object(helper.subprocess, 'run', side_effect=self.fake),
                        patch('socket.socket', side_effect=AssertionError('No network')),
                        patch.dict('sys.modules', {name: None for name in
                            ('torch', 'torchvision', 'pytorchvideo', 'ultralytics', 'cv2', 'av', 'numpy', 'boto3')}),
                        redirect_stdout(self.stdout), redirect_stderr(self.stderr)):
            self.stack.enter_context(context)

    def execute(self):
        return helper.build_verify(*self.args)

    def builds(self):
        return [args for args in self.fake.calls if args[:3] == ['docker', 'buildx', 'build']]

    def test_frozen_context_has_24_files_with_full_hash_verification(self):
        self.assertEqual(self.identity['context_files'], 24)
        self.assertEqual(len(helper.SERVICE), 17)
        self.assertEqual(len(helper.CONTEXT), 24)
        compile(helper.PROBE, '<image-probe>', 'exec')
        self.assertNotIn('create_app(', helper.PROBE)
        self.assertNotIn('.startup(', helper.PROBE)
        self.assertNotIn('nvidia-smi', helper.PROBE)
        self.assertIn(helper.CATALOG, helper.SERVICE)
        self.assertIn(helper.RTX_CATALOG, helper.SERVICE)
        self.assertEqual(helper.GPU, 'NVIDIA CUDA GPU')
        self.assertIn("os.environ.get('CCTV_EXPECTED_GPU') == 'auto'", helper.PROBE)

    def test_build_then_immutable_id_readonly_probe_and_receipt(self):
        result = self.execute()
        self.assertEqual(result['status'], 'cuda_portable_image_built_and_verified')
        self.assertTrue(result['docker_build_performed'])
        self.assertEqual(len(self.builds()), 1)
        build = self.builds()[0]
        for flag in ('--load', '--pull=false'):
            self.assertIn(flag, build)
        self.assertNotIn('--builder', build)
        self.assertEqual(build[build.index('--platform') + 1], 'linux/amd64')
        self.assertEqual(build[build.index('--network') + 1], 'none')
        self.assertEqual(build[build.index('--tag') + 1], helper.IMAGE)
        probe, = [call for call in self.fake.calls if call[:2] == ['docker', 'run']]
        self.assertIn(ID, probe)
        self.assertNotIn(helper.IMAGE, probe)
        self.assertIn('--read-only', probe)
        self.assertIn('--rm', probe)
        self.assertIn('--pull=never', probe)
        self.assertEqual(probe[probe.index('--network') + 1], 'none')
        self.assertEqual(probe[probe.index('--entrypoint') + 1], '/opt/cctv-image/.venv/bin/python')
        for forbidden in ('--gpus', '--volume', '-v', '--env', '-e', '--publish', '-p'):
            self.assertNotIn(forbidden, probe)
        self.assertFalse(any('push' in call or 'prune' in call or 'rmi' in call for call in self.fake.calls))
        self.assertEqual(json.loads((self.release / helper.RECEIPT).read_text()), result)
        self.assertTrue((self.release / 'image-build-attempt-001.json').is_file())
        log = json.loads((self.release / 'image-build-attempt-001-output.json').read_text())
        self.assertEqual(log['stderr'], 'synthetic cached layer progress')
        self.assertIn('cuda_portable_image_building', self.stderr.getvalue())

    def test_existing_tag_is_preserved_and_verified_without_build(self):
        self.fake.image_id = ID
        result = self.execute()
        self.assertEqual(result['status'], 'cuda_portable_existing_image_verified')
        self.assertFalse(result['docker_build_performed'])
        self.assertEqual(self.builds(), [])
        self.assertEqual(self.fake.image_id, ID)
        self.assertFalse((self.release / 'image-build-attempt-001.json').exists())

    def test_repeated_success_rechecks_without_build_or_receipt_overwrite(self):
        first = self.execute()
        original = (self.release / helper.RECEIPT).read_bytes()
        second = self.execute()
        third = self.execute()
        self.assertEqual(second['status'], 'cuda_portable_image_reverified_no_build')
        self.assertEqual(second['image_id'], first['image_id'])
        self.assertEqual(third['receipt'], 'image-recheck-002.json')
        self.assertEqual((self.release / helper.RECEIPT).read_bytes(), original)
        self.assertEqual(len(self.builds()), 1)

    def test_failed_build_logs_preserved_and_manual_next_call_can_retry(self):
        self.fake.build_error = True
        with self.assertRaisesRegex(helper.CheckError, 'docker_command_failed'):
            self.execute()
        first_log = (self.release / 'image-build-attempt-001-output.json').read_bytes()
        self.assertEqual(json.loads(first_log)['returncode'], 1)
        self.assertFalse((self.release / helper.RECEIPT).exists())
        self.fake.build_error = False
        result = self.execute()
        self.assertEqual(result['status'], 'cuda_portable_image_built_and_verified')
        self.assertEqual(len(self.builds()), 2)
        self.assertEqual((self.release / 'image-build-attempt-001-output.json').read_bytes(), first_log)
        self.assertTrue((self.release / 'image-build-attempt-002.json').exists())

    def test_bad_expected_sha_or_context_change_stops_before_docker(self):
        with self.assertRaisesRegex(helper.CheckError, 'archive_sha256_mismatch'):
            helper.build_verify(self.release, '0' * 64, self.args[2], self.args[3])
        target = self.release / 'context/runtime/job.py'
        target.write_bytes(target.read_bytes() + b'\n# altered\n')
        with self.assertRaisesRegex(helper.CheckError, 'archive_context_mismatch'):
            self.execute()
        self.assertEqual(self.fake.calls, [])

    def test_extra_file_or_symlink_stops_before_docker(self):
        extra = self.release / 'context/unlisted.py'; extra.write_text('unlisted')
        with self.assertRaisesRegex(helper.CheckError, 'context_roster_mismatch'):
            self.execute()
        extra.unlink()
        extra.symlink_to(self.release / 'context/Dockerfile')
        with self.assertRaisesRegex(helper.CheckError, 'context_special_file'):
            self.execute()
        self.assertEqual(self.fake.calls, [])

    def test_daemon_failure_is_not_treated_as_absent_image(self):
        self.fake.daemon_error = True
        with self.assertRaisesRegex(helper.CheckError, 'docker_command_failed'):
            self.execute()
        self.assertEqual(self.builds(), [])
        self.assertFalse((self.release / 'image-build-attempt-001.json').exists())

    def test_existing_image_inspect_failure_does_not_trigger_build(self):
        self.fake.image_id = ID
        self.fake.inspect_error = True
        with self.assertRaisesRegex(helper.CheckError, 'docker_command_failed'):
            self.execute()
        self.assertEqual(self.builds(), [])
        self.assertFalse((self.release / helper.RECEIPT).exists())

    def test_wrong_architecture_stops_before_probe(self):
        self.fake.image_id = ID
        self.fake.wrong_config = True
        with self.assertRaisesRegex(helper.CheckError, 'image_config_mismatch'):
            self.execute()
        self.assertFalse(any(call[:2] == ['docker', 'run'] for call in self.fake.calls))
        self.assertFalse((self.release / helper.RECEIPT).exists())

    def test_probe_failure_preserves_built_tag_and_next_call_only_rechecks(self):
        self.fake.probe_error = True
        with self.assertRaisesRegex(helper.CheckError, 'docker_command_failed'):
            self.execute()
        self.assertEqual(self.fake.image_id, ID)
        self.assertFalse((self.release / helper.RECEIPT).exists())
        self.fake.probe_error = False
        result = self.execute()
        self.assertEqual(result['status'], 'cuda_portable_existing_image_verified')
        self.assertEqual(len(self.builds()), 1)

    def test_profile_adapter_and_environment_claims_must_match(self):
        self.fake.image_id = ID
        for mutate in (lambda value: value['profiles']['B'].update(execution_adapter_sha256='0' * 64),
                       lambda value: value.update(environment_matches=False),
                       lambda value: value.update(expected_gpu_setting='NVIDIA RTX 2000 Ada Generation'),
                       lambda value: value.update(model_calls=True)):
            self.fake.probe_mutation = mutate
            with self.assertRaises(helper.CheckError):
                self.execute()
            self.assertFalse((self.release / helper.RECEIPT).exists())
        self.assertEqual(self.builds(), [])

    def test_changed_recorded_image_is_preserved_without_rebuild(self):
        self.execute()
        original = (self.release / helper.RECEIPT).read_bytes()
        self.fake.image_id = OTHER_ID
        with self.assertRaisesRegex(helper.CheckError, 'recorded_image_missing_or_changed_no_rebuild'):
            self.execute()
        self.assertEqual(self.fake.image_id, OTHER_ID)
        self.assertEqual(len(self.builds()), 1)
        self.assertEqual((self.release / helper.RECEIPT).read_bytes(), original)

    def test_tag_change_during_probe_has_no_success_receipt(self):
        self.fake.image_id = ID
        self.fake.after_probe = lambda: setattr(self.fake, 'image_id', OTHER_ID)
        with self.assertRaisesRegex(helper.CheckError, 'image_tag_changed_during_check'):
            self.execute()
        self.assertFalse((self.release / helper.RECEIPT).exists())

    def test_context_change_during_probe_has_no_success_receipt(self):
        self.fake.image_id = ID
        self.fake.after_probe = lambda: (self.release / 'context/Dockerfile').write_text('changed')
        with self.assertRaisesRegex(helper.CheckError, 'archive_context_mismatch'):
            self.execute()
        self.assertFalse((self.release / helper.RECEIPT).exists())

    def test_cli_failure_has_clear_redacted_status(self):
        self.fake.daemon_error = True
        result = helper.main(['--release', str(self.release), '--archive-sha256', self.args[1],
            '--image-manifest-sha256', self.args[2], '--service-manifest-sha256', self.args[3]])
        self.assertEqual(result, 1)
        output = self.stdout.getvalue()
        self.assertNotIn('synthetic-private-daemon-diagnostic', output + self.stderr.getvalue())
        self.assertEqual(json.loads(output)['status'], 'cuda_portable_image_build_check_stopped')

    def test_real_probe_on_assembled_temp_image_uses_only_metadata_and_files(self):
        image = self.release / 'assembled-image'; image.mkdir()
        service = self.release / 'context/service'
        manifest_bytes = (self.release / 'context/image-manifest.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        frozen = ROOT / 'artifacts/runpod/image-p4-20261001-01/context'
        # Copy unchanged saved bytes; no model is imported, loaded, or executed.
        for name in manifest['files']:
            source = self.release / 'context' / name if name in helper.RUNTIME else frozen / name
            destination = image / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        (image / 'image-manifest.json').write_bytes(manifest_bytes)
        code = helper.PROBE.replace("Path('/opt/cctv-image')", 'Path(' + repr(str(image)) + ')')
        code = code.replace("Path('/opt/cctv-service')", 'Path(' + repr(str(service)) + ')')
        output = io.StringIO()
        with patch.object(helper.sys, 'argv', ['probe', json.dumps(self.identity)]), \
                patch.dict('os.environ', {'CCTV_ANALYSIS_DEPLOYMENT': helper.DEPLOYMENT, 'CCTV_EXPECTED_GPU': 'auto'}), \
                patch('importlib.metadata.version', side_effect=lambda name: manifest['packages'][name]), \
                patch('platform.python_version', return_value=manifest['python_version']), redirect_stdout(output):
            exec(compile(code, '<assembled-image-probe>', 'exec'), {'__name__': '__main__'})
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'cuda_portable_image_contents_verified')
        self.assertEqual(result['image_files_verified'], 53)
        self.assertEqual(result['service_files_verified'], 17)
        self.assertEqual(result['expected_gpu_setting'], 'auto')
        self.assertEqual(set(result['profiles']), {'A', 'B'})
        self.assertTrue(all(value['full_manifest_links_verified'] for value in result['profiles'].values()))
        self.assertEqual(self.fake.calls, [])

    def test_frozen_rtx_helper_and_wrapper_preserve_original_release(self):
        rtx = ROOT / 'artifacts/runpod/service-rtx2000ada-20261003-01'
        history = json.loads((rtx / 'build-helper-history-01.json').read_text())
        for name, record in history['files'].items():
            if isinstance(record, dict):
                self.assertEqual(helper.sha((rtx / name).read_bytes()), record['sha256'])
        self.assertEqual(helper.sha((rtx / 'cctv-runpod-rtx2000ada.zip').read_bytes()),
                         history['files']['preserved_rtx_archive_sha256'])
        wrapper = (rtx / 'build-command-01.sh').read_text()
        self.assertIn('"$rtx_release_dir/build-helper-frozen-01.py"', wrapper)
        self.assertNotIn('scripts/build_verify_runpod_gpu_image.py', wrapper)
        original = importlib.util.spec_from_file_location('frozen_rtx_image_builder', rtx / 'build-helper-frozen-01.py')
        frozen = importlib.util.module_from_spec(original); original.loader.exec_module(frozen)
        prepared = json.loads((rtx / 'preparation.json').read_text())
        result = frozen.verify_release(rtx, prepared['sha256'], prepared['image_manifest_sha256'],
                                       prepared['service_manifest_sha256'])
        self.assertEqual(result['context_files'], 23)
        self.assertEqual(frozen.IMAGE, 'cctv-runpod-ab:p6-rtx2000ada-v1')
        self.assertEqual(self.fake.calls, [])


if __name__ == '__main__':
    unittest.main()
