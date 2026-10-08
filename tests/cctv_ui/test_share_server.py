"""Public boundary tests: no network, stored media or model execution."""
import unittest
from apps.cctv_ui.share_server import ReadOnlyBoundary


class PublicBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def dispatch(self, path, method='GET', kind='http', raw_path=None):
        calls, sent = [], []

        async def downstream(scope, receive, send):
            calls.append(scope)
            if kind == 'http':
                await send({'type': 'http.response.start', 'status': 206, 'headers': []})
                await send({'type': 'http.response.body', 'body': b'media'})
            else:
                await send({'type': 'websocket.accept'})

        async def receive():
            raise AssertionError('Rejected requests must not consume upload bodies')

        async def send(message):
            sent.append(message)

        scope = {'type': kind, 'path': path, 'raw_path': raw_path or path.encode(),
                 'method': method, 'headers': [], 'query_string': b''}
        await ReadOnlyBoundary(downstream)(scope, receive, send)
        return calls, sent

    async def test_mutations_never_reach_app_including_streamlit_upload(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT'):
            for path in ('/', '/_stcore/upload_file/session/file', '/selections',
                         '/jobs/' + 'a' * 32 + '/resume', '/media/stored.mp4'):
                with self.subTest(method=method, path=path):
                    calls, sent = await self.dispatch(path, method)
                    self.assertFalse(calls)
                    self.assertEqual(sent[0]['status'], 403)

    async def test_internal_and_encoded_paths_blocked_even_for_get(self):
        for path, raw in [('/analysis', None), ('/jobs', None), ('/selections', None),
                          ('/connection', None), ('/_stcore/metrics', None),
                          ('/_stcore/upload_file/a/b', None), ('/api/2.0/mlflow/runs/search', None),
                          ('/static/../analysis', None), ('//media/file', None),
                          ('/media/file', b'/%6dedia/file'), ('/.env', None),
                          ('/app/static/secrets.toml', None)]:
            with self.subTest(path=path):
                calls, sent = await self.dispatch(path, raw_path=raw)
                self.assertFalse(calls)
                self.assertEqual(sent[0]['status'], 403)

    async def test_display_and_media_head_get_preserve_response(self):
        for method in ('GET', 'HEAD'):
            for path in ('/', '/_stcore/health', '/_stcore/host-config',
                         '/static/js/main-file.js', '/media/abc123.mp4',
                         '/_stcore/bidi-components/cctv_report_copy/index.js'):
                with self.subTest(method=method, path=path):
                    calls, sent = await self.dispatch(path, method)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(sent[0]['status'], 206)
                    self.assertIn((b'x-cctv-access', b'read-only'), sent[0]['headers'])

    async def test_websocket_only_for_registered_readonly_ui(self):
        calls, sent = await self.dispatch('/_stcore/stream', kind='websocket')
        self.assertEqual(len(calls), 1)
        self.assertEqual(sent[0]['type'], 'websocket.accept')
        for path in ('/analysis', '/jobs', '/_stcore/upload_file/a/b'):
            calls, sent = await self.dispatch(path, kind='websocket')
            self.assertFalse(calls)
            self.assertEqual(sent[0], {'type': 'websocket.close', 'code': 1008})


if __name__ == '__main__':
    unittest.main()
