"""Versioned teammate scene/report jobs over existing verified evidence.

Raw observations and legacy tables are preserved. CPU retrieval has its own
durable claim; it never consumes a Gemini embedding/report claim.
"""
import json
import os
from pathlib import Path
import uuid

from .common import WorkerError, locked, read_json, save
from .scene_jobs import SceneJobs
from .report_jobs import Journal, fingerprint
from . import hour_session, trial_limits, team_settings, mlflow_tracing
from apps.scene_description import team_scene, team_db, scene

VERSION = 'cctv-team-report-v1'
SECTIONS = ('사고 개요', '영상 관찰 및 판단 불가 항목', '관제의 전달·요청 사항',
            '현장 대응 참고', '확인 필요 사항', '관련 근거')


def validate_saved_scene(inputs, output):
    """Only an explicit recovery sidecar opts into the narrow saved repair."""
    marker = Path(output) / 'normalization.json'
    if marker.exists() or marker.is_symlink():
        from .report_recovery import validate_saved_output
        return validate_saved_output(inputs, output)
    return team_scene.validate_saved_output(inputs, output)


class TeamSceneJobs(SceneJobs):
    def __init__(self, coordinator, persist_result=None, preflight=None):
        super().__init__(coordinator, persist_result or (
            lambda result: team_db.store(self.store.connection, result)), preflight)

    def check_schema(self):
        conn = self.store.connection
        conn.execute('SELECT result FROM cctv_scene.team_descriptions LIMIT 0')
        if not conn.execute("SELECT has_table_privilege(current_user, 'cctv_scene.team_descriptions', 'INSERT') AS ok").fetchone()['ok']:
            raise WorkerError('team_scene_schema_required')

    def _description_output(self, event_dir):
        return event_dir / 'description-team-v1'

    def describe(self, identifier, event_id, *, model, fps, key_loader, caller=None):
        row, directory = self._context(identifier, event_id)
        with locked(directory):
            state, handoff, expected = self._identity(row, directory, event_id)
            inputs = directory/'scenes'/event_id/'input'
            manifest, event, media = self._media(inputs, expected, handoff)
            output = self._description_output(directory/'scenes'/event_id)
            if (output/'result.json').is_file():
                with mlflow_tracing.stage('saved_response_reuse', reused=True,
                                          new_model_calls=0) as trace:
                    result = validate_saved_scene(inputs,output)
                    trace.output(analysis_id=result.get('analysis_id'),
                                 scene_json=result.get('team_event'))
                if (result['model'], result['video_sample_fps']) != (model, fps):
                    raise WorkerError('team_scene_saved_settings_changed')
            elif output.exists():
                raise WorkerError('team_scene_incomplete_inspect_saved_response_no_retry')
            else:
                self.preflight()
                api_key = key_loader()
                count = state.get('scene_events', {}).get(event_id, {}).get('gemini_api_calls', 0)
                trace_provenance = {}
                def reserve(request):
                    trial_limits.claim(self.store.connection, row, event_id, 'scene',
                                       request['request_sha256'], model=model)
                    self._save(row, directory, state, event_id, status='describing',
                               media_bytes_verified=True, gemini_api_calls=count+1,
                               description_directory=output.name, error_code=None)
                    trace_provenance.update({name: request.get(name) for name in
                        ('prompt_version', 'request_sha256', 'contract_sha256', 'video_sample_fps')})
                try:
                    with mlflow_tracing.stage('vlm_observation', provider_model=model):
                        result = team_scene.describe(inputs, output, model, fps, api_key,
                            execute=True, caller=mlflow_tracing.gemini_caller(
                                caller or scene.call_gemini, provenance=trace_provenance),
                            pre_call=reserve)
                except Exception as exc:
                    code = getattr(exc, 'code', 'team_scene_failed_inspect_saved_response_no_retry')
                    self._save(row, directory, state, event_id, status='description_failed', error_code=code)
                    raise WorkerError(code) from None
            with mlflow_tracing.stage('scene_contract_validation') as trace:
                digest = team_scene.validate_result(result)
                trace.output(analysis_id=result['analysis_id'], scene_sha256=digest,
                             scene_json=result.get('team_event'))
            source = result['source']
            if (result['provider_job_id'] != expected['job_id'] or result['event_id'] != event_id
                    or result['input_sha256'] != expected['expected_input_sha256']
                    or source['bucket'] != expected['bucket'] or source['region'] != expected['region']
                    or source['manifest_sha256'] != handoff['manifest_sha256']
                    or source.get('analysis_profile') != expected.get('analysis_profile')
                    or source.get('gpu_key') != expected.get('gpu_key')
                    or (source['source_start_s'],source['source_end_s']) != (event['source_start_s'],event['source_end_s'])
                    or any({k: asset[k] for k in ('key','sha256','bytes')} != {
                        k: manifest['files'][event[kind]][k] for k in ('key','sha256','bytes')}
                        for kind, asset in source['assets'].items())):
                raise WorkerError('team_scene_identity_mismatch')
            previous = state.get('scene_events', {}).get(event_id, {})
            if previous.get('result_sha256') not in (None, digest):
                raise WorkerError('team_scene_saved_result_changed')
            self._save(row, directory, state, event_id, status='description_ready',
                       analysis_id=result['analysis_id'], result_sha256=digest, error_code=None)
            try:
                with mlflow_tracing.stage('scene_database_storage', analysis_id=result['analysis_id']):
                    receipt = self.persist_result(result)
                    if receipt.get('committed') is not True or receipt.get('result_sha256') != digest:
                        raise WorkerError('team_scene_database_readback_failed')
            except Exception:
                self._save(row,directory,state,event_id,status='database_pending',
                           error_code='team_scene_database_pending_reuse_saved_result')
                raise WorkerError('team_scene_database_pending_reuse_saved_result') from None
            save(output/'database.json', receipt)
            return self._save(row, directory, state, event_id, status='description_stored',
                              database_loaded=True, error_code=None)


