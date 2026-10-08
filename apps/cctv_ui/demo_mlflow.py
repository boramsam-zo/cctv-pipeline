"""MLflow evidence inside the demo URL; tracking administration stays private."""
import os
from urllib.parse import urlencode

import streamlit as st

from apps.cctv_ui.client import request


@st.cache_data(ttl=10, max_entries=64, show_spinner=False)
def load(path, endpoint):
    # endpoint distinguishes deployments without ever being sent to the browser.
    return request(path)


def show_content(value):
    """Use text/JSON, not executable HTML or Markdown media supplied by a model."""
    if isinstance(value, str):
        st.code(value, language=None, wrap_lines=True)
    elif value is not None:
        st.json(value, expanded=2)
    else:
        st.caption('미기록')


def show_messages(value):
    messages = (value or {}).get('messages', [])
    for message in messages:
        st.caption({'system': '시스템 지시', 'user': '사용자 요청', 'assistant': '응답'}.get(
            message.get('role'), message.get('role', '요청')))
        for part in message.get('content', []):
            if isinstance(part, dict) and part.get('text'):
                st.code(part['text'], language=None, wrap_lines=True)
    if not messages:
        st.info('이 Trace에는 자연어 요청 본문이 없습니다. 아래 저장 자료에서 과거 보완 기록을 확인하세요.')


st.title('모델·실행·설명 기록')
st.caption('MLflow에 저장된 기록을 조회합니다. 이 화면에서는 모델 실행·설정 변경·기록 삭제를 하지 않습니다.')
endpoint = os.environ.get('CCTV_API_URL', '')
if st.button('기록 새로고침', icon=':material/refresh:', key='demo_mlflow_refresh'):
    load.clear()
view = st.segmented_control('확인할 기록', ['실행 기록', '등록 모델'], default='실행 기록',
                            key='demo_mlflow_view')
