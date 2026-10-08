"""Public-demo admission/paid-call boundaries with durable fake DB, no models."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles as profiles, generation_settings as generation
from apps.be_worker import demo_session as demo, hour_session as session, team_settings
from apps.be_worker.common import WorkerError
from tests.be_worker.test_hour_session import Connection, FakeDB


class DemoConnection(Connection):
    def execute(self, query, args=None):
        if query.startswith('SELECT count(*) AS count FROM cctv_jobs.analysis_tasks'):
            return SimpleNamespace(fetchone=lambda: {'count': getattr(self.db, 'busy_jobs', 0)})
        if query.startswith('SELECT EXISTS') and len(args) == 3:
            already = any(name != args[0] and (
                args[1] in payload.get('bindings', {}) or any(b.get('gpu_key') == args[2]
                    for b in payload.get('bindings', {}).values()))
                for name, payload in self.db.rows.items()
                if name.startswith(('p6-hour-session-', 'mlops-session-', 'demo-session-')))
            return SimpleNamespace(fetchone=lambda: {'already_bound': already})
        return super().execute(query, args)


class DemoSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        # Admission freezes resource hashes; it does not execute retrieval.
        # Copy only public contracts/code and invent corpus/index bytes so this
        # fixture also works in the source-only checkout without private data.
        resources = root / 'synthetic-contract-resources'
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
        contract_root = patch.object(team_settings, 'ROOT', resources)
        contract_root.start()
        self.addCleanup(contract_root.stop)
        release = root / 'release.json'
        release.write_text(json.dumps(dict(schema_version='cctv-mlops-deployment-v1',
            deployment=profiles.MLOPS_DEPLOYMENT, gpu=profiles.PORTABLE_GPU,
            image_manifest_sha256='d' * 64, execution_adapter_sha256='e' * 64,
            code_files={'runtime/model_classifier.py': 'f' * 64})))
        for target in (patch.dict(os.environ, {'BE_SPOOL_PATH': str(root),
                'BE_REPORT_PIPELINE': team_settings.PIPELINE,
                'CCTV_ANALYSIS_DEPLOYMENT': profiles.MLOPS_DEPLOYMENT}),
                patch.dict(profiles._RELEASE_CATALOGS, {profiles.MLOPS_DEPLOYMENT: release})):
            target.start()
            self.addCleanup(target.stop)
        self.db = FakeDB()
        self.conn = DemoConnection(self.db)
        self.marker = dict(schema=demo.SCHEMA, name='demo-session-20261006-01',
            versions=['B', 'R2'], purpose='full_report', max_jobs=5,
            per_job_seconds=300, window_seconds=28800,
            max_upload_bytes=demo.DEFAULT_UPLOAD_BYTES, max_video_seconds=60)
        self.select(self.marker)

    def select(self, marker):
        session.marker_path().write_text(json.dumps(marker))

    def start(self):
        return session.initialize(self.conn, self.db.time, self.db.time + self.marker['window_seconds'])

    def row(self, version='B', digit='2', sha_digit=None):
        profile = profiles.profile(version)
        sha = (sha_digit or digit) * 64
        return dict(worker_job_id=digit * 32, state=dict(worker_job_id=digit * 32,
            input_sha256=sha, input_bytes=42, analysis_profile=profile,
            gpu_key=profiles.gpu_identity(sha, profile)['gpu_key']),
            handoff={'scene_configs': ['scene-event_000.json']})

    def settings(self, row):
        return generation.from_environment(analysis_profile=row['state']['analysis_profile'])

    def bind(self, row, settings=None):
        value = self.settings(row) if settings is None else settings
        with self.conn.transaction():
            session.check_admission(self.conn, row['state']['input_sha256'], row['state']['input_bytes'],
                [row['state']['analysis_profile']['version']], 'full_report')
            return session.bind_job(self.conn, row, value, None)

    def ready(self):
        self.start()
        row = self.row()
        self.bind(row)
        session.check_events(self.conn, row, ['event_000'], self.settings(row), 'full_report', None)
        return row

    def test_eight_hour_policy_roundtrip_new_inputs_b_and_r2(self):
        before = deepcopy(self.db.rows)
        self.start()
        for row in (self.row(), self.row('R2', '3'), self.row('B', '4')):
            self.bind(row)
            session.check_dispatch(self.conn, row)
        value = session.get(self.conn)
        self.assertEqual(session.marker_from_state(value), self.marker)
        self.assertEqual(value['deadline_at'] - value['started_at'], 28800)
        self.assertEqual(len(value['bindings']), 3)
        self.assertEqual({name: self.db.rows[name] for name in before}, before)

    def test_invalid_policy_and_boolean_numbers_fail_closed(self):
        for change in ({'max_jobs': 0}, {'max_jobs': 21}, {'max_jobs': True},
                       {'window_seconds': 59}, {'window_seconds': 86401}, {'window_seconds': True},
                       {'per_job_seconds': 301}, {'max_video_seconds': 121},
                       {'max_upload_bytes': 0}, {'max_upload_bytes': 200 * 1024 * 1024 + 1},
                       {'versions': ['R2']}, {'versions': ['B', 'A']}, {'purpose': 'gpu_comparison'},
                       {'name': 'demo-session-20260230-01'}, {'name': 'demo-session-20261006-00'},
                       {'extra': 'not accepted'}):
            with self.subTest(change=change):
                self.select(dict(self.marker, **change))
                with self.assertRaises(WorkerError):
                    session.configured()

    def test_twenty_job_policy_roundtrip_preserves_window_and_default(self):
        self.marker['max_jobs'] = 20
        self.select(self.marker)
        self.start()
        self.bind(self.row())
        value = session.get(self.conn)
        self.assertEqual(session.marker_from_state(value), self.marker)
        self.assertEqual(value['max_jobs'], 20)
        self.assertEqual(value['deadline_at'] - value['started_at'], 28800)
        self.assertEqual(demo.DEFAULT_MAX_JOBS, 5)

    def test_policy_edit_and_restart_do_not_extend_existing_grant(self):
        self.start()
        before = deepcopy(self.db.rows)
        self.select(dict(self.marker, max_jobs=6))
        with self.assertRaisesRegex(WorkerError, 'policy_changed'):
            session.get(self.conn)
        self.select(self.marker)
        self.db.time += 1
        with self.assertRaisesRegex(WorkerError, 'existing_window_preserved'):
            self.start()
        self.assertEqual(self.db.rows, before)

    def test_single_model_and_total_capacity_reject_extra_calls(self):
        self.marker.update(versions=['B'], max_jobs=1)
        self.select(self.marker)
        self.start()
        self.bind(self.row())
        with self.assertRaisesRegex(WorkerError, 'selection_not_approved'):
            self.bind(self.row('R2', '3'))
        with self.assertRaisesRegex(WorkerError, 'capacity_reached'):
            self.bind(self.row('B', '4'))
        self.assertEqual(len(session.get(self.conn)['bindings']), 1)

    def test_large_upload_and_multi_model_selection_rejected(self):
        self.start()
        for size, versions, purpose in ((self.marker['max_upload_bytes'] + 1, ['B'], 'full_report'),
                (42, ['B', 'R2'], 'full_report'), (42, ['B'], 'gpu_comparison')):
            with self.subTest(size=size, versions=versions), self.assertRaises(WorkerError):
                with self.conn.transaction():
                    session.check_admission(self.conn, '2' * 64, size, versions, purpose)
        self.assertEqual(session.get(self.conn)['bindings'], {})

    def test_active_job_blocks_sequential_admission_until_terminal(self):
        self.start()
        self.bind(self.row())
        self.db.busy_jobs = 1
        with self.assertRaisesRegex(WorkerError, 'demo_session_busy'):
            self.bind(self.row('R2', '3'))
        self.assertEqual(len(session.get(self.conn)['bindings']), 1)
        self.db.busy_jobs = 0
        self.bind(self.row('R2', '3'))
        self.assertEqual(len(session.get(self.conn)['bindings']), 2)

    def test_same_gpu_identity_cannot_be_renamed_or_rebound_after_close(self):
        self.start()
        row = self.row()
        self.bind(row)
        first = session.get(self.conn)
        self.bind(row)
        self.assertEqual(session.get(self.conn), first)
        with self.assertRaisesRegex(WorkerError, 'gpu_identity_changed'):
            self.bind(self.row('B', '5', sha_digit='2'))
        session.stop(self.conn)
        history = deepcopy(self.db.rows)
        self.marker['name'] = 'demo-session-20261006-02'
        self.select(self.marker)
        self.start()
        for old in (row, self.row('B', '5', sha_digit='2')):
            with self.assertRaisesRegex(WorkerError, 'old_job_preserved'):
                self.bind(old)
        self.assertEqual({name: self.db.rows[name] for name in history}, history)

    def test_only_first_generated_candidate_is_claimable_and_handoff_preserved(self):
        self.start()
        row = self.row()
        row['handoff']['scene_configs'] = ['scene-event_010.json', 'scene-event_002.json', 'scene-event_003.json']
        before = deepcopy(row)
        self.bind(row)
        selected = demo.representative_events(['event_010', 'event_002', 'event_003'])
        self.assertEqual(selected, ['event_002'])
        session.check_events(self.conn, row, selected, self.settings(row), 'full_report', None)
        with self.assertRaisesRegex(WorkerError, 'event_not_approved'):
            session.claim(self.conn, row, 'event_003', 'scene', 'a' * 64, session.MODEL)
        session.claim(self.conn, row, 'event_002', 'scene', 'a' * 64, session.MODEL)
        self.assertEqual(row, before)

    def test_candidate_ids_must_be_unique_and_valid(self):
        for values in (['event_0', 'event_0'], ['wrong'], ['event_-1'], ['event_0', None]):
            with self.subTest(values=values), self.assertRaises(WorkerError):
                demo.representative_events(values)

    def test_bound_job_excludes_unrelated_legacy_work(self):
        self.start()
        row = self.row()
        self.bind(row)
        self.assertTrue(demo.bound_job(self.conn, row))
        self.assertFalse(demo.bound_job(self.conn, self.row('B', '3')))
        changed = deepcopy(row)
        changed['state']['input_bytes'] += 1
        with self.assertRaisesRegex(WorkerError, 'bound_job_changed'):
            demo.bound_job(self.conn, changed)

    def test_stop_and_expiry_block_admission_dispatch_and_all_claims(self):
        row = self.ready()
        before = deepcopy(session.get(self.conn)['bindings'])
        self.db.time += self.marker['window_seconds']
        for reason in ('expired', 'stopped'):
            if reason == 'stopped':
                session.stop(self.conn)
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(WorkerError, reason):
                    with self.conn.transaction():
                        session.check_admission(self.conn, '3' * 64, 42, ['B'], 'full_report')
                with self.assertRaisesRegex(WorkerError, reason):
                    session.check_dispatch(self.conn, row)
                for kind in ('scene', 'cpu_retrieval'):
                    with self.assertRaisesRegex(WorkerError, reason):
                        session.claim(self.conn, row, 'event_000', kind, 'a' * 64, session.model_for(kind))
        saved = session.get(self.conn)
        self.assertEqual(saved['claims'], [])
        self.assertEqual(saved['bindings'], before)

    def test_only_one_vlm_and_one_cpu_retrieval_and_no_external_report(self):
        row = self.ready()
        for kind in ('embedding', 'report'):
            with self.assertRaisesRegex(WorkerError, 'external_report_forbidden'):
                session.claim(self.conn, row, 'event_000', kind, 'a' * 64, session.model_for(kind))
        for kind in ('scene', 'cpu_retrieval'):
            session.claim(self.conn, row, 'event_000', kind, 'a' * 64, session.model_for(kind))
            with self.assertRaisesRegex(WorkerError, 'already_claimed_no_retry'):
                session.claim(self.conn, row, 'event_000', kind, 'a' * 64, session.model_for(kind))
        self.assertEqual(len(session.get(self.conn)['claims']), 2)

    def test_legacy_external_report_settings_cannot_bind_demo(self):
        self.start()
        row = self.row()
        legacy = generation.from_environment(environ={'BE_REPORT_PIPELINE': 'legacy'},
                                              analysis_profile=row['state']['analysis_profile'])
        with self.assertRaisesRegex(WorkerError, 'cpu_report_required'):
            self.bind(row, legacy)
        self.assertEqual(session.get(self.conn)['bindings'], {})

    def test_competing_admissions_cannot_exceed_total_capacity(self):
        self.marker['max_jobs'] = 1
        self.select(self.marker)
        self.start()
        outcomes = []
        def admit(digit):
            conn = DemoConnection(self.db)
            row = self.row(digit=digit)
            try:
                with conn.transaction():
                    session.check_admission(conn, row['state']['input_sha256'], 42, ['B'], 'full_report')
                    session.bind_job(conn, row, self.settings(row), None)
                outcomes.append('ok')
            except WorkerError:
                outcomes.append('blocked')
        threads = [threading.Thread(target=admit, args=(digit,)) for digit in ('2', '3')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertCountEqual(outcomes, ['ok', 'blocked'])
        self.assertEqual(len(session.get(self.conn)['bindings']), 1)

    def test_competing_claims_commit_only_one_attempt(self):
        row = self.ready()
        outcomes = []
        def reserve():
            try:
                session.claim(DemoConnection(self.db), row, 'event_000', 'scene', 'a' * 64, session.MODEL)
                outcomes.append('ok')
            except WorkerError:
                outcomes.append('blocked')
        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertCountEqual(outcomes, ['ok', 'blocked'])
        self.assertEqual(len(session.get(self.conn)['claims']), 1)


if __name__ == '__main__':
    unittest.main()
