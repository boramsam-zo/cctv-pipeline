"""Read-only MLflow page interactions; all replies are local fixtures."""
from pathlib import Path
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT/'apps/cctv_ui/demo_mlflow.py'
RID = 'a' * 32
TRACE = 'tr-' + 'b' * 32


class Replies:
    def __init__(self): self.calls=[]; self.scope='inference_only'
    def __call__(self,path,data=None,**kwargs):
        if data is not None: raise AssertionError('UI must never write')
        self.calls.append(path)
        run={'run_id': RID, 'name':'X3D-S 보고 실행', 'model_id':'x3d_s', 'model_version':'epoch7',
            'worker_job_id':'c'*32, 'status':'FINISHED', 'outcome':'succeeded',
            'evaluation_scope':self.scope, 'metrics':{'gpu_seconds':9.1}, 'trace_ids':[TRACE],
            'params':{'input_sha256':'d'*64}, 'tags':{'cctv.new_model_calls':'1'},
            'artifacts':[{'path':'content-enrichment/request.json', 'bytes':100, 'readable':True}]}
        if path=='/mlflow/models':
            return {'models':[{'model_id':'x3d_s','model_version':'epoch7','name':'cctv-x3d-s','version':'1','status':'READY'}]}
        if path.startswith('/mlflow/runs?') or path=='/mlflow/runs': return {'runs':[run]}
        if path=='/mlflow/runs/'+RID:return run
        if '/artifact?' in path: return {'content':'과거 자연어 요청 본문', 'format':'text'}
        if path.startswith('/mlflow/traces/'):
            self.assert_run_query(path)
            return {'spans':[{'name':'gemini_video_observation','kind':'LLM','status':'OK','duration_seconds':3.8,
                'token_usage':{'input_tokens':6630,'output_tokens':814,'total_tokens':7444},
                'inputs':{'request':{'messages':[{'role':'user','content':[{'text':'영상 속 차량의 움직임을 설명해줘'}]}]}},
                'outputs':{'response':{'candidates':[{'message':{'content':[{'text':'차량 두 대의 접촉이 관찰됩니다.'}]}}]}}},
                {'name':'rag_search_relevance_template','kind':'RETRIEVER','status':'OK','duration_seconds':9.6,
                 'inputs':{},'outputs':{'queries':[{'query':'차량 접촉 현장 대응'}],
                    'sources':[{'title':'교통사고 대응','excerpt':'현장 안전을 확인한다'}],
                    'report_markdown':'# 사고 보고\n저장된 여섯 절 보고'}}]}
        raise AssertionError(path)
    def assert_run_query(self,path):
        if parse_qs(urlsplit(path).query).get('run_id') != [RID]:raise AssertionError('run membership missing')


class DemoMLflowTests(unittest.TestCase):
    def setUp(self): st.cache_data.clear()
    def test_actual_prompt_response_tokens_and_rag_visible_without_calls(self):
        replies=Replies()
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
            self.assertFalse(app.exception)
            self.assertTrue(any('차량의 움직임' in x.value for x in app.code))
            self.assertTrue(any('접촉이 관찰' in x.value for x in app.code))
            self.assertTrue(any(7444 in x.value.to_numpy() for x in app.dataframe))
            app.selectbox(key='demo_mlflow_span').select(1).run()
            self.assertFalse(app.exception)
            self.assertTrue(any('여섯 절 보고' in x.value for x in app.code))
            self.assertTrue(any('차량 접촉 현장 대응' in json.dumps(json.loads(x.value), ensure_ascii=False) for x in app.json))
        self.assertFalse(any('/artifact?' in path for path in replies.calls))
    def test_artifact_is_loaded_only_after_select_and_models_have_versions(self):
        replies=Replies()
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
            app.selectbox(key='demo_mlflow_artifact').select('content-enrichment/request.json').run()
            self.assertFalse(app.exception)
            self.assertTrue(any('과거 자연어' in x.value for x in app.code))
            app.segmented_control(key='demo_mlflow_view').set_value('등록 모델').run()
            self.assertFalse(app.exception)
            self.assertEqual(app.dataframe[0].value.iloc[0]['Registry 버전'],'1')
    def test_development_metrics_are_not_presented_as_independent_final_accuracy(self):
        replies=Replies(); replies.scope='development_validation'
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        self.assertTrue(any('독립 최종평가' in x.value for x in app.warning))
    def test_unavailable_tracker_shows_safe_notice(self):
        with patch('apps.cctv_ui.client.request',side_effect=RuntimeError('secret internal URL')):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        self.assertTrue(any('현재 조회할 수 없습니다' in x.value for x in app.warning))
        self.assertFalse(any('secret' in x.value for x in app.warning))


if __name__=='__main__':unittest.main()
