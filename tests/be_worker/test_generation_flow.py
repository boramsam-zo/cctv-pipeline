"""P5 orchestration with real spool files and a transactional in-memory DB.

The SQL adapter deliberately implements only statements used by this test. It
models rollback but is not evidence of real PostgreSQL or Compose validation.
No HTTP, model, credential, production DB, or Docker call is made.
"""
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_queue import MemoryStore
from apps.be_worker import flow_service as flow, generation_settings as generation
from apps.be_worker.common import WorkerError, read_json, save
from apps.scene_description import scene


class TransactionalConnection:
    """Purposefully small fake; unknown SQL fails instead of appearing to pass."""
    def __init__(self, store):
        self.store, self.flows, self.heartbeats = store, {}, []
        self.closed = False
        self.fail_flow_insert = False
        self.transactions = self.rollbacks = 0
        self.advisory_locks = set()
        self.aborted = False

    @contextmanager
    def transaction(self):
        self.transactions += 1
        before = copy.deepcopy((self.store.data, self.flows))
        try:
            yield
        except BaseException:
            self.store.data, self.flows = before
            self.aborted = False
            self.rollbacks += 1
            raise

    def execute(self, sql, params=()):
        if self.aborted:
            raise RuntimeError('synthetic aborted transaction rejects SQL until rollback')
        statement = ' '.join(sql.split())
        answer = None
        if statement.startswith('SELECT * FROM cctv_jobs.analysis_tasks WHERE NOT paused'):
            answer = []  # Legacy jobs never become A/B tasks implicitly.
        elif statement.startswith('INSERT INTO cctv_jobs.flows'):
            if self.fail_flow_insert:
                raise RuntimeError('synthetic interruption after queue insertion')
            identifier, reference_date, settings = params
            if identifier in self.flows:
                if 'ON CONFLICT' not in statement:
                    raise AssertionError('unexpected duplicate flow insertion')
            else:
                self.flows[identifier] = dict(worker_job_id=identifier,
                    reference_date=reference_date, generation_settings=copy.deepcopy(settings),
                    status='queued', error_code=None, paused=False, finished_at=None)
        elif statement.startswith('UPDATE cctv_jobs.flows SET generation_settings='):
            settings, identifier = params
            row = self.flows[identifier]
            if row['generation_settings'] is None:
                row['generation_settings'] = copy.deepcopy(settings)
        elif statement.startswith('SELECT generation_settings FROM cctv_jobs.flows'):
            row = self.flows.get(params[0])
            answer = dict(generation_settings=copy.deepcopy(row['generation_settings'])) if row else None
        elif statement.startswith('SELECT * FROM cctv_jobs.flows WHERE NOT paused'):
            answer = [copy.deepcopy(row) for row in self.flows.values()
                      if not row['paused'] and row['finished_at'] is None]
        elif statement.startswith('SELECT worker_job_id FROM cctv_jobs.flows WHERE NOT paused'):
            answer = [dict(worker_job_id=row['worker_job_id']) for row in self.flows.values()
                      if not row['paused'] and row['finished_at'] is None]
        elif statement.startswith('SELECT *, retry_after <= now() AS ready FROM cctv_jobs.queue'):
            answer = self.store.rows()
        elif statement.startswith('INSERT INTO cctv_jobs.service_state'):
            self.heartbeats.append(copy.deepcopy(params[0]))
        elif statement.startswith('UPDATE cctv_jobs.flows SET status='):
            status, error, paused, finished, identifier = params
            row = self.flows[identifier]
            row.update(status=status, error_code=error, paused=paused)
            if finished:
                row['finished_at'] = row['finished_at'] or 'synthetic-finished-time'
        elif statement.startswith('SELECT pg_try_advisory_lock'):
            key = params[0]
            answer = {'ok': key not in self.advisory_locks}
            if answer['ok']:
                self.advisory_locks.add(key)
        elif statement.startswith('SELECT pg_advisory_unlock'):
            self.advisory_locks.remove(params[0])
        else:
            raise AssertionError('Unimplemented fake SQL: ' + statement)
        return Mock(fetchone=lambda: copy.deepcopy(answer), fetchall=lambda: copy.deepcopy(answer))


class GenerationFlowTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(flow.trial_limits, 'get', return_value=None)
        guard.start(); self.addCleanup(guard.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spool = self.root / 'spool'
        self.store = MemoryStore()
        self.store.locks.update(admission=threading.Lock(), flow=threading.Lock())
        self.conn = TransactionalConnection(self.store)
        self.store.connection = self.conn
        self.config = dict(runpod_url='https://test.invalid',
                           s3_bucket='cctv-synthetic-bucket', s3_region='ap-northeast-2')
        for patcher in (patch.object(flow, 'spool', return_value=self.spool),
                        patch.object(flow, 'config', return_value=self.config),
                        patch.object(flow, 'jsonb', side_effect=copy.deepcopy),
                        patch.dict(os.environ, {}, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def video(self, number=1):
        path = self.root / f'input-{number}.mp4'
        path.write_bytes(b'0000ftyp-SYNTHETIC-' + str(number).encode())
        return path

    def admit(self, number=1, reference_date='2026-10-01'):
        return flow.admit(self.store, self.video(number), reference_date)

    def context(self, identifier):
        row = next(row for row in self.store.rows() if row['worker_job_id'] == identifier)
        queue = Mock()
        queue.directory.side_effect = lambda item: self.spool / item['directory_id']
        queue.store = self.store
        return queue, row, self.conn.flows[identifier]

    def test_new_defaults_affect_only_new_video_and_duplicate_keeps_original_date(self):
        with patch.dict(os.environ, {generation.SCENE_MODEL_ENV: 'gemini-scene-a',
                                    generation.REPORT_MODEL_ENV: 'gemini-report-a'}):
            first = self.admit()
        original = copy.deepcopy(self.conn.flows[first['worker_job_id']])
        with patch.dict(os.environ, {generation.SCENE_MODEL_ENV: 'gemini-scene-b',
                                    generation.REPORT_MODEL_ENV: 'gemini-report-b'}):
            repeated = self.admit(reference_date='2026-10-02')
            new = self.admit(2)
        self.assertEqual(repeated['worker_job_id'], first['worker_job_id'])
        self.assertTrue(repeated['already_present'])
        self.assertEqual(self.conn.flows[first['worker_job_id']], original)
        self.assertEqual(self.conn.flows[new['worker_job_id']]['generation_settings']['scene_model'], 'gemini-scene-b')
        self.assertEqual(len(self.store.rows()), 2)
        self.assertEqual(self.conn.transactions, 2)

    def test_invalid_current_defaults_do_not_prevent_existing_result_lookup(self):
        first = self.admit()
        with patch.dict(os.environ, {generation.SCENE_MODEL_ENV: 'not/a/model'}):
            repeated = self.admit()
            with self.assertRaisesRegex(WorkerError, 'generation_model_invalid'):
                self.admit(2)
        self.assertEqual(first['worker_job_id'], repeated['worker_job_id'])
        self.assertEqual(len(self.store.rows()), 1)

    def test_transaction_failure_rolls_back_both_rows_and_preserves_unsubmitted_spool(self):
        self.conn.fail_flow_insert = True
        with patch.object(flow, 'api_for') as api:
            with self.assertRaisesRegex(RuntimeError, 'synthetic interruption'):
                self.admit()
        api.assert_not_called()
        self.assertEqual(self.store.rows(), [])
        self.assertEqual(self.conn.flows, {})
        self.assertEqual(self.conn.rollbacks, 1)
        directories = list(self.spool.iterdir())
        self.assertEqual(len(directories), 1)
        orphan = directories[0]
        state = read_json(orphan / 'state.json')
        self.assertEqual((state['status'], state['post_attempts'], state['provider_job_id']),
                         ('prepared', 0, None))
        before = {path.name: path.read_bytes() for path in orphan.iterdir() if path.is_file()}
        self.conn.fail_flow_insert = False
        self.admit()
        self.assertEqual(len(self.store.rows()), 1)
        self.assertEqual(len(list(self.spool.iterdir())), 2)
        self.assertEqual(before, {path.name: path.read_bytes() for path in orphan.iterdir() if path.is_file()})

    def test_queue_insert_abort_rolls_back_before_releasing_session_locks(self):
        # Exercise the real SQL advisory-lock context manager. Unlike ordinary
        # Python errors, an SQL failure makes PostgreSQL reject the unlock
        # statement until rollback; the fake reproduces that ordering rule.
        self.store.lock = flow.Store(self.conn).lock
        def failing_insert(*args):
            self.conn.aborted = True
            raise RuntimeError('synthetic queue INSERT failure')
        with patch.object(self.store, 'insert', side_effect=failing_insert):
            with self.assertRaisesRegex(RuntimeError, 'synthetic queue INSERT failure'):
                self.admit()
        self.assertFalse(self.conn.aborted)
        self.assertEqual(self.conn.advisory_locks, set())
        self.assertEqual(self.conn.rollbacks, 1)
        self.assertEqual(self.store.rows(), [])
        self.assertEqual(self.conn.flows, {})
        self.admit()  # Same connection can acquire both locks again.
        self.assertEqual(len(self.store.rows()), 1)
        self.assertEqual(self.conn.advisory_locks, set())

    def test_legacy_null_snapshot_is_pinned_without_new_environment_defaults(self):
        identifier = self.admit()['worker_job_id']
        queue, row, old_flow = self.context(identifier)
        old_flow['generation_settings'] = None
        (queue.directory(row) / 'flow-settings.json').unlink()
        with patch.dict(os.environ, {generation.SCENE_MODEL_ENV: 'gemini-new-model',
                                    generation.REPORT_MODEL_ENV: 'gemini-other-model'}):
            settings = flow.prepare_settings(self.conn, queue, row, old_flow)
        self.assertEqual(settings, generation.legacy_settings())
        self.assertEqual(self.conn.flows[identifier]['generation_settings'], settings)
        receipt = read_json(queue.directory(row) / 'flow-settings.json')
        self.assertEqual(receipt['generation_settings'], settings)
        self.assertEqual(receipt['reference_date'], '2026-10-01')

    def test_receipt_mismatch_blocks_without_overwriting_either_copy(self):
        identifier = self.admit()['worker_job_id']
        queue, row, saved_flow = self.context(identifier)
        path = queue.directory(row) / 'flow-settings.json'
        receipt = read_json(path)
        receipt['generation_settings']['report_model'] = 'gemini-wrong-model'
        save(path, receipt)
        before = path.read_bytes()
        saved = copy.deepcopy(saved_flow)
        with self.assertRaisesRegex(WorkerError, 'flow_settings_receipt_mismatch'):
            flow.prepare_settings(self.conn, queue, row, saved_flow)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(saved_flow, saved)

    def test_restart_passes_pinned_models_to_both_processing_steps(self):
        with patch.dict(os.environ, {generation.SCENE_MODEL_ENV: 'gemini-scene-pinned',
                                    generation.REPORT_MODEL_ENV: 'gemini-report-pinned'}):
            identifier = self.admit()['worker_job_id']
        queue, row, saved_flow = self.context(identifier)
        row['handoff'] = {'scene_configs': ['scene-event_000.json']}
        self.store.data[0]['handoff'] = copy.deepcopy(row['handoff'])
        scenes, reports, emit = Mock(), Mock(), Mock()
        with patch.dict(os.environ, {generation.SCENE_MODEL_ENV: 'gemini-new-default',
                                    generation.REPORT_MODEL_ENV: 'gemini-new-report'}):
            settings = flow.prepare_settings(self.conn, queue, row, copy.deepcopy(saved_flow))
            flow.process_events(queue, row, saved_flow['reference_date'], emit,
                                scenes, reports, settings=settings)
        scenes.describe.assert_called_once_with(identifier, 'event_000',
            model='gemini-scene-pinned', fps=4, key_loader=flow.key)
        reports.run.assert_called_once_with(identifier, 'event_000', '2026-10-01',
            'gemini-report-pinned', flow.key, expected_scene={
                'model': 'gemini-scene-pinned', 'fps': 4, 'prompt_version': 'cctv-scene-v2'})
        self.assertEqual(emit.call_args.args, ('completed',))

    def test_prompt_drift_pauses_before_new_gpu_submission(self):
        identifier = self.admit()['worker_job_id']
        with patch.object(scene, 'SYSTEM', scene.SYSTEM + '\nChanged prompt.'), \
                patch.object(flow, 'api_for') as api:
            flow.tick(self.conn)
        api.assert_not_called()
        saved = self.conn.flows[identifier]
        self.assertEqual(saved['status'], 'paused')
        self.assertTrue(saved['paused'])
        self.assertEqual(saved['error_code'], 'generation_contract_changed')
        self.assertEqual(self.store.data[0]['state']['post_attempts'], 0)

    def test_dispatch_filter_rechecks_snapshot_and_receipt_after_admission(self):
        identifier = self.admit()['worker_job_id']
        queue, row, saved_flow = self.context(identifier)
        automatic = flow.AutomaticStore(self.conn)
        self.assertEqual([item['worker_job_id'] for item in automatic.rows()], [identifier])
        receipt = queue.directory(row) / 'flow-settings.json'
        original = receipt.read_bytes()
        receipt.unlink()
        self.assertEqual(automatic.rows(), [])
        receipt.write_bytes(original)
        saved_flow['generation_settings']['scene_model'] = 'invalid/model'
        self.assertEqual(automatic.rows(), [])
        # An existing uncertain submission must still occupy a GPU slot; the
        # filter must not hide it and allow a second charged submission.
        self.store.data[0]['state']['status'] = 'submission_unknown'
        self.assertEqual([item['worker_job_id'] for item in automatic.rows()], [identifier])

    def test_admission_lock_blocks_overlapping_click_before_creating_spool(self):
        with self.store.lock('admission'):
            with self.assertRaisesRegex(WorkerError, 'queue_worker_already_running'):
                self.admit()
        self.assertEqual(self.store.rows(), [])
        self.assertFalse(self.spool.exists())
        accepted = self.admit()
        repeated = self.admit()
        self.assertEqual(accepted['worker_job_id'], repeated['worker_job_id'])
        self.assertEqual(len(self.store.rows()), 1)

    def test_no_candidates_and_provider_failure_remain_distinct(self):
        no_candidates = self.admit()['worker_job_id']
        failed = self.admit(2)['worker_job_id']
        for row, status in zip(self.store.data, ('no_candidates', 'provider_failed')):
            row['state'].update(status=status, error_code='provider_failed_check_log' if status == 'provider_failed' else None)
        with patch.object(flow, 'api_for') as api, patch.object(flow, 'process_events') as events:
            flow.tick(self.conn)
        api.assert_not_called()
        events.assert_not_called()
        self.assertEqual(self.conn.flows[no_candidates]['status'], 'no_candidates')
        self.assertIsNotNone(self.conn.flows[no_candidates]['finished_at'])
        self.assertFalse(self.conn.flows[no_candidates]['paused'])
        self.assertEqual(self.conn.flows[failed]['status'], 'provider_failed')
        self.assertTrue(self.conn.flows[failed]['paused'])
        self.assertIsNone(self.conn.flows[failed]['finished_at'])


if __name__ == '__main__':
    unittest.main()
