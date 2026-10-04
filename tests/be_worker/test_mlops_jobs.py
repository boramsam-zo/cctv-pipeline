"""Dispatch registry checks and report references, with a fake MLflow client."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from apps.be_worker import analysis_profiles as p, mlops_jobs as jobs, model_catalog as catalog
from tests.be_worker.test_mlflow_tracking import FakeClient


class JobTrackingTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.snapshot=p.profile('R2',deployment=p.MLOPS_DEPLOYMENT)
        c=self.snapshot['classifier']
        self.binding=dict(registered_model_name='cctv-r2plus1d-18',registered_model_version='7',
            **{key:c[key] for key in ('model_id','model_version','weights_sha256')},
            contract_sha256=catalog.contract_sha256(c),source_uri='s3://private-models/models/'+c['weights_sha256']+'/candidate-epoch-7.pt')
        path=self.directory/'bindings.json';path.write_text(json.dumps({'R2':self.binding}))
        env=patch.dict(os.environ,{'BE_MLFLOW_BINDINGS_FILE':str(path)})
        env.start();self.addCleanup(env.stop)
        self.client=FakeClient(); b=self.binding
        self.client.versions=[SimpleNamespace(name=b['registered_model_name'],version='7',status='READY',
            source=b['source_uri'],tags={'cctv.'+key:b[key] for key in
            ('model_id','model_version','weights_sha256','contract_sha256')})]
        self.row={'worker_job_id':'a'*32,'state':{'analysis_profile':self.snapshot,
            'input_sha256':'b'*64,'gpu_key':p.gpu_identity('b'*64,self.snapshot)['gpu_key']}}
        started=datetime(2026,10,4,tzinfo=timezone.utc)
        self.task=dict(task_id='c'*32,purpose='full_report',status='completed',reference_date=None,
            generation_settings=None,created_at=started,finished_at=started+timedelta(seconds=2))

    def test_dispatch_checks_registered_version_before_saving_binding(self):
        self.assertEqual(jobs.freeze_binding(self.directory,self.snapshot,client=self.client),self.binding)
        self.client.versions[0].tags['cctv.weights_sha256']='d'*64
        with self.assertRaises(Exception):jobs.freeze_binding(self.directory,self.snapshot,client=self.client)
        self.assertEqual(json.loads((self.directory/'model-binding.json').read_text()),self.binding)

    def test_report_ids_recorded_and_tracking_failure_never_reexecutes(self):
        jobs.freeze_binding(self.directory,self.snapshot,client=self.client)
        reports=[{'report_id':'report-1','status':'report_stored','result_sha256':'e'*64}]
        before=deepcopy(self.row)
        self.client.fail_delivery=True
        result=jobs.record_task(self.directory,self.task,self.row,reports=reports,client=self.client)
        self.assertEqual(result['status'],'pending');self.assertEqual(self.row,before)
        self.client.fail_delivery=False
        result=jobs.record_task(self.directory,self.task,self.row,reports=reports,client=self.client)
        self.assertEqual(result['status'],'recorded');self.assertEqual(self.client.created_runs,1)
        self.assertEqual(self.row,before)

    def test_missing_report_is_not_logged_as_full_report_success(self):
        jobs.freeze_binding(self.directory,self.snapshot,client=self.client)
        result=jobs.record_task(self.directory,self.task,self.row,client=self.client)
        self.assertEqual(result['status'],'pending')
        self.assertEqual(result['error_code'],'mlflow_completed_report_reference_missing')
        self.assertFalse(self.client.runs)

    def test_legacy_jobs_do_not_require_mlflow(self):
        self.assertIsNone(jobs.freeze_binding(self.directory,p.profile('B',deployment=p.LEGACY_DEPLOYMENT)))

    def test_unsubmitted_pause_does_not_invent_model_execution_or_retry(self):
        self.row['state'].update(status='prepared', post_attempts=0, provider_job_id=None)
        self.task.update(status='paused', paused=True, finished_at=None)
        original = deepcopy((self.row, self.task))
        result = jobs.record_task(self.directory, self.task, self.row, client=self.client)
        self.assertEqual(result['status'], 'not_submitted')
        self.assertIsNone(result['run_id'])
        self.assertFalse(self.client.runs)
        self.assertEqual((self.row, self.task), original)
        # A submitted job missing its binding is a real integrity problem.
        self.row['state']['post_attempts'] = 1
        self.assertEqual(jobs.record_task(self.directory, self.task, self.row,
                                         client=self.client)['status'], 'pending')

    def test_same_gpu_job_has_separate_comparison_and_full_report_runs(self):
        jobs.freeze_binding(self.directory, self.snapshot, client=self.client)
        reports = [{'report_id': 'report-1', 'status': 'report_stored', 'result_sha256': 'e' * 64}]
        first = jobs.record_task(self.directory, self.task, self.row, reports=reports, client=self.client)
        comparison = dict(self.task, task_id='d' * 32, purpose='gpu_comparison', status='comparison_completed')
        second = jobs.record_task(self.directory, comparison, self.row, client=self.client)
        self.assertEqual(first['status'], 'recorded')
        self.assertEqual(second['status'], 'recorded')
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertEqual(self.client.created_runs, 2)
        artifacts = list(self.client.artifacts.values())
        self.assertEqual(artifacts[0]['worker_job_id'], artifacts[1]['worker_job_id'])
        self.assertEqual(artifacts[1]['result_refs']['reports'], [])

    def test_paused_failure_then_resumed_success_keeps_history_in_same_run(self):
        jobs.freeze_binding(self.directory, self.snapshot, client=self.client)
        failed = dict(self.task, status='paused', paused=True, finished_at=None,
                      error_code='report_database_pending')
        failed_reports = [{'report_id': 'report-1', 'status': 'failed', 'result_sha256': None}]
        first = jobs.record_task(self.directory, failed, self.row,
                                 reports=failed_reports, client=self.client)
        self.assertEqual(first['status'], 'recorded')
        self.assertEqual(self.client.runs[first['run_id']].info.status, 'FAILED')
        before = deepcopy(self.row)
        successful = dict(self.task, paused=False, error_code=None)
        reports = [{'report_id': 'report-1', 'status': 'report_stored', 'result_sha256': 'e' * 64}]
        # Losing delivery of the newer observation must not freeze the old
        # failed outcome, create another GPU run, or mutate analysis state.
        self.client.fail_delivery = True
        pending = jobs.record_task(self.directory, successful, self.row,
                                   reports=reports, client=self.client)
        self.assertEqual(pending['status'], 'pending')
        self.client.fail_delivery = False
        final = jobs.record_task(self.directory, successful, self.row,
                                 reports=reports, client=self.client)
        self.assertEqual(final['status'], 'recorded')
        self.assertEqual(first['run_id'], final['run_id'])
        self.assertEqual(self.client.created_runs, 1)
        self.assertEqual(self.client.runs[final['run_id']].info.status, 'FINISHED')
        self.assertEqual(self.client.runs[final['run_id']].data.tags['cctv.error_code'], '')
        history = list(self.client.artifacts.values())
        self.assertEqual([observation['outcome'] for observation in history], ['failed', 'succeeded'])
        self.assertEqual(history[0]['error_code'], 'report_database_pending')
        self.assertEqual(history[1]['result_refs']['reports'], reports)
        self.assertEqual(self.row, before)

    def test_status_view_does_not_show_stale_receipt_as_recorded(self):
        self.task['updated_at'] = self.task['finished_at']
        path = self.directory / ('mlflow-' + self.task['task_id'] + '.json')
        path.write_text(json.dumps({'status':'recorded','revision':'old','run_id':'old-run'}))
        before = path.read_bytes()
        status = jobs.view_status(self.directory, self.task, self.snapshot)
        self.assertEqual(status['status'], 'pending')
        self.assertEqual(path.read_bytes(), before)
        path.write_text(json.dumps({'status':'recorded','revision':str(self.task['updated_at']), 'run_id':'new-run'}))
        status = jobs.view_status(self.directory, self.task, self.snapshot)
        self.assertEqual(status['status'], 'recorded')
        self.assertEqual(status['run_id'], 'new-run')

    def test_status_view_of_unsubmitted_or_corrupt_receipt_is_read_only(self):
        self.task.update(status='paused', paused=True, finished_at=None,
                         updated_at=self.task['created_at'])
        path = self.directory / ('mlflow-' + self.task['task_id'] + '.json')
        path.write_text(json.dumps({'status': 'not_submitted', 'revision': str(self.task['updated_at'])}))
        self.assertEqual(jobs.view_status(self.directory, self.task, self.snapshot)['status'], 'not_submitted')
        path.write_text('{')
        self.assertEqual(jobs.view_status(self.directory, self.task, self.snapshot)['status'], 'pending')
        self.assertEqual(path.read_text(), '{')


class FairSyncTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.spool = Path(self.temp.name)
        self.rows, self.tasks = [], []
        for digit in ('a', 'b', 'c'):
            (self.spool / digit).mkdir()
            self.rows.append({'worker_job_id': digit * 32, 'directory_id': digit,
                'state': {'analysis_profile': {'deployment': catalog.DEPLOYMENT}}})
            self.tasks.append({'task_id': digit * 32, 'worker_job_id': digit * 32,
                'purpose': 'gpu_comparison', 'updated_at': 'fixed-' + digit})
        self.conn = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: deepcopy(self.tasks)))

    def test_two_permanent_failures_cannot_starve_older_success(self):
        attempted = []
        original = deepcopy((self.tasks, self.rows))
        def record_task(directory, task, row, **kwargs):
            attempted.append(task['task_id'])
            return {'status': 'recorded' if task['task_id'] == 'c' * 32 else 'pending',
                    'run_id': 'saved-run' if task['task_id'] == 'c' * 32 else None}
        with patch.object(jobs, 'record_task', side_effect=record_task):
            jobs.sync_tasks(self.conn, self.rows, self.spool, limit=2)
            self.assertEqual(attempted, ['a' * 32, 'b' * 32])
            # A second call uses the saved cursor, as a restarted worker would.
            jobs.sync_tasks(self.conn, self.rows, self.spool, limit=2)
        self.assertEqual(attempted, ['a' * 32, 'b' * 32, 'c' * 32, 'a' * 32])
        receipt = json.loads((self.spool / 'c' / ('mlflow-' + 'c' * 32 + '.json')).read_text())
        self.assertEqual(receipt['status'], 'recorded')
        self.assertEqual((self.tasks, self.rows), original)

    def test_unsubmitted_receipt_is_skipped_until_task_revision_changes(self):
        self.tasks = self.tasks[:1]
        with patch.object(jobs, 'record_task', return_value={'status': 'not_submitted', 'run_id': None}) as send:
            jobs.sync_tasks(self.conn, self.rows, self.spool)
            self.assertEqual(jobs.sync_tasks(self.conn, self.rows, self.spool), [])
            self.assertEqual(send.call_count, 1)
            self.tasks[0]['updated_at'] = 'resumed-task-revision'
            jobs.sync_tasks(self.conn, self.rows, self.spool)
            self.assertEqual(send.call_count, 2)

    def test_corrupt_recent_receipt_does_not_starve_next_task(self):
        path = self.spool / 'a' / ('mlflow-' + 'a' * 32 + '.json')
        path.write_text('{')
        with patch.object(jobs, 'record_task', return_value={'status': 'recorded', 'run_id': 'saved'}) as send:
            result = jobs.sync_tasks(self.conn, self.rows, self.spool, limit=1)
            self.assertEqual(result[0]['status'], 'pending')
            jobs.sync_tasks(self.conn, self.rows, self.spool, limit=1)
            self.assertEqual(send.call_args.args[1]['task_id'], 'b' * 32)
        self.assertEqual(path.read_text(), '{')
