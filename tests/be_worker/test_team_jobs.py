"""Saved actual scene/report + worker journals; no models, network or real PG."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import team_jobs
from apps.be_worker.common import WorkerError, read_json, save
from apps.scene_description import scene, team_scene
from apps.team_rag.control_rag import markdown

ROOT = Path(__file__).resolve().parents[2]
REPLAY = ROOT/'artifacts/be-worker/teammate-rag-integration-20261003-01/offline-replay-01'


class QueryResult:
    def __init__(self, value=None):
        self.value = value
    def fetchone(self):
        return deepcopy(self.value)


class ConnectionRecorder:
    """Records only team report SQL and mirrors durable state for restart cases."""
    def __init__(self):
        self.sql = []
        self.records = {}
        self.fail_on_calls = False
    def execute(self, sql, args=None):
        self.sql.append(sql)
        if 'has_table_privilege' in sql:
            return QueryResult({'ok':True})
        if sql.lstrip().startswith('INSERT'):
            report_id, analysis_id, worker_id, settings, state = args
            if self.fail_on_calls and state.get('calls'):
                self.fail_on_calls = False
                raise RuntimeError('simulated database interruption')
            old = self.records.get(report_id)
            if old is None or old['settings'] == settings:
                self.records[report_id] = dict(settings=deepcopy(settings),state=deepcopy(state),
                    analysis_id=analysis_id,worker_job_id=worker_id)
            return QueryResult()
        if 'WHERE report_id=' in sql:
            return QueryResult(self.records.get(args[0]))
        if 'WHERE analysis_id=' in sql:
            return QueryResult(next((v for v in self.records.values() if v['analysis_id']==args[0]),None))
        return QueryResult()


class QueueRecorder:
    def __init__(self, directory, row):
        self.job_directory = directory
        self.row = deepcopy(row)
        self.connection = ConnectionRecorder()
        self.store = self
    def rows(self):
        return [deepcopy(self.row)]
    def directory(self, row):
        return self.job_directory
    def load(self, row):
        return read_json(self.job_directory/'state.json')
    def write(self, row, state):
        self.row['state'] = deepcopy(state)


class TeamJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual_scene = read_json(REPLAY/'scene/result.json')
        receipt = read_json(REPLAY/'scene-replay-receipt.json')
        if scene.sha((REPLAY/'scene/result.json').read_bytes()) != receipt['result_file_sha256']:
            raise AssertionError('actual scene fixture differs from replay receipt')
        cls.actual_report = dict(pipeline_id='team-rag-v1',
            report_json=read_json(REPLAY/'report/report.json'),
            report_markdown=(REPLAY/'report/report.md').read_text())
        report_receipt = read_json(REPLAY/'report/replay-receipt.json')
        for name in ('report.json','report.md'):
            if scene.sha((REPLAY/'report'/name).read_bytes()) != report_receipt['files'][name]['sha256']:
                raise AssertionError('actual report fixture differs from replay receipt')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()/'job'
        self.event_directory = self.directory/'scenes/event_000'
        self.event_directory.mkdir(parents=True)
        self.result = deepcopy(self.actual_scene)
        source = self.result['source']
        expected = dict(bucket=source['bucket'],region=source['region'],
            job_id=self.result['provider_job_id'],event_id='event_000',
            expected_input_sha256=self.result['input_sha256'],
            analysis_profile=source['analysis_profile'],gpu_key=source['gpu_key'])
        state = dict(status='handoff_ready',worker_job_id='d'*32,
            provider_job_id=self.result['provider_job_id'],input_sha256=self.result['input_sha256'],
            config=dict(s3_bucket=source['bucket'],s3_region=source['region']),
            analysis_profile=source['analysis_profile'],gpu_key=source['gpu_key'],scene_events={})
        handoff = {key:state[key] for key in ('worker_job_id','provider_job_id','input_sha256',
                                             'analysis_profile','gpu_key')}
        handoff.update(manifest_sha256=source['manifest_sha256'],scene_configs=['scene-event_000.json'])
        save(self.directory/'state.json',state)
        save(self.directory/'handoff.json',handoff)
        save(self.directory/'scene-event_000.json',expected)
        self.queue = QueueRecorder(self.directory,dict(worker_job_id='d'*32,state=state,handoff=handoff))
        self.persisted = {}
        self.scenes = team_jobs.TeamSceneJobs(self.queue,self.persist_scene,lambda:None)
        self.manifest = {'files':{f'evidence/event_000/{kind}.' + asset['key'].rsplit('.',1)[1]:
             {k:v for k,v in asset.items() if k != 'asset_id'} for kind,asset in source['assets'].items()}}
        self.event = dict(source_start_s=source['source_start_s'],source_end_s=source['source_end_s'])
        self.event.update({kind:'evidence/event_000/'+kind+'.'+asset['key'].rsplit('.',1)[1]
                          for kind,asset in source['assets'].items()})
        self.scenes._media = Mock(return_value=(self.manifest,self.event,{}))
        self.caller = Mock(return_value={'synthetic':'provider response supplied by test'})
        self.key_loader = Mock(return_value='mock-key')
        self.describe_mock = patch('apps.be_worker.team_jobs.team_scene.describe',side_effect=self.mock_describe)
        self.describe_mock.start()
        self.addCleanup(self.describe_mock.stop)
        # This file tests worker orchestration. The adapter's real media/raw
        # recheck is covered by test_team_scene and never bypassed in production.
        self.cached_check = patch('apps.be_worker.team_jobs.team_scene.validate_saved_output',
            side_effect=lambda inputs,output:read_json(output/'result.json'))
        self.cached_check_mock = self.cached_check.start()
        self.addCleanup(self.cached_check.stop)
        self.claim = patch('apps.be_worker.team_jobs.trial_limits.claim',return_value={})
        self.claim_mock = self.claim.start()
        self.addCleanup(self.claim.stop)

    def persist_scene(self, value):
        digest = team_scene.validate_result(value)
        self.persisted[value['analysis_id']] = deepcopy(value)
        return dict(committed=True,result_sha256=digest)

    def mock_describe(self, inputs, output, model, fps, key, *, execute, caller, pre_call):
        self.assertTrue(execute)
        self.assertEqual((model,fps,key),('gemini-3.5-flash-lite',4,'mock-key'))
        output.mkdir(mode=0o700)
        save(output/'request.json',dict(request_sha256=self.result['request_sha256']))
        pre_call(dict(request_sha256=self.result['request_sha256']))
        raw = caller(model,b'mocked media transport',key)
        save(output/'response.raw.json',raw)
        save(output/'result.json',self.result)
        return deepcopy(self.result)

    def describe(self):
        return self.scenes.describe('d'*32,'event_000',model='gemini-3.5-flash-lite',fps=4,
                                    key_loader=self.key_loader,caller=self.caller)

    def save_completed_scene(self):
        output=self.event_directory/'description-team-v1'
        output.mkdir()
        save(output/'result.json',self.result)
        state=self.queue.load(self.queue.row)
        state['scene_events']['event_000']=dict(status='description_stored',
            analysis_id=self.result['analysis_id'],result_sha256=team_scene.validate_result(self.result),
            media_bytes_verified=True,gemini_api_calls=1,description_directory=output.name)
        save(self.directory/'state.json',state)
        self.queue.write(self.queue.row,state)
        self.persisted[self.result['analysis_id']]=deepcopy(self.result)

    def report_patches(self, **environment):
        from contextlib import ExitStack
        stack=ExitStack()
        stack.enter_context(patch.dict(sys.modules,{
            'psycopg':SimpleNamespace(),'psycopg.types':SimpleNamespace(),
            'psycopg.types.json':SimpleNamespace(Jsonb=lambda value:deepcopy(value))}))
        stack.enter_context(patch('apps.be_worker.team_jobs.team_db.show',
            side_effect=lambda conn,aid:deepcopy(self.persisted[aid])))
        stack.enter_context(patch('apps.be_worker.team_jobs.hour_session.configured',return_value=True))
        stack.enter_context(patch.dict('os.environ',environment,clear=True))
        return stack

    def test_actual_report_envelope_preserves_event_and_document_sources(self):
        value=team_jobs.envelope(self.actual_report,self.result,'report-test','d'*32)
        self.assertEqual(value['report_json']['input'],self.result['team_event'])
        self.assertEqual(value['scene_sha256'],team_scene.validate_result(self.result))
        self.assertFalse(value['document_reference_date_supported'])
        self.assertEqual((value['generation_calls'],value['embedding_calls']),(0,0))

    def test_document_input_and_citation_tamper_rejected(self):
        edits=[lambda r:r['report_json']['input'].update(description='modified VLM fact'),
               lambda r:r['report_json'].update(event_id='other'),
               lambda r:r['report_json']['sources'][0].update(content='invented source'),
               lambda r:r['report_json']['references'][0].update(reference_id='R42'),
               lambda r:r['report_json']['references'][0].update(source_url='https://fabricated.invalid'),
               lambda r:r['report_json']['retrieval'].update(reference_date='2026-10-03')]
        for index,edit in enumerate(edits):
            changed=deepcopy(self.actual_report);edit(changed)
            changed['report_markdown']=markdown(changed['report_json'])
            with self.subTest(index=index),self.assertRaises(WorkerError):
                team_jobs.validate_report(changed,self.result['team_event'])

    def test_scene_key_claim_and_caller_once_across_restart(self):
        first=self.describe()
        self.assertEqual(first['status'],'description_stored')
        self.scenes=team_jobs.TeamSceneJobs(self.queue,self.persist_scene,lambda:None)
        self.scenes._media=Mock(return_value=(self.manifest,self.event,{}))
        second=self.describe()
        self.assertEqual(second['result_sha256'],first['result_sha256'])
        self.key_loader.assert_called_once();self.caller.assert_called_once()
        self.claim_mock.assert_called_once()
        self.cached_check_mock.assert_called_once()
        self.assertEqual(self.queue.row['state']['gemini_api_calls'],1)

    def test_scene_failed_provider_never_calls_again(self):
        self.caller.side_effect=TimeoutError('mock provider timeout')
        with self.assertRaises(WorkerError):self.describe()
        with self.assertRaisesRegex(WorkerError,'incomplete'):self.describe()
        self.caller.assert_called_once();self.key_loader.assert_called_once()
        self.assertEqual(self.queue.row['state']['scene_events']['event_000']['status'],'description_failed')

    def test_scene_saved_hash_and_asset_identity_tamper_blocks_key_and_provider(self):
        self.save_completed_scene()
        path=self.event_directory/'description-team-v1/result.json'
        before=read_json(path)
        changed=deepcopy(before)
        changed['source']['assets']['clip']['sha256']='0'*64
        save(path,changed)
        with self.assertRaisesRegex(WorkerError,'identity_mismatch'):self.describe()
        self.key_loader.assert_not_called();self.caller.assert_not_called()

    def test_scene_database_pending_can_resume_saved_result_without_key(self):
        self.scenes.persist_result=Mock(side_effect=RuntimeError('simulated DB failure'))
        with self.assertRaises(WorkerError):self.describe()
        state=self.queue.row['state']['scene_events']['event_000']
        self.assertEqual(state['status'],'database_pending')
        self.scenes=team_jobs.TeamSceneJobs(self.queue,self.persist_scene,lambda:None)
        self.scenes._media=Mock(return_value=(self.manifest,self.event,{}))
        self.assertEqual(self.describe()['status'],'description_stored')
        self.caller.assert_called_once();self.key_loader.assert_called_once()

    def test_cpu_report_runs_once_and_reuses_saved_response_after_restart(self):
        self.save_completed_scene()
        execute=Mock(return_value=deepcopy(self.actual_report))
        claims=[]
        def claim(*args,**kwargs):claims.append(args[3]);return{}
        with self.report_patches(),patch('apps.be_worker.team_jobs.hour_session.claim',side_effect=claim):
            reports=team_jobs.TeamReportJobs(self.queue,execute)
            first=reports.run('d'*32,'event_000',None,'cpu-template',lambda:self.fail('no Gemini key'))
            reports=team_jobs.TeamReportJobs(self.queue,execute)
            second=reports.run('d'*32,'event_000',None,'cpu-template',lambda:self.fail('no Gemini key'))
        execute.assert_called_once()
        self.assertEqual(claims,['cpu_retrieval'])
        self.assertEqual(first['result_sha256'],second['result_sha256'])
        self.assertEqual(first['calls'],{'cpu_retrieval':1})
        self.assertEqual((first['generation_calls'],first['embedding_calls']),(0,0))

    def test_cpu_failure_no_automatic_retry_or_second_claim(self):
        self.save_completed_scene()
        execute=Mock(side_effect=RuntimeError('simulated CPU failure'))
        with self.report_patches(),patch('apps.be_worker.team_jobs.hour_session.claim') as claim:
            reports=team_jobs.TeamReportJobs(self.queue,execute)
            with self.assertRaisesRegex(WorkerError,'failed_inspect'):reports.run('d'*32,'event_000',None,'cpu-template',None)
            with self.assertRaisesRegex(WorkerError,'response_missing_no_auto_retry'):reports.run('d'*32,'event_000',None,'cpu-template',None)
        execute.assert_called_once();claim.assert_called_once()

    def test_cpu_response_saved_before_db_failure_reused_without_new_call(self):
        self.save_completed_scene()
        execute=Mock(return_value=deepcopy(self.actual_report))
        original=self.queue.connection.execute
        def interrupt(sql,args=None):
            if sql.lstrip().startswith('INSERT') and args[-1].get('stages'):
                self.queue.connection.execute=original
                raise RuntimeError('simulated DB failure after saved CPU response')
            return original(sql,args)
        self.queue.connection.execute=interrupt
        with self.report_patches(),patch('apps.be_worker.team_jobs.hour_session.claim') as claim:
            reports=team_jobs.TeamReportJobs(self.queue,execute)
            with self.assertRaises(RuntimeError):reports.run('d'*32,'event_000',None,'cpu-template',None)
            again=team_jobs.TeamReportJobs(self.queue,execute).run('d'*32,'event_000',None,'cpu-template',None)
        execute.assert_called_once();claim.assert_called_once()
        self.assertEqual(again['status'],'report_stored')

    def test_missing_cpu_model_does_not_consume_claim(self):
        self.save_completed_scene()
        with (self.report_patches(),patch('apps.be_worker.team_jobs.hour_session.claim') as claim,
              patch('apps.team_rag.runtime.build_result',side_effect=ValueError('model unavailable'))):
            reports=team_jobs.TeamReportJobs(self.queue)
            with self.assertRaisesRegex(WorkerError,'local_model_not_prepared'):
                reports.run('d'*32,'event_000',None,'cpu-template',None)
        claim.assert_not_called()

    def test_cpu_saved_report_document_changed_blocks_reuse(self):
        self.save_completed_scene()
        execute=Mock(return_value=deepcopy(self.actual_report))
        with self.report_patches(),patch('apps.be_worker.team_jobs.hour_session.claim'):
            reports=team_jobs.TeamReportJobs(self.queue,execute)
            reports.run('d'*32,'event_000',None,'cpu-template',None)
            path=self.event_directory/'report-team-v1/cpu_retrieval.result.json'
            changed=read_json(path);changed['report_json']['sources'][0]['content']='changed'
            save(path,changed)
            with self.assertRaisesRegex(WorkerError,'saved_result_changed'):
                reports.run('d'*32,'event_000',None,'cpu-template',None)
        execute.assert_called_once()


if __name__=='__main__':unittest.main()
