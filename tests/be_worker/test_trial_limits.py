"""Durable trial logic against a transactional DB double; no providers or models."""
from contextlib import contextmanager
from copy import deepcopy
import json
import threading
import types
import unittest

from apps.be_worker import analysis_profiles as profiles, generation_settings as generation
from apps.be_worker import trial_limits as trial
from apps.be_worker.common import WorkerError


class Database:
    def __init__(self):
        self.payload = None
        self.lock = threading.RLock()
        self.log = []
        self.table_exists = True
        self.lookup_error = None


class Connection:
    def __init__(self, db=None):
        self.db = db or Database()
        self.autocommit = True
        self.info = types.SimpleNamespace(transaction_status=0)
        self.fail_commit = False
        self.fail_write = False

    @contextmanager
    def transaction(self):
        if self.info.transaction_status != 0:
            raise AssertionError('unexpected nested transaction')
        with self.db.lock:
            previous = deepcopy(self.db.payload)
            self.info.transaction_status = 2
            self.db.log.append('begin')
            try:
                yield
                if self.fail_commit:
                    raise OSError('synthetic commit failure')
                self.db.log.append('commit')
            except BaseException:
                self.db.payload = previous
                self.db.log.append('rollback')
                raise
            finally:
                self.info.transaction_status = 0

    def execute(self, sql, parameters=None):
        query = ' '.join(sql.split())
        result = None
        if query == "SELECT to_regclass('cctv_jobs.service_state') AS table_name":
            if self.db.lookup_error is not None:
                raise self.db.lookup_error
            result = {'table_name': 'cctv_jobs.service_state' if self.db.table_exists else None}
        elif not self.db.table_exists:
            raise RuntimeError('synthetic undefined table')
        elif query.startswith('SELECT payload FROM cctv_jobs.service_state WHERE name=%s'):
            if parameters != (trial.NAME,):
                raise AssertionError('unexpected state name')
            if query.endswith('FOR UPDATE'):
                self.db.log.append('locked_select')
            if self.db.payload is not None:
                result = {'payload': deepcopy(self.db.payload)}
        elif query.startswith('INSERT INTO cctv_jobs.service_state'):
            if self.info.transaction_status != 2 or parameters[0] != trial.NAME:
                raise AssertionError('insert requires the fixed trial transaction')
            if self.db.payload is None:
                self.db.payload = json.loads(parameters[1])
            self.db.log.append('insert')
        elif query.startswith('UPDATE cctv_jobs.service_state'):
            if self.info.transaction_status != 2 or parameters[1] != trial.NAME:
                raise AssertionError('update requires the fixed trial transaction')
            if self.fail_write:
                raise OSError('synthetic write failure')
            if self.db.payload is not None:
                self.db.payload = json.loads(parameters[0])
                result = {'payload': deepcopy(self.db.payload)}
            self.db.log.append('update')
        else:
            raise AssertionError('unexpected SQL: ' + query)
        return types.SimpleNamespace(fetchone=lambda: deepcopy(result))


def row(version='A', identifier=None):
    frozen = profiles.profile(version)
    identifier = identifier or ('a' if version == 'A' else 'b') * 32
    return {'worker_job_id': identifier, 'state': {
        'worker_job_id': identifier, 'input_sha256': trial.INPUT_SHA256, 'input_bytes': trial.INPUT_BYTES,
        'analysis_profile': frozen,
        'gpu_key': profiles.gpu_identity(trial.INPUT_SHA256, frozen)['gpu_key'],
    }, 'handoff': {'scene_configs': ['scene-event_000.json']}}


def settings(version='A'):
    return generation.from_environment({}, analysis_profile=profiles.profile(version))