def validate_report(value, event):
    from apps.team_rag.control_rag import markdown, validate, ROOT
    validate(event)
    if value.get('pipeline_id') != team_settings.PIPELINE:
        raise WorkerError('team_report_pipeline_mismatch')
    report = value.get('report_json', {})
    if (report.get('input') != event or report.get('event_id') != event['event_id']
            or report.get('report_format_version') != '2.1'
            or report.get('retrieval', {}).get('date_filter_supported') is not False
            or report.get('retrieval', {}).get('reference_date') is not None
            or value.get('report_markdown') != markdown(report)):
        raise WorkerError('team_report_contract_mismatch')
    body = value['report_markdown']
    if any(section not in body for section in SECTIONS):
        raise WorkerError('team_report_six_sections_missing')
    corpus = {row['chunk_id']: row for row in (
        json.loads(line) for line in (ROOT/'data/chunks.jsonl').read_text().splitlines() if line.strip())}
    seen = set()
    for source in report.get('sources', []):
        original = corpus.get(source.get('chunk_id'))
        citation = source.get('chunk_id')
        if (original is None or citation in seen or not citation
                or any(source.get(k) != original.get(k) for k in ('content','title','source_url'))):
            raise WorkerError('team_report_document_reference_mismatch')
        seen.add(citation)
    refs = report.get('references', [])
    if (len(refs) != len(seen) or {r.get('chunk_id') for r in refs} != seen
            or [r.get('reference_id') for r in refs] != [f'R{i+1}' for i in range(len(refs))]):
        raise WorkerError('team_report_citation_ids_invalid')
    for ref in refs:
        original = corpus[ref['chunk_id']]
        if any(ref.get(k) != original.get(k) for k in ('content','title','source_url')):
            raise WorkerError('team_report_citation_content_changed')
    return value


def envelope(result, scene_result, report_id, worker_job_id):
    value = validate_report(result, scene_result['team_event'])
    return dict(value, schema_version=VERSION, analysis_id=scene_result['analysis_id'],
                report_id=report_id, worker_job_id=worker_job_id,
                provider_job_id=scene_result['provider_job_id'], event_id=scene_result['event_id'],
                scene_sha256=team_scene.validate_result(scene_result),
                document_reference_date_supported=False,
                generation_calls=0, embedding_calls=0)