try:
    if view == '등록 모델':
        payload = load('/mlflow/models', endpoint)
        rows = payload.get('models', [])
        if not rows:
            st.info('공유할 등록 모델 기록이 없습니다.')
        else:
            st.dataframe([{'모델': r.get('model_id'), '학습 버전': r.get('model_version'),
                '등록 이름': r.get('name'), 'Registry 버전': r.get('version'),
                '상태': r.get('status'), '가중치 SHA-256': r.get('weights_sha256'),
                '입력 계약 SHA-256': r.get('contract_sha256')} for r in rows], hide_index=True)
            st.caption('등록 버전은 사용할 가중치와 입력 설정을 식별합니다. 등록 자체가 정확도를 보장하지 않습니다.')
        if payload.get('truncated'):
            st.caption('표시 한도를 넘어 일부 등록 버전이 생략되었습니다.')
    else:
        with st.form('demo_mlflow_filter', border=False):
            st.text_input('작업 ID로 찾기 (비우면 최근 실행)', key='demo_mlflow_job_input',
                          help='카드의 전체 worker_job_id를 사용합니다. 같은 영상의 다른 모델·재실행은 별도 Run입니다.')
            if st.form_submit_button('조회'):
                st.session_state['demo_mlflow_job'] = st.session_state['demo_mlflow_job_input'].strip()
        job = st.session_state.get('demo_mlflow_job', '')
        payload = load('/mlflow/runs' + ('?' + urlencode({'worker_job_id': job}) if job else ''), endpoint)
        rows = payload.get('runs', [])
        if not rows:
            st.info('조건에 맞는 실행 기록이 없습니다. 실행 직후에는 기록 저장이 끝난 뒤 새로고침하세요.')
            st.stop()
        names = {r['run_id']: f"{r.get('name') or r['run_id'][:8]} · {r.get('status', '미기록')}" for r in rows}
        rid = st.selectbox('확인할 실행', list(names), format_func=names.get, key='demo_mlflow_run')
        run = load('/mlflow/runs/' + rid, endpoint)
        st.subheader(run.get('name') or rid)
        st.caption('Run ID: ' + rid)
        st.dataframe([{'모델': run.get('model_id'), '버전': run.get('model_version'),
            '실행 상태': run.get('status'), '처리 결과': run.get('outcome'),
            '목적': run.get('purpose'), '작업 ID': run.get('worker_job_id'),
            '영상 SHA-256': run.get('input_sha256')}], hide_index=True)
        if run.get('evaluation_scope') == 'development_validation':
            st.warning('개발 검증 성적입니다. 모델·epoch 선택에 사용한 자료이므로 독립 최종평가나 지금 영상의 정확도가 아닙니다.')
        else:
            st.caption('추론 실행의 성공 여부와 모델 정확도는 다릅니다. 정답 기반 평가 기록이 없으면 정확도·F1을 표시하지 않습니다.')
        st.caption(run.get('duration_note', 'Duration은 Run 경과시간입니다.'))
        if run.get('metrics'):
            st.dataframe([{'지표': k, '측정값': v} for k, v in run['metrics'].items()], hide_index=True)
        else:
            st.info('저장된 수치 지표가 없습니다.')
        if run.get('description'):
            with st.expander('이 실행의 목적·시간 범위·평가 조건'):
                show_content(run['description'])
        with st.expander('모델·입력 설정과 실행 출처'):
            show_content(run.get('params'))
            show_content(run.get('tags'))
        traces = run.get('trace_ids', [])
        if traces:
            trace_id = st.selectbox('단계별 Trace', traces, key='demo_mlflow_trace')
            trace = load('/mlflow/traces/' + trace_id + '?' + urlencode({'run_id': rid}), endpoint)
            spans = trace.get('spans', [])
            st.dataframe([{'단계': s.get('name'), '종류': s.get('kind'), '상태': s.get('status'),
                '시간(초)': s.get('duration_seconds'),
                '입력 토큰': (s.get('token_usage') or {}).get('input_tokens'),
                '출력 토큰': (s.get('token_usage') or {}).get('output_tokens'),
                '전체 토큰': (s.get('token_usage') or {}).get('total_tokens')} for s in spans], hide_index=True)
            st.caption('빈 토큰 값은 미기록입니다. 저장 응답을 재사용한 단계에 원래 호출 토큰을 중복 합산하지 않습니다.')
            if spans:
                index = st.selectbox('요청·결과를 볼 단계', list(range(len(spans))),
                    format_func=lambda n: spans[n].get('name', str(n)), key='demo_mlflow_span')
                span = spans[index]
                inputs, outputs = span.get('inputs') or {}, span.get('outputs') or {}
                if span.get('kind') == 'LLM':
                    st.subheader('실제 VLM 요청')
                    show_messages(inputs.get('request'))
                    st.subheader('실제 VLM 응답')
                    response = outputs.get('response') or {}
                    for candidate in response.get('candidates', []):
                        message = candidate.get('message') or {}
                        for part in message.get('content', []):
                            if isinstance(part, dict) and part.get('text'):
                                st.code(part['text'], language=None, wrap_lines=True)
                    if not response:
                        st.info('응답 본문이 미기록이거나 요청이 완료되지 않았습니다.')
                elif 'rag' in str(span.get('name', '')).lower():
                    st.subheader('RAG 질의·문서 근거')
                    show_content(outputs.get('queries'))
                    show_content(outputs.get('sources'))
                    if outputs.get('report_markdown'):
                        st.subheader('저장 보고서')
                        show_content(outputs['report_markdown'])
                    st.caption('현재 보고 생성은 CPU 검색·템플릿 방식입니다. 별도 보고 LLM 토큰은 발생하지 않습니다.')
                else:
                    st.subheader('단계 입력·결과')
                    show_content(inputs)
                    show_content(outputs)
                with st.expander('선택 단계의 전체 기록'):
                    show_content(span)
        else:
            st.info('연결된 Trace가 없습니다. 과거 자료 또는 개발평가 Run은 아래 저장 자료를 확인하세요.')
        artifacts = run.get('artifacts', [])
        if artifacts:
            st.subheader('저장 자료 · 과거 본문과 평가 출처')
            paths = {a['path']: a for a in artifacts}
            selected = st.selectbox('열어 볼 자료', [''] + list(paths),
                format_func=lambda x: x or '자료 선택', key='demo_mlflow_artifact')
            if selected:
                if not paths[selected].get('readable'):
                    st.warning('이 자료는 공유 조회 크기 상한을 넘었거나 크기가 미기록입니다.')
                else:
                    artifact = load('/mlflow/runs/' + rid + '/artifact?' + urlencode({'path': selected}), endpoint)
                    st.caption(artifact.get('projection_note', '저장 자료 조회'))
                    show_content(artifact.get('content'))
        if run.get('artifacts_truncated') or payload.get('truncated'):
            st.caption('조회 한도를 넘어 일부 과거 기록이 생략되었습니다.')
except (RuntimeError, ValueError, KeyError, TypeError):
    st.warning('MLflow 기록을 현재 조회할 수 없습니다. 서버 연결 또는 시연 활성 상태를 확인한 뒤 새로고침하세요.')
