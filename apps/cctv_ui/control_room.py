"""Read-only control room for recorded CCTV results. No analysis submission path."""
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st

from apps.cctv_ui.client import request, fetch_media
from apps.cctv_ui.control_data import (
    load_saved_snapshot, normalize_snapshot, normalize_case, media_reference,
    historical_frame_reference,
)
from apps.cctv_ui.control_panels import render_observations, render_checks, render_agencies, render_report
from apps.cctv_ui import control_demo as demo

st.set_page_config(page_title='CCTV 관제 · 저장 결과', page_icon=':material/videocam:', layout='wide')
st.html(Path(__file__).with_name('control_room.css'))
st.session_state.setdefault('control_selected_job', None)
st.session_state.setdefault('control_demo', None)

STAGES = {
    'completed': '처리 완료', 'comparison_completed': '근거 비교 완료',
    'no_candidates': '후보 없음 · 정상 판정 아님', 'paused': '확인 필요',
    'provider_failed': 'GPU·저장 실패', 'verification_failed': '근거 검증 실패',
    'submission_unknown': '접수 불확실', 'submission_rejected': '접수 거절',
    'storage_not_ready': '저장 확인 필요', 'queued': '대기 중', 'prepared': '대기 중',
    'waiting': '분석·저장 중', 'describing': '관찰 설명 처리 중', 'reporting': '보고 처리 중',
}
MEDIA = [('original', '원본 영상', '업로드한 전체 원본 · 사건 전후의 전체 흐름'),
         ('clip', '사고 후보 클립', '후보 전후의 전체 화면 동영상'),
         ('frame', '박스 주석 이미지 · 보조 근거', '박스·궤적이 표시된 정지 이미지 · 동영상과 구분되는 보조 근거')]


def time_text(value):
    from zoneinfo import ZoneInfo
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return str(value) + ' · 시간대 미확인'
        return dt.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S KST')
    except (TypeError, ValueError):
        return '미기록'


@st.cache_data(ttl=30, max_entries=4, show_spinner=False)
def api_snapshot(base_url):
    # The base URL is part of the cache identity; client credentials remain server-side.
    return normalize_snapshot(request('/jobs'), request('/selections'), metadata={
        'mode': 'api', 'captured_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'API가 반환한 전체 작업 · 작업 ID로 중복 제거',
    })


@st.cache_data(ttl=3600, max_entries=2, show_spinner=False)
def original_bytes(base_url, identifier):
    return fetch_media(identifier, None, 'original')


@st.cache_data(ttl=3600, max_entries=24, show_spinner=False)
def evidence_bytes(base_url, identifier, event_id, kind):
    return fetch_media(identifier, event_id, kind)


def get_media(job, event_id, kind, mode):
    if mode == 'api':
        if kind == 'original':
            return original_bytes(os.environ.get('CCTV_API_URL', ''), job['worker_job_id'])
        return evidence_bytes(os.environ.get('CCTV_API_URL', ''), job['worker_job_id'], event_id, kind)
    reference = media_reference(job, event_id, kind)
    if reference.get('status') == 'integrity_failed':
        raise ValueError('보관 파일의 해시·크기 검증 실패')
    if reference.get('local_path'):
        return Path(reference['local_path']).read_bytes()
    return None


def show_media(job, event_id, kind, label, mode):
    try:
        raw = get_media(job, event_id, kind, mode)
    except Exception as exc:
        st.warning('영상 조회 확인 필요: ' + str(exc))
        return
    if raw is None:
        st.info(label + ' 미연결 · 현재 조회 범위에 재생 가능한 파일이 없습니다.')
        return
    if kind == 'frame':
        st.image(raw, caption='모델 주석 프레임 · 동영상과 별개인 보조 이미지 근거', width='stretch')
        return
    version = (job.get('analysis_profile') or {}).get('version') or job.get('version')
    playback = raw
    if version in ('B', 'R2') and kind != 'original':
        try:
            playback = get_media(job, event_id, kind + '_preview', mode)
        except Exception:
            playback = None
        if playback is None:
            st.info('브라우저 재생용 사본 미연결 · 보존 원본을 다운로드해 확인할 수 있습니다.')
    if playback is not None:
        # CSS constrains the stage height with contain; the stored aspect ratio is preserved.
        st.video(playback, autoplay=False, width='stretch')
    with st.container(horizontal=True, vertical_alignment='center', gap='small'):
        st.download_button(label + ' 저장', raw, file_name=f"{job['worker_job_id'][:12]}-{kind}.mp4",
                           mime='video/mp4', key=f"control_download_{job['worker_job_id']}_{event_id}_{kind}",
                           on_click='ignore', width='content')
        if playback is not None and playback is not raw:
            st.caption('브라우저 재생용 사본 · 다운로드는 보존 원본입니다.')


