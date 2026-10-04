"""Existing handoff -> media -> synthetic Gemini -> resumable persistence."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/scene_description'))
sys.path.insert(0, str(ROOT/'tests/be_worker'))
from test_scene import fixture, response
from test_queue import MemoryStore
from apps.be_worker import job_queue as q, worker
from apps.be_worker.common import WorkerError, read_json, save
from apps.be_worker.scene_jobs import SceneJobs
from apps.scene_description import db, scene


class SceneJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        original = self.root/'original'; original.mkdir()
        self.config, self.event, self.s3 = fixture(original)
        self.connections = []
        self.real_db = os.environ.get('CCTV_QUEUE_ISOLATED_POSTGRES') == '1'
        if self.real_db:
            import psycopg
            from psycopg.rows import dict_row
            from apps.be_worker.queue_store import Store
            admin = psycopg.connect(host='db', dbname='cctv_queue_test', user='postgres',
                password=Path('/run/secrets/db_admin_password').read_text().strip(), autocommit=True)
            self.connections.append(admin)
            admin.execute('TRUNCATE cctv_jobs.queue, cctv_scene.descriptions RESTART IDENTITY')
            conn = psycopg.connect(host='db', dbname='cctv_queue_test', user='cctv_app',
                password=Path('/run/secrets/db_app_password').read_text().strip(),
                autocommit=True, row_factory=dict_row)
            self.connections.append(conn)
            self.store = Store(conn)
        else:
            self.store = MemoryStore()
            guard = patch('apps.be_worker.trial_limits.get', return_value=None)
            guard.start(); self.addCleanup(guard.stop)
        video = self.root/'input.mp4'; video.write_bytes(b'\0\0\0\x18ftyp-SYNTHETIC')
        spool = self.root/'spool'
        be_config = dict(runpod_url='https://test.invalid', s3_bucket=self.config['bucket'], s3_region=self.config['region'])
        self.identifier = q.enqueue(self.store, spool, video, be_config, 'synthetic-A')['worker_job_id']
        self.coordinator = q.Coordinator(self.store, spool, lambda _: self.fail('Unexpected Runpod call'), lambda _: self.s3)
        row = self.store.rows()[0]
        self.directory = self.coordinator.directory(row)
        state = row['state']
        worker.update(self.directory, state, input_sha256=self.config['expected_input_sha256'],
            provider_job_id=self.config['job_id'], status='evidence_ready',
            s3=dict(bucket=self.config['bucket'], region=self.config['region'],
                    manifest_key=f"cctv-evidence/{self.config['job_id']}/manifest.json"))
        self.store.write(row, state)
        self.coordinator.collect_tick()
        self.assertEqual(self.store.rows()[0]['state']['status'], 'handoff_ready')
        self.results = {}
        self.scenes = SceneJobs(self.coordinator) if self.real_db else SceneJobs(
            self.coordinator, self.persist, lambda: None)
        self.calls = 0
        self.expected_attempts = 1
    def tearDown(self):
        for conn in reversed(self.connections): conn.close()
        self.tmp.cleanup()
    def persist(self, result):
        digest = db.validate_result(result)
        identifier = result['analysis_id']
        if identifier in self.results and self.results[identifier] != result:
            raise ValueError('conflict')
        self.results[identifier] = copy.deepcopy(result)
        return dict(committed=True, result_sha256=digest, analysis_id=identifier)
    def call(self, *args):
        self.calls += 1
        state = self.store.rows()[0]['state']
        self.assertEqual(state['gemini_api_calls'], self.expected_attempts)
        raw = response()
        body = json.loads(raw['candidates'][0]['content']['parts'][0]['text'])
        body.update(accident_suspicion='suspected', incident_types=['rollover'])
        raw['candidates'][0]['content']['parts'][0]['text'] = json.dumps(body)
        return raw
    def fetch(self):
        return self.scenes.fetch(self.identifier, 'event_000')
    def describe(self, **options):
        options.setdefault('key_loader', lambda: 'synthetic-key')
        return self.scenes.describe(self.identifier, 'event_000', model='gemini-test', fps=4,
                                    caller=self.call, **options)
    def test_fetch_then_store_once_without_gpu_or_duplicate_model_call(self):
        self.fetch()
        reads = len(self.s3.reads)
        self.fetch()
        self.assertEqual(len(self.s3.reads), reads)
        result = self.describe()
        self.assertEqual(result['status'], 'description_stored')
        self.assertEqual(result['incident_types'], ['rollover'])
        self.describe(key_loader=lambda: self.fail('Should reuse result without key'))
        self.assertEqual(self.calls, 1)
        self.assertTrue(read_json(self.directory/'scenes/event_000/rag-handoff.json')['database_loaded'])
        self.assertFalse(read_json(self.directory/'scenes/event_000/rag-handoff.json')['report_generated'])
        self.assertEqual(self.fetch()['status'], 'description_stored')
    def test_fetch_hash_failure_can_retry_read_only(self):
        key = f"cctv-evidence/{self.config['job_id']}/{self.event['clip']}"
        before = self.s3.objects[key]['Body']
        self.s3.objects[key]['Body'] = b'corrupted'
        with self.assertRaisesRegex(WorkerError, 'scene_fetch_failed'): self.fetch()
        self.s3.objects[key]['Body'] = before
        result = self.fetch()
        self.assertIsNone(result['error_code'])
        self.assertEqual(result['status'], 'media_verified')
        self.assertEqual(self.calls, 0)
    def test_trial_reservation_precedes_scene_call_and_saved_result_reuses_it(self):
        self.fetch()
        reserved = []
        def reserve(conn, row, event, kind, request_sha, model):
            self.assertEqual(self.calls, 0)
            self.assertEqual((event, kind, model), ('event_000','scene','gemini-test'))
            self.assertEqual(len(request_sha), 64)
            reserved.append(True)
        original = self.call
        def caller(*args):
            self.assertEqual(reserved, [True])
            return original(*args)
        with patch('apps.be_worker.trial_limits.claim', side_effect=reserve) as claim, patch.object(self,'call',caller):
            self.describe()
            self.describe(key_loader=lambda: self.fail('saved response does not require key'))
        self.assertEqual(claim.call_count, 1)
        self.assertEqual(self.calls, 1)
    def test_trial_reservation_denial_preserves_reason_and_never_calls_scene(self):
        self.fetch()
        with patch('apps.be_worker.trial_limits.claim', side_effect=WorkerError('trial_attempt_already_claimed_no_retry')):
            with self.assertRaisesRegex(WorkerError, 'trial_attempt_already_claimed_no_retry'):
                self.describe()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.store.rows()[0]['state']['scene_events']['event_000']['error_code'],
                         'trial_attempt_already_claimed_no_retry')
    def test_changed_manifest_since_handoff_is_rejected(self):
        key = f"cctv-evidence/{self.config['job_id']}/manifest.json"
        self.s3.objects[key]['Body'] += b' '
        with self.assertRaisesRegex(WorkerError, 'scene_fetch_failed'): self.fetch()
    def test_corrupted_local_media_blocks_api(self):
        self.fetch()
        (self.directory/'scenes/event_000/input'/self.event['clip']).write_bytes(b'broken')
        with self.assertRaises(ValueError): self.describe()
        self.assertEqual(self.calls, 0)
    def test_db_failure_reuses_result_without_key(self):
        self.fetch()
        persist = self.scenes.persist_result
        self.scenes.persist_result = lambda _: (_ for _ in ()).throw(RuntimeError('synthetic DB interruption'))
        with self.assertRaisesRegex(WorkerError, 'scene_database_failed'): self.describe()
        self.assertEqual(self.calls, 1)
        self.scenes.persist_result = persist
        self.assertEqual(self.describe(key_loader=lambda: self.fail('unexpected key read'))['status'], 'description_stored')
        self.assertEqual(self.calls, 1)
    def test_timeout_is_not_automatically_retried(self):
        self.fetch()
        def timeout(*args):
            self.calls += 1
            raise TimeoutError('synthetic')
        with patch.object(self, 'call', timeout):
            with self.assertRaisesRegex(WorkerError, 'scene_attempt_failed'): self.describe()
        with self.assertRaisesRegex(WorkerError, 'scene_attempt_incomplete'): self.describe()
        self.assertEqual(self.calls, 1)
    def test_result_for_other_job_is_rejected(self):
        self.fetch(); self.describe()
        path = self.directory/'scenes/event_000/description/result.json'
        result = read_json(path); result['input_sha256'] = 'c'*64; save(path, result)
        with self.assertRaisesRegex(WorkerError, 'scene_result_identity_mismatch'): self.describe()
        self.assertEqual(self.calls, 1)

    def test_diagnostic_preserves_failed_attempt_and_does_not_retry(self):
        self.fetch()
        with patch.object(self, 'call', side_effect=scene.GeminiError({'error_code': 'gemini_http_403', 'http_status': 403})):
            with self.assertRaisesRegex(WorkerError, 'gemini_http_403'): self.describe()
        before = {p: p.read_bytes() for p in self.directory.rglob('*.json')}
        database_before = copy.deepcopy(self.store.rows())
        reads = len(self.s3.reads)
        def check(model, key):
            self.assertEqual((model, key), ('gemini-test', 'synthetic-key'))
            return {'status': 'metadata_access_ok', 'generation_validated': False, 'supports_generate_content': True}
        for _ in range(2):
            result = self.scenes.diagnose(self.identifier, 'event_000', key_loader=lambda: 'synthetic-key', checker=check)
            self.assertEqual(result['generation_requests'], 0)
            self.assertFalse(result['database_modified'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(database_before, self.store.rows())
        self.assertEqual(reads, len(self.s3.reads))
        self.assertEqual(len(list((self.directory/'scenes/event_000/diagnostics').glob('*/result.json'))), 2)
        with self.assertRaisesRegex(WorkerError, 'scene_attempt_incomplete'): self.describe()

    def test_diagnostic_failure_is_sanitized_and_wrong_event_blocks_network(self):
        self.fetch(); self.describe()
        def denied(*args):
            raise scene.GeminiError({'error_code': 'gemini_http_400', 'http_status': 400,
                                    'reasons': ['API_KEY_INVALID']})
        result = self.scenes.diagnose(self.identifier, 'event_000', key_loader=lambda: 'secret-value', checker=denied)
        self.assertEqual(result['status'], 'metadata_access_failed')
        self.assertEqual(result['reasons'], ['API_KEY_INVALID'])
        self.assertNotIn('secret-value', json.dumps(result))
        with self.assertRaises(WorkerError):
            self.scenes.diagnose(self.identifier, 'event_099', key_loader=lambda: self.fail('wrong event'))
        path = self.directory/'scenes/event_000/description/request.json'
        record = read_json(path); record['provider_job_id'] = 'f'*32; save(path, record)
        with self.assertRaisesRegex(WorkerError, 'scene_request_identity_mismatch'):
            self.scenes.diagnose(self.identifier, 'event_000', key_loader=lambda: self.fail('wrong job'))
    def test_unknown_event_and_concurrent_owner_rejected(self):
        with self.assertRaises(WorkerError): self.scenes.fetch(self.identifier, '../event_000')
        with self.assertRaises(WorkerError): self.scenes.fetch(self.identifier, 'event_099')
        from apps.be_worker.common import locked
        with locked(self.directory):
            with self.assertRaisesRegex(WorkerError, 'job_already_locked'): self.fetch()

    def failed_attempt(self):
        self.fetch()
        with patch.object(self, 'call', side_effect=scene.GeminiError({'error_code': 'gemini_http_429', 'http_status': 429})):
            with self.assertRaisesRegex(WorkerError, 'gemini_http_429'): self.describe()
        output = self.directory/'scenes/event_000/description'
        return read_json(output/'request.json')['analysis_id']

    def test_text_probe_preserves_business_state_and_is_at_most_once(self):
        failed_id = self.failed_attempt()
        before = {p: p.read_bytes() for p in self.directory.rglob('*.json')}
        database_before = copy.deepcopy(self.store.rows())
        reads = len(self.s3.reads)
        with patch.object(scene, 'probe_text_generation', return_value={
                'status': 'text_generation_ok', 'video_generation_validated': False}) as probe:
            result = self.scenes.diagnose(self.identifier, 'event_000', text_probe=True,
                                         key_loader=lambda: 'synthetic-key')
            again = self.scenes.diagnose(self.identifier, 'event_000', text_probe=True,
                                        key_loader=lambda: self.fail('Repeated key read'))
        probe.assert_called_once_with('gemini-test', 'synthetic-key')
        self.assertEqual(result['failed_analysis_id'], failed_id)
        self.assertEqual(result['generation_requests'], 1)
        self.assertEqual(result['metadata_requests_attempted'], 0)
        self.assertFalse(result['media_uploaded'])
        self.assertFalse(result['database_modified'])
        self.assertEqual(again['generation_requests_this_run'], 0)
        self.assertTrue(again['saved_receipt_reused'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(database_before, self.store.rows())
        self.assertEqual(reads, len(self.s3.reads))

    def test_text_probe_failed_503_is_also_reused(self):
        self.failed_attempt()
        with patch.object(scene, 'probe_text_generation', side_effect=scene.GeminiError({
                'error_code': 'gemini_http_503', 'http_status': 503, 'api_status': 'UNAVAILABLE'})) as probe:
            result = self.scenes.diagnose(self.identifier, 'event_000', text_probe=True,
                                         key_loader=lambda: 'secret-value')
            again = self.scenes.diagnose(self.identifier, 'event_000', text_probe=True,
                                        key_loader=lambda: self.fail('Repeated key read'))
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(result['status'], 'text_generation_failed')
        self.assertEqual(result['error_code'], 'gemini_http_503')
        self.assertEqual(again['generation_requests_this_run'], 0)
        self.assertNotIn('secret-value', json.dumps(result))

    def test_text_probe_interruption_does_not_auto_retry(self):
        failed_id = self.failed_attempt()
        with patch.object(scene, 'probe_text_generation', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.scenes.diagnose(self.identifier, 'event_000', text_probe=True,
                                     key_loader=lambda: 'synthetic-key')
        self.assertTrue((self.directory/f'scenes/event_000/text-probes/{failed_id}/request.json').exists())
        with self.assertRaisesRegex(WorkerError, 'scene_probe_incomplete_no_auto_retry'):
            self.scenes.diagnose(self.identifier, 'event_000', text_probe=True,
                                 key_loader=lambda: self.fail('Interrupted probe repeated'))

    def test_text_probe_uses_latest_selected_failure(self):
        failed_id = self.failed_attempt()
        with patch.object(self, 'call', side_effect=scene.GeminiError({'error_code': 'gemini_http_503', 'http_status': 503})):
            with self.assertRaisesRegex(WorkerError, 'gemini_http_503'):
                self.describe(retry_failed_analysis_id=failed_id)
        latest = read_json(self.directory/f'scenes/event_000/description-retry-{failed_id}/request.json')['analysis_id']
        result = self.scenes.diagnose(self.identifier, 'event_000', text_probe=True,
                                     key_loader=lambda: 'synthetic-key', checker=lambda *_: {'status': 'text_generation_ok'})
        self.assertEqual(result['failed_analysis_id'], latest)
        self.assertNotEqual(latest, failed_id)
        self.assertEqual(self.store.rows()[0]['state']['gemini_api_calls'], 2)

    def test_text_probe_does_not_run_for_successful_scene_or_malformed_id(self):
        self.fetch(); self.describe()
        output = self.directory/'scenes/event_000/description'
        with self.assertRaisesRegex(WorkerError, 'scene_probe_requires_failed_attempt'):
            self.scenes.diagnose(self.identifier, 'event_000', text_probe=True,
                                 key_loader=lambda: self.fail('Successful scene'))
        request = read_json(output/'request.json'); request['analysis_id'] = '../not-a-uuid'
        save(output/'request.json', request)
        with self.assertRaisesRegex(WorkerError, 'scene_invalid_failed_analysis_id'):
            self.scenes.diagnose(self.identifier, 'event_000', text_probe=True,
                                 key_loader=lambda: self.fail('Malformed ID'))

    def test_explicit_retry_keeps_history_counts_and_reuses_stored_result(self):
        failed_id = self.failed_attempt()
        event_dir = self.directory/'scenes/event_000'
        before = {p: p.read_bytes() for p in (event_dir/'description').iterdir()}
        reads = len(self.s3.reads)
        self.expected_attempts = 2
        result = self.describe(retry_failed_analysis_id=failed_id)
        self.assertEqual(result['status'], 'description_stored')
        self.assertEqual(result['gemini_api_calls'], 2)
        self.assertNotEqual(result['analysis_id'], failed_id)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        handoff = read_json(event_dir/'rag-handoff.json')
        self.assertEqual(handoff['scene_result'], f'description-retry-{failed_id}/result.json')
        for options in ({}, {'retry_failed_analysis_id': failed_id}):
            self.describe(key_loader=lambda: self.fail('Should reuse saved result'), **options)
        self.assertEqual(self.calls, 1)
        self.assertEqual(reads, len(self.s3.reads))
        self.assertEqual(self.store.rows()[0]['state']['gemini_api_calls'], 2)

    def test_retry_failure_does_not_automatically_create_a_third_attempt(self):
        failed_id = self.failed_attempt()
        with patch.object(self, 'call', side_effect=scene.GeminiError({'error_code': 'gemini_http_400', 'http_status': 400})) as call:
            with self.assertRaisesRegex(WorkerError, 'gemini_http_400'):
                self.describe(retry_failed_analysis_id=failed_id)
            for options in ({}, {'retry_failed_analysis_id': failed_id}):
                with self.assertRaisesRegex(WorkerError, 'scene_attempt_incomplete'): self.describe(**options)
            self.assertEqual(call.call_count, 1)
        self.assertEqual(self.store.rows()[0]['state']['gemini_api_calls'], 2)

    def test_retry_db_failure_reuses_new_result_without_key(self):
        failed_id = self.failed_attempt()
        self.expected_attempts = 2
        persist = self.scenes.persist_result
        self.scenes.persist_result = lambda _: (_ for _ in ()).throw(RuntimeError('synthetic DB failure'))
        with self.assertRaisesRegex(WorkerError, 'scene_database_failed'):
            self.describe(retry_failed_analysis_id=failed_id)
        self.scenes.persist_result = persist
        result = self.describe(retry_failed_analysis_id=failed_id, key_loader=lambda: self.fail('unexpected key read'))
        self.assertEqual(result['status'], 'description_stored')
        self.assertEqual(result['gemini_api_calls'], 2)
        self.assertEqual(self.calls, 1)

    def test_retry_checks_failed_id_and_original_settings_before_call(self):
        failed_id = self.failed_attempt()
        event_dir = self.directory/'scenes/event_000'
        for identifier in ('../../escape', '00000000-0000-0000-0000-000000000001'):
            with self.assertRaises(WorkerError):
                self.describe(retry_failed_analysis_id=identifier, key_loader=lambda: self.fail('wrong id'))
        with self.assertRaisesRegex(WorkerError, 'scene_retry_settings_differ'):
            self.scenes.describe(self.identifier, 'event_000', model='gemini-different', fps=4,
                                 retry_failed_analysis_id=failed_id, key_loader=lambda: self.fail('wrong model'))
        self.assertFalse((event_dir/'description-selection.json').exists())
        self.assertEqual(self.store.rows()[0]['state']['gemini_api_calls'], 1)

    def test_interrupted_retry_selection_requires_explicit_resume(self):
        failed_id = self.failed_attempt()
        self.scenes.preflight = lambda: (_ for _ in ()).throw(RuntimeError('synthetic schema failure'))
        with self.assertRaises(RuntimeError): self.describe(retry_failed_analysis_id=failed_id)
        self.scenes.preflight = self.scenes.check_schema if self.real_db else lambda: None
        with self.assertRaisesRegex(WorkerError, 'scene_retry_requires_explicit'):
            self.describe(key_loader=lambda: self.fail('implicit retry'))
        self.expected_attempts = 2
        self.assertEqual(self.describe(retry_failed_analysis_id=failed_id)['status'], 'description_stored')

    def test_saved_raw_response_and_success_block_new_retry(self):
        self.fetch()
        raw = response(); raw['candidates'][0]['finishReason'] = 'MAX_TOKENS'
        with patch.object(self, 'call', return_value=raw):
            with self.assertRaisesRegex(WorkerError, 'scene_attempt_failed'): self.describe()
        output = self.directory/'scenes/event_000/description'
        failed_id = read_json(output/'request.json')['analysis_id']
        with self.assertRaisesRegex(WorkerError, 'scene_saved_response_requires_review'):
            self.describe(retry_failed_analysis_id=failed_id)
        self.assertEqual(self.store.rows()[0]['state']['gemini_api_calls'], 1)
    def test_schema_failure_happens_before_key_and_paid_call(self):
        self.fetch()
        self.scenes.preflight = lambda: (_ for _ in ()).throw(RuntimeError('missing schema'))
        with self.assertRaises(RuntimeError): self.describe(key_loader=lambda: self.fail('unexpected key read'))
        self.assertEqual(self.calls, 0)
        self.assertFalse((self.directory/'scenes/event_000/description').exists())
    @unittest.skipUnless(os.environ.get('CCTV_QUEUE_ISOLATED_POSTGRES') == '1', 'isolated PostgreSQL only')
    def test_legacy_v1_and_v2_store_without_reclassifying_history(self):
        self.fetch(); self.describe()
        result = read_json(self.directory/'scenes/event_000/description/result.json')
        import uuid
        result.update(schema_version='cctv-scene-v1', analysis_id=str(uuid.uuid4()))
        result['description'].pop('accident_suspicion'); result['description'].pop('incident_types')
        result['description']['assessment'] = 'no_visible_collision'
        db.store(self.store.connection, result)
        rows = self.store.connection.execute('SELECT assessment, accident_suspicion FROM cctv_scene.descriptions ORDER BY created_at').fetchall()
        self.assertEqual(rows[0], dict(assessment=None, accident_suspicion='suspected'))
        self.assertEqual(rows[1], dict(assessment='no_visible_collision', accident_suspicion=None))
        import psycopg
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.store.connection.execute('DELETE FROM cctv_scene.descriptions')


if __name__ == '__main__': unittest.main()
