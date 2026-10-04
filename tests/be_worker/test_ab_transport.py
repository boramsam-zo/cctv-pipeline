"""Versioned BE transport and queue preflight; no remote server or models."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from apps.be_worker import job_queue, tracing, worker
from apps.be_worker.analysis_profiles import gpu_identity, profile
from apps.be_worker.common import WorkerError, read_json, save
from apps.be_worker.runpod_api import ApiError, RunpodAPI, validate_status

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ab_transport_queue_fixture', ROOT / 'tests/be_worker/test_queue.py')
queue_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue_fixture)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / 'video.mp4'
        self.video.write_bytes(b'\x00\x00\x00\x18ftypisom-synthetic')
        self.config = dict(runpod_url='https://synthetic.invalid', s3_bucket='synthetic-evidence', s3_region='ap-northeast-2')
        self.folder = self.root / ('b' * 32)
        self.state = worker.create_job(self.video, self.folder, self.config)
        self.identity = gpu_identity(self.state['input_sha256'], profile('B'))
        self.state.update(analysis_profile=self.identity['profile'], gpu_key=self.identity['gpu_key'])
        save(self.folder / 'state.json', self.state)
        self.api = RunpodAPI(self.config['runpod_url'], 'synthetic-token')
        self.trace = tracing.Recorder(self.folder, 'run', self.state)

    def row(self, status='queued'):
        return dict(schema_version='runpod-ab-api-v1', job_id='a' * 32, status=status,
                    analysis_profile=deepcopy(self.identity['profile']), profile_sha256=self.identity['profile_sha256'],
                    gpu_key=self.identity['gpu_key'], input_sha256=self.state['input_sha256'],
                    input_bytes=self.state['input_bytes'])

    def health(self):
        return {'status': 'ok', 'api_schema': 'runpod-ab-api-v1', 'storage': 's3',
                'gpu_concurrency': 1, 'supported_versions': ['A', 'B'],
                'profiles': {v: gpu_identity('0' * 64, profile(v))['profile_sha256'] for v in ('A', 'B')}}

    def test_health_accepts_verified_ab_for_connection_and_selected_job(self):
        with patch.object(self.api, 'request', return_value=self.health()):
            self.assertEqual(self.api.health()['supported_versions'], ['A', 'B'])
            self.assertEqual(self.api.health(expected_profile=profile('B'))['api_schema'], 'runpod-ab-api-v1')
        bad = self.health()
        bad['profiles']['B'] = 'f' * 64
        with patch.object(self.api, 'request', return_value=bad):
            with self.assertRaises(WorkerError):
                self.api.health(expected_profile=profile('B'))

    def test_legacy_health_cannot_satisfy_selected_ab_preflight(self):
        with patch.object(self.api, 'request', return_value={'status': 'ok', 'api_schema': 'runpod-local-api-v1', 'storage': 's3'}):
            self.assertEqual(self.api.health()['api_schema'], 'runpod-local-api-v1')
            with self.assertRaises(WorkerError):
                self.api.health(expected_profile=profile('B'))

    def test_submit_transmits_frozen_headers_and_checks_ack(self):
        calls = []
        def request(path, method, stream, size, headers):
            calls.append((path, method, stream.read(), size, headers))
            return self.row()
        with patch.object(self.api, 'request', side_effect=request):
            self.assertEqual(self.api.submit_versioned(self.video, self.state), 'a' * 32)
        _, method, body, size, headers = calls[0]
        self.assertEqual((method, body, size), ('POST', self.video.read_bytes(), self.state['input_bytes']))
        self.assertEqual(headers['X-CCTV-Version'], 'B')
        self.assertEqual(headers['X-CCTV-GPU-Key'], self.identity['gpu_key'])

    def test_ack_profile_input_and_gpu_mismatch_fail_after_single_request(self):
        changes = [lambda x: x.update(analysis_profile=profile('A')),
                   lambda x: x.update(input_sha256='f' * 64),
                   lambda x: x.update(gpu_key='gpu-v1:' + 'f' * 64),
                   lambda x: x.update(profile_sha256='f' * 64),
                   lambda x: x.update(input_bytes=True)]
        for change in changes:
            row = self.row()
            change(row)
            with patch.object(self.api, 'request', return_value=row) as request:
                with self.assertRaises(WorkerError):
                    self.api.submit_versioned(self.video, self.state)
                self.assertEqual(request.call_count, 1)

    def test_worker_uncertain_ack_never_posts_again(self):
        def request(path, *args):
            if path == '/health':
                return self.health()
            raise ApiError('network_error')
        with patch.object(self.api, 'request', side_effect=request) as calls:
            worker.submit_once(self.folder, self.state, self.api, self.trace)
            self.assertEqual((self.state['status'], self.state['post_attempts']), ('submission_unknown', 1))
            with self.assertRaises(WorkerError):
                worker.submit_once(self.folder, self.state, self.api, self.trace)
            self.assertEqual(calls.call_count, 2)

    def test_lookup_validates_identity_uses_get_and_does_not_submit(self):
        with patch.object(self.api, 'request', return_value=self.row('running')) as request:
            self.assertEqual(self.api.lookup_versioned(self.state), 'a' * 32)
            self.assertEqual(len(request.call_args.args), 1)
            self.assertTrue(request.call_args.args[0].startswith('/jobs/by-gpu-key?gpu_key=gpu-v1%3A'))

    def test_resume_evidence_verifies_existing_provider_and_profile(self):
        self.state['provider_job_id'] = 'a' * 32
        with patch.object(self.api, 'request', return_value=self.row('running')) as request:
            self.assertEqual(self.api.resume_evidence(self.state), 'a' * 32)
            self.assertEqual(request.call_args.args[0], '/jobs/' + 'a' * 32 + '/resume-evidence')
            self.assertEqual(request.call_args.args[1:], ('POST', b''))

    def test_success_requires_versioned_s3_identity_and_manifest_digest(self):
        self.state['provider_job_id'] = 'a' * 32
        row = self.row('succeeded')
        row.update(storage='s3', s3_uploaded=True,
                   s3=dict(bucket=self.config['s3_bucket'], region=self.config['s3_region'],
                       manifest_key='cctv-evidence/' + 'a' * 32 + '/manifest.json', s3_uploaded=True,
                       analysis_profile=profile('B'), gpu_key=self.identity['gpu_key'],
                       input_sha256=self.state['input_sha256'], manifest_sha256='f' * 64))
        self.assertEqual(validate_status(row, self.state)['s3']['manifest_sha256'], 'f' * 64)
        row['s3']['gpu_key'] = 'changed'
        with self.assertRaises(WorkerError):
            validate_status(row, self.state)

    def test_validated_provider_phase_and_failure_are_safe_to_display(self):
        self.state['provider_job_id'] = 'a' * 32
        row = self.row('failed')
        row.update(phase='s3_upload', error='s3_upload_failed', attempts=1,
                   outcome='completed', event_count=1)
        checked = validate_status(row, self.state)
        self.assertEqual((checked['phase'], checked['error'], checked['attempts']), ('s3_upload', 's3_upload_failed', 1))
        row['error'] = 'arbitrary sensitive output'
        with self.assertRaises(WorkerError):
            validate_status(row, self.state)

    def test_service_budget_rejection_is_not_generic_retriable_queue_429(self):
        body = io.BytesIO(json.dumps({'error': 'service_job_budget_exhausted'}).encode())
        error = urllib.error.HTTPError('https://synthetic.invalid', 429, 'limit', {}, body)
        with patch.object(self.api.opener, 'open', side_effect=error):
            with self.assertRaises(ApiError) as caught:
                self.api.request('/jobs', 'POST', b'data')
        self.assertEqual(caught.exception.code, 'provider_service_job_budget_exhausted')

    def coordinator(self):
        store = queue_fixture.MemoryStore()
        store.insert('synthetic-analysis-key', self.folder.name, self.state)
        return store, job_queue.Coordinator(store, self.root, lambda state: self.api, slots=1)

    def test_queue_profile_mismatch_is_blocked_before_submit_intent(self):
        store, coordinator = self.coordinator()
        tampered = read_json(self.folder / 'state.json')
        changed = gpu_identity(tampered['input_sha256'], profile('A'))
        tampered.update(analysis_profile=changed['profile'], gpu_key=changed['gpu_key'])
        save(self.folder / 'state.json', tampered)
        with patch.object(self.api, 'request') as request:
            with self.assertRaisesRegex(WorkerError, 'queue_spool_analysis_identity_mismatch'):
                coordinator.dispatch_tick()
            self.assertFalse(request.called)
        self.assertEqual(store.rows()[0]['submit_attempts'], 0)

    def test_queue_wrong_service_profile_is_blocked_before_db_intent(self):
        store, coordinator = self.coordinator()
        health = self.health()
        health['profiles']['B'] = 'f' * 64
        with patch.object(self.api, 'request', return_value=health):
            with self.assertRaises(WorkerError):
                coordinator.dispatch_tick()
        self.assertEqual(store.rows()[0]['submit_attempts'], 0)
        self.assertEqual(store.rows()[0]['state']['status'], 'prepared')

    def test_queue_versioned_dispatch_and_load_preserve_profile(self):
        store, coordinator = self.coordinator()
        with patch.object(self.api, 'request', side_effect=lambda path, *args: self.health() if path == '/health' else self.row()):
            coordinator.dispatch_tick()
        state = coordinator.load(store.rows()[0])
        self.assertEqual((state['status'], state['post_attempts']), ('waiting', 1))
        self.assertEqual(state['analysis_profile'], profile('B'))
        self.assertEqual(store.rows()[0]['submit_attempts'], 1)


if __name__ == '__main__':
    unittest.main()
