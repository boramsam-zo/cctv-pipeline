"""ASGI expiry checks, including sockets accepted before the window closes."""
import asyncio
from io import BytesIO
import json
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from urllib.error import URLError

from apps.cctv_ui.demo_server import DemoBoundary, Gate, allowed


def scope(path='/', method='GET', kind='http'):
    return {'type':kind,'path':path,'raw_path':path.encode(),'method':method,'headers':[]}


class ToggleGate:
    def __init__(self, values):self.values=iter(values)
    def read(self):return next(self.values, False)


class DemoBoundaryTests(IsolatedAsyncioTestCase):
    async def invoke(self, request, gate, app=None, readonly=False):
        sent=[]
        async def send(message):sent.append(message)
        async def receive():return {'type':'http.request','body':b''}
        async def fallback(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        await DemoBoundary(app or fallback,gate=gate,readonly=readonly,interval=0.002)(request,receive,send)
        return sent

    async def test_closed_window_rejects_http_before_downstream(self):
        called=[]
        async def app(*args):called.append(True)
        sent=await self.invoke(scope('/media/video.mp4'),ToggleGate([False]),app)
        self.assertEqual(sent[0]['status'],410)
        self.assertFalse(called)
        self.assertIn(b'no-store',dict(sent[0]['headers'])[b'cache-control'])

    async def test_closed_window_rejects_new_websocket(self):
        sent=await self.invoke(scope('/_stcore/stream',kind='websocket'),ToggleGate([False]))
        self.assertEqual(sent,[{'type':'websocket.close','code':1008}])

    async def test_existing_websocket_is_closed_and_downstream_cancelled(self):
        cancelled=[]
        async def app(scope,receive,send):
            await send({'type':'websocket.accept','headers':[]})
            try:await asyncio.Event().wait()
            finally:cancelled.append(True)
        sent=await self.invoke(scope('/_stcore/stream',kind='websocket'),ToggleGate([True,False]),app)
        self.assertEqual(sent[0]['type'],'websocket.accept')
        self.assertEqual(sent[-1],{'type':'websocket.close','code':1008})
        self.assertTrue(cancelled)

    async def test_in_progress_http_media_stops_emitting_after_expiry(self):
        cancelled=[]
        async def app(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'first','more_body':True})
            try:await asyncio.Event().wait()
            finally:
                cancelled.append(True)
                await send({'type':'http.response.body','body':b'after-close'})
        sent=await self.invoke(scope('/media/video.mp4'),ToggleGate([True,False]),app)
        self.assertTrue(cancelled)
        self.assertNotIn(b'after-close',[x.get('body') for x in sent])

    async def test_readonly_share_remains_readonly_when_demo_is_active(self):
        for request in (scope('/analysis'),scope('/_stcore/upload_file/session/file','POST')):
            with self.subTest(path=request['path']):
                sent=await self.invoke(request,ToggleGate([True]),readonly=True)
                self.assertEqual(sent[0]['status'],403)


class DemoPathTests(TestCase):
    def test_only_demo_pages_and_transient_upload_paths_are_added(self):
        for request in (scope('/analysis'),scope('/mlflow'),scope('/_stcore/upload_file/session-1/file_2','POST'),
                        scope('/_stcore/upload_file/session-1/file_2','DELETE')):
            self.assertTrue(allowed(request),request)
        for request in (scope('/api/2.0/mlflow/runs/delete','POST'),scope('/jobs','POST'),
                scope('/tasks/'+'a'*32+'/resume','POST'),scope('/analysis','DELETE'),
                scope('/_stcore/upload_file/a/b/c','POST'),scope('/_stcore/stream',kind='websocket',method='POST')|{'raw_path':b'/_stcore/%73tream'}):
            self.assertFalse(allowed(request),request)

    def test_gate_closes_on_outage_missing_deadline_or_expired_deadline(self):
        class Reply(BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):pass
        for value in (None, [], {'active':True,'expires_at_epoch':float('inf'),'server_time_epoch':20},
                      {'active':True,'expires_at_epoch':float('nan'),'server_time_epoch':20},
                      {'active':False}, {'active':True},
                      {'active':True,'server_time_epoch':20,'expires_at_epoch':19}):
            with self.subTest(value=value),patch('apps.cctv_ui.demo_server.build_opener') as build:
                build.return_value.open.return_value=Reply(json.dumps(value).encode())
                self.assertFalse(Gate().read())
        with patch('apps.cctv_ui.demo_server.build_opener') as build:
            build.return_value.open.side_effect=URLError('private internal failure')
            self.assertFalse(Gate().read())
