"""New model-session guard with old rows/claims preserved; no provider calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles as profiles, generation_settings as generation
from apps.be_worker import hour_session as session
from apps.be_worker.common import WorkerError
from tests.be_worker.test_hour_session import Connection, FakeDB


class ModelConnection(Connection):
    def execute(self, query, args=None):
        if query.startswith('SELECT EXISTS') and len(args) == 3:
            already = any(name != args[0] and (
                args[1] in payload.get('bindings', {}) or any(b.get('gpu_key') == args[2]
                    for b in payload.get('bindings', {}).values()))
                for name, payload in self.db.rows.items()
                if name.startswith(('p6-hour-session-', 'mlops-session-')))
            return SimpleNamespace(fetchone=lambda: {'already_bound': already})
        return super().execute(query, args)


class ModelSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        # Synthetic release descriptor exercises the real profile validator.
        self.release = root / 'release.json'
        self.release.write_text(json.dumps(dict(schema_version='cctv-mlops-deployment-v1',
            deployment=profiles.MLOPS_DEPLOYMENT, gpu=profiles.PORTABLE_GPU,
            image_manifest_sha256='d' * 64, execution_adapter_sha256='e' * 64,
            code_files={'runtime/model_classifier.py': 'f' * 64})))
        for target in (patch.dict(os.environ, {'BE_SPOOL_PATH': str(root),
                                             'CCTV_ANALYSIS_DEPLOYMENT': profiles.MLOPS_DEPLOYMENT}),
                       patch.dict(profiles._RELEASE_CATALOGS, {profiles.MLOPS_DEPLOYMENT: self.release})):
            target.start()
            self.addCleanup(target.stop)
        self.db = FakeDB()
        self.conn = ModelConnection(self.db)
        self.marker = dict(schema=session.MODEL_SESSION_SCHEMA, name='mlops-session-20261004-01',
            versions=['B', 'R2'], purpose='gpu_comparison', max_jobs=2,
            per_job_seconds=300, window_seconds=900, approved_input_sha256='1' * 64)
        self.select(self.marker)

    def select(self, marker):
        session.marker_path().write_text(json.dumps(marker))

    def start(self):
        return session.initialize(self.conn, self.db.time, self.db.time + 900)

    def row(self, version='B', digit='2'):
        profile = profiles.profile(version)
        sha = self.marker['approved_input_sha256']
        key = profiles.gpu_identity(sha, profile)['gpu_key']
        return dict(worker_job_id=digit * 32, state=dict(worker_job_id=digit * 32,
            input_sha256=sha, input_bytes=42, analysis_profile=profile, gpu_key=key),
            handoff={'scene_configs': ['scene-event_000.json']})

    def bind(self, row, settings=None, day=None):
        with self.conn.transaction():
            session.check_admission(self.conn, row['state']['input_sha256'],
                row['state']['input_bytes'], [row['state']['analysis_profile']['version']],
                self.marker['purpose'])
            return session.bind_job(self.conn, row, settings, day)

    def test_same_input_b_and_r2_each_once(self):
        before = deepcopy(self.db.rows)
        self.start()
        rows = [self.row('B', '2'), self.row('R2', '3')]
        for row in rows:
            self.bind(row)
            session.check_dispatch(self.conn, row)
        saved = session.get(self.conn)
        self.assertEqual({b['version'] for b in saved['bindings'].values()}, {'B', 'R2'})
        self.assertEqual(saved['max_jobs'], 2)
        self.assertEqual(saved['deadline_at'] - saved['started_at'], 900)
        self.assertEqual(saved['claims'], [])
        self.bind(rows[0])
        self.assertEqual(session.get(self.conn), saved)
        self.assertEqual({key: self.db.rows[key] for key in before}, before)

    def test_same_model_cannot_get_second_worker_or_changed_gpu_key(self):
        self.start()
        self.bind(self.row())
        with self.assertRaisesRegex(WorkerError, 'model_already_bound'):
            self.bind(self.row('B', '4'))
        changed = self.row('R2', '3')
        changed['state']['gpu_key'] = 'a' * 64
        with self.assertRaisesRegex(WorkerError, 'gpu_identity_changed'):
            self.bind(changed)
        self.assertEqual(len(session.get(self.conn)['bindings']), 1)

    def test_other_input_or_purpose_is_not_approved(self):
        self.start()
        for sha, purpose, versions in [('2' * 64, 'gpu_comparison', ['B']),
                                      ('1' * 64, 'full_report', ['B']),
                                      ('1' * 64, 'gpu_comparison', ['A']),
                                      ('1' * 64, 'gpu_comparison', ['R2', 'B'])]:
            with self.subTest(sha=sha, purpose=purpose, versions=versions), self.assertRaises(WorkerError):
                with self.conn.transaction():
                    session.check_admission(self.conn, sha, 42, versions, purpose)

    def test_comparison_forbids_settings_date_and_every_generation_claim(self):
        self.start()
        row = self.row()
        value = generation.from_environment(analysis_profile=row['state']['analysis_profile'])
        for config, day in ((value, None), (None, '2026-10-04')):
            with self.subTest(config=config, day=day), self.assertRaisesRegex(WorkerError, 'comparison_has_generation'):
                self.bind(row, config, day)
        self.bind(row)
        session.check_events(self.conn, row, ['event_000'], None, 'gpu_comparison', None)
        for kind in session.KINDS:
            with self.subTest(kind=kind), self.assertRaisesRegex(WorkerError, 'generation_not_approved'):
                session.claim(self.conn, row, 'event_000', kind, 'a' * 64, session.model_for(kind))
        self.assertEqual(session.get(self.conn)['claims'], [])

    def test_comparison_claim_in_database_is_rejected(self):
        self.start()
        self.db.rows[self.marker['name']]['claims'] = [{'injected': True}]
        with self.assertRaisesRegex(WorkerError, 'generation_not_approved'):
            session.get(self.conn)

    def test_model_policy_change_or_bool_numeric_fields_fail_closed(self):
        self.start()
        for key, value in [('purpose', 'full_report'), ('approved_input_sha256', '2' * 64)]:
            self.select(dict(self.marker, **{key: value}))
            with self.subTest(key=key), self.assertRaisesRegex(WorkerError, 'model_policy_changed'):
                session.get(self.conn)
        self.select(self.marker)
        self.db.rows[self.marker['name']]['max_jobs'] = True
        with self.assertRaises(WorkerError):
            session.get(self.conn)

    def test_invalid_and_extended_markers_fail_closed(self):
        for changed in ({'max_jobs': 3}, {'per_job_seconds': 301}, {'window_seconds': 3600},
                        {'name': 'mlops-session-20260230-01'}, {'name': 'mlops-session-20261004-00'},
                        {'versions': ['B', 'R2', 'A']}, {'approved_input_sha256': None},
                        {'purpose': 'auto'}, {'extra': True}):
            self.select(dict(self.marker, **changed))
            with self.subTest(changed=changed), self.assertRaises(WorkerError):
                session.configured()

    def test_deadline_cannot_be_extended_and_stop_keeps_bindings(self):
        self.start()
        row = self.row()
        self.bind(row)
        before = deepcopy(self.db.rows)
        self.db.time += 1000
        with self.assertRaisesRegex(WorkerError, 'existing_window_preserved'):
            self.start()
        self.assertEqual(before, self.db.rows)
        with self.assertRaisesRegex(WorkerError, 'expired'):
            session.check_dispatch(self.conn, row)
        session.stop(self.conn)
        self.assertEqual(session.get(self.conn)['bindings'], before[self.marker['name']]['bindings'])

    def test_new_window_cannot_rebind_old_job_or_gpu_identity(self):
        self.start()
        old = self.row()
        self.bind(old)
        before = deepcopy(self.db.rows)
        self.marker = dict(self.marker, name='mlops-session-20261004-02')
        self.select(self.marker)
        self.db.time += 1000
        self.start()
        for row in (old, self.row('B', '5')):
            with self.subTest(worker=row['worker_job_id']), self.assertRaisesRegex(WorkerError, 'old_job_preserved'):
                self.bind(row)
        self.assertEqual({key: self.db.rows[key] for key in before}, before)

    def test_marker_change_during_dispatch_fails_closed(self):
        self.start()
        row = self.row()
        self.bind(row)
        original = session.row_binding
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.select(dict(self.marker, purpose='full_report'))
            return result
        with patch.object(session, 'row_binding', side_effect=changed):
            with self.assertRaisesRegex(WorkerError, 'marker_changed_during_dispatch'):
                session.check_dispatch(self.conn, row)

    def test_full_report_policy_preserves_existing_claim_rules(self):
        self.marker = dict(self.marker, purpose='full_report')
        self.select(self.marker)
        self.start()
        row = self.row('R2', '3')
        value = generation.from_environment(analysis_profile=row['state']['analysis_profile'])
        self.bind(row, value, '2026-10-04')
        session.check_events(self.conn, row, ['event_000'], value, 'full_report', '2026-10-04')
        claim = session.claim(self.conn, row, 'event_000', 'scene', 'a' * 64, session.MODEL)
        self.assertEqual(claim['worker_job_id'], row['worker_job_id'])
        with self.assertRaisesRegex(WorkerError, 'already_claimed_no_retry'):
            session.claim(self.conn, row, 'event_000', 'scene', 'a' * 64, session.MODEL)
        with self.assertRaisesRegex(WorkerError, 'cpu_pipeline_invalid'):
            session.claim(self.conn, row, 'event_000', 'cpu_retrieval', 'a' * 64,
                          session.model_for('cpu_retrieval'))


if __name__ == '__main__':
    unittest.main()
