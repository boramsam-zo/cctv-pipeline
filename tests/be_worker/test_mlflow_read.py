"""Public read boundary tests with a fake tracker; no remote writes/model calls."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from apps.be_worker.mlflow_read import MLflowReader, ReadError, public_content

RUN = 'a' * 32
TRACE = 'tr-' + 'b' * 32


class Fake:
    def __init__(self):
        self.calls = []
        self.run = NS(info=NS(run_id=RUN, experiment_id='2', lifecycle_stage='active',
            status='FINISHED', start_time=1000, end_time=4000), data=NS(
            tags={'mlflow.runName': 'X3D-S 실제 보고', 'cctv.trace_ids': TRACE,
                  'cctv.worker_job_id': 'c' * 32, 'mlflow.user': '/path/to/user'},
            params={'model_id': 'x3d_s', 'model_version': 'epoch7', 'input_sha256': 'd' * 64},
            metrics={'gpu_seconds': 2.1}))
        self.body = {'request': {'messages': [{'role': 'user', 'content': [{'text': '이 영상의 사고 상황을 설명해줘'}]}]},
                     'api_key': 'never-export', 'input_sha256': 'd' * 64}
        self.size = 300

    def get_experiment_by_name(self, name): return NS(experiment_id='2', lifecycle_stage='active')
    def get_run(self, run): self.calls.append(('get_run', run)); return self.run
    def search_runs(self, *args, **kwargs): self.calls.append(('search_runs', args, kwargs)); return [self.run]
    def search_model_versions(self, **kwargs):
        return [NS(name='cctv-x3d-s', version=1, status='READY', description='existing model',
            tags={'cctv.model_id': 'x3d_s', 'cctv.model_version': 'epoch7', 'cctv.weights_sha256': 'd'*64})]
    def list_artifacts(self, run, path=None):
        self.calls.append(('list_artifacts', path))
        if not path: return [NS(path='content-enrichment', is_dir=True), NS(path='private', is_dir=True)]
        if path == 'content-enrichment': return [NS(path=path+'/20261006-01', is_dir=True)]
        if path == 'content-enrichment/20261006-01':
            return [NS(path=path+'/vlm-request.json', is_dir=False, file_size=self.size)]
        return []
    def download_artifacts(self, run, path, directory):
        self.calls.append(('download', path))
        target = Path(directory) / 'vlm-request.json'
        target.write_text(json.dumps(self.body, ensure_ascii=False))
        return str(target)
    def get_trace(self, trace, **kwargs):
        self.calls.append(('trace', trace, kwargs))
        return NS(info=NS(trace_id=TRACE, state='OK', trace_metadata={'mlflow.sourceRun':RUN}), data=NS(spans=[NS(name='gemini_video_observation',
            span_id='one', parent_id=None, span_type='LLM', status='OK', start_time_ns=1_000_000_000,
            end_time_ns=2_000_000_000, inputs=self.body, outputs={'usage': {'totalTokenCount': 7}},
            attributes={'mlflow.chat.tokenUsage': {'input_tokens': 5, 'output_tokens': 2, 'total_tokens': 7}})]))


class ReadTests(unittest.TestCase):
    def setUp(self): self.fake = Fake(); self.reader = MLflowReader(client=self.fake)
    def test_runs_filter_only_safe_identifier_and_keep_duration_scope(self):
        value = self.reader.runs('c' * 32)
        self.assertEqual(value['runs'][0]['duration_seconds'], 3)
        self.assertEqual(value['runs'][0]['metrics']['gpu_seconds'], 2.1)
        self.assertNotIn('mlflow.user', json.dumps(value))
        with self.assertRaises(ReadError): self.reader.runs("a' OR 1=1")
        self.assertEqual(len([x for x in self.fake.calls if x[0]=='search_runs']), 1)
    def test_foreign_experiment_run_is_not_shared(self):
        self.fake.run.info.experiment_id = '99'
        with self.assertRaises(ReadError) as got: self.reader.run(RUN)
        self.assertEqual(got.exception.status, 404)
    def test_run_list_is_lazy_no_artifact_download(self):
        value = self.reader.run(RUN)
        self.assertEqual(len(value['artifacts']), 1)
        self.assertFalse(any(x[0] == 'download' for x in self.fake.calls))
        self.assertNotIn('private', json.dumps(value))
    def test_artifact_preserves_request_and_hash_but_redacts_credentials(self):
        value = self.reader.artifact(RUN, 'content-enrichment/20261006-01/vlm-request.json')
        self.assertIn('사고 상황', json.dumps(value, ensure_ascii=False))
        self.assertNotIn('never-export', json.dumps(value))
        self.assertEqual(value['content']['input_sha256'], 'd' * 64)
    def test_traversal_binary_unlisted_and_large_artifact_are_rejected(self):
        for path in ('../secret.json', 'content-enrichment/../secret.json', '/tmp/secret.json',
                     'content-enrichment/model.pt', 'content-enrichment/20261006-01/absent.json'):
            with self.subTest(path=path), self.assertRaises(ReadError): self.reader.artifact(RUN,path)
        self.fake.size = 600_000
        with self.assertRaises(ReadError): self.reader.artifact(RUN,'content-enrichment/20261006-01/vlm-request.json')
        self.assertFalse(any(x[0] == 'download' for x in self.fake.calls))
    def test_trace_requires_matching_run_and_never_flushes_tracking(self):
        with self.assertRaises(ReadError): self.reader.trace(TRACE)
        with self.assertRaises(ReadError): self.reader.trace('tr-' + 'e'*32, RUN)
        value = self.reader.trace(TRACE, RUN)
        self.assertEqual(value['spans'][0]['token_usage']['total_tokens'], 7)
        self.assertEqual(value['spans'][0]['duration_seconds'], 1)
        self.assertEqual(self.fake.calls[-1][2], {'display': False, 'flush': False})
    def test_trace_metadata_must_confirm_link_even_when_run_tag_matches(self):
        original=self.fake.get_trace
        def wrong(*args,**kwargs):
            value=original(*args,**kwargs)
            value.info.trace_metadata['mlflow.sourceRun']='f'*32
            return value
        self.fake.get_trace=wrong
        with self.assertRaises(ReadError) as got:self.reader.trace(TRACE,RUN)
        self.assertEqual(got.exception.status,404)
    def test_public_content_removes_private_paths_urls_binary_and_secrets(self):
        text = json.dumps(public_content({'request': '/path/to/user/private.key https://host/path?token=secret s3://bucket/key',
            'cctv.api_token': 'sensitive', 'token':'raw-token-value', 'path':'/mnt/private/secrets.json', 'normal': '정상 판정 아님', 'inlineData': {'data': 'secret-media'},
            'output_tokens': 42, 'input_sha256': 'd'*64}), ensure_ascii=False)
        for forbidden in ('/Users/', 'https://', 's3://', 'sensitive', 'secret-media', 'raw-token-value', '/mnt/private'): self.assertNotIn(forbidden,text)
        self.assertIn('정상 판정 아님', text); self.assertIn('42', text)
    def test_raw_error_is_never_exposed(self):
        def fail(*args, **kwargs): raise ValueError('http://internal?api_key=oops /path/to/user')
        self.fake.search_model_versions = fail
        with self.assertRaises(ReadError) as got: self.reader.models()
        self.assertEqual(str(got.exception), 'mlflow_read_unavailable')
    def test_registry_reference_has_no_model_download(self):
        model = self.reader.models()['models'][0]
        self.assertEqual(model['model_id'], 'x3d_s')
        self.assertNotIn('source', model)


if __name__ == '__main__': unittest.main()
