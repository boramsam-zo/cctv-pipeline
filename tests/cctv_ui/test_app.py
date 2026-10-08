"""Headless Streamlit interactions; all API/media replies are local test doubles."""
from copy import deepcopy
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from apps.cctv_ui.client import APIError
from streamlit.testing.v1 import AppTest
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT/'apps/cctv_ui/analysis_app.py'


def job(identifier='a'*32, version=None, stage='no_candidates', **extra):
    return dict(worker_job_id=identifier, stage=stage, version=version,
        analysis_profile={'version': version} if version else None, flow={}, error_code=None,
        reports=[], descriptions=[], scene_events={}, post_attempts=1, scene_calls=0,
        evidence_events=[], **extra)


class Replies:
    def __init__(self, jobs=None, groups=None, connected=True, report_pipeline=None):
        self.jobs, self.groups = jobs or [], groups or []
        self.connected, self.calls = connected, []
        self.report_pipeline = report_pipeline
    def __call__(self, path, data=None, **kwargs):
        self.calls.append((path, data))
        if data is not None:
            if path.startswith('/selections?'):
                query=parse_qs(urlsplit(path).query)
                group={'selection_id':'d'*32,'already_present':bool(self.groups),
                       'selection':query['selection'][0],'purpose':query['purpose'][0],'jobs':self.jobs}
                self.groups=[group]
                return group
            if path.endswith('/resume'):return {'status':'resume_requested'}
            raise AssertionError(path)
        if path=='/connection':return {'connected':self.connected,'error_code':'network_error'}
        if path=='/report-pipeline':
            if self.report_pipeline is None:raise APIError('not_found',404)
            return self.report_pipeline
        if path=='/worker':return {'age_seconds':1}
        if path=='/jobs':return self.jobs
        if path=='/selections':return self.groups
        if path in ('/ui-revision', '/ui-snapshot'):
            revision = hashlib.sha256(json.dumps([self.jobs, self.groups], sort_keys=True).encode()).hexdigest()
            if path == '/ui-revision': return {'revision': revision}
            return {'revision': revision, 'jobs': self.jobs, 'selections': self.groups}
        if path.startswith('/selections/'):
            return next(row for row in self.groups if row['selection_id']==path.split('/')[-1])
        if '/media/' in path:
            if path.endswith('/frame'):
                return (ROOT/'artifacts/runpod/image-p4-20261001-01/image-validation-20261001-01/input_001-B/output/raw/event_000/event_000_objects.jpg').read_bytes()
            return b'0000ftyp-synthetic-video'
        if path.startswith('/jobs/'):
            return next(row for row in self.jobs if row['worker_job_id']==path.split('/')[-1])
        raise AssertionError(path)
    @property
    def posts(self):
        return [(path,data) for path,data in self.calls if data is not None]


class AppTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()

    def test_model_selection_keeps_full_report_request_and_does_not_auto_submit(self):
        replies=Replies(report_pipeline={'pipeline_id':'team-rag-v1',
            'document_reference_date_supported':False,
            'analysis_models':[{'selection':'B'},{'selection':'R2'}]})
        upload=io.BytesIO(b'0000ftyp-synthetic-model-selection');upload.name='test.mp4'
        with patch('apps.cctv_ui.client.request',side_effect=replies), \
                patch('streamlit.file_uploader',return_value=upload):
            app=AppTest.from_file(str(APP)).run()
            self.assertFalse(app.exception);self.assertFalse(replies.posts)
            self.assertEqual(app.session_state['analysis_selection'],'B')
            app.session_state['analysis_selection']='R2'
            app.selectbox(key='analysis_purpose').select('full_report')
            app.button[0].click().run()
        self.assertFalse(app.exception);self.assertEqual(len(replies.posts),1)
        query=parse_qs(urlsplit(replies.posts[0][0]).query)
        self.assertEqual(query,{'selection':['R2'],'purpose':['full_report']})

    def test_refresh_legacy_job_only_reads_and_never_labels_it_a(self):
        replies=Replies(jobs=[job()])
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run();app.run()
        self.assertFalse(app.exception)
        self.assertFalse(replies.posts)
        self.assertTrue(any('정상 판정 아님' in row.value for row in app.subheader))
        self.assertTrue(any('버전 미기록' in row.value for row in app.subheader))

    def test_offline_runpod_is_visible_without_submit(self):
        replies=Replies(connected=False)
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        self.assertFalse(replies.posts)
        self.assertTrue(any('Runpod 연결 대기' in row.value for row in app.warning))

    def test_empty_submit_does_not_post(self):
        replies=Replies()
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertFalse(replies.posts)
        self.assertTrue(any('영상을 선택' in row.value for row in app.warning))

    def test_input_inspection_hashes_full_upload_without_post_or_stale_receipt(self):
        replies=Replies()
        original=(ROOT/'artifacts/runpod/image-p4-20261001-01/cctv-image-validation-20261001-01/input_001.mp4').read_bytes()
        upload=io.BytesIO(original);upload.name='input_001.mp4'
        upload.seek(400)  # The uploader cursor must not truncate the diagnostic.
        with patch('apps.cctv_ui.client.request',side_effect=replies), \
                patch('streamlit.file_uploader',return_value=upload):
            app=AppTest.from_file(str(APP)).run()
            app.session_state['analysis_selection']='A+B'
            app.selectbox(key='analysis_purpose').select('full_report')
            app.button(key='inspect_input').click().run()
            self.assertFalse(app.exception)
            receipt=json.loads(app.json[0].value)
            self.assertEqual(receipt['action'],'inspect_only')
            self.assertEqual(receipt['ui_received_bytes'],5855313)
            self.assertEqual(receipt['ui_received_sha256'],
                '6c8233026b1393dd1a7287fb73127fa8799b11337985b444b31f72091295e459')
            self.assertEqual((receipt['selection'],receipt['purpose']),('A+B','full_report'))
            app.run()
            self.assertFalse(app.json)  # No previous input receipt presented as current.
        self.assertFalse(replies.posts)

    def test_same_filename_different_bytes_are_visible_in_rejection_receipt(self):
        replies=Replies()
        payload=b'0000ftyp-other-video'
        upload=io.BytesIO(payload);upload.name='input_001.mp4'
        def reject(path,data=None,**kwargs):
            if data is not None:
                replies.calls.append((path,data))
                raise RuntimeError('trial_input_not_approved')
            return replies(path,data,**kwargs)
        with patch('apps.cctv_ui.client.request',side_effect=reject), \
                patch('streamlit.file_uploader',return_value=upload):
            app=AppTest.from_file(str(APP)).run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            receipt=json.loads(app.json[0].value)
            self.assertEqual(receipt['file_name'],'input_001.mp4')
            self.assertEqual(receipt['ui_received_bytes'],len(payload))
            self.assertEqual(receipt['ui_received_sha256'],hashlib.sha256(payload).hexdigest())
            self.assertEqual(replies.posts[0][1],payload)
            self.assertIn('trial_input_not_approved',app.error[0].value)
            app.run();app.run()
        self.assertEqual(len(replies.posts),1)

    def test_empty_input_inspection_does_not_post(self):
        replies=Replies()
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
            app.button(key='inspect_input').click().run()
        self.assertFalse(app.exception)
        self.assertFalse(replies.posts)
        self.assertTrue(any('영상을 선택' in row.value for row in app.warning))

    def test_default_comparison_scope_and_widget_changes_do_not_submit(self):
        replies=Replies()
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
            self.assertEqual(app.session_state['analysis_purpose'],'gpu_comparison')
            app.selectbox(key='analysis_purpose').select('full_report').run()
            app.session_state['analysis_selection']='A+B'
            app.run()
        self.assertFalse(app.exception)
        self.assertFalse(replies.posts)

    def test_ab_submit_then_refresh_is_one_post_with_explicit_comparison_scope(self):
        replies=Replies(jobs=[job(version='A'),job('b'*32,'B')])
        with patch('apps.cctv_ui.client.request',side_effect=replies), \
                patch('streamlit.file_uploader',return_value=io.BytesIO(b'0000ftyp-input')):
            app=AppTest.from_file(str(APP)).run()
            app.session_state['analysis_selection']='A+B'
            app.button[0].click().run()
            app.run();app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(replies.posts),1)
        query=parse_qs(urlsplit(replies.posts[0][0]).query)
        self.assertEqual(query,{'selection':['A+B'],'purpose':['gpu_comparison']})
        self.assertEqual(replies.posts[0][1],b'0000ftyp-input')

    def test_full_report_submit_sends_selected_version_and_reference_date(self):
        replies=Replies(jobs=[job(version='B')])
        with patch('apps.cctv_ui.client.request',side_effect=replies), \
                patch('streamlit.file_uploader',return_value=io.BytesIO(b'0000ftyp-input')):
            app=AppTest.from_file(str(APP)).run()
            app.session_state['analysis_selection']='B'
            app.selectbox(key='analysis_purpose').select('full_report')
            app.date_input(key='reference_date').set_value(date(2026,10,2))
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(parse_qs(urlsplit(replies.posts[0][0]).query),
            {'selection':['B'],'purpose':['full_report'],'reference_date':['2026-10-02']})

    def test_team_report_disables_date_and_omits_date_from_submission(self):
        replies=Replies(jobs=[job(version='B')], report_pipeline={
            'pipeline_id':'team-rag-v1','document_reference_date_supported':False})
        with patch('apps.cctv_ui.client.request',side_effect=replies), \
                patch('streamlit.file_uploader',return_value=io.BytesIO(b'0000ftyp-input')):
            app=AppTest.from_file(str(APP)).run()
            self.assertTrue(app.date_input(key='reference_date').disabled)
            self.assertTrue(any('필터를 지원하지 않습니다' in row.value for row in app.caption))
            app.session_state['analysis_selection']='B'
            app.selectbox(key='analysis_purpose').select('full_report')
            app.button[0].click().run()
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(replies.posts),1)
        self.assertEqual(parse_qs(urlsplit(replies.posts[0][0]).query),
            {'selection':['B'],'purpose':['full_report']})

    def test_failed_capability_lookup_does_not_silently_use_legacy_date_or_submit(self):
        replies=Replies()
        def failed_metadata(path,data=None,**kwargs):
            if path=='/report-pipeline':raise APIError('read_failed',503)
            return replies(path,data,**kwargs)
        with patch('apps.cctv_ui.client.request',side_effect=failed_metadata), \
                patch('streamlit.file_uploader',return_value=io.BytesIO(b'0000ftyp-input')):
            app=AppTest.from_file(str(APP)).run()
            self.assertTrue(app.date_input(key='reference_date').disabled)
            app.selectbox(key='analysis_purpose').select('full_report')
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertFalse(replies.posts)
        self.assertTrue(any('분석 요청은 보내지 않았습니다' in row.value for row in app.error))

    def test_comparison_keeps_no_candidates_and_failure_distinct(self):
        children=[job(version='A'),job('b'*32,'B','provider_failed')]
        children[1]['error_code']='s3_upload_failed'
        group={'selection_id':'d'*32,'selection':'A+B','purpose':'gpu_comparison','jobs':children}
        replies=Replies(children,[group])
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run();app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any('후보 없음' in row.value for row in app.subheader))
        self.assertTrue(any('단계 실패' in row.value for row in app.subheader))
        self.assertTrue(any(row.value=='s3_upload_failed' for row in app.error))
        self.assertFalse(replies.posts)

    def test_b_evidence_requests_previews_keeps_originals_and_distinct_times(self):
        child=job(version='B',stage='completed',purpose='gpu_comparison')
        child['evidence_events']=[{'event_id':'event_000','version':'B',
            'candidate_time_s':2.0,'display_sample_time_s':6.153846,'representative_source_time_s':6.16,
            'incident_context':{'limitations':['검수 필요']},'class_mapping':{'independent_training_metadata_verified':False}}]
        child['scene_events']={'event_000':{'media_bytes_verified':True}}
        replies=Replies([child],[{'selection_id':'d'*32,'selection':'B','purpose':'gpu_comparison','jobs':[child]}])
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        paths=[path for path,_ in replies.calls]
        for kind in ('clip','roi_clip','clip_preview','roi_clip_preview','frame'):
            self.assertTrue(any(path.endswith('/'+kind) for path in paths),kind)
        self.assertTrue(any('2.000초' in row.value and '6.160초' in row.value for row in app.caption))
        self.assertTrue(any('클래스 매핑' in row.value for row in app.caption))
        self.assertFalse(replies.posts)

    def test_new_task_resume_uses_task_id_without_readmitting_video(self):
        child=job(version='B',stage='paused',purpose='full_report',task_id='e'*32)
        child['flow']={'paused':True}
        replies=Replies([child],[{'selection_id':'d'*32,'selection':'B','purpose':'full_report','jobs':[child]}])
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
            app.button(key='resume_'+'e'*32).click().run()
        self.assertFalse(app.exception)
        self.assertEqual(replies.posts,[('/tasks/'+'e'*32+'/resume',b'')])

    def test_missing_preview_keeps_original_download_without_rerun_submission(self):
        child=job(version='B',stage='comparison_completed',purpose='gpu_comparison')
        child['scene_events']={'event_000':{'media_bytes_verified':True}}
        replies=Replies([child])
        def missing_preview(path,data=None,**kwargs):
            if path.endswith('_preview'):
                replies.calls.append((path,data))
                raise RuntimeError('preview_not_ready')
            return replies(path,data,**kwargs)
        with patch('apps.cctv_ui.client.request',side_effect=missing_preview):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        self.assertTrue(any('원본 근거를 다운로드' in row.value for row in app.info))
        self.assertTrue(any(row.value=='근거 비교 완료' for row in app.subheader))
        self.assertEqual(len(app.get('download_button')),2)
        self.assertFalse(replies.posts)

    def test_unknown_admission_response_is_not_automatically_posted_again(self):
        replies=Replies()
        def timeout(path,data=None,**kwargs):
            if data is not None:
                replies.calls.append((path,data))
                raise RuntimeError('접수 응답 불확실')
            return replies(path,data,**kwargs)
        with patch('apps.cctv_ui.client.request',side_effect=timeout), \
                patch('streamlit.file_uploader',return_value=io.BytesIO(b'0000ftyp-input')):
            app=AppTest.from_file(str(APP)).run()
            app.button[0].click().run()
            app.run();app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(replies.posts),1)

    def test_reports_are_rendered_only_for_matching_scene_analysis(self):
        child=job(version='A',stage='completed',purpose='full_report')
        child['descriptions']=[{'event_id':'event_000','analysis_id':'scene-a',
            'payload':{'description':{'accident_suspicion':'uncertain','incident_types':['unknown'],
                'summary':'영상 관측 원문 A','observations':[]}}}]
        draft={'document_coverage':'insufficient','scene_summary':'연결된 보고 초안 A',
            'notice':'검수용 초안','document_findings':[],'limitations':['근거 부족'],'followup':['검수']}
        saved={'draft':draft,'documents':[],'query':'A 검색 문장','reference_date':'2026-10-02'}
        child['reports']=[{'report_id':'report-a','settings':{'analysis_id':'scene-a'},
            'state':{'status':'report_stored','calls':{},'result':saved}}]
        foreign=deepcopy(child['reports'][0]);foreign['report_id']='report-b'
        foreign['settings']['analysis_id']='scene-b'
        foreign['state']['result']['draft']['scene_summary']='다른 장면의 보고 B'
        child['reports'].append(foreign)
        replies=Replies([child])
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        values=[row.value for row in app.text]
        self.assertIn('영상 관측 원문 A',values)
        self.assertIn('연결된 보고 초안 A',values)
        self.assertNotIn('다른 장면의 보고 B',values)
        self.assertFalse(replies.posts)

    def test_team_six_sections_and_sources_render_without_new_submission(self):
        child=team_job()
        replies=Replies([child],report_pipeline={
            'pipeline_id':'team-rag-v1','document_reference_date_supported':False})
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run();app.run()
        self.assertFalse(app.exception)
        rendered='\n'.join(row.value for row in app.markdown)
        for heading in TEAM_SECTIONS:
            self.assertIn('## '+heading,rendered)
        self.assertIn('기존 VLM 관측 원문', [row.value for row in app.text])
        self.assertTrue(any('필터 미지원' in row.value for row in app.caption))
        self.assertEqual(len(app.get('link_button')),1)
        self.assertEqual(sum(row.proto.label=='보고 결과 JSON 저장' for row in app.get('download_button')),1)
        self.assertFalse(replies.posts)

    def test_saved_actual_team_replay_displays_same_event_and_six_sections(self):
        directory=ROOT/'artifacts/be-worker/teammate-rag-integration-20261003-01/offline-replay-01/report'
        json_bytes=(directory/'report.json').read_bytes()
        markdown_bytes=(directory/'report.md').read_bytes()
        payload=json.loads(json_bytes)
        child=team_job()
        child['descriptions'][0]['payload']['team_event']=payload['input']
        saved=child['reports'][0]['state']['result']
        saved['report_json']=payload
        saved['report_markdown']=markdown_bytes.decode('utf-8')
        replies=Replies([child], report_pipeline={
            'pipeline_id':'team-rag-v1','document_reference_date_supported':False})
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run();app.run()
        self.assertFalse(app.exception)
        self.assertIn(payload['input']['description'],[row.value for row in app.text])
        body=next(row.value for row in app.markdown if '## 사고 개요' in row.value)
        for heading in TEAM_SECTIONS:
            self.assertIn('## '+heading,body)
        self.assertTrue(any(payload['event_id'] in row.value for row in app.caption))
        self.assertEqual(len(app.get('link_button')),len(payload['references']))
        self.assertFalse(replies.posts)
        self.assertEqual((directory/'report.json').read_bytes(),json_bytes)
        self.assertEqual((directory/'report.md').read_bytes(),markdown_bytes)

    def test_team_database_view_shape_renders_saved_case_with_provider_offline(self):
        from tests.be_worker.test_team_view import actual_records, ReadOnlyReplies, read_view
        row,descriptions,reports=actual_records()
        payload=reports[0]['state']['result']['report_json']
        conn=ReadOnlyReplies(row,descriptions,reports)
        viewed=read_view(conn)
        self.assertEqual(len(conn.queries),6)
        self.assertEqual(viewed[0]['descriptions'],descriptions)
        self.assertEqual(viewed[0]['reports'],reports)
        self.assertEqual(viewed[0]['stage'],'completed')
        wire_payload=json.loads(json.dumps(viewed))
        replies=Replies(wire_payload,connected=False,report_pipeline={
            'pipeline_id':'team-rag-v1','document_reference_date_supported':False})
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Runpod 기동 없이 조회' in row.value for row in app.caption))
        self.assertFalse(any(path == '/connection' for path, _ in replies.calls))
        self.assertIn(payload['input']['description'],[row.value for row in app.text])
        self.assertTrue(any(payload['event_id'] in row.value for row in app.caption))
        self.assertFalse(replies.posts)

    def test_team_saved_view_refresh_and_input_inspection_do_not_probe_or_submit(self):
        replies=Replies([team_job()],report_pipeline={
            'pipeline_id':'team-rag-v1','document_reference_date_supported':False})
        upload=io.BytesIO(b'0000ftyp-selected-input')
        upload.name='selected.mp4'
        with patch('apps.cctv_ui.client.request',side_effect=replies), \
                patch('streamlit.file_uploader',return_value=upload):
            app=AppTest.from_file(str(APP)).run()
            app.run()
            next(button for button in app.button
                 if button.label=='선택 영상 확인 · 분석 실행 안 함').click().run()
            inspection_visible=any('분석 요청은 보내지 않았습니다.' in row.value for row in app.info)
            app.run()
        self.assertFalse(app.exception)
        self.assertGreaterEqual(sum(path == '/jobs' for path, _ in replies.calls),4)
        self.assertFalse(any(path == '/connection' for path, _ in replies.calls))
        self.assertFalse(replies.posts)
        self.assertTrue(inspection_visible)

    def test_team_report_row_identity_mismatch_is_not_rendered(self):
        child=team_job()
        child['reports'][0]['state']['result']['report_id']='different-report'
        replies=Replies([child])
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        self.assertFalse(any('## 사고 개요' in row.value for row in app.markdown))
        self.assertTrue(any('stored_report_identity_mismatch' in row.value for row in app.error))
        self.assertFalse(replies.posts)

    def test_same_event_keeps_legacy_and_team_descriptions_and_matching_reports_visible(self):
        from tests.be_worker.test_team_view import actual_records, ReadOnlyReplies, read_view
        row,descriptions,reports=actual_records()
        old_scene={'event_id':'event_000','analysis_id':'legacy-scene',
                   'payload':{'description':{'accident_suspicion':'uncertain','incident_types':['unknown'],
                       'summary':'보존된 이전 VLM 관측 원문','observations':[]}}}
        old_report={'report_id':'legacy-report','settings':{'analysis_id':'legacy-scene'},
                    'state':{'status':'report_stored','calls':{},'result':{
                        'draft':{'document_coverage':'insufficient','scene_summary':'보존된 이전 보고 원문',
                                 'notice':'검수용 초안','document_findings':[],'limitations':['근거 부족'],
                                 'followup':['검수']},
                        'documents':[],'query':'이전 검색 문장','reference_date':'2026-10-02'}}}
        original=deepcopy((old_scene,old_report))
        conn=ReadOnlyReplies(row,descriptions,reports,legacy_descriptions=[old_scene],legacy_reports=[old_report])
        viewed=read_view(conn)
        replies=Replies(json.loads(json.dumps(viewed)),connected=False,report_pipeline={
            'pipeline_id':'team-rag-v1','document_reference_date_supported':False})
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run();app.run()
        self.assertFalse(app.exception)
        texts=[row.value for row in app.text]
        self.assertIn('보존된 이전 VLM 관측 원문',texts)
        self.assertIn('보존된 이전 보고 원문',texts)
        self.assertIn(descriptions[0]['payload']['team_event']['description'],texts)
        self.assertTrue(any('## 사고 개요' in row.value for row in app.markdown))
        self.assertTrue(any('문서 검색 기준일: 2026-10-02' in row.value for row in app.caption))
        self.assertTrue(any('필터 미지원' in row.value for row in app.caption))
        self.assertEqual(sum(row.proto.label=='보고 결과 JSON 저장' for row in app.get('download_button')),2)
        self.assertFalse(replies.posts)
        self.assertEqual((old_scene,old_report),original)

    def test_team_report_html_and_images_remain_display_text(self):
        child=team_job()
        child['reports'][0]['state']['result']['report_markdown'] += (
            '\n<script>alert(1)</script>\n![hidden](https://example.com/pixel.png)')
        child['reports'][0]['state']['result']['report_json']['references'][0]['source_url']='javascript:alert(1)'
        replies=Replies([child])
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        body=next(row for row in app.markdown if '## 사고 개요' in row.value)
        self.assertIn('&lt;script&gt;',body.value)
        self.assertIn(r'\![hidden]',body.value)
        self.assertFalse(body.proto.allow_html)
        self.assertFalse(app.get('html'))
        self.assertFalse(app.get('link_button'))
        self.assertFalse(replies.posts)

    def test_team_report_with_another_event_is_not_displayed_as_this_scene(self):
        child=team_job()
        child['reports'][0]['state']['result']['report_json']['event_id']='different-event'
        replies=Replies([child])
        with patch('apps.cctv_ui.client.request',side_effect=replies):
            app=AppTest.from_file(str(APP)).run()
        self.assertFalse(app.exception)
        self.assertFalse(any('## 사고 개요' in row.value for row in app.markdown))
        self.assertTrue(any('사건 ID가 일치하지 않습니다' in row.value for row in app.error))
        self.assertFalse(replies.posts)


