"""Queue behavior with synthetic MP4/API replies. Optional isolated PostgreSQL.

The Docker integration runner repeats these cases against the real Store.
No production database, GPU, AWS, Gemini or LangSmith request is made.
"""
from contextlib import contextmanager
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from apps.be_worker import job_queue as q, worker
from apps.be_worker.common import WorkerError, read_json
from apps.be_worker.runpod_api import ApiError


class MemoryStore:
    def __init__(self):
        self.connection = None  # This double has no SQL backend.
        self.data = []
        self.locks = {'dispatch': threading.Lock(), 'collect': threading.Lock()}
    @contextmanager
    def lock(self, kind):
        if not self.locks[kind].acquire(False):
            raise WorkerError('queue_worker_already_running')
        try:
            yield
        finally:
            self.locks[kind].release()
    def rows(self):
        return copy.deepcopy(self.data)
    def by_key(self, key):
        return next((r for r in self.rows() if r['request_key'] == key), None)
    def insert(self, key, directory_id, state):
        row = dict(sequence=len(self.data)+1, worker_job_id=state['worker_job_id'], directory_id=directory_id,
                   state=copy.deepcopy(state), request_key=key, submit_attempts=0, ready=True, handoff=None)
        self.data.append(row)
        return copy.deepcopy(row)
    def write(self, row, state, *, attempt=False, delay=0, handoff=None):
        found = next(r for r in self.data if r['worker_job_id'] == row['worker_job_id'])
        found.update(state=copy.deepcopy(state), submit_attempts=found['submit_attempts']+int(attempt), ready=not delay)
        if handoff is not None:
            found['handoff'] = copy.deepcopy(handoff)


