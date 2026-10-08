"""Explicit, one-shot CPU report recovery from an already paid VLM response.

The selected normalization is explicit and part of the reviewed plan. Original
bytes, failed task/session and failed tracking records are never changed. There is no
provider client, GPU dispatch or generation key loader in this execution path.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from .common import WorkerError, checked_id, locked, read_json
from . import hour_session, report_continuation, selection_jobs, team_settings, demo_session
from . import mlflow_tracking, mlflow_tracing
from .mlflow_trace_content import bounded_record
from .report_jobs import fingerprint
from .report_review import save_review
from apps.scene_description import scene, team_scene, team_db

SCHEMA = 'cctv-cpu-report-recovery-v1'
NORMALIZATION = 'cctv-team-schema-annotation-normalization-v1'
ROAD_NORMALIZATION = 'cctv-team-unregistered-road-normalization-v2'
PERSON_EVIDENCE_NORMALIZATION = 'cctv-team-person-evidence-warning-v1'
NORMALIZATIONS = (NORMALIZATION, ROAD_NORMALIZATION, PERSON_EVIDENCE_NORMALIZATION)
ROAD_CORRECTION = {'path': '/location/road_type', 'before': 'highway', 'after': 'unknown',
                   'reason': 'camera_registration_not_provided'}
PERSON_EVIDENCE_CORRECTION = {'path': '/people/person_on_road',
    'before': {'state': 'observed', 'evidence_ids': []},
    'after': {'state': 'unknown', 'evidence_ids': []},
    'reason': 'observed_without_evidence_reference'}
PERSON_EVIDENCE_WARNING = ('모델은 도로 위 사람을 관찰했다고 응답함 — 근거 연결 확인 필요'
                           '(people.person_on_road: 영상 근거 번호 누락).')
SCHEMA_URI = 'https://json-schema.org/draft/2020-12/schema'
CPU_SECONDS = 300
ORIGINAL_FILES = ('request.json', 'response.raw.json', 'failure.json')
RECEIPT_FIELDS = {'schema','execution_id','worker_job_id','input_sha256','source_task_id',
    'source_session','source_session_sha256','plan_sha256','event_id','analysis_id',
    'source_failed_run_id','source_trace_id','status','report_id','result_sha256','scene_sha256',
    'new_model_calls','cpu_retrieval_calls','source_response_sha256','started_at_ms',
    'finished_at_ms','pipeline_seconds','original_failed_task_preserved','automatic_retry',
    'duration_scope','storage_attempt_id'}


def require(ok, code):
    if not ok:
        raise WorkerError(code)


def canonical(value):
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def file_sha(path):
    return report_continuation.file_sha(Path(path))


def _read(path):
    return scene.parse(team_scene._read(Path(path)))


def write_new(path, value):
    path = Path(path)
    require(not path.exists() and not path.is_symlink(), 'report_recovery_existing_receipt_no_retry')
    save_review(path, value)


def _version_allowed(version, normalization):
    return normalization in NORMALIZATIONS and (
        version == 'R2' or version == 'B' and normalization == PERSON_EVIDENCE_NORMALIZATION)


def _normalized_event(raw, event_id, normalization):
    """Reproduce one explicitly selected derivation without changing raw bytes."""
    require(normalization in NORMALIZATIONS, 'report_recovery_normalization_not_supported')
    require(isinstance(raw, dict) and not raw.get('promptFeedback', {}).get('blockReason'),
            'report_recovery_response_blocked')
    candidates = raw.get('candidates')
    require(isinstance(candidates, list) and len(candidates) == 1
            and candidates[0].get('finishReason') == 'STOP', 'report_recovery_response_incomplete')
    parts = candidates[0].get('content', {}).get('parts')
    require(isinstance(parts, list), 'report_recovery_response_parts_invalid')
    text = ''.join(part.get('text', '') for part in parts if not part.get('thought', False))
    value = team_scene._parse_observation(text)
    require(isinstance(value, dict), 'report_recovery_event_required')
    answer = deepcopy(value)
    removed = {}
    if normalization == NORMALIZATION or '$schema' in answer:
        require(answer.get('$schema') == SCHEMA_URI, 'report_recovery_exact_annotation_required')
        del answer['$schema']
        removed = {'$schema': SCHEMA_URI}
    if normalization == ROAD_NORMALIZATION:
        # Check the full original schema before the one approved downgrade.
        # No other invalid location/time value or observation is repaired.
        team_scene.contract()[2].validate(answer)
        require(answer['candidate_time_s'] is None
                and answer['location']['camera_id'] is None
                and answer['location']['registered_location'] is None
                and answer['location']['road_type'] == 'highway',
                'report_recovery_exact_unregistered_road_required')
        answer['location']['road_type'] = 'unknown'
    if normalization == PERSON_EVIDENCE_NORMALIZATION:
        # Preserve the model's statement as an explicitly unverified warning.
        # Unknown is not absence; it prevents an unlinked signal from driving
        # an evidence-backed agency conclusion. Never invent an evidence ID.
        team_scene.contract()[2].validate(answer)
        require(answer['people']['person_on_road'] == PERSON_EVIDENCE_CORRECTION['before'],
                'report_recovery_exact_person_evidence_gap_required')
        answer['people']['person_on_road'] = deepcopy(PERSON_EVIDENCE_CORRECTION['after'])
        answer['description'] += '\n' + PERSON_EVIDENCE_WARNING
        answer['uncertainties'].append(PERSON_EVIDENCE_WARNING)
    event = team_scene.validate_event(answer, event_id)
    details = {'removed_annotation': removed}
    if normalization == ROAD_NORMALIZATION:
        details.update(corrections=[deepcopy(ROAD_CORRECTION)], other_observation_values_changed=False)
    if normalization == PERSON_EVIDENCE_NORMALIZATION:
        details.update(corrections=[deepcopy(PERSON_EVIDENCE_CORRECTION)],
            warnings=[PERSON_EVIDENCE_WARNING], appended_warning_fields=['description', 'uncertainties'],
            other_observation_values_changed=False)
    return event, details


def normalized_event(raw, event_id, *, normalization=NORMALIZATION):
    return _normalized_event(raw, event_id, normalization)[0]


def inspect_response(inputs, output, *, normalization=NORMALIZATION):
    output = Path(output)
    request, raw, failure = (_read(output / name) for name in ORIGINAL_FILES)
    require(failure.get('analysis_id') == request.get('analysis_id')
            and failure.get('status') == 'failed' and failure.get('stage') == 'team_vlm'
            and failure.get('request_attempts') == 1 and type(failure.get('request_attempts')) is int
            and failure.get('automatic_retry') is False
            and failure.get('provider_completion') == 'response_saved',
            'report_recovery_confirmed_saved_response_required')
    data, expected = team_scene.prepare(inputs, request['model'], request['video_sample_fps'],
        response_format_version=request.get('response_format_version'))
    require(set(request) == set(expected) and request['schema_version'] == team_scene.REQUEST_SCHEMA
            and str(uuid.UUID(request['analysis_id'])) == request['analysis_id'],
            'report_recovery_request_metadata_changed')
    require(all(request[key] == expected[key] for key in set(expected) - {'analysis_id', 'created_at'})
            and request['request_sha256'] == scene.sha(data), 'report_recovery_request_identity_changed')
    event = normalized_event(raw, request['team_event_id'], normalization=normalization)
    hashes = {name: file_sha(output / name) for name in ORIGINAL_FILES}
    return request, raw, failure, event, hashes


def normalize_saved_response(inputs, output, *, execution_id, source_task_id, plan_sha256,
                             normalization=NORMALIZATION):
    checked_id(execution_id); checked_id(source_task_id)
    require(hour_session.valid_sha(plan_sha256), 'report_recovery_plan_hash_required')
    output = Path(output)
    for name in ('event-v2.json', 'result.json', 'normalization.json'):
        require(not (output / name).exists() and not (output / name).is_symlink(),
                'report_recovery_existing_derived_output_preserved')
    request, raw, failure, event, hashes = inspect_response(inputs, output, normalization=normalization)
    _, details = _normalized_event(raw, request['team_event_id'], normalization)
    provenance = dict(schema=normalization, execution_id=execution_id, source_task_id=source_task_id,
        plan_sha256=plan_sha256, original_file_sha256=hashes,
        **details, new_generation_calls=0,
        original_failure_finished_at=failure['finished_at'],
        normalization_code_sha256=file_sha(Path(__file__)))
    result = team_scene._result(request, event, raw, None, provenance=provenance)
    digest = team_scene.validate_result(result)
    receipt = dict(provenance, event_sha256=fingerprint(event), result_sha256=digest,
                   analysis_id=result['analysis_id'], request_sha256=request['request_sha256'])
    write_new(output / 'event-v2.json', event)
    write_new(output / 'result.json', result)
    write_new(output / 'normalization.json', receipt)
    return validate_saved_output(inputs, output)


def validate_saved_output(inputs, output):
    """Read-only derivation proof; the original response is always rechecked."""
    output = Path(output)
    receipt = _read(output / 'normalization.json')
    normalization = receipt.get('schema')
    require(normalization in NORMALIZATIONS, 'report_recovery_normalization_receipt_changed')
    request, raw, failure, event, hashes = inspect_response(inputs, output, normalization=normalization)
    _, details = _normalized_event(raw, request['team_event_id'], normalization)
    result, stored = _read(output / 'result.json'), _read(output / 'event-v2.json')
    require(all(receipt.get(key) == value for key, value in details.items())
            and receipt.get('new_generation_calls') == 0
            and hour_session.valid_sha(receipt.get('normalization_code_sha256'))
            and receipt.get('original_file_sha256') == hashes,
            'report_recovery_normalization_receipt_changed')
    for key in ('execution_id', 'source_task_id'):
        checked_id(receipt[key])
    require(hour_session.valid_sha(receipt.get('plan_sha256'))
            and receipt['original_failure_finished_at'] == failure['finished_at']
            and result['generation_origin'] == 'operating_generation'
            and result['provenance'] == {key: value for key, value in receipt.items()
                if key not in ('event_sha256', 'result_sha256', 'analysis_id', 'request_sha256')}
            and result['elapsed_seconds'] is None
            and result['response_sha256'] == scene.sha(scene.encoded(raw))
            and receipt['request_sha256'] == request['request_sha256']
            and receipt['analysis_id'] == request['analysis_id'] == result['analysis_id']
            and all(result[key] == value for key, value in request.items() if key != 'schema_version')
            and event == stored == result['team_event']
            and receipt['event_sha256'] == fingerprint(event)
            and receipt['result_sha256'] == team_scene.validate_result(result),
            'report_recovery_derived_observation_changed')
    return result


def _demo_source(payload, marker, row, task, directory, input_sha256, *, normalization=ROAD_NORMALIZATION):
    """Bind preserved demo history, including an expired or stopped window.

    This separate, explicitly approved CPU recovery never calls active(),
    claim(), or write(): it cannot revive the demo or grant a model call.
    All original settings, bindings, and historical claim times stay strict.
    """
    hour_session.validate(payload, expected_marker=marker)
    wid = checked_id(row['worker_job_id'])
    state = row['state']
    binding = payload['bindings'].get(wid)
    require(binding is not None and binding.get('version') == task['version']
            and _version_allowed(task['version'], normalization)
            and binding['settings'] == task['generation_settings']
            and binding['reference_date'] == task['reference_date'] is None,
            'report_recovery_demo_binding_changed')
    _, expected = hour_session.row_binding(row, task['generation_settings'], None, policy=payload)
    expected['event_ids'] = binding['event_ids']
    require(binding == expected and binding['input_sha256'] == input_sha256
            and isinstance(binding['event_ids'], list) and len(binding['event_ids']) == 1,
            'report_recovery_demo_binding_changed')
    event_id = binding['event_ids'][0]
    require(state.get('worker_job_id') == wid and state.get('status') == 'handoff_ready'
            and not state.get('error_code') and row.get('submit_attempts') == state.get('post_attempts') == 1
            and hour_session.handoff(row, policy=payload) == [event_id],
            'report_recovery_completed_demo_gpu_required')
    checked_id(state.get('provider_job_id'))
    files = ('input.mp4', 'handoff.json', 'normalized-result.json', 'model-binding.json',
             f'scene-{event_id}.json', f'scenes/{event_id}/input/manifest.json',
             f'scenes/{event_id}/input/fetch.complete.json')
    require(file_sha(directory / 'input.mp4') == input_sha256
            and read_json(directory / 'handoff.json') == row.get('handoff'),
            'report_recovery_demo_evidence_changed')
    normalized = read_json(directory / 'normalized-result.json')
    require(normalized.get('input_sha256') == input_sha256
            and normalized.get('version') == task['version'] and normalized.get('status') == 'completed',
            'report_recovery_demo_gpu_result_changed')
    for name in files:
        file_sha(directory / name)  # Plain immutable source files are required.
    return event_id, files


def _source(conn, store, *, worker_job_id, task_id, session_name, execution_id,
            input_sha256, request_sha256, response_file_sha256, failure_file_sha256,
            normalization=NORMALIZATION):
    require(normalization in NORMALIZATIONS, 'report_recovery_normalization_not_supported')
    for value in (worker_job_id, task_id, execution_id):
        checked_id(value)
    require(uuid.UUID(hex=execution_id).version == 4 and execution_id != task_id,
            'report_recovery_new_execution_uuid_required')
    rows = [row for row in store.rows() if row['worker_job_id'] == worker_job_id]
    require(len(rows) == 1, 'report_recovery_source_not_unique')
    row = rows[0]
    task = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE task_id=%s', (task_id,)).fetchone()
    require(task is not None and task['worker_job_id'] == worker_job_id
            and task['purpose'] == 'full_report' and _version_allowed(task['version'], normalization)
            and task['paused'] is True and task['finished_at'] is None and task.get('error_code'),
            'report_recovery_paused_selected_report_required')
    selection_jobs.validate(task, row)  # New CPU work still uses today's exact contract.
    directory = hour_session.marker_path().parent / checked_id(row['directory_id'])
    selection_jobs.validate_stored(task, row, directory=directory)
    current = _read(hour_session.marker_path())
    require(current['name'] == session_name, 'report_recovery_source_session_not_selected')
    state = conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s', (session_name,)).fetchone()
    require(state is not None, 'report_recovery_report_session_required')
    if demo_session.selected(current):
        require(normalization in (ROAD_NORMALIZATION, PERSON_EVIDENCE_NORMALIZATION),
                'report_recovery_demo_normalization_required')
        event_id, source_files = _demo_source(state['payload'], current, row, task, directory,
            input_sha256, normalization=normalization)
    else:
        require(report_continuation.selected(current), 'report_recovery_report_session_required')
        report_continuation.validate_state(state['payload'], current)
        report_continuation.check_origin(conn, current)
        report_continuation.check_row(current, row)
        event_id = current['source']['event_id']
        source_files = current['source']['files']
        require(current['source']['worker_job_id'] == worker_job_id
                and current['source']['input_sha256'] == input_sha256
                and current['generation_settings'] == task['generation_settings'],
                'report_recovery_approved_source_changed')
    claims = [claim for claim in state['payload']['claims'] if claim['worker_job_id'] == worker_job_id]
    require(len(claims) == 1 and claims[0]['kind'] == 'scene'
            and claims[0]['event_id'] == event_id and claims[0]['request_sha256'] == request_sha256,
            'report_recovery_one_scene_and_no_cpu_required')
    scene_state = row['state'].get('scene_events', {}).get(event_id, {})
    require(scene_state.get('status') == 'description_failed'
            and scene_state.get('gemini_api_calls') == 1 and not scene_state.get('analysis_id'),
            'report_recovery_failed_scene_required')
    output = directory / 'scenes' / event_id / 'description-team-v1'
    inputs = output.parent / 'input'
    request, raw, failure, event, hashes = inspect_response(inputs, output, normalization=normalization)
    require(request['request_sha256'] == request_sha256
            and hashes['response.raw.json'] == response_file_sha256
            and hashes['failure.json'] == failure_file_sha256
            and request['input_sha256'] == input_sha256
            and request['source']['analysis_profile'] == row['state']['analysis_profile']
            and request['source']['gpu_key'] == row['state']['gpu_key'],
            'report_recovery_reviewed_response_changed')
    for target in (output / 'result.json', output / 'event-v2.json', output / 'normalization.json',
                   output.parent / 'report-team-v1', directory / 'report-recovery.json',
                   directory / 'report-recovery-intent.json', directory / 'mlflow-outbox' / execution_id,
                   directory / ('trace-' + execution_id + '-' + event_id + '.json')):
        require(not target.exists() and not target.is_symlink(), 'report_recovery_existing_output_no_retry')
    for table, column, value in (('cctv_scene.team_descriptions', 'analysis_id', request['analysis_id']),
                                 ('cctv_scene.team_report_runs', 'worker_job_id', worker_job_id)):
        found = conn.execute('SELECT EXISTS(SELECT 1 FROM ' + table + ' WHERE ' + column + '=%s) AS found',
                             (value,)).fetchone()
        require(not found['found'], 'report_recovery_existing_database_output_preserved')
    busy = conn.execute('SELECT EXISTS(SELECT 1 FROM cctv_jobs.analysis_tasks WHERE NOT paused AND finished_at IS NULL) '
        'OR EXISTS(SELECT 1 FROM cctv_jobs.flows WHERE NOT paused AND finished_at IS NULL) AS busy').fetchone()
    require(not busy['busy'], 'report_recovery_active_work_preserved')
    require(read_json(directory / 'state.json') == row['state'], 'report_recovery_spool_state_changed')
    from .team_model import preflight
    require(os.environ.get('RAG_MODEL_PATH'), 'report_recovery_local_e5_required')
    preflight(os.environ['RAG_MODEL_PATH'])
    preserved = {name: file_sha(directory / name) for name in source_files}
    preserved['state.json'] = file_sha(directory / 'state.json')
    preserved['task-' + task_id + '.json'] = file_sha(directory / ('task-' + task_id + '.json'))
    for name in ORIGINAL_FILES:
        preserved[str((output / name).relative_to(directory))] = file_sha(output / name)
    for path in list(directory.glob('trace-' + task_id + '-*.json')) + list(directory.glob('mlflow-' + task_id + '.json')):
        preserved[str(path.relative_to(directory))] = file_sha(path)
    prior_tracking = directory / 'mlflow-outbox' / task_id
    source_failed_run_id = source_trace_id = None
    if prior_tracking.exists():
        for path in prior_tracking.glob('*.json'):
            preserved[str(path.relative_to(directory))] = file_sha(path)
        prior = _read(prior_tracking / 'receipt.json')
        observation_sha = prior.get('recorded_observation')
        require(hour_session.valid_sha(observation_sha), 'report_recovery_failed_tracking_unconfirmed')
        observation = _read(prior_tracking / (observation_sha + '.json'))
        require(mlflow_tracking._hash(observation) == observation_sha
                and observation['execution_id'] == task_id and observation['worker_job_id'] == worker_job_id
                and observation['outcome'] == 'failed', 'report_recovery_failed_tracking_changed')
        source_failed_run_id = checked_id(prior['run_id'])
    prior_trace = directory / ('trace-' + task_id + '-' + event_id + '.json')
    if prior_trace.exists():
        trace = _read(prior_trace)
        require(trace['identity']['execution_id'] == task_id and trace['status'] == 'ERROR',
                'report_recovery_failed_trace_changed')
        source_trace_id = trace.get('delivery', {}).get('trace_id')
    summary = dict(schema=SCHEMA, execution_id=execution_id, worker_job_id=worker_job_id,
        source_task_id=task_id, source_session=session_name, source_session_sha256=fingerprint(state['payload']),
        source_task_sha256=fingerprint(canonical(task)), source_queue_sha256=fingerprint(canonical(row)),
        marker_file_sha256=file_sha(hour_session.marker_path()), input_sha256=input_sha256,
        event_id=event_id, analysis_id=request['analysis_id'], request_sha256=request_sha256,
        response_file_sha256=response_file_sha256, failure_file_sha256=failure_file_sha256,
        normalized_event_sha256=fingerprint(event), preserved_files=preserved,
        code_sha256=file_sha(Path(__file__)), max_new_gpu_calls=0, max_new_scene_calls=0,
        source_failed_run_id=source_failed_run_id, source_trace_id=source_trace_id,
        max_cpu_retrievals=1, cpu_budget_seconds=CPU_SECONDS, automatic_retry=False,
        generation_settings=task['generation_settings'], normalization=normalization)
    return dict(summary, plan_sha256=fingerprint(summary)), (row, task, state['payload'], directory, inputs, output)


def plan(conn, store, **parameters):
    return _source(conn, store, **parameters)[0]


def _preserved(conn, context, approved):
    row, task, original_session, directory, inputs, output = context
    now_task = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE task_id=%s', (task['task_id'],)).fetchone()
    now_row = conn.execute('SELECT * FROM cctv_jobs.queue WHERE worker_job_id=%s', (row['worker_job_id'],)).fetchone()
    now_session = conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s', (approved['source_session'],)).fetchone()
    # Store.rows() adds a volatile convenience field, not a stored queue column.
    old_row = {key: value for key, value in row.items() if key != 'ready'}
    require(canonical(now_task) == canonical(task) and canonical(now_row) == canonical(old_row)
            and now_session['payload'] == original_session
            and file_sha(hour_session.marker_path()) == approved['marker_file_sha256'],
            'report_recovery_original_database_state_changed')
    for name, expected in approved['preserved_files'].items():
        require(file_sha(directory / name) == expected, 'report_recovery_original_file_changed')


def _store_report(conn, result, payload, task, execution_id):
    from psycopg.types.json import Jsonb
    from .team_jobs import VERSION
    require(_version_allowed(task['version'], result['provenance']['schema'])
            and result['source'].get('version', 'R2') == task['version'],
            'report_recovery_store_model_changed')
    receipt = team_db.store(conn, result)
    require(receipt.get('committed') is True, 'report_recovery_scene_store_unverified')
    settings = dict(version=VERSION, pipeline_id=team_settings.PIPELINE, report_id=payload['report_id'],
        analysis_id=result['analysis_id'], worker_job_id=task['worker_job_id'], scene_sha256=team_scene.validate_result(result),
        reference_date=None, document_reference_date_supported=False, model='cpu-template',
        contract=team_settings.contract(task['version']), recovery_execution_id=execution_id)
    progress = dict(status='report_stored', error_code=None, result=payload,
        result_sha256=fingerprint(payload), generation_calls=0, embedding_calls=0,
        calls={'cpu_retrieval': 1}, recovery_execution_id=execution_id)
    conn.execute('''INSERT INTO cctv_scene.team_report_runs
        (report_id,analysis_id,worker_job_id,settings,state) VALUES (%s,%s,%s,%s,%s)''',
        (payload['report_id'],result['analysis_id'],task['worker_job_id'],Jsonb(settings),Jsonb(progress)))
    stored = conn.execute('SELECT settings,state FROM cctv_scene.team_report_runs WHERE report_id=%s',
                          (payload['report_id'],)).fetchone()
    require(stored is not None and canonical(stored['settings']) == canonical(settings)
            and canonical(stored['state']) == canonical(progress)
            and canonical(team_db.show(conn, result['analysis_id'])) == canonical(result),
            'report_recovery_database_readback_failed')
    return settings, progress


def _tracking_derivation(result):
    """Project the reviewed correction into metadata, never relax its boundary."""
    provenance = result['provenance']
    corrections = provenance.get('corrections', [])
    warnings = []
    if provenance['schema'] == PERSON_EVIDENCE_NORMALIZATION:
        warnings = provenance.get('warnings')
        require(corrections == [PERSON_EVIDENCE_CORRECTION]
                and warnings == [PERSON_EVIDENCE_WARNING]
                and provenance.get('appended_warning_fields') == ['description', 'uncertainties'],
                'report_recovery_tracking_correction_changed')
    if corrections:
        expected = PERSON_EVIDENCE_CORRECTION if provenance['schema'] == PERSON_EVIDENCE_NORMALIZATION else ROAD_CORRECTION
        require(provenance['schema'] in (ROAD_NORMALIZATION, PERSON_EVIDENCE_NORMALIZATION)
                and corrections == [expected],
                'report_recovery_tracking_correction_changed')
        # JSON Pointer stays in the immutable source provenance and trace. The
        # metadata API rejects leading-slash strings as potential host paths.
        corrections = [dict(field=expected['path'].lstrip('/').replace('/', '.'), **{key: deepcopy(expected[key])
            for key in ('before', 'after', 'reason')})]
    warning_config = {'warnings': warnings} if warnings else {}
    return dict(normalization=provenance['schema'], corrections=corrections, **warning_config)


def _tracking_record(directory, receipt, task, approved, result):
    return mlflow_tracking.execution_record(worker_job_id=receipt['worker_job_id'],
        execution_id=receipt['execution_id'],
        model_binding=mlflow_tracking.validate_binding(read_json(Path(directory) / 'model-binding.json')),
        input_sha256=receipt['input_sha256'],
        config={'operation':'saved_response_cpu_report_recovery', 'source_task_id':task['task_id'],
            'plan_sha256':approved['plan_sha256'], 'generation_settings':task['generation_settings'],
            **_tracking_derivation(result)},
        outcome='succeeded', result_refs={'purpose':'report_recovery','new_model_calls':0,
            'cpu_retrieval_calls':1,'source_task_id':task['task_id'],'analysis_id':receipt['analysis_id'],
            'source_failed_run_id':approved['source_failed_run_id'],'source_trace_id':approved['source_trace_id'],
            'report_id':receipt['report_id'],'result_sha256':receipt['result_sha256']},
        durations={'pipeline_seconds':receipt['pipeline_seconds']},
        started_at_ms=receipt['started_at_ms'],finished_at_ms=receipt['finished_at_ms'])


def apply(conn, store, parameters, approved_plan_sha256):
    """A separate fixed-input CPU attempt; never resumes the failed task."""
    from .team_jobs import envelope, validate_report, VERSION
    from apps.team_rag.runtime import build_result
    with ExitStack() as locks:
        for kind in ('admission', 'dispatch', 'flow', 'collect'):
            locks.enter_context(store.lock(kind))
        approved, context = _source(conn, store, **parameters)
        require(approved['plan_sha256'] == approved_plan_sha256, 'report_recovery_reviewed_plan_changed')
        row, task, original_session, directory, inputs, output = context
        locks.enter_context(locked(directory))
        started = time.time()
        write_new(directory / 'report-recovery-intent.json', dict(status='cpu_attempt_reserved',
            approved_plan=approved, started_at=started, deadline_at=started + CPU_SECONDS,
            cpu_attempts=1, new_model_calls=0, automatic_retry=False))
        contract = row['state']['analysis_profile']['classifier']
        collector = mlflow_tracing.Collector(dict(worker_job_id=row['worker_job_id'],
            execution_id=approved['execution_id'], event_id=approved['event_id'],
            input_sha256=approved['input_sha256'], model_id=contract['model_id'], model_version=contract['model_version']))
        trace_path = directory / ('trace-' + approved['execution_id'] + '-' + approved['event_id'] + '.json')
        try:
            with collector.stage('saved_response_normalization', reused=True, new_model_calls=0) as trace:
                result = normalize_saved_response(inputs, output, execution_id=approved['execution_id'],
                    source_task_id=task['task_id'], plan_sha256=approved_plan_sha256,
                    normalization=approved.get('normalization', NORMALIZATION))
                trace.output(normalization=result['provenance']['schema'],
                    corrections=result['provenance'].get('corrections', []),
                    warnings=result['provenance'].get('warnings', []),
                    original_response_sha256=result['response_sha256'],
                    scene_json=result['team_event'])
            with collector.stage('rag_search_relevance_template', 'RETRIEVER', new_model_calls=0) as trace:
                answer = build_result(result['team_event'], deadline_at=started + CPU_SECONDS)
                trace.output(**collector.report_summary(answer['report_json'],
                    report_markdown=answer.get('report_markdown')))
            report_id = str(uuid.uuid5(uuid.NAMESPACE_URL, VERSION + '/' + result['analysis_id']))
            with collector.stage('report_storage', analysis_id=result['analysis_id'], report_id=report_id) as trace:
                validate_report(answer, result['team_event'])
                payload = envelope(answer, result, report_id, row['worker_job_id'])
                report_dir = output.parent / 'report-team-v1'
                report_dir.mkdir(mode=0o700)
                write_new(report_dir / 'report.json', payload['report_json'])
                write_new(report_dir / 'result.json', payload)
                markdown = report_dir / 'report.md'
                fd = os.open(markdown, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'w') as stream:
                    stream.write(payload['report_markdown']); stream.flush(); os.fsync(stream.fileno())
                with conn.transaction():
                    settings, progress = _store_report(conn, result, payload, task, approved['execution_id'])
                write_new(report_dir / 'state.json', dict(progress, settings=settings))
                trace.output(report_id=report_id, analysis_id=result['analysis_id'],
                             result_sha256=fingerprint(payload), status='report_stored',
                             report_markdown=payload['report_markdown'])
            _preserved(conn, context, approved)
            receipt = {key: approved[key] for key in ('schema','execution_id','worker_job_id','input_sha256',
                'source_task_id','source_session','source_session_sha256','plan_sha256','event_id','analysis_id',
                'source_failed_run_id','source_trace_id')}
            receipt.update(status='report_stored', report_id=report_id, result_sha256=fingerprint(payload),
                scene_sha256=team_scene.validate_result(result), new_model_calls=0, cpu_retrieval_calls=1,
                source_response_sha256=approved['response_file_sha256'], started_at_ms=int(started * 1000),
                finished_at_ms=int(time.time() * 1000), pipeline_seconds=time.time() - started,
                original_failed_task_preserved=True, automatic_retry=False,
                duration_scope='single_recovery_attempt', storage_attempt_id=None)
            write_new(directory / 'report-recovery.json', receipt)
        except BaseException as exc:
            collector.status = 'ERROR'
            write_new(directory / 'report-recovery-failure.json', dict(schema=SCHEMA,
                execution_id=approved['execution_id'], plan_sha256=approved_plan_sha256,
                error_code=getattr(exc,'code','report_recovery_failed_inspect_receipts'),
                new_model_calls=0, cpu_attempts=1, automatic_retry=False))
            raise
        finally:
            record = dict(schema=mlflow_tracing.SCHEMA, identity=collector.identity,
                started_ns=collector.started_ns, ended_ns=time.time_ns(), status=collector.status,
                spans=collector.spans, delivery={'status':'pending','trace_id':None,'attempted':False})
            write_new(trace_path, bounded_record(record))
        try:
            tracked = _tracking_record(directory, receipt, task, approved, result)
            delivered = mlflow_tracking.record_execution(directory, tracked,
                tracking_uri=os.environ.get('BE_MLFLOW_TRACKING_URI'))
            if delivered.get('status') == 'recorded':
                mlflow_tracing.link_run(directory, receipt['execution_id'], delivered['run_id'])
        except Exception:
            delivered = {'status':'pending','run_id':None}
        return dict(receipt, mlflow=delivered)


def retry_tracking(conn, store, worker_job_id):
    """Deliver a completed single-attempt recovery's metadata only; no CPU work."""
    checked_id(worker_job_id)
    with ExitStack() as locks:
        for kind in ('admission', 'dispatch', 'flow', 'collect'):
            locks.enter_context(store.lock(kind))
        record = status(conn, store, worker_job_id)
        require(record['duration_scope'] == 'single_recovery_attempt'
                and record['storage_attempt_id'] is None, 'report_recovery_tracking_scope_changed')
        rows = [row for row in store.rows() if row['worker_job_id'] == worker_job_id]
        require(len(rows) == 1, 'report_recovery_source_not_unique')
        row = rows[0]
        directory = hour_session.marker_path().parent / checked_id(row['directory_id'])
        locks.enter_context(locked(directory))
        approved = _read(directory / 'report-recovery-intent.json')['approved_plan']
        require(fingerprint(canonical(row)) == approved['source_queue_sha256']
                and file_sha(hour_session.marker_path()) == approved['marker_file_sha256'],
                'report_recovery_original_database_state_changed')
        task = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE task_id=%s',
                            (record['source_task_id'],)).fetchone()
        saved_session = conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',
                                     (record['source_session'],)).fetchone()
        output = directory / 'scenes' / record['event_id'] / 'description-team-v1'
        inputs = output.parent / 'input'
        context = (row, task, saved_session['payload'], directory, inputs, output)
        _preserved(conn, context, approved)
        result = validate_saved_output(inputs, output)
        tracked = _tracking_record(directory, record, task, approved, result)
        delivered = mlflow_tracking.record_execution(directory, tracked,
            tracking_uri=os.environ.get('BE_MLFLOW_TRACKING_URI'))
        if delivered.get('status') == 'recorded':
            mlflow_tracing.link_run(directory, record['execution_id'], delivered['run_id'])
        _preserved(conn, context, approved)
        return dict(status(conn, store, worker_job_id), mlflow=delivered, additional_cpu_retrievals=0)


