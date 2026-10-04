"""Read-only control-room panels for existing scene observations and reports.

The panels render saved text. They do not infer a new observation, regenerate a
report, contact an agency, or write an operator decision.
"""
import json

import streamlit as st

from .report_view import TEAM_REPORT_SCHEMA, display_markdown, source_link


STATE_LABELS = {
    'observed': '관찰됨',
    'not_observed': '가시 구간에서 관찰되지 않음',
    'unknown': '판단 불가',
}
FIELD_LABELS = {
    'fire': '불꽃', 'smoke': '연기', 'debris': '파편',
    'cargo_spill': '적재물 유출', 'lane_blocked': '차로 점유',
    'forest_fire_spread': '산림 화재 확산',
    'secondary_collision': '2차 충돌',
    'dangerous_goods_mark': '위험물 표지',
    'person_on_road': '도로 위 사람', 'rider_separated': '탑승자 이탈',
    'visible_entrapment': '차체 끼임',
}
CORPUS_LABELS = {
    'law': '법률', 'statute': '법률', 'enforcement_decree': '시행령',
    'enforcement_rule': '시행규칙', 'administrative_rule': '행정규칙',
    'manual': '매뉴얼', 'guideline': '지침',
}
ROLE_LABELS = {
    'official_source_text': '공식 원문',
    'source_based_summary': '제공자가 작성한 비원문 요약',
}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _items(value):
    return value if isinstance(value, list) else []


def _scene(case):
    scene = _mapping(case.get('scene'))
    if scene:
        return scene
    payload = _mapping(_mapping(case.get('description')).get('payload'))
    return _mapping(payload.get('team_event') or payload.get('description'))


def report_result(case):
    """Return the selected saved result, rejecting a crossed team-report link.

    Required identities mirror the existing report viewer. Additional stored
    job, event, provider and scene hashes are checked whenever both sides are
    available, so absent legacy metadata is not invented.
    """
    report = _mapping(case.get('report'))
    result = _mapping(_mapping(report.get('state')).get('result'))
    if not result or result.get('schema_version') != TEAM_REPORT_SCHEMA:
        return result
    settings = _mapping(report.get('settings'))
    description = _mapping(case.get('description'))
    job = _mapping(case.get('job'))
    scene = _scene(case)
    report_json = _mapping(result.get('report_json'))
    required = (
        (result.get('report_id'), report.get('report_id')),
        (result.get('analysis_id'), settings.get('analysis_id')),
    )
    if any(not left or not right or str(left) != str(right)
           for left, right in required):
        raise ValueError('stored_report_identity_mismatch')
    optional = (
        (result.get('analysis_id'), description.get('analysis_id')),
        (result.get('worker_job_id'), job.get('worker_job_id')),
        (result.get('worker_job_id'), settings.get('worker_job_id')),
        (result.get('provider_job_id'), job.get('provider_job_id')),
        (result.get('event_id'), description.get('event_id')),
        (report_json.get('event_id'), scene.get('event_id')),
        (_mapping(report_json.get('input')).get('event_id'), scene.get('event_id')),
        (result.get('scene_sha256'), description.get('result_sha256')),
        (result.get('scene_sha256'), settings.get('scene_sha256')),
    )
    if any(left is not None and right is not None and str(left) != str(right)
           for left, right in optional):
        raise ValueError('stored_report_identity_mismatch')
    return result


def _checked_result(case):
    try:
        return report_result(case)
    except ValueError:
        st.error('영상 관찰과 보고의 사건·분석·보고 식별 정보가 일치하지 않습니다. 저장 결과를 확인해 주세요.')
        return None


def _render_compact_observations(scene):
    """Keep the saved summary verbatim; detailed evidence remains below."""
    with st.container(key='control_observations_compact', gap='xsmall'):
        st.subheader('VLM 관찰 요약', anchor=False)
        if not scene:
            st.caption('저장된 VLM 관찰 없음 · 미실행·실패를 정상 판정으로 바꾸지 않습니다.')
            return
        summary = scene.get('description') or scene.get('summary')
        if isinstance(summary, str) and summary:
            st.text(summary, width='stretch')
        else:
            st.caption('저장된 관찰 요약 없음 · 아래 관찰 원문에서 개별 근거를 확인하세요.')
        with st.container(key='control_observation_tags', horizontal=True, gap='xsmall'):
            for group in ('hazards', 'people'):
                for field, value in _mapping(scene.get(group)).items():
                    state = _mapping(value).get('state')
                    if state in ('observed', 'unknown'):
                        st.badge(FIELD_LABELS.get(field, field) + ' · ' + STATE_LABELS[state],
                                 color='orange' if state == 'observed' else 'gray')
        st.caption('저장된 모델 관찰 · 운영자 확인 전 · 전체 항목은 아래 관찰 원문')