def choose_job(identifier, event_id=None, analysis_id=None):
    st.session_state.control_selected_job = identifier
    st.session_state.control_scroll_to_top = True
    st.session_state.control_navigation = st.session_state.get('control_navigation', 0) + 1
    if event_id is not None:
        st.session_state['control_event_' + identifier] = event_id
    if analysis_id is not None:
        st.session_state['control_analysis_' + identifier + '_' + event_id] = analysis_id


def status_color(status):
    return ('green' if status in ('completed', 'comparison_completed', 'no_candidates')
            else 'red' if status in ('provider_failed', 'verification_failed', 'submission_rejected')
            else 'orange' if status in ('paused', 'storage_not_ready', 'submission_unknown') else 'blue')


def start_demo(targets, scope):
    target = next((t for t in targets if demo.target_key(t) == st.session_state.get('control_demo_target')), None)
    if target is not None:
        st.session_state.control_demo = demo.schedule(st.session_state.control_demo, target, scope)


def reset_demo():
    st.session_state.control_demo = None


def read_demo():
    st.session_state.control_demo = demo.acknowledge(st.session_state.control_demo)


@st.fragment(run_every=0.2 if (st.session_state.control_demo or {}).get('status') == 'pending' else None)
def alert_banner(snapshot, scope):
    current = st.session_state.control_demo
    advanced = demo.advance(current)
    if advanced != current:
        st.session_state.control_demo = advanced
        st.rerun()  # Redraw the one banner and the matching card; then stop the timer.
    if not current:
        return
    target = current['target']
    with st.container(border=True, key='control_demo_banner_' + current['status'], gap='xsmall'):
        with st.container(horizontal=True, vertical_alignment='center', gap='small'):
            st.badge('시연 모드 · 저장 사건 재현', color='violet')
            if current['status'] == 'pending':
                st.text('알림 재현 대기 · 2초 뒤 1건 표시')
            else:
                st.text(('새 알림 1건' if current['status'] == 'unread' else '알림 읽음')
                        + ' · ' + target['job_id'][:8] + ' · ' + target['event_id'])
                available = current['scope'] == scope and demo.target_available(snapshot, target)
                if st.button('알림 열기', icon=':material/open_in_new:', disabled=not available, key='control_demo_open'):
                    choose_job(target['job_id'], target['event_id'], target['analysis_id'])
                    st.rerun()
                if st.button('알림 확인', icon=':material/done:', disabled=current['status'] == 'read', key='control_demo_read'):
                    read_demo()
                    st.rerun()
                if not available:
                    st.caption('현재 조회 자료에 같은 사건·보고가 없습니다. 원래 조회 자료를 선택해 주세요.')
        st.caption(('시연 알림 시각: ' + time_text(demo.timestamp(current['arrived_at'])) + ' · '
                    if current['arrived_at'] is not None else '')
                   + '화면 세션의 재현·읽음 상태입니다. 실제 접수·분석·사고 확정·운영자 판단 저장과 무관합니다.')


def demo_controls(snapshot, scope):
    targets = demo.candidates(snapshot)
    by_key = {demo.target_key(t): t for t in targets}
    pending = (st.session_state.control_demo or {}).get('status') == 'pending'
    with st.container(border=True, key='control_demo_controls', gap='xsmall'):
        with st.container(horizontal=True, vertical_alignment='center', gap='small'):
            st.badge('시연 모드 · 저장 사건 재현', color='violet')
            st.selectbox('알림 재현 사건', list(by_key), key='control_demo_target',
                         format_func=lambda k: str(by_key[k]['version'] or '?') + ' · '
                         + by_key[k]['job_id'][:8] + ' · ' + by_key[k]['title'],
                         label_visibility='collapsed', width=420, disabled=pending or not targets,
                         persist_state='session')
            st.button('알림 재현', icon=':material/notifications_active:', key='control_demo_start',
                      disabled=pending or not targets, on_click=start_demo, args=(targets, scope))
            st.button('시연 초기화', key='control_demo_reset', on_click=reset_demo,
                      disabled=st.session_state.control_demo is None)
        st.caption('보고·근거가 있는 저장 사건을 선택하면 2초 뒤 알림 1건이 표시됩니다. 실제 기록·통계는 변경하지 않습니다.'
                   if targets else '이 조회 범위에는 알림 시연에 사용할 저장 보고·근거가 없습니다.')


