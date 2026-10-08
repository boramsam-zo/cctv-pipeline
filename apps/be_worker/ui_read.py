"""Small change hints and one validated snapshot for the saved-result UI.

Revision is an invalidation hint, never a completion or integrity receipt.
Only snapshot() returns the existing validated scene/report views. These GET
paths never call providers, update the DB, or inspect worker heartbeat stages.
"""
import hashlib
import json

from . import flow_service


QUEUE_SQL = '''SELECT worker_job_id,
    state->>'status' AS status, state->>'error_code' AS error_code,
    state->>'provider_job_id' AS provider_job_id,
    state->>'input_sha256' AS input_sha256, state->>'gpu_key' AS gpu_key,
    state->'scene_events' AS scene_events,
    handoff->>'evidence_index_sha256' AS evidence_sha256,
    handoff->>'manifest_sha256' AS manifest_sha256
    FROM cctv_jobs.queue ORDER BY worker_job_id'''
FLOW_SQL = '''SELECT worker_job_id,status,error_code,paused,finished_at
    FROM cctv_jobs.flows ORDER BY worker_job_id'''
TASK_SQL = '''SELECT task_id,worker_job_id,status,error_code,paused,finished_at
    FROM cctv_jobs.analysis_tasks ORDER BY task_id'''
SELECTION_SQL = '''SELECT selection_id,input_sha256,selection,purpose,children
    FROM cctv_jobs.analysis_selections ORDER BY selection_id'''
TEAM_SQL = '''SELECT to_regclass('cctv_scene.team_descriptions') IS NOT NULL
    AND to_regclass('cctv_scene.team_report_runs') IS NOT NULL AS ok'''


def _description_sql(table):
    return f'''SELECT analysis_id,provider_job_id,event_id,result_sha256
        FROM cctv_scene.{table} ORDER BY analysis_id'''


def _report_sql(table):
    return f'''SELECT report_id,analysis_id,worker_job_id,
        state->>'status' AS status, state->>'error_code' AS error_code,
        state->>'result_sha256' AS result_sha256
        FROM cctv_scene.{table} ORDER BY report_id'''


def _queue_hint(row):
    row = dict(row)
    # A scene save refreshes updated_at even when its visible state is the
    # same. Include only fields that affect cards and report readiness.
    events = row.get('scene_events') or {}
    row['scene_events'] = {event_id: {key: event.get(key) for key in (
        'status', 'error_code', 'analysis_id', 'result_sha256',
        'media_bytes_verified', 'database_loaded')}
        for event_id, event in sorted(events.items())}
    return row


def revision(conn):
    """Read compact semantic DB fields; do not load media or result bodies."""
    data = {
        'queue': [_queue_hint(row) for row in conn.execute(QUEUE_SQL).fetchall()],
        'flows': conn.execute(FLOW_SQL).fetchall(),
        'tasks': conn.execute(TASK_SQL).fetchall(),
        'selections': conn.execute(SELECTION_SQL).fetchall(),
        'descriptions': conn.execute(_description_sql('descriptions')).fetchall(),
        'reports': conn.execute(_report_sql('report_runs')).fetchall(),
    }
    team = conn.execute(TEAM_SQL).fetchone()
    if team and team['ok']:
        data['team_descriptions'] = conn.execute(_description_sql('team_descriptions')).fetchall()
        data['team_reports'] = conn.execute(_report_sql('team_report_runs')).fetchall()
    raw = json.dumps(data, sort_keys=True, separators=(',', ':'), default=str,
                     allow_nan=False).encode()
    return {'revision': hashlib.sha256(raw).hexdigest()}


def snapshot(conn):
    # Capture the hint before validation: a concurrent completion after this
    # point remains visible to the next cheap poll, rather than being skipped.
    hint = revision(conn)
    jobs = flow_service.view(conn)
    selections = flow_service.selection_views(conn, verified_jobs=jobs)
    return dict(hint, jobs=jobs, selections=selections)
