"""Explicit user import of one approved saved case; no provider/model execution.

Run after the new schema/code deployment, through the bundled host command.
Only additive teammate tables are written; legacy facts/jobs/claims are checked.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import uuid

from .common import WorkerError, read_json
from .queue_store import Store, connect
from . import flow_service, team_jobs, team_settings
from . import worker
from .runpod_api import analysis_identity
from .report_jobs import fingerprint
from apps.scene_description import team_scene, team_db

WORKER = 'c2f5dc5084f744f0addaff03ad877e04'
PROVIDER = '6efe62e3bdce4fbdb8270980332db872'
LEGACY_TABLES = ('cctv_jobs.queue','cctv_jobs.flows','cctv_jobs.analysis_tasks',
                 'cctv_jobs.analysis_selections','cctv_scene.descriptions',
                 'cctv_scene.report_runs','cctv_rag.chunks')


def require(value, code):
    if not value: raise WorkerError(code)


@contextmanager
def existing_lock(directory):
    """Lock an existing job without creating/chmodding any original file."""
    path=Path(directory)/'.lock'
    require(path.is_file() and not any(p.is_symlink() for p in (path,*path.parents)),
            'saved_case_existing_lock_required')
    with path.open('rb') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise WorkerError('job_already_locked') from None
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def readonly_identity(queue, row, directory, event_id):
    """Validate saved identity without Coordinator.load's recovery writes."""
    require(not (directory/'retarget.pending.json').exists(),'retarget_incomplete_repeat_retarget')
    state=worker.load_state(directory);old=row['state']
    require(state.get('status')==old.get('status')=='handoff_ready'
            and state.get('provider_job_id')==old.get('provider_job_id')==PROVIDER,
            'saved_case_completed_spool_and_database_required')
    for key in ('worker_job_id','input_sha256','input_bytes','config'):
        require(state.get(key)==old.get(key),'queue_spool_identity_mismatch')
    if 'analysis_profile' in state or 'analysis_profile' in old:
        analysis_identity(state);analysis_identity(old)
        require(all(state.get(k)==old.get(k) for k in ('analysis_profile','gpu_key')),
                'queue_spool_analysis_identity_mismatch')
    handoff=read_json(directory/'handoff.json')
    require(all(handoff.get(k)==state.get(k) for k in ('worker_job_id','provider_job_id','input_sha256'))
            and handoff==row.get('handoff'),'scene_handoff_database_mismatch')
    expected={'bucket':state['config']['s3_bucket'],'region':state['config']['s3_region'],
              'job_id':state['provider_job_id'],'event_id':event_id,
              'expected_input_sha256':state['input_sha256']}
    if 'analysis_profile' in state:
        expected.update(analysis_profile=state['analysis_profile'],gpu_key=state['gpu_key'])
        require(all(handoff.get(k)==state[k] for k in ('analysis_profile','gpu_key')),
                'scene_handoff_analysis_mismatch')
    path=directory/f'scene-{event_id}.json'
    require(path.name in handoff.get('scene_configs',[]) and path.is_file()
            and read_json(path)==expected,'scene_event_not_in_handoff')
    return state,handoff,expected


def legacy_receipts(conn):
    receipts={}
    for table in LEGACY_TABLES:
        receipts[table]=conn.execute("SELECT count(*) AS count,md5(COALESCE(string_agg(x,E'\\n' ORDER BY x),'')) AS md5 FROM (SELECT to_jsonb(t)::text x FROM "+table+' t) z').fetchone()
    for name in ('p6-final-trial','p6-hour-session-20261002-01'):
        receipts[name]=conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',(name,)).fetchone()
    return deepcopy(receipts)


def parse_case(data):
    require(type(data) is dict and set(data)=={'scene','report_json','report_markdown'},'saved_case_shape_invalid')
    observed=data['scene']; sha=team_scene.validate_result(observed)
    require(observed['generation_origin']=='existing_independent_vlm'
            and observed['provider_job_id']==PROVIDER and observed['event_id']=='event_000',
            'saved_case_source_invalid')
    # Fixed canonical hashes are inserted by the preparation script below.
    require(sha==SCENE_HASH and fingerprint(data['report_json'])==REPORT_HASH
            and hashlib.sha256(data['report_markdown'].encode()).hexdigest()==MARKDOWN_HASH,
            'saved_case_immutable_input_changed')
    replay=dict(pipeline_id=team_settings.PIPELINE,report_json=data['report_json'],
                report_markdown=data['report_markdown'],execution={
                    'mode':'saved_report_replay','retrieval_executed':False,'vlm_calls':0,
                    'generation_calls':0,'query_embedding_calls':0,'gpu_calls':0,'network_requests':0})
    team_jobs.validate_report(replay,observed['team_event'])
    rid=str(uuid.uuid5(uuid.NAMESPACE_URL,team_jobs.VERSION+'/'+observed['analysis_id']))
    return observed,team_jobs.envelope(replay,observed,rid,WORKER)


