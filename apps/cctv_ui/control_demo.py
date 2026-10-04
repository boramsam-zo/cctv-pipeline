"""One presentation alert per screen session; never writes records or calls providers."""
from datetime import datetime, timezone
import time

from apps.cctv_ui.control_data import event_ids, normalize_case
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
