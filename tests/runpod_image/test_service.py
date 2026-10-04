"""Versioned API/ledger tests: ASGI and synthetic work only, no sockets/GPU/S3."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from apps.be_worker.analysis_profiles import gpu_identity, profile
from apps.be_worker.common import read_json, save
from apps.runpod_image import service
from apps.runpod_image.service_job import Runtime

TOKEN = 'synthetic-token-' + 'x' * 40
VIDEO = b'\x00\x00\x00\x18ftypisom-synthetic-video-only'
S3 = dict(bucket='synthetic-evidence', region='ap-northeast-2', prefix='cctv-evidence')
LIMITS = dict(max_jobs=4, per_job_seconds=30, whole_service_seconds=600)


async def request(app, path, method='GET', body=b'', *, token=TOKEN, version='A', headers=None):
    identity = gpu_identity(hashlib.sha256(body).hexdigest(), profile(version))
    supplied = {'authorization': 'Bearer ' + token, 'content-type': 'video/mp4',
                'content-length': str(len(body)), 'x-cctv-version': version,
                'x-cctv-input-sha256': identity['input_sha256'], 'x-cctv-gpu-key': identity['gpu_key']}
    supplied.update(headers or {})
    url = urlsplit(path)
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
             'http_version': '1.1', 'method': method, 'scheme': 'http', 'path': url.path,
             'raw_path': url.path.encode(), 'query_string': url.query.encode(), 'root_path': '',
             'headers': [(k.encode(), v.encode()) for k, v in supplied.items()],
             'client': ('127.0.0.1', 1), 'server': ('synthetic', 80)}
    sent, messages = False, []
    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await asyncio.Event().wait()
    async def send(message):
        messages.append(message)
    await app(scope, receive, send)
    code = next(m['status'] for m in messages if m['type'] == 'http.response.start')
    return code, json.loads(b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body'))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'jobs'
        self.calls = self.uploads = self.active = self.maximum = 0
        self.gate, self.started = asyncio.Event(), asyncio.Event()
        self.gate.set()
        self.fail_upload = self.fail_analysis = False
        self.outcome = 'completed'
        self.clock = 1000.
        self.app = self.new_app()
        await self.app.state.jobs.start()

    def new_app(self, **kwargs):
        return service.create_app(self.root, TOKEN, LIMITS, S3, runner=self.run_job,
                                  prepare=self.prepare, publisher=self.publish,
                                  clock=lambda: self.clock, **kwargs)

    async def asyncTearDown(self):
        await self.app.state.jobs.close()
        self.temp.cleanup()

    async def run_job(self, directory, state, seconds):
        self.calls += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.started.set()
        try:
            await self.gate.wait()
            if self.fail_analysis:
                raise ValueError('synthetic_inference_failure')
            save(directory / 'synthetic-result.json', {'outcome': self.outcome,
                 'event_count': 0 if self.outcome == 'no_candidates' else 1})
        finally:
            self.active -= 1

    def prepare(self, directory, state):
        return read_json(directory / 'synthetic-result.json')

    async def publish(self, directory, state):
        self.uploads += 1
        if self.fail_upload:
            raise TimeoutError('synthetic_s3_timeout')
        return dict(bucket=S3['bucket'], region=S3['region'],
                    manifest_key=f"cctv-evidence/{state['job_id']}/manifest.json", s3_uploaded=True,
                    analysis_profile=state['analysis_profile'], gpu_key=state['gpu_key'],
                    input_sha256=state['input_sha256'], manifest_sha256='f' * 64)

    async def submit(self, version='A', body=VIDEO, **kwargs):
        return await request(self.app, '/jobs', 'POST', body, version=version, **kwargs)

    async def test_auth_and_header_validation_before_admission(self):
        self.assertEqual((await request(self.app, '/health', token='wrong'))[0], 401)
        self.assertEqual((await self.submit(token='wrong'))[0], 401)
        self.assertEqual((await self.submit(headers={'x-cctv-version': 'C'}))[0], 400)
        self.assertEqual((await self.submit(headers={'x-cctv-gpu-key': 'changed'}))[0], 400)
        self.assertEqual((await self.submit(headers={'content-type': 'application/json'}))[0], 415)
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.app.state.jobs.by_gpu, {})
        code, health = await request(self.app, '/health')
        self.assertEqual(code, 200)
        self.assertEqual(health['api_schema'], 'runpod-ab-api-v1')
        self.assertEqual(set(health['profiles']), {'A', 'B'})
        self.assertFalse(health['checks_model_inference'])

    async def test_a_b_different_jobs_share_only_one_gpu_slot(self):
        self.gate.clear()
        _, a = await self.submit('A')
        await self.started.wait()
        _, b = await self.submit('B')
        self.assertNotEqual(a['job_id'], b['job_id'])
        self.gate.set()
        await self.app.state.jobs.queue.join()
        self.assertEqual((self.calls, self.maximum), (2, 1))
        for row in (a, b):
            state = self.app.state.jobs.get(row['job_id'])
            self.assertEqual((state['status'], state['attempts'], state['outcome']), ('succeeded', 1, 'completed'))

    async def test_duplicate_running_and_completed_requests_reuse_one_id(self):
        self.gate.clear()
        _, first = await self.submit()
        await self.started.wait()
        _, second = await self.submit()
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertTrue(second['reused'])
        self.gate.set()
        await self.app.state.jobs.queue.join()
        _, third = await self.submit()
        self.assertEqual(first['job_id'], third['job_id'])
        self.assertEqual((third['status'], self.calls), ('succeeded', 1))

    async def test_repeated_concurrent_requests_only_admit_once(self):
        responses = await asyncio.gather(self.submit(), self.submit(), self.submit())
        await self.app.state.jobs.queue.join()
        self.assertEqual(len({row[1]['job_id'] for row in responses}), 1)
        self.assertEqual(self.calls, 1)

    async def test_bad_upload_hash_preserves_failed_input_without_model_execution(self):
        sha = 'f' * 64
        key = gpu_identity(sha, profile('A'))['gpu_key']
        code, _ = await self.submit(headers={'x-cctv-input-sha256': sha, 'x-cctv-gpu-key': key})
        self.assertEqual(code, 400)
        self.assertEqual(self.calls, 0)
        self.assertEqual(len(list(self.root.glob('*/input.mp4'))), 1)
        row = read_json(next(self.root.glob('*/status.json')))
        self.assertEqual((row['status'], row['phase'], row['attempts']), ('failed', 'input', 0))

    async def test_unknown_ack_lookup_reads_existing_job_and_never_submits(self):
        _, job = await self.submit()
        await self.app.state.jobs.queue.join()
        code, found = await request(self.app, '/jobs/by-gpu-key?gpu_key=' + job['gpu_key'])
        self.assertEqual((code, found['job_id'], self.calls), (200, job['job_id'], 1))
        self.assertEqual((await request(self.app, '/jobs/by-gpu-key?gpu_key=missing'))[0], 404)

    async def test_no_candidates_and_analysis_failure_remain_distinct(self):
        self.outcome = 'no_candidates'
        _, a = await self.submit('A')
        await self.app.state.jobs.queue.join()
        self.fail_analysis = True
        _, b = await self.submit('B')
        await self.app.state.jobs.queue.join()
        a, b = [self.app.state.jobs.get(row['job_id']) for row in (a, b)]
        self.assertEqual((a['status'], a['outcome'], a['event_count']), ('succeeded', 'no_candidates', 0))
        self.assertEqual((b['status'], b['error']), ('failed', 'analysis_failed'))
        self.assertNotIn('outcome', b)

    async def test_s3_resume_reuses_existing_result_and_attempt_count(self):
        self.fail_upload = True
        _, job = await self.submit()
        await self.app.state.jobs.queue.join()
        failed = self.app.state.jobs.get(job['job_id'])
        self.assertEqual((failed['status'], failed['phase']), ('failed', 's3_upload'))
        self.fail_upload = False
        code, resumed = await request(self.app, f"/jobs/{job['job_id']}/resume-evidence", 'POST')
        self.assertEqual(code, 202)
        await self.app.state.jobs.queue.join()
        self.assertEqual((self.calls, self.uploads), (1, 2))
        self.assertEqual(self.app.state.jobs.get(job['job_id'])['status'], 'succeeded')

    async def test_restart_reuses_completed_job_without_new_run(self):
        _, first = await self.submit()
        await self.app.state.jobs.queue.join()
        await self.app.state.jobs.close()
        self.app = self.new_app()
        await self.app.state.jobs.start()
        _, repeated = await self.submit()
        self.assertEqual((first['job_id'], self.calls), (repeated['job_id'], 1))

    async def test_restart_does_not_repeat_uncertain_gpu_execution(self):
        self.gate.clear()
        _, job = await self.submit()
        await self.started.wait()
        await self.app.state.jobs.close()
        self.app = self.new_app()
        await self.app.state.jobs.start()
        self.gate.set()
        _, duplicate = await self.submit()
        self.assertEqual((duplicate['status'], duplicate['job_id'], self.calls), ('failed', job['job_id'], 1))
        code, other = await self.submit('B')
        self.assertEqual((code, other['error']), (429, 'uncertain_execution_requires_reconciliation'))
        self.assertEqual((await request(self.app, f"/jobs/{job['job_id']}/resume-evidence", 'POST'))[0], 409)

    async def test_deadline_blocks_unattempted_queued_job_and_fresh_admission(self):
        self.gate.clear()
        _, a = await self.submit('A')
        await self.started.wait()
        _, b = await self.submit('B')
        self.clock += 601
        self.gate.set()
        await self.app.state.jobs.queue.join()
        state = self.app.state.jobs.get(b['job_id'])
        self.assertEqual((state['status'], state['attempts'], state['error']), ('failed', 0, 'service_time_budget_exhausted'))
        self.assertEqual(self.calls, 1)
        code, result = await self.submit(body=VIDEO + b'new')
        self.assertEqual((code, result['error']), (429, 'service_time_budget_exhausted'))

    async def test_job_cap_and_budget_cannot_reset_on_restart(self):
        for suffix in (b'1', b'2', b'3', b'4'):
            self.assertEqual((await self.submit(body=VIDEO + suffix))[0], 202)
            await self.app.state.jobs.queue.join()
        code, value = await self.submit(body=VIDEO + b'5')
        self.assertEqual((code, value['error']), (429, 'service_job_budget_exhausted'))
        await self.app.state.jobs.close()
        self.app = self.new_app()
        self.app.state.jobs.limits['max_jobs'] += 1
        with self.assertRaisesRegex(ValueError, 'budget_changed'):
            await self.app.state.jobs.start()

    async def test_second_process_cannot_open_same_ledger(self):
        other = self.new_app()
        with self.assertRaises(OSError):
            await other.state.jobs.start()
        await other.state.jobs.close()

    async def test_missing_budget_with_retained_jobs_does_not_reset_approval(self):
        await self.submit()
        await self.app.state.jobs.queue.join()
        await self.app.state.jobs.close()
        (self.root / 'budget.json').unlink()  # Synthetic temporary ledger only.
        self.app = self.new_app()
        with self.assertRaisesRegex(ValueError, 'budget_missing'):
            await self.app.state.jobs.start()
        self.assertEqual(self.calls, 1)


class RuntimeContractTests(unittest.TestCase):
    def test_finite_limits_are_required_without_defaults(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(KeyError):
                service.limits_from_environment()
        for change in ({'max_jobs': 0}, {'per_job_seconds': float('inf')}, {'whole_service_seconds': -1}):
            with self.assertRaises(ValueError):
                service.validate_limits(dict(LIMITS, **change))

    def test_production_startup_checks_gpu_without_loading_models(self):
        runtime = Runtime()
        manifest_hash = profile('A')['image_manifest_sha256']
        output = type('Output', (), {'stdout': 'NVIDIA RTX A4500, 20470\n'})()
        with patch('apps.runpod_image.service_job.runtime.inspect_image', return_value=({}, manifest_hash)), \
             patch('apps.runpod_image.service_job.runtime.check_environment', return_value={'mismatches': {}}), \
             patch('apps.runpod_image.service_job.sys.platform', 'linux'), \
             patch('apps.runpod_image.service_job.subprocess.run', return_value=output), \
             patch.dict('sys.modules', {'torch': None, 'ultralytics': None}):
            for memory_mib in (1, 16384, 19999, 20000, 20470):
                with self.subTest(memory_mib=memory_mib):
                    output.stdout = f'NVIDIA RTX A4500, {memory_mib}\n'
                    result = runtime.startup()
                    self.assertEqual(result['gpu'], 'NVIDIA RTX A4500')
                    self.assertEqual(result['model_calls'], 0)
                    self.assertFalse(result['model_libraries_imported'])
            for detected in ('NVIDIA RTX A4000, 16384\n', 'NVIDIA RTX 2000 Ada Generation, 16380\n',
                             'NVIDIA RTX A4500, 20470\nNVIDIA RTX A4500, 20470\n',
                             '', 'NVIDIA RTX A4500\n', 'NVIDIA RTX A4500, 20470, extra\n'):
                with self.subTest(detected=detected):
                    output.stdout = detected
                    with self.assertRaisesRegex(ValueError, 'service_requires_one_verified_a4500'):
                        runtime.startup()

    def test_startup_rejects_invalid_memory_reading_without_capacity_threshold(self):
        runtime = Runtime()
        manifest_hash = profile('A')['image_manifest_sha256']
        output = type('Output', (), {'stdout': ''})()
        with patch('apps.runpod_image.service_job.runtime.inspect_image', return_value=({}, manifest_hash)), \
             patch('apps.runpod_image.service_job.runtime.check_environment', return_value={'mismatches': {}}), \
             patch('apps.runpod_image.service_job.sys.platform', 'linux'), \
             patch('apps.runpod_image.service_job.subprocess.run', return_value=output), \
             patch.dict('sys.modules', {'torch': None, 'ultralytics': None}):
            for memory_mib in ('', 'N/A', '0', '-1', '19999.5', '1_6384', '１６３８４'):
                with self.subTest(memory_mib=memory_mib):
                    output.stdout = f'NVIDIA RTX A4500, {memory_mib}\n'
                    with self.assertRaisesRegex(ValueError, 'service_gpu_memory_invalid'):
                        runtime.startup()

    def test_runtime_command_fixes_one_version_one_attempt_and_common_lock_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            identity = gpu_identity(hashlib.sha256(VIDEO).hexdigest(), profile('B'))
            state = dict(job_id='a' * 32, input_bytes=len(VIDEO), input_sha256=identity['input_sha256'],
                         gpu_key=identity['gpu_key'], analysis_profile=profile('B'))
            runtime = Runtime()
            command = runtime.command(root, state, 25.)
            plan = read_json(root / 'execution-plan.json')
            self.assertIn('/opt/cctv-image/runtime/image_runtime.py', command)
            self.assertEqual(plan['state_root'], '/workspace/cctv-image-state')
            self.assertEqual((plan['jobs'][0]['version'], plan['limits']['max_jobs']), ('B', 1))
            self.assertEqual((plan['limits']['gpu_concurrency'], plan['limits']['automatic_retries']), (1, 0))
            with self.assertRaisesRegex(ValueError, 'no_automatic_rerun'):
                runtime.command(root, state, 25.)


if __name__ == '__main__':
    unittest.main()
