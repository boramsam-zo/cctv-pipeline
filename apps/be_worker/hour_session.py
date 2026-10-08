"""Explicitly selected sessions, with durable attempts and expiry.

A private spool marker enables this guard. Absence keeps the original P6 guard.
The marker never grants execution: the matching DB session must be initialized.
The original B/full-report marker is unchanged. The separately named model
session allows one approved input with B and R2, at most once per model.
"""
from copy import deepcopy
from datetime import date
import json
import math
import os
from pathlib import Path
import re

from . import analysis_profiles as profiles, generation_settings as generation, team_settings, demo_session
from .common import MAX_VIDEO, WorkerError, checked_id, read_json

NAME = 'p6-hour-session-20261002-01'
MODEL = 'gemini-3.5-flash-lite'
KINDS = ('scene', 'embedding', 'report', 'cpu_retrieval')
MAX_JOBS = 100  # Existing provider's absolute job capacity, within the one-hour window.
MARKER = {'schema': 'cctv-hour-session-v1', 'name': NAME}
MODEL_SESSION_SCHEMA = 'cctv-model-session-v1'
MODEL_SESSION_FIELDS = {'schema', 'name', 'versions', 'purpose', 'max_jobs',
                        'per_job_seconds', 'window_seconds', 'approved_input_sha256'}


def require(value, code):
    if not value:
        raise WorkerError(code)


def marker_path():
    return Path(os.environ.get('BE_SPOOL_PATH', '/spool')) / '.hour-session.json'


def configured():
    path = marker_path()
    if not path.exists() and not path.is_symlink():
        return False
    current_marker()
    return True


def current_marker():
    """Select a separately approved row; never extend or reset an existing row."""
    path = marker_path()
    if not path.exists() and not path.is_symlink():
        return dict(MARKER)
    require(not path.is_symlink(), 'test_session_marker_invalid')
    value = read_json(path)
    return _validate_marker(value)


def _model_session(value):
    return isinstance(value, dict) and value.get('schema') == MODEL_SESSION_SCHEMA


def marker_from_state(value):
    from . import report_continuation
    fields = (report_continuation.FIELDS if report_continuation.selected(value) else
              demo_session.FIELDS if demo_session.selected(value) else
              MODEL_SESSION_FIELDS if _model_session(value) else {'schema', 'name'})
    result = {k: value[k] for k in fields}
    if 'max_jobs' in value:
        result['max_jobs'] = value['max_jobs']
    return _validate_marker(result)


def _validate_marker(value):
    from . import report_continuation
    if report_continuation.selected(value):
        return report_continuation.validate_marker(value)
    if demo_session.selected(value):
        return demo_session.validate_marker(value)
    if _model_session(value):
        require(set(value) == MODEL_SESSION_FIELDS, 'test_session_marker_invalid')
        name = value['name']
        require(isinstance(name, str) and re.fullmatch(r'mlops-session-[0-9]{8}-[0-9]{2}', name)
                and not name.endswith('-00'), 'test_session_marker_invalid')
        try:
            day = name.split('-')[-2]
            date.fromisoformat(day[:4] + '-' + day[4:6] + '-' + day[6:])
        except ValueError:
            raise WorkerError('test_session_marker_invalid') from None
        require(value['versions'] == ['B', 'R2'] and value['purpose'] in ('gpu_comparison', 'full_report')
                and type(value['max_jobs']) is int and value['max_jobs'] == 2
                and type(value['per_job_seconds']) is int and value['per_job_seconds'] == 300
                and type(value['window_seconds']) is int and value['window_seconds'] == 900
                and valid_sha(value['approved_input_sha256']), 'test_session_marker_invalid')
        return value
    require(isinstance(value, dict) and set(value) in (
            {'schema', 'name'}, {'schema', 'name', 'max_jobs'})
            and value.get('schema') == MARKER['schema'], 'test_session_marker_invalid')
    name = value.get('name')
    require(isinstance(name, str) and re.fullmatch(r'p6-hour-session-[0-9]{8}-[0-9]{2}', name)
            and not name.endswith('-00'), 'test_session_marker_invalid')
    try:
        day = name.split('-')[-2]
        date.fromisoformat(day[:4] + '-' + day[4:6] + '-' + day[6:])
    except ValueError:
        raise WorkerError('test_session_marker_invalid') from None
    require(name == NAME or 'max_jobs' in value, 'test_session_new_window_limit_required')
    if 'max_jobs' in value:
        require(type(value['max_jobs']) is int and 1 <= value['max_jobs'] <= MAX_JOBS,
                'test_session_marker_invalid')
    return value


