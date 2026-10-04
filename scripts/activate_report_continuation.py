"""Plan one B report from completed GPU evidence; --apply grants new VLM work.

Default: read-only DB queries and local evidence checks, no network/model call.
--apply --approved-plan-sha256 <reviewed hash>: preserve the previous session,
activate one new 900-second admission window and admit one full_report task.
The existing worker can then make one scene call and one CPU retrieval. This
window is not a hard wall-clock timeout. No Pod URL/health/window is changed.
"""
import argparse
import base64
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.be_worker import analysis_profiles, flow_service, generation_settings, hour_session
from apps.be_worker import report_continuation as policy, selection_jobs, team_model, team_settings, mlops_jobs
from apps.be_worker.common import checked_id, read_json
from apps.be_worker.queue_store import Store, connect
from apps.scene_description import scene, team_scene
from scripts import activate_mlops_session as activation

require = hour_session.require
APPLY_INTENT = False


def inspect_source(conn, store, *, name, source_session, worker_job_id, event_id,
                   input_sha256, gpu_key, manifest_sha256, result_sha256):
    """Build a deterministic, reviewable plan. Never repairs or writes receipts."""
    checked_id(worker_job_id)
    rows = [r for r in store.rows() if r['worker_job_id'] == worker_job_id]
    require(len(rows) == 1, 'report_continuation_source_not_unique')
    row = rows[0]
    state = row['state']
    profile = analysis_profiles.profile('B')
    require(state.get('analysis_profile') == profile
            and state.get('input_sha256') == input_sha256 and state.get('gpu_key') == gpu_key
            and state.get('manifest_sha256') == manifest_sha256,
            'report_continuation_approved_source_mismatch')
    require(state.get('gemini_api_calls', 0) == 0 and all(
        item.get('gemini_api_calls', 0) == 0 and not item.get('analysis_id')
        for item in state.get('scene_events', {}).values()), 'report_continuation_existing_scene_preserved')
    require(hour_session.handoff(row) == [event_id], 'report_continuation_event_mismatch')
    tasks = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE worker_job_id=%s',
                         (worker_job_id,)).fetchall()
    require(len(tasks) == 1, 'report_continuation_existing_task_preserved')
    task = tasks[0]
    require(task['purpose'] == 'gpu_comparison' and task['status'] == 'comparison_completed'
            and task['finished_at'] is not None and not task['paused'] and not task.get('error_code'),
            'report_continuation_comparison_not_completed')
    selection_jobs.validate(task, row)
    directory = hour_session.marker_path().parent / checked_id(row['directory_id'])
    require(read_json(directory / 'state.json') == state, 'report_continuation_spool_state_changed')
    require(read_json(directory / ('task-' + checked_id(task['task_id']) + '.json')) ==
            selection_jobs.snapshot(task), 'report_continuation_task_receipt_changed')
    for folder in ('description', 'description-team-v1', 'report-team-v1'):
        path = directory / 'scenes' / event_id / folder
        require(not path.exists() and not path.is_symlink(), 'report_continuation_existing_output_preserved')
    for table in ('cctv_scene.team_descriptions', 'cctv_scene.team_report_runs',
                  'cctv_scene.descriptions', 'cctv_scene.report_runs'):
        column, identifier = (('provider_job_id', state['provider_job_id']) if table.endswith('descriptions')
                              else ('worker_job_id', worker_job_id))
        found = conn.execute('SELECT EXISTS(SELECT 1 FROM ' + table + ' WHERE ' + column + '=%s) AS found',
                             (identifier,)).fetchone()
        require(not found['found'], 'report_continuation_existing_generation_preserved')
    bundle = directory / 'scenes' / event_id / 'input'
    config, manifest, event, media, actual_manifest = scene.load_bundle(bundle)
    require(actual_manifest == manifest_sha256 and config == read_json(directory / f'scene-{event_id}.json')
            and config['expected_input_sha256'] == input_sha256
            and config['job_id'] == state['provider_job_id'] and config['event_id'] == event_id
            and config['analysis_profile'] == profile and config['gpu_key'] == gpu_key,
            'report_continuation_bundle_identity_changed')
    require(policy.file_sha(directory / 'normalized-result.json') == result_sha256,
            'report_continuation_result_changed')
    normalized = read_json(directory / 'normalized-result.json')
    require(normalized.get('input_sha256') == input_sha256 and normalized.get('version') == 'B'
            and normalized.get('status') == 'completed', 'report_continuation_result_not_successful')
    files = ['input.mp4', 'handoff.json', 'normalized-result.json', 'model-binding.json', f'scene-{event_id}.json']
    for path in sorted(bundle.rglob('*')):
        require(not path.is_symlink(), 'report_continuation_evidence_symlink')
        if path.is_file():
            files.append(str(path.relative_to(directory)))
    hashes = {f: policy.file_sha(directory / f) for f in files}
    require(read_json(directory / 'model-binding.json') == mlops_jobs.binding_for(profile),
            'report_continuation_model_binding_changed')
    require((directory / 'input.mp4').stat().st_size == state['input_bytes'],
            'report_continuation_input_size_changed')
    history = activation.preflight_db(conn, {'name': name}, hour_session.clock(conn))
    require(source_session in history, 'report_continuation_source_session_missing')
    require(hour_session.current_marker() == hour_session.marker_from_state(history[source_session]),
            'report_continuation_source_session_not_selected')
    for previous in history.values():
        require(not policy.selected(previous) or (
            previous['source']['worker_job_id'] != worker_job_id and previous['source']['gpu_key'] != gpu_key),
            'report_continuation_already_approved_no_new_window')
    require(team_settings.enabled(), 'report_continuation_team_pipeline_required')
    settings = generation_settings.from_environment(analysis_profile=profile)
    source = dict(session=source_session, session_sha256=policy.fingerprint(history[source_session]),
        worker_job_id=worker_job_id, comparison_task_id=task['task_id'], input_sha256=input_sha256,
        input_bytes=state['input_bytes'], gpu_key=gpu_key, provider_job_id=state['provider_job_id'],
        event_id=event_id, analysis_profile=profile, handoff_sha256=policy.fingerprint(row['handoff']),
        manifest_sha256=manifest_sha256, result_sha256=result_sha256, files=hashes)
    marker = policy.validate_marker(dict(schema=policy.SCHEMA, name=name, purpose='full_report',
        max_jobs=1, window_seconds=900, source=source, generation_settings=settings))
    policy.check_origin(conn, marker)
    policy.check_row(marker, row)
    # CPU-only validation of existing contracts/media/model files; no decoding,
    # inference, key loading or provider call.
    _, request = team_scene.prepare(bundle, settings['scene_model'], settings['scene_fps'])
    model_path = os.environ.get('RAG_MODEL_PATH')
    require(model_path is not None, 'report_continuation_local_e5_required')
    team_model.preflight(model_path)
    next_task = selection_jobs.identifier(dict(gpu_key=gpu_key, purpose='full_report',
        generation_settings=settings, reference_date=None))
    plan = {'schema': 'cctv-report-continuation-plan-v1', 'marker': marker,
            'task_id': next_task, 'scene_request_sha256': request['request_sha256'],
            'admission_window_seconds': 900, 'hard_wall_clock_timeout': False,
            'max_new_gpu_calls': 0, 'max_new_scene_calls': 1, 'max_cpu_retrievals': 1,
            'max_external_embedding_or_report_calls': 0, 'automatic_retry': False}
    return dict(plan, plan_sha256=policy.fingerprint(plan)), row, history