def import_case(conn, data, *, apply=False):
    observed,payload=parse_case(data)
    store=Store(conn);scenes=team_jobs.TeamSceneJobs(flow_service.coordinator(store))
    row,directory=scenes._context(WORKER,'event_000')
    with store.lock('admission'),store.lock('dispatch'),store.lock('collect'),store.lock('flow'),existing_lock(directory),conn.transaction():
        require(conn.execute('''SELECT NOT EXISTS(SELECT 1 FROM cctv_jobs.analysis_tasks WHERE finished_at IS NULL AND NOT paused)
            AND NOT EXISTS(SELECT 1 FROM cctv_jobs.flows WHERE finished_at IS NULL AND NOT paused) AS ok''').fetchone()['ok'],
            'saved_case_wait_for_idle_worker')
        state,handoff,expected=readonly_identity(scenes.queue,row,directory,'event_000')
        manifest,event,media=scenes._media(directory/'scenes/event_000/input',expected,handoff)
        source=observed['source']
        require(state['provider_job_id']==PROVIDER and state['input_sha256']==observed['input_sha256']
                and source['bucket']==expected['bucket'] and source['region']==expected['region']
                and source['manifest_sha256']==handoff['manifest_sha256']
                and source.get('analysis_profile')==state.get('analysis_profile')
                and source.get('gpu_key')==state.get('gpu_key')
                and (source['source_start_s'],source['source_end_s'])==(event['source_start_s'],event['source_end_s'])
                and all({k:asset[k] for k in ('key','sha256','bytes')}=={
                    k:manifest['files'][event[kind]][k] for k in ('key','sha256','bytes')}
                    for kind,asset in source['assets'].items()),'saved_case_local_media_identity_mismatch')
        before=legacy_receipts(conn)
        settings=dict(version=team_jobs.VERSION,pipeline_id=team_settings.PIPELINE,
            report_id=payload['report_id'],analysis_id=observed['analysis_id'],worker_job_id=WORKER,
            scene_sha256=team_scene.validate_result(observed),reference_date=None,
            document_reference_date_supported=False,model='cpu-template',contract=team_settings.contract(),
            execution_mode='saved_report_replay')
        progress=dict(settings=settings,status='report_stored',calls={},stages={},
            created_at=observed['finished_at'],updated_at=observed['finished_at'],elapsed_seconds=0,
            result=payload,result_sha256=fingerprint(payload),generation_calls=0,embedding_calls=0)
        if apply:
            from psycopg.types.json import Jsonb
            receipt=team_db.store(conn,observed)
            conn.execute('''INSERT INTO cctv_scene.team_report_runs (report_id,analysis_id,worker_job_id,settings,state)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (report_id) DO NOTHING''',
                (payload['report_id'],observed['analysis_id'],WORKER,Jsonb(settings),Jsonb(progress)))
            saved=conn.execute('SELECT settings,state FROM cctv_scene.team_report_runs WHERE report_id=%s',(payload['report_id'],)).fetchone()
            require(saved and saved['settings']==settings and saved['state']==progress,'saved_case_report_conflict_original_preserved')
            require(team_db.show(conn,observed['analysis_id'])==observed,'saved_case_scene_readback_failed')
        require(legacy_receipts(conn)==before,'saved_case_legacy_data_changed')
    return dict(status='team_saved_case_loaded' if apply else 'team_saved_case_preflight_passed',
                worker_job_id=WORKER,provider_job_id=PROVIDER,analysis_id=observed['analysis_id'],
                report_id=payload['report_id'],result_sha256=fingerprint(payload),
                saved_media_verified=True,legacy_business_tables_and_claims_unchanged=True,
                database_committed=apply,runpod_requests=0,s3_requests=0,generation_calls=0,
                embedding_calls=0,cpu_retrieval_executed=False,original_vlm_facts_preserved=True,
                date_filter_supported=False,new_upload_end_to_end_verified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();raw=sys.stdin.buffer.read(2*1024*1024+1)
    require(len(raw)<=2*1024*1024,'saved_case_input_too_large')
    data=json.loads(raw)
    with connect() as conn: result=import_case(conn,data,apply=args.apply)
    print(json.dumps(result,ensure_ascii=False))


SCENE_HASH = 'ecb43a45dfe956383f23a0e4c3464cfef4e1a0735f958a5b074914d2f15111e0'
REPORT_HASH = 'eb0c3ee4f10a9a12209491c10910d2e70b262dc724bcc4e3b1454e59d674ed36'
MARKDOWN_HASH = 'ce37301944ad6c904a563014dc195aaff1d6d89f4075a74dd663f1b23b7a8279'

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'status':'team_saved_case_import_stopped','error_code':getattr(exc,'code','saved_case_check_failed'),
                          'automatic_retry':False,'model_calls':0,'s3_requests':0,'runpod_requests':0}))
        raise SystemExit(1)
