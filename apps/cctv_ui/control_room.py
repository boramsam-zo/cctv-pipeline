"""Read stored CCTV results; navigate to the separate, explicit analysis form."""
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st

from apps.cctv_ui.client import request, fetch_media, is_read_only
from apps.cctv_ui.ui_data import load_snapshot, invalidate_snapshot
from apps.cctv_ui.control_data import (
    load_saved_snapshot, normalize_snapshot, normalize_case, media_reference,
    historical_frame_reference, card_identities, candidate_classification,
    build_video_aggregates, model_identity, TYPE_LABELS,
)
from apps.cctv_ui.control_panels import render_observations, render_agencies, render_report
from apps.cctv_ui import control_demo as demo

st.set_page_config(page_title='CCTV 관제 · 저장 결과', page_icon=':material/videocam:', layout='wide')
st.html(Path(__file__).with_name('control_room.css'))
st.session_state.setdefault('control_selected_job', None)
st.session_state.setdefault('control_demo', None)
READ_ONLY = is_read_only()
DEMO_MODE = os.environ.get('CCTV_DEMO_MODE') == '1'

MEDIA = [('frame', '박스 주석 이미지 · 보조 근거', '박스·궤적이 표시된 정지 이미지 · 동영상과 구분되는 보조 근거'),
         ('original', '원본 영상', '업로드한 전체 원본 · 사건 전후의 전체 흐름'),
         ('clip', '사고 후보 클립', '후보 전후의 전체 화면 동영상')]


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


