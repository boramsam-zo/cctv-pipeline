import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from apps.be_worker.common import WorkerError
from apps.be_worker.flow_status import report, render


class FlowStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root/('a'*32)
        self.directory.mkdir()
        self.state = {'worker_job_id': 'b'*32, 'provider_job_id': 'c'*32, 'status': 'handoff_ready',
                      'post_attempts': 1, 'poll_count': 5, 'gemini_api_calls': 3, 'event_count': 1,
                      's3': {'s3_uploaded': True, 'bucket': 'private-bucket'},
                      'config': {'token': 'DO_NOT_PRINT', 'runpod_url': 'https://private.invalid'},
                      'scene_events': {'event_000': {'status': 'description_failed',
                          'media_bytes_verified': True, 'error_code': 'gemini_http_503', 'gemini_api_calls': 3}}}
        self.write(self.directory/'state.json', self.state)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_failed_scene_and_probe_preserve_all_files_and_hide_config(self):
        p = self.directory/'scenes/event_000/text-probes/test'
        identity = {'worker_job_id': 'b'*32, 'provider_job_id': 'c'*32, 'event_id': 'event_000',
                    'generation_requests': 1, 'created_at': '2026-01-01T00:00:00Z'}
        self.write(p/'request.json', identity)
        self.write(p/'result.json', dict(identity, status='text_generation_failed', error_code='gemini_http_503'))
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*.json')}
        result = report(self.root, 'b'*32)
        job = result['jobs'][0]
        self.assertEqual(job['scene_request_intents'], 3)
        self.assertEqual(job['text_probe_request_intents'], 1)
        self.assertFalse(job['events'][0]['database_stored_record'])
        self.assertEqual(job['events'][0]['last_text_probe']['error_code'], 'gemini_http_503')
        self.assertFalse(result['live_db_checked'])
        self.assertEqual(result['remote_requests'], 0)
        for secret in ('DO_NOT_PRINT', 'private-bucket', 'private.invalid'):
            self.assertNotIn(secret, json.dumps(result)+render(result))
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*.json')})

    def test_new_job_has_no_phantom_success(self):
        state = dict(self.state, provider_job_id=None, status='prepared', post_attempts=0,
                     scene_events={}, s3=None, gemini_api_calls=0)
        self.write(self.directory/'state.json', state)
        job = report(self.root)['jobs'][0]
        self.assertIsNone(job['provider_job_id'])
        self.assertFalse(job['s3_uploaded_record'])
        self.assertEqual(job['events'], [])

    def test_database_pending_and_completed_are_distinct(self):
        for stage, stored in [('database_pending', False), ('description_stored', True)]:
            state = copy.deepcopy(self.state)
            state['scene_events']['event_000'].update(status=stage, database_loaded=stored, error_code=None)
            self.write(self.directory/'state.json', state)
            self.assertEqual(report(self.root)['jobs'][0]['events'][0]['database_stored_record'], stored)

    def test_invalid_ids_symlinks_and_probe_mismatch_fail_closed(self):
        with self.assertRaises(WorkerError): report(self.root, '../escape')
        with self.assertRaises(WorkerError): report(self.root, 'd'*32)
        p = self.directory/'scenes/event_000/text-probes/test/request.json'
        self.write(p, {'worker_job_id': 'd'*32})
        with self.assertRaisesRegex(WorkerError, 'probe_identity'): report(self.root)
        p.unlink()
        (self.root/('e'*32)).symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(WorkerError, 'invalid_receipt_path'): report(self.root)

    def test_interrupted_probe_and_no_candidates_are_not_generation_success(self):
        p = self.directory/'scenes/event_000/text-probes/test/request.json'
        self.write(p, {'worker_job_id': 'b'*32, 'provider_job_id': 'c'*32, 'event_id': 'event_000',
                       'generation_requests': 1})
        job = report(self.root)['jobs'][0]
        self.assertEqual(job['events'][0]['last_text_probe']['status'], 'incomplete')
        self.state.update(status='no_candidates', scene_events={})
        self.write(self.directory/'state.json', self.state)
        job = report(self.root)['jobs'][0]
        self.assertIn('정상 확정과 구분', job['next_step'])


if __name__ == '__main__':
    unittest.main()
