"""Separate approved windows preserve old requests/calls; no models or providers."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker import hour_session as s
from apps.be_worker.common import WorkerError
from tests.be_worker.test_hour_session import Connection, FakeDB, HourSessionTests


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'BE_SPOOL_PATH': self.tmp.name})
        env.start(); self.addCleanup(env.stop)
        self.db = FakeDB(); self.conn = Connection(self.db)
        self.new = {'schema': s.MARKER['schema'], 'name': 'p6-hour-session-20261004-01', 'max_jobs': 1}

    def select(self, value):
        s.marker_path().write_text(json.dumps(value))

    def old_attempt(self):
        s.initialize(self.conn, 1000.0, 4600.0)
        row = HourSessionTests.row(self, '1')
        from apps.be_worker import generation_settings as g, analysis_profiles as p
        self.settings = g.from_environment(analysis_profile=p.profile('B'))
        with self.conn.transaction():
            s.bind_job(self.conn, row, self.settings, '2026-10-02')
        s.check_events(self.conn, row, ['event_000'], self.settings, 'full_report', '2026-10-02')
        s.claim(self.conn, row, 'event_000', 'scene', 'a'*64, s.MODEL)
        before = deepcopy(self.db.rows)
        self.db.time = 5000.0
        return row, before

    def new_window(self):
        self.select(self.new)
        return s.initialize(self.conn, 5000.0, 8600.0)

    def test_new_row_preserves_old_claim_and_trial_byte_values(self):
        row, before = self.old_attempt()
        result = self.new_window()
        self.assertEqual({k:self.db.rows[k] for k in before}, before)
        self.assertEqual(result['max_jobs'], 1)
        self.assertEqual(result['bindings'], {})
        self.assertEqual(result['claims'], [])

    def test_same_request_cannot_move_to_another_window(self):
        row, before = self.old_attempt(); self.new_window()
        with self.assertRaisesRegex(WorkerError, 'old_job_preserved_view_only'):
            with self.conn.transaction(): s.bind_job(self.conn, row, self.settings, '2026-10-02')
        self.assertEqual(s.get(self.conn)['bindings'], {})
        self.assertEqual(self.db.rows[s.NAME], before[s.NAME])

    def test_new_input_can_bind_once_and_cap_one_blocks_second(self):
        _, before = self.old_attempt(); self.new_window()
        row = HourSessionTests.row(self, '2')
        with self.conn.transaction(): s.bind_job(self.conn, row, self.settings, '2026-10-02')
        first = s.get(self.conn)
        with self.conn.transaction(): s.bind_job(self.conn, row, self.settings, '2026-10-02')
        self.assertEqual(first, s.get(self.conn))
        with self.assertRaisesRegex(WorkerError, 'capacity_reached'):
            with self.conn.transaction(): s.bind_job(self.conn, HourSessionTests.row(self, '3'), self.settings, '2026-10-02')
        self.assertEqual(self.db.rows[s.NAME], before[s.NAME])

    def test_cap_edit_and_bool_in_db_fail_closed(self):
        self.db.time = 5000.0; self.new_window()
        self.select(dict(self.new, max_jobs=2))
        with self.assertRaisesRegex(WorkerError, 'approved_limit_changed'): s.get(self.conn)
        self.select(self.new)
        self.db.rows[self.new['name']]['max_jobs'] = True
        with self.assertRaisesRegex(WorkerError, 'approved_limit_changed'): s.get(self.conn)

    def test_expired_named_row_cannot_be_restarted_or_extended(self):
        self.db.time = 5000.0; self.new_window(); old = deepcopy(self.db.rows)
        self.db.time = 9000.0
        with self.assertRaisesRegex(WorkerError, 'existing_window_preserved'):
            s.initialize(self.conn, 9000.0, 12600.0)
        self.assertEqual(old, self.db.rows)

    def test_marker_switch_during_write_rolls_back_original_row(self):
        self.db.time = 5000.0; self.new_window(); before = deepcopy(self.db.rows)
        original = self.conn.execute
        def execute(query, args=None):
            result = original(query, args)
            if query.startswith('UPDATE '):
                self.select(dict(self.new, name='p6-hour-session-20261004-02'))
            return result
        with patch.object(self.conn, 'execute', side_effect=execute):
            with self.assertRaisesRegex(WorkerError, 'marker_changed_during_write'):
                s.stop(self.conn)
        self.assertEqual(self.db.rows, before)

    def test_marker_switch_during_get_is_not_different_row_read(self):
        self.db.time = 5000.0; self.new_window()
        original = self.conn.execute
        def execute(query, args=None):
            result = original(query, args)
            if query.startswith('SELECT payload'):
                self.select(dict(self.new, name='p6-hour-session-20261004-02'))
            return result
        with patch.object(self.conn, 'execute', side_effect=execute):
            with self.assertRaisesRegex(WorkerError, 'marker_changed_during_read'): s.get(self.conn)

    def test_marker_switch_after_insert_rolls_back_even_if_other_times_match(self):
        self.db.time = 5000.0
        other = dict(self.new, name='p6-hour-session-20261004-02')
        self.select(other); s.initialize(self.conn, 5000.0, 8600.0)
        before = deepcopy(self.db.rows); self.select(self.new)
        execute = self.conn.execute
        def switched(query, args=None):
            result = execute(query, args)
            if query.startswith('INSERT '): self.select(other)
            return result
        with patch.object(self.conn, 'execute', side_effect=switched):
            with self.assertRaisesRegex(WorkerError, 'marker_changed_during_initialize'):
                s.initialize(self.conn, 5000.0, 8600.0)
        self.assertEqual(self.db.rows, before)

    def test_old_payload_cannot_write_into_new_row(self):
        _, before = self.old_attempt(); self.new_window(); rows = deepcopy(self.db.rows)
        with self.assertRaisesRegex(WorkerError, 'state_invalid|identity_changed'):
            with self.conn.transaction(): s.write(self.conn, before[s.NAME])
        self.assertEqual(rows, self.db.rows)

    def test_bad_or_uncapped_new_marker_not_accepted(self):
        values = [dict(self.new, name='../escape'), dict(self.new, name='p6-hour-session-20260230-01'),
                  dict(self.new, name='p6-hour-session-20261004-00'), dict(self.new, max_jobs=True),
                  dict(self.new, max_jobs=101), dict(self.new, extra=True),
                  {'schema':s.MARKER['schema'], 'name':self.new['name']}]
        for value in values:
            with self.subTest(value=value):
                self.select(value)
                with self.assertRaises(WorkerError): s.configured()

    def test_status_read_selects_only_named_row_and_runs_no_claim(self):
        _, old = self.old_attempt(); result = self.new_window()
        self.assertEqual(s.current_name(), self.new['name'])
        self.assertEqual(s.get(self.conn), result)
        self.assertEqual({k:self.db.rows[k] for k in old}, old)
        self.assertEqual(result['claims'], [])


if __name__ == '__main__': unittest.main()