def current_name():
    return current_marker()['name']


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def settings(value, *, analysis_profile=None):
    frozen = profiles.profile('B') if analysis_profile is None else analysis_profile
    result = generation.validate(value, analysis_profile=frozen)
    require(result['scene_model'] == MODEL and result['report_model'] == (
            'cpu-template' if team_settings.is_team(result) else MODEL),
            'test_session_model_changed')
    return result


def _binding_profile(binding):
    version = binding.get('version', 'B')
    if version == 'B':
        # Reading an old row must not depend on a new release file being valid.
        legacy = profiles.profile('B', deployment=profiles.LEGACY_DEPLOYMENT)
        if profiles.gpu_identity(binding['input_sha256'], legacy)['gpu_key'] == binding['gpu_key']:
            return legacy
    for frozen in profiles.trusted_profiles(version):
        if profiles.gpu_identity(binding['input_sha256'], frozen)['gpu_key'] == binding['gpu_key']:
            return frozen
    raise WorkerError('test_session_gpu_identity_changed')


def validate(p, *, expected_marker=None):
    """Execution validation always requires this release's frozen settings."""
    return _validate(p, expected_marker=expected_marker)


def validate_stored_history(p, *, expected_marker):
    """Inspect legacy session history after checking its saved task receipts.

    This does not authorize dispatch, admission, resume or claims. Callers must
    bind every stored team setting to the immutable task receipt first.
    """
    require(p.get('schema') == MARKER['schema'] and expected_marker.get('schema') == MARKER['schema'],
            'test_session_history_schema_invalid')
    return _validate(p, expected_marker=expected_marker, stored=True)


