"""Finite demo UI: real storage transitions and honest source-level summaries."""
from copy import deepcopy
import io
import os
import time
from pathlib import Path
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from apps.cctv_ui.control_data import build_video_aggregates, load_saved_snapshot
from apps.cctv_ui.control_demo import acknowledge_live, observe_saved_descriptions
from tests.cctv_ui.test_app import Replies, job

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / 'apps/cctv_ui/control_room.py'
ANALYSIS = ROOT / 'apps/cctv_ui/analysis_app.py'
DEMO = ROOT / 'apps/cctv_ui/demo_app.py'
STATUS = dict(active=True, expires_at='2026-10-06T20:00:00+09:00', remaining_jobs=5,
              max_upload_bytes=2 * 1024**2, max_video_seconds=60, allowed_selections=['B', 'R2'])
PIPELINE = dict(pipeline_id='team-rag-v1', document_reference_date_supported=False,
                analysis_models=[{'selection': 'B'}, {'selection': 'R2'}])


def observed(identifier, digest, stamp, tags, version='B'):
    result = job(identifier, version=version, stage='completed', input_sha256=digest, submitted_at=stamp)
    result['descriptions'] = [{'event_id': 'event_000', 'analysis_id': identifier + '-analysis',
        'payload': {'status': 'completed', 'analysis_id': identifier + '-analysis',
            'event_id': 'event_000', 'input_sha256': digest, 'finished_at': stamp,
            'description': {'summary': '저장 관찰', 'incident_types': tags}}}]
    return result


def reported(item, report_id='stored-report'):
    result = deepcopy(item)
    description = result['descriptions'][0]
    result['reports'] = [{'report_id': report_id,
        'settings': {'analysis_id': description['analysis_id']},
        'state': {'status': 'report_stored', 'result': {
            'schema_version': 'cctv-team-report-v1', 'report_id': report_id,
            'analysis_id': description['analysis_id'], 'event_id': description['event_id'],
            'worker_job_id': result['worker_job_id']}}}]
    return result


class AggregateTests(unittest.TestCase):
    def test_same_sha_deduplicates_per_model_and_discloses_old_valid_observation(self):
        old = observed('a'*32, '1'*64, '2026-10-06T00:00:00Z', ['rear_end'])
        current = observed('b'*32, '1'*64, '2026-10-06T01:00:00Z', ['collision', 'side_collision'])
        failed = job('c'*32, version='B', stage='paused', input_sha256='1'*64,
                     submitted_at='2026-10-06T02:00:00Z')
        other_model = observed('d'*32, '1'*64, '2026-10-06T03:00:00Z', ['rollover'], 'R2')
        result = build_video_aggregates([old, current, failed, other_model], 'x3d_s')
        self.assertEqual(result['source_count'], 1)
        self.assertEqual(result['type_counts'], {'collision': 1, 'side_collision': 1})
        self.assertEqual(result['multiple_types'], 1)
        self.assertEqual(result['previous_observations'], 1)
        self.assertEqual(result['rows'][0]['description'], '미저장')
        self.assertEqual(result['rows'][0]['job_id'], failed['worker_job_id'])
        self.assertEqual(result['rows'][0]['representative_job_id'], current['worker_job_id'])

    def test_missing_hash_tied_recency_empty_and_no_candidate_are_not_normal_or_types(self):
        unclassified = observed('a'*32, '1'*64, '2026-10-06T00:00:00Z', [])
        no_candidate = job('b'*32, version='B', stage='no_candidates', input_sha256='2'*64)
        missing = job('c'*32, version='B', stage='paused')
        tied1 = observed('d'*32, '3'*64, '2026-10-06T00:00:00Z', ['collision'])
        tied2 = observed('e'*32, '3'*64, '2026-10-06T00:00:00Z', ['collision'])
        incomplete = job('f'*32, version='B', stage='provider_failed', input_sha256='4'*64)
        result = build_video_aggregates([unclassified, no_candidate, missing, tied1, tied2, incomplete], 'x3d_s')
        for key in ('unclassified', 'no_candidates', 'unknown_hash_jobs', 'recency_unknown', 'incomplete'):
            self.assertEqual(result[key], 1, key)
        self.assertEqual(result['type_counts'], {})

    def test_latest_description_revision_replaces_older_tags_in_same_job(self):
        item = observed('a'*32, '1'*64, '2026-10-06T00:00:00Z', ['rear_end'])
        newer = deepcopy(item['descriptions'][0])
        newer['analysis_id'] = newer['payload']['analysis_id'] = 'new-observation'
        newer['payload']['finished_at'] = '2026-10-06T01:00:00Z'
        newer['payload']['description']['incident_types'] = ['side_collision']
        item['descriptions'].append(newer)
        self.assertEqual(build_video_aggregates([item], 'x3d_s')['type_counts'], {'side_collision': 1})