def show_thumbnail(job, case, mode):
    event_id = case.get('event_id')
    try:
        kind = 'thumbnail' if mode == 'saved' else 'frame'
        thumb = get_media(job, event_id, kind, mode) if event_id or mode == 'saved' else None
    except Exception:
        st.warning('영상 이미지 조회·검증 실패 · 상세에서 근거 상태를 확인하세요.')
        return
    if thumb:
        st.image(thumb, width='stretch')
    else:
        st.markdown(':material/videocam: **영상 이미지 미연결**')


def show_card_notice(job, scope):
    notice = demo.card_notice(st.session_state.control_demo, job['worker_job_id'], scope)
    if notice:
        st.badge(notice, color='violet', icon=':material/notifications:')


def monitor(snapshot, mode, scope, selected):
    with st.container(border=True, key='control_monitor', gap='xsmall'):
        st.subheader('CCTV 영상 모니터링')
        stats = snapshot['stats']
        st.caption(f"저장 {len(snapshot['jobs'])}작업 · 완료 {stats['completed_count']} · 확인 필요 {stats['attention_count']}")
        with st.container(height=680, border=False, key='control_monitor_list', gap='small', autoscroll=False):
            for job in sorted(snapshot['jobs'], key=lambda j: j.get('submitted_at') or '', reverse=True):
                case = normalize_case(job)
                identifier = job['worker_job_id']
                active = identifier == selected
                with st.container(border=True, key='control_monitor_card_' + ('selected_' if active else '') + identifier, gap='xsmall'):
                    with st.container(horizontal=True, vertical_alignment='center', gap='xsmall'):
                        st.text(str(case.get('version') or '?') + ' · ' + identifier[:8])
                        st.badge(case['status_label'], color=status_color(case['status']))
                    show_thumbnail(job, case, mode)
                    st.text(' · '.join(case['type_labels']) if case['type_labels'] else '영상 관찰 미저장')
                    st.caption('접수 ' + time_text(job.get('submitted_at')))
                    st.caption('카메라 ' + str(case.get('camera_id') or '미등록') + ' · 위치 ' + str(case.get('location') or '미등록'))
                    show_card_notice(job, scope)
                    st.caption('현재 선택' if active else '사건 선택')
                    # The native button covers this card via scoped CSS; keyboard access stays native.
                    st.button('영상 선택 · ' + identifier[:8], key='control_monitor_open_' + identifier,
                              on_click=choose_job, args=(identifier,), width='stretch')
        st.caption('정지 미리보기 · 실시간 수신 미연결')


def show_annotation(job, event_id, mode):
    try:
        raw = get_media(job, event_id, 'frame', mode)
    except Exception as exc:
        st.warning('주석 이미지 조회 확인 필요: ' + str(exc))
        return
    if raw is not None:
        with st.container(key='control_annotation_image'):
            st.image(raw, caption='현재 작업의 대표 정지 이미지 · 동영상과 별개인 보조 근거', width='stretch')
        evidence = next((e for e in job.get('evidence_events', []) if e.get('event_id') == event_id), {})
        if evidence.get('representative_source_time_s') is not None:
            st.caption(f"대표 프레임: 원본 {evidence['representative_source_time_s']:g}초")
        return
    st.caption('현재 작업의 주석 이미지 미연결')
    if mode == 'saved':
        try:
            reference = historical_frame_reference(job, event_id)
        except Exception as exc:
            st.warning('과거 주석 이미지 조회 확인 필요: ' + str(exc))
            return
        if reference.get('status') in ('integrity_failed', 'identity_mismatch'):
            st.warning('과거 주석 이미지의 원본 연결·파일 검증 실패')
            return
        if reference.get('local_path'):
            with st.container(key='control_annotation_image'):
                st.image(Path(reference['local_path']).read_bytes(),
                         caption='동일 원본의 과거 B 분석 참고 · 박스 주석 정지 이미지', width='stretch')
            st.caption('이 이미지는 현재 분석의 이미지 근거가 아닙니다. 이전 분석의 박스·궤적을 참고합니다.')
            provenance = reference.get('provenance') or {}
            with st.expander('박스 이미지 출처 · 보조 기록'):
                st.json(provenance)
            return
    st.info('박스 주석 이미지 미연결 · 현재 조회 범위에 확인된 사본이 없습니다.')


