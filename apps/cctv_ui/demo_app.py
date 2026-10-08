"""Finite public demo entrypoint; the gateway enforces admission and closure."""
import os
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st

from apps.cctv_ui import control_demo as demo
from apps.cctv_ui.control_data import unique_jobs
from apps.cctv_ui.ui_data import demo_status, load_snapshot


def deadline_kst(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return '시간대 확인 필요'
        return stamp.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S KST')
    except (TypeError, ValueError):
        return '미확인'


st.set_page_config(page_title='CCTV 실제 분석 시연', page_icon=':material/videocam:', layout='wide')
if os.environ.get('CCTV_DEMO_MODE') != '1':
    st.error('시연 서버 설정을 확인해 주세요.')
    st.stop()
st.session_state['cctv_navigation_enabled'] = True
st.session_state['demo_live_panel_enabled'] = True
page = st.navigation([
    st.Page('control_room.py', title='영상·보고 관제', icon=':material/videocam:', default=True),
    st.Page('analysis_app.py', title='새 영상 분석', icon=':material/upload_file:', url_path='analysis'),
    st.Page('demo_mlflow.py', title='MLflow 실행 기록', icon=':material/monitoring:', url_path='mlflow'),
], position='hidden')
# The established control-room CSS intentionally hides Streamlit's header.
# Keep shared navigation in the main content so all three views remain reachable.
with st.container(horizontal=True, border=True, key='demo_navigation', gap='medium'):
    st.page_link('control_room.py', label='영상·보고 관제', icon=':material/videocam:')
    st.page_link('analysis_app.py', label='새 영상 분석', icon=':material/upload_file:')
    st.page_link('demo_mlflow.py', label='MLflow 실행 기록', icon=':material/monitoring:')


def open_live_case(target):
    st.session_state.control_selected_job = target['job_id']
    st.session_state.control_navigation = st.session_state.get('control_navigation', 0) + 1
    st.session_state['control_event_' + target['job_id']] = target['event_id']
    st.session_state['control_analysis_' + target['job_id'] + '_' + target['event_id']] = target['analysis_id']
    st.session_state.control_scroll_to_top = True
    st.switch_page('control_room.py')


@st.fragment(run_every=5)
def demo_live_updates():
    """Only this small progress/notification area refreshes on a timer."""
    try:
        current = demo_status(os.environ.get('CCTV_API_URL', ''))
        st.caption('실제 분석 시연 · 종료 시각: ' + deadline_kst(current.get('expires_at'))
                   + f" · 새 접수 잔여 {current.get('remaining_jobs', 0)}건 · 동시 분석 1건")
        if not current.get('active'):
            st.info('시연이 종료되었습니다. 저장 결과는 보존됩니다.')
            return
        snapshot = load_snapshot()
    except Exception:
        # An outage never triggers a full rerun or fabricates a completion.
        st.warning('진행 상태 연결을 확인 중입니다. 표시 중인 영상·보고는 유지합니다.')
        return
    jobs = unique_jobs(snapshot['jobs'], snapshot['selections'])
    labels = {'queued': '접수 대기', 'prepared': '실행 준비', 'submitting': 'GPU 요청 중',
              'waiting': 'GPU 분석·결과 저장 중', 'handoff_ready': '결과 인계 완료',
              'verifying_media': '영상 근거 확인 중', 'preparing_preview': '재생 영상 준비 중',
              'describing': '장면 설명 생성 중', 'reporting': '보고 생성 중',
              'cpu_retrieval': '문서 근거 검색 중', 'completed': '처리 완료',
              'no_candidates': '후보 없음 · 정상 판정 아님'}
    watched = set(st.session_state.get('demo_watch_jobs', []))
    for job in jobs:
        stage = (job.get('flow') or {}).get('status') or job.get('stage')
        paused = (job.get('flow') or {}).get('paused')
        if job['worker_job_id'] in watched:
            st.caption('내 접수 ' + job['worker_job_id'][:8] + ' · '
                       + ('확인 필요' if paused else labels.get(stage, '상태 확인 중')))
    demo.render_live_notifications(jobs, 'api:' + os.environ.get('CCTV_API_URL', ''), open_live_case)
    displayed = st.session_state.get('cctv_display_revision')
    if displayed and displayed != snapshot['revision']:
        st.info('새 상태·결과가 있습니다. 영상 재생과 보고 열람은 그대로 유지합니다.')
        if st.button('새 결과를 화면에 반영', key='demo_apply_snapshot'):
            # Explicit user action, never a periodic refresh.
            st.rerun()


try:
    status = demo_status(os.environ.get('CCTV_API_URL', ''))
except Exception:
    st.error('시연 상태를 확인할 수 없습니다. 연결이 확인되면 화면을 다시 열어 주세요.')
    st.stop()
if not status.get('active'):
    st.info('시연이 종료되었거나 아직 시작되지 않았습니다. 저장 결과는 보존됩니다.')
    st.stop()
demo_live_updates()
page.run()
