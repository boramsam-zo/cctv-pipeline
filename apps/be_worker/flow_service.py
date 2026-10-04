"""Shared API/worker functions. Views never execute models or advance jobs."""
from datetime import date
from pathlib import Path
import os
import re

from .common import WorkerError, checked_id, config_checked, locked, read_json, save
from . import generation_settings as generation
from . import selection_jobs, trial_limits, team_settings
from .queue_store import Store
from .job_queue import Coordinator, enqueue, fingerprint, ACTIVE
from .runpod_api import RunpodAPI
from .scene_jobs import SceneJobs
from .report_jobs import ReportJobs
from .worker import secret
from apps.scene_description.scene import InstanceRoleReader


def config():
    return config_checked(read_json(Path(os.environ.get('BE_TARGET_CONFIG', '/input/config-ec2-v1.json'))))


def spool():
    return Path(os.environ.get('BE_SPOOL_PATH', '/spool'))


def api_for(state):
    token = secret(Path('/run/secrets/runpod_worker_token'), 'RUNPOD_WORKER_TOKEN', '')
    return RunpodAPI(state['config']['runpod_url'], token)


def key():
    return generation.read_generation_key()


def coordinator(store):
    return Coordinator(store, spool(), api_for,
                       lambda state: InstanceRoleReader(state['config']['s3_region']), slots=1)


def jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def admit_selection(store, video, reference_date, selection, purpose):
    return selection_jobs.admit(store,video,reference_date,selection,purpose,
                                spool=spool(),config=config(),jsonb=jsonb)


def resume_task(conn, identifier):
    return selection_jobs.resume(conn,identifier)


def admit(store, video, reference_date):
    date.fromisoformat(reference_date)
    sha, _ = fingerprint(video)
    request_key = 'ui-v1-' + sha
    # Serialize the lookup and enrolment across clients. enqueue keeps its own
    # dispatch lock; this is a separate session lock and never invokes Runpod.
    with store.lock('admission'):
        if trial_limits.get(store.connection) is not None:
            raise WorkerError('trial_requires_versioned_selection')
        previous = next((r for r in store.rows() if r['state']['input_sha256'] == sha), None)
        if previous:
            # Returning an existing input never applies today's defaults, even
            # if those defaults are invalid. Legacy CLI jobs remain unenrolled.
            if previous['request_key'] == request_key:
                store.connection.execute('''INSERT INTO cctv_jobs.flows
                    (worker_job_id, reference_date, generation_settings)
                    VALUES (%s,%s,%s) ON CONFLICT (worker_job_id) DO NOTHING''',
                    (previous['worker_job_id'], reference_date, jsonb(generation.legacy_settings())))
            return {'worker_job_id': previous['worker_job_id'], 'already_present': True}
        settings = generation.from_environment()
        # Both rows commit together, so a restart cannot enrol a queued video
        # under different defaults. A rollback leaves only an unsubmitted spool.
        with store.lock('dispatch'), store.connection.transaction():
            answer = enqueue(store, spool(), video, config(), request_key, dispatch_locked=True)
            row = store.by_key(request_key)
            write_settings_receipt(spool()/row['directory_id'], reference_date, settings)
            store.connection.execute('''INSERT INTO cctv_jobs.flows
                (worker_job_id, reference_date, generation_settings) VALUES (%s,%s,%s)''',
                (answer['worker_job_id'], reference_date, jsonb(settings)))
        return answer


def write_settings_receipt(directory, reference_date, settings):
    expected = {'reference_date': str(reference_date), 'generation_settings': settings}
    with locked(directory):
        path = directory/'flow-settings.json'
        if path.exists():
            if read_json(path) != expected:
                raise WorkerError('flow_settings_receipt_mismatch')
        else:
            save(path, expected)


def prepare_settings(conn, queue, row, flow):
    """Check frozen settings before dispatch; old rows use literal old defaults."""
    settings = generation.validate(flow.get('generation_settings') if
        flow.get('generation_settings') is not None else generation.legacy_settings())
    write_settings_receipt(queue.directory(row), flow['reference_date'], settings)
    if flow.get('generation_settings') is None:
        conn.execute('''UPDATE cctv_jobs.flows SET generation_settings=%s
            WHERE worker_job_id=%s AND generation_settings IS NULL''',
            (jsonb(settings), row['worker_job_id']))
        stored = conn.execute('SELECT generation_settings FROM cctv_jobs.flows WHERE worker_job_id=%s',
                              (row['worker_job_id'],)).fetchone()
        if not stored or stored['generation_settings'] != settings:
            raise WorkerError('flow_settings_database_mismatch')
    return settings