def _validate(p, *, expected_marker=None, stored=False):
    marker = current_marker() if expected_marker is None else _validate_marker(expected_marker)
    from . import report_continuation
    if report_continuation.selected(marker):
        return report_continuation.validate_state(p, marker)
    is_model = _model_session(marker)
    is_demo = demo_session.selected(marker)
    versioned = is_model or is_demo
    policy_fields = demo_session.FIELDS if is_demo else MODEL_SESSION_FIELDS
    keys = {'schema', 'name', 'started_at', 'deadline_at', 'stopped', 'bindings', 'claims'}
    if versioned:
        keys |= policy_fields
    if 'max_jobs' in marker:
        keys.add('max_jobs')
    require(isinstance(p, dict) and set(p) == keys, 'test_session_state_invalid')
    require(p['schema'] == marker['schema'] and p['name'] == marker['name'],
            'test_session_identity_changed')
    if versioned:
        require(all(type(p[k]) is type(marker[k]) and p[k] == marker[k]
                    for k in policy_fields), 'test_session_model_policy_changed')
    cap = marker.get('max_jobs', MAX_JOBS)
    require(type(cap) is int and 1 <= cap <= MAX_JOBS
            and type(p.get('max_jobs', MAX_JOBS)) is int
            and p.get('max_jobs', MAX_JOBS) == cap, 'test_session_approved_limit_changed')
    require(all(type(p[k]) in (int, float) and math.isfinite(p[k]) for k in ('started_at', 'deadline_at'))
            and p['deadline_at'] - p['started_at'] == (marker['window_seconds'] if versioned else 3600)
            and type(p['stopped']) is bool, 'test_session_window_invalid')
    require(isinstance(p['bindings'], dict) and len(p['bindings']) <= cap
            and isinstance(p['claims'], list), 'test_session_state_invalid')
    keys = set()
    versions, input_sizes = set(), set()
    for wid, b in p['bindings'].items():
        checked_id(wid)
        binding_keys = {'input_sha256', 'input_bytes', 'gpu_key', 'settings', 'reference_date', 'event_ids'}
        if versioned:
            binding_keys.add('version')
        require(isinstance(b, dict) and set(b) == binding_keys,
                'test_session_binding_invalid')
        require(valid_sha(b['input_sha256']) and type(b['input_bytes']) is int
                and 0 < b['input_bytes'] <= MAX_VIDEO, 'test_session_binding_invalid')
        if versioned:
            require(isinstance(b['version'], str) and b['version'] in marker['versions'],
                    'test_session_model_binding_invalid')
        if is_demo:
            require(b['input_bytes'] <= marker['max_upload_bytes'], 'demo_session_input_too_large')
        frozen = _binding_profile(b)
        if is_model:
            require(b['input_sha256'] == marker['approved_input_sha256']
                    and b['version'] in marker['versions'] and b['version'] not in versions,
                    'test_session_model_binding_invalid')
            versions.add(b['version'])
            input_sizes.add(b['input_bytes'])
            require(len(input_sizes) == 1, 'test_session_input_size_changed')
        require(b['gpu_key'] not in keys, 'test_session_gpu_identity_changed')
        keys.add(b['gpu_key'])
        if is_model and marker['purpose'] == 'gpu_comparison':
            require(b['settings'] is None and b['reference_date'] is None,
                    'test_session_comparison_has_generation_settings')
        else:
            if is_demo:
                require(team_settings.is_team(b['settings']), 'demo_session_cpu_report_required')
            if stored and team_settings.is_team(b['settings']):
                saved = team_settings.validate_stored(b['settings'], frozen)
                require(saved['scene_model'] == MODEL and saved['report_model'] == 'cpu-template',
                        'test_session_model_changed')
            else:
                settings(b['settings'], analysis_profile=frozen)
            require(team_settings.reference_date(b['settings'], b['reference_date']) == b['reference_date'],
                    'test_session_date_invalid')
        if b['event_ids'] is not None:
            events(b['event_ids'])
    seen = set()
    if is_model and marker['purpose'] == 'gpu_comparison':
        require(not p['claims'], 'test_session_generation_not_approved')
    for c in p['claims']:
        require(set(c) == {'worker_job_id', 'event_id', 'kind', 'request_sha256', 'model', 'claimed_at'},
                'test_session_claim_invalid')
        wid, kind = c['worker_job_id'], c['kind']
        require(wid in p['bindings'] and kind in KINDS and (wid, kind) not in seen,
                'test_session_claim_invalid')
        b = p['bindings'][wid]
        require(kind != 'cpu_retrieval' or team_settings.is_team(b['settings']),
                'test_session_cpu_pipeline_invalid')
        require(not team_settings.is_team(b['settings']) or kind in ('scene', 'cpu_retrieval'),
                'test_session_team_external_report_forbidden')
        require(b['event_ids'] is not None and c['event_id'] in b['event_ids']
                and valid_sha(c['request_sha256']) and c['model'] == model_for(kind)
                and type(c['claimed_at']) in (int, float) and math.isfinite(c['claimed_at'])
                and p['started_at'] <= c['claimed_at'] < p['deadline_at'], 'test_session_claim_invalid')
        seen.add((wid, kind))
    return p


def model_for(kind):
    if kind == 'cpu_retrieval':
        return 'intfloat/multilingual-e5-small'
    return 'gemini-embedding-001' if kind == 'embedding' else MODEL


def events(value):
    require(isinstance(value, (list, tuple)) and len(value) <= 1
            and all(isinstance(v, str) and re.fullmatch(r'event_[0-9]+', v) for v in value),
            'test_session_candidate_limit')
    return list(value)


def get(conn, *, lock=False):
    marker = current_marker()
    row = conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s' +
                       (' FOR UPDATE' if lock else ''), (marker['name'],)).fetchone()
    require(row is not None, 'test_session_not_started')
    result = validate(deepcopy(row['payload']), expected_marker=marker)
    require(current_marker() == marker, 'test_session_marker_changed_during_read')
    from . import report_continuation
    if report_continuation.selected(marker):
        report_continuation.check_origin(conn, marker)
    return result


def clock(conn):
    return float(conn.execute('SELECT extract(epoch FROM clock_timestamp()) AS epoch').fetchone()['epoch'])


