"""Explicit model references and a durable metadata outbox; never executes models.

MLflow is optional until configured. A failed metadata delivery is kept separate
from the analysis/report state. Retrying this module only sends saved metadata.
Model versions point at existing weights; registration does not load or upload
them. In particular, ``baseimage:`` is provenance, not an MLflow model flavor.
"""
from copy import deepcopy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from .common import WorkerError, read_json, save

EXPERIMENT = 'cctv-model-executions'
SCHEMA = 'cctv-mlflow-execution-v1'
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}')
_SHA = re.compile(r'[a-f0-9]{64}')
_BINDING = {'registered_model_name', 'registered_model_version', 'model_id',
            'model_version', 'weights_sha256', 'contract_sha256', 'source_uri'}
_TAG_FIELDS = ('model_id', 'model_version', 'weights_sha256', 'contract_sha256')
_DURATIONS = {'gpu_seconds', 'pipeline_seconds', 'report_seconds', 'total_seconds'}


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise WorkerError('mlflow_invalid_json') from None


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise WorkerError('mlflow_invalid_identifier')
    return value


def _sha(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise WorkerError('mlflow_invalid_sha256')
    return value


def _source(value):
    if not isinstance(value, str) or len(value) > 1024 or any(c.isspace() for c in value):
        raise WorkerError('mlflow_invalid_source_uri')
    uri = urlsplit(value)
    if (uri.scheme not in ('s3', 'baseimage') or not uri.netloc or not uri.path
            or uri.username or uri.password or uri.query or uri.fragment
            or '..' in uri.path.split('/')):
        raise WorkerError('mlflow_invalid_source_uri')
    return value


def validate_binding(binding):
    if not isinstance(binding, dict) or set(binding) != _BINDING:
        raise WorkerError('mlflow_invalid_model_binding')
    value = deepcopy(binding)
    for field in ('registered_model_name', 'model_id', 'model_version'):
        _name(value[field])
    if not isinstance(value['registered_model_version'], str) or not re.fullmatch(
            r'[1-9][0-9]*', value['registered_model_version']):
        raise WorkerError('mlflow_invalid_registered_version')
    for field in ('weights_sha256', 'contract_sha256'):
        _sha(value[field])
    _source(value['source_uri'])
    return value


def verify_registered_model(client, binding):
    """Resolve an exact numeric version; aliases/latest are never execution IDs."""
    binding = validate_binding(binding)
    model = client.get_model_version(binding['registered_model_name'],
                                     binding['registered_model_version'])
    if (model.name != binding['registered_model_name']
            or str(model.version) != binding['registered_model_version']
            or model.status != 'READY' or model.source != binding['source_uri']
            or any(model.tags.get('cctv.' + k) != binding[k] for k in _TAG_FIELDS)):
        raise WorkerError('mlflow_registered_model_mismatch')
    return binding


@contextmanager
def _locked(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise WorkerError('mlflow_unsafe_outbox')
    with (directory / '.lock').open('a') as stream:
        os.chmod(stream.name, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkerError('mlflow_outbox_busy') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def register_model_reference(directory, spec, *, client):
    """Explicit administrative step, idempotent by business version and tags.

``spec`` is a binding without registered_model_version. An ambiguous create is
only reconciled by search on the next call; an absent result is not recreated.
The caller must verify the referenced artifact bytes before registration.
"""
    if not isinstance(spec, dict) or set(spec) != _BINDING - {'registered_model_version'}:
        raise WorkerError('mlflow_invalid_registration_spec')
    spec = validate_binding(dict(spec, registered_model_version='1'))
    spec.pop('registered_model_version')
    root = Path(directory) / ('registration-' + _hash(spec))
    with _locked(root):
        path = root / 'receipt.json'
        receipt = read_json(path) if path.exists() else {'spec': spec, 'create_attempted': False}
        if receipt['spec'] != spec:
            raise WorkerError('mlflow_registration_identity_changed')
        if receipt.get('binding'):
            return verify_registered_model(client, receipt['binding'])
        name = spec['registered_model_name']
        matches = []
        page = None
        while True:
            versions = client.search_model_versions(f"name = '{name}'", max_results=100,
                                                     page_token=page)
            matches.extend(v for v in versions if
                           v.tags.get('cctv.model_id') == spec['model_id'] and
                           v.tags.get('cctv.model_version') == spec['model_version'])
            page = getattr(versions, 'token', None)
            if not page:
                break
        if len(matches) > 1:
            raise WorkerError('mlflow_duplicate_registered_version')
        if matches:
            binding = dict(spec, registered_model_version=str(matches[0].version))
            verify_registered_model(client, binding)
        else:
            if receipt['create_attempted']:
                raise WorkerError('mlflow_registration_outcome_unknown')
            try:
                client.get_registered_model(name)
            except Exception as exc:
                if getattr(exc, 'error_code', None) != 'RESOURCE_DOES_NOT_EXIST':
                    raise
                client.create_registered_model(name, description='CCTV existing trained weights; reference only')
            receipt['create_attempted'] = True
            save(path, receipt)  # Intent precedes the non-idempotent create call.
            created = client.create_model_version(name, spec['source_uri'],
                tags={'cctv.' + k: spec[k] for k in _TAG_FIELDS}, await_creation_for=0,
                description='Existing checkpoint; no training or weight upload performed by registration')
            binding = dict(spec, registered_model_version=str(created.version))
            verify_registered_model(client, binding)
        receipt['binding'] = binding
        save(path, receipt)
        return binding


def _safe_json(value, depth=0):
    """A metadata boundary: reject secrets/URLs/host paths instead of redacting."""
    if depth > 12:
        raise WorkerError('mlflow_metadata_too_deep')
    if isinstance(value, dict):
        for key, child in value.items():
            if (not isinstance(key, str) or len(key) > 160 or re.search(
                    r'(?:^|_)(?:secret|password|credentials?|authorization|api_?key|'
                    r'(?:access_|auth_|refresh_)?token)(?:$|_)', key, re.I)):
                raise WorkerError('mlflow_private_metadata_disallowed')
            _safe_json(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > 10000:
            raise WorkerError('mlflow_metadata_too_large')
        for child in value:
            _safe_json(child, depth + 1)
    elif isinstance(value, str):
        if len(value) > 4096 or value.startswith(('/', '~', 'file:', 'http:', 'https:')):
            raise WorkerError('mlflow_private_metadata_disallowed')
        if '://' in value:
            _source(value)
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise WorkerError('mlflow_invalid_metadata')
    _canonical(value)


def execution_record(*, worker_job_id, model_binding, input_sha256, config,
                     outcome, result_refs=None, durations=None, execution_id=None,
                     error_code=None, started_at_ms=None, finished_at_ms=None):
    """Freeze one final observation. IDs/references are logged, not video bytes.

Pass a task ID as execution_id when one GPU job has multiple report tasks.
Accuracy/F1 are deliberately not accepted by this API. They require a separate
evaluation operation with common labeled data; this is an inference record.
"""
    if outcome not in ('succeeded', 'failed', 'no_candidates'):
        raise WorkerError('mlflow_invalid_execution_outcome')
    durations = dict(durations or {})
    if (not set(durations) <= _DURATIONS or any(isinstance(v, bool) or
            not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
            for v in durations.values())):
        raise WorkerError('mlflow_invalid_duration_metric')
    config, result_refs = deepcopy(config), deepcopy(result_refs or {})
    if not isinstance(config, dict) or not isinstance(result_refs, dict):
        raise WorkerError('mlflow_invalid_metadata')
    _safe_json(config)
    _safe_json(result_refs)
    if error_code is not None:
        _name(error_code)
    for value in (started_at_ms, finished_at_ms):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise WorkerError('mlflow_invalid_timestamp')
    if started_at_ms is not None and finished_at_ms is not None and finished_at_ms < started_at_ms:
        raise WorkerError('mlflow_invalid_timestamp')
    record = dict(schema_version=SCHEMA, execution_id=_name(execution_id or worker_job_id),
        worker_job_id=_name(worker_job_id), model_binding=validate_binding(model_binding),
        input_sha256=_sha(input_sha256), config=config, config_sha256=_hash(config),
        outcome=outcome, result_refs=result_refs, durations=durations, error_code=error_code,
        started_at_ms=started_at_ms, finished_at_ms=finished_at_ms,
        evaluation_status='not_evaluated', evaluation_scope='inference_only')
    if len(_canonical(record)) > 512 * 1024:
        raise WorkerError('mlflow_metadata_too_large')
    return record


def client_for_uri(tracking_uri):
    """SDK import is deferred; offline tests and normal legacy use need no MLflow."""
    uri = urlsplit(tracking_uri)
    if (uri.scheme not in ('http', 'https') or not uri.hostname or uri.username
            or uri.password or uri.query or uri.fragment or uri.path not in ('', '/')):
        raise WorkerError('mlflow_invalid_tracking_uri')
    # A disconnected tracker must not hold the analysis worker in SDK retries.
    os.environ['MLFLOW_HTTP_REQUEST_TIMEOUT'] = '5'
    os.environ['MLFLOW_HTTP_REQUEST_MAX_RETRIES'] = '0'
    from mlflow import MlflowClient
    return MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)


def record_execution(directory, record, *, client=None, tracking_uri=None,
                     experiment_name=EXPERIMENT):
    """Persist first, then attempt metadata delivery; never raise into GPU flow.

Returns status recorded/pending and a safe code. Persisted records can be sent
again with retry_pending(), without looking up or submitting a Runpod job.
"""
    try:
        if not isinstance(record, dict) or record.get('schema_version') != SCHEMA:
            raise WorkerError('mlflow_invalid_execution_record')
        args = {k: v for k, v in record.items() if k not in
                ('schema_version', 'config_sha256', 'evaluation_status', 'evaluation_scope')}
        if execution_record(**args) != record:
            raise WorkerError('mlflow_execution_record_changed')
        _name(experiment_name)
        root = Path(directory) / 'mlflow-outbox' / record['execution_id']
        with _locked(root):
            identity = {k: record[k] for k in ('execution_id', 'worker_job_id',
                         'model_binding', 'input_sha256', 'config_sha256')}
            path = root / 'receipt.json'
            receipt = read_json(path) if path.exists() else {
                'identity': identity, 'experiment_name': experiment_name, 'run_id': None,
                'create_attempted': False, 'status': 'pending'}
            if receipt['identity'] != identity or receipt['experiment_name'] != experiment_name:
                raise WorkerError('mlflow_execution_identity_changed')
            observation_sha = _hash(record)
            observation = root / (observation_sha + '.json')
            if not observation.exists():
                save(observation, record)
            receipt['pending_observation'] = observation_sha
            save(path, receipt)
            if receipt.get('recorded_observation') == observation_sha:
                return {'status': 'recorded', 'run_id': receipt['run_id'], 'error_code': None}
            try:
                if client is None:
                    tracking_uri = tracking_uri or os.environ.get('BE_MLFLOW_TRACKING_URI')
                    if not tracking_uri:
                        raise WorkerError('mlflow_not_configured')
                    client = client_for_uri(tracking_uri)
                _deliver(client, record, receipt, path, observation_sha)
                receipt.update(status='recorded', recorded_observation=observation_sha, error_code=None)
            except Exception as exc:
                # Raw exception strings may contain credentials or signed URLs.
                receipt.update(status='pending', error_code=exc.code if isinstance(exc, WorkerError)
                               else 'mlflow_delivery_failed')
            save(path, receipt)
            return {key: receipt.get(key) for key in ('status', 'run_id', 'error_code')}
    except Exception as exc:
        return {'status': 'pending', 'run_id': None, 'error_code':
                exc.code if isinstance(exc, WorkerError) else 'mlflow_outbox_write_failed'}


def display_summary(record):
    """Explain this execution without treating recovery as another model call."""
    binding, refs = record['model_binding'], record['result_refs']
    purpose = refs.get('purpose') or record['config'].get('operation') or 'execution'
    labels = {'full_report': '영상 분석·보고', 'gpu_comparison': 'GPU 모델 비교',
              'gpu_only': 'GPU 분석', 'report_recovery': '저장 응답·보고 복구',
              'saved_response_cpu_report_recovery': '저장 응답·보고 복구',
              'report_relevance_review': '저장 보고 근거 검수'}
    purpose_label = labels.get(purpose, purpose)
    model = {'x3d_s': 'X3D-S', 'r2plus1d_18': 'R(2+1)D-18', 'r3d_18': 'R3D-18 lastconv631'}.get(
        binding['model_id'], binding['model_id'])
    name = f"{model} · {purpose_label} · {record['execution_id'][:8]}"
    lines = [f"작업: {purpose_label}",
             f"모델: {model} / {binding['model_version']} / Registry v{binding['registered_model_version']}",
             f"결과: {record['outcome']}" + (f" ({record['error_code']})" if record['error_code'] else ''),
             f"입력 SHA-256: `{record['input_sha256']}`",
             f"설정 SHA-256: `{record['config_sha256']}`",
             f"실행 ID: `{record['execution_id']}`",
             "Traces에서 실제 요청·응답, 단계별 시간, 제공자가 반환한 토큰 사용량을 확인합니다.",
             "Artifacts의 observations JSON에는 고정된 설정과 결과 식별자가 있습니다."]
    if refs.get('new_model_calls') == 0:
        lines.append('이 실행의 새 모델 호출은 0회입니다. 이전 호출의 토큰을 이 실행에 합산하지 않습니다.')
    source_run = refs.get('source_failed_run_id') or refs.get('source_run_id')
    if isinstance(source_run, str) and re.fullmatch(r'[a-f0-9]{32}', source_run):
        lines.append(f"원본 호출 Run: `{source_run}`")
    report_ids = [refs.get('report_id')] + list(refs.get('report_ids', []))
    report_ids += [r.get('report_id') for r in refs.get('reports', []) if isinstance(r, dict)]
    report_ids = list(dict.fromkeys(x for x in report_ids if isinstance(x, str)))
    if report_ids:
        lines.append('연결 보고: ' + ', '.join(f'`{value}`' for value in report_ids))
    if record['durations']:
        lines.append('기록 시간(초): ' + ', '.join(
            f'{key}={value:.3f}' for key, value in record['durations'].items()))
    lines += ['Duration은 Run 시작부터 종료까지의 경과시간이며 대기·수동 복구가 포함될 수 있습니다.',
              '정확도·F1: 이 추론 실행에서는 평가하지 않았습니다.']
    return dict(name=name, description='\n\n'.join(lines), purpose=purpose)


def _deliver(client, record, receipt, path, observation_sha):
    binding = verify_registered_model(client, record['model_binding'])
    experiment = client.get_experiment_by_name(receipt['experiment_name'])
    if experiment is None:
        identifier = client.create_experiment(receipt['experiment_name'])
    else:
        if experiment.lifecycle_stage != 'active':
            raise WorkerError('mlflow_experiment_inactive')
        identifier = str(experiment.experiment_id)
    identity_sha = _hash(receipt['identity'])
    if receipt['run_id']:
        run = client.get_run(receipt['run_id'])
    else:
        runs = client.search_runs([identifier],
            filter_string=f"tags.cctv.identity_sha256 = '{identity_sha}'", max_results=2)
        if len(runs) > 1:
            raise WorkerError('mlflow_duplicate_execution_run')
        if runs:
            run = runs[0]
        else:
            if receipt['create_attempted']:
                raise WorkerError('mlflow_run_creation_outcome_unknown')
            receipt['create_attempted'] = True
            save(path, receipt)
            run = client.create_run(identifier, start_time=record['started_at_ms'],
                tags={'cctv.identity_sha256': identity_sha,
                      'cctv.execution_id': record['execution_id'],
                      'cctv.worker_job_id': record['worker_job_id']},
                run_name=display_summary(record)['name'])
        receipt['run_id'] = run.info.run_id
        save(path, receipt)
    if (run.data.tags.get('cctv.identity_sha256') != identity_sha
            or str(run.info.experiment_id) != identifier
            or run.info.lifecycle_stage != 'active'):
        raise WorkerError('mlflow_run_identity_mismatch')
    run_id = receipt['run_id']
    if run.data.tags.get('cctv.observation_sha256') == observation_sha:
        return  # Reconcile an acknowledged delivery whose local save failed.
    for key, value in {
        'model_id': binding['model_id'], 'model_version': binding['model_version'],
        'registered_model_name': binding['registered_model_name'],
        'registered_model_version': binding['registered_model_version'],
        'weights_sha256': binding['weights_sha256'], 'contract_sha256': binding['contract_sha256'],
        'input_sha256': record['input_sha256'], 'config_sha256': record['config_sha256'],
    }.items():
        client.log_param(run_id, key, value)
    client.log_dict(run_id, record, 'observations/' + observation_sha + '.json')
    timestamp = record['finished_at_ms'] or int(time.time() * 1000)
    for key, value in record['durations'].items():
        client.log_metric(run_id, key, float(value), timestamp=timestamp, step=0)
    for key, value in {'outcome': record['outcome'],
                       'evaluation_status': 'not_evaluated', 'evaluation_scope': 'inference_only',
                       'error_code': record['error_code'] or ''}.items():
        client.set_tag(run_id, 'cctv.' + key, value)
    summary = display_summary(record)
    client.set_tag(run_id, 'cctv.purpose', summary['purpose'])
    client.set_tag(run_id, 'mlflow.note.content', summary['description'])
    client.set_terminated(run_id, status='FAILED' if record['outcome'] == 'failed' else 'FINISHED',
                          end_time=record['finished_at_ms'])
    # Commit tag is last: earlier partial sends may be safely repeated.
    client.set_tag(run_id, 'cctv.observation_sha256', observation_sha)


def retry_pending(directory, *, client=None, tracking_uri=None, limit=2):
    """Bounded metadata-only sweep, suitable for a separate worker idle step."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
        raise WorkerError('mlflow_invalid_retry_limit')
    results = []
    root = Path(directory) / 'mlflow-outbox'
    if not root.is_dir() or root.is_symlink():
        return results
    for folder in sorted(root.iterdir()):
        if len(results) >= limit:
            break
        if folder.is_symlink() or not folder.is_dir() or not _NAME.fullmatch(folder.name):
            continue
        try:
            receipt = read_json(folder / 'receipt.json')
            if receipt.get('status') == 'recorded':
                continue
            sha = _sha(receipt['pending_observation'])
            record = read_json(folder / (sha + '.json'))
            if _hash(record) != sha:
                raise WorkerError('mlflow_outbox_observation_changed')
            result = record_execution(directory, record, client=client, tracking_uri=tracking_uri,
                                      experiment_name=receipt['experiment_name'])
        except Exception as exc:
            result = {'status': 'pending', 'run_id': None, 'error_code':
                      exc.code if isinstance(exc, WorkerError) else 'mlflow_outbox_read_failed'}
        results.append(dict(execution_id=folder.name, **result))
    return results
