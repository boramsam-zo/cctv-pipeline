"""Injected DB/API and temporary files only; never contact a provider or secret."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import activate_mlops_session as activation


class Result:
    def __init__(self, value):
        self.value = value
    def fetchone(self):
        return self.value
    def fetchall(self):
        return self.value


class FakeDB:
    def __init__(self):
        self.rows, self.busy, self.now = {}, False, 1000.
        self.fail_commit = False
    def execute(self, sql, args=None):
        if 'clock_timestamp' in sql:
            return Result({'epoch': self.now})
        if ' AS busy' in sql:
            return Result({'busy': self.busy})
        if 'WHERE name=%s' in sql:
            value = self.rows.get(args[0])
            return Result(None if value is None else {'payload': deepcopy(value)})
        if 'SELECT name,payload' in sql:
            return Result([{'name': key, 'payload': deepcopy(value)} for key, value in self.rows.items()])
        raise AssertionError('Unexpected synthetic SQL')
    @contextmanager
    def transaction(self):
        before = deepcopy(self.rows)
        try:
            yield
        except BaseException:
            self.rows = before
            raise
        if self.fail_commit:
            raise RuntimeError('synthetic uncertain commit')


class FakeStore:
    @contextmanager
    def lock(self, _kind):
        yield


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'runpod_url': 'https://oldpod123-8000.proxy.runpod.net',
                                          's3_bucket': 'synthetic-evidence', 's3_region': 'ap-northeast-2'}))
        self.old_config = self.config.read_bytes()
        self.marker = self.root / '.hour-session.json'
        self.old_marker = {'schema': 'cctv-hour-session-v1', 'name': 'p6-hour-session-20261002-01'}
        self.marker.write_text(json.dumps(self.old_marker))
        self.old_marker_bytes = self.marker.read_bytes()
        self.conn = FakeDB()
        self.old = dict(self.old_marker, started_at=-2700., deadline_at=900., stopped=False, bindings={}, claims=[])
        self.conn.rows[self.old['name']] = self.old
        self.api = Mock()
        self.api.health.return_value = {'limits': dict(activation.LIMITS), 'deadline_at': 1800., 'admissions': 0}
        self.candidate = activation.marker_for('mlops-session-20261005-01', 'a' * 64)
        for item in (patch.dict(os.environ, BE_SPOOL_PATH=str(self.root)),
                     patch.object(activation.profiles, 'profile', return_value={'synthetic': True}),
                     patch.object(activation.profiles, 'deployment_for_profile', return_value=activation.DEPLOYMENT)):
            item.start()
            self.addCleanup(item.stop)

    def initialize(self, conn, started, deadline):
        value = dict(self.candidate, started_at=started, deadline_at=deadline,
                     stopped=False, bindings={}, claims=[])
        conn.rows[value['name']] = deepcopy(value)
        return value

    def run_activation(self):
        return activation.activate(self.conn, self.api, FakeStore(), candidate=self.candidate,
                                   url='https://newpod123-8000.proxy.runpod.net',
                                   config_path=self.config, marker_path=self.marker)

    def assert_originals(self):
        self.assertEqual(self.config.read_bytes(), self.old_config)
        self.assertEqual(self.marker.read_bytes(), self.old_marker_bytes)
        self.assertEqual(self.conn.rows, {self.old['name']: self.old})

    def test_success_preserves_history_replaces_only_url_and_never_reactivates(self):
        with patch.object(activation.session, 'initialize', side_effect=self.initialize):
            result = self.run_activation()
        self.assertEqual(result['jobs_submitted'], 0)
        self.assertEqual(self.api.method_calls[0][0], 'health')
        self.assertEqual(len(self.api.method_calls), 1)
        self.assertEqual(self.conn.rows[self.old['name']], self.old)
        config = json.loads(self.config.read_bytes())
        previous = json.loads(self.old_config)
        self.assertEqual({k: v for k, v in config.items() if k != 'runpod_url'},
                         {k: v for k, v in previous.items() if k != 'runpod_url'})
        self.assertEqual(json.loads(self.marker.read_bytes()), self.candidate)
        with self.assertRaises(activation.ActivationError):
            self.run_activation()

    def test_used_or_short_provider_window_fails_before_file_or_db_change(self):
        for key, value in [('admissions', 1), ('deadline_at', 1200.),
                           ('limits', dict(activation.LIMITS, max_jobs=3))]:
            with self.subTest(key=key):
                self.api.health.return_value = {'limits': dict(activation.LIMITS), 'deadline_at': 1800., 'admissions': 0}
                self.api.health.return_value[key] = value
                with self.assertRaises(activation.ActivationError):
                    self.run_activation()
                self.assert_originals()
                self.assertFalse(list(self.root.glob('.mlops-activation-*')))

    def test_unfinished_work_or_active_previous_window_preserved(self):
        self.conn.busy = True
        with self.assertRaisesRegex(activation.ActivationError, 'unfinished'):
            self.run_activation()
        self.conn.busy = False
        self.conn.rows[self.old['name']]['started_at'] = 0.
        self.conn.rows[self.old['name']]['deadline_at'] = 3600.
        with self.assertRaisesRegex(activation.ActivationError, 'active_session'):
            self.run_activation()
        self.api.health.assert_not_called()
        self.assertEqual(self.config.read_bytes(), self.old_config)

    def test_database_failure_rolls_back_files_and_keeps_private_backup(self):
        with patch.object(activation.session, 'initialize', side_effect=RuntimeError('synthetic DB failure')):
            with self.assertRaisesRegex(activation.ActivationError, 'manual_review'):
                self.run_activation()
        self.assert_originals()
        backup = list(self.root.glob('.mlops-activation-*'))
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0].stat().st_mode & 0o777, 0o600)

    def test_config_failure_restores_old_marker_without_database_grant(self):
        real = activation.replace_file
        def replace(path, before, after):
            if path == self.config:
                raise OSError('synthetic read-only mount')
            real(path, before, after)
        with patch.object(activation, 'replace_file', side_effect=replace):
            with self.assertRaises(activation.ActivationError):
                self.run_activation()
        self.assert_originals()

    def test_uncertain_commit_keeps_pinned_new_marker_and_never_extends_window(self):
        self.conn.fail_commit = True
        with patch.object(activation.session, 'initialize', side_effect=self.initialize):
            with self.assertRaises(activation.ActivationError):
                self.run_activation()
        self.assertEqual(json.loads(self.marker.read_bytes()), self.candidate)
        self.assertEqual(self.conn.rows[self.candidate['name']]['deadline_at'], 1800.)
        with self.assertRaises(activation.ActivationError):
            self.run_activation()

    def test_invalid_proxy_cannot_receive_token(self):
        for value in ('https://example.com', 'https://newpod123-8000.proxy.runpod.net/redirect',
                      'http://newpod123-8000.proxy.runpod.net', 'https://user@newpod123-8000.proxy.runpod.net'):
            with self.assertRaises(activation.ActivationError):
                activation.provider_url(value)


if __name__ == '__main__':
    unittest.main()
