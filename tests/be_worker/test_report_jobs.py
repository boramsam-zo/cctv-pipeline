"""Synthetic restart/citation tests; no model, network, or database calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from apps.be_worker.common import WorkerError, locked, read_json, save
from apps.be_worker.report_jobs import Journal, parse_report, query_from_description, retrieve

DOCS = [dict(citation_id='D1', content='도로에서 안전하게 운전해야 한다.',
             metadata={'source_url': 'https://example.invalid/law'})]


def raw_report():
    value = dict(scene_summary='충돌 의심 장면이다.', document_findings=[dict(
        citation_id='D1', quote='안전하게 운전해야 한다.', relevance='insufficient',
        interpretation='과실을 판단할 근거는 부족하다.')], limitations=['발생일 미확인'], followup=['영상 검수'])
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(value)}]}}]}


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'report'
        self.saved = None
        self.settings = {'analysis_id': 'synthetic', 'reference_date': '2026-09-30'}
    def persist(self, state):
        self.saved = copy.deepcopy(state)
    def journal(self):
        existing = {'settings': self.settings, 'state': self.saved} if self.saved else None
        return Journal(self.path, self.settings, self.persist, existing)
    def test_restart_reuses_each_completed_external_stage(self):
        call = Mock(return_value={'vector': [1]})
        j = self.journal()
        self.assertEqual(j.step('embedding', {'query': 'q'}, call, lambda r:r, external=True), {'vector':[1]})
        j = self.journal()
        j.step('embedding', {'query': 'q'}, call, lambda r:r, external=True)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(j.state['calls'], {'embedding': 1})
    def test_uncertain_call_is_not_retried(self):
        call = Mock(side_effect=TimeoutError())
        with self.assertRaises(WorkerError):
            self.journal().step('generation', {}, call, lambda r:r, external=True)
        with self.assertRaisesRegex(WorkerError, 'response_missing_no_auto_retry'):
            self.journal().step('generation', {}, call, lambda r:r, external=True)
        self.assertEqual(call.call_count, 1)
    def test_reservation_precedes_call_and_is_not_repeated_for_saved_response(self):
        events = []
        reserve = Mock(side_effect=lambda: events.append('reserved'))
        call = Mock(side_effect=lambda: events.append('called') or {'vector':[1]})
        self.journal().step('embedding', {}, call, lambda r:r, external=True, reserve=reserve)
        self.journal().step('embedding', {}, call, lambda r:r, external=True, reserve=reserve)
        self.assertEqual(events, ['reserved','called'])
        self.assertEqual(reserve.call_count, 1)
    def test_rejected_reservation_makes_no_request_and_no_call_intent(self):
        call = Mock()
        reserve = Mock(side_effect=WorkerError('trial_call_limit_reached'))
        j = self.journal()
        with self.assertRaisesRegex(WorkerError, 'trial_call_limit_reached'):
            j.step('generation', {}, call, lambda r:r, external=True, reserve=reserve)
        call.assert_not_called()
        self.assertFalse((self.path/'generation.request.json').exists())
        self.assertEqual(j.state['calls'], {})
    def test_crash_after_committed_reservation_cannot_make_later_call(self):
        consumed = []
        def reserve():
            if consumed: raise WorkerError('trial_call_already_claimed')
            consumed.append(True)
        call = Mock()
        j = self.journal()
        with patch('apps.be_worker.report_jobs.save', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                j.step('generation', {}, call, lambda r:r, external=True, reserve=reserve)
        with self.assertRaisesRegex(WorkerError, 'trial_call_already_claimed'):
            self.journal().step('generation', {}, call, lambda r:r, external=True, reserve=reserve)
        call.assert_not_called()
    def test_saved_raw_response_resumes_validation_without_call(self):
        j = self.journal()
        save(self.path/'generation.request.json', {})
        save(self.path/'generation.response.json', raw_report())
        call = Mock(side_effect=AssertionError('must not generate'))
        preflight = Mock(side_effect=AssertionError('must not read credentials'))
        report = j.step('generation', {}, call, lambda r:parse_report(r,DOCS),
                        external=True, preflight=preflight)
        self.assertEqual(report['document_coverage'], 'insufficient')
        call.assert_not_called()
        preflight.assert_not_called()
    def test_database_failure_after_response_does_not_regenerate(self):
        j = self.journal()
        calls = Mock(return_value={'answer': 42})
        def fail_after_result(state):
            if 'generation' in state['stages']: raise RuntimeError('DB unavailable')
            self.persist(state)
        j.persist = fail_after_result
        with self.assertRaises(RuntimeError):
            j.step('generation', {}, calls, lambda r:r, external=True)
        self.journal().step('generation', {}, calls, lambda r:r, external=True)
        self.assertEqual(calls.call_count, 1)
    def test_readonly_failure_retries_only_failed_stage(self):
        j = self.journal()
        embed = Mock(return_value={'vector':[1]})
        j.step('embedding', {}, embed, lambda r:r, external=True)
        search = Mock(side_effect=[RuntimeError('DB down'), {'documents':[]}])
        with self.assertRaises(WorkerError):j.step('retrieval', {}, search, lambda r:r)
        j = self.journal()
        j.step('embedding', {}, embed, lambda r:r, external=True)
        self.assertEqual(j.step('retrieval', {}, search, lambda r:r), {'documents':[]})
        self.assertEqual(embed.call_count, 1)
    def test_changed_settings_and_result_rejected(self):
        j = self.journal()
        j.step('embedding', {}, lambda:{'vector':[1]}, lambda r:r, external=True)
        save(self.path/'embedding.result.json', {'vector':[2]})
        with self.assertRaisesRegex(WorkerError, 'saved_result_changed'):
            self.journal().step('embedding', {}, Mock(), lambda r:r, external=True)
        with self.assertRaisesRegex(WorkerError, 'settings_changed'):
            Journal(self.path, {'changed':True}, self.persist)
    def test_missing_spool_and_database_ahead_stop(self):
        self.journal()
        self.saved['calls']['embedding'] = 1
        with self.assertRaisesRegex(WorkerError, 'database_ahead'):
            self.journal()
        with self.assertRaisesRegex(WorkerError, 'spool_missing'):
            Journal(self.path/'missing', self.settings, self.persist,
                    {'settings': self.settings, 'state':self.saved})
    def test_same_job_lock_blocks_concurrent_click(self):
        self.path.mkdir()
        with locked(self.path):
            with self.assertRaisesRegex(WorkerError, 'job_already_locked'):
                with locked(self.path): self.fail('second writer')


class ReportTests(unittest.TestCase):
    def change(self, raw, key, value):
        part=raw['candidates'][0]['content']['parts'][0]
        body=json.loads(part['text']);body['document_findings'][0][key]=value
        part['text']=json.dumps(body)
        return raw
    def test_scene_report_cli_is_registered(self):
        import subprocess, sys
        result=subprocess.run([sys.executable, 'apps/be_worker/job_queue.py',
                               'scene-report', '--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--reference-date',result.stdout)
    def test_unknown_citation_and_fabricated_quote_rejected(self):
        for key,value in [('citation_id','D9'),('quote','존재하지 않는 구절')]:
            with self.assertRaises(WorkerError):parse_report(self.change(raw_report(),key,value),DOCS)
    def test_truncated_response_and_missing_document_assessment_rejected(self):
        raw=raw_report();raw['candidates'][0]['finishReason']='MAX_TOKENS'
        with self.assertRaises(WorkerError):parse_report(raw,DOCS)
        with self.assertRaises(WorkerError):parse_report(raw_report(),DOCS+[dict(DOCS[0],citation_id='D2')])
    def test_no_documents_is_not_normal_or_failure(self):
        raw=raw_report();part=raw['candidates'][0]['content']['parts'][0]
        body=json.loads(part['text']);body['document_findings']=[];part['text']=json.dumps(body)
        answer=parse_report(raw,[])
        self.assertEqual(answer['document_coverage'],'no_documents')
        self.assertTrue(answer['review_required'])
    def test_query_is_deterministic_and_uses_only_description(self):
        result={'description':dict(accident_suspicion='suspected',incident_types=['collision'],
            summary='밴과 오토바이', observations=[{'text':'접촉 의심'}]), 'filename':'accident_label.mp4'}
        query=query_from_description(result)
        self.assertEqual(query,query_from_description(result))
        self.assertNotIn(result['filename'],query)
        self.assertIn('접촉 의심',query)
    def test_retrieval_reuses_existing_search_and_keeps_source(self):
        row=dict(chunk_id='c1',content='원문',metadata={'source_url':'https://example.invalid'},cosine_distance=0.5)
        with patch('apps.be_worker.report_jobs.index.search_vector',return_value=[row]) as search:
            docs=retrieve(object(),[1]*768,'2026-09-30')
        self.assertEqual(docs[0]['citation_id'],'D1')
        self.assertEqual(docs[0]['content'],'원문')
        self.assertEqual(search.call_count,1)



class ReportPipelineTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_scene_jobs import SceneJobTests
        self.fixture = SceneJobTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.fetch(); self.fixture.describe()
        self.scene_result = next(iter(self.fixture.results.values()))
        self.db_state = None
        self.fixture.store.connection = self
        from apps.be_worker.report_jobs import ReportJobs
        self.jobs = ReportJobs(self.fixture.coordinator)
        import types
        class Jsonb:
            def __init__(self, obj):self.obj=copy.deepcopy(obj)
        modules={'psycopg.types.json':types.SimpleNamespace(Jsonb=Jsonb)}
        self.module_patch=patch.dict(sys.modules,modules);self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
    def execute(self, sql, params=()):
        from apps.scene_description.db import validate_result
        answer=None
        if sql.startswith('SELECT payload, result_sha256'):
            answer=dict(payload=self.scene_result,result_sha256=validate_result(self.scene_result))
        elif sql.startswith('SELECT has_table_privilege'):answer={'ok':True}
        elif 'count(*) AS total' in sql:answer={'total':2,'compatible':2}
        elif sql.startswith('INSERT INTO cctv_scene.report_runs'):
            self.db_state={'settings':params[3].obj,'state':params[4].obj}
        elif sql.startswith('SELECT settings, state'):answer=copy.deepcopy(self.db_state)
        elif sql!='SELECT state FROM cctv_scene.report_runs LIMIT 0':raise AssertionError(sql)
        return Mock(fetchone=lambda:answer)
    def run_report(self, key_loader=None, report_model='gemini-test', **kwargs):
        return self.jobs.run(self.fixture.identifier,'event_000','2026-09-30',report_model,
                             key_loader if key_loader is not None else lambda:'fake-key', **kwargs)
    def report_directory(self):
        _, directory = self.jobs.scenes._context(self.fixture.identifier, 'event_000')
        return directory/'scenes'/'event_000'/'report-v1'
    def test_full_saved_scene_to_report_db_and_repeat_without_api(self):
        first_key = Mock(return_value='fake-key')
        missing_key = Mock(side_effect=WorkerError('gemini_key_not_configured'))
        with patch('apps.be_worker.report_jobs.index.embed',return_value=[1]*768) as embed, \
             patch('apps.be_worker.report_jobs.retrieve',return_value=DOCS) as search, \
             patch('apps.be_worker.report_jobs.scene.call_gemini',return_value=raw_report()) as generate:
            first=self.run_report(first_key)
            settings = copy.deepcopy(self.db_state['settings'])
            stored_result = copy.deepcopy(self.db_state['state']['result'])
            second=self.run_report(missing_key)
        self.assertEqual(first,second)
        self.assertEqual(self.db_state['settings'], settings)
        self.assertEqual(self.db_state['state']['result'], stored_result)
        self.assertEqual(self.db_state['state']['result_sha256'], first['result_sha256'])
        self.assertEqual((embed.call_count,search.call_count,generate.call_count),(1,1,1))
        self.assertEqual(first_key.call_count, 2)
        missing_key.assert_not_called()
        self.assertEqual(first['calls'],{'embedding':1,'generation':1})
        self.assertTrue(first['database_readback_verified'])
        self.assertEqual(self.fixture.calls,1)  # only original synthetic scene generation
        self.assertEqual(self.db_state['state']['result']['documents'],DOCS)
    def test_changed_report_model_preserves_original_files_and_database(self):
        changed_key = Mock(return_value='replacement-key')
        with patch('apps.be_worker.report_jobs.index.embed',return_value=[1]*768) as embed, \
             patch('apps.be_worker.report_jobs.retrieve',return_value=DOCS) as search, \
             patch('apps.be_worker.report_jobs.scene.call_gemini',return_value=raw_report()) as generate:
            original = self.run_report()
            saved_db = copy.deepcopy(self.db_state)
            saved_files = {p.name: p.read_bytes() for p in self.report_directory().iterdir() if p.is_file()}
            with self.assertRaisesRegex(WorkerError, 'report_settings_changed_original_preserved'):
                self.run_report(changed_key, report_model='gemini-other')
        self.assertEqual(self.db_state, saved_db)
        self.assertEqual({p.name: p.read_bytes() for p in self.report_directory().iterdir() if p.is_file()},
                         saved_files)
        self.assertEqual(self.db_state['state']['result_sha256'], original['result_sha256'])
        self.assertEqual((embed.call_count, search.call_count, generate.call_count), (1, 1, 1))
        changed_key.assert_not_called()
    def test_database_failure_after_generation_receipt_resumes_without_key_or_call(self):
        execute = self.execute
        failed_once = False
        def fail_generation_commit(sql, params=()):
            nonlocal failed_once
            if (sql.startswith('INSERT INTO cctv_scene.report_runs')
                    and 'generation' in params[4].obj['stages'] and not failed_once):
                failed_once = True
                raise RuntimeError('synthetic_database_unavailable')
            return execute(sql, params)
        missing_key = Mock(side_effect=WorkerError('gemini_key_not_configured'))
        with patch.object(self, 'execute', side_effect=fail_generation_commit), \
             patch('apps.be_worker.report_jobs.index.embed',return_value=[1]*768) as embed, \
             patch('apps.be_worker.report_jobs.retrieve',return_value=DOCS) as search, \
             patch('apps.be_worker.report_jobs.scene.call_gemini',return_value=raw_report()) as generate:
            with self.assertRaisesRegex(RuntimeError, 'synthetic_database_unavailable'):
                self.run_report()
            pending = read_json(self.report_directory()/'state.json')
            self.assertIn('generation', pending['stages'])
            self.assertNotIn('generation', self.db_state['state']['stages'])
            receipts = {p.name: p.read_bytes() for p in self.report_directory().glob('generation.*.json')}
            self.jobs = type(self.jobs)(self.fixture.coordinator)
            resumed = self.run_report(missing_key)
            repeated = self.run_report(missing_key)
        self.assertTrue(failed_once)
        self.assertEqual(resumed, repeated)
        self.assertEqual(resumed['calls'], {'embedding': 1, 'generation': 1})
        self.assertEqual(self.db_state['settings'], pending['settings'])
        self.assertEqual(self.db_state['state']['stages']['generation']['sha256'],
                         pending['stages']['generation']['sha256'])
        self.assertEqual(self.db_state['state']['result']['draft'],
                         pending['stages']['generation']['result'])
        self.assertEqual({p.name: p.read_bytes() for p in self.report_directory().glob('generation.*.json')},
                         receipts)
        self.assertEqual((embed.call_count, search.call_count, generate.call_count), (1, 1, 1))
        missing_key.assert_not_called()
    def test_generation_timeout_preserves_embedding_and_search(self):
        changed_key = Mock(return_value='replacement-key')
        with patch('apps.be_worker.report_jobs.index.embed',return_value=[1]*768) as embed, \
             patch('apps.be_worker.report_jobs.retrieve',return_value=DOCS) as search, \
             patch('apps.be_worker.report_jobs.scene.call_gemini',side_effect=TimeoutError()) as generate:
            with self.assertRaises(WorkerError):self.run_report()
            with self.assertRaisesRegex(WorkerError,'response_missing_no_auto_retry'):
                self.run_report(changed_key)
        self.assertEqual((embed.call_count,search.call_count,generate.call_count),(1,1,1))
        changed_key.assert_not_called()
    def test_missing_key_before_embedding_records_no_request_and_can_resume(self):
        missing_key = Mock(side_effect=WorkerError('gemini_key_not_configured'))
        replacement = Mock(return_value='replacement-key')
        with patch('apps.be_worker.report_jobs.index.embed',return_value=[1]*768) as embed, \
             patch('apps.be_worker.report_jobs.retrieve',return_value=DOCS) as search, \
             patch('apps.be_worker.report_jobs.scene.call_gemini',return_value=raw_report()) as generate:
            with self.assertRaisesRegex(WorkerError, 'gemini_key_not_configured'):
                self.run_report(missing_key)
            self.assertEqual(self.db_state['state']['calls'], {})
            self.assertEqual(list(self.report_directory().glob('*.request.json')), [])
            self.assertEqual((embed.call_count, search.call_count, generate.call_count), (0, 0, 0))
            result = self.run_report(replacement)
        self.assertEqual(result['calls'], {'embedding': 1, 'generation': 1})
        self.assertEqual((embed.call_count, search.call_count, generate.call_count), (1, 1, 1))
        self.assertEqual(replacement.call_count, 2)
        self.assertEqual(embed.call_args.kwargs['key'], 'replacement-key')
        self.assertEqual(generate.call_args.args[2], 'replacement-key')
        self.assertNotIn('replacement-key', json.dumps(self.db_state))
    def test_missing_generation_key_preserves_embedding_and_resumes_once(self):
        key_loader = Mock(side_effect=['first-key', WorkerError('gemini_key_not_configured')])
        replacement = Mock(return_value='replacement-key')
        with patch('apps.be_worker.report_jobs.index.embed',return_value=[1]*768) as embed, \
             patch('apps.be_worker.report_jobs.retrieve',return_value=DOCS) as search, \
             patch('apps.be_worker.report_jobs.scene.call_gemini',return_value=raw_report()) as generate:
            with self.assertRaisesRegex(WorkerError, 'gemini_key_not_configured'):
                self.run_report(key_loader)
            self.assertEqual(self.db_state['state']['calls'], {'embedding': 1})
            self.assertFalse((self.report_directory()/'generation.request.json').exists())
            self.assertEqual((embed.call_count, search.call_count, generate.call_count), (1, 1, 0))
            result = self.run_report(replacement)
        self.assertEqual(result['calls'], {'embedding': 1, 'generation': 1})
        self.assertEqual((embed.call_count, search.call_count, generate.call_count), (1, 1, 1))
        replacement.assert_called_once_with()
        self.assertEqual(generate.call_args.args[2], 'replacement-key')
    def test_embedding_timeout_blocks_key_reload_and_retry(self):
        changed_key = Mock(return_value='replacement-key')
        with patch('apps.be_worker.report_jobs.index.embed',side_effect=TimeoutError()) as embed, \
             patch('apps.be_worker.report_jobs.scene.call_gemini') as generate:
            with self.assertRaisesRegex(WorkerError, 'embedding_failed_inspect_saved_request'):
                self.run_report()
            with self.assertRaisesRegex(WorkerError, 'embedding_response_missing_no_auto_retry'):
                self.run_report(changed_key)
        self.assertEqual(embed.call_count, 1)
        generate.assert_not_called()
        changed_key.assert_not_called()
    def test_changed_scene_db_stops_before_billable_calls(self):
        self.scene_result=copy.deepcopy(self.scene_result)
        self.scene_result['description']['summary']='changed'
        with patch('apps.be_worker.report_jobs.index.embed') as embed:
            with self.assertRaisesRegex(WorkerError,'scene_database_mismatch'):self.run_report()
        embed.assert_not_called()
    def test_expected_scene_settings_checked_before_keys_or_external_calls(self):
        expected = dict(model=self.scene_result['model'], fps=self.scene_result['video_sample_fps'],
                        prompt_version=self.scene_result['prompt_version'])
        key = Mock(return_value='synthetic-key')
        with patch('apps.be_worker.report_jobs.index.embed', return_value=[1]*768) as embed, \
             patch('apps.be_worker.report_jobs.retrieve', return_value=DOCS), \
             patch('apps.be_worker.report_jobs.scene.call_gemini', return_value=raw_report()) as generate:
            for field, wrong in [('model', 'gemini-other'), ('fps', 2), ('prompt_version', 'different')]:
                with self.subTest(field=field):
                    with self.assertRaisesRegex(WorkerError, 'report_scene_settings_mismatch'):
                        self.run_report(key, expected_scene=dict(expected, **{field: wrong}))
            key.assert_not_called()
            embed.assert_not_called()
            generate.assert_not_called()
            self.assertIsNone(self.db_state)
            result = self.run_report(key, expected_scene=expected)
        self.assertEqual(result['status'], 'report_stored')
        self.assertEqual((embed.call_count, generate.call_count), (1, 1))

if __name__=='__main__':unittest.main()