def load_recovery(directory):
    directory = Path(directory)
    path = directory / 'report-recovery.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _read(path)
    require(set(value) == RECEIPT_FIELDS and value.get('schema') == SCHEMA and value.get('status') == 'report_stored'
            and value.get('new_model_calls') == 0 and value.get('cpu_retrieval_calls') == 1,
            'report_recovery_receipt_invalid')
    checked_id(value['execution_id']); checked_id(value['source_task_id']); checked_id(value['worker_job_id'])
    require(re.fullmatch(r'event_[0-9]+',value['event_id'])
            and re.fullmatch(r'(?:mlops|demo)-session-[0-9]{8}-[0-9]{2}',value['source_session'])
            and all(hour_session.valid_sha(value[key]) for key in ('input_sha256','source_session_sha256',
                'plan_sha256','result_sha256','scene_sha256','source_response_sha256')),
            'report_recovery_identity_invalid')
    for key in ('analysis_id','report_id'):
        require(str(uuid.UUID(value[key])) == value[key], 'report_recovery_result_id_invalid')
    if value['source_failed_run_id'] is not None:
        checked_id(value['source_failed_run_id'])
    require(value['source_trace_id'] is None or re.fullmatch(r'[A-Za-z0-9_.-]{1,160}',value['source_trace_id']),
            'report_recovery_trace_id_invalid')
    output = directory / 'scenes' / value['event_id'] / 'description-team-v1'
    result = validate_saved_output(output.parent / 'input', output)
    payload = _read(output.parent / 'report-team-v1' / 'result.json')
    from .team_jobs import validate_report
    validate_report(payload, result['team_event'])
    normalization = _read(output / 'normalization.json')
    require(not value['source_session'].startswith('demo-session-')
            or normalization.get('schema') in (ROAD_NORMALIZATION, PERSON_EVIDENCE_NORMALIZATION),
            'report_recovery_demo_normalization_required')
    require(_version_allowed(result['source'].get('version', 'R2'), normalization['schema']),
            'report_recovery_result_model_changed')
    require(value['analysis_id'] == result['analysis_id'] == payload['analysis_id']
            and value['input_sha256'] == result['input_sha256']
            and value['scene_sha256'] == team_scene.validate_result(result) == payload['scene_sha256']
            and value['report_id'] == payload['report_id'] and value['worker_job_id'] == payload['worker_job_id']
            and value['result_sha256'] == fingerprint(payload)
            and all(value[key] == normalization[key] for key in ('execution_id','source_task_id','plan_sha256')),
            'report_recovery_result_identity_changed')
    return {key:value[key] for key in RECEIPT_FIELDS}


