"""Read-only UI behavior, using stored real results and isolated API doubles."""
from copy import deepcopy
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest
from apps.cctv_ui.control_data import load_saved_snapshot
from apps.cctv_ui import control_demo as demo
from tests.cctv_ui.test_control_panels import rendered_report
from tests.cctv_ui.test_app import Replies, job as analysis_job

APP = Path(__file__).resolve().parents[2] / 'apps/cctv_ui/app.py'
B = '9ef3054db80346c29225fa45eb7ac8ed'


class ControlRoomTests(unittest.TestCase):
    def setUp(self):
        self.copy_mount = patch('apps.cctv_ui.control_report._COPY')
        self.copy_mock = self.copy_mount.start()
        self.addCleanup(self.copy_mount.stop)
        st.cache_data.clear()
        self.environment = patch.dict(os.environ, {'CCTV_UI_DATA_MODE': 'saved', 'CCTV_UI_VIEW': 'control'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_default_saved_gallery_refresh_and_grid_never_contact_api(self):
        with patch('apps.cctv_ui.client.request', side_effect=AssertionError('network forbidden')) as request:
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            self.assertFalse(app.exception)
            self.assertEqual([m.value for m in app.metric], ['6', '5', '1', '0'])
            self.assertEqual(sum(x.value == '등록된 작업 없음' for x in app.caption), 3)
            app.session_state['control_grid'] = '4×4'
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(sum(x.value == '등록된 작업 없음' for x in app.caption), 10)
            app.button(key='control_refresh').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(request.call_count, 0)
            self.assertFalse(app.get('file_uploader'))

    def test_live_control_room_without_snapshot_navigates_to_models_and_back(self):
        saved = deepcopy(load_saved_snapshot())
        selected = next(j for j in saved['jobs'] if j['worker_job_id'] == B)
        replies = Replies([selected], report_pipeline={'pipeline_id': 'team-rag-v1',
            'document_reference_date_supported': False,
            'analysis_models': [{'selection': 'B'}, {'selection': 'R2'}]})
        def response(path, data=None, **kwargs):
            if path.endswith('/source'):
                replies.calls.append((path, data))
                return b'0000ftyp-test-original'
            return replies(path, data, **kwargs)
        with patch.dict(os.environ, {'CCTV_API_URL': 'http://be-api:8080', 'CCTV_UI_DATA_MODE': 'api'}), \
                patch('apps.cctv_ui.control_data.load_saved_snapshot', side_effect=AssertionError('snapshot unavailable')), \
                patch('apps.cctv_ui.client.request', side_effect=response):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            self.assertFalse(app.exception)
            app.button(key='control_open_' + B).click().run()
            before = rendered_report(app)
            self.assertEqual(before.count('<section '), 6)
            self.assertEqual(len(app.get('video')), 0)  # Annotation is the default view.
            app.button(key='control_new_analysis').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['analysis_selection'], 'B')
            # AppTest does not retain the browser page hash after switch_page.
            app.switch_page('analysis_app.py')
            app.session_state['analysis_selection'] = 'R2'
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['analysis_selection'], 'R2')
            app.button(key='analysis_control_home').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['control_selected_job'], B)
            self.assertEqual(app.session_state['control_source'], 'api')
            self.assertEqual(rendered_report(app), before)
        self.assertFalse(replies.posts)
        self.assertFalse(any(path == '/connection' for path, _ in replies.calls))

    def test_explicit_r2_submit_opens_that_job_in_live_control_room(self):
        pending = analysis_job('f' * 32, version='R2', stage='queued')
        pending['post_attempts'] = 0
        replies = Replies([pending], report_pipeline={'pipeline_id': 'team-rag-v1',
            'document_reference_date_supported': False,
            'analysis_models': [{'selection': 'B'}, {'selection': 'R2'}]})
        upload = io.BytesIO(b'0000ftyp-local-test'); upload.name = 'test.mp4'
        with patch('apps.cctv_ui.client.request', side_effect=replies), \
                patch('streamlit.file_uploader', return_value=upload), \
                patch('apps.cctv_ui.client.fetch_media', return_value=None):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            app.button(key='control_new_analysis').click().run()
            self.assertFalse(app.exception)
            self.assertFalse(replies.posts)
            app.switch_page('analysis_app.py')
            app.session_state['analysis_selection'] = 'R2'
            app.selectbox(key='analysis_purpose').select('full_report')
            next(b for b in app.button if b.label == '분석 시작').click().run()
            self.assertEqual(len(replies.posts), 1)
            self.assertEqual(replies.posts[0][0], '/selections?selection=R2&purpose=full_report')
            app.button(key='analysis_control_job_' + pending['worker_job_id']).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['control_source'], 'api')
            self.assertEqual(app.session_state['control_selected_job'], pending['worker_job_id'])
            self.assertEqual(rendered_report(app), '')
            self.assertEqual(len(replies.posts), 1)
        self.assertFalse(any(path == '/connection' for path, _ in replies.calls))

    def test_selected_real_b_defaults_annotation_and_preserves_two_videos_and_report(self):
        with patch('apps.cctv_ui.client.request', side_effect=AssertionError('network forbidden')) as request:
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            app.button(key='control_open_' + B).click().run()
            self.assertFalse(app.exception)
            self.assertEqual([t.label for t in app.tabs], ['박스 주석 이미지 · 보조 근거', '원본 영상', '사고 후보 클립'])
            self.assertEqual(len(app.get('video')), 0)
            markdown = rendered_report(app)
            for title in ['사고 개요', '영상 관찰 및 판단 불가 항목', '관제의 전달·요청 사항',
                          '현장 대응 참고', '확인 필요 사항', '관련 근거']:
                self.assertIn(title, markdown)
            self.assertFalse(any('먼저 확인할 사항' in x.value for x in app.subheader))
            app.session_state['control_video_' + B + '_event_000'] = '원본 영상'
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get('video')), 1)
            app.session_state['control_video_' + B + '_event_000'] = '사고 후보 클립'
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get('video')), 1)
            app.session_state['control_video_' + B + '_event_000'] = '박스 주석 이미지 · 보조 근거'
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get('video')), 0)
            self.assertFalse(any(x.key and x.key.startswith('control_download_') for x in app.get('download_button')))
            self.assertEqual(len(app.get('download_button')), 2)  # Saved report downloads remain.
            self.assertEqual(len(app.get_by_key('control_annotation_image').get('image')), 1)
            self.assertTrue(any('현재 작업의 주석 이미지 미연결' in x.value for x in app.caption))
            self.assertTrue(any('현재 분석의 이미지 근거가 아닙니다' in x.value for x in app.caption))
            self.assertFalse(any('주석 프레임 · 보조 이미지 근거' == x.label for x in app.expander))
            self.assertFalse(any('재생용 사본 미연결' in x.value for x in app.info))
            self.assertEqual(request.call_count, 0)
            app.button(key='control_back').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.metric), 4)

    def test_api_mode_only_reads_and_does_not_start_provider_or_submit(self):
        def reply(path, data=None, **kwargs):
            self.assertIsNone(data)
            if path == '/jobs':
                return [{'worker_job_id': 'a'*32, 'stage': 'no_candidates', 'submitted_at': None,
                         'evidence_events': [], 'descriptions': [], 'reports': []}]
            if path == '/selections':
                return []
            raise AssertionError(path)
        with patch('apps.cctv_ui.client.request', side_effect=reply) as request:
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            app.selectbox(key='control_source').select('api').run()
            self.assertFalse(app.exception)
            self.assertEqual([x.args[0] for x in request.call_args_list], ['/jobs', '/selections'])
            self.assertEqual(app.metric[1].value, '1')
            self.assertEqual(app.metric[3].value, '0')
            self.assertTrue(any('정상 판정 아님' in x.value for x in app.caption))

    def test_classification_filter_keeps_uncertain_results_separate_and_preserves_selection(self):
        jobs = [dict(worker_job_id=key * 32, stage=stage, version='B',
                     input_sha256='1' * 64, submitted_at=stamp, evidence_events=events,
                     descriptions=[], reports=[])
                for key, stage, stamp, events in [
                    ('a', 'completed', '2026-10-05T00:00:00Z', [{'event_id': 'event_000'}]),
                    ('b', 'no_candidates', '2026-10-05T01:00:00Z', []),
                    ('c', 'provider_failed', '2026-10-05T02:00:00Z', [])]]
        def reply(path, data=None, **kwargs):
            self.assertIsNone(data)
            return jobs if path == '/jobs' else [] if path == '/selections' else None
        with patch.dict(os.environ, {'CCTV_UI_DATA_MODE': 'api'}), \
                patch('apps.cctv_ui.client.request', side_effect=reply) as request, \
                patch('apps.cctv_ui.client.fetch_media', return_value=None):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[1].label, '요청 처리 완료')
            for category, key in [('no_candidates', 'b'), ('unjudged', 'c'), ('candidate', 'a')]:
                app.session_state['control_candidate_filter'] = category
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual([b.key for b in app.button if b.key.startswith('control_open_')],
                                 ['control_open_' + key * 32])
            self.assertTrue(any('설명: 미저장 · 보고: 미저장' == x.value for x in app.caption))
            app.button(key='control_open_' + 'a' * 32).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['control_selected_job'], 'a' * 32)
            app.button(key='control_back').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['control_candidate_filter'], 'candidate')
            self.assertEqual([b.key for b in app.button if b.key.startswith('control_open_')],
                             ['control_open_' + 'a' * 32])
            self.assertTrue(all(call.args[0] in ('/jobs', '/selections') for call in request.call_args_list))

    def test_current_annotation_is_shown_without_a_historical_substitution(self):
        identifier = 'c9e8788932f544a682af5e8990e7b589'
        with patch('apps.cctv_ui.client.request', side_effect=AssertionError('network forbidden')) as request:
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            app.button(key='control_open_' + identifier).click().run()
            app.session_state['control_video_' + identifier + '_event_000'] = '박스 주석 이미지 · 보조 근거'
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get_by_key('control_annotation_image').get('image')), 1)
        self.assertEqual(len(app.get('video')), 0)
        self.assertFalse(any('현재 작업의 주석 이미지 미연결' in x.value for x in app.caption))
        self.assertFalse(any('현재 분석의 이미지 근거가 아닙니다' in x.value for x in app.caption))
        self.assertEqual(request.call_count, 0)

    def test_gallery_distinguishes_missing_thumbnail_from_integrity_failure(self):
        with patch('apps.cctv_ui.control_data.media_reference', return_value={'status': 'integrity_failed'}):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertEqual(sum('조회·검증 실패' in x.value for x in app.warning), 6)
        self.assertFalse(any('영상 이미지 미연결' in x.value for x in app.markdown))
        with patch('apps.cctv_ui.control_data.media_reference', return_value={'status': 'api_read_required'}):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertFalse(app.warning)
        self.assertEqual(sum('영상 이미지 미연결' in x.value for x in app.markdown), 6)

    def test_api_annotation_only_fetches_current_frame_without_local_substitution(self):
        snapshot = load_saved_snapshot()
        job = next(j for j in snapshot['jobs'] if j['worker_job_id'] == B)
        def reply(path, data=None, **kwargs):
            self.assertIsNone(data)
            return [job] if path == '/jobs' else [] if path == '/selections' else None
        with patch('apps.cctv_ui.client.request', side_effect=reply), \
                patch('apps.cctv_ui.client.fetch_media', side_effect=RuntimeError('frame unavailable')) as media, \
                patch('apps.cctv_ui.control_data.historical_frame_reference', side_effect=AssertionError('local substitution forbidden')) as history:
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            app.session_state['control_selected_job'] = B
            app.session_state['control_video_' + B + '_event_000'] = '박스 주석 이미지 · 보조 근거'
            app.selectbox(key='control_source').select('api').run()
        self.assertFalse(app.exception)
        self.assertTrue(all(c.args == (B, 'event_000', 'frame') for c in media.call_args_list))
        self.assertGreaterEqual(media.call_count, 1)  # Monitor and active annotation both read the same current frame.
        self.assertEqual(history.call_count, 0)
        self.assertTrue(any('주석 이미지 조회 확인 필요' in x.value for x in app.warning))
        self.assertEqual(len(app.get('video')), 0)

    def test_api_failure_stays_failure_and_does_not_fallback_to_saved_count(self):
        with patch('apps.cctv_ui.client.request', side_effect=RuntimeError('offline')):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            app.selectbox(key='control_source').select('api').run()
            self.assertFalse(app.exception)
            self.assertTrue(any('조회 실패' in x.value for x in app.error))
            self.assertFalse(app.metric)

    def test_empty_records_and_missing_vlm_are_not_normal_predictions(self):
        with patch('apps.cctv_ui.control_data.load_saved_snapshot', return_value={
                'jobs': [], 'metadata': {}, 'stats': {'completed_count': 0, 'attention_count': 0,
                 'states': {}, 'report_count': 0, 'stored_report_count': 0, 'candidate_event_count': 0}}):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertEqual([m.value for m in app.metric], ['0', '0', '0', '0'])
        self.assertFalse(any('사고 없음' in x.value for x in app.caption))

    def test_same_scene_legacy_and_team_saved_reports_remain_selectable(self):
        identifier = 'c2f5dc5084f744f0addaff03ad877e04'
        with patch('apps.cctv_ui.client.request', side_effect=AssertionError('network forbidden')):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            app.button(key='control_open_' + identifier).click().run()
            self.assertFalse(app.exception)
            picker = app.selectbox(key='control_analysis_' + identifier + '_event_000')
            self.assertEqual(len(picker.options), 2)
            self.assertEqual(rendered_report(app).count('<section '), 6)
            other = next(i for i in picker.options if '기존 저장 보고' in i)
            picker.select(other).run()
            self.assertFalse(app.exception)
            self.assertTrue(any('기존 경로의 저장 보고' in x.value for x in app.caption))

    def test_monitor_switches_all_content_and_preserves_legacy_choice_and_grid(self):
        other = 'c2f5dc5084f744f0addaff03ad877e04'
        failed = '36545d0d2927430d9231c0c880ba4ef8'
        with patch('apps.cctv_ui.client.request', side_effect=AssertionError('network forbidden')):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            app.session_state['control_grid'] = '4×4'
            app.button(key='control_open_' + B).click().run()
            self.assertEqual(len([b for b in app.button if b.key.startswith('control_monitor_open_')]), 6)
            app.button(key='control_monitor_open_' + other).click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any('33c0252e-ce27-5bd1-8b2d-224343021ccc' in x.value for x in app.text))
            self.assertFalse(any('50bb10ef-4e80-503f-80f5-4f9771e21c04' in x.value for x in app.text))
            picker = app.selectbox(key='control_analysis_' + other + '_event_000')
            picker.select(next(v for v in picker.options if '기존 저장 보고' in v)).run()
            app.button(key='control_monitor_open_' + B).click().run()
            app.button(key='control_monitor_open_' + other).click().run()
            self.assertTrue(any('기존 경로의 저장 보고' in x.value for x in app.caption))
            app.button(key='control_monitor_open_' + failed).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(rendered_report(app), '')
            self.assertTrue(any('저장 보고 없음' in x.value for x in app.caption))
            app.button(key='control_back').click().run()
            self.assertEqual(app.session_state['control_grid'], '4×4')

    def test_alert_flow_keeps_same_scene_report_across_navigation_and_replay(self):
        target = next(t for t in demo.candidates(load_saved_snapshot()) if t['job_id'].startswith('c2f5'))
        with patch('apps.cctv_ui.client.request', side_effect=AssertionError('network forbidden')) as request, \
                patch('apps.cctv_ui.control_demo._now', return_value=100) as now:
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            before = [m.value for m in app.metric]
            app.selectbox(key='control_demo_target').select(demo.target_key(target)).run()
            app.button(key='control_demo_start').click().run()
            self.assertTrue(app.button(key='control_demo_start').disabled)
            self.assertEqual(app.session_state['control_demo']['status'], 'pending')
            app.button(key='control_open_' + B).click().run()
            now.return_value = 102.01
            app.run()
            self.assertEqual(app.session_state['control_demo']['status'], 'unread')
            app.button(key='control_demo_open').click().run()
            self.assertEqual(app.session_state['control_selected_job'], target['job_id'])
            self.assertTrue(any(target['report_id'] in x.value for x in app.text))
            app.button(key='control_demo_read').click().run()
            self.assertEqual(app.session_state['control_demo']['status'], 'read')
            self.assertTrue(app.button(key='control_demo_read').disabled)
            app.button(key='control_back').click().run()
            self.assertEqual([m.value for m in app.metric], before)
            app.button(key='control_demo_start').click().run()
            self.assertEqual(app.session_state['control_demo']['status'], 'pending')
            app.button(key='control_demo_reset').click().run()
            self.assertIsNone(app.session_state['control_demo'])
            self.assertEqual([m.value for m in app.metric], before)
            self.assertFalse(app.exception)
            self.assertEqual(request.call_count, 0)

    def test_alert_cannot_open_in_a_different_data_source(self):
        target = demo.candidates(load_saved_snapshot())[0]
        with patch('apps.cctv_ui.client.request', return_value=[]):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
            app.session_state['control_demo'] = demo.advance(demo.schedule(None, target, 'saved', now=100), now=102)
            app.selectbox(key='control_source').select('api').run()
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key='control_demo_open').disabled)
            self.assertTrue(any('현재 조회 자료에 같은 사건·보고가 없습니다' in x.value for x in app.caption))
            app.selectbox(key='control_source').select('saved').run()
            self.assertFalse(app.button(key='control_demo_open').disabled)
            self.assertEqual(app.session_state['control_demo']['status'], 'unread')


if __name__ == '__main__':
    unittest.main()
