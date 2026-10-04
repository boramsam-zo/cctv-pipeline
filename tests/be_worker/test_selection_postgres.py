"""Actual isolated PostgreSQL transactions; all provider/model calls are doubles.

Run only through scripts/check_p6_postgres.py. The DB/container are disposable;
production DB, credentials, local service volumes and external networks are absent.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles, flow_service, generation_settings, job_queue, selection_jobs, worker
from apps.be_worker.common import WorkerError, read_json, save
from apps.be_worker.prepare_selections import sql as selection_migration
from apps.be_worker.queue_store import Store
from apps.be_worker.runpod_api import ApiError

ROOT=Path(__file__).resolve().parents[2]
DB='cctv_selection_test'
APP='postgresql://cctv_app@127.0.0.1:5432/'+DB
ADMIN='postgresql://postgres@127.0.0.1:5432/'+DB
CONFIG={'runpod_url':'https://test.invalid','s3_bucket':'cctv-test-bucket','s3_region':'ap-northeast-2'}


class FakePod:
    def __init__(self):self.posts=[];self.jobs={};self.error=None;self.lookups=0;self.resumes=0;self.recovered_id=None;self.analysis_complete=False
    def api(self,state):
        outer=self
        class API:
            def health(self,expected_profile=None):return {'storage':'s3'}
            def submit_versioned(self,path,current):
                outer.posts.append(current['worker_job_id'])
                if outer.error:raise outer.error
                identifier=f'{len(outer.posts):032x}'
                outer.jobs[identifier]=dict(state=deepcopy(current),status='running')
                return identifier
            def lookup_versioned(self,current):
                outer.lookups+=1
                if outer.recovered_id is None:raise WorkerError('no_saved_provider_job')
                return outer.recovered_id
            def resume_evidence(self,current):
                outer.resumes+=1
                if not outer.analysis_complete:raise WorkerError('provider_analysis_not_complete')
                outer.jobs[current['provider_job_id']]['status']='queued'
            def status(self,identifier):
                job=outer.jobs[identifier];original=job['state']
                identity=analysis_profiles.gpu_identity(original['input_sha256'],original['analysis_profile'])
                return {'schema_version':'runpod-ab-api-v1','job_id':identifier,'status':job['status'],
                        'analysis_profile':identity['profile'],'profile_sha256':identity['profile_sha256'],
                        'gpu_key':identity['gpu_key'],'input_sha256':original['input_sha256'],
                        'input_bytes':original['input_bytes'],'storage':'s3','s3_uploaded':True,
                        's3':{'bucket':CONFIG['s3_bucket'],'region':CONFIG['s3_region'],
                              'manifest_key':f'cctv-evidence/{identifier}/manifest.json','s3_uploaded':True,
                              'analysis_profile':identity['profile'],'gpu_key':identity['gpu_key'],
                              'input_sha256':original['input_sha256'],'manifest_sha256':'a'*64}}
        return API()


@unittest.skipUnless(os.environ.get('CCTV_P6_ISOLATED_POSTGRES')=='1','Isolated PostgreSQL runner required')
class SelectionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
        cls.psycopg,cls.row_factory,cls.Jsonb=psycopg,staticmethod(dict_row),Jsonb
        admin=psycopg.connect(ADMIN,autocommit=True,row_factory=dict_row)
        cls.addClassCleanup(admin.close)
        if admin.execute('SELECT current_database() AS name').fetchone()['name']!=DB:
            raise RuntimeError('isolated_database_required')
        admin.execute('CREATE ROLE cctv_app LOGIN')
        for path in ('apps/be_worker/queue_schema.sql','apps/scene_description/schema.sql',
                     'apps/be_worker/report_schema.sql','apps/be_worker/flow_schema.sql','apps/rag_index/schema.sql'):
            admin.execute((ROOT/path).read_text())
        admin.execute('GRANT USAGE ON SCHEMA cctv_rag TO cctv_app; GRANT SELECT ON cctv_rag.chunks TO cctv_app')
        identifier='a'*32
        state={'schema_version':'be-runpod-v1','worker_job_id':identifier,'status':'handoff_ready'}
        admin.execute('INSERT INTO cctv_jobs.queue(worker_job_id,request_key,directory_id,state) VALUES(%s,%s,%s,%s)',
                      (identifier,'preserved-legacy',identifier,Jsonb(state)))
        admin.execute('INSERT INTO cctv_jobs.flows(worker_job_id,reference_date,status,finished_at) VALUES(%s,%s,%s,now())',
                      (identifier,'2026-09-30','completed'))
        aid='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        payload={'schema_version':'cctv-scene-v2','status':'completed','analysis_id':aid,'provider_job_id':identifier,
                 'event_id':'event_000','description':{'accident_suspicion':'uncertain','incident_types':['unknown']}}
        admin.execute('''INSERT INTO cctv_scene.descriptions(analysis_id,provider_job_id,event_id,model,result_sha256,
            payload,accident_suspicion,incident_types) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
            (aid,identifier,'event_000','gemini-existing','b'*64,Jsonb(payload),'uncertain',Jsonb(['unknown'])))
        admin.execute('INSERT INTO cctv_scene.report_runs VALUES(%s,%s,%s,%s,%s,now())',
                      ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',aid,identifier,Jsonb({'model':'existing'}),Jsonb({'status':'report_stored'})))
        vector='['+','.join(['1']+['0']*767)+']'
        for n in range(2):
            admin.execute('''INSERT INTO cctv_rag.chunks(chunk_id,embedding_profile,input_sha256,document_id,content,metadata,embedding)
                 VALUES(%s,%s,%s,%s,%s,%s,%s::vector)''',
                 (f'chunk-{n}','gemini-001-768-retrieval-v1',str(n)*64,'source',f'unchanged {n}',Jsonb({'source_url':'https://example.invalid/document'}),vector))
        tables=('cctv_jobs.queue','cctv_jobs.flows','cctv_scene.descriptions','cctv_scene.report_runs','cctv_rag.chunks')
        before={table:admin.execute(f'SELECT to_jsonb(t) AS value FROM {table} t').fetchall() for table in tables}
        for _ in range(2):admin.execute(selection_migration())
        after={table:admin.execute(f'SELECT to_jsonb(t) AS value FROM {table} t').fetchall() for table in tables}
        cls.migration_preserved=before==after
        cls.migration_counts={table:len(rows) for table,rows in after.items()}

    def setUp(self):
        self.admin=self.connection(admin=True)
        self.admin.execute('TRUNCATE cctv_jobs.analysis_selections,cctv_jobs.analysis_tasks,cctv_jobs.flows,cctv_jobs.queue RESTART IDENTITY CASCADE')
        self.conn=self.connection();self.store=Store(self.conn)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.spool=self.root/'spool'
        self.video=self.root/'input.mp4';self.video.write_bytes(b'\0\0\0\x18ftypisom-SYNTHETIC-P6-TEST')
        self.pod=FakePod();self.queue=job_queue.Coordinator(self.store,self.spool,self.pod.api,slots=1)
        self.patch=patch.object(flow_service,'spool',return_value=self.spool);self.patch.start();self.addCleanup(self.patch.stop)

    def connection(self,admin=False):
        conn=self.psycopg.connect(ADMIN if admin else APP,autocommit=True,row_factory=self.row_factory)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute('SELECT current_database() AS name').fetchone()['name'],DB)
        return conn

    def admit(self,selection='A',purpose='gpu_comparison',day='2026-10-01',store=None):
        return selection_jobs.admit(store or self.store,self.video,day,selection,purpose,
                                    spool=self.spool,config=CONFIG,jsonb=self.Jsonb)

    def counts(self):
        return tuple(self.conn.execute(f'SELECT count(*) AS count FROM cctv_jobs.{table}').fetchone()['count']
                     for table in ('queue','analysis_tasks','analysis_selections'))

    def tasks(self):return self.conn.execute('SELECT * FROM cctv_jobs.analysis_tasks ORDER BY created_at,version').fetchall()
    def rows(self):return self.store.rows()

    def set_status(self,row,status,**kwargs):
        state=worker.load_state(self.queue.directory(row))
        worker.update(self.queue.directory(row),state,status=status,**kwargs)
        self.store.write(row,state)

    def test_migration_twice_preserves_existing_five_tables_and_grants(self):
        self.assertTrue(self.migration_preserved)
        self.assertEqual(list(self.migration_counts.values()),[1,1,1,1,2])
        permissions=self.conn.execute('''SELECT has_table_privilege(current_user,'cctv_jobs.analysis_tasks','SELECT,INSERT,UPDATE') AS tasks,
            has_table_privilege(current_user,'cctv_jobs.analysis_selections','SELECT,INSERT') AS groups,
            has_table_privilege(current_user,'cctv_jobs.analysis_tasks','DELETE') AS deletion''').fetchone()
        self.assertEqual(permissions,{'tasks':True,'groups':True,'deletion':False})
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM cctv_rag.chunks').fetchone()['n'],2)

    def test_a_then_ab_reuses_gpu_and_task_double_clicks_are_idempotent(self):
        a=self.admit();both=self.admit('A+B');again=self.admit('A+B')
        self.assertEqual(self.counts(),(2,2,2))
        self.assertEqual(a['jobs'][0]['worker_job_id'],both['jobs'][0]['worker_job_id'])
        self.assertEqual(a['jobs'][0]['task_id'],both['jobs'][0]['task_id'])
        self.assertEqual(both['selection_id'],again['selection_id']);self.assertTrue(again['already_present'])
        self.assertEqual(self.pod.posts,[])
        self.assertEqual(len(list(self.spool.iterdir())),2)
        for task in self.tasks():selection_jobs.validate(task,next(r for r in self.rows() if r['worker_job_id']==task['worker_job_id']))

    def test_compare_then_report_reuses_gpu_and_freezes_generation_settings(self):
        compare=self.admit('A+B');report=self.admit('A+B','full_report')
        self.assertEqual(self.counts(),(2,4,2))
        self.assertEqual([x['worker_job_id'] for x in compare['jobs']],[x['worker_job_id'] for x in report['jobs']])
        snapshots=[t['generation_settings'] for t in self.tasks() if t['purpose']=='full_report']
        self.assertEqual([s['analysis_version'] for s in snapshots],['A','B'])
        self.assertTrue(all(s['scene_prompt_version']=='cctv-scene-ab-v1' for s in snapshots))
        self.assertEqual(self.pod.posts,[])
        repeated=self.admit('A+B','full_report');self.assertEqual(report['selection_id'],repeated['selection_id'])

    def test_report_changed_date_or_model_preserves_original_and_rejects_variant(self):
        self.admit('A','full_report');before=self.tasks();spool_before=sorted(p.as_posix() for p in self.spool.rglob('*'))
        with self.assertRaisesRegex(WorkerError,'full_report_settings_conflict_existing_preserved'):
            self.admit('A','full_report','2026-10-02')
        with patch.dict(os.environ,{generation_settings.SCENE_MODEL_ENV:'gemini-other'}):
            with self.assertRaisesRegex(WorkerError,'full_report_settings_conflict_existing_preserved'):
                self.admit('A','full_report')
        self.assertEqual(self.tasks(),before);self.assertEqual(self.counts(),(1,1,1))
        self.assertEqual(sorted(p.as_posix() for p in self.spool.rglob('*')),spool_before)

    def test_concurrent_admission_lock_blocks_duplicate_then_retry_reuses(self):
        entered=threading.Event();release=threading.Event();result=[];errors=[]
        other=Store(self.connection());original=other.insert
        def blocking(*args):
            entered.set();self.assertTrue(release.wait(5));return original(*args)
        def run():
            try:
                with patch.object(other,'insert',side_effect=blocking):result.append(self.admit('A+B',store=other))
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(WorkerError,'queue_worker_already_running'):self.admit('A+B')
        finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        same=self.admit('A+B');self.assertEqual(same['selection_id'],result[0]['selection_id'])
        self.assertEqual(self.counts(),(2,2,1))

    def test_b_insert_failure_rolls_back_whole_group_and_preserves_private_orphans(self):
        self.admin.execute('''CREATE FUNCTION cctv_jobs.reject_test_b() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.version='B' THEN RAISE EXCEPTION 'synthetic B insert failure'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER reject_test_b BEFORE INSERT ON cctv_jobs.analysis_tasks FOR EACH ROW EXECUTE FUNCTION cctv_jobs.reject_test_b();''')
        try:
            with self.assertRaises(self.psycopg.Error):self.admit('A+B')
        finally:self.admin.execute('DROP TRIGGER reject_test_b ON cctv_jobs.analysis_tasks; DROP FUNCTION cctv_jobs.reject_test_b()')
        self.assertEqual(self.counts(),(0,0,0));orphans=list(self.spool.iterdir());self.assertEqual(len(orphans),2)
        self.assertTrue(all(read_json(path/'state.json')['post_attempts']==0 for path in orphans))
        self.assertTrue(all(path.stat().st_mode&0o777==0o700 for path in orphans))
        self.admit('A+B');self.assertEqual(self.counts(),(2,2,1));self.assertEqual(len(list(self.spool.iterdir())),4)
        self.assertTrue(all(path.exists() for path in orphans));self.assertEqual(self.pod.posts,[])
        # Locks were released after rollback: another real connection can own both.
        with Store(self.connection()).lock('admission'),Store(self.connection()).lock('dispatch'):pass

    def test_dispatch_single_slot_and_new_coordinator_never_reposts_waiting(self):
        self.admit('A+B');self.queue.dispatch_tick()
        self.assertEqual(len(self.pod.posts),1);self.assertEqual([r['state']['status'] for r in self.rows()],['waiting','prepared'])
        restarted=job_queue.Coordinator(Store(self.connection()),self.spool,self.pod.api,slots=1)
        restarted.dispatch_tick();self.assertEqual(len(self.pod.posts),1)
        self.pod.jobs['0'*31+'1']['status']='succeeded';restarted.dispatch_tick()
        self.assertEqual(len(self.pod.posts),2);self.assertEqual([r['state']['status'] for r in self.rows()],['evidence_ready','waiting'])
        self.assertEqual([r['submit_attempts'] for r in self.rows()],[1,1])

    def test_unknown_submit_blocks_retry_other_version_and_resume_only_requests_reconciliation(self):
        self.admit('A+B');self.pod.error=ApiError('network_error')
        self.queue.dispatch_tick();self.assertEqual(len(self.pod.posts),1)
        restarted=job_queue.Coordinator(Store(self.connection()),self.spool,self.pod.api,slots=1)
        restarted.dispatch_tick();self.assertEqual(len(self.pod.posts),1)
        self.assertEqual([r['state']['status'] for r in self.rows()],['submission_unknown','prepared'])
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=lambda *a,**k:self.fail('no scene processing'))
        reply=selection_jobs.resume(self.conn,self.tasks()[0]['task_id'])
        self.assertEqual(reply['status'],'resume_requested');self.assertEqual(self.pod.lookups,0)
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=lambda *a,**k:self.fail('no scene'))
        self.assertEqual(self.pod.lookups,1);self.assertEqual(len(self.pod.posts),1)
        self.assertTrue(self.tasks()[0]['paused'])

    def test_unknown_ack_reconciles_saved_id_without_second_gpu_submission(self):
        self.admit();self.pod.error=ApiError('network_error');self.queue.dispatch_tick()
        row=self.rows()[0];saved_id='f'*32
        self.pod.jobs[saved_id]={'state':deepcopy(row['state']),'status':'succeeded'}
        self.pod.recovered_id=saved_id
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=lambda *a,**k:None)
        selection_jobs.resume(self.conn,self.tasks()[0]['task_id'])
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=lambda *a,**k:None)
        self.assertEqual(self.rows()[0]['state']['status'],'evidence_ready')
        self.assertEqual(self.rows()[0]['state']['provider_job_id'],saved_id)
        self.assertEqual(self.pod.lookups,1);self.assertEqual(len(self.pod.posts),1)
        self.assertEqual(self.rows()[0]['submit_attempts'],1)

    def test_failed_provider_resumes_only_saved_evidence_and_missing_analysis_stays_paused(self):
        self.admit();self.queue.dispatch_tick();identifier='0'*31+'1'
        self.pod.jobs[identifier]['status']='failed';self.queue.dispatch_tick()
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=lambda *a,**k:None)
        task=self.tasks()[0];selection_jobs.resume(self.conn,task['task_id'])
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=lambda *a,**k:None)
        self.assertTrue(self.tasks()[0]['paused']);self.assertEqual(self.pod.resumes,1)
        self.assertEqual(self.rows()[0]['state']['status'],'provider_failed')
        self.pod.analysis_complete=True
        selection_jobs.resume(self.conn,task['task_id'])
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=lambda *a,**k:None)
        self.assertEqual(self.rows()[0]['state']['status'],'waiting')
        self.assertEqual(self.pod.resumes,2);self.assertEqual(len(self.pod.posts),1)
        self.assertEqual(self.rows()[0]['submit_attempts'],1)

    def test_compare_processing_fetches_media_without_generation_and_preserves_other_failure(self):
        self.admit('A+B');a,b=self.rows()
        self.set_status(a,'handoff_ready',scene_events={'event_000':{'media_bytes_verified':True}});self.set_status(b,'provider_failed',error_code='test_provider_failed')
        self.admin.execute('UPDATE cctv_jobs.queue SET handoff=%s WHERE worker_job_id=%s',
                           (self.Jsonb({'scene_configs':['scene-event_000.json']}),a['worker_job_id']))
        calls=[]
        class Scenes:
            def fetch(inner,*args):calls.append('fetch')
            def describe(inner,*args,**kwargs):self.fail('comparison must not generate')
        class Reports:
            def run(inner,*args,**kwargs):self.fail('comparison must not report/embed')
        def process(queue,row,day,emit,**kwargs):
            return flow_service.process_events(queue,row,day,emit,scenes=Scenes(),reports=Reports(),**kwargs)
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=process)
        tasks=self.tasks();self.assertEqual([t['status'] for t in tasks],['comparison_completed','provider_failed'])
        self.assertIsNotNone(tasks[0]['finished_at']);self.assertFalse(tasks[0]['paused']);self.assertTrue(tasks[1]['paused'])
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=process)
        self.assertEqual(calls,['fetch']);self.assertEqual(self.pod.posts,[])

    def test_no_candidates_is_distinct_finished_state_and_failure_is_paused(self):
        self.admit('A+B');a,b=self.rows();self.set_status(a,'no_candidates');self.set_status(b,'verification_failed')
        selection_jobs.process(self.conn,self.queue,heartbeat=lambda stage:None,process_events=lambda *a,**k:self.fail('must not generate'))
        tasks=self.tasks();self.assertEqual([t['status'] for t in tasks],['no_candidates','verification_failed'])
        self.assertIsNotNone(tasks[0]['finished_at']);self.assertIsNone(tasks[1]['finished_at'])

    def test_task_receipt_tamper_pauses_before_dispatch(self):
        self.admit('A','full_report');row=self.rows()[0];task=self.tasks()[0]
        path=self.queue.directory(row)/('task-'+task['task_id']+'.json');data=read_json(path)
        data['generation_settings']['scene_model']='gemini-tampered';save(path,data)
        selection_jobs.prepare(self.conn,self.queue,{row['worker_job_id']:row})
        self.assertEqual(self.tasks()[0]['status'],'paused');self.assertTrue(self.tasks()[0]['paused'])
        auto=flow_service.AutomaticStore(self.conn);self.assertEqual(auto.rows(),[])
        job_queue.Coordinator(auto,self.spool,self.pod.api,slots=1).dispatch_tick();self.assertEqual(self.pod.posts,[])

    def test_changed_profile_or_settings_in_db_pauses(self):
        self.admit('A','full_report');row=self.rows()[0];task=self.tasks()[0]
        changed=deepcopy(task['generation_settings']);changed['scene_prompt_sha256']='0'*64
        self.admin.execute('UPDATE cctv_jobs.analysis_tasks SET generation_settings=%s WHERE task_id=%s',
                           (self.Jsonb(changed),task['task_id']))
        selection_jobs.prepare(self.conn,self.queue,{row['worker_job_id']:row})
        self.assertTrue(self.tasks()[0]['paused'])
        self.assertEqual(flow_service.AutomaticStore(self.conn).rows(),[])


if __name__=='__main__':
    import time
    started=time.monotonic();suite=unittest.defaultTestLoader.loadTestsFromTestCase(SelectionPostgresTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    receipt={'status':'passed' if result.wasSuccessful() else 'failed','tests_run':result.testsRun,
             'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
             'elapsed_seconds':round(time.monotonic()-started,6),'migration_preserved':getattr(SelectionPostgresTests,'migration_preserved',None),
             'migration_counts':getattr(SelectionPostgresTests,'migration_counts',{}),
             'external_requests':0,'gpu_calls':0,'gemini_calls':0,'embedding_calls':0}
    output=os.environ.get('CCTV_P6_TEST_RECEIPT')
    if output:Path(output).write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt));raise SystemExit(0 if result.wasSuccessful() and result.testsRun and not result.skipped else 1)
