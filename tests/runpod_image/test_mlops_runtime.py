"""New-image selection/output validation with no model or GPU imports."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch

from apps.runpod_image.runtime import image_runtime, job


class RuntimeTests(unittest.TestCase):
    def test_service_source_tamper_blocks_startup_and_command_before_gpu(self):
        from apps.runpod_image.service_job import Runtime
        from scripts.prepare_runpod_ci import payloads_for_release
        payloads, catalog = payloads_for_release()
        with tempfile.TemporaryDirectory() as folder:
            service = Path(folder).resolve() / 'service'
            for name, data in payloads.items():
                if name.startswith('service/'):
                    target = Path(folder).resolve() / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            runtime = Runtime(service_root=service)
            snapshot = {'deployment': 'mlops-20261004-v1',
                        'image_manifest_sha256': catalog['image_manifest_sha256'],
                        'execution_adapter_sha256': catalog['execution_adapter_sha256'],
                        'code_files': catalog['code_files']}
            runtime.verify_service(snapshot=snapshot)
            adapter = service / 'apps/runpod_image/classifier_adapter.py'
            adapter.write_bytes(adapter.read_bytes() + b'\n# synthetic unregistered change\n')
            with patch.dict('os.environ', CCTV_ANALYSIS_DEPLOYMENT='mlops-20261004-v1'):
                with patch('apps.runpod_image.service_job.subprocess.run') as gpu:
                    with self.assertRaisesRegex(ValueError, 'code mismatch|file hash mismatch'):
                        runtime.startup()
                    gpu.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'code mismatch|file hash mismatch'):
                    runtime.command(Path(folder) / 'not-created', {'analysis_profile': snapshot}, 300)
                self.assertFalse((Path(folder) / 'not-created').exists())

    def test_v4_manifest_requires_exact_supported_classifiers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for name in ('runtime', 'sources/cctv-runpod-a4000', 'sources/deployment_handoff_1001'):
                (root / name).mkdir(parents=True)
            files = {}
            for name in ('runtime/image_runtime.py', 'runtime/job.py', 'runtime/runner.py'):
                data = b'# synthetic source\n'
                (root / name).write_bytes(data)
                files[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            manifest = {'schema_version': 'cctv-runpod-image-v4', 'default_version': 'B',
                        'supported_versions': ['B', 'R2'], 'gpu': 'NVIDIA CUDA GPU', 'files': files}
            path = root / 'image-manifest.json'
            path.write_text(json.dumps(manifest))
            self.assertEqual(image_runtime.inspect_image(root)[0], manifest)
            manifest['supported_versions'] = ['A', 'B', 'R2']
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'contract'):
                image_runtime.inspect_image(root)

    def test_r2_tied_window_is_valid_no_candidate(self):
        row = {'start_s': 0., 'end_s': 2., 'collision_probability': .5,
               'is_positive': False, 'logits': [0., 0.]}
        result = {'duration_s': 2., 'fps': 30.,
                  'classifier': {'model_id': 'r2plus1d_18', 'windows': [row]},
                  'events': [], 'vlm_handoffs': []}
        self.assertEqual(job.validate_result(result, {'duration_s': 2., 'fps': 30.},
                                            lambda duration: [0.]), 'no_candidates')
        row['is_positive'] = True
        with self.assertRaisesRegex(ValueError, 'threshold'):
            job.validate_result(result, {'duration_s': 2., 'fps': 30.}, lambda duration: [0.])

    def test_cli_only_new_versions_and_no_local_execution(self):
        self.assertEqual(image_runtime.VERSIONS, ('B', 'R2', 'LC'))
        if __import__('sys').platform != 'linux':
            with self.assertRaisesRegex(RuntimeError, 'local inference disabled'):
                job.require_remote_process()


@unittest.skipUnless(importlib.util.find_spec('starlette'), 'optional service dependency unavailable')
class ServicePreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_happens_before_gpu_with_same_time_budget(self):
        from apps.runpod_image.service import Jobs
        fake = Mock()
        fake.process = AsyncMock()
        fake.runtime.command.return_value = ['synthetic-gpu-command']
        await Jobs.run_process(fake, Path('/synthetic-job'), {'analysis_profile': {'version': 'R2'}}, 300.)
        calls = fake.process.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn('apps.runpod_image.model_prepare', calls[0].args[0])
        self.assertEqual(calls[0].args[2], 300.)
        self.assertLessEqual(calls[1].args[2], 300.)
        self.assertTrue(calls[1].kwargs['clean'])

    async def test_prepare_failure_does_not_start_gpu_or_retry(self):
        from apps.runpod_image.service import Jobs
        fake = Mock()
        fake.process = AsyncMock(side_effect=RuntimeError('synthetic S3 failure'))
        with self.assertRaises(RuntimeError):
            await Jobs.run_process(fake, Path('/synthetic-job'), {'analysis_profile': {'version': 'R2'}}, 300.)
        self.assertEqual(fake.process.await_count, 1)
        fake.runtime.command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
