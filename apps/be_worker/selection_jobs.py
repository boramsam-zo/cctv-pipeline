"""A/B request groups over shared GPU jobs. No provider calls during admission.

Comparison and reporting have separate durable tasks. The first full report
freezes settings/date; a second settings variant is explicitly out of scope.
"""
from datetime import date
from pathlib import Path
import json

from . import analysis_profiles as profiles, generation_settings as generation
from . import trial_limits, team_settings
from .common import WorkerError, checked_id, digest, locked, read_json, save
from .job_queue import enqueue, fingerprint


def identifier(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())[:32]


def snapshot(task):
    return {key: (str(task[key]) if key == 'reference_date' and task[key] is not None else task[key])
            for key in ('task_id','worker_job_id','purpose','version','gpu_key','reference_date','generation_settings')}


def receipt(directory, task, *, create=False):
    path = Path(directory)/('task-'+checked_id(task['task_id'])+'.json')
    expected = snapshot(task)
    with locked(directory):
        if path.exists():
            if read_json(path) != expected:
                raise WorkerError('analysis_task_receipt_mismatch')
        elif create:
            save(path, expected)
        else:
            raise WorkerError('analysis_task_receipt_missing')


def _validate_identity(task, row, *, stored=False):
    state = row['state']
    profile = profiles.validate_profile(state.get('analysis_profile'))
    key = profiles.gpu_identity(state['input_sha256'], profile)['gpu_key']
    if (task['worker_job_id'], task['version'], task['gpu_key'], state.get('gpu_key')) != (
            row['worker_job_id'], profile['version'], key, key):
        raise WorkerError('analysis_task_identity_mismatch')
    purpose = task['purpose']
    settings, day = task['generation_settings'], task['reference_date']
    if purpose == 'gpu_comparison':
        if settings is not None or day is not None:
            raise WorkerError('comparison_has_generation_settings')
    elif purpose == 'full_report':
        if stored and team_settings.is_team(settings):
            team_settings.validate_stored(settings, profile)
        else:
            generation.validate(settings, analysis_profile=profile)
        day = team_settings.reference_date(settings, day)
    else:
        raise WorkerError('invalid_analysis_purpose')
    expected = identifier(dict(gpu_key=key, purpose=purpose, generation_settings=settings, reference_date=day))
    if task['task_id'] != expected:
        raise WorkerError('analysis_task_id_mismatch')
    return settings


def validate(task, row):
    """Execution boundary: frozen generation settings must match this release."""
    return _validate_identity(task, row)


def validate_stored(task, row, *, directory):
    """View only: bind stored settings, task/GPU identity and the saved receipt.

    Keep this separate from validate so admission, resume and worker paths
    cannot accidentally accept an old code contract for new execution.
    """
    settings = _validate_identity(task, row, stored=True)
    path = Path(directory)/('task-'+checked_id(task['task_id'])+'.json')
    if Path(directory).is_symlink() or path.is_symlink():
        raise WorkerError('analysis_task_receipt_mismatch')
    if not path.is_file():
        raise WorkerError('analysis_task_receipt_missing')
    # These are immutable, atomically written receipts. Viewing them must not
    # create/chmod a lock file or contend with a worker's execution lock.
    if read_json(path) != snapshot(task):
        raise WorkerError('analysis_task_receipt_mismatch')
    return settings


