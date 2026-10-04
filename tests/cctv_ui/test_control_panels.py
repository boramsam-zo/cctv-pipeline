"""Saved observation/report integrity and headless detail-panel checks."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from html import unescape

from streamlit.testing.v1 import AppTest

from apps.cctv_ui.control_panels import report_result
from apps.cctv_ui.report_view import display_markdown


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'artifacts/be-worker/pod-restore-20261003-01/new-B-api-state-agent-result-01.json'


def saved_case(team=True):
    jobs = json.loads(json.loads(SOURCE.read_text())['stdout'])['jobs']
    job = next(row for row in jobs if row['worker_job_id'].startswith('9ef')) if team else jobs[0]
    description = job['descriptions'][0]
    return {'job': job, 'description': description,
            'scene': description['payload'].get('team_event') or description['payload']['description'],
            'report': job['reports'][0]}


def panels_script(team=True):
    return f'''
import json
from pathlib import Path
from apps.cctv_ui.control_panels import render_observations, render_checks, render_agencies, render_report
jobs = json.loads(json.loads(Path({str(SOURCE)!r}).read_text())['stdout'])['jobs']
job = next(row for row in jobs if row['worker_job_id'].startswith('9ef')) if {team!r} else jobs[0]
description = job['descriptions'][0]
case = {{'job': job, 'description': description, 'report': job['reports'][0]}}
render_observations(case)
render_checks(case)
render_agencies(case)
render_report(case)
'''


def compact_panels_script():
    script = panels_script()
    for name in ('render_observations', 'render_checks', 'render_agencies'):
        script = script.replace(name + '(case)', name + '(case, compact=True)')
    return script


def rendered_report(app):
    return unescape('\n'.join(x.proto.body for x in app.get('html') if x.proto.body.startswith('<div class="cc-stored-report"')))


class ControlPanelTests(unittest.TestCase):
    def setUp(self):
        self.copy_mount = patch('apps.cctv_ui.control_report._COPY')
        self.copy_mock = self.copy_mount.start()
        self.addCleanup(self.copy_mount.stop)

    def test_real_saved_report_returns_same_object_without_editing(self):
        case = saved_case()
        before = deepcopy(case)
        result = report_result(case)
        self.assertIs(result, case['report']['state']['result'])
        self.assertEqual(case, before)

    def test_crossed_team_report_links_are_rejected(self):
        mutations = (
            lambda c: c['report']['state']['result'].__setitem__('report_id', 'other'),
            lambda c: c['report']['settings'].__setitem__('analysis_id', 'other'),
            lambda c: c['job'].__setitem__('worker_job_id', 'other'),
            lambda c: c['job'].__setitem__('provider_job_id', 'other'),
            lambda c: c['scene'].__setitem__('event_id', 'other'),
            lambda c: c['description'].__setitem__('result_sha256', 'other'),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                case = deepcopy(saved_case())
                mutation(case)
                with self.assertRaisesRegex(ValueError, 'stored_report_identity_mismatch'):
                    report_result(case)

    def test_missing_required_team_identity_is_rejected(self):
        case = saved_case()
        del case['report']['settings']['analysis_id']
        with self.assertRaisesRegex(ValueError, 'stored_report_identity_mismatch'):
            report_result(case)

    def test_real_six_sections_checks_and_document_sources_are_visible(self):
        app = AppTest.from_string(panels_script()).run(timeout=10)
        self.assertFalse(app.exception)
        self.assertFalse(app.expander)
        case = saved_case()
        result = report_result(case)
        html = rendered_report(app)
        self.assertEqual(html.count('<section '), 6)
        self.assertIn(result['report_id'], html)
        self.assertEqual(self.copy_mock.call_args.kwargs['data']['markdown'], result['report_markdown'])
        text = '\n'.join(row.value for row in app.text)
        for check in result['report_json']['verification_needed']:
            self.assertIn(check, text)
        for observation in case['scene']['evidence']:
            self.assertIn(observation['observation'], text)
        for reference in result['report_json']['references']:
            self.assertIn(reference['reference_id'], html)
        self.assertIn('사고유형 정의 참고', html)
        self.assertEqual(html.count('rel="noopener noreferrer"'), 3)
        self.assertEqual(len(app.warning), 2)

    def test_mismatched_report_does_not_render_agencies_sections_or_sources(self):
        script = panels_script().replace('render_observations(case)',
            "case['report']['state']['result']['report_id'] = 'other'\nrender_observations(case)")
        app = AppTest.from_string(script).run(timeout=10)
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertFalse(app.get('link_button'))
        self.assertFalse(any('## 사고 개요' in row.value for row in app.markdown))

    def test_legacy_scene_and_report_keep_saved_text_and_date(self):
        app = AppTest.from_string(panels_script(team=False)).run(timeout=10)
        self.assertFalse(app.exception)
        self.assertFalse(app.expander)
        case = saved_case(team=False)
        result = report_result(case)
        text = '\n'.join(row.value for row in app.text)
        self.assertIn(case['scene']['summary'], text)
        self.assertIn(result['reference_date'], text)
        for finding in result['draft']['document_findings']:
            self.assertIn(finding['quote'], text)

    def test_compact_panels_keep_summary_every_check_and_agency_reason_verbatim(self):
        app = AppTest.from_string(compact_panels_script()).run(timeout=10)
        self.assertFalse(app.exception)
        case = saved_case()
        result = report_result(case)
        text = [row.value for row in app.text]
        self.assertIn(case['scene']['description'], text)
        for index, check in enumerate(result['report_json']['verification_needed'], 1):
            self.assertIn(f'{index:02d}  {check}', text)
        for agency in result['report_json']['agency_responses']:
            self.assertIn(agency['agency'] + ' · ' + agency['notification'], text)
            self.assertIn(agency['reason'], text)
            self.assertFalse(any(agency['control_handoff']['request'] in value for value in text))
        self.assertIn('문서 근거 [R1] [R2]', text)
        self.assertEqual(text.count('현장 대응 근거 미확인 · 구체 조치 생성 없음'), 2)
        tags = [row.value for row in app.markdown if '-badge[' in row.value]
        self.assertEqual(tags, [':orange-badge[파편 · 관찰됨]', ':orange-badge[차로 점유 · 관찰됨]',
                                ':gray-badge[도로 위 사람 · 판단 불가]', ':gray-badge[차체 끼임 · 판단 불가]'])
        self.assertEqual(rendered_report(app).count('<section '), 6)
        self.assertEqual(self.copy_mock.call_args.kwargs['data']['markdown'], result['report_markdown'])
        self.assertFalse(app.expander)

    def test_compact_and_full_observation_panels_can_coexist_with_all_saved_evidence(self):
        script = compact_panels_script() + '\nrender_observations(case)\n'
        app = AppTest.from_string(script).run(timeout=10)
        self.assertFalse(app.exception)
        text = '\n'.join(row.value for row in app.text)
        for evidence in saved_case()['scene']['evidence']:
            self.assertIn(evidence['observation'], text)
        self.assertIn('가시 구간에서 관찰되지 않음', text)
        keys = [row.key for row in app.get('flex_container') if row.key]
        for key in ('control_observations_compact', 'control_observation_tags',
                    'control_checks_compact', 'control_check_6', 'control_agencies_compact',
                    'control_agency_3'):
            self.assertIn(key, keys)
        self.assertEqual(len(keys), len(set(keys)))

    def test_compact_panels_keep_missing_and_mismatched_data_explicit(self):
        script = compact_panels_script().replace('render_observations(case, compact=True)',
            "case['report']['state']['result']['report_id'] = 'other'\n"
            'render_observations(case, compact=True)')
        app = AppTest.from_string(script).run(timeout=10)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.error), 3)
        self.assertFalse(any('경찰 ·' in row.value for row in app.text))
        missing = AppTest.from_string('''
from apps.cctv_ui.control_panels import render_observations, render_checks, render_agencies
render_observations({}, compact=True)
render_checks({}, compact=True)
render_agencies({}, compact=True)
''').run(timeout=10)
        self.assertFalse(missing.exception)
        captions = '\n'.join(row.value for row in missing.caption)
        self.assertIn('미실행·실패를 정상 판정으로 바꾸지 않습니다', captions)
        self.assertIn('확인 완료로 간주하지 않습니다', captions)
        self.assertIn('구체 조치를 생성하지 않습니다', captions)


if __name__ == '__main__':
    unittest.main()