def case_title(case):
    return ('영상 후보' if case.get('event_id') else '영상 작업') + ' · ' + str(case.get('version') or '버전 미기록') + ' · ' + case['worker_job_id'][:8]


def review_text(case):
    review = case.get('human_review')
    if review == 'unreviewed':
        return '사람 검토 전'
    return '사람 검토 기록: ' + str(review) if review else '사람 검토 기록 미연결'


def gallery(snapshot, mode, scope):
    jobs = sorted(snapshot['jobs'], key=lambda j: j.get('submitted_at') or '', reverse=True)
    demo_controls(snapshot, scope)
    with st.container(key='control_statistics'):
        statistics(snapshot)
    with st.container(horizontal=True, vertical_alignment='center', key='control_gallery_toolbar'):
        st.subheader('영상 목록', width='stretch')
        grid = st.segmented_control('목록 배치', ['3×3', '4×4'], default='3×3', required=True,
                                    key='control_grid', width='content', label_visibility='collapsed', persist_state='session')
    st.caption(('원본 첫 프레임 · 정지 미리보기' if mode == 'saved' else '주석 프레임 · 보조 이미지')
               + ' · 슬롯은 배치 수이며 실제 연결 CCTV 수·독립 사고 수가 아닙니다.')
    size = 4 if grid == '4×4' else 3
    capacity = size * size
    pages = max(1, math.ceil(len(jobs) / capacity))
    page = st.selectbox('목록 페이지', list(range(1, pages + 1)), key='control_page',
                        format_func=lambda n: f'{n} / {pages}') if pages > 1 else 1
    visible = jobs[(page - 1) * capacity:page * capacity]
    for row in range(size):
        for offset, column in enumerate(st.columns(size, gap='small', wrap=True)):
            index = row * size + offset
            with column, st.container(border=True, key=f'control_card_{index}', gap='xsmall'):
                if index >= len(visible):
                    with st.container(key=f'control_empty_{index}'):
                        st.markdown(':material/videocam_off: **빈 슬롯**')
                    st.caption('등록된 작업 없음')
                    continue
                job = visible[index]
                case = normalize_case(job)
                show_thumbnail(job, case, mode)
                show_card_notice(job, scope)
                st.text(case_title(case))
                st.caption(case['status_label'] + ' · ' + review_text(case))
                st.caption('접수 ' + time_text(job.get('submitted_at')))
                st.button('사건 확인', icon=':material/arrow_forward:', width='stretch',
                          key='control_open_' + job['worker_job_id'], on_click=choose_job,
                          args=(job['worker_job_id'],))



def statistics(snapshot):
    # Counts are computed from the returned jobs, never copied from the HTML mockup.
    jobs = snapshot['jobs']
    stats = snapshot['stats']
    complete = stats['completed_count']
    attention = stats['attention_count']
    st.subheader('관제 기록 집계')
    with st.container(horizontal=True, key='control_metrics'):
        for name, value in [('고유 작업', len(jobs)), ('처리 완료', complete),
                            ('확인 필요 작업', attention), ('대기·처리 중', len(jobs)-complete-attention)]:
            st.metric(name, value, border=True)
    left, right = st.columns(2, wrap=True)
    from collections import Counter
    statuses = Counter()
    for state, count in stats['states'].items():
        statuses[STAGES.get(state, state)] += count
    hours = Counter(time_text(j.get('submitted_at'))[:13] + '시' for j in jobs
                    if time_text(j.get('submitted_at')) != '미기록' and '시간대 미확인' not in time_text(j.get('submitted_at')))
    with left, st.container(border=True, key='control_status_chart', gap='xsmall'):
        st.markdown('**작업 상태**')
        if statuses:
            st.bar_chart([{'상태': k, '작업 수': v} for k, v in statuses.items()], x='상태', y='작업 수', horizontal=True, height=100, color='#2463df')
        else:
            st.caption('집계할 작업 없음')
    with right, st.container(border=True, key='control_hour_chart', gap='xsmall'):
        st.markdown('**접수 시간대 · KST**')
        if hours:
            import altair as alt
            # Short axis labels keep the bars visible; the tooltip retains the full date.
            chart = alt.Chart(alt.Data(values=[{'접수 시간대': k, '작업 수': v}
                                               for k, v in sorted(hours.items())])).mark_bar(color='#2463df').encode(
                x=alt.X('접수 시간대:N', title=None, axis=alt.Axis(
                    labelAngle=0, labelExpr="substring(datum.label, 5)", labelFontSize=10)),
                y=alt.Y('작업 수:Q', title=None, axis=alt.Axis(tickMinStep=1, labelFontSize=10)),
                tooltip=['접수 시간대:N', '작업 수:Q'],
            ).properties(height=72)
            st.altair_chart(chart, width='stretch')
        else:
            st.caption('확인된 접수시각 없음')
    st.caption(f"고유 보고 {stats['report_count']}개 · 저장 완료 보고 {stats['stored_report_count']}개 · "
               f"영상 후보 {stats['candidate_event_count']}개 · 후보는 작업/장면 기준, 확정 사고 수 아님 · 접수시각은 사고시각과 별개")


