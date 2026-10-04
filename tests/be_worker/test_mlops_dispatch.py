"""Actual worker/queue flow with fake Registry, Pod API, and in-memory DB only."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import analysis_profiles as profiles, job_queue, mlflow_tracking, model_catalog, worker
from apps.be_worker.common import WorkerError
from apps.be_worker.runpod_api import ApiError
from tests.be_worker.test_mlflow_tracking import FakeClient
from tests.be_worker.test_queue import MemoryStore


class DispatchPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.video = self.root / 'synthetic.mp4'
        self.video.write_bytes(b'\x00\x00\x00\x18ftypisom-SYNTHETIC')
        self.snapshot = profiles.profile('R2', deployment=profiles.MLOPS_DEPLOYMENT)
        contract = self.snapshot['classifier']
        self.binding = dict(registered_model_name='cctv-r2plus1d-18', registered_model_version='7',
            **{key: contract[key] for key in ('model_id', 'model_version', 'weights_sha256')},
            contract_sha256=model_catalog.contract_sha256(contract),
            source_uri='s3://private-models/models/' + contract['weights_sha256'] + '/candidate-epoch-7.pt')
        self.bindings_file = self.root / 'bindings.json'
        self.bindings_file.write_text(json.dumps({'R2': self.binding}))
        self.client = FakeClient()
        self.client.versions = [SimpleNamespace(name=self.binding['registered_model_name'], version='7', status='READY',
            source=self.binding['source_uri'], tags={'cctv.' + key: self.binding[key] for key in
                ('model_id', 'model_version', 'weights_sha256', 'contract_sha256')})]
        for item in (patch.dict(os.environ, BE_MLFLOW_BINDINGS_FILE=str(self.bindings_file),
                                BE_MLFLOW_TRACKING_URI='http://synthetic.invalid'),
                     patch.object(mlflow_tracking, 'client_for_uri', return_value=self.client),
                     patch('apps.be_worker.hour_session.configured', return_value=False)):
            item.start()
            self.addCleanup(item.stop)
        self.store = MemoryStore()
        self.spool = self.root / 'spool'
        self.config = dict(runpod_url='https://first.invalid', s3_bucket='synthetic-evidence', s3_region='ap-northeast-2')
        self.job = job_queue.enqueue(self.store, self.spool, self.video, self.config, 'synthetic',
                                     analysis_profile=self.snapshot)
        self.api = Mock()
        self.api.health.return_value = {'storage': 's3'}
        self.api.submit_versioned.side_effect = self.submit
        self.coordinator = job_queue.Coordinator(self.store, self.spool, lambda state: self.api, slots=1)

    def submit(self, path, state):
        row = self.store.rows()[0]
        self.assertEqual(row['state']['status'], 'submitting')
        self.assertEqual(row['submit_attempts'], 1)
        self.assertEqual(state['post_attempts'], 1)
        self.assertEqual(json.loads((path.parent / 'model-binding.json').read_text()), self.binding)
        return 'f' * 32

    def assert_unsubmitted(self):
        row = self.store.rows()[0]
        self.assertEqual(row['submit_attempts'], 0)
        self.assertEqual(row['state']['status'], 'prepared')
        self.assertEqual(worker.load_state(self.coordinator.directory(row))['post_attempts'], 0)
        self.api.submit_versioned.assert_not_called()

    def test_registry_outage_keeps_zero_attempts_and_allows_explicit_retarget(self):
        with patch.object(self.client, 'get_model_version', side_effect=RuntimeError('synthetic outage')):
            with self.assertRaisesRegex(WorkerError, 'registry_unavailable'):
                self.coordinator.dispatch_tick()
        self.assert_unsubmitted()
        result = self.coordinator.retarget(self.job['worker_job_id'], dict(self.config, runpod_url='https://next.invalid'))
        self.assertEqual(result['status'], 'retargeted')
        self.assertEqual(self.store.rows()[0]['state']['config']['runpod_url'], 'https://next.invalid')
        self.assert_unsubmitted()

    def test_registry_success_still_commits_intent_before_first_post_byte(self):
        self.coordinator.dispatch_tick()
        self.api.submit_versioned.assert_called_once()
        self.assertEqual(self.store.rows()[0]['state']['status'], 'waiting')

    def test_binding_change_during_health_fails_before_db_intent(self):
        count = 0
        def health(**kwargs):
            nonlocal count
            count += 1
            if count == 2:
                self.bindings_file.write_text(json.dumps({'R2': dict(self.binding, registered_model_version='8')}))
            return {'storage': 's3'}
        self.api.health.side_effect = health
        with self.assertRaisesRegex(WorkerError, 'frozen_binding_changed'):
            self.coordinator.dispatch_tick()
        self.assert_unsubmitted()

    def test_second_health_outage_keeps_zero_db_attempts(self):
        self.api.health.side_effect = [{'storage': 's3'}, ApiError('network_error')]
        with self.assertRaises(ApiError):
            self.coordinator.dispatch_tick()
        self.assert_unsubmitted()

    def test_uncertain_post_is_never_retried(self):
        self.api.submit_versioned.side_effect = ApiError('network_error')
        self.coordinator.dispatch_tick()
        self.coordinator.dispatch_tick()
        self.assertEqual(self.store.rows()[0]['submit_attempts'], 1)
        self.assertEqual(self.store.rows()[0]['state']['status'], 'submission_unknown')
        self.api.submit_versioned.assert_called_once()

    def test_ambiguous_db_intent_write_is_preserved_without_post(self):
        original = self.store.write
        def fail_after_commit(row, state, **kwargs):
            original(row, state, **kwargs)
            if kwargs.get('attempt'):
                raise RuntimeError('synthetic lost commit acknowledgement')
        with patch.object(self.store, 'write', side_effect=fail_after_commit):
            with self.assertRaises(RuntimeError):
                self.coordinator.dispatch_tick()
        self.assertEqual(self.store.rows()[0]['state']['status'], 'submitting')
        self.coordinator.dispatch_tick()
        self.assertEqual(self.store.rows()[0]['state']['status'], 'submission_unknown')
        self.assertEqual(self.store.rows()[0]['submit_attempts'], 1)
        self.api.submit_versioned.assert_not_called()


if __name__ == '__main__':
    unittest.main()
