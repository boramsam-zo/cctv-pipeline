from datetime import date, datetime, timezone
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlencode
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from apps.cctv_ui.client import request
from apps.cctv_ui.report_view import (
    TEAM_REPORT_SCHEMA, TEAM_SCENE_SCHEMA, display_markdown, report_pipeline, source_link,
)

st.set_page_config(page_title='CCTV 분석', page_icon=':material/videocam:', layout='wide')
if st.session_state.get('cctv_navigation_enabled') and st.button(
        '관제 화면으로', icon=':material/arrow_back:', key='analysis_control_home'):
    st.session_state.control_refresh_pending = True
    st.switch_page('control_room.py')
st.title('CCTV 영상 분석')
st.caption('선택한 사고 영상 모델로 분석하고, 장면 설명·문서 근거·보고서까지 확인합니다.')
for key, value in [('selected_result', None), ('last_submission', None)]:
    st.session_state.setdefault(key, value)
VERSIONS = {'A': 'A · 과거 기준', 'B': 'X3D-S · 운영 epoch 7', 'R2': 'R(2+1)D-18 · epoch 7', 'A+B': 'A+B · 과거 판본 비교'}
PURPOSES = {'gpu_comparison': '분석 근거 비교', 'full_report': '장면 설명·보고까지'}
LABELS = {'prepared': '대기 중', 'queued': '대기 중', 'submitting': '분석 요청 중',
    'waiting': 'GPU 분석·S3 저장 중', 'evidence_ready': '근거 수집 대기',
    'handoff_ready': '근거 인계 완료', 'verifying_media': '클립·프레임 검증 중',
    'describing': '장면 설명 생성·저장 중', 'reporting': '문서 검색·보고 생성 중',
    'completed': '처리 완료', 'comparison_completed': '근거 비교 완료',
    'preparing_preview': '화면 재생용 클립 준비 중', 'embedding': '검색 문장 임베딩 중',
    'retrieval': '관련 문서 검색 중', 'generation': '보고 초안 생성 중',
    'no_candidates': '후보 없음 — 정상 판정 아님', 'paused': '실패 — 확인 후 재개 필요',
    'provider_failed': 'GPU 또는 S3 단계 실패', 'submission_unknown': '접수 응답 불확실 — 재제출 중지',
    'submission_rejected': '분석 요청 거절', 'verification_failed': '근거 검증 실패',
    'storage_not_ready': 'S3 저장 확인 필요'}


@st.cache_data(ttl=3600, max_entries=48)
def media(identifier, event, kind):
    return request(f'/jobs/{identifier}/media/{event}/{kind}', binary=True)


try:
    pipeline = report_pipeline(request)
    reference_supported = pipeline['document_reference_date_supported']
except Exception:
    pipeline = None
    reference_supported = False


model_selections = [item['selection'] for item in (pipeline or {}).get('analysis_models', [])]
choices = {key: VERSIONS[key] for key in model_selections if key in ('B', 'R2')} if model_selections else {key: VERSIONS[key] for key in ('A', 'B', 'A+B')}

with st.form('video_submission'):
    video = st.file_uploader('분석할 MP4 영상', type=['mp4'], max_upload_size=200, key='video_upload')
    selection = st.segmented_control('사고 영상 모델', list(choices), default='B' if model_selections else 'A',
        format_func=VERSIONS.get, required=True, key='analysis_selection')
    purpose = st.selectbox('실행 범위', list(PURPOSES), format_func=PURPOSES.get, key='analysis_purpose')
    reference = st.date_input('문서 검색 기준일', value=date.today(), key='reference_date',
                              disabled=not reference_supported)
    st.caption('분석 근거 비교는 GPU 결과만 처리하며 Gemini를 호출하지 않습니다. '
               '장면 설명·보고까지 선택하면 장면 설명과 문서 근거를 연결해 보고서를 생성합니다.')
    if pipeline is None:
        st.caption('보고 경로의 문서 검색 기준일 지원 정보를 확인하지 못했습니다. 보고 접수 전에 API 연결을 확인하세요.')
    elif not reference_supported:
        st.caption('새 동료 RAG 보고 경로는 문서 검색 기준일 필터를 지원하지 않습니다. '
                   '표시된 날짜는 새 보고의 문서 검색에 적용되지 않습니다. 과거 보고의 날짜 정보는 보존됩니다.')
    else:
        st.caption('문서 검색 기준일은 보고 실행에만 사용하며 사고 발생일과 별개입니다.')
    st.caption('같은 영상·모델 버전의 분석 근거는 재사용합니다. 모델을 바꾸면 별도 분석으로 기록합니다.')
    submit = st.form_submit_button('분석 시작', type='primary')
    inspect_input = st.form_submit_button('선택 영상 확인 · 분석 실행 안 함', key='inspect_input')
