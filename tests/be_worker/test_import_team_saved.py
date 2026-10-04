"""Immutable saved-case loader boundary; no database, network or model."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from apps.be_worker.common import read_json, WorkerError
from apps.be_worker.import_team_saved import parse_case, WORKER, PROVIDER,readonly_identity,existing_lock

ROOT=Path(__file__).resolve().parents[2]/'artifacts/be-worker/teammate-rag-integration-20261003-01/offline-replay-01'


class SavedCaseTests(unittest.TestCase):
    def setUp(self):
        self.case={'scene':read_json(ROOT/'scene/result.json'),
                   'report_json':read_json(ROOT/'report/report.json'),
                   'report_markdown':(ROOT/'report/report.md').read_text()}

    def test_actual_saved_case_becomes_matching_envelope_without_new_calls(self):
        original=deepcopy(self.case)
        scene,report=parse_case(self.case)
        self.assertEqual(report['worker_job_id'],WORKER)
        self.assertEqual(report['provider_job_id'],PROVIDER)
        self.assertEqual(report['analysis_id'],scene['analysis_id'])
        self.assertEqual(report['report_json']['input'],scene['team_event'])
        self.assertEqual(report['execution']['mode'],'saved_report_replay')
        self.assertFalse(report['execution']['retrieval_executed'])
        self.assertEqual(report['generation_calls'],0)
        self.assertEqual(report['embedding_calls'],0)
        self.assertEqual(self.case,original)

    def test_changed_saved_vlm_facts_rejected_even_with_rehashed_event(self):
        from apps.scene_description import scene
        self.case['scene']['team_event']['description']+=' changed'
        self.case['scene']['team_event_sha256']=scene.sha(scene.encoded(self.case['scene']['team_event']))
        with self.assertRaisesRegex(WorkerError,'immutable_input_changed'):parse_case(self.case)

    def test_changed_document_or_report_render_rejected(self):
        for name in ('report_json','report_markdown'):
            value=deepcopy(self.case)
            if name=='report_json':value[name]['sources'][0]['content']+=' changed'
            else:value[name]+=' changed'
            with self.assertRaisesRegex(WorkerError,'immutable_input_changed'):parse_case(value)

    def test_another_provider_is_not_imported_as_existing_case(self):
        self.case['scene']['generation_origin']='operating_generation'
        with self.assertRaisesRegex(WorkerError,'source_invalid'):parse_case(self.case)

    def test_unknown_payload_fields_are_rejected(self):
        self.case['new_execution']=True
        with self.assertRaisesRegex(WorkerError,'shape_invalid'):parse_case(self.case)

    def test_old_prepared_or_missing_provider_spool_is_rejected_without_recovery(self):
        for status,provider in (('prepared',None),('submitting',None),('handoff_ready','a'*32)):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as raw:
                directory=Path(raw).resolve()
                state={'status':status,'provider_job_id':provider}
                (directory/'state.json').write_text(__import__('json').dumps(state))
                before=(directory/'state.json').read_bytes()
                queue=Mock();queue.load.side_effect=AssertionError('recovery must never be called')
                row={'state':{'status':'handoff_ready','provider_job_id':PROVIDER}}
                with patch('apps.be_worker.worker.load_state',return_value=state), \
                        patch('apps.be_worker.worker.update',side_effect=AssertionError('write forbidden')):
                    with self.assertRaisesRegex(WorkerError,'completed_spool_and_database_required'):
                        readonly_identity(queue,row,directory,'event_000')
                self.assertEqual((directory/'state.json').read_bytes(),before)
                queue.load.assert_not_called()

    def test_existing_lock_preserves_mode_bytes_and_does_not_create_missing_lock(self):
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw).resolve();path=directory/'.lock'
            with self.assertRaisesRegex(WorkerError,'existing_lock_required'):
                with existing_lock(directory):pass
            self.assertFalse(path.exists())
            path.write_bytes(b'original lock');path.chmod(0o640)
            with existing_lock(directory):pass
            self.assertEqual(path.read_bytes(),b'original lock')
            self.assertEqual(path.stat().st_mode&0o777,0o640)

    def test_completed_identity_read_matches_saved_profile_without_queue_load(self):
        import json
        scene=self.case['scene'];source=scene['source']
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw).resolve()
            state=dict(status='handoff_ready',provider_job_id=PROVIDER,worker_job_id=WORKER,
                input_sha256=scene['input_sha256'],input_bytes=5855313,
                config=dict(s3_bucket=source['bucket'],s3_region=source['region']),
                analysis_profile=source['analysis_profile'],gpu_key=source['gpu_key'])
            expected=dict(bucket=source['bucket'],region=source['region'],job_id=PROVIDER,
                event_id='event_000',expected_input_sha256=scene['input_sha256'],
                analysis_profile=source['analysis_profile'],gpu_key=source['gpu_key'])
            handoff={k:state[k] for k in ('worker_job_id','provider_job_id','input_sha256','analysis_profile','gpu_key')}
            handoff['scene_configs']=['scene-event_000.json']
            (directory/'handoff.json').write_text(json.dumps(handoff))
            (directory/'scene-event_000.json').write_text(json.dumps(expected))
            before={p.name:p.read_bytes() for p in directory.iterdir()}
            queue=Mock();queue.load.side_effect=AssertionError('recovery forbidden')
            with patch('apps.be_worker.worker.load_state',return_value=state), \
                    patch('apps.be_worker.worker.update',side_effect=AssertionError('write forbidden')):
                got=readonly_identity(queue,dict(state=deepcopy(state),handoff=handoff),directory,'event_000')
            self.assertEqual(got,(state,handoff,expected))
            self.assertEqual({p.name:p.read_bytes() for p in directory.iterdir()},before)
            queue.load.assert_not_called()


if __name__=='__main__':unittest.main()
