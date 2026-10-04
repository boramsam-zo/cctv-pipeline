"""Release selection and historical identity checks; no GPU, DB or network."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles as p, generation_settings as g
from apps.be_worker import hour_session as session, trial_limits as trial
from apps.be_worker.common import WorkerError
from apps.be_worker.runpod_api import RunpodAPI
from tests.be_worker.test_hour_session import Connection, FakeDB


class GPUProfileReleaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / (p.RTX2000ADA_DEPLOYMENT + '.json')
        self.release = {
            'schema_version': 'cctv-analysis-deployment-v1',
            'deployment': p.RTX2000ADA_DEPLOYMENT,
            'gpu': 'NVIDIA RTX 2000 Ada Generation',
            # Explicit synthetic fixture provenance; never shipped as a release.
            'image_manifest_sha256': hashlib.sha256(b'test-only image manifest').hexdigest(),
            'execution_adapter_sha256': hashlib.sha256(b'test-only adapter').hexdigest(),
        }
        self.write(self.release)
        for context in (
            patch.dict(p._RELEASE_CATALOGS, {p.RTX2000ADA_DEPLOYMENT: self.path}, clear=True),
            patch.dict(os.environ, {'CCTV_ANALYSIS_DEPLOYMENT': p.LEGACY_DEPLOYMENT,
                                    'BE_SPOOL_PATH': tmp.name}),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.conn = Connection(FakeDB())

    def write(self, release):
        self.path.write_text(json.dumps(release), encoding='utf-8')

    def select_new(self):
        os.environ['CCTV_ANALYSIS_DEPLOYMENT'] = p.RTX2000ADA_DEPLOYMENT

    def row(self, *, deployment=p.LEGACY_DEPLOYMENT, digit='1'):
        frozen = p.profile('B', deployment=deployment)
        sha = digit * 64
        return {'worker_job_id': digit * 32, 'state': {
            'worker_job_id': digit * 32, 'input_sha256': sha, 'input_bytes': 42,
            'analysis_profile': frozen, 'gpu_key': p.gpu_identity(sha, frozen)['gpu_key'],
        }, 'handoff': {'scene_configs': ['scene-event_000.json']}}

    def bind(self, row):
        settings = g.from_environment({}, analysis_profile=row['state']['analysis_profile'])
        with self.conn.transaction():
            session.check_admission(self.conn, row['state']['input_sha256'], 42, ['B'], 'full_report')
            return session.bind_job(self.conn, row, settings, '2026-10-03')

    def test_no_selector_keeps_original_profile_without_catalog(self):
        del os.environ['CCTV_ANALYSIS_DEPLOYMENT']
        self.path.unlink()
        for version in p.VERSIONS:
            self.assertEqual(p.profile(version), p._PROFILES[version])

    def test_missing_selected_catalog_fails_closed_and_legacy_still_reads(self):
        old = p.profile('B')
        self.path.unlink()
        self.select_new()
        with self.assertRaisesRegex(ValueError, 'catalog_unavailable'):
            p.profile('B')
        self.assertEqual(p.profile('B', deployment=p.LEGACY_DEPLOYMENT), old)
        self.assertEqual(p.validate_profile(old, expected_version='B'), old)

    def test_unknown_selector_and_environment_path_do_not_register_a_release(self):
        for value in ('other-gpu', '', str(self.path)):
            with self.subTest(value=value), patch.dict(os.environ, {'CCTV_ANALYSIS_DEPLOYMENT': value}):
                with self.assertRaisesRegex(ValueError, 'deployment_unknown'):
                    p.profile('B')
        os.environ['CCTV_ANALYSIS_PROFILE_CATALOG'] = str(self.path)
        try:
            self.assertEqual(p.profile('B'), p._PROFILES['B'])
        finally:
            os.environ.pop('CCTV_ANALYSIS_PROFILE_CATALOG')

    def test_catalog_changes_only_explicit_gpu_adapter_and_manifest(self):
        self.select_new()
        for version in p.VERSIONS:
            current = p.profile(version)
            expected = deepcopy(p._PROFILES[version])
            expected['runtime']['gpu'] = self.release['gpu']
            for field in ('execution_adapter_sha256', 'image_manifest_sha256'):
                expected[field] = self.release[field]
            self.assertEqual(current, expected)
            self.assertEqual(p.validate_profile(current), current)

    def test_runtime_and_adapter_separate_gpu_identity_from_legacy(self):
        old = p.profile('B')
        self.select_new()
        new = p.profile('B')
        for key in ('gpu_key', 'profile_sha256', 'pipeline_sha256'):
            self.assertNotEqual(p.gpu_identity('1' * 64, old)[key], p.gpu_identity('1' * 64, new)[key])

    def test_both_frozen_snapshots_validate_independently_of_selection(self):
        old = p.profile('B')
        new = p.profile('B', deployment=p.RTX2000ADA_DEPLOYMENT)
        for selected in (p.LEGACY_DEPLOYMENT, p.RTX2000ADA_DEPLOYMENT, 'unknown'):
            with patch.dict(os.environ, {'CCTV_ANALYSIS_DEPLOYMENT': selected}):
                for frozen in (old, new):
                    self.assertEqual(p.validate_profile(frozen, expected_version='B'), frozen)

    def test_changing_only_gpu_or_any_trusted_snapshot_field_is_not_accepted(self):
        old = p.profile('B')
        old['runtime']['gpu'] = self.release['gpu']
        with self.assertRaisesRegex(ValueError, 'profile_changed'):
            p.validate_profile(old)
        current = p.profile('B', deployment=p.RTX2000ADA_DEPLOYMENT)
        for field in ('execution_adapter_sha256', 'image_manifest_sha256', 'source_archive_sha256'):
            changed = deepcopy(current)
            changed[field] = hashlib.sha256(('tampered ' + field).encode()).hexdigest()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'profile_changed'):
                p.validate_profile(changed)

    def test_catalog_rejects_unknown_fields_gpu_schema_and_provenance(self):
        mutations = [
            ('gpu', 'NVIDIA RTX A4500'), ('gpu', 'unregistered GPU'),
            ('deployment', 'arbitrary'), ('schema_version', 'other'), ('unexpected', True),
            ('image_manifest_sha256', '0' * 64), ('execution_adapter_sha256', 'f' * 64),
            ('image_manifest_sha256', 'PLACEHOLDER'), ('execution_adapter_sha256', None),
            ('image_manifest_sha256', p._IMAGE_MANIFEST), ('execution_adapter_sha256', p._ADAPTER),
            ('image_manifest_sha256', self.release['execution_adapter_sha256']),
        ]
        self.select_new()
        for field, value in mutations:
            changed = dict(self.release, **{field: value})
            self.write(changed)
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'catalog_'):
                p.profile('B')
        self.write({})
        with self.assertRaisesRegex(ValueError, 'catalog_invalid'):
            p.profile('B')

    def test_symlink_catalog_is_rejected(self):
        target = self.path.with_name('target.json')
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'catalog_invalid'):
            p.profile('B', deployment=p.RTX2000ADA_DEPLOYMENT)

    def test_mutating_returned_profile_does_not_change_release(self):
        expected = p.profile('B', deployment=p.RTX2000ADA_DEPLOYMENT)
        changed = p.profile('B', deployment=p.RTX2000ADA_DEPLOYMENT)
        changed['runtime']['gpu'] = 'changed'
        self.assertEqual(p.profile('B', deployment=p.RTX2000ADA_DEPLOYMENT), expected)

    def test_legacy_trial_plan_and_settings_stay_frozen_with_new_selector(self):
        expected = trial.approved_plan()
        settings = g.from_environment({}, analysis_profile=p.profile('B'))
        self.select_new()
        self.assertEqual(trial.approved_plan(), expected)
        self.assertEqual(trial._settings(settings, 'B'), settings)
        self.path.unlink()
        self.assertEqual(trial.approved_plan(), expected)
        self.assertEqual(trial._settings(settings, 'B'), settings)

    def test_legacy_trial_rejects_new_gpu_rows(self):
        row = self.row(deployment=p.RTX2000ADA_DEPLOYMENT)
        with self.assertRaisesRegex(WorkerError, 'trial_profile_not_approved'):
            trial._row(row)

    def test_saved_hour_binding_and_claims_survive_deployment_selection(self):
        session.initialize(self.conn, 1000, 4600)
        old = self.row()
        bound = self.bind(old)
        session.check_events(self.conn, old, ['event_000'], bound['settings'], 'full_report', '2026-10-03')
        session.claim(self.conn, old, 'event_000', 'scene', 'a' * 64, session.MODEL)
        before = session.get(self.conn)
        self.select_new()
        self.assertEqual(session.get(self.conn), before)
        self.assertEqual(self.bind(old), before['bindings'][old['worker_job_id']])
        self.path.unlink()
        self.assertEqual(session.get(self.conn), before)

    def test_new_hour_binding_uses_current_gpu_and_preserves_old_bindings(self):
        session.initialize(self.conn, 1000, 4600)
        old = self.row()
        self.bind(old)
        old_binding = deepcopy(session.get(self.conn)['bindings'][old['worker_job_id']])
        self.select_new()
        new = self.row(deployment=p.RTX2000ADA_DEPLOYMENT, digit='2')
        bound = self.bind(new)
        self.assertEqual(bound['gpu_key'], new['state']['gpu_key'])
        self.assertEqual(session.get(self.conn)['bindings'][old['worker_job_id']], old_binding)
        os.environ['CCTV_ANALYSIS_DEPLOYMENT'] = p.LEGACY_DEPLOYMENT
        self.assertEqual(len(session.get(self.conn)['bindings']), 2)

    def test_new_admission_cannot_bind_previous_gpu_under_new_deployment(self):
        session.initialize(self.conn, 1000, 4600)
        old = self.row()
        self.select_new()
        with self.assertRaisesRegex(WorkerError, 'requires_current_deployment'):
            self.bind(old)
        self.assertEqual(session.get(self.conn)['bindings'], {})

    def test_explicit_generation_snapshot_does_not_read_missing_current_catalog(self):
        frozen = p.profile('B')
        settings = g.from_environment({}, analysis_profile=frozen)
        self.select_new()
        self.path.unlink()
        self.assertEqual(g.validate(settings, analysis_profile=frozen), settings)
        with self.assertRaisesRegex(WorkerError, 'generation_contract_changed'):
            g.validate(settings)

    def health(self, deployment):
        return {'status': 'ok', 'api_schema': 'runpod-ab-api-v1', 'storage': 's3',
                'gpu_concurrency': 1, 'supported_versions': ['A', 'B'],
                'profiles': {v: p.gpu_identity('0' * 64, p.profile(v, deployment=deployment))['profile_sha256']
                             for v in p.VERSIONS}}

    def test_explicit_old_health_uses_original_provider_release_under_new_selection(self):
        api = RunpodAPI('https://test-8000.proxy.runpod.net', 'synthetic-token')
        frozen = p.profile('B')
        health = self.health(p.LEGACY_DEPLOYMENT)
        self.select_new()
        self.path.unlink()
        with patch.object(api, 'request', return_value=health) as request:
            self.assertEqual(api.health(expected_profile=frozen)['profiles'], health['profiles'])
            request.assert_called_once_with('/health')

    def test_explicit_new_health_uses_matching_release_even_when_default_is_legacy(self):
        api = RunpodAPI('https://test-8000.proxy.runpod.net', 'synthetic-token')
        frozen = p.profile('B', deployment=p.RTX2000ADA_DEPLOYMENT)
        health = self.health(p.RTX2000ADA_DEPLOYMENT)
        with patch.object(api, 'request', return_value=health):
            self.assertEqual(api.health(expected_profile=frozen)['profiles'], health['profiles'])
            with self.assertRaisesRegex(WorkerError, 'profile_not_ready'):
                api.health()

    def test_health_rejects_mixed_provider_releases_or_untrusted_expected_snapshot(self):
        api = RunpodAPI('https://test-8000.proxy.runpod.net', 'synthetic-token')
        frozen = p.profile('B')
        health = self.health(p.LEGACY_DEPLOYMENT)
        health['profiles']['A'] = self.health(p.RTX2000ADA_DEPLOYMENT)['profiles']['A']
        with patch.object(api, 'request', return_value=health):
            with self.assertRaisesRegex(WorkerError, 'profile_not_ready'):
                api.health(expected_profile=frozen)
        health = self.health(p.LEGACY_DEPLOYMENT)
        with patch.object(api, 'request', return_value=health):
            for change in ({'execution_adapter_sha256': 'f' * 64}, {'version': 'C'}):
                with self.subTest(change=change), self.assertRaisesRegex(WorkerError, 'profile_not_ready'):
                    api.health(expected_profile=dict(frozen, **change))


if __name__ == '__main__':
    unittest.main()
