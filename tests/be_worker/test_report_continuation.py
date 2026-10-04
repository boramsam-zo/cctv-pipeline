"""Synthetic evidence and fake DB only: no model, network or real DB writes."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import analysis_profiles as profiles, hour_session as session
from apps.be_worker import report_continuation as policy, selection_jobs, team_settings, worker
from apps.be_worker import flow_service
from apps.be_worker.common import WorkerError
from tests.be_worker.test_hour_session import FakeDB
from tests.be_worker.test_mlops_session import ModelConnection


class ReportConnection(ModelConnection):
    def __init__(self, db):
        super().__init__(db)
        self.tasks, self.busy, self.generated = [], False, False

    def execute(self, sql, args=None):
        if sql.startswith('SELECT name,payload'):
            value = [{'name': k, 'payload': deepcopy(v)} for k, v in self.db.rows.items()
                     if k.startswith(('mlops-session-', 'p6-hour-session-'))]
            return SimpleNamespace(fetchall=lambda: value)
        if sql.startswith('SELECT * FROM cctv_jobs.analysis_tasks'):
            return SimpleNamespace(fetchall=lambda: deepcopy(self.tasks))
        if ' AS busy' in sql:
            return SimpleNamespace(fetchone=lambda: {'busy': self.busy})
        if ' AS found' in sql:
            return SimpleNamespace(fetchone=lambda: {'found': self.generated})
        return super().execute(sql, args)


class ReportContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = patch.dict(os.environ, BE_SPOOL_PATH=str(self.root),
            CCTV_ANALYSIS_DEPLOYMENT=profiles.MLOPS_DEPLOYMENT, BE_REPORT_PIPELINE='team-rag-v1')
        self.env.start(); self.addCleanup(self.env.stop)
        self.db = FakeDB(); self.conn = ReportConnection(self.db)
        self.profile = profiles.profile('B')
        # Only public code/contracts are copied. Private corpus/index bytes are
        # synthetic here: these tests exercise admission identity, not search.
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
        contract_root = patch.object(team_settings, 'ROOT', resources)
        contract_root.start(); self.addCleanup(contract_root.stop)
        self.settings = team_settings.from_environment(os.environ, self.profile)
        self.wid, self.provider, self.event = 'a' * 32, 'b' * 32, 'event_000'
        self.directory = self.root / self.wid; self.directory.mkdir(mode=0o700)
        self.bundle = self.directory / 'scenes' / self.event / 'input'; self.bundle.mkdir(parents=True)
        data = b'0000ftyp-synthetic-video'
        sha = hashlib.sha256(data).hexdigest()
        gpu = profiles.gpu_identity(sha, self.profile)['gpu_key']
        manifest = b'{"synthetic_manifest":true}'
        manifest_sha = hashlib.sha256(manifest).hexdigest()
        handoff = dict(worker_job_id=self.wid, provider_job_id=self.provider, input_sha256=sha,
                       scene_configs=['scene-event_000.json'], manifest_sha256=manifest_sha)
        self.row = dict(worker_job_id=self.wid, directory_id=self.wid, submit_attempts=1,
            request_key='ab-gpu-' + gpu, handoff=handoff,
            state=dict(worker_job_id=self.wid, provider_job_id=self.provider, input_sha256=sha,
                input_bytes=len(data), gpu_key=gpu, analysis_profile=self.profile, post_attempts=1,
                status='handoff_ready', manifest_sha256=manifest_sha, gemini_api_calls=0,
                config={'runpod_url': 'https://syntheticpod-8000.proxy.runpod.net',
                        's3_bucket': 'test-only', 's3_region': 'ap-northeast-2'}))
        self.config = dict(bucket='test-only', region='ap-northeast-2', job_id=self.provider,
            event_id=self.event, expected_input_sha256=sha, analysis_profile=self.profile, gpu_key=gpu)
        values = {'input.mp4': data, 'handoff.json': json.dumps(handoff).encode(),
            'normalized-result.json': json.dumps({'status': 'completed', 'version': 'B', 'input_sha256': sha}).encode(),
            'model-binding.json': b'{}', 'scene-event_000.json': json.dumps(self.config).encode(),
            'scenes/event_000/input/manifest.json': manifest,
            'scenes/event_000/input/fetch.complete.json': b'{}',
            'scenes/event_000/input/source.json': json.dumps(self.config).encode(),
            'scenes/event_000/input/clip.mp4': b'0000ftyp-evidence'}
        for path, body in values.items():
            (self.directory / path).write_bytes(body)
        old_marker = dict(schema=session.MODEL_SESSION_SCHEMA, name='mlops-session-20261005-01',
            versions=['B', 'R2'], purpose='gpu_comparison', max_jobs=2, per_job_seconds=300,
            window_seconds=900, approved_input_sha256=sha)
        old_binding = dict(input_sha256=sha, input_bytes=len(data), gpu_key=gpu,
                           version='B', settings=None, reference_date=None, event_ids=[self.event])
        self.old = dict(old_marker, started_at=0., deadline_at=900., stopped=False,
                        bindings={self.wid: old_binding}, claims=[])
        self.db.rows[old_marker['name']] = deepcopy(self.old)
        self.task = dict(task_id=selection_jobs.identifier(dict(gpu_key=gpu, purpose='gpu_comparison',
            generation_settings=None, reference_date=None)), worker_job_id=self.wid, purpose='gpu_comparison',
            version='B', gpu_key=gpu, generation_settings=None, reference_date=None,
            status='comparison_completed', paused=False, error_code=None, finished_at='synthetic-completed')
        self.conn.tasks = [self.task]
        (self.directory / 'state.json').write_text(json.dumps(self.row['state']))
        (self.directory / ('task-' + self.task['task_id'] + '.json')).write_text(json.dumps(selection_jobs.snapshot(self.task)))
        source = dict(session=old_marker['name'], session_sha256=policy.fingerprint(self.old),
            worker_job_id=self.wid, comparison_task_id=self.task['task_id'], input_sha256=sha,
            input_bytes=len(data), gpu_key=gpu, provider_job_id=self.provider, event_id=self.event,
            analysis_profile=self.profile, handoff_sha256=policy.fingerprint(handoff),
            manifest_sha256=manifest_sha,
            result_sha256=policy.file_sha(self.directory / 'normalized-result.json'),
            files={path: policy.file_sha(self.directory / path) for path in values})
        self.marker = dict(schema=policy.SCHEMA, name='mlops-session-20261005-02', purpose='full_report',
            max_jobs=1, window_seconds=900, source=source, generation_settings=self.settings)
        session.marker_path().write_text(json.dumps(self.marker))

    def start(self):
        return session.initialize(self.conn, self.db.time, self.db.time + 900)

    def test_original_session_immutable_and_exact_job_prebound(self):
        before = deepcopy(self.old)
        self.start()
        with self.conn.transaction():
            session.check_admission(self.conn, self.row['state']['input_sha256'],
                                    self.row['state']['input_bytes'], ['B'], 'full_report')
            session.bind_job(self.conn, self.row, self.settings, None)
        session.check_dispatch(self.conn, self.row)
        session.check_events(self.conn, self.row, [self.event], self.settings, 'full_report', None)
        self.assertEqual(self.db.rows[self.old['name']], before)
        self.assertEqual(list(session.get(self.conn)['bindings']), [self.wid])
        self.assertEqual(session.get(self.conn)['claims'], [])

    def test_scene_and_cpu_once_with_no_external_report_or_embedding(self):
        self.start()
        for kind in ('embedding', 'report', 'cpu_retrieval'):
            with self.subTest(kind=kind), self.assertRaises(WorkerError):
                session.claim(self.conn, self.row, self.event, kind, 'c' * 64, session.model_for(kind))
        for kind in ('scene', 'cpu_retrieval'):
            session.claim(self.conn, self.row, self.event, kind, 'c' * 64, session.model_for(kind))
            with self.assertRaisesRegex(WorkerError, 'already_claimed'):
                session.claim(self.conn, self.row, self.event, kind, 'c' * 64, session.model_for(kind))
        self.assertEqual(len(session.get(self.conn)['claims']), 2)
        self.assertEqual(self.db.rows[self.old['name']], self.old)

    def test_source_mutation_or_different_job_blocks_every_stage(self):
        self.start()
        for key, value in [('worker_job_id', 'd' * 32), ('status', 'prepared'), ('gpu_key', 'bad'),
                           ('input_sha256', 'd' * 64), ('provider_job_id', 'd' * 32), ('post_attempts', 2)]:
            changed = deepcopy(self.row); changed['state'][key] = value
            with self.subTest(key=key), self.assertRaises(WorkerError):
                session.check_dispatch(self.conn, changed)
        asset = self.bundle / 'clip.mp4'; asset.write_bytes(b'changed')
        with self.assertRaisesRegex(WorkerError, 'evidence_changed'):
            session.claim(self.conn, self.row, self.event, 'scene', 'c' * 64, session.MODEL)
        self.assertEqual(session.get(self.conn)['claims'], [])

    def test_other_input_model_purpose_or_event_rejected(self):
        self.start(); source = self.marker['source']
        for sha, versions, purpose in [('d' * 64, ['B'], 'full_report'),
                                       (source['input_sha256'], ['R2'], 'full_report'),
                                       (source['input_sha256'], ['B'], 'gpu_comparison')]:
            with self.assertRaises(WorkerError), self.conn.transaction():
                session.check_admission(self.conn, sha, source['input_bytes'], versions, purpose)
        with self.assertRaises(WorkerError):
            session.check_events(self.conn, self.row, ['event_001'], self.settings, 'full_report', None)

    def test_expiry_remaining_time_and_old_session_change_fail_closed(self):
        self.start(); self.db.time += 781
        with self.assertRaisesRegex(WorkerError, 'insufficient_call_time'):
            session.claim(self.conn, self.row, self.event, 'scene', 'c' * 64, session.MODEL)
        self.db.time += 119
        with self.assertRaisesRegex(WorkerError, 'expired'):
            session.check_dispatch(self.conn, self.row)
        self.db.rows[self.old['name']]['stopped'] = True
        with self.assertRaisesRegex(WorkerError, 'original_session_changed'):
            session.get(self.conn)

    def test_gpu_submit_blocks_before_any_health_binding_or_post(self):
        api, trace = Mock(), Mock()
        with patch('apps.be_worker.mlops_jobs.freeze_binding') as freeze, self.assertRaisesRegex(
                WorkerError, 'gpu_submission_forbidden'):
            worker.submit_once(self.directory, {'status': 'prepared', 'post_attempts': 0}, api, trace)
        self.assertFalse(api.mock_calls); self.assertFalse(trace.mock_calls); freeze.assert_not_called()

    def test_admission_never_creates_a_new_gpu_queue(self):
        self.start()
        store = Mock(connection=self.conn)
        store.lock.side_effect = lambda _: nullcontext()
        store.by_key.return_value = None
        with patch('apps.be_worker.selection_jobs.enqueue') as enqueue, self.assertRaisesRegex(
                WorkerError, 'cannot_enqueue_gpu'):
            selection_jobs.admit(store, self.directory / 'input.mp4', None, 'B', 'full_report',
                                  spool=self.root, config=self.row['state']['config'], jsonb=lambda v: v)
        enqueue.assert_not_called()

    def test_mutated_policy_or_second_binding_never_grants_calls(self):
        self.start()
        for field, value in [('window_seconds', 901), ('max_jobs', 2), ('purpose', 'gpu_comparison')]:
            altered = deepcopy(self.marker); altered[field] = value
            with self.assertRaises(WorkerError):
                policy.validate_marker(altered)
        self.db.rows[self.marker['name']]['bindings']['d' * 32] = policy.binding(self.marker)
        with self.assertRaisesRegex(WorkerError, 'binding_changed'):
            session.get(self.conn)

    def test_existing_process_events_reaches_scene_and_report_without_gpu(self):
        self.start()
        queue = Mock()
        queue.store.connection = self.conn
        queue.store.rows.return_value = [deepcopy(self.row)]
        scenes, reports, emit = Mock(), Mock(), Mock()
        scenes.describe.side_effect = lambda *a, **kw: session.claim(
            self.conn, self.row, self.event, 'scene', 'c' * 64, session.MODEL)
        reports.run.side_effect = lambda *a, **kw: session.claim(
            self.conn, self.row, self.event, 'cpu_retrieval', 'd' * 64,
            session.model_for('cpu_retrieval'))
        with patch.object(worker, 'submit_once', side_effect=AssertionError('GPU forbidden')) as gpu:
            flow_service.process_events(queue, self.row, None, emit, scenes, reports,
                settings=self.settings, purpose='full_report', previews=Mock())
        gpu.assert_not_called()
        scenes.describe.assert_called_once()
        reports.run.assert_called_once()
        self.assertEqual(emit.call_args.args, ('completed',))
        self.assertEqual([c['kind'] for c in session.get(self.conn)['claims']], ['scene', 'cpu_retrieval'])
        self.assertEqual(self.db.rows[self.old['name']], self.old)


if __name__ == '__main__':
    unittest.main()
