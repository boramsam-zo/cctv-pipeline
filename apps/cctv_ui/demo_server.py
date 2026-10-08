"""ASGI boundary: finite public demo including existing WebSockets and media."""
import asyncio
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.request import build_opener, ProxyHandler

from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from .share_server import allowed_path as share_path


class Gate:
    def __init__(self, url=None):
        self.url = url or os.environ.get('CCTV_DEMO_GATE_URL', 'http://demo-gateway:8082/demo/status')
        self.lock = threading.Lock()
        self.checked = 0
        self.deadline = 0
        self.allowed = False

    def read(self):
        with self.lock:
            now = time.monotonic()
            if now - self.checked < 0.5:
                return self.allowed and now < self.deadline
            try:
                with build_opener(ProxyHandler({})).open(self.url, timeout=2) as response:
                    value = json.loads(response.read(8192))
                if (not isinstance(value, dict) or not all(type(value.get(k)) in (int, float)
                        and math.isfinite(value[k]) for k in ('expires_at_epoch', 'server_time_epoch'))):
                    raise ValueError('invalid_demo_clock')
                self.allowed = value.get('active') is True
                remaining = float(value.get('expires_at_epoch', 0)) - float(value.get('server_time_epoch', 0))
                if not 0 < remaining <= 86400: self.allowed = False
                self.deadline = now + max(0, remaining)
            except (OSError, ValueError, TypeError):
                self.allowed = False
            self.checked = now
            return self.allowed and now < self.deadline


def allowed(scope):
    if share_path(scope):
        return True
    path = scope.get('path', '')
    if scope.get('raw_path', path.encode()) != path.encode(): return False
    if scope['type'] != 'http': return False
    if path in ('/analysis', '/mlflow'):
        return scope.get('method') in ('GET', 'HEAD')
    if re.fullmatch(r'/_stcore/upload_file/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+', path):
        # This deletes only a transient uploader object, never an analysis record.
        return scope.get('method') in ('POST', 'PUT', 'DELETE')
    return False


class DemoBoundary:
    def __init__(self, app, gate=None, readonly=False, interval=1):
        self.app = app
        self.gate = gate or Gate()
        self.readonly = readonly
        self.interval = interval

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        websocket = scope['type'] == 'websocket'
        if not (share_path(scope) if self.readonly else allowed(scope)):
            if websocket: return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'error': 'demo_route_not_allowed'}, status_code=403)(scope, receive, send)
        if not await asyncio.to_thread(self.gate.read):
            if websocket: return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'error': 'demo_closed'}, status_code=410,
                                      headers={'Cache-Control': 'no-store'})(scope, receive, send)

        stopped = False

        async def guarded_send(message):
            if stopped: return
            if message['type'] in ('http.response.start', 'websocket.accept'):
                message = dict(message)
                message['headers'] = list(message.get('headers', [])) + [
                    (b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                    (b'x-robots-tag', b'noindex, nofollow, noarchive'),
                    (b'referrer-policy', b'no-referrer')]
            await send(message)

        async def expiration():
            nonlocal stopped
            while True:
                await asyncio.sleep(self.interval)
                if not await asyncio.to_thread(self.gate.read):
                    stopped = True
                    if websocket: await send({'type': 'websocket.close', 'code': 1008})
                    return

        downstream = asyncio.create_task(self.app(scope, receive, guarded_send))
        watchdog = asyncio.create_task(expiration())
        try:
            await asyncio.wait((downstream, watchdog), return_when=asyncio.FIRST_COMPLETED)
            if downstream.done():
                await downstream
        finally:
            for task in (downstream, watchdog):
                if not task.done(): task.cancel()
            await asyncio.gather(downstream, watchdog, return_exceptions=True)


def main():
    if (os.environ.get('CCTV_DEMO_MODE') != '1'
            or os.environ.get('CCTV_API_URL') != 'http://demo-gateway:8082'
            or os.environ.get('CCTV_UI_READ_ONLY') == '1'):
        raise RuntimeError('demo_requires_isolated_gateway')
    import streamlit as st
    app = st.App(Path(__file__).with_name('demo_app.py'), middleware=[Middleware(DemoBoundary)])
    app.run(config={'server.address': '0.0.0.0', 'server.port': 8503,
        'server.headless': True, 'server.runOnSave': False, 'server.fileWatcherType': 'none',
        'server.enableStaticServing': False, 'server.enableCORS': True,
        'server.enableXsrfProtection': True, 'server.maxUploadSize': 50,
        'server.maxMessageSize': 64, 'client.showErrorDetails': False,
        'client.toolbarMode': 'viewer', 'browser.gatherUsageStats': False})


if __name__ == '__main__': main()
