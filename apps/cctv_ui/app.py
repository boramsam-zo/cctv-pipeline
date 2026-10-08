"""One app for the control room and the established analysis submission form."""
import os
import streamlit as st

st.session_state['cctv_navigation_enabled'] = True
analysis_default = os.environ.get('CCTV_UI_VIEW') == 'analysis'
page = st.navigation([
    st.Page('control_room.py', title='CCTV 사고 관제', icon=':material/videocam:',
            default=not analysis_default),
    st.Page('analysis_app.py', title='새 영상 분석', icon=':material/upload_file:',
            url_path='analysis', default=analysis_default),
], position='hidden')
page.run()