def admit(store, video, reference_date, selection, purpose, *, spool, config, jsonb):
    versions = {'A':['A'], 'B':['B'], 'A+B':['A','B'], 'R2':['R2']}.get(selection)
    if versions is None or purpose not in ('gpu_comparison','full_report'):
        raise WorkerError('invalid_analysis_selection')
    team = team_settings.enabled() and purpose == 'full_report'
    if team and (versions not in (['B'], ['R2']) or reference_date is not None):
        raise WorkerError('team_rag_requires_b_without_date')
    if team and not trial_limits.hour_session.configured():
        raise WorkerError('team_rag_requires_active_test_session')
    day = None if team or purpose != 'full_report' else date.fromisoformat(reference_date).isoformat()
    sha, size = fingerprint(video)
    conn = store.connection
    # Locks enclose the SQL transaction, so rollback precedes advisory unlock.
    with store.lock('admission'), store.lock('dispatch'), conn.transaction():
        admission = trial_limits.check_admission(conn, sha, size, versions, purpose)
        children, answers = [], []
        for version in versions:
            profile = profiles.profile(version)
            key = profiles.gpu_identity(sha, profile)['gpu_key']
            queue_key = 'ab-gpu-'+key
            row = store.by_key(queue_key)
            from . import report_continuation
            if report_continuation.selected(admission):
                if row is None:
                    raise WorkerError('report_continuation_cannot_enqueue_gpu')
                report_continuation.check_row(admission, row)
            if row is None:
                answer = enqueue(store, spool, video, config, queue_key,
                                 dispatch_locked=True, analysis_profile=profile)
                row = store.by_key(queue_key)
            else:
                state = row['state']
                if (state.get('analysis_profile'), state.get('gpu_key'), state['input_sha256'], state['input_bytes']) != (
                        profile, key, sha, size):
                    raise WorkerError('analysis_gpu_identity_conflict')
                if team and any(event.get('analysis_id') and event.get('description_directory') != 'description-team-v1'
                                for event in state.get('scene_events', {}).values()):
                    raise WorkerError('team_existing_legacy_scene_preserved_use_new_video')
                # Reuse the frozen provider target; a changed current endpoint
                # must not retarget an existing submitted job behind the user.
                answer = dict(worker_job_id=row['worker_job_id'], already_present=True)
            settings = generation.from_environment(analysis_profile=profile) if purpose == 'full_report' else None
            trial_limits.bind_job(conn, row, settings, day)
            task = dict(task_id=identifier(dict(gpu_key=key, purpose=purpose,
                        generation_settings=settings, reference_date=day)),
                        worker_job_id=row['worker_job_id'], purpose=purpose, version=version,
                        gpu_key=key, reference_date=day, generation_settings=settings)
            old = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE worker_job_id=%s AND purpose=%s',
                               (row['worker_job_id'],purpose)).fetchone()
            if old:
                if snapshot(old) != task:
                    raise WorkerError('full_report_settings_conflict_existing_preserved')
                validate(old, row)
                receipt(Path(spool)/row['directory_id'], old)
            else:
                receipt(Path(spool)/row['directory_id'], task, create=True)
                conn.execute('''INSERT INTO cctv_jobs.analysis_tasks
                    (task_id,worker_job_id,purpose,version,gpu_key,reference_date,generation_settings)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                    (task['task_id'],row['worker_job_id'],purpose,version,key,day,
                     jsonb(settings) if settings is not None else None))
            child = {k:task[k] for k in ('task_id','worker_job_id','version','gpu_key')}
            children.append(child)
            answers.append(dict(child, already_present=answer['already_present']))
        group_id = identifier(dict(input_sha256=sha, selection=selection, purpose=purpose,
                                   tasks=[child['task_id'] for child in children]))
        existing = conn.execute('SELECT * FROM cctv_jobs.analysis_selections WHERE selection_id=%s', (group_id,)).fetchone()
        expected = dict(selection_id=group_id,input_sha256=sha,selection=selection,purpose=purpose,children=children)
        if existing:
            if any(existing.get(k) != v for k,v in expected.items()):
                raise WorkerError('analysis_selection_conflict')
        else:
            conn.execute('''INSERT INTO cctv_jobs.analysis_selections
                (selection_id,input_sha256,selection,purpose,children) VALUES (%s,%s,%s,%s,%s)''',
                (group_id,sha,selection,purpose,jsonb(children)))
        return dict(selection_id=group_id,already_present=bool(existing),selection=selection,purpose=purpose,jobs=answers)


def pending(conn):
    return conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE NOT paused AND finished_at IS NULL ORDER BY created_at, version').fetchall()


def progress(conn, task_id, status, error=None, *, paused=False, finished=False):
    conn.execute('''UPDATE cctv_jobs.analysis_tasks SET status=%s,error_code=%s,paused=%s,
        updated_at=now(),finished_at=CASE WHEN %s THEN COALESCE(finished_at,now()) ELSE finished_at END
        WHERE task_id=%s''', (status,error,paused,finished,task_id))


def prepare(conn, queue, rows):
    for task in pending(conn):
        try:
            row = rows[task['worker_job_id']]
            if trial_limits.hour_session.configured() and row['state']['status'] not in ('submitting', 'waiting'):
                trial_limits.hour_session.check_dispatch(conn, row)
            validate(task,row)
            receipt(queue.directory(row),task)
        except Exception as exc:
            progress(conn,task['task_id'],'paused',getattr(exc,'code','analysis_settings_check_failed'),paused=True)


def process(conn, queue, *, heartbeat, process_events):
    from .queue_store import Store
    for task in pending(conn):
        row = next(r for r in Store(conn).rows() if r['worker_job_id']==task['worker_job_id'])
        state = row['state']; status=state['status']
        try:
            settings=validate(task,row)
            receipt(queue.directory(row),task)
            if task['status']=='reconcile_requested' and status in ('submission_unknown','provider_failed'):
                reconcile(queue,row)
                latest=next(r for r in Store(conn).rows() if r['worker_job_id']==task['worker_job_id'])
                progress(conn,task['task_id'],latest['state']['status'],latest['state'].get('error_code'))
                continue
            if status=='no_candidates':
                progress(conn,task['task_id'],'no_candidates',finished=True)
            elif status=='handoff_ready':
                def emit(stage):
                    progress(conn,task['task_id'],stage,finished=stage in ('completed','comparison_completed'))
                    heartbeat(stage)
                process_events(queue,row,str(task['reference_date']) if task['reference_date'] else None,
                               emit,settings=settings,purpose=task['purpose'])
            elif status in ('provider_failed','submission_unknown','storage_not_ready','verification_failed'):
                progress(conn,task['task_id'],status,state.get('error_code'),paused=True)
            elif status=='submission_rejected' and state.get('error_code')!='http_429':
                progress(conn,task['task_id'],status,state.get('error_code'),paused=True)
            else:
                progress(conn,task['task_id'],status,state.get('error_code'))
        except Exception as exc:
            progress(conn,task['task_id'],'paused',getattr(exc,'code','analysis_failed_inspect_receipts'),paused=True)


def resume(conn, task_id):
    from .queue_store import Store
    task = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE task_id=%s',(checked_id(task_id),)).fetchone()
    if task is None: raise WorkerError('analysis_task_not_found')
    row=next(r for r in Store(conn).rows() if r['worker_job_id']==task['worker_job_id'])
    if row['state']['status'] in ('submitting','verification_failed','storage_not_ready'):
        raise WorkerError('manual_provider_reconciliation_required')
    validate(task,row)
    conn.execute('''UPDATE cctv_jobs.analysis_tasks SET paused=false,error_code=NULL,status='reconcile_requested',updated_at=now()
        WHERE task_id=%s AND paused AND finished_at IS NULL''',(task_id,))
    return {'status':'resume_requested','task_id':task_id}


def reconcile(queue, row):
    """Worker-only explicit recovery. Neither branch invokes GPU submission."""
    from . import worker
    from .runpod_api import validate_status
    directory=queue.directory(row)
    with queue.store.lock('dispatch'), locked(directory):
        state=queue.load(row)
        api=queue.api_factory(state)
        trace=queue.trace(row,state,'resume')
        try:
            if state['status']=='submission_unknown':
                identifier=api.lookup_versioned(state)
            elif state['status']=='provider_failed':
                identifier=checked_id(state['provider_job_id'])
                remote=validate_status(api.status(identifier),state)
                if remote['status']=='failed':
                    # Server accepts only a saved successful analysis; even an
                    # ambiguous response here cannot launch a second GPU run.
                    api.resume_evidence(state)
            else:
                raise WorkerError('manual_provider_reconciliation_required')
            worker.resume(directory,state,api,trace,wait_seconds=0,provider_job_id=identifier)
        finally:
            trace.finish(state)
            queue.store.write(row,state)