def detail(job, mode):
    st.button('영상 목록으로', icon=':material/arrow_back:', on_click=choose_job, args=(None,), key='control_back')
    event_ids = list(dict.fromkeys([e['event_id'] for e in job.get('evidence_events', []) if e.get('event_id')]
                    + [d['event_id'] for d in job.get('descriptions', []) if d.get('event_id')]))
    event_id = st.selectbox('영상 후보', event_ids, key='control_event_' + job['worker_job_id'], persist_state='session') if len(event_ids)>1 else (event_ids[0] if event_ids else None)
    descriptions = [d for d in job.get('descriptions', []) if d.get('event_id') == event_id]
    analysis_id = None
    if len(descriptions) > 1:
        ids = [d['analysis_id'] for d in descriptions]
        by_id = {d['analysis_id']: d for d in descriptions}
        default = next((i for i, d in enumerate(descriptions) if d.get('payload', {}).get('schema_version') == 'cctv-team-scene-v1'), 0)
        analysis_key = 'control_analysis_' + job['worker_job_id'] + '_' + str(event_id)
        analysis_id = st.selectbox('저장된 관찰·보고', ids, index=None if analysis_key in st.session_state else default,
                                   format_func=lambda i: ('동료 6절 보고' if by_id[i].get('payload', {}).get('schema_version') == 'cctv-team-scene-v1' else '기존 저장 보고') + ' · ' + i[:8],
                                   key=analysis_key, persist_state='session')
    case = normalize_case(job, event_id, analysis_id=analysis_id)
    with st.container(key='control_case_strip', border=True, gap='xsmall'):
        with st.container(horizontal=True, vertical_alignment='center'):
            st.subheader(case_title(case), width='stretch')
            st.badge(case['status_label'], color=status_color(case['status']))
            st.badge(review_text(case), color='orange')
        st.caption('카메라: ' + str(case.get('camera_id') or '미등록') + ' · 위치: '
                   + str(case.get('location') or '미등록') + ' · 영상 사건 시각: '
                   + str(case.get('incident_at') or '미확인') + ' · 접수 ' + time_text(job.get('submitted_at')))
        if case.get('type_labels'):
            st.caption('VLM 관찰 유형: ' + ' · '.join(case['type_labels']) + ' · 한 후보의 복수 태그 / 접수시각 ≠ 사고 발생시각')
    left, right = st.columns([66, 34], gap='small', wrap=True)
    with left:
        with st.container(border=True, key='control_media', gap='xsmall'):
            st.subheader('영상 근거')
            tabs = st.tabs([m[1] for m in MEDIA], key='control_video_' + job['worker_job_id'] + '_' + str(event_id), on_change='rerun')
            for tab, (kind, label, caption) in zip(tabs, MEDIA):
                if tab.open:
                    with tab:
                        st.caption(caption)
                        if event_id is None and kind != 'original':
                            st.info('저장된 영상 후보 근거 없음')
                        elif kind == 'frame':
                            show_annotation(job, event_id, mode)
                        else:
                            show_media(job, event_id, kind, label, mode)
            evidence = next((e for e in job.get('evidence_events', []) if e.get('event_id') == event_id), {})
            start, end = evidence.get('source_start_s'), evidence.get('source_end_s')
            timeline = []
            if start is not None and end is not None:
                timeline.append(f'후보 클립: 원본 {start:g}–{end:g}초 · 클립 0초 = 원본 {start:g}초')
            if evidence.get('candidate_time_s') is not None:
                timeline.append(f"모델 후보 {evidence['candidate_time_s']:g}초 (충돌 정답 시각 아님)")
            if evidence.get('pre_context_truncated'):
                timeline.append('영상 시작 경계로 이전 구간 단축')
            if timeline:
                st.caption(' · '.join(timeline))
        with st.container(border=True, key='control_observations', gap='xsmall'):
            render_observations(case, compact=True)
    with right:
        with st.container(border=True, key='control_checks', gap='xsmall'):
            render_checks(case, compact=True)
        with st.container(border=True, key='control_agencies', gap='xsmall'):
            render_agencies(case, compact=True)
    with st.container(border=True, key='control_report', gap='small'):
        render_report(case)
    with st.container(border=True, key='control_observations_full', gap='xsmall'):
        render_observations(case)
    with st.expander('처리 기록 · 저장 식별 정보'):
        st.caption('작업 ID: ' + job['worker_job_id'])
        st.json({'input_sha256': job.get('input_sha256'), 'GPU 제출 기록': job.get('post_attempts'),
                 'VLM 호출 기록': job.get('scene_calls'), 'GPU 처리 기록': (job.get('metrics') or {}).get('timings'),
                 '보고 ID': [r.get('report_id') for r in job.get('reports', [])]})


