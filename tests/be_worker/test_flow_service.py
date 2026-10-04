"""Synthetic orchestration and UI admission policy tests; no model/API/DB calls."""
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from apps.be_worker import flow_service as f


class FlowTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(f.trial_limits, 'get', return_value=None)
        guard.start(); self.addCleanup(guard.stop)
        adapter = patch.object(f, 'jsonb', lambda value: value)
        adapter.start(); self.addCleanup(adapter.stop)

    def test_completed_scene_skips_media_and_scene_generation(self):
        row={'worker_job_id':'a'*32,'state':{'scene_events':{'event_000':{'status':'description_stored','media_bytes_verified':True}}},
             'handoff':{'scene_configs':['scene-event_000.json']}}
        queue=Mock();queue.store.rows.return_value=[row]
        scenes,reports,emit=Mock(),Mock(),Mock()
        f.process_events(queue,row,'2026-09-30',emit,scenes,reports)
        scenes.fetch.assert_not_called();scenes.describe.assert_not_called()
        reports.run.assert_called_once()
        self.assertEqual(emit.call_args.args,('completed',))
    def test_stage_failure_does_not_run_dependent_generation(self):
        row={'worker_job_id':'a'*32,'state':{},'handoff':{'scene_configs':['scene-event_000.json']}}
        queue=Mock();queue.store.rows.return_value=[row]
        scenes,reports=Mock(),Mock();scenes.fetch.side_effect=RuntimeError('S3 unavailable')
        with self.assertRaises(RuntimeError):f.process_events(queue,row,'2026-09-30',Mock(),scenes,reports)
        scenes.describe.assert_not_called();reports.run.assert_not_called()
    def test_same_video_reuses_legacy_result_without_enrolling(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input.mp4';path.write_bytes(b'0000ftyp-SYNTHETIC')
            sha,_=f.fingerprint(path)
            store=Mock();store.rows.return_value=[{'worker_job_id':'a'*32,'request_key':'existing-cli', 'state':{'input_sha256':sha}}]
            @contextmanager
            def lock(_):yield
            store.lock=lock
            with patch.object(f,'enqueue') as enqueue:
                first=f.admit(store,path,'2026-09-30');second=f.admit(store,path,'2026-09-30')
            self.assertEqual(first,second);self.assertTrue(first['already_present'])
            enqueue.assert_not_called();store.connection.execute.assert_not_called()
    def test_new_admission_is_repaired_after_enqueue_before_enrolment(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input.mp4';path.write_bytes(b'0000ftyp-SYNTHETIC')
            sha,_=f.fingerprint(path)
            store=Mock();store.rows.return_value=[{'worker_job_id':'a'*32,'request_key':'ui-v1-'+sha,'state':{'input_sha256':sha}}]
            @contextmanager
            def lock(_):yield
            store.lock=lock
            with patch.object(f,'enqueue') as enqueue: answer=f.admit(store,path,'2026-09-30')
            enqueue.assert_not_called();self.assertTrue(answer['already_present'])
            self.assertIn('ON CONFLICT',store.connection.execute.call_args.args[0])

if __name__=='__main__':unittest.main()