def render_observations(case, compact=False):
    """Show saved VLM text, optionally using an upper-area summary panel."""
    if compact:
        _render_compact_observations(_scene(case))
        return
    st.subheader('VLM 관찰', anchor=False)
    scene = _scene(case)
    if not scene:
        st.caption('저장된 VLM 관찰이 없습니다. 미실행·실패를 정상 판정으로 바꾸지 않습니다.')
        return
    summary = scene.get('description') or scene.get('summary')
    if isinstance(summary, str) and summary:
        st.text(summary, width='stretch')
    st.caption('저장된 모델 관찰 · 운영자 확인 전 · 가시 구간의 미관찰은 실제 부재와 다릅니다.')
    if scene.get('accident_suspicion'):
        st.text('저장 사고 의심: ' + str(scene['accident_suspicion']), width='stretch')
    if scene.get('incident_types'):
        st.text('저장 유형 태그: ' + ', '.join(map(str, _items(scene['incident_types']))), width='stretch')
    observations = _items(scene.get('evidence') or scene.get('observations'))
    for row in observations:
        if isinstance(row, dict):
            text = row.get('observation') or row.get('description') or row.get('text')
            identifier = row.get('id') or row.get('evidence_id')
            if text:
                st.text((f'[{identifier}] ' if identifier else '') + str(text), width='stretch')
                times = _items(row.get('source_times_seconds'))
                if times:
                    st.text('원본 영상 시각(초): ' + ', '.join(map(str, times)), width='stretch')
        elif isinstance(row, str):
            st.text(row, width='stretch')
    states = []
    for group in ('hazards', 'people'):
        for field, value in _mapping(scene.get(group)).items():
            state = _mapping(value).get('state')
            if state:
                states.append(FIELD_LABELS.get(field, field) + ': ' + STATE_LABELS.get(state, str(state)))
    if states:
        st.caption('영상 관찰 상태')
        st.text(' · '.join(states), width='stretch')
    for uncertainty in _items(scene.get('uncertainties')):
        st.text('미확인: ' + str(uncertainty), width='stretch')


def render_checks(case, compact=False):
    """Keep the saved verification list visible in the detail's upper area."""
    if compact:
        with st.container(key='control_checks_compact', gap='xsmall'):
            st.subheader('먼저 확인할 사항', anchor=False)
            result = _checked_result(case)
            if result is None:
                return
            payload = _mapping(result.get('report_json'))
            draft = _mapping(result.get('draft'))
            checks = _items(payload.get('verification_needed') or draft.get('followup'))
            if not checks:
                st.caption('저장된 확인 사항 없음 · 확인 완료로 간주하지 않습니다.')
                return
            for index, item in enumerate(checks, 1):
                with st.container(key=f'control_check_{index}', gap=None):
                    st.text(f'{index:02d}  {item}', width='stretch')
        return
    st.subheader('먼저 확인할 사항', anchor=False)
    result = _checked_result(case)
    if result is None:
        return
    payload = _mapping(result.get('report_json'))
    draft = _mapping(result.get('draft'))
    checks = _items(payload.get('verification_needed') or draft.get('followup'))
    if not checks:
        st.caption('저장 보고에 확인 사항이 없습니다. 확인 완료로 간주하지 않습니다.')
        return
    st.caption('저장 보고에서 요구한 확인 사항 · 원본 영상과 현장에서 확인해 주세요.')
    for index, item in enumerate(checks, 1):
        st.text(f'{index}. {item}', width='stretch')


def _agency_reference_ids(row):
    actions = _items(row.get('field_response_reference') or row.get('actions'))
    return list(dict.fromkeys(str(identifier) for action in actions
        if isinstance(action, dict) for identifier in _items(action.get('reference_ids'))))