def progress(conn, identifier, status, error=None, paused=False, finished=False):
    conn.execute('''UPDATE cctv_jobs.flows SET status=%s, error_code=%s, paused=%s,
        updated_at=now(), finished_at=CASE WHEN %s THEN COALESCE(finished_at, now()) ELSE finished_at END
        WHERE worker_job_id=%s''', (status, error, paused, finished, identifier))


class AutomaticStore(Store):
    def rows(self):
        enrolled = {}
        tasks = {}
        for task in selection_jobs.pending(self.connection):
            tasks.setdefault(task['worker_job_id'],[]).append(task)
        for flow in self.connection.execute(
                'SELECT * FROM cctv_jobs.flows WHERE NOT paused AND finished_at IS NULL').fetchall():
            try:
                enrolled[flow['worker_job_id']] = (flow, generation.validate(
                    flow.get('generation_settings') if flow.get('generation_settings') is not None
                    else generation.legacy_settings()))
            except WorkerError:
                continue  # tick records the error; a concurrent admission cannot slip into dispatch.
        # Other active jobs still occupy the GPU slot. Unenrolled prepared CLI
        # jobs are never submitted by this service.
        rows = []
        for row in super().rows():
            if row['state']['status'] in ACTIVE:
                rows.append(row)  # Keep known IDs and uncertain submissions for reconciliation.
            elif row['worker_job_id'] in enrolled:
                flow, settings = enrolled[row['worker_job_id']]
                try:
                    receipt = read_json(spool()/row['directory_id']/'flow-settings.json')
                    if receipt == {'reference_date': str(flow['reference_date']), 'generation_settings': settings}:
                        rows.append(row)
                except (OSError, WorkerError):
                    pass  # No new submission before a durable settings receipt exists.
            elif row['worker_job_id'] in tasks:
                for task in tasks[row['worker_job_id']]:
                    try:
                        if trial_limits.hour_session.configured():
                            trial_limits.hour_session.check_dispatch(self.connection, row)
                        selection_jobs.validate(task,row)
                        selection_jobs.receipt(spool()/row['directory_id'],task)
                        rows.append(row)
                        break
                    except (OSError,WorkerError,ValueError):
                        pass
        return rows


def process_events(queue, row, reference_date, emit, scenes=None, reports=None, *, settings=None,
                   purpose='full_report', previews=None):
    """Reusable scheduler seam for restart tests; all costly operations are existing steps."""
    if purpose not in ('gpu_comparison','full_report'):
        raise WorkerError('invalid_analysis_purpose')
    if purpose=='full_report':
        settings = generation.validate(settings if settings is not None else generation.legacy_settings(),
                                       analysis_profile=row['state'].get('analysis_profile'))
    elif settings is not None:
        raise WorkerError('comparison_has_generation_settings')
    if purpose=='full_report' and team_settings.is_team(settings):
        from .team_jobs import TeamSceneJobs, TeamReportJobs
        scenes, reports = scenes or TeamSceneJobs(queue), reports or TeamReportJobs(queue)
    else:
        scenes, reports = scenes or SceneJobs(queue), reports or ReportJobs(queue)
    scene_configs = (row.get('handoff') or {}).get('scene_configs', [])
    if not scene_configs:
        raise WorkerError('flow_handoff_has_no_events')
    events = []
    for name in scene_configs:
        match = re.fullmatch(r'scene-(event_[0-9]+)\.json', name)
        if not match: raise WorkerError('flow_invalid_event')
        events.append(match[1])
    if len(set(events)) != len(events):
        raise WorkerError('flow_duplicate_event')
    # Validate the entire candidate list before even the first paid stage.
    trial_limits.check_events(queue.store.connection, row, events, settings, purpose, reference_date)
    for event in events:
        latest = next(r for r in queue.store.rows() if r['worker_job_id'] == row['worker_job_id'])
        scene_state = latest['state'].get('scene_events', {}).get(event, {})
        if 'analysis_profile' in row['state'] or not scene_state.get('media_bytes_verified'):
            emit('verifying_media')
            scenes.fetch(row['worker_job_id'], event)
        if row['state'].get('analysis_profile',{}).get('version') in ('B','R2'):
            emit('preparing_preview')
            (previews or prepare_previews)(scenes,row['worker_job_id'],event)
        if purpose=='gpu_comparison':
            continue
        if scene_state.get('status') != 'description_stored':
            emit('describing')
            scenes.describe(row['worker_job_id'], event, model=settings['scene_model'],
                            fps=settings['scene_fps'], key_loader=key)
        emit('reporting')
        reports.run(row['worker_job_id'], event, reference_date, settings['report_model'], key,
                    expected_scene={'model': settings['scene_model'], 'fps': settings['scene_fps'],
                                    'prompt_version': settings['scene_prompt_version']})
    emit('comparison_completed' if purpose=='gpu_comparison' else 'completed')