if submit or inspect_input:
    if video is None:
        st.warning('영상을 선택해 주세요.')
    elif selection not in choices or purpose not in PURPOSES:
        st.error('분석 버전과 실행 범위를 확인해 주세요.')
    else:
        payload = video.getvalue()
        input_receipt = {
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'action': 'inspect_only' if inspect_input else 'analysis_submission',
            'file_name': getattr(video, 'name', None),
            'ui_received_bytes': len(payload),
            'ui_received_sha256': hashlib.sha256(payload).hexdigest(),
            'selection': selection, 'purpose': purpose,
        }
        query = {'selection': selection, 'purpose': purpose}
        if purpose == 'full_report' and reference_supported:
            query['reference_date'] = reference.isoformat()
        if inspect_input:
            st.info('화면 서버가 받은 영상의 확인 기록입니다. 분석 요청은 보내지 않았습니다. '
                    '이 기록만으로 BE 접수 통과를 뜻하지는 않습니다.')
            st.json(input_receipt)
        else:
            try:
                if purpose == 'full_report' and pipeline is None:
                    raise RuntimeError('보고 경로 정보를 확인한 뒤 다시 접수해 주세요. 분석 요청은 보내지 않았습니다.')
                result = request('/selections?' + urlencode(query), payload)
                st.session_state.selected_result = 'selection:' + result['selection_id']
                st.session_state.result_picker = st.session_state.selected_result
                st.session_state.last_submission = result
                st.session_state.control_refresh_pending = True
                st.success('기존 요청을 불러왔습니다.' if result['already_present']
                           else '접수했습니다. worker가 선택한 범위를 처리합니다.')
            except Exception as exc:
                if str(exc) == 'trial_input_not_approved':
                    st.error('시험에 허용된 영상과 입력 정보가 일치하지 않아 접수가 거절됐습니다. '
                             '(trial_input_not_approved)')
                else:
                    st.error(str(exc))
                with st.expander('이번 접수의 입력 확인 기록', expanded=True):
                    st.json(input_receipt)
                st.info('접수 결과를 아래 목록에서 먼저 확인하세요. 화면 갱신은 영상을 다시 제출하지 않습니다.')


def version_label(job):
    version = (job.get('analysis_profile') or {}).get('version') or job.get('version')
    return VERSIONS.get(version, '버전 미기록 · 기존 결과')


def show_clip(identifier, event_id, kind, label, version):
    st.caption(label)
    try:
        original = media(identifier, event_id, kind)
    except Exception as exc:
        st.warning('근거 읽기 실패: ' + str(exc))
        return
    if version in ('B', 'R2'):
        try:
            st.video(media(identifier, event_id, kind + '_preview'))
            st.caption('화면 재생용 사본 · 아래 다운로드는 원본 근거입니다.')
        except Exception:
            st.info('재생용 사본이 아직 준비되지 않았습니다. 원본 근거를 다운로드해 확인할 수 있습니다.')
    else:
        st.video(original)
    st.download_button(label + ' 원본 저장', original, file_name=f'{identifier[:12]}-{event_id}-{kind}.mp4',
        mime='video/mp4', key=f'media_{identifier}_{event_id}_{kind}', on_click='ignore')


