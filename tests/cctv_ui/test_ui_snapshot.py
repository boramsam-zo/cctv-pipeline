"""Snapshot reuse and public projection; no API, DB, model, or socket calls."""
from copy import deepcopy
import os
import unittest
from unittest.mock import patch

import streamlit as st

from apps.cctv_ui import ui_data
from apps.cctv_ui.demo_gateway import projection, route
from apps.cctv_ui.share_gateway import allowed_path, project_response
from apps.cctv_ui.control_demo import observe_saved_descriptions
from tests.cctv_ui.test_demo_flow import observed, reported


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()
        self.env = patch.dict(os.environ, {'CCTV_API_URL': 'http://snapshot-test'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_timer_and_page_share_one_verified_snapshot_until_revision_changes(self):
        state = {'revision': '1'*64, 'jobs': [], 'selections': []}
        def response(path):
            return {'revision': state['revision']} if path == '/ui-revision' else deepcopy(state)
        with patch('apps.cctv_ui.client.request', side_effect=response) as request:
            first = ui_data.load_snapshot()
            self.assertEqual(ui_data.load_snapshot(), first)
            self.assertEqual([c.args[0] for c in request.call_args_list], ['/ui-revision', '/ui-snapshot'])
            ui_data._revision.clear()
            self.assertEqual(ui_data.load_snapshot(), first)
            self.assertEqual(sum(c.args[0] == '/ui-snapshot' for c in request.call_args_list), 1)
            state['revision'] = '2'*64
            ui_data._revision.clear()
            self.assertEqual(ui_data.load_snapshot()['revision'], '2'*64)
            self.assertEqual(sum(c.args[0] == '/ui-snapshot' for c in request.call_args_list), 2)

    def test_changed_revision_or_failed_snapshot_never_invents_report_notice(self):
        pending = observed('a'*32, '1'*64, '2026-10-06T00:00:00Z', ['collision'])
        notices = observe_saved_descriptions(None, [pending], 'test')
        with patch('apps.cctv_ui.client.request', side_effect=[{'revision': '2'*64}, RuntimeError('offline')]):
            with self.assertRaises(RuntimeError):
                ui_data.load_snapshot()
        self.assertFalse(notices['notices'])
        verified = reported(pending)
        notices = observe_saved_descriptions(notices, [verified], 'test')
        self.assertEqual(len(notices['notices']), 1)
        self.assertEqual(observe_saved_descriptions(notices, [verified], 'test'), notices)

    def test_snapshot_routes_remain_get_only_without_query_aliases(self):
        for path in ('/ui-revision', '/ui-snapshot'):
            self.assertTrue(allowed_path(path))
            self.assertEqual(route(path), path)
            for method in ('POST', 'PUT', 'DELETE'):
                self.assertIsNone(route(path, method))
            for invalid in (path + '?x=1', path + '?', path + '/', '/%75' + path[2:]):
                self.assertFalse(allowed_path(invalid))
                self.assertIsNone(route(invalid))

    def test_snapshot_uses_existing_projection_for_both_job_copies(self):
        job = {'worker_job_id': 'a'*32, 'config': {'token': 'secret'},
               'evidence_events': [], 'descriptions': [], 'reports': [],
               'mlflow': {'tracking_uri': 'http://private', 'run_id': 'b'*32}}
        value = {'revision': '1'*64, 'jobs': [job], 'selections': [{'selection_id': 'c'*32, 'jobs': [job]}],
                 'private': 'secret'}
        for project in (projection, project_response):
            public = project('/ui-snapshot', value)
            self.assertEqual(set(public), {'revision', 'jobs', 'selections'})
            for item in (public['jobs'][0], public['selections'][0]['jobs'][0]):
                self.assertNotIn('config', item)
                self.assertNotIn('tracking_uri', item['mlflow'])
            self.assertEqual(project('/ui-revision', value), {'revision': '1'*64})


if __name__ == '__main__':
    unittest.main()