def prepare_previews(scenes, identifier, event_id):
    from . import browser_preview
    from apps.scene_description import scene
    row,directory=scenes._context(identifier,event_id)
    with locked(directory):
        _,handoff,expected=scenes._identity(row,directory,event_id)
        inputs=directory/'scenes'/event_id/'input'
        manifest,event,assets=scenes._media(inputs,expected,handoff)
        index=read_json(inputs/'evidence.json')
        for kind in ('clip','roi_clip'):
            browser_preview.prepare(inputs/event[kind],directory/'scenes'/event_id/'display-preview'/kind,
                source_sha256=scene.sha(assets[kind]),expected_frames=event['clip_frame_count'],fps=index['source_fps'])


def tick(conn):
    store = AutomaticStore(conn)
    queue = coordinator(store)
    errors = []
    def heartbeat(stage):
        conn.execute('''INSERT INTO cctv_jobs.service_state(name,payload) VALUES ('worker',%s)
            ON CONFLICT(name) DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()''',
            (jsonb({'stage': stage, 'errors': errors, 'gpu_slots': 1}),))
    with store.lock('flow'):
        heartbeat('checking_settings')
        pending = conn.execute('SELECT * FROM cctv_jobs.flows WHERE NOT paused AND finished_at IS NULL ORDER BY created_at').fetchall()
        rows = {r['worker_job_id']: r for r in Store(conn).rows()}
        selection_jobs.prepare(conn,queue,rows)
        for flow in pending:
            try:
                row = rows[flow['worker_job_id']]
                if row['state']['status'] == 'no_candidates':
                    progress(conn, flow['worker_job_id'], 'no_candidates', finished=True)
                else:
                    prepare_settings(conn, queue, row, flow)
            except Exception as exc:
                progress(conn, flow['worker_job_id'], 'paused',
                         getattr(exc, 'code', 'flow_settings_check_failed'), paused=True)
        heartbeat('dispatch')
        try: queue.dispatch_tick()
        except Exception as exc: errors.append(getattr(exc, 'code', 'runpod_connection_or_dispatch_error'))
        heartbeat('collect')
        try: queue.collect_tick()
        except Exception as exc: errors.append(getattr(exc, 'code', 'evidence_collection_error'))
        flows = conn.execute('SELECT * FROM cctv_jobs.flows WHERE NOT paused AND finished_at IS NULL ORDER BY created_at').fetchall()
        for flow in flows:
            identifier = flow['worker_job_id']
            row = next(r for r in Store(conn).rows() if r['worker_job_id'] == identifier)
            status = row['state']['status']
            if status == 'no_candidates':
                progress(conn, identifier, 'no_candidates', finished=True)
            elif status == 'handoff_ready':
                def emit(stage):
                    progress(conn, identifier, stage, finished=stage == 'completed')
                    heartbeat(stage)
                try:
                    settings = prepare_settings(conn, queue, row, flow)
                    process_events(queue, row, str(flow['reference_date']), emit, settings=settings)
                except Exception as exc:
                    progress(conn, identifier, 'paused', getattr(exc, 'code', 'flow_failed_inspect_receipts'), paused=True)
            elif status in ('provider_failed','submission_unknown','storage_not_ready','verification_failed'):
                progress(conn, identifier, status, row['state'].get('error_code'), paused=True)
            elif status == 'submission_rejected' and row['state'].get('error_code') != 'http_429':
                progress(conn, identifier, status, row['state'].get('error_code'), paused=True)
            else:
                progress(conn, identifier, status, row['state'].get('error_code'))
        selection_jobs.process(conn,queue,heartbeat=heartbeat,process_events=process_events)
        from .mlops_jobs import sync_tasks
        try:
            sync_tasks(conn, Store(conn).rows(), spool())
        except Exception:
            errors.append('mlflow_metadata_pending')
        heartbeat('idle')


