"""Stored report revisions: strict source binding and read-only UI selection."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from apps.cctv_ui import control_data as data


WORKER = 'a' * 32
SOURCE = '11111111-1111-5111-8111-111111111111'
REVIEW = '22222222-2222-5222-8222-222222222222'
ANALYSIS = '33333333-3333-5333-8333-333333333333'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def fixture():
    scene = {'event_id': 'event_000', 'description': '저장된 차량 관찰',
             'evidence': [], 'people': {}, 'hazards': {}, 'location': {}}
    original = {
        'schema_version': data.TEAM_REPORT, 'report_id': SOURCE, 'analysis_id': ANALYSIS,
        'worker_job_id': WORKER, 'provider_job_id': 'b' * 32, 'event_id': 'event_000',
        'scene_sha256': 'c' * 64, 'report_markdown': '# 최초 보고\n\n저장 원문 내용\n',
        'report_json': {'event_id': 'event_000', 'references': [], 'agency_responses': [],
                        'verification_needed': ['원본 확인']},
    }
    settings = {'analysis_id': ANALYSIS, 'worker_job_id': WORKER, 'scene_sha256': 'c' * 64}
    review_result = dict(deepcopy(original), report_id=REVIEW,
                         report_markdown='# 검수 보고\n\n검수된 저장 내용\n')
    review_result['report_json']['verification_needed'] = ['검수 후 확인']
    review = {'report_id': REVIEW, 'settings': dict(settings, source_report_id=SOURCE),
        'state': {'status': 'report_stored', 'result': review_result,
                  'result_sha256': fingerprint(review_result)},
        'review': {'schema': 'cctv-team-report-review-v1', 'source_report_id': SOURCE,
                   'source_result_sha256': fingerprint(original), 'policy_sha256': 'd' * 64,
                   'excluded_count': 1, 'model_calls': 0}}
    return {'worker_job_id': WORKER, 'provider_job_id': 'b' * 32, 'version': 'B',
        'flow': {'status': 'completed'}, 'input_sha256': 'e' * 64,
        'submitted_at': '2026-10-05T00:00:00+09:00',
        'descriptions': [{'analysis_id': ANALYSIS, 'event_id': 'event_000',
                          'result_sha256': 'c' * 64,
                          'payload': {'schema_version': 'cctv-team-scene-v1', 'team_event': scene}}],
        'evidence_events': [{'event_id': 'event_000'}],
        'reports': [{'report_id': SOURCE, 'settings': settings,
                     'state': {'status': 'report_stored', 'result': original,
                               'result_sha256': fingerprint(original)},
                     'quality_reviews': [review]}]}


class ReportRevisionDataTests(unittest.TestCase):
    def test_default_review_and_explicit_original_preserve_source_and_counts(self):
        job = fixture()
        before = deepcopy(job)
        reviewed = data.normalize_case(job)
        original = data.normalize_case(job, report_id=SOURCE)
        self.assertEqual(reviewed['report']['report_id'], REVIEW)
        self.assertEqual(original['report']['report_id'], SOURCE)
        self.assertEqual(reviewed['verification_needed'], ['검수 후 확인'])
        self.assertEqual(original['verification_needed'], ['원본 확인'])
        self.assertEqual(reviewed['description'], original['description'])
        self.assertEqual(reviewed['report_review']['excluded_count'], 1)
        self.assertEqual(original['report_review'], {})
        self.assertEqual(data.build_statistics([job])['report_count'], 1)
        self.assertEqual(job, before)

    def test_missing_review_keeps_original_and_missing_explicit_revision_is_rejected(self):
        job = fixture()
        del job['reports'][0]['quality_reviews']
        self.assertEqual(data.normalize_case(job)['report']['report_id'], SOURCE)
        with self.assertRaisesRegex(ValueError, 'selected_report_revision_not_in_case'):
            data.normalize_case(job, report_id=REVIEW)

    def test_crossed_review_identity_never_replaces_original(self):
        changes = (
            ('review', 'source_report_id', REVIEW),
            ('review', 'source_result_sha256', '0' * 64),
            ('review', 'policy_sha256', 'invalid'),
            ('review', 'model_calls', 1),
            ('settings', 'analysis_id', 'different-scene'),
            ('settings', 'source_report_id', REVIEW),
            ('settings', 'worker_job_id', 'f' * 32),
            ('result', 'event_id', 'event_001'),
            ('result', 'worker_job_id', 'f' * 32),
            ('result', 'analysis_id', 'different-scene'),
            ('result', 'scene_sha256', 'f' * 64),
        )
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                job = fixture()
                review = job['reports'][0]['quality_reviews'][0]
                target = review['state']['result'] if section == 'result' else review[section]
                target[key] = value
                review['state']['result_sha256'] = fingerprint(review['state']['result'])
                case = data.normalize_case(job)
                self.assertEqual(case['report']['report_id'], SOURCE)
                self.assertTrue(case['report_review_errors'])
                with self.assertRaises(ValueError):
                    data.normalize_case(job, report_id=REVIEW)

    def test_changed_source_or_review_content_is_rejected_even_with_matching_ids(self):
        for source in (True, False):
            with self.subTest(source=source):
                job = fixture()
                report = job['reports'][0] if source else job['reports'][0]['quality_reviews'][0]
                report['state']['result']['report_markdown'] += '내용 변경'
                case = data.normalize_case(job)
                self.assertEqual(case['report']['report_id'], SOURCE)
                self.assertTrue(case['report_review_errors'])

    def test_other_event_description_cannot_select_review(self):
        job = fixture()
        job['descriptions'].append({'analysis_id': 'other', 'event_id': 'event_001',
                                   'payload': {'schema_version': 'cctv-team-scene-v1', 'team_event': {}}})
        case = data.normalize_case(job, event_id='event_001')
        self.assertIsNone(case['report'])
        with self.assertRaises(ValueError):
            data.normalize_case(job, event_id='event_001', report_id=REVIEW)

    def test_backend_held_review_surfaces_warning_without_hiding_original(self):
        job = fixture()
        job['reports'][0]['quality_reviews'] = []
        job['reports'][0]['quality_review_error'] = 'stored_report_review_identity_changed'
        case = data.normalize_case(job)
        self.assertEqual(case['report']['report_id'], SOURCE)
        self.assertEqual(case['report_review_errors'], ['stored_report_review_unavailable'])


class ReportRevisionUITests(unittest.TestCase):
    def test_native_revision_selector_changes_copy_download_and_preserves_scene(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        app_path = Path(__file__).resolve().parents[2] / 'apps/cctv_ui/app.py'
        job = fixture()
        original_bytes = json.dumps(job, ensure_ascii=False, sort_keys=True)
        snapshot = data.normalize_snapshot([job], metadata={'mode': 'saved',
            'captured_at': '2026-10-05T00:00:00+09:00', 'scope': '격리 저장 자료'})
        st.cache_data.clear()
        with patch.dict(os.environ, {'CCTV_UI_DATA_MODE': 'saved', 'CCTV_UI_VIEW': 'control'}), \
                patch('apps.cctv_ui.control_data.load_saved_snapshot', return_value=snapshot), \
                patch('apps.cctv_ui.client.request', side_effect=AssertionError('network forbidden')), \
                patch('apps.cctv_ui.control_report._COPY') as copy_widget, \
                patch('streamlit.download_button', wraps=st.download_button) as download:
            app = AppTest.from_file(str(app_path), default_timeout=15).run()
            app.button(key='control_open_' + WORKER).click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any('제외한 근거 1개' in c.value for c in app.caption))
            self.assertEqual(copy_widget.call_args.kwargs['data']['markdown'],
                             job['reports'][0]['quality_reviews'][0]['state']['result']['report_markdown'])
            revision_key = 'control_report_revision_' + WORKER + '_' + ANALYSIS
            self.assertEqual(app.session_state[revision_key], REVIEW)
            app.session_state[revision_key] = SOURCE
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(copy_widget.call_args.kwargs['data']['markdown'],
                             job['reports'][0]['state']['result']['report_markdown'])
            last_markdown = [call for call in download.call_args_list
                             if call.args[0] == 'Markdown 저장'][-1]
            self.assertEqual(last_markdown.args[1], job['reports'][0]['state']['result']['report_markdown'])
            self.assertEqual(last_markdown.kwargs['file_name'], 'report-' + SOURCE + '.md')
            self.assertTrue(any(t.value == '저장된 차량 관찰' for t in app.text))
            self.assertFalse(any('제외한 근거 1개' in c.value for c in app.caption))
        self.assertEqual(json.dumps(job, ensure_ascii=False, sort_keys=True), original_bytes)


if __name__ == '__main__':
    unittest.main()