class Pod:
    def __init__(self):
        self.posts, self.jobs = [], {}
        self.error = None
        self.before_submit = lambda state: None
    def api(self, state):
        outer = self
        class API:
            def health(self):
                return {'storage': 's3'}
            def submit(self, path, size):
                outer.before_submit(state)
                outer.posts.append(state['worker_job_id'])
                if outer.error:
                    raise outer.error
                identifier = f'{len(outer.posts):032x}'
                outer.jobs[identifier] = dict(status='running', state=copy.deepcopy(state))
                return identifier
            def status(self, identifier):
                job = outer.jobs[identifier]
                original = job['state']
                return {'schema_version': 'runpod-local-api-v1', 'job_id': identifier, 'status': job['status'],
                        'input_sha256': original['input_sha256'], 'input_bytes': original['input_bytes'],
                        'storage': 's3', 's3_uploaded': True,
                        's3': {'bucket': original['config']['s3_bucket'], 'region': 'ap-northeast-2',
                               'manifest_key': f'cctv-evidence/{identifier}/manifest.json', 's3_uploaded': True}}
        return API()


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.video = self.root/'original.mp4'
        self.video.write_bytes(b'\0\0\0\x18ftypisom-SYNTHETIC-QUEUE-TEST')
        self.spool = self.root/'spool'
        self.config = {'runpod_url': 'https://test.invalid', 's3_bucket': 'cctv-test-bucket', 's3_region': 'ap-northeast-2'}
        self.connections = []
        if os.environ.get('CCTV_QUEUE_ISOLATED_POSTGRES') == '1':
            import psycopg
            from psycopg.rows import dict_row
            from apps.be_worker.queue_store import Store
            password = Path('/run/secrets/db_admin_password').read_text().strip()
            admin = psycopg.connect(host='db', dbname='cctv_queue_test', user='postgres', password=password,
                                   autocommit=True, row_factory=dict_row)
            self.connections.append(admin)
            self.assertEqual(admin.execute('SELECT current_database() AS name').fetchone()['name'], 'cctv_queue_test')
            admin.execute('TRUNCATE cctv_jobs.queue RESTART IDENTITY')
            app_password = Path('/run/secrets/db_app_password').read_text().strip()
            conn = psycopg.connect(host='db', dbname='cctv_queue_test', user='cctv_app', password=app_password,
                                  autocommit=True, row_factory=dict_row)
            self.connections.append(conn)
            self.store = Store(conn)
            self.new_store = lambda: self.additional_connection(psycopg, dict_row, app_password, Store)
        else:
            self.store = MemoryStore()
            self.new_store = lambda: self.store
        self.pod = Pod()
        self.coordinator = q.Coordinator(self.store, self.spool, self.pod.api)
    def additional_connection(self, psycopg, row_factory, password, Store):
        conn = psycopg.connect(host='db', dbname='cctv_queue_test', user='cctv_app', password=password,
                              autocommit=True, row_factory=row_factory)
        self.connections.append(conn)
        return Store(conn)
    def tearDown(self):
        for conn in reversed(self.connections):
            conn.close()
        self.tmp.cleanup()
    def add(self, key='A'):
        return q.enqueue(self.store, self.spool, self.video, self.config, key)
    def states(self):
        return [r['state']['status'] for r in self.store.rows()]
    def make_ready(self, row):
        self.store.write(row, row['state'])

    def test_three_videos_capacity_fifo_and_independent_collection(self):
        ids = [self.add(k)['worker_job_id'] for k in ('A', 'B', 'C')]
        self.coordinator.dispatch_tick()
        self.assertEqual(self.pod.posts, ids[:2])
        self.assertEqual(self.states(), ['waiting', 'waiting', 'prepared'])
        self.pod.jobs[f'{1:032x}']['status'] = 'succeeded'
        self.coordinator.dispatch_tick()
        self.assertEqual(self.pod.posts, ids)
        self.assertEqual(self.states(), ['evidence_ready', 'waiting', 'waiting'])
        self.coordinator.reader_factory = lambda state: object()
        with patch.object(worker, 'collect', side_effect=RuntimeError('test S3 outage')):
            self.coordinator.collect_tick()
        self.assertEqual(self.states()[0], 'evidence_ready')
        self.coordinator.dispatch_tick()
        self.assertEqual(len(self.pod.posts), 3)

    def failed_with_uploaded_s3(self):
        identifier = self.add()['worker_job_id']
        self.coordinator.dispatch_tick()
        row = self.store.rows()[0]
        state = worker.load_state(self.coordinator.directory(row))
        worker.update(self.coordinator.directory(row), state, provider_job_id='a'*32,
                      status='provider_failed', error_code='provider_failed_check_log')
        self.store.write(row, state)
        path = Path(__file__).resolve().parents[1] / 'scene_description/test_scene.py'
        spec = importlib.util.spec_from_file_location('recovery_scene_fixture', path)
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        producer = self.root/'producer'
        producer.mkdir()
        _, _, reader = fixture.fixture(producer)
        key = 'cctv-evidence/' + 'a'*32 + '/manifest.json'
        manifest = json.loads(reader.objects[key]['Body'])
        manifest['input_sha256'] = state['input_sha256']
        reader.objects[key]['Body'] = json.dumps(manifest).encode()
        self.coordinator.reader_factory = lambda state: reader
        self.coordinator.api_factory = lambda state: self.fail('Recovery must not contact Runpod')
        return identifier, reader, key

    def test_recover_s3_verifies_existing_result_without_runpod_or_repost(self):
        identifier, reader, key = self.failed_with_uploaded_s3()
        self.coordinator.recover_s3(identifier)
        self.coordinator.recover_s3(identifier)
        row = self.store.rows()[0]
        self.assertEqual(row['state']['status'], 'handoff_ready')
        self.assertEqual((row['submit_attempts'], row['state']['post_attempts']), (1, 1))
        self.assertEqual(len(self.pod.posts), 1)
        history = read_json(self.coordinator.directory(row)/'s3-recovery-before.json')
        self.assertEqual(history['file']['status'], 'provider_failed')

    def test_recover_s3_wrong_source_keeps_failure(self):
        identifier, reader, key = self.failed_with_uploaded_s3()
        manifest = json.loads(reader.objects[key]['Body'])
        manifest['input_sha256'] = '0'*64
        reader.objects[key]['Body'] = json.dumps(manifest).encode()
        with self.assertRaises(ValueError):
            self.coordinator.recover_s3(identifier)
        row = self.store.rows()[0]
        self.assertEqual(row['state']['status'], 'provider_failed')
        self.assertEqual(worker.load_state(self.coordinator.directory(row))['status'], 'provider_failed')

    def test_recover_s3_db_failure_resumes_without_repost(self):
        identifier, reader, key = self.failed_with_uploaded_s3()
        with patch.object(self.store, 'write', side_effect=RuntimeError('DB unavailable')):
            with self.assertRaises(RuntimeError):
                self.coordinator.recover_s3(identifier)
        self.coordinator.recover_s3(identifier)
        self.assertEqual(self.states(), ['handoff_ready'])
        self.assertEqual(len(self.pod.posts), 1)

    def test_enqueue_is_idempotent_and_conflicting_input_is_rejected(self):
        first = self.add()
        self.assertEqual(self.add()['worker_job_id'], first['worker_job_id'])
        self.assertTrue(self.add()['already_present'])
        self.video.write_bytes(b'\0\0\0\x18ftypisom-DIFFERENT')
        with self.assertRaisesRegex(WorkerError, 'request_key_conflict'):
            self.add()
        self.assertEqual(len(self.store.rows()), 1)
        self.assertFalse(self.pod.posts)

    def test_submission_intent_committed_before_http(self):
        self.add()
        def check(state):
            row = self.store.rows()[0]
            self.assertEqual(row['state']['status'], 'submitting')
            self.assertEqual(row['submit_attempts'], 1)
        self.pod.before_submit = check
        self.coordinator.dispatch_tick()

    def test_response_loss_blocks_new_submissions_and_never_reposts(self):
        self.add('A'); self.add('B')
        self.pod.error = ApiError('network_error')
        self.coordinator.dispatch_tick()
        for _ in range(3):
            q.Coordinator(self.store, self.spool, self.pod.api).dispatch_tick()
        self.assertEqual(self.states(), ['submission_unknown', 'prepared'])
        self.assertEqual(len(self.pod.posts), 1)

    def test_crash_after_db_intent_before_file_write_requires_reconciliation(self):
        self.add()
        row = self.store.rows()[0]
        self.store.write(row, dict(row['state'], status='submitting'), attempt=True)
        self.coordinator.dispatch_tick()
        self.assertEqual(self.states(), ['submission_unknown'])
        self.assertFalse(self.pod.posts)

    def test_crash_after_ack_before_db_update_recovers_known_id(self):
        self.add()
        original = self.store.write
        def crash(row, state, **kwargs):
            if state['status'] == 'waiting':
                raise RuntimeError('synthetic DB disconnect')
            original(row, state, **kwargs)
        with patch.object(self.store, 'write', side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.coordinator.dispatch_tick()
        self.assertEqual(self.states(), ['submitting'])
        self.coordinator.dispatch_tick()
        self.assertEqual(self.states(), ['waiting'])
        self.assertEqual(len(self.pod.posts), 1)

    def test_db_ack_recovers_failed_local_receipt_without_repost(self):
        self.add(); self.coordinator.dispatch_tick()
        row = self.store.rows()[0]
        local = worker.load_state(self.coordinator.directory(row))
        worker.update(self.coordinator.directory(row), local, status='submitting', provider_job_id=None)
        self.coordinator.dispatch_tick()
        self.assertEqual(self.states(), ['waiting'])
        self.assertEqual(len(self.pod.posts), 1)
        self.assertEqual(worker.load_state(self.coordinator.directory(row))['provider_job_id'], f'{1:032x}')

    def test_second_health_failure_is_known_not_submitted(self):
        self.add()
        api = self.pod.api(self.store.rows()[0]['state'])
        with patch.object(api, 'health', side_effect=[{'storage': 's3'}, ApiError('network_error')]):
            self.coordinator.api_factory = lambda state: api
            with self.assertRaises(ApiError):
                self.coordinator.dispatch_tick()
        self.assertEqual(self.states(), ['prepared'])
        self.assertFalse(self.pod.posts)

    def test_429_delays_fifo_head_then_retries_only_rejected_job(self):
        self.add('A'); self.add('B')
        self.pod.error = ApiError('http_429', 429)
        self.coordinator.dispatch_tick()
        self.coordinator.dispatch_tick()
        self.assertEqual(len(self.pod.posts), 1)
        self.assertEqual(self.states(), ['submission_rejected', 'prepared'])
        self.pod.error = None
        self.make_ready(self.store.rows()[0])
        self.coordinator.dispatch_tick()
        self.assertEqual(self.states(), ['waiting', 'waiting'])
        self.assertEqual(self.store.rows()[0]['submit_attempts'], 2)

    def test_429_has_bounded_attempts(self):
        self.add()
        self.pod.error = ApiError('http_429', 429)
        for _ in range(9):
            self.make_ready(self.store.rows()[0])
            self.coordinator.dispatch_tick()
        self.assertEqual(len(self.pod.posts), 5)

    def test_failed_video_does_not_become_no_candidates(self):
        self.add()
        self.coordinator.dispatch_tick()
        self.pod.jobs[f'{1:032x}']['status'] = 'failed'
        self.coordinator.dispatch_tick()
        self.assertEqual(self.states(), ['provider_failed'])
        self.assertEqual(len(self.pod.posts), 1)

    def test_original_changed_after_enqueue_uses_private_snapshot(self):
        self.add()
        self.video.unlink()
        self.coordinator.dispatch_tick()
        self.assertEqual(self.states(), ['waiting'])

    def test_spool_tamper_refuses_submission(self):
        self.add()
        row = self.store.rows()[0]
        (self.coordinator.directory(row)/'input.mp4').write_bytes(b'\0\0\0\x18ftypisom-tampered')
        with self.assertRaisesRegex(WorkerError, 'queued_input_changed'):
            self.coordinator.dispatch_tick()
        self.assertFalse(self.pod.posts)

    def test_one_slot_setting_and_invalid_parallel_setting(self):
        self.add('A'); self.add('B')
        q.Coordinator(self.store, self.spool, self.pod.api, slots=1).dispatch_tick()
        self.assertEqual(self.states(), ['waiting', 'prepared'])
        with self.assertRaises(WorkerError):
            q.Coordinator(self.store, self.spool, self.pod.api, slots=3)

    def test_another_dispatcher_refused_but_collector_lock_independent(self):
        other = self.new_store()
        with self.store.lock('dispatch'):
            with self.assertRaisesRegex(WorkerError, 'queue_worker_already_running'):
                with other.lock('dispatch'):
                    pass
            with other.lock('collect'):
                pass

    def test_finished_collection_before_db_update_preserves_handoff(self):
        self.add(); self.coordinator.dispatch_tick()
        self.pod.jobs[f'{1:032x}']['status'] = 'succeeded'
        self.coordinator.dispatch_tick()
        row = self.store.rows()[0]
        state = worker.load_state(self.coordinator.directory(row))
        from apps.be_worker.common import save
        receipt = {'status': 'handoff_ready', 'event_count': 1, 'test_fixture': True}
        save(self.coordinator.directory(row)/'handoff.json', receipt)
        worker.update(self.coordinator.directory(row), state, status='handoff_ready')
        self.coordinator.reader_factory = lambda state: object()
        self.coordinator.collect_tick()
        self.assertEqual(self.states(), ['handoff_ready'])
        self.assertEqual(self.store.rows()[0]['handoff'], receipt)

    def test_known_id_recovery_validates_source(self):
        self.add(); self.coordinator.dispatch_tick()
        row = self.store.rows()[0]
        state = worker.load_state(self.coordinator.directory(row))
        identifier = state['provider_job_id']
        worker.update(self.coordinator.directory(row), state, status='submission_unknown', provider_job_id=None)
        self.store.write(row, state)
        self.coordinator.recover(row['worker_job_id'], identifier)
        self.assertEqual(self.states(), ['waiting'])
        self.assertEqual(len(self.pod.posts), 1)

    def test_application_cannot_delete_queue(self):
        if not self.connections:
            self.skipTest('Only checked against isolated PostgreSQL')
        import psycopg
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.store.connection.execute('DELETE FROM cctv_jobs.queue')

    def test_secret_setup_preserves_token_and_does_not_print_credentials(self):
        import contextlib
        import io
        from apps.be_worker import prepare_queue
        private = self.root/'private'
        private.mkdir()
        prepare_queue.private_write(private/'runpod_worker_token', 'synthetic-token\n')
        secret = {'AccessKeyId': 'synthetic-id', 'SecretAccessKey': 'synthetic-secret', 'SessionToken': 'synthetic-session'}
        import json
        from types import SimpleNamespace
        with patch('sys.argv', ['prepare_queue', '--directory', str(private), '--aws-profile', 'test']), \
                patch.object(prepare_queue.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(secret))), \
                patch.object(prepare_queue.getpass, 'getpass', side_effect=AssertionError('Existing token must be preserved')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(prepare_queue.main(), 0)
        self.assertNotIn('synthetic-secret', output.getvalue())
        self.assertEqual((private/'runpod_worker_token').read_text(), 'synthetic-token\n')
        self.assertEqual((private/'aws_credentials').stat().st_mode & 0o777, 0o600)
        self.assertIn('aws_session_token = synthetic-session', (private/'aws_credentials').read_text())

    def test_invalid_secret_line_is_rejected(self):
        from apps.be_worker.prepare_queue import credential_text
        with self.assertRaises(ValueError):
            credential_text({'AccessKeyId': 'x\n[other]', 'SecretAccessKey': 'y'})

    def test_retarget_preserves_job_and_video_without_submitting(self):
        identifier = self.add()['worker_job_id']
        before = self.store.rows()[0]
        directory = self.coordinator.directory(before)
        video = (directory/'input.mp4').read_bytes()
        config = dict(self.config, runpod_url='https://new-pod.invalid')
        health_targets = []
        def api(state):
            health_targets.append(state['config']['runpod_url'])
            return self.pod.api(state)
        self.coordinator.api_factory = api
        answer = self.coordinator.retarget(identifier, config)
        after = self.store.rows()[0]
        self.assertEqual(answer['jobs_submitted'], 0)
        self.assertTrue(answer['health_checked'])
        self.assertEqual(health_targets, [config['runpod_url']])
        for key in ('worker_job_id', 'directory_id', 'sequence', 'request_key', 'submit_attempts'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after['state'], worker.load_state(directory))
        self.assertEqual(after['state']['config'], config)
        self.assertEqual((directory/'input.mp4').read_bytes(), video)
        self.assertEqual(after['state']['input_sha256'], before['state']['input_sha256'])
        history = list(directory.glob('retarget-*.json'))
        self.assertEqual(len(history), 1)
        self.assertEqual(read_json(history[0])['db_before'], before['state'])
        self.assertFalse((directory/'retarget.pending.json').exists())
        self.assertTrue(self.coordinator.retarget(identifier, config)['already_applied'])
        self.assertEqual(len(list(directory.glob('retarget-*.json'))), 1)
        self.assertTrue(q.enqueue(self.store, self.spool, self.video, config, 'A')['already_present'])
        self.assertFalse(self.pod.posts)
        self.coordinator.dispatch_tick()
        self.assertEqual(self.pod.posts, [identifier])
        self.assertEqual(self.pod.jobs[f'{1:032x}']['state']['config'], config)

    def test_retarget_bad_health_preserves_both_records(self):
        identifier = self.add()['worker_job_id']
        before = self.store.rows()[0]
        directory = self.coordinator.directory(before)
        api = self.pod.api(before['state'])
        with patch.object(api, 'health', side_effect=ApiError('http_401', 401)):
            self.coordinator.api_factory = lambda state: api
            with self.assertRaisesRegex(ApiError, 'http_401'):
                self.coordinator.retarget(identifier, dict(self.config, runpod_url='https://new.invalid'))
        self.assertEqual(self.store.rows()[0], before)
        self.assertEqual(worker.load_state(directory), before['state'])
        self.assertFalse(list(directory.glob('retarget*')))
        self.assertFalse(self.pod.posts)

    def test_retarget_refuses_prior_attempt_even_if_prepared(self):
        identifier = self.add()['worker_job_id']
        row = self.store.rows()[0]
        self.store.write(row, row['state'], attempt=True)
        with self.assertRaisesRegex(WorkerError, 'retarget_requires_zero_submission_attempts'):
            self.coordinator.retarget(identifier, dict(self.config, runpod_url='https://new.invalid'))
        self.assertEqual(self.store.rows()[0]['state']['config'], self.config)

    def test_retarget_refuses_active_other_job(self):
        self.add('A'); self.add('B')
        q.Coordinator(self.store, self.spool, self.pod.api, slots=1).dispatch_tick()
        row = self.store.rows()[1]
        with self.assertRaisesRegex(WorkerError, 'cannot_retarget_with_active_jobs'):
            self.coordinator.retarget(row['worker_job_id'], dict(self.config, runpod_url='https://new.invalid'))
        self.assertEqual(len(self.pod.posts), 1)

    def test_retarget_refuses_local_submission_receipt(self):
        identifier = self.add()['worker_job_id']
        row = self.store.rows()[0]
        directory = self.coordinator.directory(row)
        state = worker.load_state(directory)
        worker.update(directory, state, status='waiting', provider_job_id='f'*32, post_attempts=1)
        with self.assertRaisesRegex(WorkerError, 'retarget_requires_unsubmitted_job'):
            self.coordinator.retarget(identifier, dict(self.config, runpod_url='https://new.invalid'))
        self.assertEqual(worker.load_state(directory)['provider_job_id'], 'f'*32)
        self.assertFalse(self.pod.posts)

    def test_retarget_refuses_video_or_s3_destination_change(self):
        identifier = self.add()['worker_job_id']
        config = dict(self.config, runpod_url='https://new.invalid')
        with self.assertRaisesRegex(WorkerError, 'retarget_cannot_change_s3_destination'):
            self.coordinator.retarget(identifier, dict(config, s3_bucket='other-test-bucket'))
        directory = self.coordinator.directory(self.store.rows()[0])
        (directory/'input.mp4').write_bytes(b'\0\0\0\x18ftypisom-tampered')
        with self.assertRaisesRegex(WorkerError, 'queued_input_changed'):
            self.coordinator.retarget(identifier, config)
        self.assertFalse(list(directory.glob('retarget*')))
        self.assertFalse(self.pod.posts)

    def test_retarget_partial_db_commit_blocks_dispatch_and_resumes_same_target(self):
        identifier = self.add()['worker_job_id']
        directory = self.coordinator.directory(self.store.rows()[0])
        config = dict(self.config, runpod_url='https://new.invalid')
        original_save = q.save
        def crash(path, value):
            if Path(path).name == 'state.json':
                raise OSError('synthetic local write failure')
            return original_save(path, value)
        with patch.object(q, 'save', side_effect=crash):
            with self.assertRaises(OSError):
                self.coordinator.retarget(identifier, config)
        self.assertEqual(self.store.rows()[0]['state']['config'], config)
        self.assertEqual(worker.load_state(directory)['config'], self.config)
        with self.assertRaisesRegex(WorkerError, 'retarget_incomplete_repeat_retarget'):
            self.coordinator.dispatch_tick()
        with self.assertRaisesRegex(WorkerError, 'retarget_pending_target_mismatch'):
            self.coordinator.retarget(identifier, dict(config, runpod_url='https://different.invalid'))
        answer = self.coordinator.retarget(identifier, config)
        self.assertEqual(answer['jobs_submitted'], 0)
        self.assertEqual(self.store.rows()[0]['state'], worker.load_state(directory))
        self.assertFalse((directory/'retarget.pending.json').exists())
        self.assertFalse(self.pod.posts)

    def test_retarget_uncertain_db_write_recovers_without_resetting_attempts(self):
        identifier = self.add()['worker_job_id']
        config = dict(self.config, runpod_url='https://new.invalid')
        original_write = self.store.write
        def crash(row, state, **kwargs):
            original_write(row, state, **kwargs)
            raise RuntimeError('synthetic lost commit response')
        with patch.object(self.store, 'write', side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.coordinator.retarget(identifier, config)
        self.coordinator.retarget(identifier, config)
        row = self.store.rows()[0]
        self.assertEqual(row['submit_attempts'], 0)
        self.assertEqual(row['state'], worker.load_state(self.coordinator.directory(row)))
        self.assertFalse(self.pod.posts)

    def test_retarget_before_db_write_failure_keeps_a_recoverable_journal(self):
        identifier = self.add()['worker_job_id']
        config = dict(self.config, runpod_url='https://new.invalid')
        before = self.store.rows()[0]
        with patch.object(self.store, 'write', side_effect=RuntimeError('synthetic DB failure')):
            with self.assertRaises(RuntimeError):
                self.coordinator.retarget(identifier, config)
        self.assertEqual(self.store.rows()[0], before)
        self.assertEqual(worker.load_state(self.coordinator.directory(before)), before['state'])
        with self.assertRaisesRegex(WorkerError, 'retarget_incomplete_repeat_retarget'):
            self.coordinator.dispatch_tick()
        self.coordinator.retarget(identifier, config)
        self.assertFalse(self.pod.posts)


if __name__ == '__main__':
    unittest.main()
