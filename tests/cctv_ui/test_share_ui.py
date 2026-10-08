"""Public entry point and client boundaries; no real API/model requests."""
import base64
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from apps.cctv_ui import client
from tests.cctv_ui.test_app import Replies
from tests.cctv_ui.test_report_revisions import fixture, WORKER

ROOT = Path(__file__).resolve().parents[2]
SHARE = ROOT / 'apps/cctv_ui/share_app.py'
ANALYSIS = ROOT / 'apps/cctv_ui/analysis_app.py'
SHARE_ENV = {'CCTV_UI_READ_ONLY': '1', 'CCTV_API_URL': 'http://share-gateway:8081',
             'CCTV_UI_DATA_MODE': 'api'}
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7u0AAAAASUVORK5CYII=')


class ShareClientTests(unittest.TestCase):
    def test_mutations_private_routes_and_path_variants_never_reach_network(self):
        invalid = [('/selections', b'video'), ('/jobs', b''),
            ('/selections?selection=R2&purpose=full_report', b'video'),
            ('/jobs/' + WORKER + '/resume', b''), ('/connection', None),
            ('/worker', None), ('/report-pipeline', None), ('/mlflow', None),
            ('/jobs?submit=1', None), ('/jobs#other', None), ('//jobs', None),
            ('/jobs/' + WORKER + '/media/event_000/../../resume', None),
            ('/jobs/' + WORKER + '/media/event_000/%66rame', None)]
        with patch.dict(os.environ, SHARE_ENV), patch('urllib.request.urlopen') as network:
            for path, data in invalid:
                with self.subTest(path=path), self.assertRaises(client.APIError) as caught:
                    client.request(path, data)
                self.assertEqual(caught.exception.status, 403)
            network.assert_not_called()

    def test_allowed_reads_are_get_only_and_preserve_media(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'[]'
        with patch.dict(os.environ, SHARE_ENV), patch('urllib.request.urlopen', return_value=response) as network:
            self.assertEqual(client.request('/jobs'), [])
            self.assertEqual(client.request('/selections'), [])
            self.assertEqual(client.fetch_media(WORKER, None, 'original'), b'[]')
            self.assertEqual(client.fetch_media(WORKER, 'event_000', 'clip_preview'), b'[]')
        self.assertEqual(network.call_count, 4)
        for call in network.call_args_list:
            request = call.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            self.assertTrue(request.full_url.startswith('http://share-gateway:8081/'))


class ShareUITests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()
        self.copy = patch('apps.cctv_ui.control_report._COPY')
        self.copy.start()
        self.addCleanup(self.copy.stop)

    def test_share_reuses_saved_model_scene_report_and_ignores_forged_state(self):
        saved = fixture()
        saved['mlflow'] = {'model_id': 'x3d_s', 'model_version': 'operating-epoch7', 'status': 'recorded'}
        before = deepcopy(saved)
        calls = []
        def response(path, data=None, **kwargs):
            self.assertIsNone(data)
            calls.append(path)
            if path == '/jobs': return [saved]
            if path == '/selections': return []
            if path == '/ui-revision': return {'revision': '1'*64}
            if path == '/ui-snapshot': return {'revision': '1'*64, 'jobs': [saved], 'selections': []}
            if path.endswith('/frame'): return PNG
            if path.endswith('/source'): return b'0000ftyp-saved-video'
            raise AssertionError(path)
        with patch.dict(os.environ, SHARE_ENV), patch('apps.cctv_ui.client.request', side_effect=response), \
                patch('apps.cctv_ui.control_data.load_saved_snapshot', side_effect=AssertionError('local data forbidden')):
            app = AppTest.from_file(str(SHARE), default_timeout=15)
            app.session_state['cctv_navigation_enabled'] = True
            app.session_state['control_source'] = 'saved'
            app.session_state['CCTV_UI_READ_ONLY'] = False
            app.query_params['CCTV_UI_READ_ONLY'] = '0'
            app.query_params['page'] = 'analysis'
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.get('file_uploader'))
            self.assertFalse(any(b.key in {'control_new_analysis', 'control_demo_start', 'control_demo_reset'} for b in app.button))
            self.assertFalse(any(s.key == 'control_source' for s in app.selectbox))
            with self.assertRaises(ValueError):
                app.switch_page('analysis_app.py')
            app.button(key='control_open_' + WORKER).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get('video')), 0)  # Shared detail also defaults to annotation.
            self.assertTrue(any('x3d_s · operating-epoch7' in c.value for c in app.caption))
            displayed = '\n'.join(m.value for m in app.markdown)
            self.assertIn('검수된 저장 내용', displayed)
            self.assertTrue(any('저장된 차량 관찰' in t.value for t in app.text))
            app.button(key='control_refresh').click().run()
            self.assertFalse(app.exception)
        self.assertEqual(saved, before)
        self.assertTrue(all(p in {'/ui-snapshot', '/ui-revision', '/jobs/' + WORKER + '/source',
                                  '/jobs/' + WORKER + '/media/event_000/frame'} for p in calls))

    def test_direct_analysis_entry_stops_before_widgets_or_requests(self):
        with patch.dict(os.environ, SHARE_ENV), patch('apps.cctv_ui.client.request') as request:
            app = AppTest.from_file(str(ANALYSIS)).run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertFalse(app.get('file_uploader'))
        self.assertFalse(app.button)
        request.assert_not_called()

    def test_share_fails_closed_without_private_gateway_configuration(self):
        for overrides in ({'CCTV_UI_READ_ONLY': '0'}, {'CCTV_API_URL': 'http://be-api:8080'}):
            with self.subTest(overrides=overrides), patch.dict(os.environ, dict(SHARE_ENV, **overrides)), \
                    patch('apps.cctv_ui.client.request') as request:
                app = AppTest.from_file(str(SHARE)).run()
                self.assertFalse(app.exception)
                self.assertTrue(app.error)
                self.assertFalse(app.get('file_uploader'))
                request.assert_not_called()

    def test_operating_analysis_keeps_explicit_submission_when_flag_unset(self):
        replies = Replies(report_pipeline={'pipeline_id': 'team-rag-v1',
            'document_reference_date_supported': False,
            'analysis_models': [{'selection': 'B'}, {'selection': 'R2'}]})
        upload = io.BytesIO(b'0000ftyp-synthetic-test'); upload.name = 'test.mp4'
        with patch.dict(os.environ, {'CCTV_UI_READ_ONLY': ''}), \
                patch('apps.cctv_ui.client.request', side_effect=replies), \
                patch('streamlit.file_uploader', return_value=upload):
            app = AppTest.from_file(str(ANALYSIS)).run()
            self.assertFalse(app.exception)
            self.assertFalse(replies.posts)
            app.session_state['analysis_selection'] = 'R2'
            app.selectbox(key='analysis_purpose').select('full_report')
            next(b for b in app.button if b.label == '분석 시작').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(replies.posts), 1)
        self.assertEqual(replies.posts[0][0], '/selections?selection=R2&purpose=full_report')


if __name__ == '__main__':
    unittest.main()