class TeamReportJobs:
    def __init__(self, coordinator, execute=None):
        self.queue, self.conn = coordinator, coordinator.store.connection
        self.scenes = TeamSceneJobs(coordinator)
        self.execute = execute

    def run(self, identifier, event_id, reference_date, model, key_loader, *, expected_scene=None):
        if reference_date is not None or model != 'cpu-template':
            raise WorkerError('team_report_settings_invalid')
        row, directory = self.scenes._context(identifier, event_id)
        with locked(directory):
            state, handoff, expected = self.scenes._identity(row, directory, event_id)
            saved = state.get('scene_events', {}).get(event_id, {})
            if saved.get('status') != 'description_stored':
                raise WorkerError('team_report_requires_stored_scene')
            result = read_json(self.scenes._description_output(directory/'scenes'/event_id)/'result.json')
            scene_sha = team_scene.validate_result(result)
            if (result['provider_job_id'] != state['provider_job_id'] or result['event_id'] != event_id
                    or result['input_sha256'] != state['input_sha256']
                    or scene_sha != saved.get('result_sha256')
                    or result['analysis_id'] != saved.get('analysis_id')):
                raise WorkerError('team_report_scene_identity_mismatch')
            if expected_scene and expected_scene != dict(model=result['model'],fps=result['video_sample_fps'],prompt_version=result['prompt_version']):
                raise WorkerError('team_report_scene_settings_mismatch')
            if team_db.show(self.conn, result['analysis_id']) != result:
                raise WorkerError('team_report_scene_database_mismatch')
            report_id = str(uuid.uuid5(uuid.NAMESPACE_URL, VERSION+'/'+result['analysis_id']))
            settings = dict(version=VERSION,pipeline_id=team_settings.PIPELINE,report_id=report_id,
                analysis_id=result['analysis_id'],worker_job_id=identifier,scene_sha256=scene_sha,
                reference_date=None,document_reference_date_supported=False,
                model='cpu-template',contract=team_settings.contract(state.get('analysis_profile', {}).get('version', 'B')))
            self.conn.execute('SELECT state FROM cctv_scene.team_report_runs LIMIT 0')
            permissions = self.conn.execute("SELECT has_table_privilege(current_user, 'cctv_scene.team_report_runs', 'INSERT') AND has_table_privilege(current_user, 'cctv_scene.team_report_runs', 'UPDATE') AS ok").fetchone()
            if not permissions['ok']:
                raise WorkerError('team_report_schema_permissions_required')
            existing = self.conn.execute('SELECT settings,state FROM cctv_scene.team_report_runs WHERE analysis_id=%s',(result['analysis_id'],)).fetchone()
            def persist(progress):
                from psycopg.types.json import Jsonb
                self.conn.execute('''INSERT INTO cctv_scene.team_report_runs
                    (report_id,analysis_id,worker_job_id,settings,state) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (report_id) DO UPDATE SET state=EXCLUDED.state,updated_at=now()
                    WHERE team_report_runs.settings=EXCLUDED.settings''',
                    (report_id,result['analysis_id'],identifier,Jsonb(settings),Jsonb(progress)))
                readback = self.conn.execute('SELECT settings,state FROM cctv_scene.team_report_runs WHERE report_id=%s',(report_id,)).fetchone()
                # JSON/JSONB represents ranking tuples as arrays. Compare the
                # serialized values, while rejecting unsupported/nonfinite
                # objects instead of converting them to strings.
                expected_settings = json.loads(json.dumps(settings, allow_nan=False))
                expected_state = json.loads(json.dumps(progress, allow_nan=False))
                if (not readback or readback['settings'] != expected_settings
                        or readback['state'] != expected_state):
                    raise WorkerError('team_report_database_readback_mismatch')
            journal = Journal(directory/'scenes'/event_id/'report-team-v1',settings,persist,existing)
            request = dict(scene_sha256=scene_sha,team_event=result['team_event'],contract=settings['contract'])
            def execute():
                from apps.team_rag.runtime import build_result
                # Combined measured stage: the existing RAG implementation and
                # its saved-review policy remain byte-for-byte unchanged.
                with mlflow_tracing.stage('rag_search_relevance_template', 'RETRIEVER') as trace:
                    if self.execute:
                        answer = self.execute(result['team_event'])
                    else:
                        deadline = hour_session.get(self.conn)['deadline_at']
                        answer = build_result(result['team_event'],deadline_at=deadline)
                    try:
                        observer = mlflow_tracing.observer()
                        if observer:
                            trace.output(**observer.report_summary(answer['report_json'],
                                report_markdown=answer.get('report_markdown')))
                    except Exception:
                        pass
                    return answer
            def reserve():
                # This is CPU work, with a separate name/model and one attempt.
                # No Gemini report/embedding call occurs in the new path.
                model_path = os.environ.get('RAG_MODEL_PATH')
                if not self.execute and (not model_path or not Path(model_path).is_dir()):
                    raise WorkerError('team_rag_local_model_not_prepared')
                if not self.execute:
                    from .team_model import preflight
                    preflight(model_path)
                if not hour_session.configured():
                    raise WorkerError('team_rag_requires_active_test_session')
                hour_session.claim(self.conn,row,event_id,'cpu_retrieval',fingerprint(request),
                                   model=hour_session.model_for('cpu_retrieval'))
            report = journal.step('cpu_retrieval',request,execute,
                                  lambda raw: validate_report(raw,result['team_event']),
                                  external=True,reserve=reserve)
            with mlflow_tracing.stage('report_contract_validation'):
                payload = envelope(report,result,report_id,identifier)
            with mlflow_tracing.stage('report_storage', report_id=report_id,
                                      analysis_id=result['analysis_id']) as trace:
                journal.state.update(status='report_stored',error_code=None,result=payload,
                                     result_sha256=fingerprint(payload),generation_calls=0,embedding_calls=0)
                journal.commit()
                save(journal.directory/'report.json',payload['report_json'])
                markdown_path = journal.directory/'report.md'
                if markdown_path.exists() and markdown_path.read_text() != payload['report_markdown']:
                    raise WorkerError('team_report_markdown_changed')
                if not markdown_path.exists():
                    markdown_path.write_text(payload['report_markdown'])
                trace.output(report_id=report_id, analysis_id=result['analysis_id'],
                             result_sha256=journal.state['result_sha256'], status='report_stored',
                             report_markdown=payload['report_markdown'])
            return dict(report_id=report_id,status='report_stored',database_readback_verified=True,
                        result_sha256=journal.state['result_sha256'],calls=journal.state['calls'],
                        generation_calls=0,embedding_calls=0,
                        document_count=len(payload['report_json'].get('sources',[])))