def view(conn, identifier=None):
    rows = Store(conn).rows()
    if identifier: rows = [r for r in rows if r['worker_job_id'] == checked_id(identifier)]
    output = []
    for row in rows:
        identifier = row['worker_job_id']; state = row['state']
        flow = conn.execute('SELECT *, EXTRACT(EPOCH FROM (COALESCE(finished_at, now())-created_at)) AS elapsed_seconds FROM cctv_jobs.flows WHERE worker_job_id=%s', (identifier,)).fetchone()
        descriptions = conn.execute('SELECT analysis_id, event_id, payload FROM cctv_scene.descriptions WHERE provider_job_id=%s ORDER BY created_at', (state.get('provider_job_id'),)).fetchall()
        reports = conn.execute('SELECT report_id, settings, state FROM cctv_scene.report_runs WHERE worker_job_id=%s', (identifier,)).fetchall()
        # Additive table lookup: legacy deployments before migration have none.
        team_exists = conn.execute("SELECT to_regclass('cctv_scene.team_descriptions') IS NOT NULL AND to_regclass('cctv_scene.team_report_runs') IS NOT NULL AS ok").fetchone()
        if team_exists and team_exists['ok']:
            from .team_jobs import validate_report
            from apps.scene_description import team_scene
            from .report_jobs import fingerprint as report_fingerprint
            new_descriptions = conn.execute('SELECT analysis_id,event_id,result AS payload,result_sha256 FROM cctv_scene.team_descriptions WHERE provider_job_id=%s ORDER BY created_at',(state.get('provider_job_id'),)).fetchall()
            by_analysis = {}
            for item in new_descriptions:
                if team_scene.validate_result(item['payload']) != item['result_sha256']:
                    raise WorkerError('team_view_scene_hash_changed')
                by_analysis[str(item['analysis_id'])] = item['payload']
            new_reports = conn.execute('SELECT report_id,settings,state FROM cctv_scene.team_report_runs WHERE worker_job_id=%s',(identifier,)).fetchall()
            for item in new_reports:
                progress=item['state']; payload=progress.get('result')
                if progress['status']=='report_stored':
                    source=by_analysis.get(item['settings']['analysis_id'])
                    if (source is None or payload.get('analysis_id') != source['analysis_id']
                            or payload.get('report_id') != str(item['report_id'])
                            or payload.get('worker_job_id') != identifier
                            or payload.get('provider_job_id') != state.get('provider_job_id')
                            or payload.get('event_id') != source['event_id']
                            or payload.get('scene_sha256') != team_scene.validate_result(source)
                            or report_fingerprint(payload) != progress.get('result_sha256')):
                        raise WorkerError('team_view_report_identity_changed')
                    validate_report(payload,source['team_event'])
            descriptions += new_descriptions
            reports += new_reports
        complete = bool(descriptions) and len(reports) == len(descriptions) and all(r['state']['status']=='report_stored' for r in reports)
        stage = flow['status'] if flow else ('completed' if complete else state['status'])
        item=dict(worker_job_id=identifier, provider_job_id=state.get('provider_job_id'),
            generation_settings=(flow.get('generation_settings') if flow else None),
            stage=stage, error_code=(flow or {}).get('error_code') or state.get('error_code'),
            flow=flow, submitted_at=row['created_at'], queue_updated_at=row['updated_at'],
            post_attempts=state.get('post_attempts',0), scene_calls=state.get('gemini_api_calls',0),
            scene_events=state.get('scene_events', {}), descriptions=descriptions, reports=reports,
            input_sha256=state['input_sha256'])
        if 'analysis_profile' in state:
            from . import ab_evidence
            item.update(analysis_profile=state['analysis_profile'],version=state['analysis_profile']['version'],
                        gpu_key=state['gpu_key'],evidence_events=[],metrics={})
            handoff=row.get('handoff')
            if handoff:
                from .common import digest, JSON_LIMIT
                path=spool()/row['directory_id']/'evidence.raw.json'
                if path.is_symlink() or path.stat().st_size>JSON_LIMIT or digest(path.read_bytes())!=handoff['evidence_index_sha256']:
                    raise WorkerError('view_evidence_receipt_mismatch')
                index=ab_evidence.validate_index(read_json(path),snapshot=state['analysis_profile'],input_sha256=state['input_sha256'])
                item.update(evidence_events=[ab_evidence.selected_event(index,e['event_id']) for e in index['events']],
                            metrics=handoff.get('metrics',{}))
        output.append(item)
    return output


