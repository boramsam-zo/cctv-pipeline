"""The share boundary cannot issue mutations or follow alternate upstreams."""
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from apps.cctv_ui import share_gateway as gateway


class Socket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()

    def settimeout(self, timeout):
        pass

    def makefile(self, mode, *args):
        return self.input

    def sendall(self, data):
        self.output.extend(data)


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, content_type='application/json'):
        super().__init__(body)
        self.headers = {'Content-Length': str(len(body)), 'Content-Type': content_type}


class ShareGatewayTests(unittest.TestCase):
    def request(self, method, path, response=None, headers=''):
        upstream = Mock()
        upstream.open.return_value = response or Response(b'[]')
        sock = Socket(f'{method} {path} HTTP/1.1\r\nHost: share\r\nConnection: close\r\n{headers}\r\n'.encode())
        server = SimpleNamespace(upstream='http://be-api:8080', opener=upstream)
        gateway.Handler(sock, ('127.0.0.1', 10000), server)
        return bytes(sock.output), upstream

    def test_every_mutation_method_is_denied_before_upstream(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'CONNECT', 'TRACE'):
            for path in ('/jobs', '/selections', '/tasks/' + 'a' * 32 + '/resume', '/jobs/' + 'a' * 32 + '/resume'):
                with self.subTest(method=method, path=path):
                    output, upstream = self.request(method, path, headers='Content-Length: 999\r\n')
                    self.assertIn(b' 403 ', output)
                    upstream.open.assert_not_called()

    def test_path_and_body_bypasses_do_not_reach_upstream(self):
        for path in ('/jobs?x=1', '//jobs', '/jobs/', '/%6aobs', '/jobs/../worker',
                     '/connection', '/worker', '/report-pipeline', 'http://internal/jobs',
                     '/jobs/' + 'a' * 32 + '/media/event_1/../../secret'):
            output, upstream = self.request('GET', path)
            self.assertIn(b' 403 ', output)
            upstream.open.assert_not_called()
        for headers in ('Content-Length: 1\r\n', 'Transfer-Encoding: chunked\r\n'):
            output, upstream = self.request('GET', '/jobs', headers=headers)
            self.assertIn(b' 403 ', output)
            upstream.open.assert_not_called()

    def test_saved_jobs_project_internal_settings_without_changing_results(self):
        report = {'report_id': 'report', 'state': {'result': {'report_markdown': 'six sections'}, 'result_sha256': 'hash'}}
        job = {'worker_job_id': 'a' * 32, 'reports': [report], 'generation_settings': {'secret': 'private'},
               'local_media': {'source': '/private/input'}, 'mlflow': {'tracking_uri': 'http://private'},
               'flow': {'status': 'completed', 'generation_settings': {'secret': 'private'}},
               'analysis_profile': {'version': 'R2', 'model_files': {'path': 'private'}}}
        output, upstream = self.request('GET', '/jobs', Response(json.dumps([job]).encode()))
        value = json.loads(output.split(b'\r\n\r\n', 1)[1])[0]
        self.assertEqual(value['reports'], [report])
        self.assertEqual(value['analysis_profile'], {'version': 'R2'})
        self.assertEqual(value['flow'], {'status': 'completed'})
        self.assertNotIn(b'private', output)
        self.assertEqual(upstream.open.call_args.args[0].get_method(), 'GET')
        self.assertEqual(upstream.open.call_args.args[0].full_url, 'http://be-api:8080/jobs')

    def test_stored_media_is_streamed_only_from_fixed_upstream(self):
        path = '/jobs/' + 'a' * 32 + '/media/event_1/clip_preview'
        output, upstream = self.request('GET', path, Response(b'stored-video', 'video/mp4'))
        self.assertIn(b' 200 ', output)
        self.assertEqual(output.split(b'\r\n\r\n', 1)[1], b'stored-video')
        self.assertEqual(upstream.open.call_args.args[0].full_url, 'http://be-api:8080' + path)

    def test_redirect_is_not_followed_and_upstream_errors_are_not_disclosed(self):
        handler = gateway.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, '', {}, 'http://secret/'))
        sock = Socket(b'GET /jobs HTTP/1.1\r\nHost: share\r\nConnection: close\r\n\r\n')
        opener = Mock()
        opener.open.side_effect = HTTPError('http://private/', 302, 'secret', {}, None)
        gateway.Handler(sock, ('127.0.0.1', 10000), SimpleNamespace(upstream='http://be-api:8080', opener=opener))
        self.assertIn(b' 502 ', sock.output)
        self.assertNotIn(b'secret', sock.output)

    def test_selections_keep_same_model_results(self):
        result = gateway.project_response('/selections', [{'selection': 'R2', 'generation_settings': {'secret': 1},
            'jobs': [{'worker_job_id': 'a' * 32, 'version': 'R2', 'descriptions': [{'payload': {'team_event': 'same'}}]}]}])
        self.assertEqual(result[0]['jobs'][0]['version'], 'R2')
        self.assertNotIn('generation_settings', result[0])


if __name__ == '__main__':
    unittest.main()