def active(conn, p):
    marker = current_marker()
    require(p['name'] == marker['name'] and p.get('max_jobs', MAX_JOBS) == marker.get('max_jobs', MAX_JOBS),
            'test_session_identity_changed')
    from . import report_continuation
    if _model_session(p) or _model_session(marker) or demo_session.selected(marker) or report_continuation.selected(marker):
        # Pin every field, not merely the session name/cap, between reads.
        validate(p, expected_marker=marker)
    now = clock(conn)
    require(not p['stopped'], 'test_session_stopped')
    require(p['started_at'] <= now < p['deadline_at'], 'test_session_expired')
    return now


def write(conn, p):
    marker = current_marker()
    validate(p, expected_marker=marker)
    raw = json.dumps(p, sort_keys=True, allow_nan=False)
    row = conn.execute('UPDATE cctv_jobs.service_state SET payload=%s::jsonb, updated_at=now() '
                       'WHERE name=%s RETURNING payload', (raw, p['name'])).fetchone()
    require(row is not None and row['payload'] == p, 'test_session_write_mismatch')
    require(current_marker() == marker, 'test_session_marker_changed_during_write')


def initialize(conn, started_at, deadline_at):
    marker = current_marker()
    from . import report_continuation
    bindings = {}
    if report_continuation.selected(marker):
        report_continuation.check_origin(conn, marker)
        bindings = {marker['source']['worker_job_id']: report_continuation.binding(marker)}
    initial = dict(marker, started_at=started_at, deadline_at=deadline_at,
                   stopped=False, bindings=bindings, claims=[])
    validate(initial, expected_marker=marker)
    with conn.transaction():
        active(conn, initial)
        conn.execute('INSERT INTO cctv_jobs.service_state(name,payload) VALUES (%s,%s::jsonb) '
                     'ON CONFLICT (name) DO NOTHING', (marker['name'], json.dumps(initial)))
        saved = get(conn, lock=True)
        require(current_marker() == marker and saved['name'] == marker['name']
                and saved.get('max_jobs', MAX_JOBS) == marker.get('max_jobs', MAX_JOBS),
                'test_session_marker_changed_during_initialize')
        require(saved['started_at'] == started_at and saved['deadline_at'] == deadline_at,
                'test_session_existing_window_preserved')
    return saved


def stop(conn):
    with conn.transaction():
        p = get(conn, lock=True)
        p['stopped'] = True
        write(conn, p)
    return p


def check_admission(conn, sha, size, versions, purpose):
    p = get(conn, lock=True)
    active(conn, p)
    from . import report_continuation
    if report_continuation.selected(p):
        require(tuple(versions) == (p['source']['analysis_profile']['version'],) and purpose == 'full_report'
                and sha == p['source']['input_sha256'] and size == p['source']['input_bytes'],
                'report_continuation_admission_not_approved')
    elif demo_session.selected(p):
        require(isinstance(versions, (list, tuple)) and len(versions) == 1
                and versions[0] in p['versions'] and purpose == 'full_report',
                'demo_session_selection_not_approved')
        require(type(size) is int and 0 < size <= p['max_upload_bytes'], 'demo_session_input_too_large')
        if p['bindings']:
            busy = conn.execute('''SELECT count(*) AS count FROM cctv_jobs.analysis_tasks
                WHERE worker_job_id = ANY(%s) AND finished_at IS NULL AND NOT paused''',
                (list(p['bindings']),)).fetchone()['count']
            require(busy == 0, 'demo_session_busy')
    elif _model_session(p):
        require(isinstance(versions, (list, tuple)) and tuple(versions) in (('B',), ('R2',), ('B', 'R2'))
                and purpose == p['purpose'], 'test_session_model_selection_not_approved')
        require(sha == p['approved_input_sha256'], 'test_session_input_not_approved')
    else:
        require(tuple(versions) == ('B',) and purpose == 'full_report',
                'test_session_select_b_full_report')
    require(valid_sha(sha) and type(size) is int and 0 < size <= MAX_VIDEO,
            'test_session_input_invalid')
    return p