def tracking_status(directory, record):
    try:
        root = Path(directory) / 'mlflow-outbox' / checked_id(record['execution_id'])
        receipt = _read(root / 'receipt.json')
        digest = receipt['recorded_observation']
        require(hour_session.valid_sha(digest), 'report_recovery_tracking_invalid')
        value = _read(root / (digest + '.json'))
        require(mlflow_tracking._hash(value) == digest and receipt['status'] == 'recorded'
                and value['execution_id'] == record['execution_id']
                and value['worker_job_id'] == record['worker_job_id']
                and value['result_refs']['purpose'] == 'report_recovery'
                and value['result_refs']['result_sha256'] == record['result_sha256'],
                'report_recovery_tracking_changed')
        return {'status':'recorded','run_id':checked_id(receipt['run_id'])}
    except (OSError,ValueError,KeyError,TypeError,WorkerError):
        return {'status':'pending','run_id':None}


def _storage_source(conn, store, worker_job_id, storage_attempt_id):
    """A saved CPU result may be stored once; this path never calls build_result."""
    checked_id(worker_job_id); checked_id(storage_attempt_id)
    require(uuid.UUID(hex=storage_attempt_id).version == 4, 'report_recovery_storage_uuid_required')
    rows = [row for row in store.rows() if row['worker_job_id'] == worker_job_id]
    require(len(rows) == 1, 'report_recovery_source_not_unique')
    row = rows[0]
    directory = hour_session.marker_path().parent / checked_id(row['directory_id'])
    intent = _read(directory / 'report-recovery-intent.json')
    approved = intent['approved_plan']
    require(approved['schema'] == SCHEMA and intent['status'] == 'cpu_attempt_reserved'
            and intent['cpu_attempts'] == 1 and intent['new_model_calls'] == 0
            and approved['plan_sha256'] == fingerprint({k:v for k,v in approved.items() if k != 'plan_sha256'})
            and approved['worker_job_id'] == worker_job_id
            and approved['max_new_gpu_calls'] == approved['max_new_scene_calls'] == 0
            and approved['max_cpu_retrievals'] == 1,
            'report_recovery_original_cpu_approval_changed')
    failure = _read(directory / 'report-recovery-failure.json')
    require(failure['execution_id'] == approved['execution_id']
            and failure['plan_sha256'] == approved['plan_sha256']
            and failure['error_code'] == 'report_recovery_database_readback_failed'
            and failure['cpu_attempts'] == 1 and failure['new_model_calls'] == 0,
            'report_recovery_storage_failure_required')
    for target in ('report-recovery.json','report-recovery-storage-intent.json'):
        require(not (directory/target).exists() and not (directory/target).is_symlink(),
                'report_recovery_storage_already_attempted_no_retry')
    require(not (directory/'mlflow-outbox'/approved['execution_id']).exists(),
            'report_recovery_existing_run_preserved')
    task = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE task_id=%s',
                        (approved['source_task_id'],)).fetchone()
    saved_session = conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',
                                 (approved['source_session'],)).fetchone()
    require(task is not None and saved_session is not None
            and fingerprint(canonical(task)) == approved['source_task_sha256']
            and fingerprint(canonical(row)) == approved['source_queue_sha256']
            and fingerprint(saved_session['payload']) == approved['source_session_sha256'],
            'report_recovery_original_database_state_changed')
    selection_jobs.validate(task,row)
    output = directory/'scenes'/approved['event_id']/'description-team-v1'
    inputs = output.parent/'input'
    context = (row,task,saved_session['payload'],directory,inputs,output)
    _preserved(conn,context,approved)
    result = validate_saved_output(inputs,output)
    normalization = _read(output/'normalization.json')
    require(normalization['execution_id'] == approved['execution_id']
            and normalization['plan_sha256'] == approved['plan_sha256']
            and normalization['source_task_id'] == task['task_id'], 'report_recovery_normalization_approval_changed')
    folder = output.parent/'report-team-v1'
    payload = _read(folder/'result.json')
    from .team_jobs import validate_report, envelope, VERSION
    validate_report(payload,result['team_event'])
    report_id = str(uuid.uuid5(uuid.NAMESPACE_URL,VERSION+'/'+result['analysis_id']))
    answer = {key:payload[key] for key in ('pipeline_id','report_json','report_markdown')}
    require(payload == canonical(envelope(answer,result,report_id,worker_job_id))
            and _read(folder/'report.json') == payload['report_json']
            and (folder/'report.md').read_text() == payload['report_markdown']
            and not (folder/'state.json').exists(), 'report_recovery_saved_cpu_result_changed')
    for table,column,key in (('cctv_scene.team_descriptions','analysis_id',result['analysis_id']),
                             ('cctv_scene.team_report_runs','worker_job_id',worker_job_id)):
        found = conn.execute('SELECT EXISTS(SELECT 1 FROM '+table+' WHERE '+column+'=%s) AS found',(key,)).fetchone()
        require(not found['found'],'report_recovery_existing_database_output_preserved')
    original_trace = directory/('trace-'+approved['execution_id']+'-'+approved['event_id']+'.json')
    trace = _read(original_trace)
    require(trace['status'] == 'ERROR' and trace['identity']['execution_id'] == approved['execution_id']
            and not trace['delivery'].get('attempted')
            and sum(s['name'] == 'rag_search_relevance_template' and s['status'] == 'OK' for s in trace['spans']) == 1,
            'report_recovery_saved_cpu_trace_changed')
    paths = [directory/'report-recovery-intent.json',directory/'report-recovery-failure.json',original_trace,
             *[output/name for name in ('event-v2.json','result.json','normalization.json')],
             *[folder/name for name in ('result.json','report.json','report.md')]]
    summary = dict(schema=SCHEMA,operation='saved_cpu_report_storage',storage_attempt_id=storage_attempt_id,
        execution_id=approved['execution_id'],worker_job_id=worker_job_id,source_task_id=task['task_id'],
        source_plan_sha256=approved['plan_sha256'],code_sha256=file_sha(Path(__file__)),
        result_sha256=fingerprint(payload),saved_files={str(p.relative_to(directory)):file_sha(p) for p in paths},
        additional_cpu_retrievals=0,additional_gpu_calls=0,additional_vlm_calls=0,automatic_retry=False)
    return dict(summary,plan_sha256=fingerprint(summary)),(approved,context,result,payload,trace)


