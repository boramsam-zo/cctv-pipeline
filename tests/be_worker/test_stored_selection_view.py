"""Synthetic historical views versus execution guards; no corpus, model or DB."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles as profiles
from apps.be_worker import flow_service, generation_settings, selection_jobs, team_settings
from apps.be_worker.common import WorkerError


class StoredSelectionViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spool = Path(self.tmp.name)
        self.profile = profiles.profile('B', deployment=profiles.LEGACY_DEPLOYMENT)
        # Synthetic release bytes; no actual corpus or source file is required.
        with patch.object(Path, 'read_bytes', return_value=b'old synthetic release'):
            self.settings = dict(team_settings.contract('B'), scene_model='gemini-synthetic')
        self.current = {key:value for key,value in self.settings.items() if key != 'scene_model'}
        self.current['report_code_sha256'] = 'd'*64
        self.current['report_runtime_sha256'] = 'e'*64
        self.wid = '1'*32
        sha = '2'*64
        key = profiles.gpu_identity(sha, self.profile)['gpu_key']
        self.row = {'worker_job_id':self.wid, 'directory_id':self.wid,
                    'state':{'input_sha256':sha,'analysis_profile':self.profile,'gpu_key':key}}
        self.task = {'worker_job_id':self.wid, 'version':'B', 'gpu_key':key, 'purpose':'full_report',
                     'generation_settings':self.settings, 'reference_date':None,
                     'status':'completed','error_code':None}
        self.task['task_id'] = self.task_id(self.task)
        self.directory = self.spool/self.wid
        self.directory.mkdir()
        self.write_receipt(self.task)

    def task_id(self, task):
        return selection_jobs.identifier({key:task[key] for key in
            ('gpu_key','purpose','generation_settings','reference_date')})

    def receipt_path(self, task):
        return self.directory/('task-'+task['task_id']+'.json')

    def write_receipt(self, task, *, body=None):
        self.receipt_path(task).write_text(json.dumps(selection_jobs.snapshot(task) if body is None else body))

    def test_old_contract_is_readable_without_loading_current_release(self):
        before = deepcopy(self.task)
        files = {p.name:p.read_bytes() for p in self.directory.iterdir()}
        with patch.object(team_settings,'contract',side_effect=AssertionError('view must not read current release')):
            self.assertEqual(selection_jobs.validate_stored(self.task,self.row,directory=self.directory),self.settings)
        self.assertEqual(self.task,before)
        self.assertEqual({p.name:p.read_bytes() for p in self.directory.iterdir()},files)
        self.assertFalse((self.directory/'.lock').exists())

    def test_execution_still_rejects_old_contract(self):
        with patch.object(team_settings,'contract',return_value=self.current):
            self.assertEqual(selection_jobs.validate_stored(self.task,self.row,directory=self.directory),self.settings)
            with self.assertRaisesRegex(WorkerError,'team_generation_contract_changed'):
                selection_jobs.validate(self.task,self.row)
            with self.assertRaisesRegex(WorkerError,'team_generation_contract_changed'):
                generation_settings.validate(self.settings,analysis_profile=self.profile)

    def test_completed_and_paused_are_both_viewable_but_not_new_authorization(self):
        with patch.object(team_settings,'contract',return_value=self.current):
            for status in ('completed','paused'):
                task = dict(self.task,status=status)
                self.assertEqual(selection_jobs.validate_stored(task,self.row,directory=self.directory),self.settings)
                with self.assertRaisesRegex(WorkerError,'team_generation_contract_changed'):
                    selection_jobs.validate(task,self.row)

    def test_settings_tampering_fails_task_id_even_if_hash_shape_is_valid(self):
        altered = deepcopy(self.task)
        altered['generation_settings']['report_code_sha256'] = 'f'*64
        with self.assertRaisesRegex(WorkerError,'analysis_task_id_mismatch'):
            selection_jobs.validate_stored(altered,self.row,directory=self.directory)
        altered['task_id'] = self.task_id(altered)
        with self.assertRaisesRegex(WorkerError,'analysis_task_receipt_missing'):
            selection_jobs.validate_stored(altered,self.row,directory=self.directory)
        self.write_receipt(altered,body=selection_jobs.snapshot(self.task))
        with self.assertRaisesRegex(WorkerError,'analysis_task_receipt_mismatch'):
            selection_jobs.validate_stored(altered,self.row,directory=self.directory)

    def test_missing_or_symlink_receipt_fails_without_fallback(self):
        path = self.receipt_path(self.task)
        path.unlink()
        with self.assertRaisesRegex(WorkerError,'analysis_task_receipt_missing'):
            selection_jobs.validate_stored(self.task,self.row,directory=self.directory)
        outside = self.spool/'outside.json'
        outside.write_text(json.dumps(selection_jobs.snapshot(self.task)))
        path.symlink_to(outside)
        with self.assertRaisesRegex(WorkerError,'analysis_task_receipt_mismatch'):
            selection_jobs.validate_stored(self.task,self.row,directory=self.directory)

    def test_stored_schema_hashes_types_model_and_version_remain_strict(self):
        for field,value in [('report_code_sha256','not-a-hash'),('scene_fps',True),
                            ('document_reference_date_supported',0),('schema_version','unknown'),
                            ('analysis_version','R2'),('unexpected','value')]:
            changed = dict(self.settings,**{field:value})
            with self.subTest(field=field), self.assertRaises(WorkerError):
                team_settings.validate_stored(changed,self.profile)
        with self.assertRaisesRegex(WorkerError,'generation_model_invalid'):
            team_settings.validate_stored(dict(self.settings,scene_model='secret-value'),self.profile)

    def test_job_and_gpu_identity_are_still_bound(self):
        for field,value in [('worker_job_id','3'*32),('gpu_key','gpu-v1:'+'4'*64),('version','R2')]:
            changed = dict(self.task,**{field:value})
            with self.subTest(field=field), self.assertRaisesRegex(WorkerError,'analysis_task_identity_mismatch'):
                selection_jobs.validate_stored(changed,self.row,directory=self.directory)

    def test_selection_read_route_uses_receipt_validation_not_execution_contract(self):
        member = {key:self.task[key] for key in ('task_id','worker_job_id','version','gpu_key')}
        group = {'input_sha256':self.row['state']['input_sha256'],'selection':'B',
                 'purpose':'full_report','children':[member]}
        group['selection_id'] = selection_jobs.identifier(dict(input_sha256=group['input_sha256'],
            selection='B',purpose='full_report',tasks=[self.task['task_id']]))
        class Result:
            def __init__(self,rows): self.rows=rows
            def fetchall(self): return self.rows
        class Connection:
            def execute(inner,sql,args=()):
                if not sql.lstrip().startswith('SELECT'):raise AssertionError('view attempted a write')
                if 'analysis_selections' in sql:return Result([group])
                if 'analysis_tasks' in sql:return Result([self.task])
                raise AssertionError('unexpected query')
        class Store:
            def __init__(inner,conn): pass
            def rows(inner):return [self.row]
        with patch.object(flow_service,'Store',Store), patch.object(flow_service,'spool',return_value=self.spool), \
             patch.object(flow_service,'view',return_value=[{'worker_job_id':self.wid}]) as view, \
             patch.object(team_settings,'contract',side_effect=AssertionError('current contract read in GET')), \
             patch('apps.be_worker.mlops_jobs.view_status',return_value=None):
            result=flow_service.selection_views(Connection())
            reused=flow_service.selection_views(Connection(),verified_jobs=[{'worker_job_id':self.wid}])
            view.assert_called_once()
            self.assertEqual(result,reused)
            self.receipt_path(self.task).unlink()
            with self.assertRaisesRegex(WorkerError,'analysis_task_receipt_missing'):
                flow_service.selection_views(Connection(),verified_jobs=[{'worker_job_id':self.wid}])
        self.assertEqual(result[0]['jobs'][0]['generation_settings'],self.settings)
        self.assertEqual(result[0]['jobs'][0]['stage'],'completed')


if __name__ == '__main__':
    unittest.main()
