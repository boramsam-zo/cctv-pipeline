"""LangSmith wire payload tests with a fake HTTP transport, no remote trace uploads."""
from contextlib import contextmanager
import json
import unittest
import urllib.error
import uuid

from apps.be_worker.tracing import LangSmith


class Opener:
    def __init__(self, first_status=200):
        self.first_status, self.requests = first_status, []

    @contextmanager
    def open(self, request, timeout):
        self.requests.append(request)
        if len(self.requests) == 1 and self.first_status >= 300:
            raise urllib.error.HTTPError(request.full_url, self.first_status, 'synthetic', {}, None)
        class Response:
            status = 200
        yield Response()


class LangSmithTests(unittest.TestCase):
    def payload(self):
        return {'id': str(uuid.uuid4()), 'name': 'be.run', 'run_type': 'chain', 'session_name': 'test-project',
                'start_time': '2026-09-28T00:00:00+00:00', 'end_time': '2026-09-28T00:00:01+00:00',
                'inputs': {'input_bytes': 32}, 'outputs': {'status': 'evidence_ready'}}

    def test_auth_only_in_header_and_project_payload(self):
        sink = LangSmith('synthetic-langsmith-key', project='test-project')
        sink.opener = Opener()
        sink.send(self.payload())
        request = sink.opener.requests[0]
        self.assertEqual(request.full_url, 'https://api.smith.langchain.com/runs')
        self.assertEqual(request.get_header('X-api-key'), 'synthetic-langsmith-key')
        self.assertNotIn(b'synthetic-langsmith-key', request.data)
        self.assertEqual(json.loads(request.data)['session_name'], 'test-project')

    def test_duplicate_id_updates_same_run(self):
        sink = LangSmith('synthetic-key')
        sink.opener = Opener(409)
        payload = self.payload()
        sink.send(payload)
        patch = sink.opener.requests[1]
        self.assertEqual(patch.method, 'PATCH')
        self.assertTrue(patch.full_url.endswith('/runs/' + payload['id']))
        self.assertEqual(set(json.loads(patch.data)), {'outputs', 'end_time'})

    def test_redirect_and_auth_errors_are_not_followed_or_retried(self):
        for code in (307, 401, 429):
            with self.subTest(code=code):
                sink = LangSmith('synthetic-key')
                sink.opener = Opener(code)
                with self.assertRaises(Exception):
                    sink.send(self.payload())
                self.assertEqual(len(sink.opener.requests), 1)


if __name__ == '__main__':
    unittest.main()