TEAM_SECTIONS=('사고 개요','영상 관찰 및 판단 불가 항목','관제의 전달·요청 사항',
               '현장 대응 참고','확인 필요 사항','관련 근거')


def team_job():
    child=job(version='B',stage='completed',purpose='full_report')
    event=json.loads((ROOT/'tests/scene_description/fixtures/rag_v2/synthetic-event.json').read_text())
    event['event_id']='provider-event-000'
    event['description']='기존 VLM 관측 원문'
    child['descriptions']=[{'event_id':'event_000','analysis_id':'scene-team',
        'payload':{'schema_version':'cctv-team-scene-v1','team_event':event}}]
    report_json={'event_id':event['event_id'],'report_format_version':'2.1','input':event,
        'references':[{'reference_id':'R1','title':'실제 연결 문서','content':'문서 근거 원문',
                       'source_url':'https://example.com/evidence'}],
        'retrieval':{'date_filter_supported':False,'reference_date':None}}
    saved={'schema_version':'cctv-team-report-v1','pipeline_id':'team-rag-v1',
           'analysis_id':'scene-team','report_id':'report-team',
           'report_json':report_json,'report_markdown':'\n\n'.join('## '+h+'\n검수용 내용' for h in TEAM_SECTIONS)}
    child['reports']=[{'report_id':'report-team','settings':{'analysis_id':'scene-team'},
        'state':{'status':'report_stored','calls':{},'result':saved}}]
    return child


if __name__=='__main__':unittest.main()
