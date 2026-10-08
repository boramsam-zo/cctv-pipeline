"""Finite public-demo policy; selecting a marker never grants execution.

The existing hour_session database ledger owns activation, expiry and claims.
This policy permits new uploads for the packaged B/R2 models and one generated
description/report for the earliest generated candidate. All GPU candidates
remain in the original handoff; unselected candidates are not described.
"""
from datetime import date
import re

from .common import MAX_VIDEO, WorkerError

SCHEMA = 'cctv-demo-session-v1'
FIELDS = {'schema', 'name', 'versions', 'purpose', 'max_jobs', 'per_job_seconds',
          'window_seconds', 'max_upload_bytes', 'max_video_seconds'}
MAX_JOBS = 20
MAX_WINDOW_SECONDS = 86400
DEFAULT_WINDOW_SECONDS = 28800
DEFAULT_MAX_JOBS = 5
MAX_VIDEO_SECONDS = 120  # The current packaged Runpod service's input limit.
DEFAULT_UPLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_VIDEO_SECONDS = 60
CANDIDATE_POLICY = 'first-generated-candidate-v1'


def selected(value):
    return isinstance(value, dict) and value.get('schema') == SCHEMA


def require(value, code):
    if not value:
        raise WorkerError(code)


def validate_marker(value):
    require(isinstance(value, dict) and set(value) == FIELDS and selected(value),
            'demo_session_marker_invalid')
    name = value['name']
    require(isinstance(name, str) and re.fullmatch(r'demo-session-[0-9]{8}-[0-9]{2}', name)
            and not name.endswith('-00'), 'demo_session_marker_invalid')
    try:
        day = name.split('-')[-2]
        date.fromisoformat(day[:4] + '-' + day[4:6] + '-' + day[6:])
    except ValueError:
        raise WorkerError('demo_session_marker_invalid') from None
    require(value['versions'] in (['B'], ['B', 'R2']) and value['purpose'] == 'full_report'
            and type(value['max_jobs']) is int and 1 <= value['max_jobs'] <= MAX_JOBS
            and type(value['per_job_seconds']) is int and value['per_job_seconds'] == 300
            and type(value['window_seconds']) is int and 60 <= value['window_seconds'] <= MAX_WINDOW_SECONDS
            and type(value['max_upload_bytes']) is int and 1 <= value['max_upload_bytes'] <= MAX_VIDEO
            and type(value['max_video_seconds']) is int and 1 <= value['max_video_seconds'] <= MAX_VIDEO_SECONDS,
            'demo_session_marker_invalid')
    return value


def representative_events(event_ids):
    """Use the first generated ID, independent of caller list ordering."""
    require(isinstance(event_ids, (list, tuple))
            and all(isinstance(item, str) and re.fullmatch(r'event_[0-9]+', item) for item in event_ids)
            and len(set(event_ids)) == len(event_ids), 'demo_session_candidate_identity_invalid')
    return sorted(event_ids, key=lambda item: (int(item.split('_')[1]), item))[:1]


def bound_job(conn, row):
    """Apply demo-only behavior solely to a matching current session binding."""
    from . import hour_session
    if not selected(hour_session.current_marker()):
        return False
    policy = hour_session.get(conn)
    binding = policy['bindings'].get(row['worker_job_id'])
    if binding is None:
        return False
    _, expected = hour_session.row_binding(row, binding['settings'],
                                          binding['reference_date'], policy=policy)
    expected['event_ids'] = binding['event_ids']
    require(binding == expected, 'demo_session_bound_job_changed')
    return True
