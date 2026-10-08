"""Lifecycle plans, exclusive activation and rollback; no network/models."""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles as profiles, demo_control as control
from apps.be_worker import demo_session as demo, hour_session as session
from apps.be_worker.common import WorkerError
from tests.be_worker import test_demo_session as session_tests


class ControlConnection(session_tests.DemoConnection):
    def execute(self, query, args=None):
        if query.startswith('SELECT name,payload'):
            return SimpleNamespace(fetchall=lambda: [dict(name=name, payload=deepcopy(value))
                for name, value in sorted(self.db.rows.items())
                if name.startswith(('p6-hour-session-', 'mlops-session-', 'demo-session-'))])
        if query.endswith(' AS busy'):
            return SimpleNamespace(fetchone=lambda: {'busy': getattr(self.db, 'work_busy', False)})
        return super().execute(query, args)


class FakeStore:
    def __init__(self):
        self.locks = []
    def lock(self, kind):
        self.locks.append(kind)
        return nullcontext()


class FakeAPI:
    def __init__(self, value):
        self.value = value
        self.health_calls = 0
    def health(self, profile):
        self.health_calls += 1
        return deepcopy(self.value)


class DemoControlTests(unittest.TestCase):
    select = session_tests.DemoSessionTests.select

    def setUp(self):
        session_tests.DemoSessionTests.setUp(self)
        resolved = str(session.marker_path().parent.resolve())
        environment = patch.dict('os.environ', {'BE_SPOOL_PATH': resolved})
        environment.start()
        self.addCleanup(environment.stop)
        session.marker_path().unlink()
        self.conn = ControlConnection(self.db)
        self.store = FakeStore()
        self.api = FakeAPI(dict(api_schema='runpod-ab-api-v1', storage='s3',
            supported_versions=['B', 'R2'], profiles={version: profiles.gpu_identity('0' * 64,
                profiles.profile(version))['profile_sha256'] for version in ('B', 'R2')},
            limits={'max_jobs': 5, 'per_job_seconds': 300, 'whole_service_seconds': 28800},
            admissions=0, deadline_at=self.db.time + 28800))
        self.runtime_checks = []

    def prepare(self, marker):
        self.runtime_checks.append(deepcopy(marker))
        return {'cpu_model_files_verified': True, 'vlm_key_readable': True, 'model_calls': 0}

    def start(self, *, apply=False, marker=None):
        return control.start(self.conn, self.store, marker or self.marker,
                             apply=apply, api=self.api, prepare=self.prepare)

    def previous(self, *, stopped=True):
        marker = {'schema': session.MARKER['schema'], 'name': 'p6-hour-session-20261006-02', 'max_jobs': 1}
        value = dict(marker, started_at=0, deadline_at=3600, stopped=stopped, bindings={}, claims=[])
        self.db.rows[marker['name']] = value
        raw = json.dumps(marker, indent=3).encode()
        session.marker_path().write_bytes(raw)
        return marker, raw

    def test_default_start_is_read_only_and_checks_existing_runtime(self):
        before = deepcopy(self.db.rows)
        result = self.start()
        self.assertEqual(result['status'], 'demo_start_plan')
        self.assertFalse(result['apply'])
        self.assertEqual(result['new_gpu_calls'], 0)
        self.assertEqual(self.api.health_calls, 1)
        self.assertEqual(len(self.runtime_checks), 1)
        self.assertEqual(self.db.rows, before)
        self.assertFalse(session.marker_path().exists())
        self.assertEqual(list(session.marker_path().parent.glob('.demo-start-*')), [])

    def test_apply_preserves_previous_marker_bytes_history_and_uses_db_clock(self):
        old, raw = self.previous()
        before = deepcopy(self.db.rows)
        result = self.start(apply=True)
        self.assertEqual(result['status'], 'demo_started')
        self.assertEqual(result['started_at'], self.db.time)
        self.assertEqual(result['deadline_at'], self.db.time + 28800)
        self.assertEqual(session.current_marker(), self.marker)
        self.assertEqual({name: self.db.rows[name] for name in before}, before)
        intent = session.marker_path().parent / ('.demo-start-' + self.marker['name'] + '.json')
        receipt = json.loads(intent.read_text())
        self.assertEqual(control.base64.b64decode(receipt['previous_marker_base64']), raw)
        self.assertEqual(intent.stat().st_mode & 0o777, 0o600)
        self.assertTrue(intent.with_suffix('.ready.json').exists())

    def test_active_previous_session_and_busy_work_are_preserved(self):
        self.previous(stopped=False)
        before = deepcopy(self.db.rows)
        with self.assertRaisesRegex(WorkerError, 'active_session_preserved'):
            self.start(apply=True)
        self.assertEqual(self.api.health_calls, 0)
        self.assertEqual(self.db.rows, before)
        name = 'p6-hour-session-20261006-02'
        self.db.rows[name]['stopped'] = True
        self.db.work_busy = True
        with self.assertRaisesRegex(WorkerError, 'unfinished_work_preserved'):
            self.start(apply=True)

    def test_expired_previous_window_is_not_extended(self):
        marker, _ = self.previous(stopped=False)
        self.db.rows[marker['name']].update(started_at=-3000, deadline_at=600)
        before = deepcopy(self.db.rows[marker['name']])
        self.start(apply=True)
        self.assertEqual(self.db.rows[marker['name']], before)

    def test_provider_budget_catalog_and_used_window_rejected_before_mutation(self):
        original = deepcopy(self.api.value)
        changes = ({'admissions': 1}, {'admissions': False},
                   {'limits': dict(original['limits'], max_jobs=4)},
                   {'profiles': {'B': '0' * 64}}, {'deadline_at': self.db.time + 1000},
                   {'deadline_at': self.db.time + 28806}, {'deadline_at': float('nan')})
        for change in changes:
            self.api.value = dict(original, **change)
            with self.subTest(change=change), self.assertRaises(WorkerError):
                self.start(apply=True)
            self.assertFalse(session.marker_path().exists())
            self.assertFalse(any(name.startswith('demo-session-') for name in self.db.rows))

    def test_failed_initialize_restores_original_bytes_and_consumes_name(self):
        _, raw = self.previous()
        before = deepcopy(self.db.rows)
        with patch.object(session, 'initialize', side_effect=RuntimeError('simulated private failure')):
            with self.assertRaisesRegex(WorkerError, 'activation_failed_intent_preserved'):
                self.start(apply=True)
        self.assertEqual(session.marker_path().read_bytes(), raw)
        self.assertEqual(self.db.rows, before)
        with self.assertRaisesRegex(WorkerError, 'intent_preserved_no_retry'):
            self.start(apply=True)

    def test_failed_ready_receipt_restores_marker_even_if_new_db_row_committed(self):
        _, raw = self.previous()
        original = control.write_exclusive
        def fail_ready(path, value):
            if str(path).endswith('.ready.json'):
                raise OSError('simulated full disk')
            return original(path, value)
        with patch.object(control, 'write_exclusive', side_effect=fail_ready):
            with self.assertRaisesRegex(WorkerError, 'activation_failed_intent_preserved'):
                self.start(apply=True)
        self.assertEqual(session.marker_path().read_bytes(), raw)
        self.assertIn(self.marker['name'], self.db.rows)
        self.assertEqual(self.db.rows[self.marker['name']]['bindings'], {})
        with self.assertRaisesRegex(WorkerError, 'intent_preserved_no_retry'):
            self.start(apply=True)

    def test_successful_session_name_cannot_be_restarted_even_after_stop(self):
        self.start(apply=True)
        control.stop(self.conn, apply=True)
        before = deepcopy(self.db.rows)
        with self.assertRaisesRegex(WorkerError, 'intent_preserved_no_retry'):
            self.start(apply=True)
        self.assertEqual(self.db.rows, before)

    def test_stop_defaults_to_plan_and_apply_closes_only_current_demo(self):
        self.start(apply=True)
        before = deepcopy(self.db.rows)
        planned = control.stop(self.conn)
        self.assertEqual(planned['status'], 'demo_stop_plan')
        self.assertEqual(self.db.rows, before)
        stopped = control.stop(self.conn, apply=True)
        self.assertTrue(stopped['stopped'])
        self.assertFalse(stopped['power_changed'])
        expected = deepcopy(before)
        expected[self.marker['name']]['stopped'] = True
        self.assertEqual(self.db.rows, expected)

    def test_stop_refuses_legacy_marker(self):
        self.previous()
        before = deepcopy(self.db.rows)
        with self.assertRaisesRegex(WorkerError, 'demo_not_selected'):
            control.stop(self.conn, apply=True)
        self.assertEqual(self.db.rows, before)

    def test_marker_policy_argument_validation_happens_without_network(self):
        args = SimpleNamespace(hours=8, name='demo-session-20261006-03', models=['B', 'R2'],
                               max_jobs=5, max_upload_mib=50, max_video_seconds=60)
        result = control.marker_from_args(args)
        self.assertEqual(result['window_seconds'], 28800)
        args.hours = float('nan')
        with self.assertRaisesRegex(WorkerError, 'hours_invalid'):
            control.marker_from_args(args)
        self.assertEqual(self.api.health_calls, 0)


if __name__ == '__main__':
    unittest.main()
