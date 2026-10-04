"""Portable CUDA contracts using synthetic provenance and copied test media.

Changed GPU fields below are synthetic fixtures, never measured GPU results.
Original P4 input/media/result files remain untouched; no model or device runs.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker import ab_evidence as evidence, analysis_profiles as profiles
from apps.be_worker.common import WorkerError
from apps.be_worker.runpod_api import RunpodAPI
from tests.be_worker import test_ab_evidence as fixtures


class PortableGPUProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cctv-portable-contract-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalogs = {}
        for deployment, gpu in ((profiles.RTX2000ADA_DEPLOYMENT, 'NVIDIA RTX 2000 Ada Generation'),
                                (profiles.PORTABLE_DEPLOYMENT, profiles.PORTABLE_GPU)):
            path = self.root / (deployment + '.json')
            path.write_text(json.dumps({
                'schema_version': 'cctv-analysis-deployment-v1', 'deployment': deployment, 'gpu': gpu,
                'image_manifest_sha256': hashlib.sha256(('synthetic manifest ' + deployment).encode()).hexdigest(),
                'execution_adapter_sha256': hashlib.sha256(('synthetic adapter ' + deployment).encode()).hexdigest(),
            }))
            self.catalogs[deployment] = path
        for context in (
            patch.dict(profiles._RELEASE_CATALOGS, self.catalogs, clear=True),
            patch.dict(os.environ, {'CCTV_ANALYSIS_DEPLOYMENT': profiles.PORTABLE_DEPLOYMENT}),
            patch.dict('sys.modules', {name: None for name in
                       ('torch', 'torchvision', 'ultralytics', 'pytorchvideo', 'cv2', 'av', 'boto3')}),
            patch('socket.socket', side_effect=AssertionError('No network in portable profile tests')),
            patch('subprocess.Popen', side_effect=AssertionError('No process in portable profile tests')),
        ):
            context.start()
            self.addCleanup(context.stop)

    def health(self, deployment):
        return {'status': 'ok', 'api_schema': 'runpod-ab-api-v1', 'storage': 's3',
                'gpu_concurrency': 1, 'supported_versions': ['A', 'B'],
                'profiles': {v: profiles.gpu_identity('0' * 64, profiles.profile(v, deployment=deployment))['profile_sha256']
                             for v in profiles.VERSIONS}}

    def source(self, name, gpu, *, normalized_gpu=None):
        source = self.root / name
        shutil.copytree(fixtures.P4 / 'input_001-B/output', source)
        raw = fixtures.read(source / 'raw/runtime.json')
        raw['gpu'] = gpu
        fixtures.save(source / 'raw/runtime.json', raw)
        normalized = fixtures.read(source / 'result.json')
        normalized['gpu'] = gpu if normalized_gpu is None else normalized_gpu
        fixtures.save(source / 'result.json', normalized)
        fixtures.reseal(source)
        return source

    def prepare(self, source, *, deployment=profiles.PORTABLE_DEPLOYMENT):
        return evidence.prepare(source, source.with_name(source.name + '-prepared'),
                                snapshot=profiles.profile('B', deployment=deployment),
                                input_sha256=fixtures.read(source / 'result.json')['input_sha256'],
                                result_sha256=fixtures.digest(source / 'result.json'))

    def test_portable_marker_and_exact_release_are_distinct_from_both_old_profiles(self):
        for version in profiles.VERSIONS:
            portable = profiles.profile(version)
            self.assertEqual(portable['runtime']['gpu'], profiles.PORTABLE_GPU)
            self.assertEqual(profiles.deployment_for_profile(portable), profiles.PORTABLE_DEPLOYMENT)
            identities = set()
            for deployment in (profiles.LEGACY_DEPLOYMENT, profiles.RTX2000ADA_DEPLOYMENT,
                               profiles.PORTABLE_DEPLOYMENT):
                frozen = profiles.profile(version, deployment=deployment)
                self.assertEqual(profiles.validate_profile(frozen), frozen)
                self.assertEqual(profiles.deployment_for_profile(frozen), deployment)
                identities.add(profiles.gpu_identity('1' * 64, frozen)['gpu_key'])
                self.assertEqual(frozen['model_files'], portable['model_files'])
                self.assertEqual(frozen['code_files'], portable['code_files'])
                self.assertEqual(frozen['execution_settings'], portable['execution_settings'])
            self.assertEqual(len(identities), 3)

    def test_missing_or_corrupted_current_catalog_does_not_invalidate_old_profiles_or_health(self):
        old = {d: profiles.profile('B', deployment=d)
               for d in (profiles.LEGACY_DEPLOYMENT, profiles.RTX2000ADA_DEPLOYMENT)}
        health = {d: self.health(d) for d in old}
        api = RunpodAPI('https://synthetic.invalid', 'synthetic-token')
        for mode in ('missing', 'malformed'):
            path = self.catalogs[profiles.PORTABLE_DEPLOYMENT]
            if mode == 'missing':
                path.unlink()
            else:
                path.write_text('{broken')
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'catalog_unavailable'):
                profiles.profile('B')
            for deployment, frozen in old.items():
                self.assertEqual(profiles.validate_profile(frozen), frozen)
                with patch.object(api, 'request', return_value=health[deployment]):
                    self.assertEqual(api.health(expected_profile=frozen)['profiles'], health[deployment]['profiles'])

    def test_health_derives_both_versions_from_each_explicit_release(self):
        api = RunpodAPI('https://synthetic.invalid', 'synthetic-token')
        for deployment in (profiles.LEGACY_DEPLOYMENT, profiles.RTX2000ADA_DEPLOYMENT,
                           profiles.PORTABLE_DEPLOYMENT):
            health = self.health(deployment)
            with self.subTest(deployment=deployment), patch.object(api, 'request', return_value=health):
                frozen = profiles.profile('B', deployment=deployment)
                self.assertEqual(api.health(expected_profile=frozen)['profiles'], health['profiles'])
                if deployment != profiles.PORTABLE_DEPLOYMENT:
                    with self.assertRaisesRegex(WorkerError, 'profile_not_ready'):
                        api.health()

    def test_actual_name_never_replaces_frozen_portable_contract(self):
        current = profiles.profile('B')
        for gpu in ('Tesla T4', 'NVIDIA RTX 2000 Ada Generation', 'auto', 'CPU'):
            changed = deepcopy(current)
            changed['runtime']['gpu'] = gpu
            with self.subTest(gpu=gpu), self.assertRaisesRegex(ValueError, 'profile_changed'):
                profiles.validate_profile(changed)
        changed = deepcopy(current)
        changed['execution_adapter_sha256'] = profiles.profile(
            'B', deployment=profiles.RTX2000ADA_DEPLOYMENT)['execution_adapter_sha256']
        with self.assertRaisesRegex(ValueError, 'profile_changed'):
            profiles.deployment_for_profile(changed)

    def test_device_name_format_accepts_tesla_without_requiring_nvidia_prefix(self):
        for name in ('Tesla T4', 'NVIDIA A100-SXM4-40GB', 'NVIDIA RTX 2000 Ada Generation', 'X' * 160):
            with self.subTest(name=name):
                self.assertTrue(profiles.valid_gpu_name(name))
        for name in (None, 123, '', ' Tesla T4', 'Tesla T4 ', 'Tesla\nT4', '\x7f',
                     '한글', 'X' * 161, 'auto', 'AUTO', profiles.PORTABLE_GPU,
                     'cpu', 'CPU:0', 'Intel CPU', 'gpu', 'cuda', 'unknown', 'none', 'null'):
            with self.subTest(name=name):
                self.assertFalse(profiles.valid_gpu_name(name))

    def test_expected_device_selector_does_not_change_portable_cache_identity(self):
        expected = profiles.gpu_identity('1' * 64, profiles.profile('B'))
        for device in ('auto', 'Tesla T4', 'NVIDIA RTX 2000 Ada Generation'):
            with patch.dict(os.environ, {'CCTV_EXPECTED_GPU': device}):
                self.assertEqual(profiles.gpu_identity('1' * 64, profiles.profile('B')), expected)

    def test_synthetic_results_keep_actual_gpu_names_and_share_portable_evidence_key(self):
        original = fixtures.P4 / 'input_001-B/output'
        before = {p.relative_to(original).as_posix(): fixtures.digest(p)
                  for p in original.rglob('*') if p.is_file()}
        keys = []
        for index, device in enumerate(('Tesla T4', 'NVIDIA RTX 2000 Ada Generation')):
            source = self.source(f'synthetic-{index}', device)
            answer = self.prepare(source)
            output = source.with_name(source.name + '-prepared')
            self.assertEqual(fixtures.read(output / 'normalized-result.json')['gpu'], device)
            self.assertEqual(fixtures.read(output / 'raw/runtime.json')['gpu'], device)
            self.assertEqual((output / 'normalized-result.json').read_bytes(), (source / 'result.json').read_bytes())
            self.assertEqual(evidence.validate_local(output)['gpu_key'], answer['index']['gpu_key'])
            keys.append(answer['index']['gpu_key'])
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(before, {p.relative_to(original).as_posix(): fixtures.digest(p)
                                  for p in original.rglob('*') if p.is_file()})

    def test_portable_evidence_rejects_marker_cpu_or_mismatched_raw_gpu(self):
        for index, (actual, normalized, error) in enumerate((
                ('CPU', 'CPU', 'runtime_environment'),
                (profiles.PORTABLE_GPU, profiles.PORTABLE_GPU, 'runtime_environment'),
                ('auto', 'auto', 'runtime_environment'),
                ('Tesla T4', 'NVIDIA RTX 2000 Ada Generation', 'runtime_summary'))):
            source = self.source(f'invalid-{index}', actual, normalized_gpu=normalized)
            with self.subTest(actual=actual, normalized=normalized), self.assertRaisesRegex(ValueError, error):
                self.prepare(source)
            self.assertFalse(source.with_name(source.name + '-prepared').exists())

    def test_fixed_legacy_and_rtx_profiles_still_require_exact_device_names(self):
        for index, deployment in enumerate((profiles.LEGACY_DEPLOYMENT, profiles.RTX2000ADA_DEPLOYMENT)):
            source = self.source(f'wrong-fixed-{index}', 'Tesla T4')
            with self.subTest(deployment=deployment), self.assertRaisesRegex(ValueError, 'runtime_environment'):
                self.prepare(source, deployment=deployment)
            device = profiles.profile('B', deployment=deployment)['runtime']['gpu']
            valid = self.source(f'valid-fixed-{index}', device)
            self.assertEqual(self.prepare(valid, deployment=deployment)['status'], 'ab_evidence_prepared')

    def test_portable_gpu_flexibility_does_not_relax_cuda_environment(self):
        source = self.source('wrong-cuda', 'Tesla T4')
        raw = fixtures.read(source / 'raw/runtime.json')
        raw['cuda_runtime'] = 'different'
        fixtures.save(source / 'raw/runtime.json', raw)
        fixtures.reseal(source)
        with self.assertRaisesRegex(ValueError, 'runtime_environment'):
            self.prepare(source)


if __name__ == '__main__':
    unittest.main()
