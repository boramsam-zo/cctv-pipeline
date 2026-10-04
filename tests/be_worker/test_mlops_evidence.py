"""Synthetic model-switch regression over copied saved B media, never inference.

The logits, profile, and sample times below are deliberate test fixtures, NOT R2
predictions or model-quality evidence. Retained source outputs are hash checked
before and after. The whole real preparation/fetch/request path is exercised
without decoding media, importing models, writing S3, or calling a VLM.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest

from apps.be_worker import ab_evidence, analysis_profiles
from apps.be_worker.model_catalog import contract_for_version
from apps.scene_description import ab_scene, scene, team_scene

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'artifacts/runpod/image-p4-20261001-01/image-validation-20261001-01/input_001-B/output'
CONTRACT = ROOT / 'tests/scene_description/fixtures/rag_v2'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def records(root):
    return [{'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size,
             'sha256': digest(path)} for path in sorted(root.rglob('*')) if path.is_file()]


class MemoryReader:
    def __init__(self, prepared, config):
        self.reads = []
        local = read(prepared / 'manifest.json')
        prefix = 'cctv-evidence/' + config['job_id']
        remote = dict(local, schema_version=ab_scene.REMOTE_SCHEMA, storage='s3',
            bucket=config['bucket'], region=config['region'], provider_job_id=config['job_id'],
            manifest_key=prefix + '/manifest.json',
            files={name: dict(value, key=prefix + '/' + name) for name, value in local['files'].items()})
        self.objects = {prefix + '/' + name: (prepared / name).read_bytes() for name in local['files']}
        self.objects[prefix + '/manifest.json'] = scene.encoded(remote)

    def get(self, bucket, key, limit):
        self.reads.append(key)
        return self.objects[key][:limit + 1]


@unittest.skipUnless(SOURCE.is_dir(), 'retained media fixture is not distributed in Git/CI')
class MLOpsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.before = records(SOURCE)

    def tearDown(self):
        self.assertEqual(records(SOURCE), self.before, 'saved original evidence was modified')

    def fixture(self, version):
        source = self.root / ('synthetic-' + version)
        shutil.copytree(SOURCE, source)
        raw = read(source / 'raw/result.json')
        contract = contract_for_version(version)
        raw['classifier'] = {**contract, **deepcopy(raw['x3d'])}
        normalized = read(source / 'result.json')
        normalized['version'] = version
        normalized['classifier_contract'] = contract
        runtime = read(source / 'raw/runtime.json')
        runtime['version'] = version
        runtime['synthetic_contract_test'] = True
        if version == 'R2':
            raw['schema_version'] = 'cctv-classifier-result-v1'
            raw.pop('x3d')
            raw['classifier'].pop('model')
            for row in raw['classifier']['windows']:
                probability = row['collision_probability']
                row['logits'] = [math.log(1 - probability), math.log(probability)]
                row['predicted_class'] = int(row['is_positive'])
                samples = [row['start_s'] + (i + .5) * 2 / 16 for i in range(16)]
                row['sample_times_s'] = samples
                row['requested_sample_times_s'] = samples
            for event, handoff in zip(raw['events'], raw['vlm_handoffs']):
                handoff['classifier_collision_candidate'] = handoff.pop('x3d_collision_candidate')
                handoff['classifier_probability_at_trigger'] = handoff.pop('x3d_probability_at_trigger')
                handoff['classifier'] = contract
                context = event['incident_context']
                if context['display_frame_reason'] == 'x3d_candidate_time':
                    context['display_frame_reason'] = 'classifier_candidate_time'
                handoff['incident_context'] = deepcopy(context)
                save(source / 'raw' / event['event_id'] / (event['event_id'] + '_context.json'), context)
        save(source / 'raw/result.json', raw)
        save(source / 'raw/vlm_handoffs.json', raw['vlm_handoffs'])
        save(source / 'raw/runtime.json', runtime)
        manifest = read(source / 'raw/manifest.json')
        manifest['version'] = version
        manifest['files'] = [r for r in records(source / 'raw') if r['path'] != 'manifest.json']
        save(source / 'raw/manifest.json', manifest)
        normalized['artifacts'] = [dict(row, path='raw/' + row['path']) for row in records(source / 'raw')]
        save(source / 'result.json', normalized)
        snapshot = analysis_profiles.profile(version, deployment=analysis_profiles.MLOPS_DEPLOYMENT)
        return source, snapshot

    def pipeline(self, version):
        source, snapshot = self.fixture(version)
        normalized = read(source / 'result.json')
        prepared = self.root / ('prepared-' + version)
        result = ab_evidence.prepare(source, prepared, snapshot=snapshot,
            input_sha256=normalized['input_sha256'], result_sha256=digest(source / 'result.json'))
        ab_evidence.validate_local(prepared)
        config = dict(bucket='synthetic-private-evidence', region='ap-northeast-2',
                      job_id=('b' if version == 'B' else 'c') * 32, event_id='event_000',
                      expected_input_sha256=normalized['input_sha256'], analysis_profile=snapshot,
                      gpu_key=result['index']['gpu_key'])
        reader = MemoryReader(prepared, config)
        bundle = self.root / ('bundle-' + version)
        receipt = scene.fetch(config, bundle, reader)
        self.assertEqual(receipt['gemini_api_calls'], 0)
        verified = scene.load_bundle(bundle)
        _, manifest, event, media, _ = verified
        self.assertEqual(ab_scene.checked_index((bundle / 'evidence.json').read_bytes(), config, manifest),
                         result['index'])
        self.assertEqual(ab_scene.asset_kinds(event), ('clip', 'frame', 'roi_clip', 'context'))
        request = scene.parse(team_scene.build_request(verified, 'gemini-3.5-flash-lite',
                                                       contract_directory=CONTRACT))
        parts = request['contents'][0]['parts']
        self.assertEqual([part['inlineData']['mimeType'] for part in parts if 'inlineData' in part],
                         ['video/mp4', 'image/jpeg', 'video/mp4'])
        self.assertEqual(set(media), {'clip', 'frame', 'roi_clip', 'context'})
        self.assertEqual(scene.parse(media['context']), event['incident_context'])
        self.assertEqual(event['analysis_profile']['classifier'], contract_for_version(version))
        self.assertEqual(event['version'], version)
        self.assertEqual(len(reader.reads), 6)
        return result

    def test_new_b_profile_preserves_existing_report_media_route(self):
        self.pipeline('B')

    def test_synthetic_r2_profile_reaches_same_team_report_request(self):
        self.pipeline('R2')

    def test_model_choice_changes_gpu_identity_for_identical_input(self):
        a = self.pipeline('B')
        b = self.pipeline('R2')
        self.assertNotEqual(a['index']['gpu_key'], b['index']['gpu_key'])
        self.assertEqual(a['index']['input_sha256'], b['index']['input_sha256'])


if __name__ == '__main__':
    unittest.main()
