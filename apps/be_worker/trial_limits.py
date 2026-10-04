"""One durable P6 trial; missing plan preserves ordinary worker behaviour.

Initialize while admission/workers are quiescent. A claim reserves an attempt,
not a successful response: it is never reset or released after uncertainty.
"""
from copy import deepcopy
from datetime import date
import json
import re

from . import analysis_profiles as profiles, generation_settings as generation
from . import hour_session
from .common import WorkerError, checked_id, now

NAME = 'p6-final-trial'
INPUT_SHA256 = '6c8233026b1393dd1a7287fb73127fa8799b11337985b444b31f72091295e459'
INPUT_BYTES = 5855313
MODEL = 'gemini-3.5-flash-lite'
KINDS = ('scene', 'report', 'embedding')


def approved_plan():
    return {
        'schema_version': 'cctv-p6-final-trial-v1',
        'plan_id': 'p6-final-trial-20261002-01', 'approved': True,
        'input_sha256': INPUT_SHA256, 'input_bytes': INPUT_BYTES,
        'versions': ['A', 'B'], 'purpose': 'full_report',
        'profiles': {v: profiles.profile(v, deployment=profiles.LEGACY_DEPLOYMENT) for v in ('A', 'B')},
        'models': {'scene': MODEL, 'report': MODEL, 'embedding': 'gemini-embedding-001'},
        'max_candidates_per_version': 1,
        'max_calls': {kind: 2 for kind in KINDS},
        'max_calls_per_version': {kind: 1 for kind in KINDS},
    }


def _require(value, code):
    if not value:
        raise WorkerError(code)


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        raise WorkerError('trial_state_invalid') from None


def _plan(value):
    _require(_canonical(value) == _canonical(approved_plan()), 'trial_plan_not_approved')


def _settings(value, version):
    result = generation.validate(value, analysis_profile=profiles.profile(version, deployment=profiles.LEGACY_DEPLOYMENT))
    _require(result['scene_model'] == MODEL and result['report_model'] == MODEL,
             'trial_generation_model_changed')
    return result


def _day(value):
    try:
        text = str(value)
        _require(date.fromisoformat(text).isoformat() == text, 'trial_reference_date_invalid')
    except (TypeError, ValueError):
        raise WorkerError('trial_reference_date_invalid') from None
    return text


def _events(value):
    _require(isinstance(value, (list, tuple)) and len(value) <= 1
             and all(isinstance(item, str) and re.fullmatch(r'event_[0-9]+', item) for item in value)
             and len(set(value)) == len(value), 'trial_candidate_limit_or_identity')
    return list(value)


def _validate(payload):
    _require(isinstance(payload, dict) and set(payload) == {'plan', 'bindings', 'claims'},
             'trial_state_invalid')
    _plan(payload['plan'])
    bindings, claims = payload['bindings'], payload['claims']
    _require(isinstance(bindings, dict) and set(bindings) <= {'A', 'B'} and isinstance(claims, list),
             'trial_state_invalid')
    worker_ids, dates = set(), set()
    for version, binding in bindings.items():
        _require(isinstance(binding, dict) and set(binding) == {
            'worker_job_id', 'gpu_key', 'settings', 'reference_date', 'event_ids'}, 'trial_binding_invalid')
        worker_id = checked_id(binding['worker_job_id'])
        _require(worker_id not in worker_ids, 'trial_worker_bound_to_multiple_versions')
        worker_ids.add(worker_id)
        expected = profiles.gpu_identity(INPUT_SHA256, profiles.profile(version, deployment=profiles.LEGACY_DEPLOYMENT))['gpu_key']
        _require(binding['gpu_key'] == expected, 'trial_gpu_identity_mismatch')
        _settings(binding['settings'], version)
        dates.add(_day(binding['reference_date']))
        if binding['event_ids'] is not None:
            _events(binding['event_ids'])
    _require(len(dates) <= 1, 'trial_reference_date_changed')
    slots = set()
    for item in claims:
        _require(isinstance(item, dict) and set(item) == {'version', 'worker_job_id', 'gpu_key',
                 'event_id', 'kind', 'request_sha256', 'model', 'claimed_at'}, 'trial_claim_invalid')
        version, kind = item['version'], item['kind']
        _require(isinstance(version, str) and version in bindings and isinstance(kind, str) and kind in KINDS,
                 'trial_claim_invalid')
        binding = bindings[version]
        _require((version, kind) not in slots, 'trial_claim_limit_exceeded')
        slots.add((version, kind))
        _require(item['worker_job_id'] == binding['worker_job_id'] and item['gpu_key'] == binding['gpu_key']
                 and binding['event_ids'] is not None and item['event_id'] in binding['event_ids']
                 and item['model'] == payload['plan']['models'][kind]
                 and isinstance(item['request_sha256'], str)
                 and re.fullmatch('[a-f0-9]{64}', item['request_sha256'])
                 and isinstance(item['claimed_at'], str), 'trial_claim_invalid')
    return payload