def row_binding(row, value, day, *, policy=None):
    policy = current_marker() if policy is None else policy
    from . import report_continuation
    if report_continuation.selected(policy):
        report_continuation.check_row(policy, row)
        require(value == policy['generation_settings'] and day is None,
                'report_continuation_settings_changed')
        return row['worker_job_id'], report_continuation.binding(policy)
    is_model = _model_session(policy)
    is_demo = demo_session.selected(policy)
    versioned = is_model or is_demo
    s = row['state']; wid = checked_id(row['worker_job_id'])
    try:
        frozen = profiles.validate_profile(s['analysis_profile'], expected_version=None if versioned else 'B')
    except ValueError:
        raise WorkerError('test_session_requires_b') from None
    require(s['worker_job_id'] == wid, 'test_session_requires_b')
    if is_model:
        require(frozen['version'] in policy['versions'] and s['input_sha256'] == policy['approved_input_sha256'],
                'test_session_model_selection_not_approved')
    if is_demo:
        require(frozen['version'] in policy['versions'] and type(s['input_bytes']) is int
                and 0 < s['input_bytes'] <= policy['max_upload_bytes'], 'demo_session_input_or_model_not_approved')
    key = profiles.gpu_identity(s['input_sha256'], frozen)['gpu_key']
    require(key == s['gpu_key'], 'test_session_gpu_identity_changed')
    if is_model and policy['purpose'] == 'gpu_comparison':
        require(value is None and day is None, 'test_session_comparison_has_generation_settings')
        selected_settings, selected_day = None, None
    else:
        selected_settings = settings(value, analysis_profile=frozen)
        if is_demo:
            require(team_settings.is_team(selected_settings), 'demo_session_cpu_report_required')
        selected_day = team_settings.reference_date(value, day)
    result = dict(input_sha256=s['input_sha256'], input_bytes=s['input_bytes'], gpu_key=key,
                  settings=selected_settings, reference_date=selected_day, event_ids=None)
    if versioned:
        result['version'] = frozen['version']
    return wid, result


def bind_job(conn, row, value, day):
    require(getattr(conn.info, 'transaction_status', None) == 2,
            'test_session_binding_requires_transaction')
    p = get(conn, lock=True)
    active(conn, p)
    wid, b = row_binding(row, value, day, policy=p)
    previous = p['bindings'].get(wid)
    if previous:
        b['event_ids'] = previous['event_ids']
        require(b == previous, 'test_session_existing_binding_preserved')
        return previous
    from . import report_continuation
    require(not report_continuation.selected(p), 'report_continuation_prebound_job_only')
    # Changing windows must not grant a second attempt to an old request. This
    # runs under admission/dispatch locks in the caller's existing transaction.
    if demo_session.selected(p):
        already = conn.execute("SELECT EXISTS(SELECT 1 FROM cctv_jobs.service_state "
            "WHERE name ~ '^(p6-hour-session|mlops-session|demo-session)-[0-9]{8}-[0-9]{2}$' "
            "AND name<>%s AND (payload->'bindings' ? %s OR EXISTS "
            "(SELECT 1 FROM jsonb_each(payload->'bindings') AS b "
            "WHERE b.value->>'gpu_key'=%s))) AS already_bound", (p['name'], wid, b['gpu_key'])).fetchone()
        require(not any(other['gpu_key'] == b['gpu_key'] for other in p['bindings'].values()),
                'test_session_gpu_identity_changed')
    elif _model_session(p):
        already = conn.execute("SELECT EXISTS(SELECT 1 FROM cctv_jobs.service_state "
            "WHERE name ~ '^(p6-hour-session|mlops-session|demo-session)-[0-9]{8}-[0-9]{2}$' "
            "AND name<>%s AND (payload->'bindings' ? %s OR EXISTS "
            "(SELECT 1 FROM jsonb_each(payload->'bindings') AS b "
            "WHERE b.value->>'gpu_key'=%s))) AS already_bound", (p['name'], wid, b['gpu_key'])).fetchone()
        require(not any(other['version'] == b['version'] or other['gpu_key'] == b['gpu_key']
                        for other in p['bindings'].values()), 'test_session_model_already_bound')
        require(not any(other['input_bytes'] != b['input_bytes'] for other in p['bindings'].values()),
                'test_session_input_size_changed')
    else:
        already = conn.execute("SELECT EXISTS(SELECT 1 FROM cctv_jobs.service_state "
                               "WHERE name ~ '^(p6-hour-session|demo-session)-[0-9]{8}-[0-9]{2}$' "
                               "AND name<>%s AND payload->'bindings' ? %s) AS already_bound",
                               (p['name'], wid)).fetchone()
    require(not already['already_bound'], 'test_session_old_job_preserved_view_only')
    require(row['state']['analysis_profile'] == profiles.profile(b.get('version', 'B')),
            'test_session_requires_current_deployment')
    require(len(p['bindings']) < p.get('max_jobs', MAX_JOBS), 'test_session_capacity_reached')
    p['bindings'][wid] = b
    write(conn, p)
    return b


