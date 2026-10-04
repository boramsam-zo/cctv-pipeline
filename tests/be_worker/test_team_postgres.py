"""User-run disposable PostgreSQL checks; no model, provider or production DSN.

The runner must create an empty cctv_team_isolated_test database in a container
with trust authentication and network isolated from production. This module
never reads secret files or environment connection URLs.
"""
from copy import deepcopy
import os
from pathlib import Path
import unittest
import uuid

from apps.be_worker import flow_service, team_jobs
from apps.scene_description import scene, team_scene, team_db

ROOT=Path(__file__).resolve().parents[2]
DB='cctv_team_isolated_test'
ADMIN='postgresql://postgres@127.0.0.1:5432/'+DB
APP='postgresql://cctv_app@127.0.0.1:5432/'+DB
REPLAY=ROOT/'artifacts/be-worker/teammate-rag-integration-20261003-01/offline-replay-01'
LEGACY_JOB='a'*32
TEAM_JOB='d'*32


@unittest.skipUnless(os.environ.get('CCTV_TEAM_ISOLATED_PG')=='1',
                     'User-run disposable PostgreSQL runner required')
class TeamPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
        cls.psycopg=psycopg;cls.row_factory=staticmethod(dict_row);cls.Jsonb=Jsonb
        cls.admin=psycopg.connect(ADMIN,autocommit=True,row_factory=dict_row)
        cls.addClassCleanup(cls.admin.close)
        identity=cls.admin.execute('SELECT current_database() AS name,inet_server_addr() AS address').fetchone()
        if identity['name']!=DB or str(identity['address']) not in ('127.0.0.1','::1'):
            raise RuntimeError('fixed_isolated_local_database_required')
        namespaces=cls.admin.execute("SELECT count(*) AS n FROM pg_namespace WHERE nspname IN ('cctv_jobs','cctv_scene')").fetchone()
        if namespaces['n']:
            raise RuntimeError('empty_disposable_database_required_existing_data_preserved')
        if cls.admin.execute("SELECT 1 FROM pg_roles WHERE rolname='cctv_app'").fetchone():
            raise RuntimeError('fresh_isolated_cluster_required_existing_role_preserved')
        cls.admin.execute('CREATE ROLE cctv_app LOGIN')
        for name in ('apps/be_worker/queue_schema.sql','apps/scene_description/schema.sql',
                     'apps/be_worker/report_schema.sql','apps/be_worker/flow_schema.sql',
                     'apps/be_worker/selection_schema.sql'):
            cls.admin.execute((ROOT/name).read_text())
        cls.scene_result=scene.parse((REPLAY/'scene/result.json').read_bytes())
        cls.report_result=dict(pipeline_id='team-rag-v1',
            report_json=scene.parse((REPLAY/'report/report.json').read_bytes()),
            report_markdown=(REPLAY/'report/report.md').read_text())
        for wid,provider,digest in ((LEGACY_JOB,'b'*32,'c'*64),
                                   (TEAM_JOB,cls.scene_result['provider_job_id'],cls.scene_result['input_sha256'])):
            state=dict(schema_version='be-runpod-v1',worker_job_id=wid,status='handoff_ready',
                       provider_job_id=provider,input_sha256=digest)
            cls.admin.execute('''INSERT INTO cctv_jobs.queue
                (worker_job_id,request_key,directory_id,state) VALUES(%s,%s,%s,%s)''',
                (wid,'isolated-'+wid,wid,Jsonb(state)))
        old_analysis='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        old_payload=dict(schema_version='cctv-scene-v2',status='completed',analysis_id=old_analysis,
            provider_job_id='b'*32,event_id='event_000',description=dict(
                accident_suspicion='uncertain',incident_types=['unknown']))
        cls.admin.execute('''INSERT INTO cctv_scene.descriptions
            (analysis_id,provider_job_id,event_id,model,result_sha256,payload,accident_suspicion,incident_types)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(old_analysis,'b'*32,'event_000',
            'gemini-existing','1'*64,Jsonb(old_payload),'uncertain',Jsonb(['unknown'])))
        cls.admin.execute('''INSERT INTO cctv_scene.report_runs
            (report_id,analysis_id,worker_job_id,settings,state) VALUES(%s,%s,%s,%s,%s)''',
            ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',old_analysis,LEGACY_JOB,
             Jsonb({'reference_date':'2026-09-30'}),Jsonb({'status':'report_stored'})))
        cls.admin.execute('''INSERT INTO cctv_jobs.analysis_tasks
            (task_id,worker_job_id,purpose,version,gpu_key,reference_date,generation_settings)
            VALUES(%s,%s,'full_report','B',%s,%s,%s)''',
            ('0'*32,LEGACY_JOB,'legacy-key','2026-09-30',Jsonb({'schema_version':'be-generation-settings-ab-v1'})))
        cls.legacy_before=cls.legacy_receipt()
        for _ in range(2):
            cls.admin.execute((ROOT/'apps/scene_description/team_schema.sql').read_text())
            cls.admin.execute((ROOT/'apps/be_worker/team_schema.sql').read_text())
        cls.legacy_after=cls.legacy_receipt()

    @classmethod
    def legacy_receipt(cls):
        return {table:cls.admin.execute(f'SELECT to_jsonb(t) AS value FROM {table} t ORDER BY analysis_id').fetchall()
                for table in ('cctv_scene.descriptions','cctv_scene.report_runs')}

    def connection(self):
        conn=self.psycopg.connect(APP,autocommit=True,row_factory=self.row_factory)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute('SELECT current_database() AS name').fetchone()['name'],DB)
        return conn

    def test_schema_twice_preserves_legacy_rows_and_date(self):
        self.assertEqual(self.legacy_before,self.legacy_after)
        row=self.admin.execute('SELECT reference_date,generation_settings FROM cctv_jobs.analysis_tasks WHERE task_id=%s',('0'*32,)).fetchone()
        self.assertEqual(row['reference_date'].isoformat(),'2026-09-30')
        self.assertEqual(row['generation_settings'],{'schema_version':'be-generation-settings-ab-v1'})
        permissions=self.connection().execute("""SELECT
            has_table_privilege(current_user,'cctv_scene.team_descriptions','SELECT,INSERT') AS scene,
            has_table_privilege(current_user,'cctv_scene.team_descriptions','UPDATE,DELETE') AS scene_mutation,
            has_table_privilege(current_user,'cctv_scene.team_report_runs','SELECT,INSERT,UPDATE') AS report""").fetchone()
        self.assertEqual(permissions,{'scene':True,'scene_mutation':False,'report':True})

    def test_team_date_none_allowed_and_legacy_date_required(self):
        conn=self.connection()
        cases=[('gpu_comparison','A',None,None,True),
               ('gpu_comparison','B',{'schema_version':'be-generation-settings-team-rag-v1'},None,False),
               ('full_report','B',{'schema_version':'be-generation-settings-team-rag-v1'},None,True),
               ('full_report','A',{'schema_version':'be-generation-settings-team-rag-v1'},None,False),
               ('full_report','B',{'schema_version':'be-generation-settings-team-rag-v1'},'2026-10-03',False),
               ('full_report','B',{'schema_version':'be-generation-settings-ab-v1'},None,False),
               ('full_report','B',{'schema_version':'be-generation-settings-ab-v1'},'2026-10-03',True)]
        # A transaction is rolled back for each case, leaving seed rows intact.
        for index,(purpose,version,settings,day,allowed) in enumerate(cases):
            with self.subTest(index=index):
                try:
                    with conn.transaction(force_rollback=True):
                        conn.execute('''INSERT INTO cctv_jobs.analysis_tasks
                            (task_id,worker_job_id,purpose,version,gpu_key,reference_date,generation_settings)
                            VALUES(%s,%s,%s,%s,%s,%s,%s)''',
                            (f'{index+1:032x}',TEAM_JOB,purpose,version,'new-key',day,
                             self.Jsonb(settings) if settings is not None else None))
                    if not allowed:self.fail('invalid date/purpose contract accepted')
                except self.psycopg.errors.CheckViolation:
                    if allowed:raise

    def test_team_store_once_conflict_preserves_raw_facts(self):
        conn=self.connection();result=deepcopy(self.scene_result)
        # Give this storage case its own row so test order cannot change whether
        # the first INSERT is new. The captured observation stays byte-for-byte
        # equal to the saved scene; only its envelope storage ID differs.
        result['analysis_id']='cccccccc-cccc-cccc-cccc-cccccccccccc'
        with conn.transaction(force_rollback=True):
            first=team_db.store(conn,result);second=team_db.store(conn,result)
            self.assertTrue(first['inserted']);self.assertTrue(second['already_present'])
            self.assertEqual(team_db.show(conn,result['analysis_id']),result)
            changed=deepcopy(result);changed['elapsed_seconds']+=1
            with self.assertRaisesRegex(ValueError,'conflict'):
                team_db.store(conn,changed)
            self.assertEqual(team_db.show(conn,result['analysis_id']),result)
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute('DELETE FROM cctv_scene.team_descriptions')
        self.assertEqual(self.legacy_receipt(),self.legacy_after)

    def test_report_foreign_keys_and_api_readback_same_event(self):
        conn=self.connection();result=deepcopy(self.scene_result)
        team_db.store(conn,result)
        report_id=str(uuid.uuid5(uuid.NAMESPACE_URL,team_jobs.VERSION+'/'+result['analysis_id']))
        payload=team_jobs.envelope(self.report_result,result,report_id,TEAM_JOB)
        settings=dict(analysis_id=result['analysis_id'],reference_date=None,report_id=report_id)
        state=dict(status='report_stored',result=payload,result_sha256=team_jobs.fingerprint(payload))
        with self.assertRaises(self.psycopg.errors.ForeignKeyViolation):
            conn.execute('''INSERT INTO cctv_scene.team_report_runs
                (report_id,analysis_id,worker_job_id,settings,state) VALUES(%s,%s,%s,%s,%s)''',
                (str(uuid.uuid4()),str(uuid.uuid4()),TEAM_JOB,self.Jsonb(settings),self.Jsonb(state)))
        conn.execute('''INSERT INTO cctv_scene.team_report_runs
            (report_id,analysis_id,worker_job_id,settings,state) VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(report_id) DO NOTHING''',
            (report_id,result['analysis_id'],TEAM_JOB,self.Jsonb(settings),self.Jsonb(state)))
        # Shared GET-view function performs actual DB readback; no HTTP/model or
        # media retrieval. Its minimal queue fixture omits preview/GPU metrics.
        view=flow_service.view(conn,TEAM_JOB)[0]
        self.assertEqual(view['stage'],'completed')
        shown_scene=view['descriptions'][0]['payload']
        shown_report=view['reports'][0]['state']['result']
        self.assertEqual(shown_scene['analysis_id'],shown_report['analysis_id'])
        self.assertEqual(shown_report['report_json']['input'],shown_scene['team_event'])
        self.assertEqual(shown_report['report_markdown'],self.report_result['report_markdown'])
        self.assertEqual(shown_report['provider_job_id'],view['provider_job_id'])
        self.assertEqual(self.legacy_receipt(),self.legacy_after)


if __name__=='__main__':unittest.main()
