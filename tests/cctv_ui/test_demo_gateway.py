"""Typed route and upload gateway checks without sockets or an upstream server."""
from io import BytesIO
import json
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from apps.cctv_ui.demo_gateway import Handler, projection, route
from apps.be_worker.demo_read import read_mlflow
from apps.be_worker.common import WorkerError


class Reply(BytesIO):
    status=202
    def __enter__(self):return self
    def __exit__(self,*args):pass


class Headers(dict):
    def get_all(self,key,default=None):
        if key not in self:return default
        value=self[key]
        return value if isinstance(value,list) else [value]


class DemoGatewayTests(unittest.TestCase):
    def handler(self,path,body=b'mp4-bytes',window=None):
        value=Handler.__new__(Handler)
        value.path=path;value.command='POST';value.requestline='POST '+path+' HTTP/1.1'
        value.headers=Headers({'Content-Type':'video/mp4','Content-Length':str(len(body))})
        value.rfile=BytesIO(body);value.answers=[];value.calls=[]
        value.window=lambda: window or {'active':True,'max_upload_bytes':64}
        def open(request,**kwargs):
            value.calls.append(request)
            return Reply(json.dumps({'selection_id':'a'*32,'result_reused':True,'new_gpu_calls':0}).encode())
        value.server=NS(upstream='http://private-api:8080',admission=threading.Lock(),opener=NS(open=open))
        value.respond=lambda data,status=200,**kwargs:value.answers.append((status,data))
        value.send_error_json=lambda status,code:value.answers.append((status,{'error':code}))
        return value

    def test_typed_routes_reject_mutations_model_expansion_and_proxy_urls(self):
        self.assertEqual(route('/selections?selection=B&purpose=full_report','POST'),'/demo/selections?selection=B&purpose=full_report')
        self.assertEqual(route('/mlflow/traces/tr-'+'b'*32+'?run_id='+'a'*32),'/demo/mlflow/traces/tr-'+'b'*32+'?run_id='+'a'*32)
        cases=[('/selections?selection=A%2BB&purpose=full_report','POST'),
            ('/selections?selection=B&purpose=gpu_comparison','POST'),
            ('/selections?selection=B&selection=R2&purpose=full_report','POST'),
            ('/selections?selection=B&purpose=full_report&reference_date=2026-10-06','POST'),
            ('/tasks/'+'a'*32+'/resume','POST'),('/mlflow/runs/delete','POST'),
            ('http://private-api/jobs','GET'),('//private-api/jobs','GET'),('/jobs/%2e%2e/secrets','GET'),
            ('/mlflow/runs?filter=x','GET'),('/jobs?extra=x','GET')]
        for path,method in cases:
            with self.subTest(path=path):self.assertIsNone(route(path,method))

    def test_expired_upload_is_rejected_before_payload_or_upstream(self):
        value=self.handler('/selections?selection=B&purpose=full_report',window={'active':False})
        value.do_POST()
        self.assertEqual(value.answers[0][0],410)
        self.assertEqual(value.rfile.tell(),0);self.assertFalse(value.calls)

    def test_bad_upload_shape_and_size_rejected_before_upstream(self):
        for headers in ({'Content-Type':'application/octet-stream'}, {'Content-Length':'65'},
                        {'Content-Length':'0'},{'Transfer-Encoding':'chunked'}):
            value=self.handler('/selections?selection=B&purpose=full_report')
            value.headers.update(headers);value.do_POST()
            self.assertIn(value.answers[0][0],(403,413));self.assertFalse(value.calls)

    def test_upload_only_forwards_once_and_preserves_reuse_receipt(self):
        value=self.handler('/selections?selection=R2&purpose=full_report')
        value.do_POST()
        self.assertEqual(len(value.calls),1)
        self.assertEqual(value.calls[0].full_url,'http://private-api:8080/demo/selections?selection=R2&purpose=full_report')
        self.assertTrue(value.answers[0][1]['result_reused'])
        self.assertEqual(value.answers[0][1]['new_gpu_calls'],0)

    def test_lastconv_upload_requires_explicit_session_selection_before_payload_read(self):
        for allowed in (None, ['B', 'R2'], ['B', 'R2', 'LC']):
            with self.subTest(allowed=allowed):
                window={'active':True,'max_upload_bytes':64}
                if allowed is not None:window['allowed_selections']=allowed
                value=self.handler('/selections?selection=LC&purpose=full_report',window=window)
                value.do_POST()
                if allowed is not None and 'LC' in allowed:
                    self.assertEqual(len(value.calls),1)
                    self.assertEqual(value.calls[0].full_url,
                        'http://private-api:8080/demo/selections?selection=LC&purpose=full_report')
                else:
                    self.assertEqual(value.answers,[(403,{'error':'demo_model_not_allowed'})])
                    self.assertEqual(value.rfile.tell(),0);self.assertFalse(value.calls)

    def test_wire_alias_get_body_and_duplicate_length_rejected(self):
        for target, headers in (('//jobs', {}), ('/jobs', {'Content-Length':'1'}),
                                ('/jobs', {'Transfer-Encoding':'chunked'}),
                                ('/jobs', {'Content-Length':['0','0']})):
            value=self.handler('/jobs')
            value.command='GET';value.requestline='GET '+target+' HTTP/1.1'
            value.headers=Headers(headers)
            value.do_GET()
            self.assertEqual(value.answers,[(403,{'error':'demo_route_not_allowed'})])
            self.assertFalse(value.calls)

    def test_upstream_error_text_is_not_exported(self):
        value=self.handler('/selections?selection=B&purpose=full_report')
        def fail(*args,**kwargs):raise HTTPError('http://private',409,'private',{},Reply(b'{"error":"http://private?token=secret"}'))
        value.server.opener.open=fail;value.do_POST()
        self.assertEqual(value.answers,[(409,{'error':'demo_submission_failed'})])

    def test_read_projection_has_only_selected_connection_fields(self):
        value=projection('/connection',{'connected':True,'api_token':'secret','base_url':'http://private','remaining_jobs':2})
        self.assertEqual(value,{'connected':True,'remaining_jobs':2})
        job={'worker_job_id':'a'*32,'evidence_events':[{'event_id':'event_000'},{'event_id':'event_001'}],
             'descriptions':[{'event_id':'event_000'}],'config':{'token':'secret'},'generation_settings':{'token':'secret'}}
        answer=projection('/jobs',[job])[0]
        self.assertEqual(answer['demo_summary']['unexplained_candidates'],1)
        self.assertNotIn('secret',json.dumps(answer))

    def test_mlflow_dispatch_requires_trace_membership_and_exact_query(self):
        reader=NS(trace=lambda *args,**kwargs:(args,kwargs))
        path='/demo/mlflow/traces/tr-'+'b'*32
        self.assertEqual(read_mlflow(path+'?run_id='+'a'*32,reader)[1],{'run_id':'a'*32})
        for query in ('','?run_id=bad','?run_id='+'a'*32+'&run_id='+'a'*32,'?run_id='+'a'*32+'&url=http://private'):
            with self.subTest(query=query),self.assertRaises(WorkerError):read_mlflow(path+query,reader)
