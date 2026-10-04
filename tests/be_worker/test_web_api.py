"""Loopback HTTP admission integration with synthetic queue/DB; no cloud/model calls."""
from contextlib import contextmanager, nullcontext
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
from urllib.parse import urlencode
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).parent))
from test_queue import MemoryStore
from apps.be_worker import web_api as api, flow_service as f


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=MemoryStore();self.store.locks['admission']=threading.Lock()
        self.store.connection=Mock()
        self.store.connection.transaction.side_effect = nullcontext
        @contextmanager
        def connect():yield self.store.connection
        self.patches=[patch.object(api,'connect',connect),patch.object(api,'Store',lambda c:self.store),
            patch.object(f.trial_limits,'get',return_value=None),
            patch.object(f,'jsonb',lambda value:value),
            patch.object(f,'spool',lambda:Path(self.tmp.name)),
            patch.object(f,'config',lambda:{'runpod_url':'https://example.invalid','s3_bucket':'test-bucket','s3_region':'ap-northeast-2'})]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),api.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def submit(self,body):
        req=urllib.request.Request(self.base+'/jobs?reference_date=2026-09-30',data=body,headers={'Content-Type':'video/mp4'})
        with urllib.request.urlopen(req) as r:return json.load(r)
    def test_double_click_same_bytes_creates_one_job_without_gpu(self):
        with patch.object(f,'api_for',side_effect=AssertionError('GPU must not be called')):
            first=self.submit(b'0000ftyp-synthetic-video');second=self.submit(b'0000ftyp-synthetic-video')
        self.assertEqual(first['worker_job_id'],second['worker_job_id'])
        self.assertFalse(first['already_present']);self.assertTrue(second['already_present'])
        self.assertEqual(len(self.store.rows()),1)
        self.assertEqual(self.store.rows()[0]['submit_attempts'],0)
        self.assertEqual(list((Path(self.tmp.name)/'incoming').iterdir()),[])
    def test_concurrent_admission_has_at_most_one_job(self):
        answers=[]
        def submit():
            try:answers.append(self.submit(b'0000ftyp-same-video'))
            except urllib.error.HTTPError as e:answers.append({'status':e.code})
        threads=[threading.Thread(target=submit) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(self.store.rows()),1)
        self.assertTrue(all(a.get('status',202) in (202,409) for a in answers))
    def test_get_only_reads_view(self):
        with patch.object(f,'view',return_value=[]) as view,patch.object(f,'admit') as admit:
            with urllib.request.urlopen(self.base+'/jobs') as r:self.assertEqual(json.load(r),[])
            view.assert_called_once();admit.assert_not_called()
    def test_active_trial_blocks_legacy_admission_without_enqueuing(self):
        with patch.object(f.trial_limits, 'get', return_value={'active':True}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.submit(b'0000ftyp-synthetic-video')
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.store.rows(), [])

    def selection_request(self, query, body=b'0000ftyp-synthetic-video'):
        req=urllib.request.Request(self.base+'/selections?'+urlencode(query),data=body,
                                  headers={'Content-Type':'video/mp4'})
        with urllib.request.urlopen(req) as response:
            return response.status,json.load(response)

    def test_selection_post_passes_versions_purpose_and_ignores_comparison_date(self):
        received=[]
        def admit(store,video,reference_date,selection,purpose):
            received.append((video.read_bytes(),reference_date,selection,purpose))
            return {'selection_id':'d'*32,'already_present':False,'jobs':[],
                    'selection':selection,'purpose':purpose}
        with patch.object(f,'admit_selection',side_effect=admit,create=True), \
                patch.object(f,'api_for',side_effect=AssertionError('No GPU call from admission')):
            status,result=self.selection_request({'selection':'A+B','purpose':'gpu_comparison',
                                                  'reference_date':'not-used'})
        self.assertEqual(status,202)
        self.assertEqual(result['selection'],'A+B')
        self.assertEqual(received,[(b'0000ftyp-synthetic-video',None,'A+B','gpu_comparison')])
        self.assertEqual(list((Path(self.tmp.name)/'incoming').iterdir()),[])

    def test_full_report_post_passes_valid_reference_date(self):
        with patch.object(f,'admit_selection',return_value={'selection_id':'d'*32},create=True) as admit:
            self.selection_request({'selection':'B','purpose':'full_report','reference_date':'2026-10-02'})
        self.assertEqual(admit.call_args.args[2:],('2026-10-02','B','full_report'))

    def test_invalid_selection_purpose_date_or_duplicate_query_never_admits(self):
        with patch.object(f,'admit_selection',create=True) as admit:
            for query in ({'selection':'C','purpose':'gpu_comparison'},
                          {'selection':'A','purpose':'automatic'},
                          {'selection':'B','purpose':'full_report','reference_date':'bad'},
                          {'selection':'B','purpose':'full_report'}):
                with self.subTest(query=query),self.assertRaises(urllib.error.HTTPError):
                    self.selection_request(query)
            req=urllib.request.Request(self.base+'/selections?selection=A&selection=B&purpose=gpu_comparison',
                data=b'0000ftyp-synthetic',headers={'Content-Type':'video/mp4'})
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(req)
        admit.assert_not_called()

    def test_selection_lists_and_detail_are_read_only(self):
        value={'selection_id':'d'*32,'selection':'A+B','purpose':'gpu_comparison','jobs':[]}
        with patch.object(f,'selection_views',return_value=[value],create=True) as view, \
                patch.object(f,'admit_selection',create=True) as admit:
            with urllib.request.urlopen(self.base+'/selections') as response:
                self.assertEqual(json.load(response),[value])
            with urllib.request.urlopen(self.base+'/selections/'+'d'*32) as response:
                self.assertEqual(json.load(response),value)
        self.assertEqual(view.call_count,2)
        self.assertEqual(view.call_args.args[1],'d'*32)
        admit.assert_not_called()

    def test_task_resume_targets_exact_task_only(self):
        with patch.object(f,'resume_task',return_value={'status':'resume_requested'},create=True) as resume, \
                patch.object(f,'admit_selection',create=True) as admit:
            req=urllib.request.Request(self.base+'/tasks/'+'e'*32+'/resume',data=b'')
            with urllib.request.urlopen(req) as response:
                self.assertEqual(json.load(response),{'status':'resume_requested'})
        self.assertEqual(resume.call_args.args[1],'e'*32)
        admit.assert_not_called()

    def test_versioned_media_preserves_jpeg_and_preview_content_types(self):
        for kind,raw,mime in [('frame',b'\xff\xd8\xffsynthetic','image/jpeg'),
                              ('frame',b'\x89PNG\r\n\x1a\nsynthetic','image/png'),
                              ('clip_preview',b'0000ftyp-preview','video/mp4'),
                              ('roi_clip_preview',b'0000ftyp-preview','video/mp4')]:
            with self.subTest(kind=kind,mime=mime),patch.object(f,'media',return_value=raw) as media:
                with urllib.request.urlopen(self.base+'/jobs/'+'a'*32+'/media/event_000/'+kind) as response:
                    self.assertEqual(response.headers['Content-Type'],mime)
                    self.assertEqual(response.read(),raw)
                self.assertEqual(media.call_args.args[-1],kind)

    def test_whole_source_returns_exact_upload_without_advancing_or_provider_calls(self):
        raw = b'0000ftyp-synthetic-full-upload' + b'0' * (api.BLOCK_BYTES + 100)
        job = self.submit(raw)
        row = self.store.rows()[0]
        state_path = Path(self.tmp.name) / row['directory_id'] / 'state.json'
        before = state_path.read_bytes()
        with patch.object(f, 'api_for', side_effect=AssertionError('No provider read')), \
                patch.object(f.Coordinator, 'load', side_effect=AssertionError('No recovery/write')):
            with urllib.request.urlopen(self.base + '/jobs/' + job['worker_job_id'] + '/source') as response:
                self.assertEqual(response.headers['Content-Type'], 'video/mp4')
                self.assertEqual(response.headers['Content-Length'], str(len(raw)))
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(response.read(), raw)
        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(self.store.rows()[0]['submit_attempts'], 0)

    def test_missing_source_and_malformed_identifier_have_explicit_http_status(self):
        for identifier, expected_status, code in (
                ('a' * 32, 404, 'queue_job_not_found'), ('bad', 400, 'invalid_provider_job_id')):
            with self.subTest(identifier=identifier), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + '/jobs/' + identifier + '/source')
            self.assertEqual(caught.exception.code, expected_status)
            self.assertEqual(json.load(caught.exception), {'error': code})

    def test_source_missing_or_changed_does_not_fall_back_to_clip(self):
        job = self.submit(b'0000ftyp-synthetic-full-upload')
        row = self.store.rows()[0]
        source = Path(self.tmp.name) / row['directory_id'] / 'input.mp4'
        source.write_bytes(b'0000ftyp-synthetic-fake-upload')
        for remove, expected_status, code in (
                (False, 503, 'source_media_identity_mismatch'), (True, 404, 'source_media_not_found')):
            if remove:
                source.unlink()
            with self.subTest(remove=remove), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + '/jobs/' + job['worker_job_id'] + '/source')
            self.assertEqual(caught.exception.code, expected_status)
            self.assertEqual(json.load(caught.exception), {'error': code})

if __name__=='__main__':unittest.main()
