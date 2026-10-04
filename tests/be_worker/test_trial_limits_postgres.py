"""Real isolated PostgreSQL durability/concurrency; no cloud or model clients.

Run only with the user-run P6 trial check delivery. The database name and explicit
environment gate prevent accidental use against the application's database.
"""
from copy import deepcopy
import importlib
import hashlib
import json
import os
from pathlib import Path
import threading
import tempfile
import time
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles, generation_settings, selection_jobs, trial_limits as limits
from apps.be_worker.common import WorkerError
from apps.be_worker.queue_store import Store

ROOT = Path(__file__).resolve().parents[2]
DB = 'cctv_trial_limits_test'
ADMIN = 'postgresql://postgres@127.0.0.1:5432/' + DB
APP = 'postgresql://cctv_app@127.0.0.1:5432/' + DB
DAY = '2026-10-02'


@unittest.skipUnless(os.environ.get('CCTV_P6_TRIAL_ISOLATED_POSTGRES') == '1',
                     'Isolated PostgreSQL delivery required')
class TrialLimitsPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
        cls.psycopg, cls.row_factory, cls.Jsonb = psycopg, staticmethod(dict_row), Jsonb
        with psycopg.connect(ADMIN, autocommit=True, row_factory=dict_row) as admin:
            if admin.execute('SELECT current_database() AS name').fetchone()['name'] != DB:
                raise RuntimeError('isolated_database_required')
            admin.execute('CREATE ROLE cctv_app LOGIN')
            for name in ('apps/be_worker/queue_schema.sql', 'apps/scene_description/schema.sql',
                         'apps/be_worker/report_schema.sql', 'apps/be_worker/flow_schema.sql',
                         'apps/rag_index/schema.sql', 'apps/be_worker/selection_schema.sql'):
                admin.execute((ROOT / name).read_text())
            admin.execute("INSERT INTO cctv_jobs.service_state(name,payload) VALUES('worker','{\"stage\":\"idle\"}')")

    def setUp(self):
        self.admin = self.connect(admin=True)
        self.admin.execute('DELETE FROM cctv_jobs.service_state WHERE name=%s', (limits.NAME,))
        self.admin.execute('TRUNCATE cctv_jobs.analysis_selections,cctv_jobs.analysis_tasks,cctv_jobs.flows,cctv_jobs.queue RESTART IDENTITY CASCADE')
        self.conn = self.connect()

    def connect(self, *, admin=False):
        conn = self.psycopg.connect(ADMIN if admin else APP, autocommit=True, row_factory=self.row_factory)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute('SELECT current_database() AS name').fetchone()['name'], DB)
        return conn

    def job(self, version='A', *, events=('event_000',), identifier=None):
        profile = analysis_profiles.profile(version)
        identity = analysis_profiles.gpu_identity(limits.INPUT_SHA256, profile)
        worker_id = identifier or ('a' if version == 'A' else 'b') * 32
        return {'worker_job_id': worker_id, 'state': {
            'worker_job_id': worker_id, 'input_sha256': limits.INPUT_SHA256,
            'input_bytes': limits.INPUT_BYTES, 'analysis_profile': profile,
            'gpu_key': identity['gpu_key']},
            'handoff': {'scene_configs': ['scene-' + event + '.json' for event in events]}}

    def settings(self, version):
        return generation_settings.from_environment(analysis_profile=analysis_profiles.profile(version))

    def initialize(self):
        return limits.initialize(self.conn, limits.approved_plan())

    def bind(self, row, conn=None):
        conn = conn or self.conn
        version = row['state']['analysis_profile']['version']
        with conn.transaction():
            limits.bind_job(conn, row, self.settings(version), DAY)

    def ready(self, version='A', events=('event_000',)):
        row = self.job(version, events=events)
        self.bind(row)
        limits.check_events(self.conn, row, list(events), self.settings(version), 'full_report', DAY)
        return row

    def claim(self, conn, row, kind='scene', request='1' * 64):
        return limits.claim(conn, row, 'event_000', kind, request,
                            limits.approved_plan()['models'][kind])

    def test_absent_guard_preserves_ordinary_behavior_without_writes(self):
        before = self.admin.execute('SELECT name,payload,updated_at FROM cctv_jobs.service_state ORDER BY name').fetchall()
        self.assertIsNone(limits.get(self.conn))
        self.assertIsNone(limits.check_admission(self.conn, 'ordinary', 42, ['A'], 'gpu_comparison'))
        self.assertIsNone(limits.claim(self.conn, {}, 'ordinary', 'scene', 'invalid', 'ordinary'))
        self.assertEqual(before, self.admin.execute('SELECT name,payload,updated_at FROM cctv_jobs.service_state ORDER BY name').fetchall())

    def test_initialization_is_visible_to_new_connection_and_preserves_heartbeat(self):
        heartbeat = self.admin.execute("SELECT payload,updated_at FROM cctv_jobs.service_state WHERE name='worker'").fetchone()
        value = self.initialize()
        self.assertEqual(value, limits.get(self.connect()))
        self.assertEqual(value['claims'], [])
        self.assertEqual(heartbeat, self.admin.execute("SELECT payload,updated_at FROM cctv_jobs.service_state WHERE name='worker'").fetchone())
        rights = self.conn.execute("SELECT (has_table_privilege(current_user,'cctv_jobs.service_state','SELECT') AND has_table_privilege(current_user,'cctv_jobs.service_state','INSERT') AND has_table_privilege(current_user,'cctv_jobs.service_state','UPDATE')) AS allowed,has_table_privilege(current_user,'cctv_jobs.service_state','DELETE') AS deletion").fetchone()
        self.assertEqual(rights, {'allowed': True, 'deletion': False})

    def test_initialize_and_module_reload_preserve_consumed_attempt(self):
        self.initialize(); row = self.ready()
        self.claim(self.conn, row)
        saved = limits.get(self.connect())
        reloaded = importlib.reload(limits)
        self.assertEqual(reloaded.initialize(self.conn, reloaded.approved_plan()), saved)
        self.assertEqual(reloaded.get(self.connect()), saved)
        with self.assertRaisesRegex(WorkerError, 'trial_attempt_already_claimed_no_retry'):
            self.claim(self.connect(), row)
        self.assertEqual(limits.get(self.conn), saved)

    def test_binding_rollback_has_no_partial_version_or_date_changes(self):
        self.initialize(); row = self.job()
        with self.assertRaisesRegex(RuntimeError, 'synthetic_admission_failure'):
            with self.conn.transaction():
                limits.bind_job(self.conn, row, self.settings('A'), DAY)
                raise RuntimeError('synthetic_admission_failure')
        self.assertEqual(limits.get(self.connect())['bindings'], {})
        self.bind(row)
        with self.assertRaisesRegex(WorkerError, 'trial_existing_binding_preserved'):
            with self.conn.transaction():
                limits.bind_job(self.conn, self.job(identifier='c' * 32), self.settings('A'), DAY)
        self.assertEqual(limits.get(self.conn)['bindings']['A']['worker_job_id'], 'a' * 32)

    def test_committed_claim_survives_provider_failure_and_cannot_retry(self):
        self.initialize(); row = self.ready()
        with self.assertRaisesRegex(RuntimeError, 'synthetic_response_uncertain'):
            self.claim(self.conn, row)
            # A separate connection sees the reserved attempt before any external call.
            self.assertEqual(len(limits.get(self.connect())['claims']), 1)
            raise RuntimeError('synthetic_response_uncertain')
        with self.assertRaisesRegex(WorkerError, 'trial_attempt_already_claimed_no_retry'):
            self.claim(self.connect(), row, request='2' * 64)
        self.assertEqual(len(limits.get(self.conn)['claims']), 1)

    def test_two_connections_claim_same_slot_exactly_once(self):
        self.initialize(); row = self.ready()
        connections = [self.connect(), self.connect()]
        barrier = threading.Barrier(2); outcomes = []; lock = threading.Lock()
        def contender(conn):
            try:
                barrier.wait(timeout=5)
                result = self.claim(conn, row)
                outcome = ('claimed', result['version'])
            except WorkerError as exc:
                outcome = ('rejected', exc.code)
            except Exception as exc:
                outcome = ('unexpected', type(exc).__name__)
            with lock: outcomes.append(outcome)
        threads = [threading.Thread(target=contender, args=(conn,), daemon=True) for conn in connections]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads), 'concurrent_claim_timeout')
        self.assertCountEqual(outcomes, [('claimed', 'A'), ('rejected', 'trial_attempt_already_claimed_no_retry')])
        self.assertEqual(len(limits.get(self.connect())['claims']), 1)

    def test_a_b_six_slots_maximum_remain_consumed_after_reinitialization(self):
        self.initialize()
        for version in ('A', 'B'):
            row = self.ready(version)
            for kind in limits.KINDS: self.claim(self.conn, row, kind)
        state = limits.get(self.connect())
        self.assertEqual(len(state['claims']), 6)
        self.assertEqual({kind: sum(c['kind'] == kind for c in state['claims']) for kind in limits.KINDS},
                         {'scene': 2, 'report': 2, 'embedding': 2})
        self.assertEqual(self.initialize(), state)
        for kind in limits.KINDS:
            with self.assertRaisesRegex(WorkerError, 'trial_attempt_already_claimed_no_retry'):
                self.claim(self.connect(), self.job('B'), kind)
        self.assertEqual(limits.get(self.conn), state)

    def test_no_candidates_blocks_calls_and_multiple_candidates_preserve_unset_events(self):
        self.initialize(); empty = self.ready('A', events=())
        with self.assertRaisesRegex(WorkerError, 'trial_event_not_approved'):
            self.claim(self.conn, empty)
        many = self.job('B', events=('event_000', 'event_001')); self.bind(many)
        with self.assertRaisesRegex(WorkerError, 'trial_candidate_limit_or_identity'):
            limits.check_events(self.conn, many, ['event_000', 'event_001'], self.settings('B'), 'full_report', DAY)
        state = limits.get(self.connect())
        self.assertEqual(state['bindings']['A']['event_ids'], [])
        self.assertIsNone(state['bindings']['B']['event_ids'])
        self.assertEqual(state['claims'], [])

    def test_wrong_input_purpose_model_and_active_transaction_are_rejected(self):
        self.initialize(); row = self.ready()
        snapshot = limits.get(self.conn)
        for args in [('0' * 64, limits.INPUT_BYTES, ['A', 'B'], 'full_report'),
                     (limits.INPUT_SHA256, limits.INPUT_BYTES, ['A', 'B'], 'gpu_comparison')]:
            with self.assertRaises(WorkerError): limits.check_admission(self.conn, *args)
        with self.assertRaisesRegex(WorkerError, 'trial_call_model_changed'):
            limits.claim(self.conn, row, 'event_000', 'scene', '1' * 64, 'gemini-other')
        with self.assertRaisesRegex(WorkerError, 'trial_requires_idle_autocommit'):
            with self.conn.transaction(): self.claim(self.conn, row)
        self.assertEqual(limits.get(self.connect()), snapshot)

    def test_invalid_existing_plan_is_preserved_and_fails_closed(self):
        self.initialize()
        self.admin.execute("UPDATE cctv_jobs.service_state SET payload=jsonb_set(payload,'{plan,max_calls,scene}','200') WHERE name=%s", (limits.NAME,))
        before = self.admin.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s', (limits.NAME,)).fetchone()
        with self.assertRaisesRegex(WorkerError, 'trial_plan_not_approved'):
            self.initialize()
        self.assertEqual(before, self.admin.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s', (limits.NAME,)).fetchone())

    def synthetic_admission(self, root, store=None):
        return selection_jobs.admit(store or Store(self.conn), root / 'input.mp4', DAY, 'A+B', 'full_report',
            spool=root / 'spool', config={'runpod_url':'https://test.invalid',
            's3_bucket':'cctv-test-bucket','s3_region':'ap-northeast-2'}, jsonb=self.Jsonb)

    def counts(self):
        return tuple(self.conn.execute('SELECT count(*) AS count FROM cctv_jobs.' + name).fetchone()['count']
                     for name in ('queue', 'analysis_tasks', 'analysis_selections'))

    def test_active_guard_full_admission_double_click_new_connection_preserves_claim(self):
        # The two constants are substituted only inside this isolated test, using
        # the actual SHA/length of synthetic bytes. No user video is needed.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); data = b'\0\0\0\x18ftypisom-SYNTHETIC-P6-TRIAL-CHECK'
            (root / 'input.mp4').write_bytes(data)
            with patch.object(limits, 'INPUT_SHA256', hashlib.sha256(data).hexdigest()), \
                 patch.object(limits, 'INPUT_BYTES', len(data)):
                self.initialize()
                first = self.synthetic_admission(root)
                self.assertEqual(self.counts(), (2, 2, 1))
                self.assertEqual(set(limits.get(self.conn)['bindings']), {'A', 'B'})
                row = next(row for row in Store(self.conn).rows() if row['state']['analysis_profile']['version'] == 'A')
                row['handoff'] = {'scene_configs': ['scene-event_000.json']}
                limits.check_events(self.conn, row, ['event_000'], self.settings('A'), 'full_report', DAY)
                self.claim(self.conn, row)
                consumed = limits.get(self.conn)
                again = self.synthetic_admission(root, Store(self.connect()))
                self.assertEqual(first['selection_id'], again['selection_id'])
                self.assertTrue(again['already_present'])
                self.assertTrue(all(item['already_present'] for item in again['jobs']))
                self.assertEqual(self.counts(), (2, 2, 1))
                self.assertEqual(limits.get(self.connect()), consumed)

    def test_active_guard_second_binding_failure_rolls_back_all_database_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); data = b'\0\0\0\x18ftypisom-SYNTHETIC-P6-TRIAL-ROLLBACK'
            (root / 'input.mp4').write_bytes(data)
            with patch.object(limits, 'INPUT_SHA256', hashlib.sha256(data).hexdigest()), \
                 patch.object(limits, 'INPUT_BYTES', len(data)):
                self.initialize(); original = limits.bind_job
                def bind_or_fail(conn, row, settings, reference_date):
                    if row['state']['analysis_profile']['version'] == 'B':
                        raise WorkerError('synthetic_second_binding_failure')
                    return original(conn, row, settings, reference_date)
                with patch.object(limits, 'bind_job', side_effect=bind_or_fail):
                    with self.assertRaisesRegex(WorkerError, 'synthetic_second_binding_failure'):
                        self.synthetic_admission(root)
                self.assertEqual(self.counts(), (0, 0, 0))
                self.assertEqual(limits.get(self.connect())['bindings'], {})
                self.assertEqual(limits.get(self.conn)['claims'], [])
                # Private unsubmitted spool orphans are preserved after SQL rollback.
                states = [json.loads(path.read_text()) for path in (root / 'spool').glob('*/state.json')]
                self.assertEqual(len(states), 2)
                self.assertTrue(all(state['status'] == 'prepared' and state.get('post_attempts', 0) == 0 for state in states))


if __name__ == '__main__':
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TrialLimitsPostgresTests))
    receipt = {'status': 'passed' if result.wasSuccessful() and result.testsRun and not result.skipped else 'failed',
               'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
               'skipped': len(result.skipped), 'elapsed_seconds': round(time.monotonic() - started, 6),
               'database': DB, 'real_postgres_transactions': True, 'external_requests': 0,
               'admission_fixture': 'synthetic_mp4_bytes_and_test_only_input_sha_size_constants',
               'gpu_calls': 0, 'gemini_calls': 0, 'embedding_calls': 0}
    output = os.environ.get('CCTV_P6_TEST_RECEIPT')
    if output:
        with Path(output).open('x') as stream: json.dump(receipt, stream, indent=2)
    print(json.dumps(receipt))
    raise SystemExit(0 if receipt['status'] == 'passed' else 1)