def show_evidence(job, event_id, event):
    identifier = job['worker_job_id']
    version = event.get('version') or (job.get('analysis_profile') or {}).get('version') or job.get('version')
    timings = [('candidate_time_s', '사고 후보 시각'), ('display_sample_time_s', '표시 요청 시각'),
               ('representative_source_time_s', '실제 표시 프레임 시각')]
    shown = [f'{label}: {float(event[key]):.3f}초' for key, label in timings if event.get(key) is not None]
    if shown:
        st.caption(' · '.join(shown))
        st.caption('원본 영상 기준 시각이며 확인된 충돌 시점은 아닙니다.')
    show_clip(identifier, event_id, 'clip', '전체 화면 클립', version)
    if version in ('B', 'R2'):
        show_clip(identifier, event_id, 'roi_clip', 'ROI 확대 클립', version)
    try:
        st.image(media(identifier, event_id, 'frame'),
            caption='모델 주석 프레임 · PRIMARY/RELATED는 검토 후보입니다.' if version in ('B', 'R2')
                    else '원본 대표 프레임')
    except Exception as exc:
        st.warning('프레임 표시 실패: ' + str(exc))
    if version in ('B', 'R2'):
        st.caption('박스 겹침은 실제 접촉의 확정 근거가 아닙니다. 클래스 매핑·당사자 후보는 검수가 필요합니다.')
        with st.expander('모델 문맥·클래스 매핑·한계'):
            st.json({'incident_context': event.get('incident_context'),
                     'class_mapping': event.get('class_mapping')})
    elif event.get('yolo_objects') is not None:
        with st.expander('단일 프레임 객체 목록'):
            st.json(event['yolo_objects'])


def show_team_report(report, saved):
    if (str(saved['analysis_id']) != str(report['settings']['analysis_id'])
            or str(saved['report_id']) != str(report['report_id'])):
        raise ValueError('stored_report_identity_mismatch')
    payload = saved['report_json']
    st.subheader('보고 초안')
    st.caption('동료 RAG · CPU 문서 검색·템플릿 보고 · 운영자 검수가 필요한 초안')
    st.caption('문서 검색 기준일 필터 미지원 · 날짜에 따른 문서 제외는 적용되지 않았습니다.')
    st.caption('사건 ID: ' + str(payload['event_id']) + ' · 보고 ID: ' + str(report['report_id']))
    st.markdown(display_markdown(saved['report_markdown']), unsafe_allow_html=False)
    with st.expander('문서 근거·검색 기록'):
        for reference in payload.get('references', []):
            identifier = str(reference['reference_id'])
            st.text('[' + identifier + '] ' + str(reference.get('title', '')))
            st.text(str(reference.get('content', '')))
            url = source_link(reference.get('source_url'))
            if url:
                st.link_button('출처 ' + identifier, url)
            else:
                st.caption('출처 URL 미확인')
        st.json(payload.get('retrieval', {}))
    st.download_button('보고 결과 JSON 저장', json.dumps(saved, ensure_ascii=False, indent=2),
        file_name='report-' + str(report['report_id']) + '.json', mime='application/json',
        key='report_' + str(report['report_id']), on_click='ignore')


def show_report(report):
    state = report['state']
    if state['status'] != 'report_stored':
        st.info('보고 단계: ' + state['status'])
        return
    if state['result'].get('schema_version') == TEAM_REPORT_SCHEMA:
        show_team_report(report, state['result'])
        return
    saved, draft = state['result'], state['result']['draft']
    if draft['document_coverage'] in ('insufficient', 'no_documents'):
        st.warning('관련 문서 근거 부족 — 처리 완료가 법적 판단의 충분성을 뜻하지 않습니다.')
    st.subheader('보고 초안')
    st.text(draft['scene_summary'])
    st.caption(draft['notice'])
    docs = {item['citation_id']: item for item in saved['documents']}
    for finding in draft['document_findings']:
        st.text(f"[{finding['citation_id']}] {finding['interpretation']}")
        st.text('인용: ' + finding['quote'])
        st.link_button('출처 ' + finding['citation_id'], docs[finding['citation_id']]['metadata']['source_url'])
    st.text('한계\n' + '\n'.join(draft['limitations']))
    st.text('확인할 사항\n' + '\n'.join(draft['followup']))
    with st.expander('검색 문장·검색 문서'):
        st.text(saved['query'])
        st.caption('문서 검색 기준일: ' + saved['reference_date'] + ' · 사고 발생일 미확인')
        for doc in saved['documents']:
            st.text('[' + doc['citation_id'] + '] ' + doc['content'])
    st.download_button('보고 결과 JSON 저장', json.dumps(saved, ensure_ascii=False, indent=2),
        file_name='report-' + str(report['report_id']) + '.json', mime='application/json',
        key='report_' + str(report['report_id']), on_click='ignore')