def save_exclusive(path, value):
    activation.plain(path)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(activation.encoded(value)); stream.flush(); os.fsync(stream.fileno())
    activation.fsync_directory(path.parent)


def apply(conn, store, parameters, approved_plan_sha256):
    global APPLY_INTENT
    require(hour_session.valid_sha(approved_plan_sha256), 'report_continuation_reviewed_plan_required')
    with ExitStack() as stack:
        for kind in ('admission', 'dispatch', 'flow', 'collect'):
            stack.enter_context(store.lock(kind))
        plan, row, history = inspect_source(conn, store, **parameters)
        require(plan['plan_sha256'] == approved_plan_sha256, 'report_continuation_reviewed_plan_changed')
        marker = plan['marker']
        path = hour_session.marker_path()
        require(path.parent.stat().st_uid == os.geteuid()
                and not stat.S_IMODE(path.parent.stat().st_mode) & 0o077, 'private_spool_required')
        before = activation.read_file(path)
        after = activation.encoded(marker)
        backup = path.parent / ('.report-continuation-' + marker['name'] + '.json')
        save_exclusive(backup, {'status': 'approved_apply_intent', 'plan': plan,
                               'previous_marker_base64': base64.b64encode(before).decode()})
        APPLY_INTENT = True
        activation.replace_file(path, before, after)
        try:
            started = hour_session.clock(conn)
            hour_session.initialize(conn, started, started + marker['window_seconds'])
            directory = path.parent / checked_id(row['directory_id'])
            result = selection_jobs.admit(store, directory / 'input.mp4', None, 'B', 'full_report',
                spool=path.parent, config=row['state']['config'], jsonb=flow_service.jsonb)
            require(len(result['jobs']) == 1 and result['jobs'][0]['worker_job_id'] == row['worker_job_id']
                    and result['jobs'][0]['task_id'] == plan['task_id']
                    and result['jobs'][0]['already_present'] is True,
                    'report_continuation_admitted_identity_changed')
            current = activation.session_rows(conn)
            require({k: current[k] for k in history} == history, 'report_continuation_history_changed')
            require(hour_session.get(conn)['claims'] == [], 'report_continuation_generation_started_before_unlock')
            save_exclusive(backup.with_suffix('.ready.json'), {'status': 'report_continuation_admitted',
                'plan_sha256': approved_plan_sha256, 'selection': result})
        except BaseException:
            # Never reset a possibly committed grant or retry a partial admission.
            absent = False
            try:
                absent = conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',
                                      (marker['name'],)).fetchone() is None
            except Exception:
                pass
            if absent:
                activation.replace_file(path, after, before)
            raise
    return {'status': 'report_continuation_admitted', 'session': marker['name'],
            'worker_job_id': row['worker_job_id'], 'task_id': plan['task_id'],
            'selection_id': result['selection_id'], 'plan_sha256': approved_plan_sha256,
            'admission_window_seconds': 900, 'hard_wall_clock_timeout': False,
            'actual_report_completed': False, 'automatic_retry': False}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    for option in ('name', 'source-session', 'worker-job-id', 'event-id', 'input-sha256',
                   'gpu-key', 'manifest-sha256', 'result-sha256'):
        cli.add_argument('--' + option, required=True)
    cli.add_argument('--apply', action='store_true')
    cli.add_argument('--approved-plan-sha256')
    args = vars(cli.parse_args(argv))
    execute, approved = args.pop('apply'), args.pop('approved_plan_sha256')
    with connect() as conn:
        conn.execute("SET statement_timeout='15000'")
        if not execute:
            conn.execute('SET default_transaction_read_only=on')
            plan, _, _ = inspect_source(conn, Store(conn), **args)
            print(json.dumps({'status': 'read_only_report_continuation_plan', **plan}))
        else:
            print(json.dumps(apply(conn, Store(conn), args, approved)))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': ('report_continuation_apply_outcome_unverified' if APPLY_INTENT
                                    else 'report_continuation_preflight_stopped'),
                          'worker_may_have_started': APPLY_INTENT,
                          'error_code': getattr(exc, 'code', 'report_continuation_preparation_failed'),
                          'automatic_retry': False}))
        raise SystemExit(2)
