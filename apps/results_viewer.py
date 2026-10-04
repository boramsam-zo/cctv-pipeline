"""Read-only local Streamlit viewer. Importing it does not start a server."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from boram3_cctv.results_viewer import ArtifactReader, PAGES, ViewerError, load_view


def render(st, root=ROOT):
    st.set_page_config(page_title='CCTV 결과 확인', layout='wide')
    st.title('CCTV 결과 확인')
    st.caption('저장된 결과만 읽습니다. 화면 조작으로 학습·추론·영상 추출을 실행하지 않습니다.')
    key = st.sidebar.radio('확인할 기록', list(PAGES), format_func=PAGES.get, key='view')
    st.sidebar.caption('실제 자료 결과와 시험용 예시는 별도 항목입니다. 화면을 열었다는 사실만으로 사용자 검수 완료를 기록하지 않습니다.')
    try:
        data = load_view(root, key)
    except ViewerError as error:
        st.error('이 기록을 표시할 수 없습니다: ' + str(error))
        st.info('파일 누락·손상·형식 불일치는 정상 판정이나 0점으로 바꾸지 않습니다. 다른 기록은 왼쪽에서 선택할 수 있습니다.')
        return
    st.subheader(data['title'])
    if key == 'events':
        st.warning(data['notice'])
    else:
        st.info(data['notice'])
    st.caption('기록의 성격: ' + data['scope'])
    media = data['media']
    if media:
        selected = st.selectbox('미리보기 선택', list(range(len(media))),
                               format_func=lambda i: media[i]['id'], key='media_' + key)
        item = media[selected]
        try:
            content = ArtifactReader(root).media(item['path'])
            st.caption(item['caption'])
            if item['kind'] == 'video':
                st.video(content, format='video/mp4')
            else:
                st.image(content, width='stretch')
        except ViewerError as error:
            st.error('미리보기를 읽지 못했습니다: ' + str(error))
        with st.expander('모델 입력 또는 참고 영상의 원본 시각'):
            st.dataframe(item['times'], hide_index=True, width='stretch')
    for title, rows in data['tables']:
        st.subheader(title)
        if rows:
            st.dataframe(rows, hide_index=True, width='stretch')
        else:
            st.caption('저장된 행이 없습니다. 정상 영상이라는 뜻은 아닙니다.')
    for chart in data['charts']:
        st.subheader(chart['title'])
        if chart['rows']:
            draw = st.bar_chart if chart.get('kind') == 'bar' else st.line_chart
            draw(chart['rows'], x=chart['x'], y=chart['y'])
            st.caption('저장된 값만 표시합니다. 없는 step·정답·성능 수치는 만들지 않습니다.')
    for title, value in data['details']:
        with st.expander(title, expanded=True):
            st.json(value)
    with st.expander('근거 파일과 보존 확인용 SHA256'):
        st.json(data['source_sha256'])
    st.caption('화면 실행 확인과 사용자 직접 검수는 별개입니다. 이 화면은 검수 기록을 쓰거나 변경하지 않습니다.')


if __name__ == '__main__':
    try:
        import streamlit as st
    except ImportError:
        raise SystemExit('Streamlit이 설치되지 않았습니다. LOCAL_SETUP.md의 별도 뷰어 환경 설치 승인을 먼저 확인하세요.')
    render(st)
