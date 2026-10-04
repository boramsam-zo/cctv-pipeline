"""Team admission and durable limits; no provider, model or production DB."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker import generation_settings as g, team_settings as t
from apps.be_worker import hour_session as h, analysis_profiles as p, selection_jobs as j
from apps.be_worker.common import WorkerError
from tests.be_worker.test_hour_session import FakeDB, Connection


class TeamSettingsTests(unittest.TestCase):
    def setUp(self):
        self.profile=p.profile('B')
        self.value=g.from_environment({'BE_REPORT_PIPELINE':t.PIPELINE},analysis_profile=self.profile)

    def test_new_route_freezes_cpu_engine_and_no_date(self):
        self.assertEqual(g.validate(self.value,analysis_profile=self.profile),self.value)
        self.assertEqual(self.value['report_model'],'cpu-template')
        self.assertFalse(self.value['document_reference_date_supported'])
        self.assertNotEqual(self.value['embedding_profile'],g.legacy_settings()['embedding_profile'])
        self.assertNotIn('api_key',self.value)

    def test_a_is_out_of_scope(self):
        with self.assertRaises(WorkerError):
            g.from_environment({'BE_REPORT_PIPELINE':t.PIPELINE},analysis_profile=p.profile('A'))

    def test_legacy_snapshots_remain_valid_after_default_switch(self):
        old=g.from_environment({},analysis_profile=self.profile)
        with patch.dict(os.environ,{'BE_REPORT_PIPELINE':t.PIPELINE}):
            self.assertEqual(g.validate(old,analysis_profile=self.profile),old)
            self.assertEqual(g.validate(g.legacy_settings()),g.legacy_settings())

    def test_unknown_route_and_modified_contract_fail_closed(self):
        with self.assertRaises(WorkerError):g.from_environment({'BE_REPORT_PIPELINE':'typo'})
        for key in ('corpus_sha256','index_sha256','scene_adapter_sha256','report_code_sha256'):
            altered=deepcopy(self.value);altered[key]='0'*64
            with self.assertRaisesRegex(WorkerError,'contract_changed'):
                g.validate(altered,analysis_profile=self.profile)

    def test_dates_are_versioned(self):
        self.assertIsNone(t.reference_date(self.value,None))
        with self.assertRaisesRegex(WorkerError,'unsupported'):t.reference_date(self.value,'2026-10-03')
        self.assertEqual(t.reference_date(g.legacy_settings(),'2026-10-03'),'2026-10-03')
        with self.assertRaises(ValueError):t.reference_date(g.legacy_settings(),None)

    def test_task_identity_preserves_null_and_binds_settings(self):
        wid='1'*32;sha='2'*64;gpu=p.gpu_identity(sha,self.profile)['gpu_key']
        row={'worker_job_id':wid,'state':{'input_sha256':sha,'analysis_profile':self.profile,'gpu_key':gpu}}
        task=dict(worker_job_id=wid,version='B',gpu_key=gpu,purpose='full_report',
                  generation_settings=self.value,reference_date=None)
        task['task_id']=j.identifier({k:task[k] for k in ('gpu_key','purpose','generation_settings','reference_date')})
        self.assertEqual(j.validate(task,row),self.value)
        task['reference_date']='2026-10-03'
        with self.assertRaisesRegex(WorkerError,'unsupported'):j.validate(task,row)


class TeamHourTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{'BE_SPOOL_PATH':self.tmp.name});env.start();self.addCleanup(env.stop)
        self.db=FakeDB();self.conn=Connection(self.db)
        profile=p.profile('B');sha='1'*64;wid='1'*32
        self.row={'worker_job_id':wid,'state':{'worker_job_id':wid,'input_sha256':sha,'input_bytes':42,
                  'analysis_profile':profile,'gpu_key':p.gpu_identity(sha,profile)['gpu_key']},
                  'handoff':{'scene_configs':['scene-event_000.json']}}
        self.value=g.from_environment({'BE_REPORT_PIPELINE':t.PIPELINE},analysis_profile=profile)
        h.initialize(self.conn,1000.,4600.)
        with self.conn.transaction():h.bind_job(self.conn,self.row,self.value,None)
        h.check_events(self.conn,self.row,['event_000'],self.value,'full_report',None)

    def test_scene_cpu_claims_are_separate_and_each_single_attempt(self):
        for kind in ('scene','cpu_retrieval'):
            h.claim(self.conn,self.row,'event_000',kind,'a'*64,h.model_for(kind))
            with self.assertRaisesRegex(WorkerError,'already_claimed'):
                h.claim(self.conn,self.row,'event_000',kind,'a'*64,h.model_for(kind))
        saved=h.get(self.conn)
        self.assertEqual([c['kind'] for c in saved['claims']],['scene','cpu_retrieval'])
        self.assertIsNone(saved['bindings']['1'*32]['reference_date'])
        self.assertEqual(self.db.rows['p6-final-trial'],{'untouched':'legacy claims'})

    def test_gemini_embedding_and_report_forbidden_for_team(self):
        for kind in ('embedding','report'):
            with self.assertRaisesRegex(WorkerError,'external_report_forbidden'):
                h.claim(self.conn,self.row,'event_000',kind,'a'*64,h.model_for(kind))
        self.assertEqual(h.get(self.conn)['claims'],[])

    def test_expiry_stops_cpu_and_keeps_old_window(self):
        self.db.time=4600.
        before=deepcopy(self.db.rows)
        with self.assertRaisesRegex(WorkerError,'expired'):
            h.claim(self.conn,self.row,'event_000','cpu_retrieval','a'*64,h.model_for('cpu_retrieval'))
        with self.assertRaises(WorkerError):h.initialize(self.conn,4600.,8200.)
        self.assertEqual(self.db.rows,before)

    def test_legacy_claims_and_date_survive_mixed_state(self):
        wid='3'*32;sha='3'*64;old=deepcopy(self.row)
        old['worker_job_id']=wid;old['state'].update(worker_job_id=wid,input_sha256=sha,
            gpu_key=p.gpu_identity(sha,old['state']['analysis_profile'])['gpu_key'])
        settings=g.from_environment({},analysis_profile=old['state']['analysis_profile'])
        with self.conn.transaction():h.bind_job(self.conn,old,settings,'2026-10-03')
        h.check_events(self.conn,old,['event_000'],settings,'full_report','2026-10-03')
        h.claim(self.conn,old,'event_000','embedding','b'*64,h.model_for('embedding'))
        state=h.get(self.conn)
        self.assertEqual(state['bindings'][wid]['reference_date'],'2026-10-03')
        self.assertEqual(state['claims'][0]['model'],'gemini-embedding-001')
        with self.assertRaisesRegex(WorkerError,'cpu_pipeline_invalid'):
            h.claim(self.conn,old,'event_000','cpu_retrieval','c'*64,h.model_for('cpu_retrieval'))


if __name__=='__main__':unittest.main()