default_mode = os.environ.get('CCTV_UI_DATA_MODE') or ('api' if os.environ.get('CCTV_API_URL') else 'saved')
with st.container(key='control_shell', gap='small'):
    with st.container(horizontal=True, vertical_alignment='center', key='control_header'):
        st.title('CCTV 사고 관제', width='stretch')
        mode = st.selectbox('조회 자료', ['saved', 'api'], index=1 if default_mode=='api' else 0,
                            format_func=lambda m: '저장 확인 기록' if m=='saved' else '운영 API 저장 결과',
                            key='control_source', label_visibility='collapsed', width=180)
        if st.button('조회 새로고침', icon=':material/refresh:', key='control_refresh'):
            api_snapshot.clear()
            original_bytes.clear()
            evidence_bytes.clear()
    try:
        snapshot = api_snapshot(os.environ.get('CCTV_API_URL', '')) if mode == 'api' else load_saved_snapshot()
    except Exception as exc:
        st.error('선택한 자료 조회 실패: ' + str(exc))
        st.info('다른 자료로 자동 전환하지 않습니다. 저장 확인 기록과 현재 운영 결과는 조회 범위가 다릅니다.')
        st.stop()
    metadata = snapshot.get('metadata') or {}
    scope = 'saved' if mode == 'saved' else 'api:' + os.environ.get('CCTV_API_URL', '')
    with st.container(key='control_scope', gap=None):
        st.caption(('저장 스냅샷 · 자동 갱신 없음' if mode == 'saved' else '운영 저장 결과 · GET 조회/30초 캐시 · 버튼으로 갱신')
                   + ' · 조회 기록: ' + time_text(metadata.get('captured_at') or metadata.get('recorded_at_utc'))
                   + ' · 집계 범위: ' + str(metadata.get('scope') or '선택한 자료의 고유 작업 전체'))
    alert_banner(snapshot, scope)
    selected = st.session_state.control_selected_job
    job = next((j for j in snapshot['jobs'] if j['worker_job_id'] == selected), None)
    if job is not None:
        with st.container(key='control_detail_shell'):
            rail, body = st.columns([1, 5], gap='small')
            with rail:
                monitor(snapshot, mode, scope, selected)
            with body:
                detail(job, mode)
    else:
        if selected:
            st.warning('선택한 작업이 현재 조회 범위에 없습니다.')
        with st.container(key='control_gallery', gap='small'):
            gallery(snapshot, mode, scope)

if st.session_state.pop('control_scroll_to_top', False):
    # Static presentation-only script: no stored/user text enters JavaScript.
    # Native reruns retain the old page offset after an off-screen card click.
    navigation = int(st.session_state.control_navigation)
    st.html('''<script>/* navigation NAVIGATION */ requestAnimationFrame(() => {
        document.querySelector('[data-testid="stMain"]')?.scrollTo({top: 0, behavior: 'instant'});
    });</script>'''.replace('NAVIGATION', str(navigation)), unsafe_allow_javascript=True)