def _render_compact_agencies(case):
    with st.container(key='control_agencies_compact', gap='xsmall'):
        st.subheader('기관별 대응 근거', anchor=False)
        result = _checked_result(case)
        if result is None:
            return
        responses = _items(_mapping(result.get('report_json')).get('agency_responses'))
        if not responses:
            st.caption('저장된 기관별 대응 근거 없음 · 구체 조치를 생성하지 않습니다.')
            return
        st.caption('저장 보고 초안 · 실제 연락·전송 기록 아님')
        for index, row in enumerate(responses, 1):
            if not isinstance(row, dict):
                continue
            with st.container(key=f'control_agency_{index}', gap=None):
                st.text(str(row.get('agency', '기관 미기록')) + ' · '
                        + str(row.get('notification', '조건 미기록')), width='stretch')
                if row.get('reason'):
                    st.text(str(row['reason']), width='stretch')
                reference_ids = _agency_reference_ids(row)
                if reference_ids:
                    st.text('문서 근거 ' + ' '.join('[' + identifier + ']' for identifier in reference_ids),
                            width='stretch')
                else:
                    st.text(str(row.get('note') or '현장 대응 근거 미확인 · 구체 조치 생성 없음'),
                            width='stretch')


def render_agencies(case, compact=False):
    """Show agency-specific saved reasons, conditions and reference coverage."""
    if compact:
        _render_compact_agencies(case)
        return
    st.subheader('기관별 대응 근거', anchor=False)
    result = _checked_result(case)
    if result is None:
        return
    responses = _items(_mapping(result.get('report_json')).get('agency_responses'))
    if not responses:
        st.caption('저장된 기관별 대응 근거가 없습니다. 기관 전송·구체 조치를 생성하지 않습니다.')
        return
    st.caption('문서에 근거한 보고 초안 · 실제 연락·전송 기록이 아닙니다.')
    for row in responses:
        if not isinstance(row, dict):
            continue
        with st.container(border=True, gap='xsmall'):
            st.text(str(row.get('agency', '기관 미기록')) + ' · ' + str(row.get('notification', '조건 미기록')),
                    width='stretch')
            if row.get('reason'):
                st.text('이유: ' + str(row['reason']), width='stretch')
            handoff = _mapping(row.get('control_handoff'))
            if handoff.get('request'):
                st.text('확인 요청: ' + str(handoff['request']), width='stretch')
            reference_ids = _agency_reference_ids(row)
            if reference_ids:
                st.text('현장 대응 참고: ' + ' '.join('[' + identifier + ']' for identifier in reference_ids),
                        width='stretch')
                st.caption('아래 보고서의 현장 대응 참고와 같은 번호의 문서 근거를 함께 확인하세요.')
            else:
                st.warning(str(row.get('note') or '현장 대응 근거 미확인. 구체 조치 생성 없음.'))
            contact = _mapping(row.get('contact'))
            if contact.get('channel'):
                st.text('연락 경로: ' + str(contact['channel']), width='stretch')
                if not contact.get('address'):
                    st.caption('관할 연락처 미등록')


def _reference_metadata(reference):
    identifier = str(reference.get('reference_id') or reference.get('citation_id') or '번호 미기록')
    st.text('[' + identifier + '] ' + str(reference.get('title', '문서 제목 미기록')), width='stretch')
    kind, role = reference.get('corpus_kind'), reference.get('content_role')
    st.text('문서 분류: ' + CORPUS_LABELS.get(kind, str(kind or '미기록'))
            + ' · 본문 성격: ' + ROLE_LABELS.get(role, str(role or '미기록')), width='stretch')
    actors = _items(reference.get('actors'))
    if actors:
        st.text('원문 조치 주체: ' + ', '.join(map(str, actors)), width='stretch')
    conditions = _items(reference.get('application_conditions'))
    if conditions:
        st.text('적용 조건: ' + ' / '.join(map(str, conditions)), width='stretch')
    if reference.get('source_version'):
        st.text('저장 문서 판: ' + str(reference['source_version']), width='stretch')
    if reference.get('verification_status'):
        st.text('출처 확인 기록: ' + str(reference['verification_status']), width='stretch')
    if reference.get('report_use') in ('accident_type_reference', 'accident_type_definition'):
        st.caption('사고유형 정의 참고 · 현장 대응 지침으로 사용하지 않음')
    url = source_link(reference.get('source_url'))
    if url:
        st.link_button('출처 ' + identifier, url, icon=':material/open_in_new:', wrap=True)
    else:
        st.caption('출처 URL 미확인')


