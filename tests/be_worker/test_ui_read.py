"""Read-only UI change hints and validated snapshots; no models or real DB."""
from contextlib import contextmanager
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import flow_service, ui_read, web_api
from apps.be_worker.common import WorkerError


class ReadOnlyConnection:
    def __init__(self, *, team=True):
        self.team = team
        self.calls = []
        self.rows = {
            ui_read.QUEUE_SQL: [{'worker_job_id': 'a' * 32, 'status': 'waiting',
                'error_code': None, 'scene_events': {'event_000': {
                    'status': 'describing', 'updated_at': 'first', 'gemini_api_calls': 1}}}],
            ui_read.FLOW_SQL: [],
            ui_read.TASK_SQL: [{'task_id': 'b' * 32, 'status': 'waiting',
                               'error_code': None, 'paused': False, 'finished_at': None}],
            ui_read.SELECTION_SQL: [],
            ui_read._description_sql('descriptions'): [],
            ui_read._report_sql('report_runs'): [],
            ui_read._description_sql('team_descriptions'): [],
            ui_read._report_sql('team_report_runs'): [],
        }

    def execute(self, sql):
        self.calls.append(sql)
        if not sql.startswith('SELECT'):
            raise AssertionError('Read attempted a DB write')
        result = Mock()
        if sql == ui_read.TEAM_SQL:
            result.fetchone.return_value = {'ok': self.team}
        else:
            result.fetchall.return_value = deepcopy(self.rows[sql])
        return result


class UIReadTests(unittest.TestCase):
    def test_view_exits_readonly_validation_scope_even_after_bad_saved_result(self):
        entered = []
        @contextmanager
        def scope():
            entered.append('enter')
            try:
                yield
            finally:
                entered.append('exit')
        def invalid(conn, identifier):
            self.assertEqual(entered, ['enter'])
            raise WorkerError('saved_integrity_failed')
        with patch('apps.be_worker.ui_validation.readonly_validation', scope), \
             patch.object(flow_service, '_view', side_effect=invalid):
            with self.assertRaisesRegex(WorkerError, 'saved_integrity_failed'):
                flow_service.view(object(), 'a' * 32)
        self.assertEqual(entered, ['enter', 'exit'])

    def test_poll_does_not_load_result_bodies_media_or_worker_heartbeat(self):
        conn = ReadOnlyConnection()
        before = deepcopy(conn.rows)
        with patch.object(flow_service, 'view', side_effect=AssertionError('No full view in poll')):
            first = ui_read.revision(conn)
            conn.rows[ui_read.QUEUE_SQL][0]['scene_events']['event_000']['updated_at'] = 'later'
            second = ui_read.revision(conn)
        self.assertEqual(first, second)
        self.assertRegex(first['revision'], r'^[a-f0-9]{64}$')
        self.assertEqual(set(first), {'revision'})
        sql = '\n'.join(conn.calls)
        for forbidden in ('service_state', 'updated_at', 'poll_count', 'elapsed_seconds',
                          'state->\'result\'', 'SELECT *'):
            self.assertNotIn(forbidden, sql)
        before[ui_read.QUEUE_SQL][0]['scene_events']['event_000']['updated_at'] = 'later'
        self.assertEqual(conn.rows, before)

    def test_meaningful_job_scene_report_and_pause_changes_invalidate(self):
        initial = ui_read.revision(ReadOnlyConnection())
        changes = [
            lambda c: c.rows[ui_read.QUEUE_SQL][0].update(status='no_candidates'),
            lambda c: c.rows[ui_read.QUEUE_SQL][0].update(error_code='provider_failed'),
            lambda c: c.rows[ui_read.QUEUE_SQL][0]['scene_events']['event_000'].update(
                status='description_stored', database_loaded=True, result_sha256='f' * 64),
            lambda c: c.rows[ui_read.TASK_SQL][0].update(paused=True),
            lambda c: c.rows[ui_read.TASK_SQL][0].update(status='completed', finished_at='saved'),
            lambda c: c.rows[ui_read._report_sql('team_report_runs')].append(
                {'report_id': 'c' * 32, 'status': 'report_stored', 'result_sha256': 'd' * 64}),
            lambda c: c.rows[ui_read._report_sql('report_runs')].append(
                {'report_id': 'c' * 32, 'status': 'failed', 'error_code': 'report_invalid'}),
            lambda c: c.rows[ui_read.SELECTION_SQL].append({'selection_id': 'd' * 32}),
        ]
        for change in changes:
            conn = ReadOnlyConnection()
            change(conn)
            with self.subTest(change=change):
                self.assertNotEqual(initial, ui_read.revision(conn))

    def test_legacy_tables_do_not_require_team_migration(self):
        conn = ReadOnlyConnection(team=False)
        self.assertRegex(ui_read.revision(conn)['revision'], r'^[a-f0-9]{64}$')
        self.assertNotIn(ui_read._report_sql('team_report_runs'), conn.calls)
        self.assertNotIn(ui_read._description_sql('team_descriptions'), conn.calls)

    def test_snapshot_reuses_one_verified_view_and_hint_is_before_validation(self):
        conn = ReadOnlyConnection()
        before = ui_read.revision(conn)
        jobs = [{'worker_job_id': 'a' * 32, 'reports': []}]
        groups = [{'selection_id': 'b' * 32, 'jobs': jobs}]
        def view(connection):
            self.assertIs(connection, conn)
            conn.rows[ui_read.QUEUE_SQL][0]['status'] = 'handoff_ready'
            return jobs
        with patch.object(flow_service, 'view', side_effect=view) as full, \
             patch.object(flow_service, 'selection_views', return_value=groups) as selections:
            result = ui_read.snapshot(conn)
        full.assert_called_once_with(conn)
        selections.assert_called_once_with(conn, verified_jobs=jobs)
        self.assertEqual(result, dict(before, jobs=jobs, selections=groups))
        self.assertNotEqual(result['revision'], ui_read.revision(conn)['revision'])

    def test_validation_failure_never_becomes_partial_completed_snapshot(self):
        for target in ('view', 'selection_views'):
            with self.subTest(target=target), \
                 patch.object(ui_read, 'revision', return_value={'revision': 'f' * 64}), \
                 patch.object(flow_service, 'view', return_value=[]), \
                 patch.object(flow_service, 'selection_views', return_value=[]), \
                 patch.object(flow_service, target, side_effect=WorkerError('saved_integrity_failed')):
                with self.assertRaisesRegex(WorkerError, 'saved_integrity_failed'):
                    ui_read.snapshot(object())

    def test_only_exact_get_routes_dispatch_without_writes_or_query_parameters(self):
        conn = object()
        @contextmanager
        def connect():
            yield conn
        with patch.object(web_api, 'connect', connect), \
             patch.object(ui_read, 'revision', return_value={'revision': 'f' * 64}) as revision, \
             patch.object(ui_read, 'snapshot', return_value={'jobs': [], 'selections': []}) as snapshot, \
             patch.object(flow_service, 'admit') as admit:
            for path, method in (('/ui-revision', revision), ('/ui-snapshot', snapshot)):
                handler = object.__new__(web_api.Handler)
                handler.path = path
                handler.send = Mock()
                handler.do_GET()
                method.assert_called_once_with(conn)
                for suffix in ('?x=1', '?', '#anything'):
                    handler.path = path + suffix
                    handler.send.reset_mock()
                    handler.do_GET()
                    handler.send.assert_called_once_with({'error': 'invalid_ui_read_query'}, 400)
                method.assert_called_once_with(conn)
            admit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
