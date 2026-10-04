"""Time-limited manual session checks; no providers/models. Optional isolated PG."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from apps.be_worker import hour_session as s, trial_limits, analysis_profiles as p, generation_settings as g
from apps.be_worker.common import WorkerError


class FakeDB:
    def __init__(self):
        self.rows = {'p6-final-trial': {'untouched': 'legacy claims'}}
        self.lock = threading.RLock()
        self.time = 1000.0


class Connection:
    def __init__(self, db):
        self.db=db; self.autocommit=True; self.info=types.SimpleNamespace(transaction_status=0)
        self.fail_commit=False

    @contextmanager
    def transaction(self):
        assert self.info.transaction_status == 0
        with self.db.lock:
            old=deepcopy(self.db.rows); self.info.transaction_status=2
            try:
                yield
                if self.fail_commit: raise RuntimeError('commit failure')
            except BaseException:
                self.db.rows=old
                raise
            finally: self.info.transaction_status=0

    def execute(self, query, args=None):
        result=None
        if query.startswith('SELECT extract'): result={'epoch':self.db.time}
        elif query.startswith('SELECT EXISTS'):
            result={'already_bound':any(name != args[0] and args[1] in payload.get('bindings', {})
                    for name,payload in self.db.rows.items() if name.startswith('p6-hour-session-'))}
        elif query.startswith('SELECT payload'): result={'payload':deepcopy(self.db.rows[args[0]])} if args[0] in self.db.rows else None
        elif query.startswith('INSERT INTO'): self.db.rows.setdefault(args[0],json.loads(args[1]))
        elif query.startswith('UPDATE '):
            self.db.rows[args[1]]=json.loads(args[0]); result={'payload':deepcopy(self.db.rows[args[1]])}
        else: raise AssertionError(query)
        return types.SimpleNamespace(fetchone=lambda:result)


class HourSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ,{'BE_SPOOL_PATH':self.tmp.name}); self.env.start(); self.addCleanup(self.env.stop)
        self.pg=os.environ.get('CCTV_HOUR_ISOLATED_PG')=='1'
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row
            self.open=lambda:psycopg.connect('postgresql://postgres@127.0.0.1:5432/postgres',autocommit=True,row_factory=dict_row)
            self.conn=self.open(); self.addCleanup(self.conn.close)
            self.conn.execute('DROP SCHEMA IF EXISTS cctv_jobs CASCADE')
            self.conn.execute('CREATE SCHEMA cctv_jobs')
            self.conn.execute('CREATE TABLE cctv_jobs.service_state (name text PRIMARY KEY,payload jsonb NOT NULL,updated_at timestamptz DEFAULT now())')
            self.conn.execute("INSERT INTO cctv_jobs.service_state VALUES ('p6-final-trial','{\"untouched\":\"legacy claims\"}')")
            self.t=s.clock(self.conn)
        else:
            self.db=FakeDB(); self.open=lambda:Connection(self.db); self.conn=self.open(); self.t=1000.0
        self.settings=g.from_environment(analysis_profile=p.profile('B'))

    def start(self): return s.initialize(self.conn,self.t,self.t+3600)

    def row(self, digit='1'):
        identity=p.gpu_identity(digit*64,p.profile('B'))
        return {'worker_job_id':digit*32,'state':{'worker_job_id':digit*32,'input_sha256':digit*64,
            'input_bytes':42,'analysis_profile':p.profile('B'),'gpu_key':identity['gpu_key']},
            'handoff':{'scene_configs':['scene-event_000.json']}}

    def bind(self,row):
        with self.conn.transaction():
            s.check_admission(self.conn,row['state']['input_sha256'],42,['B'],'full_report')
            s.bind_job(self.conn,row,self.settings,'2026-10-02')

    def ready(self):
        self.start(); row=self.row(); self.bind(row)
        s.check_events(self.conn,row,['event_000'],self.settings,'full_report','2026-10-02')
        return row

    def claim(self, conn, row): return s.claim(conn,row,'event_000','scene','a'*64,s.MODEL)

    def test_no_marker_keeps_original_routing(self): self.assertFalse(s.configured())

    def test_marker_without_db_fails_closed(self):
        s.marker_path().write_text(json.dumps(s.MARKER))
        with self.assertRaisesRegex(WorkerError,'not_started'):trial_limits.get(self.conn)

    def test_marker_corruption_does_not_disable_guard(self):
        s.marker_path().write_text('{}')
        with self.assertRaisesRegex(WorkerError,'marker_invalid'):s.configured()

    def test_two_inputs_and_existing_bindings_reused(self):
        self.start(); self.bind(self.row('1')); self.bind(self.row('2')); before=s.get(self.conn)
        self.bind(self.row('1')); self.assertEqual(before,s.get(self.conn))
        self.assertEqual(len(before['bindings']),2)

    def test_comparison_and_a_requests_are_rejected(self):
        self.start()
        for versions,purpose in [(['A'],'full_report'),(['B'],'gpu_comparison'),(['A','B'],'full_report')]:
            with self.assertRaisesRegex(WorkerError,'select_b_full_report'):
                with self.conn.transaction():s.check_admission(self.conn,'1'*64,42,versions,purpose)

    def test_initialize_preserves_attempts_and_old_trial(self):
        row=self.ready();self.claim(self.conn,row);before=s.get(self.conn)
        self.assertEqual(s.initialize(self.conn,self.t,self.t+3600),before)
        old=self.conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',('p6-final-trial',)).fetchone()['payload']
        self.assertEqual(old,{'untouched':'legacy claims'})

    def test_reinitializing_cannot_extend_window(self):
        self.start()
        with self.assertRaisesRegex(WorkerError,'existing_window_preserved'):
            s.initialize(self.conn,self.t-1,self.t+3599)

    def test_expiry_blocks_new_admission_and_generation(self):
        row=self.ready()
        with patch.object(s,'clock',return_value=self.t+3600):
            with self.assertRaisesRegex(WorkerError,'expired'):
                with self.conn.transaction():s.check_admission(self.conn,'2'*64,42,['B'],'full_report')
            with self.assertRaisesRegex(WorkerError,'expired'):self.claim(self.conn,row)
        self.assertEqual(s.get(self.conn)['claims'],[])

    def test_stopping_preserves_claims_and_blocks_calls(self):
        row=self.ready();self.claim(self.conn,row);before=s.get(self.conn)['claims'];s.stop(self.conn)
        self.assertEqual(s.get(self.conn)['claims'],before)
        with self.assertRaisesRegex(WorkerError,'stopped'):self.claim(self.conn,row)

    def test_duplicate_claim_is_not_retried(self):
        row=self.ready();self.claim(self.conn,row)
        with self.assertRaisesRegex(WorkerError,'already_claimed'):self.claim(self.conn,row)
        self.assertEqual(len(s.get(self.conn)['claims']),1)

    def test_changed_input_identity_is_rejected(self):
        row=self.ready();row['state']['input_bytes']=43
        with self.assertRaisesRegex(WorkerError,'event_not_approved'):self.claim(self.conn,row)
        self.assertEqual(s.get(self.conn)['claims'],[])

    def test_multiple_events_stop_before_generation(self):
        self.start();row=self.row();self.bind(row)
        with self.assertRaisesRegex(WorkerError,'candidate_limit'):
            s.check_events(self.conn,row,['event_000','event_001'],self.settings,'full_report','2026-10-02')

    def test_claim_rollback_keeps_attempt_unreserved(self):
        row=self.ready()
        original=s.write
        def failed(conn,value): original(conn,value);raise RuntimeError('write failure')
        with patch.object(s,'write',side_effect=failed):
            with self.assertRaisesRegex(RuntimeError,'write failure'):self.claim(self.conn,row)
        self.assertEqual(s.get(self.conn)['claims'],[])

    def test_competing_claims_commit_only_one_attempt(self):
        row=self.ready();results=[]
        def claim():
            conn=self.open()
            try:self.claim(conn,row);results.append('ok')
            except WorkerError:results.append('blocked')
            finally:
                if self.pg:conn.close()
        threads=[threading.Thread(target=claim) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(5)
        self.assertCountEqual(results,['ok','blocked'])
        self.assertEqual(len(s.get(self.conn)['claims']),1)


if __name__=='__main__':unittest.main()
