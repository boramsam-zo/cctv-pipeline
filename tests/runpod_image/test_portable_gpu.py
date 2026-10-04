"""Portable CUDA contracts using observed device names and synthetic results only."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from apps.model_compare import job, runner
from apps.runpod_image import image_runtime as runtime, service_job


def write_json(path, value):
    path.write_text(json.dumps(value))


def spec(path):
    return {'bytes': path.stat().st_size, 'sha256': runtime.file_hash(path)}


class PortableGPUTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cctv-portable-gpu-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.image = self.root / 'image'
        for name in ('runtime', 'sources/cctv-runpod-a4000', 'sources/deployment_handoff_1001'):
            (self.image / name).mkdir(parents=True)
        for name in ('image_runtime.py', 'runner.py', 'job.py'):
            (self.image / 'runtime' / name).write_text('# synthetic identity-only file\n')
        self.manifest = {'schema_version': 'cctv-runpod-image-v3', 'default_version': 'A',
                         'supported_versions': ['A', 'B'], 'gpu': runtime.PORTABLE_GPU,
                         'files': {p.relative_to(self.image).as_posix(): spec(p)
                                   for p in self.image.rglob('*.py')}}
        self.manifest_file = self.image / 'image-manifest.json'
        write_json(self.manifest_file, self.manifest)
        self.image_sha = runtime.file_hash(self.manifest_file)
        self.directory = self.root / 'service-job'
        self.directory.mkdir()
        self.video = self.directory / 'input.mp4'
        self.video.write_bytes(b'Synthetic input; never decoded')
        self.state_root = self.root / 'state'
        self.state_root.mkdir()
        self.service = service_job.Runtime(self.image, self.state_root)
        self.state = {'job_id': 'b' * 32, 'input_sha256': runtime.file_hash(self.video),
                      'input_bytes': self.video.stat().st_size, 'gpu_key': 'synthetic-gpu-key',
                      'analysis_profile': {'version': 'B', 'runtime': {'gpu': runtime.PORTABLE_GPU},
                                           'image_manifest_sha256': self.image_sha}}
        self.identity_patch = patch.object(service_job, 'gpu_identity', return_value={'gpu_key': self.state['gpu_key']})
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)
        modules = patch.dict(sys.modules, {name: None for name in
            ('torch', 'torchvision', 'pytorchvideo', 'ultralytics', 'cv2', 'av', 'numpy', 'boto3')})
        modules.start()
        self.addCleanup(modules.stop)
        network = patch('socket.socket', side_effect=AssertionError('No network in contract tests'))
        network.start()
        self.addCleanup(network.stop)

    def startup(self, text='Tesla T4, 15109\n', expected=None):
        service = service_job.Runtime(self.image)
        with patch.dict(os.environ, {}, clear=False), \
                patch.object(service_job, 'profile', return_value=self.state['analysis_profile']), \
                patch.object(runtime, 'inspect_image', return_value=(self.manifest, self.image_sha)), \
                patch.object(runtime, 'check_environment', return_value={'mismatches': {}}), \
                patch.object(service_job.sys, 'platform', 'linux'), \
                patch.object(service_job.subprocess, 'run', return_value=types.SimpleNamespace(stdout=text)):
            os.environ.pop('CCTV_EXPECTED_GPU', None)
            if expected is not None:
                os.environ['CCTV_EXPECTED_GPU'] = expected
            result = service.startup()
        return service, result

    def make_plan(self, observed='Tesla T4'):
        self.service.actual_gpu = observed
        command = self.service.command(self.directory, self.state, 20)
        self.assertEqual(command[-2:], ['--state-root', str(self.state_root)])
        self.plan_file = self.directory / 'execution-plan.json'
        self.plan = runtime.read_json(self.plan_file)
        self.identity = {'plan_sha256': runtime.file_hash(self.plan_file),
                         'image_manifest_sha256': self.image_sha}
        return self.plan

    @property
    def folder(self):
        return self.service.plan_directory(self.state)

    def execute(self, process):
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(runner, 'run_process', side_effect=process) as call, \
                runner.trial_lock(self.state_root) as lock:
            state = runtime.execute(self.plan, self.identity, self.image, self.state_root, 'B', runner, lock)
        return state, call

    def success(self, command, log, seconds, lock, env):
        self.assertEqual(command[-2:], ['--expected-gpu', self.plan['observed_gpu']])
        self.assertNotIn('CCTV_EXPECTED_GPU', env)
        self.assertGreater(seconds, 0)
        self.assertLessEqual(seconds, self.plan['limits']['per_job_timeout_seconds'])
        output = Path(command[command.index('--output') + 1])
        output.mkdir()
        raw = output / 'raw.json'
        raw.write_text('{"synthetic":true}')
        write_json(output / 'result.json', {'version': 'B', 'status': 'no_candidates',
            'input_sha256': self.state['input_sha256'], 'event_count': 0,
            'gpu': self.plan['observed_gpu'], 'timings': {'total_seconds': .01},
            'memory': {'peak_allocated_bytes': 1}, 'artifacts': [{'path': raw.name, **spec(raw)}]})
        return {'status': 'exited', 'returncode': 0, 'subprocess_wall_seconds': .01}

    def test_name_validation_is_consistent_and_not_a_device_whitelist(self):
        valid = ['Tesla T4', runtime.GPU, runtime.RTX_GPU, 'NVIDIA A100-SXM4-40GB', 'X' * 160]
        invalid = [None, 1, '', ' ', 'auto', 'AUTO', 'gpu', 'CUDA', 'unknown', 'none', 'null',
                   runtime.PORTABLE_GPU, 'CPU', 'cpu:0', 'Intel CPU', ' Tesla T4',
                   'Tesla T4 ', 'Tesla\nT4', 'NVIDIA\x00', 'GPU\u00e9', 'X' * 161]
        for value in valid + invalid:
            for helper in (job.valid_gpu_name, runner.valid_gpu_name, runtime.valid_gpu_name):
                with self.subTest(helper=helper.__module__, value=value):
                    self.assertEqual(helper(value), value in valid)
        for value in valid:
            environment = {'gpu': value}
            job.validate_gpu(environment, 1, value)
            self.assertEqual(environment['gpu'], value)
        with self.assertRaisesRegex(RuntimeError, 'approved GPU'):
            job.validate_gpu({'gpu': 'Tesla T4'}, 1, 'NVIDIA A100-SXM4-40GB')

    def test_auto_and_exact_override_record_actual_gpu_with_no_capacity_minimum(self):
        for name in ('Tesla T4', runtime.RTX_GPU, 'NVIDIA A100-SXM4-40GB'):
            for requested in (None, 'auto', name):
                with self.subTest(name=name, requested=requested):
                    service, result = self.startup(name + ', 1\n', requested)
                    self.assertEqual(service.actual_gpu, name)
                    self.assertEqual(result, {'gpu': name, 'vram_mib': 1,
                        'model_libraries_imported': False, 'model_calls': 0, 'gpu_inference_verified': False})

    def test_startup_rejects_empty_override_multiple_devices_and_non_gpu_names(self):
        with self.assertRaisesRegex(ValueError, 'expected_gpu_empty'):
            self.startup(expected='')
        for text, override in (('Tesla T4, 15109\n', runtime.RTX_GPU),
                               ('Tesla T4, 1\nTesla T4, 1\n', None),
                               ('CPU, 16380\n', None), ('NVIDIA CUDA GPU, 16380\n', None),
                               ('auto, 16380\n', None), ('', None)):
            with self.subTest(text=text, override=override), self.assertRaises(ValueError):
                self.startup(text, override)
        for size in ('0', '-1', 'nan', '16380.0', ''):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, 'memory_invalid'):
                self.startup('Tesla T4, ' + size + '\n')

    def test_portable_manifest_and_plan_require_observed_device_and_correct_schema(self):
        self.assertEqual(runtime.inspect_image(self.image)[0]['gpu'], runtime.PORTABLE_GPU)
        self.make_plan()
        self.plan['state_root'] = str(runtime.STATE_ROOT)
        write_json(self.plan_file, self.plan)
        self.assertEqual(runtime.load_plan(self.plan_file, self.image_sha, runtime.PORTABLE_GPU)[0], self.plan)
        for observed in (None, 'auto', runtime.PORTABLE_GPU, 'CPU', ''):
            value = dict(self.plan)
            if observed is None:
                value.pop('observed_gpu')
            else:
                value['observed_gpu'] = observed
            write_json(self.plan_file, value)
            with self.subTest(observed=observed), self.assertRaisesRegex(ValueError, 'observed GPU'):
                runtime.load_plan(self.plan_file, self.image_sha, runtime.PORTABLE_GPU)
        for schema in ('cctv-runpod-image-v1', 'cctv-runpod-image-v2'):
            write_json(self.manifest_file, dict(self.manifest, schema_version=schema))
            with self.subTest(schema=schema), self.assertRaisesRegex(ValueError, 'GPU contract'):
                runtime.inspect_image(self.image)

    def test_command_requires_startup_observation_and_durably_records_device(self):
        with self.assertRaisesRegex(ValueError, 'not_observed'):
            self.service.command(self.directory, self.state, 20)
        self.assertFalse((self.directory / 'execution-plan.json').exists())
        plan = self.make_plan()
        self.assertEqual((plan['gpu'], plan['observed_gpu']), (runtime.PORTABLE_GPU, 'Tesla T4'))
        self.assertEqual(plan['limits']['max_attempts_per_job'], 1)
        self.assertEqual(plan['limits']['gpu_concurrency'], 1)
        self.assertEqual(plan['limits']['automatic_retries'], 0)
        with self.assertRaisesRegex(ValueError, 'already_exists'):
            self.service.command(self.directory, self.state, 20)

    def test_completed_t4_is_reused_after_current_device_changes_without_query_or_launch(self):
        self.make_plan()
        with patch.dict(os.environ, {'CCTV_EXPECTED_GPU': 'NVIDIA A100-SXM4-40GB'}):
            first, process = self.execute(self.success)
        self.assertEqual(first['status'], 'completed_plan')
        self.assertEqual(process.call_count, 1)
        self.service.actual_gpu = 'NVIDIA A100-SXM4-40GB'
        with patch.object(service_job.subprocess, 'run', side_effect=AssertionError('No current device query')), \
                patch.object(runtime, 'load_helpers', return_value=runner):
            output, digest, result = self.service.completed_output(self.directory, self.state)
            self.assertEqual(result['gpu'], 'Tesla T4')
            second, process = self.execute(lambda *args: self.fail('Repeated completed inference'))
        self.assertEqual(process.call_count, 0)
        self.assertEqual(second['deadline_at'], first['deadline_at'])
        self.assertEqual(second['jobs'][0]['attempts'], 1)

    def test_actual_result_must_match_saved_observation_and_failure_cannot_retry(self):
        self.make_plan()
        def wrong_result(*args):
            outcome = self.success(*args)
            path = self.folder / 'input-B/output/result.json'
            value = runtime.read_json(path)
            value['gpu'] = runtime.RTX_GPU
            write_json(path, value)
            return outcome
        first, _ = self.execute(wrong_result)
        self.assertEqual(first['status'], 'stopped_on_failure')
        self.assertEqual(first['jobs'][0]['attempts'], 1)
        second, process = self.execute(lambda *args: self.fail('Retry after result mismatch'))
        self.assertEqual(second['status'], 'blocked_previous_attempt')
        self.assertEqual(process.call_count, 0)

    def test_child_cli_accepts_non_nvidia_prefix_and_rejects_auto_and_cpu(self):
        argv = ['job.py', '--work-root', str(self.root), '--version', 'B',
                '--input', str(self.video), '--output', str(self.root / 'output'), '--expected-gpu']
        with patch.object(sys, 'argv', argv + ['Tesla T4']), patch.object(job, 'run_job') as call:
            job.main()
            self.assertEqual(call.call_args.kwargs, {'expected_gpu': 'Tesla T4'})
        for value in ('auto', runtime.PORTABLE_GPU, 'CPU'):
            with patch.object(sys, 'argv', argv + [value]), patch.object(job, 'run_job') as call, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                job.main()
            call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
