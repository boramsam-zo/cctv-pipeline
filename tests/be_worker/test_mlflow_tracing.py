"""Readable tracing with fake SDK and invented inputs. No model/network."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import mlflow_tracing as tracing
from apps.be_worker.common import read_json
from apps.be_worker.selection_jobs import identifier


class Client:
    def __init__(self):
        self.spans, self.calls, self.tags = [], [], []

    def get_experiment_by_name(self, name):
        self.calls.append(('experiment', name))
        return NS(experiment_id='2')

    def start_trace(self, name, **kwargs):
        self.calls.append(('start_trace', kwargs))
        self.spans.append(dict(name=name, **kwargs))
        return NS(trace_id='tr-test', span_id='root')

    def start_span(self, name, **kwargs):
        self.spans.append(dict(name=name, **kwargs))
        return NS(trace_id='tr-test', span_id=str(len(self.spans)-1))

    def end_span(self, trace_id, span_id, **kwargs):
        self.spans[int(span_id)].update(kwargs)

    def end_trace(self, trace_id, **kwargs):
        self.spans[0].update(kwargs)

    def get_trace(self, trace_id, **kwargs):
        self.calls.append(('get_trace', kwargs))
        return NS(info=NS(trace_id=trace_id), data=NS(spans=self.spans))

    def set_tag(self, run_id, name, value):
        self.tags.append((run_id, name, value))


class TracingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.row = dict(worker_job_id='a'*32, state=dict(input_sha256='b'*64,
            gpu_key='gpu-v1:'+'c'*64, analysis_profile={'classifier': {
                'model_id': 'r2plus1d_18', 'model_version': 'sotad-head-epoch7'}}))
        self.settings = {'synthetic': True}
        self.execution = identifier(dict(gpu_key=self.row['state']['gpu_key'],
            purpose='full_report', generation_settings=self.settings, reference_date=None))
        self.client = Client()

    def capture(self):
        return tracing.report_trace(self.directory, self.row, 'event_000',
                                    self.settings, None, client=self.client)

    def receipt(self):
        return next(self.directory.glob('trace-*.json'))

    def test_one_trace_links_actual_text_run_and_measured_tree_without_media_or_secrets(self):
        raw = {'modelVersion': 'gemini-test', 'candidates': [{'finishReason':'STOP',
               'content': {'parts':[{'text':'{"description":"차량이 접촉합니다."}'}]}}],
               'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 4,
                                 'totalTokenCount': 27, 'thoughtsTokenCount': 3,
                                 'secret': 'PRIVATE KEY'}}
        call = Mock(return_value=raw)
        request = json.dumps({'systemInstruction': {'parts':[{'text':'관찰한 장면만 설명하세요.'}]},
            'contents':[{'role':'user','parts':[{'text':'이 영상을 확인해주세요.'},
                {'inlineData':{'mimeType':'video/mp4','data':'PRIVATE VIDEO'},'videoMetadata':{'fps':4}}]}],
            'generationConfig':{'temperature':1.0,'maxOutputTokens':8192}}).encode()
        with self.capture():
            with tracing.stage('vlm_observation'):
                returned = tracing.gemini_caller(call, provenance={'prompt_version':'v1'})(
                    'gemini-test', request, 'PRIVATE KEY')
            with tracing.stage('report_storage', report_id='report-test') as span:
                span.output(report_id='report-test', result_sha256='d'*64)
        self.assertIs(returned, raw)
        call.assert_called_once_with('gemini-test', request, 'PRIVATE KEY')
        record = read_json(self.receipt())
        self.assertEqual(record['identity']['execution_id'], self.execution)
        self.assertEqual(self.client.calls, [])  # Delivery follows successful Run commit.
        self.assertEqual(record['spans'][1]['parent'], 0)
        self.assertTrue(all(s['started_ns'] <= s['ended_ns'] for s in record['spans']))
        tracing.link_run(self.directory, self.execution, 'run-test', client=self.client)
        stored = read_json(self.receipt())
        self.assertEqual(stored['delivery']['status'], 'recorded')
        self.assertEqual(self.client.spans[0]['run_id'], 'run-test')
        llm = self.client.spans[2]
        self.assertEqual(llm['start_time_ns'], record['spans'][1]['started_ns'])
        self.assertEqual(llm['attributes']['mlflow.chat.tokenUsage'],
                         {'input_tokens':20, 'output_tokens':4, 'total_tokens':27})
        self.assertEqual(llm['outputs']['usage']['thoughtsTokenCount'], 3)
        text = json.dumps([stored, self.client.spans])
        for private in ('PRIVATE VIDEO', 'PRIVATE KEY', 'inlineData'):
            self.assertNotIn(private, text)
        self.assertEqual(llm['inputs']['request']['messages'][0]['content'][0]['text'],
                         '관찰한 장면만 설명하세요.')
        self.assertEqual(llm['inputs']['request']['prompt_provenance']['prompt_version'], 'v1')
        self.assertEqual(llm['outputs']['response']['candidates'][0]['response_json']['description'],
                         '차량이 접촉합니다.')
        self.assertIn(('run-test', 'cctv.trace_ids', 'tr-test'), self.client.tags)

    def test_business_exception_preserved_once_without_exception_message(self):
        call = Mock(side_effect=RuntimeError('https://secret.invalid/?key=PRIVATE'))
        with self.assertRaisesRegex(RuntimeError, 'PRIVATE'):
            with self.capture():
                tracing.gemini_caller(call)('gemini-test', b'video', 'key')
        call.assert_called_once()
        value = read_json(self.receipt())
        self.assertEqual(value['status'], 'ERROR')
        self.assertEqual(value['spans'][0]['status'], 'ERROR')
        self.assertNotIn('PRIVATE', json.dumps(value))

    def test_failed_delivery_does_not_repeat_export_or_business_work(self):
        with self.capture():
            with tracing.stage('report_storage'):
                pass
        self.client.start_trace = Mock(side_effect=RuntimeError('private transport'))
        tracing.link_run(self.directory, self.execution, 'run-test', client=self.client)
        tracing.link_run(self.directory, self.execution, 'run-test', client=self.client)
        self.client.start_trace.assert_called_once()
        value = read_json(self.receipt())
        self.assertEqual(value['delivery']['status'], 'pending')
        self.assertTrue(value['delivery']['attempted'])
        self.assertNotIn('private transport', json.dumps(value))

    def test_reconciliation_keeps_original_timestamps_and_receipt(self):
        with self.capture():
            with tracing.stage('stored_once'):
                pass
        original = self.receipt().read_bytes()
        with self.capture() as collector:
            self.assertIsNone(collector)
            with tracing.stage('not_recorded_again'):
                pass
        self.assertEqual(self.receipt().read_bytes(), original)

    def test_rag_projection_keeps_actual_queries_excerpts_scores_and_report(self):
        report = {'references': [{'chunk_id':'doc-1', 'reference_id':'R1', 'content':'정차 차량의 후방 안전을 확보한다.', 'title':'사고 대응 지침'}],
                  'excluded_references': [{'chunk_id':'doc-2', 'agency_key':'police',
                      'reasons':['상황 조건 불일치'], 'content':'PRIVATE'}],
                  'retrieval': {'trace':[{'agency':'경찰', 'topics':[{'topic':'common','query':'사고 현장 교통 통제','selected_chunk_ids':['doc-1'],
                      'bm25_top20':[['doc-1',1.5]],'embedding_top20':[['doc-1',.75]]}], 'condition_actor_checks': {
                      'doc-2': {'accepted':False,'reason':'조치 주체 불일치 또는 미확인'}},
                      'selected':[{'chunk_id':'doc-1','rrf':0.013,'search':{
                          'embedding':{'rank':1,'score':0.75}, 'bm25':{'rank':2,'score':1.5}}}]}]}}
        saved = deepcopy(report)
        result = tracing.Collector({}).report_summary(report, report_markdown='# 보고 초안\n차량 접촉 관찰')
        self.assertEqual(report, saved)
        self.assertEqual(result['selected'][0]['search']['embedding']['score'], .75)
        self.assertEqual(result['excluded'][0]['reasons'], ['상황 조건 불일치'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(result['queries'][0]['query'], '사고 현장 교통 통제')
        self.assertEqual(result['selected'][0]['title'], '사고 대응 지침')
        self.assertEqual(result['selected'][0]['excerpt'], '정차 차량의 후방 안전을 확보한다.')
        self.assertEqual(result['report_markdown'], '# 보고 초안\n차량 접촉 관찰')

    def test_tracking_storage_and_bad_optional_usage_cannot_break_result(self):
        original = {'usageMetadata': None}
        with patch.object(tracing, 'save', side_effect=OSError('storage failure')):
            with self.capture():
                result = tracing.gemini_caller(lambda *_: original)('gemini-test', b'v', 'key')
        self.assertIs(result, original)


    def test_content_projection_failure_cannot_prevent_actual_call(self):
        original = {'candidates': None}
        provider = Mock(return_value=original)
        with patch.object(tracing.readable, 'gemini_request', side_effect=ValueError('private')):
            with self.capture():
                result = tracing.gemini_caller(provider)('gemini-test', b'input', 'secret')
        self.assertIs(result, original)
        provider.assert_called_once()
        record = read_json(self.receipt())
        self.assertEqual(record['spans'][0]['inputs']['request']['capture_status'], 'request_projection_failed')
        self.assertEqual(record['spans'][0]['outputs']['response']['capture_status'], 'response_projection_failed')
        self.assertNotIn('private', json.dumps(record))

    def test_saved_response_reuse_has_no_llm_tokens_or_provider_calls(self):
        with self.capture():
            with tracing.stage('saved_response_reuse', reused=True, new_model_calls=0) as item:
                item.output(scene_json={'description':'기존 응답입니다.'})
            with tracing.stage('report_storage') as item:
                item.output(report_markdown='# 저장된 보고서')
        tracing.link_run(self.directory, self.execution, 'run-test', client=self.client)
        self.assertEqual(self.client.spans[0]['outputs']['report_markdown'], '# 저장된 보고서')
        self.assertEqual(self.client.spans[1]['inputs']['new_model_calls'], 0)
        self.assertNotIn('mlflow.chat.tokenUsage', json.dumps(self.client.spans))

    def test_oversize_content_retains_measured_spans_and_marks_omissions(self):
        with self.capture():
            for n in range(40):
                with tracing.stage('retrieval_' + str(n), provider_model='gemini-test') as item:
                    item.output(report_markdown='교통 사고 문서 내용. ' * 5000,
                        report_id='original-report-id', result_sha256='d'*64,
                        usage={'promptTokenCount':20, 'candidatesTokenCount':4, 'totalTokenCount':24},
                        new_model_calls=1)
        path = self.receipt()
        self.assertLessEqual(path.stat().st_size, tracing.readable.RECORD_LIMIT)
        record = read_json(path)
        self.assertEqual(len(record['spans']), 40)
        self.assertEqual(record['content_truncation']['reason'], 'trace_record_size_limit')
        self.assertTrue(all(s['started_ns'] <= s['ended_ns'] for s in record['spans']))
        self.assertIn('[truncated:', json.dumps(record))
        for row in record['spans']:
            self.assertEqual(row['inputs']['provider_model'], 'gemini-test')
            self.assertEqual(row['outputs']['report_id'], 'original-report-id')
            self.assertEqual(row['outputs']['result_sha256'], 'd'*64)
            self.assertEqual(row['outputs']['usage']['totalTokenCount'], 24)
            self.assertEqual(row['outputs']['new_model_calls'], 1)
        tracing.link_run(self.directory, self.execution, 'run-test', client=self.client)
        self.assertEqual(len(self.client.spans), 41)

    def test_original_v1_receipt_without_content_fields_still_delivers(self):
        original = dict(schema=tracing.SCHEMA, identity={'execution_id':self.execution},
            started_ns=1, ended_ns=3, status='OK', delivery={'attempted':False},
            spans=[dict(name='report_storage',kind='CHAIN',parent=None,inputs={},
                        outputs={'report_id':'original-report'},started_ns=1,ended_ns=2,status='OK')])
        result = tracing.deliver(original, 'old-run', client=self.client)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(self.client.spans[0]['outputs'], {'report_id':'original-report'})


    def test_stage_projection_failure_is_best_effort(self):
        with patch.object(tracing, '_safe', side_effect=RuntimeError('projection broken')):
            stage = tracing.Stage('measured', 'CHAIN', None, {'scene_json': {'description':'text'}})
            stage.output(report_markdown='보고서')
        self.assertEqual(stage.value['inputs']['capture_status'], 'input_projection_failed')
        self.assertEqual(stage.value['outputs']['capture_status'], 'output_projection_failed')


if __name__ == '__main__':
    unittest.main()