class LiveNoticeTests(unittest.TestCase):
    def test_description_then_report_transition_notifies_once_after_storage(self):
        old = reported(observed('a'*32, '1'*64, '2026-10-06T00:00:00Z', ['collision']))
        fresh = observed('b'*32, '2'*64, '2026-10-06T01:00:00Z', ['side_collision'])
        baseline = observe_saved_descriptions(None, [old], 'api:test', now=100)
        self.assertEqual(baseline['notices'], [])
        explaining = observe_saved_descriptions(baseline, [old, fresh], 'api:test', now=102)
        self.assertEqual(explaining['notices'], [])
        fresh = reported(fresh)
        notified = observe_saved_descriptions(explaining, [old, fresh], 'api:test', now=105)
        self.assertEqual(len(notified['notices']), 1)
        self.assertEqual(notified['notices'][0]['job_id'], fresh['worker_job_id'])
        self.assertTrue(notified['notices'][0]['report_ready'])
        self.assertEqual(notified['notices'][0]['report_id'], 'stored-report')
        repeated = observe_saved_descriptions(notified, [old, fresh], 'api:test', now=110)
        self.assertEqual(repeated, notified)

    def test_watched_fast_completion_notifies_but_reused_stored_result_is_baseline(self):
        fresh = reported(observed('b'*32, '2'*64, '2026-10-06T01:00:00Z', ['side_collision']))
        reused = observe_saved_descriptions(None, [fresh], 'api:test', now=105)
        self.assertFalse(reused['notices'])
        watched = observe_saved_descriptions(None, [fresh], 'api:test',
                                              watch_jobs=[fresh['worker_job_id']], now=105)
        self.assertEqual(len(watched['notices']), 1)

    def test_failed_pending_or_crossed_result_never_announces_report_completion(self):
        fresh = reported(observed('b'*32, '2'*64, '2026-10-06T01:00:00Z', ['side_collision']))
        changes = [
            lambda row: row['descriptions'][0]['payload'].update(input_sha256='wrong-source'),
            lambda row: row['descriptions'][0]['payload'].update(status='failed'),
            lambda row: row['reports'][0]['state'].update(status='failed'),
            lambda row: row['reports'][0]['state'].update(status='pending'),
            lambda row: row['reports'][0]['state'].update(result={}),
            lambda row: row['reports'][0]['state']['result'].update(report_id='other-report'),
            lambda row: row['reports'][0]['state']['result'].update(analysis_id='other-analysis'),
            lambda row: row['reports'][0]['state']['result'].update(worker_job_id='other-job'),
            lambda row: row['reports'][0]['state']['result'].update(event_id='event_001'),
        ]
        for change in changes:
            row = deepcopy(fresh)
            change(row)
            with self.subTest(row=row):
                self.assertFalse(observe_saved_descriptions(None, [row], 'api:test',
                    watch_jobs=[row['worker_job_id']])['notices'])

    def test_new_report_id_is_distinct_and_read_removed_scope_markers_are_preserved(self):
        fresh = reported(observed('b'*32, '2'*64, '2026-10-06T01:00:00Z', ['side_collision']))
        state = observe_saved_descriptions(None, [fresh], 'api:test', watch_jobs=[fresh['worker_job_id']])
        first_key = state['notices'][0]['key']
        state = acknowledge_live(state, first_key)
        updated = reported(fresh, 'new-report')
        state = observe_saved_descriptions(state, [updated], 'api:test')
        self.assertEqual(len(state['notices']), 2)
        self.assertEqual(state['notices'][0]['status'], 'read')
        self.assertFalse(state['notices'][0]['available'])
        self.assertTrue(state['notices'][1]['available'])
        self.assertNotEqual(first_key, state['notices'][1]['key'])
        self.assertFalse(observe_saved_descriptions(state, [updated], 'api:other')['notices'])

    def test_previous_description_only_session_is_rebaselined(self):
        fresh = reported(observed('b'*32, '2'*64, '2026-10-06T01:00:00Z', ['side_collision']))
        old_state = {'scope': 'api:test', 'seen': [],
                     'notices': [{'report_ready': False, 'key': 'old-description'}]}
        self.assertFalse(observe_saved_descriptions(old_state, [fresh], 'api:test')['notices'])


class DemoAppTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()
        self.environment = patch.dict(os.environ, {'CCTV_DEMO_MODE': '1', 'CCTV_UI_READ_ONLY': '0',
                                                   'CCTV_API_URL': 'http://demo-gateway', 'CCTV_UI_DATA_MODE': 'api'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_demo_analysis_defaults_full_report_and_hides_resume(self):
        queued = job('a'*32, version='B', stage='paused')
        queued['flow'] = {'paused': True}
        replies = Replies([queued], report_pipeline=PIPELINE)
        def response(path, data=None, **kwargs):
            return STATUS if path == '/demo/status' else replies(path, data, **kwargs)
        upload = io.BytesIO(b'0000ftyp-local'); upload.name = 'demo.mp4'
        with patch('apps.cctv_ui.client.request', side_effect=response), \
                patch('streamlit.file_uploader', return_value=upload):
            app = AppTest.from_file(str(ANALYSIS), default_timeout=15).run()
            self.assertFalse(app.exception)
            self.assertFalse(any(button.key and button.key.startswith('resume_') for button in app.button))
            self.assertFalse(any(item.key == 'analysis_purpose' for item in app.selectbox))
            next(button for button in app.button if button.label == '분석 시작').click().run()
            self.assertFalse(app.exception)
        self.assertEqual(replies.posts[0][0], '/selections?selection=B&purpose=full_report')
        self.assertEqual(app.session_state['demo_watch_jobs'], ['a'*32])

    def test_demo_control_reuses_snapshot_and_refreshes_report_on_explicit_action(self):
        saved = deepcopy(load_saved_snapshot()['jobs'][0])
        stored_descriptions, stored_reports = saved['descriptions'], saved['reports']
        saved['descriptions'], saved['reports'] = [], []
        saved['stage'] = 'describing'
        replies = Replies([saved], report_pipeline=PIPELINE)
        with patch('apps.cctv_ui.client.request', side_effect=replies), \
                patch('apps.cctv_ui.client.fetch_media', return_value=None), \
                patch('apps.cctv_ui.control_report._COPY'):
            app = AppTest.from_file(str(CONTROL), default_timeout=15).run()
            self.assertFalse(app.exception)
            self.assertFalse(any(button.key == 'control_demo_start' for button in app.button))
            self.assertFalse(any(item.key == 'control_source' for item in app.selectbox))
            saved['descriptions'] = stored_descriptions
            app.button(key='control_refresh').click().run()
            self.assertFalse(app.exception)
            self.assertFalse(any('저장 완료' in row.value for row in app.success))
            self.assertFalse(any(button.key.startswith('live_open_') for button in app.button if button.key))
            saved['reports'] = stored_reports
            app.button(key='control_refresh').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any('보고서 저장 완료' in row.value for row in app.success))
            next(button for button in app.button if button.key.startswith('live_open_')).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['control_selected_job'], saved['worker_job_id'])
            notice = app.session_state['demo_live_notifications']['notices'][-1]
            self.assertEqual(app.session_state['control_report_revision_' + notice['job_id']
                                               + '_' + notice['analysis_id']], notice['report_id'])
        self.assertFalse(replies.posts)

    def test_closed_entrypoint_does_not_read_jobs_or_register_pages(self):
        with patch('apps.cctv_ui.client.request', return_value={**STATUS, 'active': False}) as request:
            app = AppTest.from_file(str(DEMO), default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertTrue(any('종료' in row.value for row in app.info))
        self.assertEqual([call.args[0] for call in request.call_args_list], ['/demo/status'])

    def test_single_entrypoint_navigates_control_analysis_and_mlflow(self):
        replies = Replies([job('a'*32, version='B')], report_pipeline=PIPELINE)
        def response(path, data=None, **kwargs):
            if path == '/demo/status':
                return STATUS
            if path.startswith('/mlflow/runs'):
                return {'runs': []}
            return replies(path, data, **kwargs)
        with patch('apps.cctv_ui.client.request', side_effect=response), \
                patch('apps.cctv_ui.client.fetch_media', return_value=None):
            app = AppTest.from_file(str(DEMO), default_timeout=15).run()
            self.assertFalse(app.exception)
            self.assertEqual([link.label for link in app.get('page_link')],
                             ['영상·보고 관제', '새 영상 분석', 'MLflow 실행 기록'])
            self.assertTrue(any('2026-10-06 20:00:00 KST' in row.value for row in app.caption))
            app.session_state['demo_last_refresh_at'] = time.monotonic() - 6
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get_by_key('demo_navigation').get('page_link')), 3)
            app.switch_page('analysis_app.py').run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get_by_key('demo_navigation').get('page_link')), 3)
            self.assertTrue(app.get('file_uploader'))
            app.session_state['analysis_selection'] = 'R2'
            app.session_state['demo_last_refresh_at'] = time.monotonic() - 6
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['analysis_selection'], 'R2')
            app.button(key='analysis_mlflow_' + 'a'*32).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['demo_mlflow_job'], 'a'*32)
            self.assertEqual(len(app.get_by_key('demo_navigation').get('page_link')), 3)
            self.assertTrue(any('조건에 맞는 실행 기록' in row.value for row in app.info))
        self.assertFalse(replies.posts)

    def test_live_panel_reuses_snapshot_and_notifies_only_for_verified_new_report(self):
        from apps.cctv_ui.ui_data import _revision
        fresh = observed('a'*32, '1'*64, '2026-10-06T00:00:00Z', ['collision'])
        replies = Replies([fresh], report_pipeline=PIPELINE)
        def response(path, data=None, **kwargs):
            return STATUS if path == '/demo/status' else replies(path, data, **kwargs)
        with patch('apps.cctv_ui.client.request', side_effect=response), \
                patch('apps.cctv_ui.client.fetch_media', return_value=None):
            app = AppTest.from_file(str(DEMO), default_timeout=15).run()
            self.assertFalse(app.exception)
            self.assertFalse(app.success)
            self.assertEqual(sum(path == '/ui-snapshot' for path, _ in replies.calls), 1)
            self.assertFalse(any(path in ('/jobs', '/selections', '/worker') for path, _ in replies.calls))
            app.run()
            self.assertEqual(sum(path == '/ui-snapshot' for path, _ in replies.calls), 1)
            replies.jobs[:] = [reported(fresh)]
            _revision.clear()
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any('보고서 저장 완료' in row.value for row in app.success))
            self.assertEqual(len(app.session_state['demo_live_notifications']['notices']), 1)
            app.run()
            self.assertEqual(len(app.session_state['demo_live_notifications']['notices']), 1)
            self.assertNotIn('control_navigation', app.session_state)
            next(button for button in app.button if button.key.startswith('live_open_')).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['control_navigation'], 1)
            self.assertEqual(app.session_state['control_selected_job'], fresh['worker_job_id'])
        self.assertFalse(replies.posts)

    def test_upload_size_rechecked_before_submission(self):
        replies = Replies(report_pipeline=PIPELINE)
        def response(path, data=None, **kwargs):
            return {**STATUS, 'max_upload_bytes': 4} if path == '/demo/status' else replies(path, data, **kwargs)
        upload = io.BytesIO(b'0000ftyp-larger'); upload.name = 'demo.mp4'
        with patch('apps.cctv_ui.client.request', side_effect=response), \
                patch('streamlit.file_uploader', return_value=upload):
            app = AppTest.from_file(str(ANALYSIS), default_timeout=15).run()
            next(button for button in app.button if button.label == '분석 시작').click().run()
        self.assertFalse(app.exception)
        self.assertFalse(replies.posts)
        self.assertTrue(any('입력 크기' in row.value for row in app.error))

    def test_only_unpaused_demo_gpu_poll_timeout_is_a_waiting_message(self):
        for demo_mode, stage, paused, error, waiting in (
                ('1', 'waiting', False, 'wait_limit_resume_only', True),
                ('1', 'waiting', True, 'wait_limit_resume_only', False),
                ('1', 'provider_failed', False, 'wait_limit_resume_only', False),
                ('1', 'waiting', False, 'provider_failed', False),
                ('0', 'waiting', False, 'wait_limit_resume_only', False)):
            with self.subTest(demo_mode=demo_mode, stage=stage, paused=paused, error=error):
                st.cache_data.clear()
                pending = job('a'*32, version='B', stage=stage)
                pending['flow'], pending['error_code'] = {'paused': paused}, error
                replies = Replies([pending], report_pipeline=PIPELINE)
                def response(path, data=None, **kwargs):
                    return STATUS if path == '/demo/status' else replies(path, data, **kwargs)
                with patch.dict(os.environ, {'CCTV_DEMO_MODE': demo_mode}), \
                        patch('apps.cctv_ui.client.request', side_effect=response):
                    app = AppTest.from_file(str(ANALYSIS), default_timeout=15).run()
                self.assertFalse(app.exception)
                self.assertEqual(any('GPU 분석·저장 결과 대기 중' in row.value for row in app.info), waiting)
                self.assertEqual(any(row.value == error for row in app.error), not waiting)
                self.assertFalse(replies.posts)


if __name__ == '__main__':
    unittest.main()