def media(conn, identifier, event, kind):
    if not re.fullmatch(r'event_[0-9]+', event) or kind not in ('clip','frame','roi_clip','clip_preview','roi_clip_preview'):
        raise WorkerError('invalid_media_request')
    scenes = SceneJobs(coordinator(Store(conn)))
    row, directory = scenes._context(identifier, event)
    _, handoff, expected = scenes._identity(row, directory, event)
    inputs = directory/'scenes'/event/'input'
    _, selected, assets = scenes._media(inputs, expected, handoff)
    if kind.endswith('_preview'):
        from . import browser_preview
        from apps.scene_description import scene
        original=kind.removesuffix('_preview')
        if selected.get('version') not in ('B','R2'):raise WorkerError('preview_requires_version_b')
        index=read_json(inputs/'evidence.json')
        target=directory/'scenes'/event/'display-preview'/original
        browser_preview.validate(inputs/selected[original],target,source_sha256=scene.sha(assets[original]),
                                 expected_frames=selected['clip_frame_count'],fps=index['source_fps'])
        return (target/'preview.mp4').read_bytes()
    # load_bundle checks every asset against the immutable S3 manifest.
    return assets[kind]


def selection_views(conn, identifier=None):
    sql='SELECT * FROM cctv_jobs.analysis_selections'
    groups=conn.execute(sql+' WHERE selection_id=%s' if identifier else sql+' ORDER BY created_at DESC',
                        (checked_id(identifier),) if identifier else ()).fetchall()
    if not groups:return []
    jobs={r['worker_job_id']:r for r in view(conn)}
    rows={r['worker_job_id']:r for r in Store(conn).rows()}
    tasks={r['task_id']:r for r in conn.execute('''SELECT *, EXTRACT(EPOCH FROM
        (COALESCE(finished_at,now())-created_at)) AS elapsed_seconds FROM cctv_jobs.analysis_tasks''').fetchall()}
    output=[]
    for group in groups:
        children=[]
        expected_versions={'A':['A'],'B':['B'],'A+B':['A','B'],'R2':['R2']}.get(group['selection'])
        if (expected_versions is None or [m.get('version') for m in group['children']] != expected_versions
                or group['selection_id'] != selection_jobs.identifier(dict(input_sha256=group['input_sha256'],
                    selection=group['selection'],purpose=group['purpose'],
                    tasks=[m['task_id'] for m in group['children']]))):
            raise WorkerError('selection_membership_mismatch')
        for member in group['children']:
            task=tasks[member['task_id']]
            row=rows[task['worker_job_id']]
            selection_jobs.validate(task,row)
            if any(task[k]!=v for k,v in member.items()) or task['purpose']!=group['purpose'] or row['state']['input_sha256']!=group['input_sha256']:
                raise WorkerError('selection_membership_mismatch')
            item=dict(jobs[task['worker_job_id']],task_id=task['task_id'],purpose=task['purpose'],
                      flow=task,stage=task['status'],error_code=task['error_code'] or row['state'].get('error_code'),
                      generation_settings=task['generation_settings'])
            if task['purpose']=='gpu_comparison':item.update(descriptions=[],reports=[])
            from . import mlops_jobs
            tracking_status = mlops_jobs.view_status(spool()/row['directory_id'], task,
                                                     row['state'].get('analysis_profile'))
            if tracking_status is not None:
                item['mlflow'] = tracking_status
            children.append(item)
        output.append(dict(group,jobs=children))
    return output