def _read(conn, *, lock=False):
    # Queue-only installations predate this optional table. Only a confirmed
    # absent relation disables the trial; permission/connection errors propagate.
    relation = conn.execute("SELECT to_regclass('cctv_jobs.service_state') AS table_name").fetchone()
    _require(isinstance(relation, dict) and 'table_name' in relation, 'trial_table_lookup_failed')
    if relation['table_name'] is None:
        return None
    row = conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s' +
                       (' FOR UPDATE' if lock else ''), (NAME,)).fetchone()
    return _validate(deepcopy(row['payload'])) if row else None


def get(conn):
    if hour_session.configured():
        return hour_session.get(conn)
    return _read(conn)


def _idle(conn):
    status = getattr(getattr(conn, 'info', None), 'transaction_status', None)
    _require(getattr(conn, 'autocommit', None) is True and status == 0,
             'trial_requires_idle_autocommit')


def _write(conn, payload):
    _validate(payload)
    result = conn.execute('''UPDATE cctv_jobs.service_state SET payload=%s::jsonb, updated_at=now()
        WHERE name=%s RETURNING payload''', (_canonical(payload), NAME)).fetchone()
    _require(result is not None and _canonical(result['payload']) == _canonical(payload),
             'trial_database_readback_mismatch')


def initialize(conn, plan):
    _plan(plan)
    _idle(conn)
    initial = {'plan': deepcopy(plan), 'bindings': {}, 'claims': []}
    with conn.transaction():
        conn.execute('''INSERT INTO cctv_jobs.service_state(name,payload)
            VALUES (%s,%s::jsonb) ON CONFLICT (name) DO NOTHING''', (NAME, _canonical(initial)))
        stored = _read(conn, lock=True)
        _require(stored is not None and _canonical(stored['plan']) == _canonical(plan),
                 'trial_existing_plan_preserved')
    return stored


def check_admission(conn, sha, size, versions, purpose):
    if hour_session.configured():
        return hour_session.check_admission(conn, sha, size, versions, purpose)
    payload = get(conn)
    if payload is None:
        return None
    _require(sha == INPUT_SHA256 and type(size) is int and size == INPUT_BYTES,
             'trial_input_not_approved')
    _require(isinstance(versions, (list, tuple)) and tuple(versions) in (('A',), ('B',), ('A', 'B'))
             and purpose == 'full_report', 'trial_selection_not_approved')
    return payload


def _row(row):
    try:
        state = row['state']
        frozen = profiles.validate_profile(state['analysis_profile'])
        _require(frozen == profiles.profile(frozen['version'], deployment=profiles.LEGACY_DEPLOYMENT),
                 'trial_profile_not_approved')
        worker_id = checked_id(row['worker_job_id'])
        identity = profiles.gpu_identity(INPUT_SHA256, frozen)
        _require(state['input_sha256'] == INPUT_SHA256 and type(state['input_bytes']) is int
                 and state['input_bytes'] == INPUT_BYTES, 'trial_input_not_approved')
        _require(state['worker_job_id'] == worker_id and state['gpu_key'] == identity['gpu_key'],
                 'trial_gpu_identity_mismatch')
        return frozen['version'], worker_id, identity['gpu_key']
    except (KeyError, TypeError, ValueError):
        raise WorkerError('trial_versioned_job_required') from None