def plan_storage(conn,store,worker_job_id,storage_attempt_id):
    return _storage_source(conn,store,worker_job_id,storage_attempt_id)[0]


def finalize_storage(conn,store,worker_job_id,storage_attempt_id,approved_plan_sha256):
    """Persist already computed files; preserve CPU failure evidence byte-for-byte."""
    with ExitStack() as locks:
        for kind in ('admission','dispatch','flow','collect'):
            locks.enter_context(store.lock(kind))
        plan, details = _storage_source(conn,store,worker_job_id,storage_attempt_id)
        require(plan['plan_sha256'] == approved_plan_sha256,'report_recovery_storage_plan_changed')
        approved,context,result,payload,old_trace = details
        row,task,session,directory,inputs,output = context
        locks.enter_context(locked(directory))
        started = time.time()
        write_new(directory/'report-recovery-storage-intent.json',dict(plan=plan,started_at=started,
            status='storage_attempt_reserved',additional_cpu_retrievals=0,new_model_calls=0,automatic_retry=False))
        collector = mlflow_tracing.Collector(old_trace['identity'])
        trace_path = directory/('trace-'+approved['execution_id']+'-'+approved['event_id']+'-storage-'+storage_attempt_id+'.json')
        try:
            with collector.stage('report_storage',analysis_id=result['analysis_id'],report_id=payload['report_id']) as trace:
                with conn.transaction():
                    settings,progress = _store_report(conn,result,payload,task,approved['execution_id'])
                write_new(output.parent/'report-team-v1/state.json',dict(progress,settings=settings))
                trace.output(report_id=payload['report_id'],analysis_id=result['analysis_id'],
                             result_sha256=fingerprint(payload),status='report_stored',new_model_calls=0,
                             report_markdown=payload['report_markdown'])
            _preserved(conn,context,approved)
            for name,digest in plan['saved_files'].items():
                require(file_sha(directory/name) == digest,'report_recovery_saved_cpu_file_changed')
            receipt = {key:approved[key] for key in ('schema','execution_id','worker_job_id','input_sha256',
                'source_task_id','source_session','source_session_sha256','plan_sha256','event_id','analysis_id',
                'source_failed_run_id','source_trace_id')}
            receipt.update(status='report_stored',report_id=payload['report_id'],result_sha256=fingerprint(payload),
                scene_sha256=team_scene.validate_result(result),new_model_calls=0,cpu_retrieval_calls=1,
                source_response_sha256=approved['response_file_sha256'],
                started_at_ms=old_trace['started_ns']//1000000,finished_at_ms=int(time.time()*1000),
                pipeline_seconds=(old_trace['ended_ns']-old_trace['started_ns'])/1e9 + time.time()-started,
                duration_scope='original_cpu_attempt_plus_storage_attempt_excluding_manual_gap',
                storage_attempt_id=storage_attempt_id,original_failed_task_preserved=True,automatic_retry=False)
            write_new(directory/'report-recovery.json',receipt)
        except BaseException as exc:
            collector.status='ERROR'
            write_new(directory/'report-recovery-storage-failure.json',dict(schema=SCHEMA,
                storage_attempt_id=storage_attempt_id,error_code=getattr(exc,'code','storage_failed_inspect_receipts'),
                new_model_calls=0,additional_cpu_retrievals=0,automatic_retry=False))
            raise
        finally:
            storage_trace = dict(schema=mlflow_tracing.SCHEMA,identity=collector.identity,
                started_ns=collector.started_ns,ended_ns=time.time_ns(),status=collector.status,
                spans=collector.spans,delivery={'status':'pending','trace_id':None,'attempted':False})
            storage_trace = bounded_record(storage_trace)
            write_new(trace_path,storage_trace)
        delivered={'status':'pending','run_id':None}
        try:
            record = mlflow_tracking.execution_record(worker_job_id=worker_job_id,execution_id=receipt['execution_id'],
                model_binding=mlflow_tracking.validate_binding(read_json(directory/'model-binding.json')),
                input_sha256=receipt['input_sha256'],config={'operation':'saved_response_cpu_report_recovery',
                    'source_task_id':task['task_id'],'plan_sha256':approved['plan_sha256'],
                    'generation_settings':task['generation_settings'],'duration_scope':receipt['duration_scope'],
                    **_tracking_derivation(result)},
                outcome='succeeded',result_refs={'purpose':'report_recovery','new_model_calls':0,'cpu_retrieval_calls':1,
                    'source_task_id':task['task_id'],'source_failed_run_id':approved['source_failed_run_id'],
                    'source_trace_id':approved['source_trace_id'],'analysis_id':receipt['analysis_id'],
                    'report_id':receipt['report_id'],'result_sha256':receipt['result_sha256'],
                    'storage_attempt_id':storage_attempt_id,'storage_additional_cpu_retrievals':0},
                durations={'pipeline_seconds':receipt['pipeline_seconds']},
                started_at_ms=receipt['started_at_ms'],finished_at_ms=receipt['finished_at_ms'])
            delivered=mlflow_tracking.record_execution(directory,record,
                tracking_uri=os.environ.get('BE_MLFLOW_TRACKING_URI'))
            if delivered.get('status') == 'recorded':
                ids=[]
                for label,trace_record in (('cpu',old_trace),('storage',storage_trace)):
                    base=directory/('report-recovery-trace-delivery-'+storage_attempt_id+'-'+label)
                    write_new(base.with_suffix('.intent.json'),dict(run_id=delivered['run_id'],
                        source_trace_sha256=fingerprint(trace_record),automatic_retry=False))
                    sent=mlflow_tracing.deliver(deepcopy(trace_record),delivered['run_id'])
                    write_new(base.with_suffix('.json'),sent)
                    if sent.get('status')=='recorded': ids.append(sent['trace_id'])
                if ids:
                    mlflow_tracking.client_for_uri(os.environ['BE_MLFLOW_TRACKING_URI']).set_tag(
                        delivered['run_id'],'cctv.trace_ids',','.join(ids))
        except Exception:
            pass  # Saved business results survive metadata delivery failure.
        return dict(receipt,mlflow=delivered,additional_cpu_retrievals=0)


