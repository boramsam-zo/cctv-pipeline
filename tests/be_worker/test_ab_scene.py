"""Stored GPU evidence -> in-memory S3 -> real BE/scene code; no model calls."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from apps.be_worker import ab_evidence, analysis_profiles, worker
from apps.scene_description import scene, ab_scene, db

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'artifacts/runpod/image-p4-20261001-01/image-validation-20261001-01'


class MemoryReader:
    def __init__(self, directory, config):
        self.objects, self.reads = {}, []
        local = scene.parse((directory/'manifest.json').read_bytes())
        prefix = 'cctv-evidence/' + config['job_id']
        remote = dict(local, schema_version=ab_scene.REMOTE_SCHEMA, storage='s3',
                      bucket=config['bucket'], region=config['region'], provider_job_id=config['job_id'],
                      manifest_key=prefix+'/manifest.json',
                      files={name: dict(info, key=prefix+'/'+name) for name, info in local['files'].items()})
        for name in local['files']:
            self.objects[prefix+'/'+name] = (directory/name).read_bytes()
        self.objects[prefix+'/manifest.json'] = scene.encoded(remote)

    def get(self, bucket, key, limit):
        self.reads.append(key)
        return self.objects[key][:limit+1]


class Trace:
    @contextmanager
    def span(self, *args):
        yield {}


class ABSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory()
        cls.prepared = {}
        cls.configs = {}
        cls.source_hashes = {}
        state = scene.parse((SOURCE/'state.json').read_bytes())
        for version in ('A', 'B'):
            source = SOURCE/f'input_001-{version}'/'output'
            raw = (source/'result.json').read_bytes()
            result = scene.parse(raw)
            # Trusted recorded run + image identity anchor, rather than assigning
            # a version from a directory name or legacy unlabelled result.
            recorded = next(row for row in state['jobs'] if row['version'] == version)
            if recorded['result'] != result or state['identity']['image_manifest_sha256'] != analysis_profiles.profile(version)['image_manifest_sha256']:
                raise AssertionError('Stored P4 evidence differs from recorded run')
            profile = analysis_profiles.profile(version)
            target = Path(cls.storage.name)/version
            ab_evidence.prepare(source, target, snapshot=profile,
                input_sha256=result['input_sha256'], result_sha256=scene.sha(raw))
            cls.prepared[version] = target
            cls.configs[version] = {'bucket': 'cctv-test-bucket', 'region': 'ap-northeast-2',
                'job_id': ('a' if version == 'A' else 'b')*32, 'event_id': 'event_000',
                'expected_input_sha256': result['input_sha256'], 'analysis_profile': profile,
                'gpu_key': analysis_profiles.gpu_identity(result['input_sha256'], profile)['gpu_key']}
            cls.source_hashes[version] = {p.relative_to(source).as_posix(): scene.sha(p.read_bytes())
                                          for p in source.rglob('*') if p.is_file()}

    @classmethod
    def tearDownClass(cls):
        for version, expected in cls.source_hashes.items():
            source = SOURCE/f'input_001-{version}'/'output'
            actual = {p.relative_to(source).as_posix(): scene.sha(p.read_bytes())
                      for p in source.rglob('*') if p.is_file()}
            if expected != actual:
                raise AssertionError('Original evidence changed during tests')
        cls.storage.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def reader(self, version='B'):
        return MemoryReader(self.prepared[version], self.configs[version])

    def fetch(self, version='B', reader=None, config=None, name='input'):
        return scene.fetch(config or self.configs[version], self.root/name, reader or self.reader(version))

    def test_both_versions_roundtrip_all_required_bytes_and_context(self):
        for version, count in [('A', 4), ('B', 6)]:
            with self.subTest(version=version):
                reader = self.reader(version)
                receipt = self.fetch(version, reader, name=version)
                config, manifest, event, media, _ = scene.load_bundle(self.root/version)
                self.assertEqual(receipt['gemini_api_calls'], 0)
                self.assertEqual(event['version'], version)
                self.assertEqual(len(reader.reads), count)
                self.assertEqual(set(media), set(ab_scene.asset_kinds(event)))
                for kind, raw in media.items():
                    self.assertEqual(raw, (self.prepared[version]/event[kind]).read_bytes())
                if version == 'B':
                    self.assertEqual(scene.parse(media['context']), event['incident_context'])
                    self.assertNotIn('yolo_objects', event)
                    self.assertEqual(event['frame_mime'], 'image/jpeg')

    def test_b_request_preserves_roles_and_contact_limit_without_obsolete_paths(self):
        self.fetch()
        _, _, event, media, _ = scene.load_bundle(self.root/'input')
        raw = scene.build_request(event, media, 4)
        request = scene.parse(raw)
        self.assertIn('bounding-box overlap', request['systemInstruction']['parts'][0]['text'])
        self.assertIn('class_review_required', raw.decode())
        self.assertNotIn('/workspace/', raw.decode())
        self.assertNotIn('yolo_objects', raw.decode())
        self.assertNotIn('cctv-test-bucket', raw.decode())
        parts = request['contents'][0]['parts']
        self.assertEqual([p['inlineData']['mimeType'] for p in parts if 'inlineData' in p],
                         ['video/mp4', 'image/jpeg', 'video/mp4'])
        refs = request['generationConfig']['responseJsonSchema']['properties']['observations']['items']['properties']['asset_refs']['items']['enum']
        self.assertEqual(refs, ['clip', 'frame', 'roi_clip'])

    def test_wrong_selected_version_rejected_before_media_or_generation(self):
        config = deepcopy(self.configs['B'])
        config['analysis_profile'] = analysis_profiles.profile('A')
        config['gpu_key'] = analysis_profiles.gpu_identity(config['expected_input_sha256'], config['analysis_profile'])['gpu_key']
        reader = self.reader()
        with self.assertRaises(ValueError): self.fetch(reader=reader, config=config)
        self.assertEqual(len(reader.reads), 1)

    def test_outside_clip_display_frame_is_not_sent_with_clip_relative_time_contract(self):
        self.fetch()
        _, _, event, media, _ = scene.load_bundle(self.root/'input')
        event['representative_within_clip'] = False
        with self.assertRaisesRegex(ValueError, 'outside'):
            scene.build_request(event, media, 4)

    def test_all_b_assets_required_hash_checked_and_incomplete_receipt_absent(self):
        event = ab_evidence.selected_event(scene.parse((self.prepared['B']/'evidence.json').read_bytes()), 'event_000')
        for kind in ('clip', 'frame', 'roi_clip', 'context'):
            with self.subTest(kind=kind):
                reader = self.reader()
                reader.objects['cctv-evidence/' + self.configs['B']['job_id'] + '/' + event[kind]] += b'changed'
                with self.assertRaises(ValueError): self.fetch(reader=reader, name=kind)
                self.assertFalse((self.root/kind/'fetch.complete.json').exists())

    def test_manifest_cross_job_key_rejected_before_object_get(self):
        reader = self.reader()
        key = 'cctv-evidence/' + self.configs['B']['job_id'] + '/manifest.json'
        manifest = scene.parse(reader.objects[key])
        name = 'evidence/event_000/roi_clip.mp4'
        manifest['files'][name]['key'] = 'outside-job/roi.mp4'
        reader.objects[key] = scene.encoded(manifest)
        with self.assertRaises(ValueError): self.fetch(reader=reader)
        self.assertNotIn('outside-job/roi.mp4', reader.reads)

    def test_corrupt_local_context_or_roi_blocks_load_before_model(self):
        self.fetch()
        root = self.root/'input'
        path = root/'evidence/event_000/context.json'
        path.write_text('{}')
        calls = []
        with self.assertRaises(ValueError):
            scene.describe(root, self.root/'description', 'gemini-test', 4, 'test-key',
                           lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_synthetic_response_stores_roi_citation_and_version_and_validates_db_payload(self):
        self.fetch()
        calls = []
        def caller(model, request, key):
            calls.append(request)
            content = {'accident_suspicion': 'uncertain', 'incident_types': ['unknown'],
                'summary': '합성 응답 검사', 'uncertainties': ['모델 생성이 아닌 대역 응답'],
                'observations': [{'text': '합성 관찰', 'clip_times_seconds': [1.0],
                                  'asset_refs': ['clip', 'frame', 'roi_clip']}]}
            return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(content)}]}}]}
        result = scene.describe(self.root/'input', self.root/'description', 'gemini-test', 4, 'test-key', caller)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['prompt_version'], ab_scene.PROMPT_VERSION)
        self.assertEqual(result['source']['gpu_key'], self.configs['B']['gpu_key'])
        self.assertEqual(db.validate_result(result), scene.sha(scene.encoded(result)))
        self.assertEqual(len(result['description']['observations'][0]['evidence_asset_ids']), 3)
        result['source']['version'] = 'A'
        with self.assertRaises(ValueError): db.validate_result(result)

    def test_existing_be_collect_creates_correct_b_scene_config_without_model(self):
        config = self.configs['B']
        directory = self.root/'worker'
        directory.mkdir()
        state = {'worker_job_id': 'c'*32, 'provider_job_id': config['job_id'], 'status': 'evidence_ready',
            'input_sha256': config['expected_input_sha256'], 'analysis_profile': config['analysis_profile'],
            'gpu_key': config['gpu_key'], 'config': {'s3_bucket': config['bucket'], 's3_region': config['region']},
            's3': {'manifest_key': 'cctv-evidence/'+config['job_id']+'/manifest.json',
                   'manifest_sha256':scene.sha(self.reader().objects['cctv-evidence/'+config['job_id']+'/manifest.json'])}}
        handoff = worker.collect(directory, state, self.reader(), Trace())
        self.assertEqual(handoff['status'], 'handoff_ready')
        self.assertFalse(handoff['media_bytes_verified'])
        self.assertEqual(handoff['analysis_profile']['version'], 'B')
        self.assertEqual(scene.parse((directory/'scene-event_000.json').read_bytes()), config)
        self.assertEqual(scene.fetch(config, self.root/'input', self.reader())['status'], 'verified')
        normalized=scene.parse((self.prepared['B']/'normalized-result.json').read_bytes())
        self.assertEqual(handoff['metrics'], {k:normalized[k] for k in ('timings','memory')})

    def test_be_rejects_manifest_different_from_provider_receipt_before_index_read(self):
        from apps.be_worker.common import WorkerError
        config=self.configs['B'];reader=self.reader();directory=self.root/'rejected';directory.mkdir()
        state={'worker_job_id':'c'*32,'provider_job_id':config['job_id'],'status':'evidence_ready',
            'input_sha256':config['expected_input_sha256'],'analysis_profile':config['analysis_profile'],
            'gpu_key':config['gpu_key'],'config':{'s3_bucket':config['bucket'],'s3_region':config['region']},
            's3':{'manifest_sha256':'f'*64}}
        with self.assertRaisesRegex(WorkerError,'provider_manifest_digest_mismatch'):
            worker.collect(directory,state,reader,Trace())
        self.assertEqual(len(reader.reads),1)
        self.assertEqual(list(directory.iterdir()),[])

    def saved_b_scene(self):
        """Simulate a saved response before its first DB write, with real bundles."""
        from apps.be_worker.common import read_json, save
        from apps.be_worker.scene_jobs import SceneJobs

        config = self.configs['B']
        directory = self.root/'saved-worker'
        directory.mkdir()
        state = {'worker_job_id': 'c'*32, 'provider_job_id': config['job_id'],
            'status': 'evidence_ready', 'input_sha256': config['expected_input_sha256'],
            'analysis_profile': config['analysis_profile'], 'gpu_key': config['gpu_key'],
            'config': {'s3_bucket': config['bucket'], 's3_region': config['region']},
            's3': {'manifest_key': 'cctv-evidence/'+config['job_id']+'/manifest.json',
                   'manifest_sha256':scene.sha(self.reader().objects['cctv-evidence/'+config['job_id']+'/manifest.json'])}}
        handoff = worker.collect(directory, state, self.reader(), Trace())
        event_dir = directory/'scenes/event_000'
        event_dir.mkdir(parents=True)
        scene.fetch(config, event_dir/'input', self.reader())
        content = {'accident_suspicion': 'uncertain', 'incident_types': ['unknown'],
            'summary': '저장 응답 검증용 합성 내용', 'uncertainties': ['실제 모델 호출 없음'],
            'observations': [{'text': '합성 관찰', 'clip_times_seconds': [1.0],
                              'asset_refs': ['clip', 'roi_clip']}]}
        result = scene.describe(event_dir/'input', event_dir/'description',
            'gemini-test', 4, 'synthetic-key', lambda *args: {'candidates': [
                {'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(content)}]}}]})
        row = {'worker_job_id': state['worker_job_id'], 'state': state, 'handoff': handoff}
        test = self
        class Store:
            def rows(self): return [row]
            def write(self, *args): test.fail('Rejected result must not update the queue')
        class Coordinator:
            store = Store()
            def directory(self, selected): return directory
            def load(self, selected): return read_json(directory/'state.json')
        persisted, generated = [], []
        scenes = SceneJobs(Coordinator(), lambda value: persisted.append(value),
                           lambda: test.fail('Saved result must not run generation preflight'))
        def resume(changed):
            save(event_dir/'description/result.json', changed)
            return scenes.describe(state['worker_job_id'], 'event_000', model='gemini-test', fps=4,
                key_loader=lambda: test.fail('Saved result must not read a key'),
                caller=lambda *args: generated.append(args))
        return result, resume, persisted, generated

    def test_saved_b_description_changed_roi_hash_or_size_rejected_before_store(self):
        from apps.be_worker.common import WorkerError
        original, resume, persisted, generated = self.saved_b_scene()
        for field, value in [('sha256', '0'*64), ('bytes', 1)]:
            with self.subTest(field=field):
                changed = deepcopy(original)
                changed['source']['assets']['roi_clip'][field] = value
                # The payload still has a valid shape; its receipt must also
                # match the selected evidence manifest before persistence.
                db.validate_result(changed)
                with self.assertRaisesRegex(WorkerError, 'scene_result_identity_mismatch'):
                    resume(changed)
        self.assertEqual(persisted, [])
        self.assertEqual(generated, [])

    def test_saved_description_self_consistent_other_profile_rejected_before_store(self):
        from apps.be_worker.common import WorkerError
        original, resume, persisted, generated = self.saved_b_scene()
        changed = deepcopy(original)
        source = changed['source']
        profile = analysis_profiles.profile('A')
        source.update(analysis_profile=profile, version='A', frame_mime='image/png',
                      frame_role='unannotated_frame', gpu_key=analysis_profiles.gpu_identity(
                          changed['input_sha256'], profile)['gpu_key'])
        source['assets'].pop('roi_clip')
        source['assets']['frame']['key'] = source['assets']['frame']['key'].removesuffix('.jpg')+'.png'
        observation = changed['description']['observations'][0]
        observation['asset_refs'] = ['clip']
        observation['evidence_asset_ids'] = [source['assets']['clip']['asset_id']]
        # A coherent A payload must still be rejected for this selected B job.
        db.validate_result(changed)
        with self.assertRaisesRegex(WorkerError, 'scene_result_analysis_mismatch'):
            resume(changed)
        self.assertEqual(persisted, [])
        self.assertEqual(generated, [])


if __name__ == '__main__':
    unittest.main()