def _binding(payload, row):
    version, worker_id, gpu_key = _row(row)
    binding = payload['bindings'].get(version)
    _require(binding is not None and binding['worker_job_id'] == worker_id and binding['gpu_key'] == gpu_key,
             'trial_job_not_bound')
    return version, binding


def bind_job(conn, row, settings, reference_date):
    if hour_session.configured():
        return hour_session.bind_job(conn, row, settings, reference_date)
    payload = _read(conn, lock=True)
    if payload is None:
        return None
    _require(getattr(getattr(conn, 'info', None), 'transaction_status', None) == 2,
             'trial_binding_requires_admission_transaction')
    version, worker_id, gpu_key = _row(row)
    binding = {'worker_job_id': worker_id, 'gpu_key': gpu_key, 'settings': _settings(settings, version),
               'reference_date': _day(reference_date), 'event_ids': None}
    previous = payload['bindings'].get(version)
    if previous is not None:
        binding['event_ids'] = previous['event_ids']
        _require(_canonical(previous) == _canonical(binding), 'trial_existing_binding_preserved')
        return deepcopy(previous)
    payload['bindings'][version] = binding
    _write(conn, payload)
    return deepcopy(binding)


def _handoff(row):
    handoff = row.get('handoff')
    _require(isinstance(handoff, dict) and isinstance(handoff.get('scene_configs'), list),
             'trial_handoff_required')
    events = []
    for name in handoff['scene_configs']:
        match = re.fullmatch(r'scene-(event_[0-9]+)\.json', name) if isinstance(name, str) else None
        _require(match is not None, 'trial_handoff_invalid')
        events.append(match[1])
    return _events(events)


def check_events(conn, row, event_ids, settings, purpose, reference_date):
    if hour_session.configured():
        return hour_session.check_events(conn, row, event_ids, settings, purpose, reference_date)
    if get(conn) is None:
        return None
    _idle(conn)
    with conn.transaction():
        payload = _read(conn, lock=True)
        _require(payload is not None, 'trial_plan_missing')
        version, binding = _binding(payload, row)
        events = _events(event_ids)
        _require(events == _handoff(row), 'trial_event_selection_changed')
        _require(purpose == 'full_report' and _canonical(_settings(settings, version)) == _canonical(binding['settings'])
                 and _day(reference_date) == binding['reference_date'], 'trial_task_settings_changed')
        _require(binding['event_ids'] is None or binding['event_ids'] == events, 'trial_existing_events_preserved')
        if binding['event_ids'] is None:
            binding['event_ids'] = events
            _write(conn, payload)
    return deepcopy(binding)


def claim(conn, row, event_id, kind, request_sha256, model=None):
    if hour_session.configured():
        return hour_session.claim(conn, row, event_id, kind, request_sha256, model)
    if get(conn) is None:
        return None
    _idle(conn)
    with conn.transaction():
        payload = _read(conn, lock=True)
        _require(payload is not None, 'trial_plan_missing')
        version, binding = _binding(payload, row)
        _require(binding['event_ids'] is not None and binding['event_ids'] == _handoff(row)
                 and event_id in binding['event_ids'], 'trial_event_not_approved')
        _require(isinstance(kind, str) and kind in KINDS, 'trial_call_kind_invalid')
        expected_model = payload['plan']['models'][kind]
        _require(model == expected_model, 'trial_call_model_changed')
        _require(isinstance(request_sha256, str) and re.fullmatch('[a-f0-9]{64}', request_sha256),
                 'trial_request_sha256_invalid')
        _require(not any(c['version'] == version and c['kind'] == kind for c in payload['claims']),
                 'trial_attempt_already_claimed_no_retry')
        _require(sum(c['kind'] == kind for c in payload['claims']) < payload['plan']['max_calls'][kind],
                 'trial_call_limit_exceeded')
        receipt = {'version': version, 'worker_job_id': binding['worker_job_id'], 'gpu_key': binding['gpu_key'],
                   'event_id': event_id, 'kind': kind, 'model': model, 'request_sha256': request_sha256,
                   'claimed_at': now()}
        payload['claims'].append(receipt)
        _write(conn, payload)
    # Exiting the outer transaction (autocommit + idle above) commits before a caller can use the result.
    return deepcopy(receipt)