def show_description(description, reports):
    result = description['payload']
    if result.get('schema_version') == TEAM_SCENE_SCHEMA:
        observed = result['team_event']
        st.subheader('영상 관측 설명')
        st.text(observed['description'])
        with st.expander('관측 내용·근거·미확인 항목'):
            st.json({key: observed[key] for key in
                     ('accident', 'participants', 'hazards', 'people', 'evidence', 'uncertainties')})
    else:
        observed = result['description']
        st.text('사고 의심: ' + observed['accident_suspicion'] + ' / 유형: ' + ', '.join(observed['incident_types']))
        st.subheader('영상 관측 설명')
        st.text(observed['summary'])
        with st.expander('관측 내용·시각·근거'):
            st.json(observed['observations'])
    for report in reports:
        if str(report['settings']['analysis_id']) == str(description['analysis_id']):
            if (result.get('schema_version') == TEAM_SCENE_SCHEMA
                    and report['state'].get('result', {}).get('schema_version') == TEAM_REPORT_SCHEMA
                    and report['state']['result']['report_json']['event_id'] != observed['event_id']):
                st.error('영상 관측과 보고의 사건 ID가 일치하지 않습니다. 저장 결과를 확인해 주세요.')
                continue
            show_report(report)


def show_job(job):
    identifier = job['worker_job_id']
    st.subheader(version_label(job))
    stage = job['stage']
    if stage == 'reporting':
        pending = [r['state']['status'] for r in job.get('reports', []) if r['state']['status'] != 'report_stored']
        if pending:
            stage = pending[0]
    st.subheader(LABELS.get(stage, stage))
    st.caption('작업 ID: ' + identifier)
    if st.session_state.get('cctv_navigation_enabled') and st.button(
            '관제에서 영상·보고 보기', icon=':material/videocam:', key='analysis_control_job_' + identifier):
        st.session_state.control_source = 'api'
        st.session_state.control_selected_job = identifier
        st.session_state.control_refresh_pending = True
        st.switch_page('control_room.py')
    if job.get('purpose') in PURPOSES:
        st.caption('실행 범위: ' + PURPOSES[job['purpose']])
    tracking = job.get('mlflow')
    if tracking:
        st.caption('사고 모델: ' + tracking['model_id'] + ' · ' + tracking['model_version'])
        tracking_labels = {'recorded': '저장 완료', 'pending': '기록 대기 · 분석 결과와 보고서는 확인할 수 있습니다.',
                           'in_progress': '분석이 끝나면 실행 기록을 저장합니다.',
                           'not_submitted': '분석 제출 전 중단 · 실행 기록 없음'}
        st.caption('MLflow: ' + tracking_labels.get(tracking['status'], '기록 상태 확인 필요'))
    flow = job.get('flow') or {}
    if flow.get('elapsed_seconds') is not None:
        st.caption(f"접수 후 경과시간: {float(flow['elapsed_seconds']):.1f}초")
    metrics = job.get('metrics') or {}
    seconds = metrics.get('timings', {}).get('job_seconds')
    memory = metrics.get('memory', {}).get('peak_allocated_bytes')
    if seconds is not None:
        st.caption(f'GPU 작업 전체 경과: {float(seconds):.3f}초 · 모델 로딩·근거 생성 포함')
    if memory is not None:
        st.caption(f'PyTorch 최대 할당 메모리: {float(memory) / 1024**2:.1f} MiB · GPU 전체 사용량과 다름')
    if job.get('error_code'):
        st.error(job['error_code'])
    if flow.get('paused') and st.button('실패 단계부터 재개', key='resume_' + str(job.get('task_id') or identifier)):
        endpoint = '/tasks/' + job['task_id'] if job.get('task_id') else '/jobs/' + identifier
        try:
            request(endpoint + '/resume', b'')
            st.info('재개를 요청했습니다. 불확실한 모델 요청은 자동 재호출하지 않습니다.')
        except Exception as exc:
            st.error(str(exc))
    st.caption('접수: ' + str(job.get('submitted_at', '')) + ' · 최근 큐 갱신: ' + str(job.get('queue_updated_at', '')))
    events = {row['event_id']: row for row in job.get('evidence_events', [])}
    descriptions = {}
    for row in job.get('descriptions', []):
        descriptions.setdefault(row['event_id'], []).append(row)
    event_ids = list(dict.fromkeys([*events, *job.get('scene_events', {}), *descriptions]))
    for event_id in event_ids:
        stored = job.get('scene_events', {}).get(event_id, {})
        event_descriptions = descriptions.get(event_id, [])
        with st.container(border=True):
            st.subheader('장면 ' + event_id)
            if stored.get('media_bytes_verified') or event_descriptions:
                show_evidence(job, event_id, events.get(event_id, {}))
            else:
                st.caption('근거 다운로드·해시 검증 대기')
            if event_descriptions:
                for description in event_descriptions:
                    with st.container(border=len(event_descriptions) > 1):
                        if len(event_descriptions) > 1:
                            st.caption('저장 설명 ID: ' + str(description['analysis_id']))
                        show_description(description, job.get('reports', []))
            elif job.get('purpose') == 'gpu_comparison':
                st.caption('분석 근거 비교 실행입니다. 장면 설명·보고 생성은 요청하지 않았습니다.')
            else:
                st.caption('장면 설명 상태: ' + stored.get('status', '대기'))
    with st.expander('처리 단계·호출 기록'):
        st.json({'GPU 제출': job.get('post_attempts', 0), '장면 설명 호출': job.get('scene_calls', 0),
                 '장면별 상태': job.get('scene_events', {}), '분석 버전': job.get('analysis_profile'),
                 '보고': [{'report_id': r['report_id'], 'state': r['state']['status'],
                           'calls': r['state'].get('calls', {})} for r in job.get('reports', [])]})


