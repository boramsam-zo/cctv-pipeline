"""Public HTTP boundary for the separate, read-only Streamlit service.

The private operator app never imports this launcher. Public clients can fetch
the UI and its in-memory media and use its read-only widgets over WebSocket.
All HTTP mutations, analysis pages and internal API paths fail before routing.
"""
import os
from pathlib import Path
import re

from starlette.middleware import Middleware
from starlette.responses import JSONResponse


def allowed_path(scope):
    path = scope.get('path', '')
    raw = scope.get('raw_path', path.encode('utf-8'))
    # No encoded/normalized aliases, directory traversal, or proxy-style URLs.
    if (raw != path.encode('utf-8') or not re.fullmatch(r'/[A-Za-z0-9_./-]*', path)
            or '//' in path or any(p in ('.', '..') for p in path.split('/'))):
        return False
    if scope['type'] == 'websocket':
        return path == '/_stcore/stream'
    if scope.get('method') not in ('GET', 'HEAD'):
        return False
    return (path in ('/', '/favicon.png', '/manifest.json', '/_stcore/health', '/_stcore/host-config')
            or path.startswith(('/static/', '/media/', '/_stcore/bidi-components/')))


class ReadOnlyBoundary:
    """Pure ASGI middleware covers HTTP and WebSocket before Streamlit."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope['type']
        if kind not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        if not allowed_path(scope):
            if kind == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            else:
                await JSONResponse({'error': 'read_only_share'}, status_code=403,
                                   headers={'Cache-Control': 'no-store'})(scope, receive, send)
            return

        async def add_headers(message):
            if message['type'] in ('http.response.start', 'websocket.accept'):
                message = dict(message)
                message['headers'] = list(message.get('headers', [])) + [
                    (b'x-cctv-access', b'read-only'),
                    (b'x-content-type-options', b'nosniff'),
                    (b'x-frame-options', b'SAMEORIGIN'),
                    (b'referrer-policy', b'no-referrer'),
                    (b'x-robots-tag', b'noindex, nofollow, noarchive'),
                ]
            await send(message)

        await self.app(scope, receive, add_headers)


def main():
    if os.environ.get('CCTV_UI_READ_ONLY') != '1':
        raise RuntimeError('share_requires_read_only_environment')
    if os.environ.get('CCTV_API_URL') != 'http://share-gateway:8081':
        raise RuntimeError('share_requires_isolated_read_gateway')
    import streamlit as st
    middlewares = [Middleware(ReadOnlyBoundary)]
    if os.environ.get('CCTV_DEMO_GATE_URL'):
        from .demo_server import DemoBoundary
        middlewares.insert(0, Middleware(DemoBoundary, readonly=True))
    app = st.App(Path(__file__).with_name('share_app.py'),
                 middleware=middlewares)
    app.run(config={
        'server.address': '0.0.0.0', 'server.port': 8502,
        'server.headless': True, 'server.runOnSave': False,
        'server.fileWatcherType': 'none', 'server.enableStaticServing': False,
        'server.enableCORS': True, 'server.enableXsrfProtection': True,
        'server.maxUploadSize': 1, 'server.maxMessageSize': 16,
        'client.showErrorDetails': False, 'client.toolbarMode': 'viewer',
        'browser.gatherUsageStats': False,
    })


if __name__ == '__main__':
    main()