def _render_legacy_report(saved):
    draft = _mapping(saved.get('draft'))
    st.caption('기존 경로의 저장 보고 · 기존 보고 형식과 기준일을 보존합니다.')
    if draft.get('document_coverage') in ('insufficient', 'no_documents'):
        st.warning('관련 문서 근거 부족 — 처리 완료가 법적 판단의 충분성을 뜻하지 않습니다.')
    if draft.get('scene_summary'):
        st.text(draft['scene_summary'], width='stretch')
    if draft.get('notice'):
        st.text(draft['notice'], width='stretch')
    documents = {row.get('citation_id'): row for row in _items(saved.get('documents')) if isinstance(row, dict)}
    for finding in _items(draft.get('document_findings')):
        if isinstance(finding, dict):
            st.text('[' + str(finding.get('citation_id', '')) + '] '
                    + str(finding.get('interpretation', '')), width='stretch')
            st.text('인용: ' + str(finding.get('quote', '')), width='stretch')
    for label, field in [('한계', 'limitations'), ('확인할 사항', 'followup')]:
        values = _items(draft.get(field))
        if values:
            st.text(label + '\n' + '\n'.join(map(str, values)), width='stretch')
    st.subheader('문서 근거', anchor=False)
    if saved.get('reference_date'):
        st.text('문서 검색 기준일: ' + str(saved['reference_date']) + ' · 사고 발생일 미확인', width='stretch')
    for document in documents.values():
        with st.container(border=True):
            metadata = _mapping(document.get('metadata'))
            st.text('[' + str(document.get('citation_id', '')) + '] '
                    + str(document.get('content', '')), width='stretch')
            url = source_link(metadata.get('source_url'))
            if url:
                st.link_button('출처 ' + str(document.get('citation_id', '')), url, wrap=True)
            else:
                st.caption('출처 URL 미확인')


def render_report(case):
    """Present only the selected stored report; exports keep its raw Markdown."""
    from .control_report import report_actions, report_html

    saved = _checked_result(case)
    if saved is None:
        st.header('보고서와 문서 근거', anchor=False)
        return
    report = _mapping(case.get('report'))
    state = _mapping(report.get('state'))
    stored = state.get('status') == 'report_stored' and bool(saved)
    report_id = str(report.get('report_id') or '미기록')
    worker_id = str(_mapping(case.get('job')).get('worker_job_id') or '')
    markdown = saved.get('report_markdown') if stored else None
    reason = ('기존 형식에는 저장된 Markdown 원문이 없습니다. 아래의 기존 보고 형식을 유지합니다.'
              if stored else '보고 미생성 · 복사·저장할 Markdown 원문이 없습니다.')
    if stored and saved.get('schema_version') == TEAM_REPORT_SCHEMA:
        reason = '저장 보고 원문이 없습니다. 보고서 6개 절을 생성하거나 보완하지 않았습니다.'
    report_actions(markdown, worker_id, report_id, reason)
    if not stored:
        st.caption('저장 보고 없음 · 보고 단계: ' + str(state.get('status') or '미실행'))
        return
    st.text('보고 ID: ' + report_id, width='stretch')
    if saved.get('schema_version') == TEAM_REPORT_SCHEMA:
        st.caption('동료 RAG · CPU 문서 검색·템플릿 보고 · 운영자 검수가 필요한 초안')
        st.caption('문서 검색 기준일 필터 미지원 · 날짜에 따른 문서 제외는 적용되지 않았습니다.')
        if not isinstance(markdown, str) or not markdown.strip():
            st.error(reason)
            return
        presentation = report_html(markdown, worker_id, report_id,
                                   _items(_mapping(saved.get('report_json')).get('references')))
        if presentation:
            st.caption('저장 제목: ' + markdown.splitlines()[0].removeprefix('# '))
            st.html(presentation)
        else:
            st.warning('저장 보고의 절 구성이 달라 원문 형식으로 표시합니다. 내용을 재구성하지 않았습니다.')
            st.markdown(display_markdown(markdown), unsafe_allow_html=False)
    else:
        _render_legacy_report(saved)
    st.download_button('저장 보고 JSON 저장', json.dumps(saved, ensure_ascii=False, indent=2),
        file_name='report-' + report_id + '.json', mime='application/json',
        key='control_report_json_' + report_id, on_click='ignore', icon=':material/download:')