@st.fragment(run_every=5)
def results():
    try:
        if pipeline is not None and pipeline['pipeline_id'] == 'legacy':
            connection = request('/connection')
            if connection.get('connected'):
                st.caption('Runpod 연결됨 · GPU 처리 슬롯 1')
            else:
                st.warning('Runpod 연결 대기: ' + connection.get('error_code', 'unavailable') + ' · 접수된 영상은 대기열에 보관됩니다.')
        else:
            st.caption('저장 결과는 Runpod 기동 없이 조회할 수 있습니다.')
        worker = request('/worker')
        age = worker.get('age_seconds')
        if age is None:
            st.warning('worker 기동 확인 대기')
        elif float(age) > 180:
            st.warning('worker 상태 갱신이 지연되고 있습니다. 처리 결과를 확인하기 전 재제출하지 마세요.')
        for error in worker.get('payload', {}).get('errors', []):
            st.warning('worker 확인 필요: ' + error)
        groups = request('/selections')
        jobs = request('/jobs')
    except Exception as exc:
        st.error(str(exc))
        return
    choices = {'selection:' + row['selection_id']: row for row in groups}
    grouped_jobs = {child['worker_job_id'] for row in groups for child in row['jobs']}
    choices.update({'job:' + row['worker_job_id']: row for row in jobs if row['worker_job_id'] not in grouped_jobs})
    if not choices:
        st.info('아직 접수된 영상이 없습니다.')
        return
    def label(value):
        row = choices[value]
        if value.startswith('selection:'):
            return f"{VERSIONS.get(row['selection'], row['selection'])} · {PURPOSES.get(row['purpose'], row['purpose'])} · {row['selection_id'][:12]}"
        return version_label(row) + ' · ' + row['worker_job_id'][:12]
    ids = list(choices)
    selected = st.session_state.selected_result
    chosen = st.selectbox('작업 선택', ids, index=ids.index(selected) if selected in ids else len(ids)-1,
                          format_func=label, key='result_picker')
    st.session_state.selected_result = chosen
    try:
        if chosen.startswith('selection:'):
            group = request('/selections/' + chosen.split(':', 1)[1])
            st.caption('요청 ID: ' + group['selection_id'])
            if group['purpose'] == 'gpu_comparison':
                st.caption('분석 근거 비교 · 조회와 새로고침은 GPU·Gemini 작업을 추가하지 않습니다.')
            children = group['jobs']
            if len(children) > 1:
                st.info('같은 영상의 두 파이프라인을 비교합니다. 설정·근거 범위가 달라 실행시간만으로 우열을 판단하지 않습니다.')
                for column, job in zip(st.columns(len(children)), children):
                    with column:
                        show_job(job)
            else:
                for job in children:
                    show_job(job)
        else:
            show_job(request('/jobs/' + chosen.split(':', 1)[1]))
    except Exception as exc:
        st.error('결과 조회 실패: ' + str(exc))


results()
