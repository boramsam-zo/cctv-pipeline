"""Synthetic S3/Gemini responses only. No remote API, model or original media."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import socket
import ssl
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'apps/scene_description'))
sys.path.insert(0, str(ROOT/'apps/runpod_worker'))
import scene
import db
import evidence
import storage


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.reads = []
    def put_object(self, **args):
        self.objects[args['Key']] = args
    def head_object(self, **args):
        row = self.objects[args['Key']]
        return {'ContentLength': len(row['Body']), 'Metadata': row['Metadata']}
    def get(self, bucket, key, limit):
        self.reads.append(key)
        return self.objects[key]['Body'][:limit + 1]


def fixture(root):
    config = {'bucket': 'cctv-test-bucket', 'region': 'ap-northeast-2', 'job_id': 'a'*32,
              'event_id': 'event_000', 'expected_input_sha256': 'b'*64}
    event = evidence.plan([{'event_id': 'event_000', 'candidate_time_s': 3,
                           'yolo_objects': [{'class_name': 'Car', 'confidence': 0.7}]}], 25, 215)[0]
    index = {'schema_version': 'runpod-evidence-v1', 'pts_verified': True,
             'time_basis': 'seconds_from_first_decoded_frame', 'events': [event]}
    files = {'result.json': b'{}', 'runtime.json': b'{}', 'vlm_handoffs.json': b'[]',
             'evidence.json': scene.encoded(index), event['clip']: b'\0\0\0\x18ftypisom-synthetic',
             event['frame']: b'\x89PNG\r\n\x1a\n-synthetic'}
    for name, data in files.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    evidence.write_manifest(root, config['expected_input_sha256'])
    s3 = MemoryS3()
    storage.publish(root, config['job_id'], {'bucket': config['bucket'], 'region': config['region'],
                    'prefix': 'cctv-evidence'}, s3)
    return config, event, s3


def response(assessment='uncertain'):
    content = {'accident_suspicion': assessment, 'incident_types': [], 'summary': '합성 테스트 설명',
               'observations': [{'text': '합성 관찰', 'clip_times_seconds': [3.0], 'asset_refs': ['clip', 'frame']}],
               'uncertainties': ['실제 영상 검증이 아닌 합성 시험']}
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(content)}]}}],
            'usageMetadata': {'promptTokenCount': 12, 'candidatesTokenCount': 7}, 'modelVersion': 'test-model'}


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = self.root/'original'
        self.original.mkdir()
        self.config, self.event, self.s3 = fixture(self.original)
        self.bundle = self.root/'input'
        self.out = self.root/'description'
    def tearDown(self):
        self.temp.cleanup()
    def fetch(self):
        return scene.fetch(self.config, self.bundle, self.s3)
    def describe(self, raw=None):
        return scene.describe(self.bundle, self.out, 'gemini-test', 4, 'test-key',
                              lambda *args: response() if raw is None else raw)
    def mutate_manifest(self, mutate):
        key = f"cctv-evidence/{self.config['job_id']}/manifest.json"
        document = scene.parse(self.s3.objects[key]['Body'])
        mutate(document)
        self.s3.objects[key]['Body'] = scene.encoded(document)

    def test_actual_producer_contract_and_selected_objects_only(self):
        receipt = self.fetch()
        self.assertEqual(receipt['gemini_api_calls'], 0)
        self.assertEqual(len(self.s3.reads), 4)
        self.assertFalse(any(key.endswith('result.json') for key in self.s3.reads))
        self.assertEqual(scene.load_bundle(self.bundle)[2], self.event)

    def test_other_job_and_original_sha_rejected(self):
        for field in ['provider_job_id', 'input_sha256', 'bucket', 'region']:
            with self.subTest(field=field):
                config, _, s3 = fixture(self.original)
                key = f"cctv-evidence/{config['job_id']}/manifest.json"
                data = scene.parse(s3.objects[key]['Body']); data[field] = 'different'
                s3.objects[key]['Body'] = scene.encoded(data)
                with self.assertRaises(ValueError): scene.fetch(config, self.root/field, s3)

    def test_cross_event_s3_key_rejected_before_reading_it(self):
        name = self.event['clip']
        self.mutate_manifest(lambda m: m['files'][name].update(key='other-job/evidence/clip.mp4'))
        with self.assertRaises(ValueError): self.fetch()
        self.assertFalse(any(k.startswith('other-job') for k in self.s3.reads))

    def test_corrupted_media_and_incomplete_receipt(self):
        key = f"cctv-evidence/{self.config['job_id']}/{self.event['clip']}"
        self.s3.objects[key]['Body'] = b'corrupt'
        with self.assertRaises(ValueError): self.fetch()
        self.assertFalse((self.bundle/'fetch.complete.json').exists())

    def test_manifest_oversize_fails_without_media_fetch(self):
        self.mutate_manifest(lambda m: m['files'][self.event['clip']].update(bytes=scene.MEDIA_LIMIT+1))
        with self.assertRaises(ValueError): self.fetch()
        self.assertEqual(len(self.s3.reads), 2)

    def test_local_tampering_rejected_before_gemini(self):
        self.fetch(); (self.bundle/self.event['frame']).write_bytes(b'changed')
        calls = []
        with self.assertRaises(ValueError):
            scene.describe(self.bundle,self.out,'gemini-test',4,'key',lambda *args: calls.append(args))
        self.assertFalse(calls)

    def test_completion_receipt_cannot_be_mixed_with_other_download(self):
        self.fetch()
        path=self.bundle/'fetch.complete.json'
        receipt=scene.parse(path.read_bytes());receipt['manifest_sha256']='0'*64
        path.write_bytes(scene.encoded(receipt))
        with self.assertRaises(ValueError):scene.load_bundle(self.bundle)

    def test_s3_cli_keeps_keys_as_arguments_and_ranges_download(self):
        def execute(args, **kwargs):
            self.assertIn('bytes=0-8', args)
            self.assertEqual(args[args.index('--key')+1], 'cctv-evidence/path with spaces/frame.png')
            self.assertNotIn('shell', kwargs)
            Path(args[-1]).write_bytes(b'12345678')
            return subprocess.CompletedProcess(args,0,b'{}',b'')
        with patch('subprocess.run',side_effect=execute):
            value=scene.AwsCliReader('ap-northeast-2','cctv').get('test-bucket','cctv-evidence/path with spaces/frame.png',8)
        self.assertEqual(value,b'12345678')

    def test_payload_contains_real_bytes_fps_and_no_bucket_or_key(self):
        self.fetch()
        _, _, event, media, _ = scene.load_bundle(self.bundle)
        request = scene.build_request(event, media, 4)
        parsed = scene.parse(request)
        video = parsed['contents'][0]['parts'][2]
        self.assertEqual(video['videoMetadata']['fps'], 4)
        import base64
        self.assertEqual(base64.b64decode(video['inlineData']['data']), media['clip'])
        self.assertNotIn(self.config['bucket'].encode(), request)
        self.assertNotIn(b'test-key', request)

    def test_success_provenance_and_database_validation(self):
        self.fetch(); result = self.describe()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['description']['accident_suspicion'], 'uncertain')
        self.assertEqual(result['human_review'], 'unreviewed')
        self.assertEqual(result['usage']['promptTokenCount'], 12)
        self.assertEqual(db.validate_result(result), scene.sha(scene.encoded(result)))
        self.assertNotIn(b'test-key', (self.out/'request.json').read_bytes())
        self.assertFalse(result['database_loaded'])

    def test_reusing_output_does_not_call_gemini_again(self):
        self.fetch(); self.describe(); calls=[]
        with self.assertRaises(FileExistsError):
            scene.describe(self.bundle,self.out,'gemini-test',4,'key',lambda *args: calls.append(args))
        self.assertFalse(calls)

    def test_failed_or_blocked_response_is_not_no_accident(self):
        self.fetch()
        for i, raw in enumerate([{'promptFeedback': {'blockReason': 'SAFETY'}},
                                {'candidates': []}, {'candidates':[{'finishReason':'MAX_TOKENS'}]}]):
            out=self.root/f'failed-{i}'
            with self.assertRaises(ValueError):
                scene.describe(self.bundle,out,'gemini-test',4,'key',lambda *args:raw)
            self.assertTrue((out/'response.raw.json').is_file())
            self.assertTrue((out/'failure.json').is_file())
            self.assertFalse((out/'result.json').exists())

    def test_transport_failure_keeps_intent_without_retry(self):
        self.fetch(); calls=[]
        def failed(*args):
            calls.append(args); raise TimeoutError('synthetic')
        with self.assertRaises(TimeoutError):
            scene.describe(self.bundle,self.out,'gemini-test',4,'key',failed)
        self.assertEqual(len(calls),1)
        self.assertTrue((self.out/'request.json').exists())
        self.assertEqual(scene.parse((self.out/'failure.json').read_bytes())['provider_completion'],'unconfirmed')

    def test_http_diagnostics_never_expose_key(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=urllib.error.HTTPError(
                'https://example.test?key=secret-value', 403, 'secret-value', {}, None)):
            with self.assertRaises(RuntimeError) as error:
                scene.call_gemini('gemini-test', b'{}', 'secret-value')
        self.assertEqual(str(error.exception),'Gemini HTTP 403; no automatic retry')

    def test_http_error_records_only_allowlisted_fields(self):
        self.fetch()
        body = {'error': {'status': 'INVALID_ARGUMENT', 'message': 'secret-value',
                         'details': [{'reason': 'API_KEY_INVALID', 'metadata': {'key': 'secret-value'}},
                                     {'reason': 'secret-value'}]}}
        failure = urllib.error.HTTPError('https://example.test?key=secret-value', 400,
                                         'secret-value', {}, io.BytesIO(scene.encoded(body)))
        with patch('urllib.request.OpenerDirector.open', side_effect=failure):
            with self.assertRaises(scene.GeminiError):
                scene.describe(self.bundle, self.out, 'gemini-test', 4, 'secret-value')
        record = scene.parse((self.out/'failure.json').read_bytes())
        self.assertEqual(record['diagnostic'], {'error_code': 'gemini_http_400', 'http_status': 400,
                          'api_status': 'INVALID_ARGUMENT', 'reasons': ['API_KEY_INVALID']})
        self.assertNotIn('secret-value', json.dumps(record))
        self.assertTrue(failure.closed)

    def test_malformed_http_body_keeps_status(self):
        for body in (b'not json secret-value', b'{"error": {"status": [], "details": 4}}', b'[]'):
            with self.subTest(body=body):
                failure = urllib.error.HTTPError('https://example.test', 403, 'secret-value', {}, io.BytesIO(body))
                with patch('urllib.request.OpenerDirector.open', side_effect=failure):
                    with self.assertRaises(scene.GeminiError) as error:
                        scene.check_model_access('gemini-test', 'secret-value')
                self.assertEqual(error.exception.diagnostic, {'error_code': 'gemini_http_403', 'http_status': 403})

    def test_transport_categories_do_not_include_exception_text(self):
        for cause, code in [(ssl.SSLCertVerificationError('secret-value'), 'tls_certificate_error'),
                            (socket.gaierror('secret-value'), 'dns_error'),
                            (TimeoutError('secret-value'), 'timeout')]:
            with self.subTest(code=code), patch('urllib.request.OpenerDirector.open', side_effect=urllib.error.URLError(cause)):
                with self.assertRaises(scene.GeminiError) as error:
                    scene.check_model_access('gemini-test', 'secret-value')
                self.assertEqual(error.exception.diagnostic, {'error_code': 'gemini_' + code})

    def test_model_check_is_metadata_get_without_generation_or_raw_output(self):
        def respond(request, **options):
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            self.assertEqual(request.full_url, 'https://generativelanguage.googleapis.com/v1beta/models/gemini-test')
            self.assertEqual(request.get_header('X-goog-api-key'), 'secret-value')
            self.assertEqual(options['timeout'], 20)
            return io.BytesIO(scene.encoded({'name': 'models/gemini-test', 'supportedGenerationMethods': ['generateContent'],
                                            'description': 'secret-value'}))
        with patch('urllib.request.OpenerDirector.open', side_effect=respond) as network:
            result = scene.check_model_access('gemini-test', 'secret-value')
        self.assertEqual(network.call_count, 1)
        self.assertEqual(result, {'status': 'metadata_access_ok', 'supports_generate_content': True,
                                  'generation_validated': False})
        with patch('urllib.request.OpenerDirector.open') as network:
            with self.assertRaises(ValueError): scene.check_model_access('gemini-test?key=bad', 'secret-value')
            network.assert_not_called()

    def test_text_probe_sends_only_fixed_prompt_once_and_sanitizes_response(self):
        def respond(request, **options):
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(json.loads(request.data), {
                'contents': [{'role': 'user', 'parts': [{'text': 'Reply only with OK.'}]}],
                'generationConfig': {'maxOutputTokens': 256}})
            self.assertEqual(request.get_header('X-goog-api-key'), 'secret-value')
            return io.BytesIO(scene.encoded({
                'candidates': [{'content': {'parts': [{'text': 'secret-value'}]}}],
                'usageMetadata': {'promptTokenCount': 5, 'candidatesTokenCount': 1,
                                  'totalTokenCount': 6, 'unknown': 'secret-value'}}))
        with patch('urllib.request.OpenerDirector.open', side_effect=respond) as network:
            result = scene.probe_text_generation('gemini-test', 'secret-value')
        self.assertEqual(network.call_count, 1)
        self.assertEqual(result['status'], 'text_generation_ok')
        self.assertEqual(result['usage']['totalTokenCount'], 6)
        self.assertFalse(result['video_generation_validated'])
        self.assertNotIn('secret-value', json.dumps(result))

    def test_text_probe_does_not_treat_empty_or_thought_only_response_as_success(self):
        for raw in [{}, {'candidates': None}, {'candidates': {'unexpected': True}},
                    {'candidates': [{'content': None}]},
                    {'candidates': [{'content': {'parts': None}}]},
                    {'candidates': [{'content': {'parts': [{'text': 'thinking', 'thought': True}]}}]}]:
            with self.subTest(raw=raw), patch.object(scene, 'call_gemini', return_value=raw):
                self.assertEqual(scene.probe_text_generation('gemini-test', 'synthetic')['status'],
                                 'text_generation_incomplete')

    def test_text_probe_keeps_503_without_retry(self):
        error = urllib.error.HTTPError('https://example.invalid', 503, 'secret-value', {},
                                       io.BytesIO(b'{"error":{"status":"UNAVAILABLE"}}'))
        with patch('urllib.request.OpenerDirector.open', side_effect=error) as network:
            with self.assertRaises(scene.GeminiError) as caught:
                scene.probe_text_generation('gemini-test', 'secret-value')
        self.assertEqual(network.call_count, 1)
        self.assertEqual(caught.exception.diagnostic['http_status'], 503)
        self.assertNotIn('secret-value', str(caught.exception))

    def test_invalid_time_unknown_asset_and_unexplained_uncertainty(self):
        original=scene.parse(response()['candidates'][0]['content']['parts'][0]['text'])
        for mutate in [lambda x:x['observations'][0].update(clip_times_seconds=[99]),
                       lambda x:x['observations'][0].update(clip_times_seconds=[float('nan')]),
                       lambda x:x['observations'][0].update(asset_refs=['other-event']),
                       lambda x:x.update(uncertainties=[]),
                       lambda x:x.update(accident_suspicion='confirmed_accident')]:
            value=copy.deepcopy(original);mutate(value)
            with self.assertRaises(ValueError):scene.validate_description(value,self.event)

    def test_source_time_conversion(self):
        event=dict(self.event, source_start_s=10,source_end_s=16)
        raw=scene.parse(response()['candidates'][0]['content']['parts'][0]['text'])
        result=scene.validate_description(raw,event)
        self.assertEqual(result['observations'][0]['source_times_seconds'],[13])

    def test_rollover_is_suspected_without_collision(self):
        raw = scene.parse(response()['candidates'][0]['content']['parts'][0]['text'])
        raw.update(accident_suspicion='suspected', incident_types=['rollover'],
                   summary='차량이 단독 전도됨. 다른 차량과 접촉은 보이지 않음.')
        value = scene.validate_description(raw, self.event)
        self.assertEqual(value['accident_suspicion'], 'suspected')
        self.assertEqual(value['incident_types'], ['rollover'])
        for changes in [dict(accident_suspicion='not_observed'), dict(incident_types=[]),
                        dict(incident_types=['unknown', 'rollover']), dict(incident_types=['rollover']*2)]:
            with self.assertRaises(ValueError):
                scene.validate_description(dict(raw, **changes), self.event)

    def test_v1_result_keeps_collision_only_meaning(self):
        self.fetch(); result = self.describe()
        result['schema_version'] = 'cctv-scene-v1'
        result['description'].pop('accident_suspicion')
        result['description'].pop('incident_types')
        result['description']['assessment'] = 'no_visible_collision'
        db.validate_result(result)
        self.assertNotIn('accident_suspicion', result['description'])

    def test_input_path_and_model_validation(self):
        config=dict(self.config,event_id='../event_000')
        with self.assertRaises(ValueError):scene.validate_config(config)
        self.fetch()
        for model in ['gemini-embedding-001','gemini-test?key=bad','../outside']:
            with self.assertRaises(ValueError):scene.describe(self.bundle,self.out,model,4,'key')

    def test_database_rejects_cross_event_or_altered_observation(self):
        self.fetch(); original=self.describe()
        for mutate in [lambda r:r.update(event_id='event_001'),
                       lambda r:r['description']['observations'][0].update(source_times_seconds=[100]),
                       lambda r:r.update(status='failed')]:
            result=copy.deepcopy(original);mutate(result)
            with self.assertRaises(ValueError):db.validate_result(result)


if __name__ == '__main__':
    unittest.main()
