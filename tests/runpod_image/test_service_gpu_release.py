"""Service GPU release bridge checks; saved/synthetic data, no GPU or network."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles as profiles
from apps.be_worker.common import read_json, save
from apps.model_compare import runner
from apps.runpod_image import image_runtime as runtime, service_job
from tests.runpod_image import test_saved_service_output as saved


class ServiceGPUReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cctv-service-gpu-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.catalog = self.root / 'rtx2000ada-20261003-v1.json'
        self.release = {
            'schema_version': 'cctv-analysis-deployment-v1',
            'deployment': profiles.RTX2000ADA_DEPLOYMENT,
            'gpu': 'NVIDIA RTX 2000 Ada Generation',
            'image_manifest_sha256': hashlib.sha256(b'synthetic service image').hexdigest(),
            'execution_adapter_sha256': hashlib.sha256(b'synthetic service adapter').hexdigest(),
        }
        self.catalog.write_text(json.dumps(self.release), encoding='utf-8')
        for context in (
            patch.dict(profiles._RELEASE_CATALOGS,
                       {profiles.RTX2000ADA_DEPLOYMENT: self.catalog}, clear=True),
            patch.dict(os.environ, {'CCTV_ANALYSIS_DEPLOYMENT': profiles.RTX2000ADA_DEPLOYMENT}),
            patch.dict('sys.modules', {name: None for name in
                       ('torch', 'torchvision', 'ultralytics', 'pytorchvideo', 'cv2', 'av', 'boto3')}),
            patch('socket.socket', side_effect=AssertionError('No network in service GPU tests')),
            patch('subprocess.Popen', side_effect=AssertionError('No process in service GPU tests')),
        ):
            context.start()
            self.addCleanup(context.stop)

    def startup(self, output='NVIDIA RTX 2000 Ada Generation, 16380\n', *, manifest=None, digest=None):
        manifest = {'gpu': self.release['gpu']} if manifest is None else manifest
        digest = self.release['image_manifest_sha256'] if digest is None else digest
        with patch.object(service_job.sys, 'platform', 'linux'), \
                patch.object(runtime, 'inspect_image', return_value=(manifest, digest)), \
                patch.object(runtime, 'check_environment', return_value={'mismatches': []}), \
                patch.object(service_job.subprocess, 'run', return_value=types.SimpleNamespace(stdout=output)) as call:
            answer = service_job.Runtime().startup()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[0],
                         ['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'])
        return answer

    def state(self, deployment=profiles.RTX2000ADA_DEPLOYMENT):
        frozen = profiles.profile('B', deployment=deployment)
        return {'job_id': 'b' * 32, 'input_sha256': '1' * 64, 'input_bytes': 42,
                'analysis_profile': frozen,
                'gpu_key': profiles.gpu_identity('1' * 64, frozen)['gpu_key']}

    def test_rtx2000ada_16380_startup_records_actual_gpu_without_claiming_inference(self):
        self.assertEqual(self.startup(), {
            'gpu': self.release['gpu'], 'vram_mib': 16380,
            'model_libraries_imported': False, 'model_calls': 0, 'gpu_inference_verified': False,
        })

    def test_positive_vram_has_no_minimum_and_invalid_values_are_rejected(self):
        for size in ('1', '16380', '19999', '24564'):
            with self.subTest(size=size):
                self.assertEqual(self.startup(f'{self.release["gpu"]}, {size}\n')['vram_mib'], int(size))
        for size in ('0', '-1', '16380.0', 'unknown', '１６３８０'):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, 'gpu_memory_invalid'):
                self.startup(f'{self.release["gpu"]}, {size}\n')

    def test_gpu_mismatch_multiple_devices_and_missing_device_are_rejected(self):
        for output in ('NVIDIA RTX A4500, 20470\n', 'NVIDIA RTX 4090, 24564\n', '',
                       (self.release['gpu'] + ', 16380\n') * 2):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'gpu_does_not_match_profile'):
                self.startup(output)

    def test_wrong_manifest_or_profile_gpu_is_rejected_before_device_read(self):
        for args in ({'manifest': {'gpu': runtime.GPU}}, {'manifest': {}},
                     {'digest': hashlib.sha256(b'wrong image').hexdigest()}):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'image_profile_mismatch'):
                self.startup(**args)

    def test_legacy_startup_keeps_exact_legacy_gpu_contract(self):
        os.environ['CCTV_ANALYSIS_DEPLOYMENT'] = profiles.LEGACY_DEPLOYMENT
        digest = profiles.profile('B')['image_manifest_sha256']
        answer = self.startup(runtime.GPU + ', 16380\n', manifest={'gpu': runtime.GPU}, digest=digest)
        self.assertEqual((answer['gpu'], answer['vram_mib']), (runtime.GPU, 16380))
        with self.assertRaisesRegex(ValueError, 'requires_one_verified_a4500'):
            self.startup(manifest={'gpu': runtime.GPU}, digest=digest)

    def test_new_plan_binds_exact_gpu_and_manifest_and_preserves_execution_limits(self):
        state = self.state()
        execution = service_job.Runtime(state_root=self.root / 'state')
        command = execution.command(self.root, state, 300.)
        plan = read_json(self.root / 'execution-plan.json')
        self.assertEqual(plan['gpu'], self.release['gpu'])
        self.assertEqual(plan['image_manifest_sha256'], self.release['image_manifest_sha256'])
        self.assertEqual(plan['jobs'][0]['version'], 'B')
        self.assertEqual(plan['limits'], {
            'max_jobs': 1, 'max_attempts_per_job': 1, 'gpu_concurrency': 1,
            'automatic_retries': 0, 'gemini_calls': 0, 'embedding_calls': 0,
            'per_job_timeout_seconds': 300., 'whole_plan_timeout_seconds': 300.,
        })
        self.assertEqual(command[command.index('--version') + 1], 'B')
        before = (self.root / 'execution-plan.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'already_exists_no_automatic_rerun'):
            execution.command(self.root, state, 300.)
        self.assertEqual((self.root / 'execution-plan.json').read_bytes(), before)

    def test_legacy_plan_shape_stays_identical_under_new_deployment_selector(self):
        state = self.state(profiles.LEGACY_DEPLOYMENT)
        service_job.Runtime(state_root=self.root / 'state').command(self.root, state, 30.)
        plan = read_json(self.root / 'execution-plan.json')
        self.assertNotIn('gpu', plan)
        self.assertEqual(plan['image_manifest_sha256'], profiles._IMAGE_MANIFEST)

    def saved_fixture(self):
        case = saved.SavedServiceOutputTests('test_saved_a_and_b_complete_prepare_and_reuse_match_original_bytes')
        case.setUp()
        self.addCleanup(case.doCleanups)
        with patch.dict(os.environ, {'CCTV_ANALYSIS_DEPLOYMENT': profiles.LEGACY_DEPLOYMENT}):
            directory, state, folder, copied = case.fixture('B')
        return case, directory, state, folder, copied

    def test_saved_legacy_result_reads_and_reuses_original_bytes_with_new_selector(self):
        original = saved.inventory(saved.SAVED / 'input_001-B')
        case, directory, state, folder, copied = self.saved_fixture()
        before = saved.inventory(copied)
        helper_patch = patch.object(runtime, 'load_helpers', return_value=runner)
        helper_patch.start()
        self.addCleanup(helper_patch.stop)
        output, digest, result = case.runtime.completed_output(directory, state)
        self.assertEqual(output, copied / 'output')
        self.assertEqual(digest, saved.sha(saved.SAVED / 'input_001-B/output/result.json'))
        self.assertEqual(result['gpu'], runtime.GPU)
        self.assertEqual(case.runtime.prepare_output(directory, state), {'outcome': 'completed', 'event_count': 1})
        prepared = saved.inventory(directory / 'output')
        with patch('apps.be_worker.ab_evidence.prepare', side_effect=AssertionError('No repeated preparation')):
            case.runtime.prepare_output(directory, state)
        self.assertEqual(saved.inventory(directory / 'output'), prepared)
        self.assertEqual(saved.inventory(copied), before)
        self.assertEqual(saved.inventory(saved.SAVED / 'input_001-B'), original)
        self.assertEqual(read_json(folder / 'state.json')['jobs'][0]['attempts'], 1)

    def test_saved_legacy_plan_cannot_be_relabelled_as_new_gpu(self):
        case, directory, state, folder, _ = self.saved_fixture()
        plan = read_json(directory / 'execution-plan.json')
        plan['gpu'] = self.release['gpu']
        save(directory / 'execution-plan.json', plan)
        save(folder / 'plan.json', plan)
        with self.assertRaisesRegex(ValueError, 'completed_execution_receipt_required'):
            case.runtime.completed_output(directory, state)


if __name__ == '__main__':
    unittest.main()
