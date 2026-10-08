"""Dedicated result-sharing entry point; analysis is never a registered page."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st

from apps.cctv_ui.client import is_read_only

if not is_read_only() or os.environ.get('CCTV_API_URL') != 'http://share-gateway:8081':
    st.error('공유 화면의 조회 전용 서버 설정을 확인해 주세요.')
    st.stop()

# A forged session/query value cannot add the operating analysis page.
st.session_state['cctv_navigation_enabled'] = False
page = st.navigation([
    st.Page('control_room.py', title='CCTV 결과 공유', icon=':material/videocam:', default=True),
], position='hidden')
page.run()
