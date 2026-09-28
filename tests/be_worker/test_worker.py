"""Synthetic orchestration, recovery and privacy checks; no remote services/models."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker import worker, tracing
from apps.be_worker.common import WorkerError, locked, read_json, save
from apps.be_worker.runpod_api import ApiError, RunpodAPI

# Reuse the existing producer -> S3 -> scene contract fixture, not a new parallel schema.
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('scene_fixture', ROOT / 'tests/scene_description/test_scene.py')
scene_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scene_fixture)


class FakeAPI:
    def __init__(self, state):
        self.state, self.posts, self.gets = state, 0, 0
        self.job = 'a' * 32
        self.submit_error = None
        self.remote_states = ['succeeded']
        self.change = lambda row: row

    def health(self):
        return {'api_schema': 'runpod-local-api-v1', 'storage': 's3'}

    def submit(self, path, size):
        self.posts += 1
        assert path.stat().st_size == size
        if self.submit_error:
            raise self.submit_error
        return self.job

    def status(self, identifier):
        self.gets += 1
        status = self.remote_states.pop(0) if len(self.remote_states) > 1 else self.remote_states[0]
        row = {'schema_version': 'runpod-local-api-v1', 'job_id': identifier, 'status': status,
               'input_bytes': self.state['input_bytes'], 'input_sha256': self.state['input_sha256'],
               's3_uploaded': True, 'storage': 's3',
               's3': {'bucket': 'cctv-test-bucket', 'region': 'ap-northeast-2',
                      'manifest_key': f'cctv-evidence/{identifier}/manifest.json', 's3_uploaded': True}}
        return self.change(row)


class Sink:
    target = 'test-target'
    project = 'synthetic-worker-test'
    def __init__(self):
        self.rows = []
        self.fail = False
    def send(self, row):
        if self.fail:
            raise RuntimeError('sensitive-token-never-log-this')
        self.rows.append(row)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / 'original.mp4'
        self.video.write_bytes(b'\x00\x00\x00\x18ftypisom-synthetic-not-real-video')
        self.directory = self.root / 'job'
        self.config = {'runpod_url': 'https://test.invalid', 's3_bucket': 'cctv-test-bucket', 's3_region': 'ap-northeast-2'}
        self.state = worker.create_job(self.video, self.directory, self.config)
        self.api = FakeAPI(self.state)
        self.trace = tracing.Recorder(self.directory, 'run', self.state)

    def tearDown(self):
        self.temp.cleanup()

    def submitted(self):
        worker.submit_once(self.directory, self.state, self.api, self.trace)

    def completed(self):
        self.submitted()
        worker.poll(self.directory, self.state, self.api, self.trace, wait_seconds=0)

    def s3(self, empty=False):
        root = self.root / 'producer'
        root.mkdir()
        config, event, reader = scene_fixture.fixture(root)
        manifest_key = f"cctv-evidence/{self.api.job}/manifest.json"
        manifest = json.loads(reader.objects[manifest_key]['Body'])
        manifest['input_sha256'] = self.state['input_sha256']
        if empty:
            key = f"cctv-evidence/{self.api.job}/evidence.json"
            index = json.loads(reader.objects[key]['Body'])
            index['events'] = []
            data = json.dumps(index).encode()
            reader.objects[key]['Body'] = data
            manifest['files']['evidence.json'].update(bytes=len(data), sha256=worker.digest(data))
        reader.objects[manifest_key]['Body'] = json.dumps(manifest).encode()
        return reader

    def test_submit_poll_collect_compatible_with_scene_fetch(self):
        self.completed()
        reader = self.s3()
        receipt = worker.collect(self.directory, self.state, reader, self.trace)
        self.assertEqual((self.api.posts, receipt['status'], receipt['event_count']), (1, 'handoff_ready', 1))
        self.assertFalse(receipt['media_bytes_verified'])
        self.assertEqual(len(reader.reads), 2)
        config = read_json(self.directory / 'scene-event_000.json')
        result = scene_fixture.scene.fetch(config, self.root / 'scene-input', reader)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['gemini_api_calls'], 0)

    def test_poll_timeout_resumes_without_resubmit_or_original(self):
        self.api.remote_states = ['queued', 'succeeded']
        self.submitted()
        worker.poll(self.directory, self.state, self.api, self.trace, wait_seconds=0)
        self.assertEqual(self.state['status'], 'waiting')
        self.video.unlink()
        restored = worker.load_state(self.directory)
        worker.resume(self.directory, restored, self.api, self.trace, wait_seconds=0)
        self.assertEqual(restored['status'], 'evidence_ready')
        self.assertEqual(self.api.posts, 1)

    def test_snapshot_is_stable_and_private(self):
        original = (self.directory / 'input.mp4').read_bytes()
        self.video.write_bytes(b'changed original')
        self.assertEqual((self.directory / 'input.mp4').read_bytes(), original)
        self.assertEqual((self.directory / 'state.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(FileExistsError):
            worker.create_job(self.directory / 'input.mp4', self.directory, self.config)

    def test_lost_submit_response_never_retries(self):
        self.api.submit_error = ApiError('network_error')
        self.submitted()
        self.assertEqual(self.state['status'], 'submission_unknown')
        with self.assertRaises(WorkerError):
            worker.resume(self.directory, self.state, self.api, self.trace, 0)
        with self.assertRaises(WorkerError):
            self.submitted()
        self.assertEqual(self.api.posts, 1)

    def test_recover_known_job_id_checks_source_first(self):
        self.api.submit_error = ApiError('network_error')
        self.submitted()
        worker.resume(self.directory, self.state, self.api, self.trace, 0, self.api.job)
        self.assertEqual(self.state['status'], 'evidence_ready')
        self.assertEqual(self.api.posts, 1)

    def test_recovery_wrong_source_cannot_bind(self):
        self.api.submit_error = ApiError('network_error')
        self.submitted()
        self.api.change = lambda row: dict(row, input_sha256='f'*64)
        with self.assertRaises(WorkerError):
            worker.resume(self.directory, self.state, self.api, self.trace, 0, self.api.job)
        self.assertIsNone(self.state['provider_job_id'])

    def test_429_is_rejected_without_retry(self):
        self.api.submit_error = ApiError('http_429', 429)
        self.submitted()
        self.assertEqual(self.state['status'], 'submission_rejected')
        self.assertEqual(self.api.posts, 1)

    def test_500_is_ambiguous_not_safe_to_resubmit(self):
        self.api.submit_error = ApiError('http_500', 500)
        self.submitted()
        self.assertEqual(self.state['status'], 'submission_unknown')

    def test_provider_failure_not_no_candidates(self):
        self.api.remote_states = ['failed']
        self.completed()
        self.assertEqual(self.state['status'], 'provider_failed')

    def test_source_mismatch_stops_handoff(self):
        self.api.change = lambda row: dict(row, input_sha256='f'*64)
        self.completed()
        self.assertEqual(self.state['status'], 'verification_failed')
        self.assertFalse((self.directory / 'handoff.json').exists())

    def test_local_only_success_requires_s3(self):
        self.api.change = lambda row: dict(row, s3_uploaded=False, storage='temporary_container_disk')
        self.completed()
        self.assertEqual(self.state['status'], 'storage_not_ready')

    def test_cross_bucket_and_cross_job_reference_rejected(self):
        for field, value in [('bucket', 'different-bucket'), ('manifest_key', 'cctv-evidence/other/manifest.json')]:
            with self.subTest(field=field):
                self.api.change = lambda row: dict(row, s3=dict(row['s3'], **{field: value}))
                self.state['provider_job_id'] = self.api.job
                worker.poll(self.directory, self.state, self.api, self.trace, 0)
                self.assertEqual(self.state['status'], 'verification_failed')

    def test_empty_evidence_is_no_candidates_without_gemini(self):
        self.completed()
        result = worker.collect(self.directory, self.state, self.s3(empty=True), self.trace)
        self.assertEqual(result['status'], 'no_candidates')
        self.assertEqual(result['scene_configs'], [])
        self.assertEqual(result['gemini_api_calls'], 0)

    def test_corrupt_index_rejected_before_handoff(self):
        self.completed()
        reader = self.s3()
        reader.objects[f'cctv-evidence/{self.api.job}/evidence.json']['Body'] += b' '
        with self.assertRaises(ValueError):
            worker.collect(self.directory, self.state, reader, self.trace)
        self.assertFalse((self.directory / 'handoff.json').exists())
        self.assertEqual(self.state['status'], 'evidence_ready')

    def test_collect_repeat_does_not_read_or_submit_again(self):
        self.completed()
        reader = self.s3()
        first = worker.collect(self.directory, self.state, reader, self.trace)
        second = worker.collect(self.directory, self.state, reader, self.trace)
        self.assertEqual(first, second)
        self.assertEqual((self.api.posts, len(reader.reads)), (1, 2))

    def test_job_lock_blocks_second_writer(self):
        with locked(self.directory):
            with self.assertRaises(WorkerError):
                with locked(self.directory):
                    pass

    def test_trace_export_only_allowlisted_fields_and_stable_ids(self):
        self.completed()
        with self.trace.span('s3.verify', {'token': 'secret', 'url': 'https://secret', **self.state}) as output:
            output.update(status='evidence_ready', authorization='Bearer secret', video=b'sensitive')
        self.trace.finish(self.state)
        sink = Sink()
        result = tracing.export_traces(self.directory, sink)
        text = json.dumps(sink.rows)
        for secret in ('Bearer', 'sensitive', 'https://', str(self.root), 'secret', 'cctv-test-bucket'):
            self.assertNotIn(secret, text)
        self.assertEqual(result['status'], 'delivered')
        self.assertEqual(tracing.export_traces(self.directory, sink)['sent'], 0)
        self.assertEqual(sink.rows[1]['parent_run_id'], sink.rows[0]['id'])

    def test_trace_failure_can_retry_without_model_call(self):
        self.completed()
        self.trace.finish(self.state)
        sink = Sink()
        sink.fail = True
        self.assertEqual(tracing.export_traces(self.directory, sink)['status'], 'pending')
        self.assertEqual(self.state['status'], 'evidence_ready')
        sink.fail = False
        self.assertEqual(tracing.export_traces(self.directory, sink)['status'], 'delivered')
        self.assertEqual(self.api.posts, 1)

    def test_trace_does_not_export_unfinished_root(self):
        self.submitted()
        sink = Sink()
        tracing.export_traces(self.directory, sink)
        self.assertEqual(sink.rows, [])

    def test_http_or_credential_urls_rejected(self):
        for url in ('http://example.com', 'https://user:secret@example.com', 'https://example.com/?token=secret'):
            with self.subTest(url=url), self.assertRaises(WorkerError):
                RunpodAPI(url, 'synthetic-token')

    def test_langsmith_untrusted_endpoint_rejected(self):
        with self.assertRaises(WorkerError):
            tracing.LangSmith('synthetic-key', endpoint='https://example.com')


if __name__ == '__main__':
    unittest.main()
