"""Saved real case through additive DB views and API handlers; no DB/HTTP/models."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import uuid

from apps.be_worker import flow_service, team_jobs, web_api
from apps.be_worker.common import WorkerError
from apps.be_worker.report_jobs import fingerprint
from apps.scene_description import team_scene
from apps.team_rag.control_rag import markdown

ROOT = Path(__file__).resolve().parents[2]
REPLAY = ROOT/'artifacts/be-worker/teammate-rag-integration-20261003-01/offline-replay-01'
WORKER = 'a'*32


def actual_records():
    scene = json.loads((REPLAY/'scene/result.json').read_bytes())
    result = {'pipeline_id':'team-rag-v1',
              'report_json':json.loads((REPLAY/'report/report.json').read_bytes()),
              'report_markdown':(REPLAY/'report/report.md').read_text()}
    report_id = str(uuid.uuid5(uuid.NAMESPACE_URL,team_jobs.VERSION+'/'+scene['analysis_id']))
    report = team_jobs.envelope(result,scene,report_id,WORKER)
    descriptions = [{'analysis_id':scene['analysis_id'],'event_id':scene['event_id'],
                     'payload':scene,'result_sha256':team_scene.validate_result(scene)}]
    reports = [{'report_id':report_id,'settings':{'analysis_id':scene['analysis_id']},
                'state':{'status':'report_stored','result':report,'result_sha256':fingerprint(report)}}]
    state = {'provider_job_id':scene['provider_job_id'],'input_sha256':scene['input_sha256'],
             'status':'handoff_ready','gemini_api_calls':1,
             'scene_events':{'event_000':{'status':'description_stored','media_bytes_verified':True}},
             'analysis_profile':scene['source']['analysis_profile'],'gpu_key':scene['source']['gpu_key']}
    row = {'worker_job_id':WORKER,'directory_id':WORKER,'state':state,
           'created_at':'2026-10-03T00:00:00+00:00','updated_at':'2026-10-03T00:00:00+00:00'}
    return row,descriptions,reports


class ReadOnlyReplies:
    def __init__(self, row, descriptions=None, reports=None, *, team_exists=True,
                 legacy_descriptions=None, legacy_reports=None):
        self.row=row
        self.descriptions=descriptions or []
        self.reports=reports or []
        self.legacy_descriptions=legacy_descriptions or []
        self.legacy_reports=legacy_reports or []
        self.team_exists=team_exists
        self.queries=[]

    def execute(self, sql, args=()):
        self.queries.append((sql,args))
        if not sql.startswith('SELECT'):
            raise AssertionError('Read-only view unexpectedly wrote SQL')
        cursor=Mock()
        if 'to_regclass(' in sql:
            cursor.fetchone.return_value={'ok':self.team_exists}
        elif 'cctv_jobs.flows' in sql:
            cursor.fetchone.return_value=None
        elif 'cctv_scene.team_descriptions' in sql:
            if args != (self.row['state']['provider_job_id'],):
                raise AssertionError('Wrong provider scene selected')
            cursor.fetchall.return_value=deepcopy(self.descriptions)
        elif 'cctv_scene.team_report_runs' in sql:
            if args != (self.row['worker_job_id'],):
                raise AssertionError('Wrong worker report selected')
            cursor.fetchall.return_value=deepcopy(self.reports)
        elif 'cctv_scene.descriptions' in sql:
            cursor.fetchall.return_value=deepcopy(self.legacy_descriptions)
        elif 'cctv_scene.report_runs' in sql:
            cursor.fetchall.return_value=deepcopy(self.legacy_reports)
        else:
            raise AssertionError('Unexpected query: '+sql)
        return cursor


def read_view(conn):
    store=Mock();store.rows.return_value=[deepcopy(conn.row)]
    with patch.object(flow_service,'Store',return_value=store), \
            patch.object(flow_service,'api_for',side_effect=AssertionError('Runpod forbidden')):
        return flow_service.view(conn,conn.row['worker_job_id'])


class TeamViewTests(unittest.TestCase):
    def test_new_tables_saved_scene_report_and_wire_identity_validate(self):
        row,descriptions,reports=actual_records()
        conn=ReadOnlyReplies(row,descriptions,reports)
        answer=read_view(conn)
        wire=json.loads(json.dumps(answer,default=str))
        self.assertEqual(len(conn.queries),6)
        self.assertEqual(wire[0]['descriptions'],descriptions)
        self.assertEqual(wire[0]['reports'],reports)
        self.assertEqual(wire[0]['stage'],'completed')
        result=wire[0]['reports'][0]['state']['result']
        self.assertEqual(result['report_json']['input'],wire[0]['descriptions'][0]['payload']['team_event'])
        self.assertEqual(result['provider_job_id'],row['state']['provider_job_id'])
        self.assertEqual(result['worker_job_id'],row['worker_job_id'])
        self.assertEqual(result['analysis_id'],descriptions[0]['analysis_id'])
        self.assertEqual(result['scene_sha256'],descriptions[0]['result_sha256'])

    def test_legacy_tables_still_load_when_new_tables_are_absent(self):
        row,_,_=actual_records()
        legacy_descriptions=[{'analysis_id':'legacy-scene','event_id':'event_000',
                              'payload':{'description':{'summary':'보존할 이전 관측'}}}]
        legacy_reports=[{'report_id':'legacy-report','settings':{'analysis_id':'legacy-scene'},
                         'state':{'status':'report_stored','result':{'reference_date':'2026-10-02'}}}]
        conn=ReadOnlyReplies(row,team_exists=False,legacy_descriptions=legacy_descriptions,
                            legacy_reports=legacy_reports)
        with patch.object(team_scene,'validate_result',side_effect=AssertionError('Legacy must not be converted')):
            answer=read_view(conn)
        self.assertEqual(len(conn.queries),4)
        self.assertEqual(answer[0]['descriptions'],legacy_descriptions)
        self.assertEqual(answer[0]['reports'],legacy_reports)

    def test_new_and_legacy_reports_merge_without_rewriting_legacy_date(self):
        row,descriptions,reports=actual_records()
        old_scene={'analysis_id':'legacy-scene','event_id':'event_000','payload':{'description':{}}}
        old_report={'report_id':'legacy-report','settings':{'analysis_id':'legacy-scene'},
                    'state':{'status':'report_stored','result':{'reference_date':'2026-10-02'}}}
        original=deepcopy((old_scene,old_report))
        conn=ReadOnlyReplies(row,descriptions,reports,legacy_descriptions=[old_scene],legacy_reports=[old_report])
        answer=read_view(conn)[0]
        self.assertEqual(answer['descriptions'],[old_scene,*descriptions])
        self.assertEqual(answer['reports'],[old_report,*reports])
        self.assertEqual((old_scene,old_report),original)

    def test_changed_scene_row_hash_blocks_readback(self):
        row,descriptions,reports=actual_records()
        descriptions[0]['result_sha256']='0'*64
        with self.assertRaises(WorkerError) as caught:
            read_view(ReadOnlyReplies(row,descriptions,reports))
        self.assertEqual(caught.exception.code,'team_view_scene_hash_changed')

    def test_wrong_report_identity_or_state_hash_blocks_readback(self):
        row,descriptions,reports=actual_records()
        for edit in (lambda value:value.update(provider_job_id='b'*32),
                     lambda value:value.update(worker_job_id='b'*32),
                     lambda value:value.update(report_id=str(uuid.uuid4())),
                     lambda value:value.update(analysis_id=str(uuid.uuid4())),
                     lambda value:value.update(event_id='event_999'),
                     lambda value:value.update(scene_sha256='0'*64)):
            changed=deepcopy(reports)
            edit(changed[0]['state']['result'])
            changed[0]['state']['result_sha256']=fingerprint(changed[0]['state']['result'])
            with self.subTest(edit=edit),self.assertRaises(WorkerError) as caught:
                read_view(ReadOnlyReplies(row,descriptions,changed))
            self.assertEqual(caught.exception.code,'team_view_report_identity_changed')
        reports[0]['state']['result_sha256']='0'*64
        with self.assertRaises(WorkerError) as caught:
            read_view(ReadOnlyReplies(row,descriptions,reports))
        self.assertEqual(caught.exception.code,'team_view_report_identity_changed')

    def test_changed_document_content_rejected_even_with_new_payload_hash(self):
        row,descriptions,reports=actual_records()
        result=reports[0]['state']['result']
        result['report_json']['sources'][0]['content']='동봉 문서와 다른 내용'
        result['report_json']['references'][0]['content']='동봉 문서와 다른 내용'
        result['report_markdown']=markdown(result['report_json'])
        reports[0]['state']['result_sha256']=fingerprint(result)
        with self.assertRaises(WorkerError) as caught:
            read_view(ReadOnlyReplies(row,descriptions,reports))
        self.assertEqual(caught.exception.code,'team_report_document_reference_mismatch')

    def test_api_get_handler_returns_actual_view_without_http_or_model_calls(self):
        row,descriptions,reports=actual_records()
        conn=ReadOnlyReplies(row,descriptions,reports)
        @contextmanager
        def connect():yield conn
        handler=object.__new__(web_api.Handler)
        handler.path='/jobs/'+row['worker_job_id']
        handler.send=Mock()
        store=Mock();store.rows.return_value=[row]
        with patch.object(web_api,'connect',connect),patch.object(flow_service,'Store',return_value=store), \
                patch.object(flow_service,'api_for',side_effect=AssertionError('Provider forbidden')):
            handler.do_GET()
        handler.send.assert_called_once()
        answer=handler.send.call_args.args[0]
        self.assertEqual(answer['reports'],reports)
        self.assertEqual(answer['descriptions'],descriptions)
        self.assertEqual(len(conn.queries),6)

    def test_pipeline_metadata_handler_does_not_touch_provider_or_database(self):
        handler=object.__new__(web_api.Handler)
        handler.path='/report-pipeline'
        handler.send=Mock()
        with patch.dict(os.environ,{'BE_REPORT_PIPELINE':'team-rag-v1'}), \
                patch.object(web_api,'connect',side_effect=AssertionError('Metadata must not query DB')), \
                patch.object(flow_service,'api_for',side_effect=AssertionError('Metadata must not call provider')):
            handler.do_GET()
        handler.send.assert_called_once_with({
            'pipeline_id':'team-rag-v1','document_reference_date_supported':False})


if __name__=='__main__':
    unittest.main()