def handoff(row, *, policy=None):
    configs = (row.get('handoff') or {}).get('scene_configs')
    require(isinstance(configs, list), 'test_session_handoff_required')
    found = []
    for name in configs:
        match = re.fullmatch(r'scene-(event_[0-9]+)\.json', name) if isinstance(name, str) else None
        require(match is not None, 'test_session_handoff_invalid')
        found.append(match[1])
    if demo_session.selected(current_marker() if policy is None else policy):
        return demo_session.representative_events(found)
    return events(found)


def check_events(conn, row, event_ids, value, purpose, day):
    with conn.transaction():
        p = get(conn, lock=True)
        active(conn, p)
        wid, expected = row_binding(row, value, day, policy=p)
        b = p['bindings'].get(wid)
        require(b is not None and purpose == (p['purpose'] if _model_session(p) else 'full_report'),
                'test_session_job_not_bound')
        expected['event_ids'] = b['event_ids']
        require(expected == b, 'test_session_task_settings_changed')
        selected = events(event_ids)
        require(selected == handoff(row, policy=p), 'test_session_event_selection_changed')
        require(b['event_ids'] is None or b['event_ids'] == selected, 'test_session_events_changed')
        b['event_ids'] = selected
        write(conn, p)
    return b


def claim(conn, row, event_id, kind, request_sha256, model=None):
    require(conn.autocommit and conn.info.transaction_status == 0, 'test_session_requires_idle_autocommit')
    with conn.transaction():
        p = get(conn, lock=True)
        observed = active(conn, p)
        from . import report_continuation
        if report_continuation.selected(p) and kind == 'scene':
            require(p['deadline_at'] - observed >= 120, 'report_continuation_insufficient_call_time')
        require(not _model_session(p) or p['purpose'] == 'full_report',
                'test_session_generation_not_approved')
        wid = checked_id(row['worker_job_id'])
        b = p['bindings'].get(wid)
        require(b is not None, 'test_session_job_not_bound')
        _, expected = row_binding(row, b['settings'], b['reference_date'], policy=p)
        expected['event_ids'] = b['event_ids']
        require(expected == b and b['event_ids'] is not None and b['event_ids'] == handoff(row, policy=p)
                and event_id in b['event_ids'], 'test_session_event_not_approved')
        require(kind in KINDS and model == model_for(kind) and valid_sha(request_sha256),
                'test_session_call_invalid')
        require(not team_settings.is_team(b['settings']) or kind in ('scene', 'cpu_retrieval'),
                'test_session_team_external_report_forbidden')
        require(kind != 'cpu_retrieval' or team_settings.is_team(b['settings']),
                'test_session_cpu_pipeline_invalid')
        require(not any(c['worker_job_id'] == wid and c['kind'] == kind for c in p['claims']),
                'test_session_attempt_already_claimed_no_retry')
        receipt = dict(worker_job_id=wid, event_id=event_id, kind=kind,
                       request_sha256=request_sha256, model=model, claimed_at=observed)
        p['claims'].append(receipt)
        write(conn, p)
    return receipt


def check_dispatch(conn, row):
    p = get(conn)
    active(conn, p)
    wid = checked_id(row['worker_job_id'])
    b = p['bindings'].get(wid)
    require(b is not None, 'test_session_job_not_bound')
    _, expected = row_binding(row, b['settings'], b['reference_date'], policy=p)
    expected['event_ids'] = b['event_ids']
    require(expected == b, 'test_session_binding_changed')
    if _model_session(p) or demo_session.selected(p):
        fields = demo_session.FIELDS if demo_session.selected(p) else MODEL_SESSION_FIELDS
        require(current_marker() == {k: p[k] for k in fields},
                'test_session_marker_changed_during_dispatch')