class TrialLimitsTests(unittest.TestCase):
    def setUp(self):
        self.conn = Connection()

    def prepare(self, version='A'):
        if trial.get(self.conn) is None:
            trial.initialize(self.conn, trial.approved_plan())
        current = row(version)
        with self.conn.transaction():
            trial.bind_job(self.conn, current, settings(version), '2026-10-02')
        trial.check_events(self.conn, current, ['event_000'], settings(version), 'full_report', '2026-10-02')
        return current

    def claim(self, current, kind='scene', request_sha='1' * 64):
        model = 'gemini-embedding-001' if kind == 'embedding' else trial.MODEL
        return trial.claim(self.conn, current, 'event_000', kind, request_sha, model=model)

    def test_absent_trial_keeps_legacy_calls_unchanged(self):
        self.assertIsNone(trial.get(self.conn))
        self.assertIsNone(trial.check_admission(self.conn, 'other', 1, [], 'legacy'))
        self.assertIsNone(trial.bind_job(self.conn, {}, None, None))
        self.assertIsNone(trial.check_events(self.conn, {}, [], None, 'legacy', None))
        self.assertIsNone(trial.claim(self.conn, {}, 'other', 'other', 'other'))
        self.assertIsNone(self.conn.db.payload)

    def test_absent_table_keeps_queue_only_legacy_behaviour_without_creating_table(self):
        self.conn.db.table_exists = False
        self.test_absent_trial_keeps_legacy_calls_unchanged()
        self.assertEqual(self.conn.db.log, [])
        with self.assertRaisesRegex(RuntimeError, 'undefined table'):
            trial.initialize(self.conn, trial.approved_plan())
        self.assertFalse(self.conn.db.table_exists)
        self.assertIsNone(self.conn.db.payload)

    def test_table_permission_and_connection_errors_never_disable_limits(self):
        for error in (PermissionError('synthetic denied'), ConnectionError('synthetic disconnected')):
            self.conn.db.lookup_error = error
            operations = (
                lambda: trial.get(self.conn),
                lambda: trial.check_admission(self.conn, 'other', 1, [], 'legacy'),
                lambda: trial.bind_job(self.conn, {}, None, None),
                lambda: trial.check_events(self.conn, {}, [], None, 'legacy', None),
                lambda: trial.claim(self.conn, {}, 'other', 'other', 'other'),
            )
            for operation in operations:
                with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                    operation()
        self.assertIsNone(self.conn.db.payload)

    def test_initialize_is_exact_idempotent_and_never_resets_claims(self):
        current = self.prepare()
        self.claim(current)
        original = deepcopy(self.conn.db.payload)
        self.assertEqual(trial.initialize(self.conn, trial.approved_plan()), original)
        wrong = trial.approved_plan()
        wrong['max_calls']['scene'] = 3
        with self.assertRaisesRegex(WorkerError, 'trial_plan_not_approved'):
            trial.initialize(self.conn, wrong)
        self.assertEqual(self.conn.db.payload, original)
        wrong = trial.approved_plan()
        wrong['max_candidates_per_version'] = True
        with self.assertRaises(WorkerError):
            trial.initialize(self.conn, wrong)

    def test_admission_accepts_only_fixed_input_versions_and_purpose(self):
        trial.initialize(self.conn, trial.approved_plan())
        for versions in (['A'], ['B'], ['A', 'B']):
            self.assertIsNotNone(trial.check_admission(self.conn, trial.INPUT_SHA256,
                                                    trial.INPUT_BYTES, versions, 'full_report'))
        for sha, size, versions, purpose in [
            ('f' * 64, trial.INPUT_BYTES, ['A'], 'full_report'),
            (trial.INPUT_SHA256, 1, ['A'], 'full_report'),
            (trial.INPUT_SHA256, trial.INPUT_BYTES, ['A', 'A'], 'full_report'),
            (trial.INPUT_SHA256, trial.INPUT_BYTES, ['B', 'A'], 'full_report'),
            (trial.INPUT_SHA256, trial.INPUT_BYTES, ['C'], 'full_report'),
            (trial.INPUT_SHA256, trial.INPUT_BYTES, ['A'], 'gpu_comparison'),
        ]:
            with self.subTest(versions=versions, purpose=purpose), self.assertRaises(WorkerError):
                trial.check_admission(self.conn, sha, size, versions, purpose)

    def test_bind_requires_transaction_and_preserves_exact_identity(self):
        trial.initialize(self.conn, trial.approved_plan())
        with self.assertRaisesRegex(WorkerError, 'admission_transaction'):
            trial.bind_job(self.conn, row(), settings(), '2026-10-02')
        current = self.prepare()
        original = deepcopy(self.conn.db.payload)
        with self.conn.transaction():
            trial.bind_job(self.conn, current, settings(), '2026-10-02')
        self.assertEqual(self.conn.db.payload, original)
        for changed in (row(identifier='c' * 32), row()):
            if changed['worker_job_id'] == 'a' * 32:
                changed['state']['gpu_key'] = 'wrong'
            with self.assertRaises(WorkerError), self.conn.transaction():
                trial.bind_job(self.conn, changed, settings(), '2026-10-02')
            self.assertEqual(self.conn.db.payload, original)
        with self.assertRaises(WorkerError), self.conn.transaction():
            trial.bind_job(self.conn, row('B', 'a' * 32), settings('B'), '2026-10-02')
        with self.assertRaises(WorkerError), self.conn.transaction():
            trial.bind_job(self.conn, row('B'), settings('B'), '2026-10-03')

    def test_bind_rollback_is_part_of_admission_transaction(self):
        trial.initialize(self.conn, trial.approved_plan())
        with self.assertRaises(RuntimeError), self.conn.transaction():
            trial.bind_job(self.conn, row(), settings(), '2026-10-02')
            raise RuntimeError('later admission insert failed')
        self.assertEqual(trial.get(self.conn)['bindings'], {})

    def test_multiple_zero_or_changed_candidates_cannot_be_claimed(self):
        trial.initialize(self.conn, trial.approved_plan())
        current = row()
        with self.conn.transaction():
            trial.bind_job(self.conn, current, settings(), '2026-10-02')
        current['handoff']['scene_configs'].append('scene-event_001.json')
        for requested in (['event_000', 'event_001'], ['event_000']):
            with self.assertRaises(WorkerError):
                trial.check_events(self.conn, current, requested, settings(), 'full_report', '2026-10-02')
        self.assertIsNone(trial.get(self.conn)['bindings']['A']['event_ids'])
        current['handoff']['scene_configs'] = []
        trial.check_events(self.conn, current, [], settings(), 'full_report', '2026-10-02')
        with self.assertRaises(WorkerError):
            self.claim(current)
        current['handoff']['scene_configs'] = ['scene-event_000.json']
        with self.assertRaisesRegex(WorkerError, 'existing_events_preserved'):
            trial.check_events(self.conn, current, ['event_000'], settings(), 'full_report', '2026-10-02')

    def test_settings_event_and_model_mismatches_stop_before_claim(self):
        current = self.prepare()
        changed = settings(); changed['scene_model'] = 'gemini-other'
        with self.assertRaises(WorkerError):
            trial.check_events(self.conn, current, ['event_000'], changed, 'full_report', '2026-10-02')
        with self.assertRaises(WorkerError):
            trial.check_events(self.conn, current, ['event_000'], settings(), 'full_report', '2026-10-03')
        with self.assertRaises(WorkerError):
            trial.claim(self.conn, current, 'event_000', 'scene', '1' * 64, model='gemini-other')
        with self.assertRaises(WorkerError):
            trial.claim(self.conn, current, 'event_001', 'scene', '1' * 64, model=trial.MODEL)
        with self.assertRaises(WorkerError):
            self.claim(current, request_sha='not-a-hash')
        current['handoff']['scene_configs'].append('scene-event_001.json')
        with self.assertRaises(WorkerError):
            self.claim(current)
        self.assertEqual(trial.get(self.conn)['claims'], [])

    def test_six_slots_commit_before_return_and_duplicate_is_never_retry(self):
        jobs = [self.prepare(version) for version in ('A', 'B')]
        for current in jobs:
            for kind in trial.KINDS:
                receipt = self.claim(current, kind)
                self.assertEqual(self.conn.info.transaction_status, 0)
                self.assertEqual(self.conn.db.log[-1], 'commit')
                self.assertIn(receipt, trial.get(self.conn)['claims'])
                for request_sha in ('1' * 64, '2' * 64):
                    with self.assertRaisesRegex(WorkerError, 'already_claimed_no_retry'):
                        self.claim(current, kind, request_sha)
        self.assertEqual(len(trial.get(self.conn)['claims']), 6)
        self.assertTrue('locked_select' in self.conn.db.log)

    def test_restart_preserves_claim_and_commit_or_write_failure_cannot_return(self):
        current = self.prepare()
        self.claim(current)
        self.conn = Connection(self.conn.db)
        with self.assertRaises(WorkerError):
            self.claim(current)
        for field in ('fail_commit', 'fail_write'):
            setattr(self.conn, field, True)
            with self.assertRaises(OSError):
                self.claim(current, 'report')
            setattr(self.conn, field, False)
            self.assertEqual(len(trial.get(self.conn)['claims']), 1)

    def test_claim_refuses_outer_transaction_or_non_autocommit(self):
        current = self.prepare()
        with self.conn.transaction():
            with self.assertRaisesRegex(WorkerError, 'idle_autocommit'):
                self.claim(current)
        self.conn.autocommit = False
        with self.assertRaisesRegex(WorkerError, 'idle_autocommit'):
            self.claim(current)
        self.assertEqual(trial.get(self.conn)['claims'], [])

    def test_two_simultaneous_claims_reserve_only_one_slot(self):
        current = self.prepare()
        barrier = threading.Barrier(2)
        outcomes = []
        def attempt():
            conn = Connection(self.conn.db)
            barrier.wait()
            try:
                trial.claim(conn, current, 'event_000', 'scene', '1' * 64, model=trial.MODEL)
                outcomes.append('claimed')
            except WorkerError as exc:
                outcomes.append(exc.code)
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, ['claimed', 'trial_attempt_already_claimed_no_retry'])
        self.assertEqual(len(trial.get(self.conn)['claims']), 1)


if __name__ == '__main__':
    unittest.main()