def status(conn, store, worker_job_id):
    checked_id(worker_job_id)
    rows = [row for row in store.rows() if row['worker_job_id'] == worker_job_id]
    require(len(rows) == 1, 'report_recovery_source_not_unique')
    directory = hour_session.marker_path().parent / checked_id(rows[0]['directory_id'])
    record = load_recovery(directory)
    require(record is not None, 'report_recovery_not_completed')
    intent = _read(directory / 'report-recovery-intent.json')
    approved = intent['approved_plan']
    task = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE task_id=%s',
                        (record['source_task_id'],)).fetchone()
    saved_session = conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',
                                 (record['source_session'],)).fetchone()
    require(fingerprint(canonical(task)) == approved['source_task_sha256']
            and fingerprint(saved_session['payload']) == record['source_session_sha256'],
            'report_recovery_original_task_session_changed')
    for name, digest in approved['preserved_files'].items():
        require(file_sha(directory / name) == digest, 'report_recovery_original_file_changed')
    trace = _read(directory / ('trace-' + record['execution_id'] + '-' + record['event_id'] + '.json'))
    report = conn.execute('SELECT settings,state FROM cctv_scene.team_report_runs WHERE report_id=%s',
                          (record['report_id'],)).fetchone()
    require(report is not None and report['state']['result_sha256'] == record['result_sha256']
            and report['state']['status'] == 'report_stored', 'report_recovery_stored_report_changed')
    deliveries=[_read(path) for path in sorted(directory.glob('report-recovery-trace-delivery-*.json'))
                if not path.name.endswith('.intent.json')]
    return dict(record, mlflow=tracking_status(directory,record), trace=trace['delivery'],traces=deliveries,
        six_sections=sum(line.startswith('## ') for line in report['state']['result']['report_markdown'].splitlines()),
        original_source_files_preserved=True, original_failed_task_preserved=task['paused'] is True,
        additional_gpu_calls=0, additional_vlm_calls=0)
