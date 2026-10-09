"""LC release/selection boundaries using metadata only; no models or services."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles as profiles, model_catalog, team_settings
from apps.be_worker import ab_evidence, demo_control, demo_session
from apps.be_worker.common import WorkerError
from apps.be_worker.runpod_api import RunpodAPI
from apps.scene_description import ab_scene


class LastconvSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = json.loads(profiles._RELEASE_CATALOGS[profiles.MLOPS_DEPLOYMENT].read_text())
        self.old_profiles = {v: profiles.profile(v, deployment=profiles.MLOPS_DEPLOYMENT)
                             for v in ('B', 'R2')}
        self.catalog = self.root / 'release.json'
        self.catalog.write_text(json.dumps(self.original))
        archive = self.root / 'model_releases'
        archive.mkdir()
        (archive / (profiles._sha(self.original) + '.json')).write_text(json.dumps(self.original))
        for context in (
                patch.dict(profiles._RELEASE_CATALOGS, {profiles.MLOPS_DEPLOYMENT: self.catalog}),
                patch.object(profiles, '__file__', str(self.root / 'analysis_profiles.py')),
                patch.dict(os.environ, {'CCTV_ANALYSIS_DEPLOYMENT': profiles.MLOPS_DEPLOYMENT})):
            context.start()
            self.addCleanup(context.stop)

    def enable_lc(self):
        release = deepcopy(self.original)
        release['classifiers']['LC'] = model_catalog.contract_for_version('LC')
        self.catalog.write_text(json.dumps(release))
        return release

    def test_existing_release_never_enables_lc_by_catalog_code_alone(self):
        self.assertEqual(profiles.supported_versions(), ('B', 'R2'))
        with self.assertRaisesRegex(ValueError, 'not_in_deployment'):
            profiles.selection_identity('a' * 64, 'LC', 'full_report')
        self.assertNotIn('LC', [m['selection'] for m in team_settings.metadata()['analysis_models']])

    def test_opt_in_lc_is_single_child_with_distinct_weights_and_report_media(self):
        self.enable_lc()
        selected = profiles.selection_identity('a' * 64, 'LC', 'full_report')
        self.assertEqual(len(selected['children']), 1)
        lc = selected['children'][0]['profile']
        self.assertEqual(lc['classifier'], model_catalog.contract_for_version('LC'))
        self.assertNotIn('models/x3d_s_real_so_tad_epoch7_best_model.pt', lc['model_files'])
        # Exercise the real contract hashing against public release files and
        # synthetic corpus/index bytes; private RAG artifacts are absent in CI.
        resources = self.root / 'synthetic-contract-resources'
        public_files = ('team_rag/VLM_PROMPT.md', 'team_rag/event.schema.json',
            'scene_description/team_scene.py', 'team_rag/control_rag.py',
            'team_rag/runtime.py', 'team_rag/e5-model-manifest.json',
            'team_rag/contacts.json')
        for relative in public_files:
            target = resources / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((team_settings.ROOT / relative).read_bytes())
        for relative, content in (
                ('team_rag/data/chunks.jsonl', b'{"synthetic_corpus":true}\n'),
                ('team_rag/data/index.sqlite', b'synthetic-index-contract-only')):
            target = resources / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        with patch.object(team_settings, 'ROOT', resources):
            self.assertEqual(team_settings.contract('LC')['analysis_version'], 'LC')
        self.assertEqual(ab_scene.asset_kinds({'version': 'LC'}), ('clip', 'frame', 'roi_clip', 'context'))
        for version in ('B', 'R2'):
            self.assertEqual(profiles.profile(version), self.old_profiles[version])
            self.assertNotEqual(selected['children'][0]['gpu_key'],
                                profiles.gpu_identity('a' * 64, profiles.profile(version))['gpu_key'])

    def test_lc_rejects_wrong_weights_or_preprocessing_in_release(self):
        for section, field, value in ((None, 'weights_sha256', '1' * 64),
                                      ('preprocessing', 'height', 182)):
            release = self.enable_lc()
            target = release['classifiers']['LC']
            if section:
                target = target[section]
            target[field] = value
            self.catalog.write_text(json.dumps(release))
            with self.assertRaisesRegex(ValueError, 'classifier_contracts_invalid'):
                profiles.profile('LC')

    def test_old_saved_release_health_does_not_require_new_lc_companion(self):
        release = self.enable_lc()
        release['execution_adapter_sha256'] = '8' * 64
        self.catalog.write_text(json.dumps(release))
        old = self.old_profiles['R2']
        health = dict(status='ok', api_schema='runpod-ab-api-v1', storage='s3', gpu_concurrency=1,
            supported_versions=['B', 'R2'], profiles={v: profiles.gpu_identity('0' * 64, item)['profile_sha256']
                                                    for v, item in self.old_profiles.items()})
        api = RunpodAPI('https://example.invalid', 'fixture-only')
        with patch.object(api, 'request', return_value=health):
            self.assertEqual(api.health(old)['supported_versions'], ['B', 'R2'])

    def test_lc_uses_argmax_tie_normal_and_requires_classifier_handoff(self):
        self.enable_lc()
        contract = model_catalog.contract_for_version('LC')
        times = [(i + .5) * 2 / 16 for i in range(16)]
        raw = dict(schema_version='cctv-classifier-result-v1', events=[], classifier=dict(contract,
            label_mapping={'0': 'Normal', '1': 'Collision'}, threshold=.5, window_seconds=2,
            stride_seconds=1, decision='Normal', max_collision_probability=.5,
            windows=[dict(start_s=0., end_s=2., logits=[0., 0.], collision_probability=.5,
                          predicted_class=0, is_positive=False,
                          requested_sample_times_s=times, sample_times_s=times)]))
        normalized = dict(version='LC', window_count=1, status='no_candidates')
        ab_evidence._windows(raw, normalized, 2.)
        raw['classifier']['windows'][0]['is_positive'] = True
        with self.assertRaisesRegex(ValueError, 'window_values'):
            ab_evidence._windows(raw, normalized, 2.)
        ab_evidence._handoff_classifier(dict(classifier_collision_candidate=True, classifier=contract),
                                        profiles.profile('LC'))
        with self.assertRaises(ValueError):
            ab_evidence._handoff_classifier(dict(x3d_collision_candidate=True), profiles.profile('LC'))

    def test_demo_cannot_add_lc_against_old_provider(self):
        marker = demo_session.validate_marker(dict(schema=demo_session.SCHEMA,
            name='demo-session-20261009-03', versions=['B', 'R2', 'LC'], purpose='full_report',
            max_jobs=1, per_job_seconds=300, window_seconds=3600,
            max_upload_bytes=50 * 1024 * 1024, max_video_seconds=60))
        api = RunpodAPI('https://example.invalid', 'fixture-only')
        with patch.object(api, 'health', return_value={}), self.assertRaisesRegex(WorkerError, 'not_packaged'):
            demo_control.provider_ready(api, marker, 0)


if __name__ == '__main__':
    unittest.main()
