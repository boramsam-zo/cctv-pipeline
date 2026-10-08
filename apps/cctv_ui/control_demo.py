"""Stored-event replay and real storage notifications; never calls providers."""
from datetime import datetime, timezone
import time

from apps.cctv_ui.control_data import event_ids, normalize_case, saved_description_cases, unique_jobs
from apps.cctv_ui.control_panels import report_result


def _now():
    return time.time()


def candidates(snapshot):
    """Only offer saved, identity-checked reports with their own scene evidence."""
    result = []
    for job in sorted(snapshot['jobs'], key=lambda j: j.get('submitted_at') or '', reverse=True):
        for event in event_ids(job):
            case = normalize_case(job, event)
            report = case.get('report') or {}
            if not case.get('analysis_id') or (report.get('state') or {}).get('status') != 'report_stored':
                continue
            try:
                if not report_result(case):
                    continue
            except ValueError:
                continue
            result.append({
                'job_id': job['worker_job_id'], 'event_id': event,
                'analysis_id': case['analysis_id'], 'report_id': report['report_id'],
                'title': case['title'], 'version': case['version'],
            })
    return result


def target_key(target):
    return ':'.join(target[k] for k in ('job_id', 'event_id', 'analysis_id', 'report_id'))


def target_available(snapshot, target):
    return any(target_key(item) == target_key(target) for item in candidates(snapshot))


def schedule(state, target, scope, *, now=None):
    # The UI disables the button too; this guard also makes queued double clicks harmless.
    if state and state['status'] == 'pending':
        return state
    now = _now() if now is None else now
    return {'target': dict(target), 'scope': scope, 'status': 'pending',
            'requested_at': now, 'due_at': now + 2.0, 'arrived_at': None, 'read_at': None}


def advance(state, *, now=None):
    now = _now() if now is None else now
    if state and state['status'] == 'pending' and now >= state['due_at']:
        return {**state, 'status': 'unread', 'arrived_at': now}
    return state


def acknowledge(state, *, now=None):
    if not state or state['status'] != 'unread':
        return state
    return {**state, 'status': 'read', 'read_at': _now() if now is None else now}


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def card_notice(state, job_id, scope):
    if not state or state['scope'] != scope or state['target']['job_id'] != job_id:
        return None
    return {'unread': '시연 알림 · 새 알림', 'read': '시연 알림 · 읽음'}.get(state['status'])


def live_description_targets(jobs):
    """Saved reports with matching scene evidence (name kept for UI callers)."""
    targets = {}
    for job in unique_jobs(jobs):
        for case in saved_description_cases(job):
            for report in case['report_revisions']:
                identifier = report.get('report_id')
                if (not isinstance(identifier, str) or not identifier
                        or (report.get('state') or {}).get('status') != 'report_stored'):
                    continue
                selected = {**case, 'report': report}
                try:
                    result = report_result(selected)
                except ValueError:
                    continue
                if not result or result.get('report_id') != identifier:
                    continue
                target = {'job_id': job['worker_job_id'], 'event_id': case['event_id'],
                          'analysis_id': case['analysis_id'], 'report_id': identifier,
                          'report_ready': True, 'report_note': '보고 저장 완료'}
                targets[target_key(target)] = target
    return targets


def observe_saved_descriptions(state, jobs, scope, *, watch_jobs=(), now=None):
    """Baseline existing results, then notify once per newly stored report.

    A just-submitted new job can complete before the first poll. Explicitly
    watched new submissions still notify; reused jobs must not be added there.
    """
    targets = live_description_targets(jobs)
    reset = (state is None or state.get('scope') != scope
             or state.get('trigger') != 'report_stored')
    previous = set() if reset else set(state.get('seen', []))
    notices = [] if reset else [dict(item) for item in state.get('notices', [])]
    watch_jobs = set(watch_jobs)
    for key, target in targets.items():
        if key not in previous and (not reset or target['job_id'] in watch_jobs):
            notices.append({'key': key, **target, 'status': 'unread',
                            'arrived_at': _now() if now is None else now})
    for item in notices:
        target = targets.get(item['key'])
        item['available'] = target is not None
        if target:
            item['report_ready'] = target['report_ready']
            item['report_note'] = target['report_note']
    return {'scope': scope, 'trigger': 'report_stored',
            'seen': sorted(previous | set(targets)), 'notices': notices[-20:]}


def acknowledge_live(state, key):
    if not state:
        return state
    return {**state, 'notices': [{**item, 'status': 'read'} if item['key'] == key else dict(item)
                                 for item in state['notices']]}


def render_live_notifications(jobs, scope, open_case):
    """Session-only read markers; notify after the actual report is saved."""
    import streamlit as st
    st.session_state['demo_live_notifications'] = observe_saved_descriptions(
        st.session_state.get('demo_live_notifications'), jobs, scope,
        watch_jobs=st.session_state.get('demo_watch_jobs', []))
    state = st.session_state['demo_live_notifications']
    for item in reversed(state['notices'][-3:]):
        with st.container(border=True):
            st.success('보고서 저장 완료 · ' + item['job_id'][:8] + ' · ' + item['event_id'])
            st.caption('VLM 설명·보고 조회 가능 · 보고 ' + item['report_id'][:8]
                       + ' · ' + ('새 알림' if item['status'] == 'unread' else '확인한 알림'))
            with st.container(horizontal=True):
                if st.button('같은 사건 열기', key='live_open_' + item['key'],
                             disabled=not item['available'], icon=':material/open_in_new:'):
                    st.session_state['control_report_revision_' + item['job_id']
                                     + '_' + item['analysis_id']] = item['report_id']
                    open_case(item)
                if st.button('알림 확인', key='live_read_' + item['key'], disabled=item['status'] == 'read'):
                    st.session_state['demo_live_notifications'] = acknowledge_live(state, item['key'])
                    st.rerun()
    st.caption('실제 보고서 저장 완료를 확인해 알립니다. 알림 확인 표시는 이 브라우저 세션에만 남습니다.')
