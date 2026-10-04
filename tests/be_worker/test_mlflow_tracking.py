"""Fake MLflow client: no SDK, network, weights, DB or GPU execution."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from apps.be_worker import mlflow_tracking as tracking
from apps.be_worker.common import WorkerError


def binding(**changes):
    return dict(registered_model_name='cctv-r2plus1d-18', registered_model_version='7',
        model_id='r2plus1d_18', model_version='sotad-head-epoch7',
        weights_sha256='a' * 64, contract_sha256='b' * 64,
        source_uri='s3://private-models/models/r2plus1d_18/' + 'a' * 64 + '/candidate-epoch-7.pt',
        **changes)


def record(**changes):
    value = dict(worker_job_id='job-1', execution_id='task-1', model_binding=binding(),
                 input_sha256='c' * 64, config={'batch_size': 8, 'frames': 16},
                 outcome='succeeded', result_refs={'provider_job_id': 'gpu-1',
                 'report_ids': ['report-1'], 'result_sha256': 'd' * 64},
                 durations={'gpu_seconds': 2.5, 'total_seconds': 5.0},
                 started_at_ms=1000, finished_at_ms=6000)
    value.update(changes)
    return tracking.execution_record(**value)


class FakeClient:
    def __init__(self):
        b = binding()
        self.versions = [NS(name=b['registered_model_name'], version=b['registered_model_version'],
            status='READY', source=b['source_uri'], tags={
                'cctv.' + k: b[k] for k in tracking._TAG_FIELDS})]
        self.registered = {b['registered_model_name']}
        self.experiment = NS(experiment_id='2', lifecycle_stage='active')
        self.runs = {}
        self.params = {}
        self.artifacts = {}
        self.metrics = []
        self.created_runs = self.created_versions = 0
        self.fail_delivery = self.fail_create_after_accept = self.fail_create_before_accept = False

    def get_model_version(self, name, version):
        return next(v for v in self.versions if v.name == name and v.version == version)

    def search_model_versions(self, filter_string, max_results, page_token):
        name = filter_string.split("'")[1]
        return [v for v in self.versions if v.name == name]

    def get_registered_model(self, name):
        if name not in self.registered:
            exc = RuntimeError('synthetic')
            exc.error_code = 'RESOURCE_DOES_NOT_EXIST'
            raise exc
        return NS(name=name)

    def create_registered_model(self, name, description):
        self.registered.add(name)

    def create_model_version(self, name, source, tags, await_creation_for, description):
        self.created_versions += 1
        value = NS(name=name, version=str(len(self.versions) + 1), status='READY',
                   source=source, tags=tags)
        self.versions.append(value)
        return value

    def get_experiment_by_name(self, name):
        return self.experiment

    def create_experiment(self, name):
        self.experiment = NS(experiment_id='2', lifecycle_stage='active')
        return '2'

    def search_runs(self, identifiers, filter_string, max_results):
        sha = filter_string.split("'")[1]
        return [v for v in self.runs.values() if v.data.tags['cctv.identity_sha256'] == sha]

    def create_run(self, experiment_id, start_time, tags, run_name):
        self.created_runs += 1
        if self.fail_create_before_accept:
            raise RuntimeError('synthetic unsafe https://private/?token=secret')
        run = NS(info=NS(run_id='run-' + str(self.created_runs), experiment_id=experiment_id,
                        lifecycle_stage='active', status='RUNNING'), data=NS(tags=deepcopy(tags)))
        self.runs[run.info.run_id] = run
        if self.fail_create_after_accept:
            self.fail_create_after_accept = False
            raise RuntimeError('synthetic unsafe https://private/?token=secret')
        return run

    def get_run(self, run_id):
        return self.runs[run_id]

    def log_param(self, run_id, key, value):
        previous = self.params.get((run_id, key))
        assert previous is None or previous == value
        self.params[run_id, key] = value

    def log_dict(self, run_id, dictionary, artifact_file):
        if self.fail_delivery:
            raise RuntimeError('synthetic unsafe https://private/?token=secret')
        self.artifacts[run_id, artifact_file] = deepcopy(dictionary)

    def log_metric(self, run_id, key, value, timestamp, step):
        self.metrics.append((run_id, key, value, timestamp, step))

    def set_tag(self, run_id, key, value):
        self.runs[run_id].data.tags[key] = value

    def set_terminated(self, run_id, status, end_time):
        self.runs[run_id].info.status = status


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = FakeClient()

    def send(self, value=None):
        return tracking.record_execution(self.root, value or record(), client=self.client)

    def test_success_contains_versions_reports_and_only_duration_metrics(self):
        original = record()
        output = self.send(original)
        self.assertEqual(output, {'status': 'recorded', 'run_id': 'run-1', 'error_code': None})
        saved = next(iter(self.client.artifacts.values()))
        self.assertEqual(saved, original)
        self.assertEqual(saved['result_refs']['report_ids'], ['report-1'])
        self.assertEqual(saved['evaluation_scope'], 'inference_only')
        self.assertEqual(self.client.params['run-1', 'registered_model_version'], '7')
        self.assertEqual({r[1] for r in self.client.metrics}, {'gpu_seconds', 'total_seconds'})
        self.assertEqual(self.client.runs['run-1'].info.status, 'FINISHED')

    def test_same_observation_is_idempotent(self):
        self.send()
        before = deepcopy(self.client.metrics)
        self.send()
        self.assertEqual(self.client.created_runs, 1)
        self.assertEqual(self.client.metrics, before)

    def test_log_failure_preserves_analysis_and_retries_metadata_only(self):
        original = record()
        self.client.fail_delivery = True
        self.assertEqual(self.send(original)['status'], 'pending')
        self.assertEqual(original['outcome'], 'succeeded')
        receipts = ''.join(p.read_text() for p in self.root.rglob('*.json'))
        self.assertNotIn('https://private/', receipts)
        self.assertNotIn('token=secret', receipts)
        self.client.fail_delivery = False
        result = tracking.retry_pending(self.root, client=self.client)
        self.assertEqual(result[0]['status'], 'recorded')
        self.assertEqual(self.client.created_runs, 1)
        self.assertEqual(tracking.retry_pending(self.root, client=self.client), [])

    def test_uncertain_create_is_reconciled_without_second_run(self):
        self.client.fail_create_after_accept = True
        self.assertEqual(self.send()['status'], 'pending')
        self.assertEqual(self.send()['status'], 'recorded')
        self.assertEqual(self.client.created_runs, 1)

    def test_uncertain_absent_create_is_not_repeated(self):
        self.client.fail_create_before_accept = True
        self.send()
        self.client.fail_create_before_accept = False
        output = self.send()
        self.assertEqual(output['error_code'], 'mlflow_run_creation_outcome_unknown')
        self.assertEqual(self.client.created_runs, 1)

    def test_wrong_registry_contract_blocks_tracking(self):
        self.client.versions[0].tags['cctv.contract_sha256'] = 'e' * 64
        self.assertEqual(self.send()['error_code'], 'mlflow_registered_model_mismatch')
        self.assertEqual(self.client.created_runs, 0)

    def test_changed_model_input_or_config_cannot_reuse_execution(self):
        self.send()
        output = self.send(record(config={'batch_size': 9}))
        self.assertEqual(output['error_code'], 'mlflow_execution_identity_changed')
        self.assertEqual(self.client.created_runs, 1)

    def test_final_failure_is_logged_as_failure_not_normal(self):
        self.send(record(outcome='failed', error_code='provider_failed', result_refs={}))
        self.assertEqual(self.client.runs['run-1'].info.status, 'FAILED')
        self.assertEqual(self.client.runs['run-1'].data.tags['cctv.outcome'], 'failed')

    def test_no_candidates_is_not_recorded_as_a_normal_prediction(self):
        self.send(record(outcome='no_candidates', result_refs={}))
        self.assertEqual(self.client.runs['run-1'].data.tags['cctv.outcome'], 'no_candidates')
        self.assertEqual(next(iter(self.client.artifacts.values()))['result_refs'], {})

    def test_unconfigured_tracker_keeps_durable_pending(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {}, clear=True):
            output = tracking.record_execution(self.root, record())
        self.assertEqual(output['error_code'], 'mlflow_not_configured')
        self.assertTrue((self.root / 'mlflow-outbox/task-1/receipt.json').is_file())

    def test_rejects_accuracy_f1_nonfinite_and_secret_metadata(self):
        for duration in ({'accuracy': .8}, {'f1': .7}, {'gpu_seconds': float('nan')},
                         {'gpu_seconds': -1}, {'gpu_seconds': True}):
            with self.subTest(duration=duration), self.assertRaises(WorkerError):
                record(durations=duration)
        for config in ({'token': 'secret'}, {'runpod_url': 'https://pod.example'},
                       {'path': '/path/to/user/model.pt'}, {'api_key': 'secret'}):
            with self.subTest(config=config), self.assertRaises(WorkerError):
                record(config=config)
        self.assertEqual(record(config={'max_output_tokens': 1024})['config']['max_output_tokens'], 1024)

    def test_registration_reuses_exact_business_version(self):
        spec = binding()
        spec.pop('registered_model_version')
        self.assertEqual(tracking.register_model_reference(self.root, spec, client=self.client), binding())
        self.assertEqual(self.client.created_versions, 0)

    def test_new_reference_returns_actual_registry_version_not_epoch(self):
        spec = binding()
        spec.pop('registered_model_version')
        spec.update(registered_model_name='cctv-x3d-s', model_id='x3d_s',
                    model_version='operating-epoch7', source_uri='baseimage://sha256:' + 'd' * 64 + '/models/x3d.pt')
        answer = tracking.register_model_reference(self.root, spec, client=self.client)
        self.assertEqual(answer['registered_model_version'], '2')
        self.assertEqual(answer['model_version'], 'operating-epoch7')
        again = tracking.register_model_reference(self.root, spec, client=self.client)
        self.assertEqual(answer, again)
        self.assertEqual(self.client.created_versions, 1)

    def test_conflicting_registered_hash_is_not_replaced(self):
        spec = binding()
        spec.pop('registered_model_version')
        spec['weights_sha256'] = 'e' * 64
        with self.assertRaisesRegex(WorkerError, 'mlflow_registered_model_mismatch'):
            tracking.register_model_reference(self.root, spec, client=self.client)
        self.assertEqual(self.client.created_versions, 0)

    def test_signed_or_credentialed_source_is_rejected(self):
        for uri in ('s3://models/path?signature=secret', 's3://user:pass@models/path',
                    'https://example/model.pt', 's3://models/../other.pt'):
            with self.subTest(uri=uri), self.assertRaises(WorkerError):
                tracking.validate_binding(dict(binding(), source_uri=uri))

    def test_retry_limit_and_tampered_observation(self):
        self.client.fail_delivery = True
        self.send()
        receipt = json.loads((self.root / 'mlflow-outbox/task-1/receipt.json').read_text())
        observation = self.root / 'mlflow-outbox/task-1' / (receipt['pending_observation'] + '.json')
        value = json.loads(observation.read_text())
        value['outcome'] = 'failed'
        observation.write_text(json.dumps(value))
        result = tracking.retry_pending(self.root, client=self.client)
        self.assertEqual(result[0]['error_code'], 'mlflow_outbox_observation_changed')
        with self.assertRaises(WorkerError):
            tracking.retry_pending(self.root, limit=0)


if __name__ == '__main__':
    unittest.main()