def public_snapshot(*, force=False):
    payload = load_snapshot(force=force)
    st.session_state['cctv_display_revision'] = payload['revision']
    return normalize_snapshot(payload['jobs'], payload['selections'], metadata={
        'mode': 'api', 'captured_at': payload['captured_at'],
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
    if version in ('B', 'R2', 'LC') and kind != 'original':
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
    if READ_ONLY or DEMO_MODE:
        return
    notice = demo.card_notice(st.session_state.control_demo, job['worker_job_id'], scope)
    if notice:
        st.badge(notice, color='violet', icon=':material/notifications:')


def show_identity_tags(identity):
    with st.container(horizontal=True, gap='xsmall'):
        st.badge(identity['model_label'], color='blue')
        st.badge(identity['run_label'], color='gray',
                 help='현재 조회 범위에서 같은 원본·같은 모델의 접수시각을 비교합니다. 가중치의 새 버전이라는 뜻은 아닙니다.')
    st.caption(identity['source_label'])


def show_result_readiness(case):
    st.caption('분류: ' + case['classification_label'])
    st.caption('설명: ' + case['description_readiness'] + ' · 보고: ' + case['report_readiness'])


def monitor(snapshot, mode, scope, selected):
    identities = card_identities(snapshot['jobs'])
    with st.container(border=True, key='control_monitor', gap='xsmall'):
        st.subheader('CCTV 영상 모니터링')
        stats = snapshot['stats']
        st.caption(f"저장 {len(snapshot['jobs'])}작업 · 요청 처리 완료 {stats['completed_count']} · 작업 확인 필요 {stats['attention_count']}")
        with st.container(height=680, border=False, key='control_monitor_list', gap='small', autoscroll=False):
            for job in sorted(snapshot['jobs'], key=lambda j: j.get('submitted_at') or '', reverse=True):
                case = normalize_case(job)
                identifier = job['worker_job_id']
                active = identifier == selected
                with st.container(border=True, key='control_monitor_card_' + ('selected_' if active else '') + identifier, gap='xsmall'):
                    with st.container(horizontal=True, vertical_alignment='center', gap='xsmall'):
                        st.text('작업 · ' + identifier[:8])
                        st.badge(case['status_label'], color=status_color(case['status']))
                    show_identity_tags(identities[identifier])
                    show_thumbnail(job, case, mode)
                    show_result_readiness(case)
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
    return ('영상 후보' if case.get('event_id') else '영상 작업') + ' · ' + case['worker_job_id'][:8]


def review_text(case):
    review = case.get('human_review')
    if review == 'unreviewed':
        return '사람 검토 전'
    return '사람 검토 기록: ' + str(review) if review else '사람 검토 기록 미연결'


def gallery(snapshot, mode, scope):
    jobs = sorted(snapshot['jobs'], key=lambda j: j.get('submitted_at') or '', reverse=True)
    identities = card_identities(jobs)
    if not READ_ONLY and not DEMO_MODE:
        demo_controls(snapshot, scope)
    with st.container(key='control_statistics'):
        statistics(snapshot)
    with st.container(horizontal=True, vertical_alignment='center', key='control_gallery_toolbar'):
        st.subheader('영상 목록', width='stretch')
        grid = st.segmented_control('목록 배치', ['3×3', '4×4'], default='3×3', required=True,
                                    key='control_grid', width='content', label_visibility='collapsed', persist_state='session')
    st.caption(('원본 첫 프레임 · 정지 미리보기' if mode == 'saved' else '주석 프레임 · 보조 이미지')
               + ' · 슬롯은 배치 수이며 실제 연결 CCTV 수·독립 사고 수가 아닙니다.')
    filters = {'all': '전체', 'candidate': '사고 후보', 'no_candidates': '후보 없음', 'unjudged': '미판정'}
    selected_filter = st.segmented_control('영상 분류', list(filters), default='all', required=True,
        format_func=filters.get, key='control_candidate_filter', persist_state='session')
    jobs = [job for job in jobs if selected_filter == 'all' or candidate_classification(job) == selected_filter]
    st.caption(f'목록 {len(jobs)}작업 · 위 집계는 조회 자료 전체 기준 · 후보 없음은 정상 확정이 아니며, 후보 유무를 확인할 결과가 없으면 미판정으로 구분합니다.')
    if selected_filter == 'unjudged':
        st.info('미판정은 분석 대기·실패·결과 누락 등으로 후보 유무를 확인할 수 없는 작업입니다. 사고가 아닌 영상이라는 뜻이 아닙니다. 이미 사고 후보가 저장된 작업은 이후 보고가 실패해도 사고 후보에 표시됩니다.')
    size = 4 if grid == '4×4' else 3
    capacity = size * size
    pages = max(1, math.ceil(len(jobs) / capacity))
    if st.session_state.get('control_page', 1) > pages:
        st.session_state.control_page = 1
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
                show_identity_tags(identities[job['worker_job_id']])
                st.caption('작업: ' + case['status_label'])
                show_result_readiness(case)
                st.caption(review_text(case))
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
        for name, value in [('고유 작업', len(jobs)), ('요청 처리 완료', complete),
                            ('작업 확인 필요', attention), ('대기·처리 중', len(jobs)-complete-attention)]:
            st.metric(name, value, border=True)
    st.caption(f"고유 보고 {stats['report_count']}개 · 저장 완료 보고 {stats['stored_report_count']}개 · "
               f"영상 후보 {stats['candidate_event_count']}개 · 후보는 작업/장면 기준, 확정 사고 수 아님 · 접수시각은 사고시각과 별개")
    st.caption('요청 처리 완료에는 GPU 비교 완료·후보 없음이 포함됩니다. 설명·보고 저장 여부와 사람 검토 여부는 각 카드에서 별도로 확인합니다.')
    video_statistics(jobs)


def video_statistics(jobs):
    models = {model_identity(job)[0]: model_identity(job)[1] for job in jobs if model_identity(job)[0]}
    if not models:
        st.caption('모델 식별 정보가 없어 영상별 집계를 만들 수 없습니다.')
        return
    selected = st.selectbox('영상 집계 모델', list(models), format_func=models.get,
        index=list(models).index('x3d_s') if 'x3d_s' in models else 0,
        key='control_aggregate_model', persist_state='session')
    aggregate = build_video_aggregates(jobs, selected)
    readiness, distribution = st.columns([3, 2], wrap=True)
    with readiness, st.container(border=True):
        st.markdown('**영상별 설명·보고 준비 현황**')
        rows = [{'원본 SHA': row['input_sha256'][:12], '최근 작업': (row['job_id'] or '미확인')[:8],
                 '작업 상태': row['status'], '설명': row['description'], '보고': row['report'],
                 '분석 작업 수': row['job_count']} for row in aggregate['rows']]
        if rows:
            st.dataframe(rows, hide_index=True, width='stretch')
        else:
            st.caption('원본 SHA가 확인된 영상 없음')
        st.caption('선택 모델의 원본별 최신 작업 기준 · 같은 SHA의 재실행은 한 행 · 카드와 과거 기록은 모두 보존')
    with distribution, st.container(border=True):
        st.markdown('**관찰된 사고 유형 분포**')
        counts = [{'관찰 유형': TYPE_LABELS.get(tag, tag), '원본 수': count}
                  for tag, count in aggregate['type_counts'].items()]
        if counts:
            st.bar_chart(counts, x='관찰 유형', y='원본 수', horizontal=True, height=160)
        else:
            st.caption('유형이 기록된 유효 관찰 없음')
        st.caption(f"고유 원본 {aggregate['source_count']}개 · 관찰 저장 {aggregate['observed_sources']}개 · "
                   f"설명 미완료 {aggregate['incomplete']}개 · 유형 미분류 {aggregate['unclassified']}개 · "
                   f"후보 없음 {aggregate['no_candidates']}개")
        st.caption(f"복수 유형 원본 {aggregate['multiple_types']}개 · 같은 영상은 각 유형에 한 번씩 포함되어 "
                   '유형별 합계가 원본 수보다 클 수 있습니다. 확정 사고 통계가 아닙니다.')
        if aggregate['previous_observations']:
            st.caption(f"최근 작업에 설명이 없어 이전 유효 관찰을 사용한 원본 {aggregate['previous_observations']}개")
    if aggregate['unknown_hash_jobs'] or aggregate['recency_unknown']:
        st.caption(f"집계 제외: 원본 SHA 미확인 {aggregate['unknown_hash_jobs']}작업 · "
                   f"실행 순서 미확인 {aggregate['recency_unknown']}원본")


def detail(job, mode, identity):
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
    if job.get('demo_summary'):
        summary = job['demo_summary']
        st.caption('대표 후보 1개 설명·보고 · 나머지 후보는 영상 근거 확인 · '
                   + f"추가 후보 {summary.get('unexplained_candidates', 0)}개")
    revisions = case['report_revisions']
    if len(revisions) > 1:
        revision_ids = [row['report_id'] for row in revisions]
        by_report = {row['report_id']: row for row in revisions}
        revision_key = 'control_report_revision_' + job['worker_job_id'] + '_' + str(case['analysis_id'])
        if st.session_state.get(revision_key) not in revision_ids:
            st.session_state[revision_key] = revision_ids[0]
        selected_report = st.segmented_control('보고 판', revision_ids, required=True,
            format_func=lambda i: ('관련성 검수본' if by_report[i].get('review') else '최초 생성본')
                + (' · ' + i[:8] if len(revision_ids) > 2 else ''),
            key=revision_key, persist_state='session')
        case = normalize_case(job, event_id, analysis_id=analysis_id, report_id=selected_report)
    if case['report_review_errors']:
        st.warning('일부 보고 검수본의 원본·사건 연결 또는 내용 검증을 확인할 수 없어 표시에서 제외했습니다.')
    if case['report_review']:
        st.caption('저장된 관찰·검색 결과를 재사용 · 새 모델 실행 없음 · 최초 원문 보존'
                   + f" · 관련성 검수로 제외한 근거 {case['report_review']['excluded_count']}개")
    with st.container(key='control_case_strip', border=True, gap='xsmall'):
        with st.container(horizontal=True, vertical_alignment='center'):
            st.subheader(case_title(case), width='stretch')
            st.badge(case['status_label'], color=status_color(case['status']))
            st.badge(review_text(case), color='orange')
        show_identity_tags(identity)
        show_result_readiness(case)
        if case['status'] == 'paused':
            st.caption('이전 작업 중단 기록을 보존합니다. 별도로 저장된 설명·보고는 위 준비 상태와 아래 원문에서 확인할 수 있습니다.')
        st.caption('카메라: ' + str(case.get('camera_id') or '미등록') + ' · 위치: '
                   + str(case.get('location') or '미등록') + ' · 영상 사건 시각: '
                   + str(case.get('incident_at') or '미확인') + ' · 접수 ' + time_text(job.get('submitted_at')))
        if READ_ONLY:
            tracking = job.get('mlflow') or {}
            st.caption('사고 모델: ' + str(tracking.get('model_id') or case.get('version') or '미기록')
                       + ' · ' + str(tracking.get('model_version') or '모델 버전 미기록'))
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
    with right:
        with st.container(border=True, key='control_observations', gap='xsmall'):
            render_observations(case, compact=True)
        with st.container(border=True, key='control_agencies', gap='xsmall'):
            render_agencies(case, compact=True)
    with st.container(border=True, key='control_report', gap='small'):
        render_report(case)
    with st.container(border=True, key='control_observations_full', gap='xsmall'):
        render_observations(case)
    with st.expander('처리 기록 · 저장 식별 정보'):
        st.caption('작업 ID: ' + job['worker_job_id'])
        if DEMO_MODE and st.session_state.get('cctv_navigation_enabled') and st.button(
                '이 작업의 MLflow 기록', key='control_mlflow_' + job['worker_job_id'],
                icon=':material/monitoring:'):
            st.session_state.demo_mlflow_job = job['worker_job_id']
            st.session_state.demo_mlflow_job_input = job['worker_job_id']
            st.switch_page('demo_mlflow.py')
        tracking = job.get('mlflow') or {}
        if tracking:
            st.caption('사고 모델: ' + str(tracking.get('model_id') or '미기록')
                       + ' · ' + str(tracking.get('model_version') or '버전 미기록'))
            labels = {'recorded': '저장 완료', 'pending': '기록 대기 · 저장 결과와 보고는 확인할 수 있습니다.',
                      'in_progress': '분석 종료 후 기록', 'not_submitted': '분석 제출 전 중단 · 실행 기록 없음'}
            tracking_label = '원본 실행 MLflow: ' if case['report_review'] else 'MLflow: '
            st.caption(tracking_label + labels.get(tracking.get('status'), '기록 상태 확인 필요'))
            review_tracking = (case.get('report') or {}).get('mlflow') or {}
            if case['report_review'] and review_tracking:
                st.caption('검수본 MLflow: ' + labels.get(review_tracking.get('status'), '기록 상태 확인 필요'))
        st.json({'input_sha256': job.get('input_sha256'), 'GPU 제출 기록': job.get('post_attempts'),
                 'VLM 호출 기록': job.get('scene_calls'), 'GPU 처리 기록': (job.get('metrics') or {}).get('timings'),
                 '보고 ID': [r.get('report_id') for r in job.get('reports', [])]})


def control_view():
    default_mode = os.environ.get('CCTV_UI_DATA_MODE') or ('api' if os.environ.get('CCTV_API_URL') else 'saved')
    if st.session_state.pop('control_refresh_pending', False):
        api_snapshot.clear()
    with st.container(key='control_shell', gap='small'):
        with st.container(horizontal=True, vertical_alignment='center', key='control_header'):
            st.title('CCTV 사고 관제', width='stretch')
            if not READ_ONLY and st.session_state.get('cctv_navigation_enabled') and st.button(
                    '새 영상 분석', icon=':material/upload_file:', key='control_new_analysis', type='primary'):
                st.switch_page('analysis_app.py')
            if READ_ONLY or DEMO_MODE:
                mode = 'api'
                st.badge('실제 분석 시연' if DEMO_MODE else '조회 전용 공유', icon=':material/visibility:', color='blue')
            else:
                mode = st.selectbox('조회 자료', ['saved', 'api'], index=1 if default_mode=='api' else 0,
                                    format_func=lambda m: '저장 확인 기록' if m=='saved' else '운영 API 저장 결과',
                                    key='control_source', label_visibility='collapsed', width=180, persist_state='session')
            if st.button('조회 새로고침', icon=':material/refresh:', key='control_refresh'):
                api_snapshot.clear()
                invalidate_snapshot()
                original_bytes.clear()
                evidence_bytes.clear()
        try:
            snapshot = (public_snapshot() if READ_ONLY or DEMO_MODE else
                        api_snapshot(os.environ.get('CCTV_API_URL', ''))) if mode == 'api' else load_saved_snapshot()
        except Exception as exc:
            st.error('선택한 자료 조회 실패: ' + str(exc))
            st.info('다른 자료로 자동 전환하지 않습니다. 저장 확인 기록과 현재 운영 결과는 조회 범위가 다릅니다.')
            st.stop()
        metadata = snapshot.get('metadata') or {}
        scope = 'saved' if mode == 'saved' else 'api:' + os.environ.get('CCTV_API_URL', '')
        with st.container(key='control_scope', gap=None):
            st.caption(('저장 스냅샷 · 자동 갱신 없음' if mode == 'saved' else '운영 저장 결과 · 진행·완료 알림 자동 확인 · 본문은 버튼으로 갱신' if DEMO_MODE else '운영 저장 결과 · GET 조회/30초 캐시 · 버튼으로 갱신')
                       + ' · 조회 기록: ' + time_text(metadata.get('captured_at') or metadata.get('recorded_at_utc'))
                       + ' · 집계 범위: ' + str(metadata.get('scope') or '선택한 자료의 고유 작업 전체'))
        if DEMO_MODE and not st.session_state.get('demo_live_panel_enabled'):
            def open_live_case(target):
                choose_job(target['job_id'], target['event_id'], target['analysis_id'])
                st.rerun()
            demo.render_live_notifications(snapshot['jobs'], scope, open_live_case)
        elif not READ_ONLY and not DEMO_MODE:
            alert_banner(snapshot, scope)
        selected = st.session_state.control_selected_job
        job = next((j for j in snapshot['jobs'] if j['worker_job_id'] == selected), None)
        if job is not None:
            with st.container(key='control_detail_shell'):
                rail, body = st.columns([1, 5], gap='small')
                with rail:
                    monitor(snapshot, mode, scope, selected)
                with body:
                    detail(job, mode, card_identities(snapshot['jobs'])[job['worker_job_id']])
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


if not DEMO_MODE:
    control_view = st.fragment(control_view)
control_view()
