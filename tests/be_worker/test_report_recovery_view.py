"""Narrow read-side recovery seams; synthetic metadata, no DB/models/network."""
from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import flow_service, team_jobs
from apps.be_worker.common import WorkerError


class RecoveryViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = ModuleType('apps.be_worker.report_recovery')
        self.module.validate_saved_output = Mock(return_value={'recovered':'verified'})
        self.receipt = {'schema':'cctv-cpu-report-recovery-v1','execution_id':'e'*32,
            'source_task_id':'f'*32,'worker_job_id':'a'*32,'input_sha256':'b'*64,
            'analysis_id':'analysis-test','report_id':'report-test','result_sha256':'c'*64,
            'status':'report_stored','new_model_calls':0,'cpu_retrieval_calls':1}
        self.module.load_recovery = Mock(return_value=deepcopy(self.receipt))
        self.module.tracking_status = Mock(return_value={'status':'recorded','run_id':'recovery-run'})
        self.modules = patch.dict(sys.modules, {'apps.be_worker.report_recovery':self.module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        import apps.be_worker
        self.package = patch.object(apps.be_worker, 'report_recovery', self.module, create=True)
        self.package.start()
        self.addCleanup(self.package.stop)

    def item(self):
        return {'worker_job_id':'a'*32,'input_sha256':'b'*64,
            'flow':{'task_id':'f'*32,'status':'paused','paused':True},
            'mlflow':{'status':'recorded','run_id':'original-failed-run'},
            'reports':[{'report_id':'report-test','settings':{'analysis_id':'analysis-test'},
                'state':{'status':'report_stored','result_sha256':'c'*64}}]}

    def test_recovered_validator_requires_explicit_sidecar_and_fails_closed(self):
        output = self.root/'description-team-v1'
        output.mkdir()
        with patch.object(team_jobs.team_scene, 'validate_saved_output', return_value={'original':True}) as normal:
            self.assertEqual(team_jobs.validate_saved_scene(self.root, output), {'original':True})
            self.module.validate_saved_output.assert_not_called()
            (output/'normalization.json').write_text('{}')
            self.assertEqual(team_jobs.validate_saved_scene(self.root, output), {'recovered':'verified'})
            self.module.validate_saved_output.side_effect = WorkerError('recovery_changed')
            with self.assertRaisesRegex(WorkerError,'recovery_changed'):
                team_jobs.validate_saved_scene(self.root, output)
            normal.assert_called_once()

    def test_recovery_run_is_separate_from_failed_task_and_run(self):
        item = self.item()
        original = deepcopy(item)
        (self.root/'report-recovery.json').write_text('{}')
        flow_service._attach_report_recovery(item, self.root)
        self.assertEqual(item['flow'], original['flow'])
        self.assertEqual(item['mlflow'], original['mlflow'])
        self.assertEqual(item['reports'][0]['state'], original['reports'][0]['state'])
        self.assertEqual(item['recovery_mlflow']['run_id'], 'recovery-run')
        self.assertEqual(item['reports'][0]['recovery']['source_task_id'], 'f'*32)
        self.assertEqual(item['reports'][0]['mlflow'], item['recovery_mlflow'])

    def test_crossed_recovery_identity_is_held_without_hiding_original_report(self):
        item = self.item()
        original = deepcopy(item)
        (self.root/'report-recovery.json').write_text('{}')
        self.module.load_recovery.return_value['worker_job_id'] = 'd'*32
        flow_service._attach_report_recovery(item, self.root)
        self.assertEqual(item.pop('report_recovery_error'), 'report_recovery_view_identity_changed')
        self.assertEqual(item, original)
        self.module.tracking_status.assert_not_called()


if __name__ == '__main__':
    unittest.main()
