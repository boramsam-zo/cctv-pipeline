"""Bounded, read-only MLflow projection for the public demonstration gateway.

No tracking writes, model loads, arbitrary experiment lookup, or artifact URLs.
The gateway must additionally enforce the demonstration's opening window.
"""
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from . import mlflow_trace_content as readable
from .mlflow_tracking import EXPERIMENT, client_for_uri

_RUN = re.compile(r'[a-f0-9]{32}')
_TRACE = re.compile(r'(?:tr-)?[a-f0-9]{32}')
_JOB = re.compile(r'[A-Za-z0-9_.-]{1,160}')
_PRIVATE = re.compile(r'(?:file://)?/(?:Users|Volumes|workspace|spool|code|root|home|tmp|private|var|opt|etc)(?:/[^\s<>\"\'`]+)?')
_ABSOLUTE_PATH = re.compile(r'(?<![A-Za-z0-9:])/(?:[A-Za-z0-9_.-]+/)+[^\s<>\"\'`]*')
_STORAGE = re.compile(r'(?:s3|gs|baseimage|file)://[^\s<>\"\'`]+')
_URL = re.compile(r'https?://[^\s<>\"\'`]+')
_SECRET = re.compile(r'(?i)(?:^|[_.-])(?:api[_-]?key|secret|password|credential|authorization|access[_-]?token|refresh[_-]?token|session[_-]?token|api[_-]?token)(?:$|[_.-])')
_ARTIFACT_ROOTS = frozenset(('observations', 'content-enrichment',
                           'historical-development-evaluation', 'live-failure-explanation'))
MAX_ARTIFACT_BYTES = 512 * 1024
MAX_ARTIFACTS = 24


class ReadError(RuntimeError):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def public_content(value):
    """Reuse readable projection and remove host/storage locations before HTTP."""
    value = readable.content(value)
    def clean(item):
        if isinstance(item, str):
            item = _STORAGE.sub('[storage location omitted]', item)
            item = _URL.sub('[URL omitted]', item)
            item = _PRIVATE.sub('[internal path omitted]', item)
            return _ABSOLUTE_PATH.sub('[internal path omitted]', item)
        if isinstance(item, list):
            return [clean(x) for x in item]
        if isinstance(item, dict):
            return {clean(k): '[credential omitted]' if (_SECRET.search(k) or k.lower() in ('token', 'cookie', 'set-cookie')
                        or re.search(r'(?i)[_.-]token$', k)) else clean(v)
                    for k, v in item.items()}
        return item
    return clean(value)


def _identifier(value, pattern):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ReadError('mlflow_read_invalid_identifier', 400)
    return value


def _mapping(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {'text': value}
    return value if isinstance(value, dict) else {}


def _status(value):
    value = getattr(value, 'status_code', value)
    return str(getattr(value, 'value', value))


def _duration(start, end, divisor=1000):
    if type(start) in (int, float) and type(end) in (int, float) and end >= start:
        value = (end - start) / divisor
        return value if math.isfinite(value) else None
    return None


def _artifact_path(path, *, directory=False):
    if not isinstance(path, str) or len(path) > 320 or '\\' in path:
        return False
    parts = PurePosixPath(path).parts
    if (not parts or parts[0] not in _ARTIFACT_ROOTS or any(p in ('.', '..') for p in path.split('/'))
            or path.startswith('/') or len(parts) > 4
            or any(re.fullmatch(r'[A-Za-z0-9_.-]+', part) is None for part in parts)):
        return False
    return directory or (len(parts) >= 2 and Path(path).suffix in ('.json', '.md', '.txt'))


def _read_operation(method):
    """Exceptions must never expose tracker addresses, credentials or host paths."""
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except ReadError:
            raise
        except Exception:
            raise ReadError('mlflow_read_unavailable') from None
    return wrapped


class MLflowReader:
    def __init__(self, tracking_uri=None, *, client=None):
        self._client = client
        self._uri = tracking_uri or os.environ.get('BE_MLFLOW_TRACKING_URI')

    @property
    def client(self):
        if self._client is None:
            if not self._uri:
                raise ReadError('mlflow_read_not_configured')
            self._client = client_for_uri(self._uri)
        return self._client

    def _experiment(self):
        experiment = self.client.get_experiment_by_name(EXPERIMENT)
        if experiment is None or experiment.lifecycle_stage != 'active':
            raise ReadError('mlflow_read_experiment_missing', 404)
        return str(experiment.experiment_id)

    def _get_run(self, run_id):
        _identifier(run_id, _RUN)
        run = self.client.get_run(run_id)
        if (str(run.info.experiment_id) != self._experiment()
                or run.info.lifecycle_stage != 'active'):
            raise ReadError('mlflow_read_run_not_shared', 404)
        return run

    @staticmethod
    def _summary(run):
        tags, params = run.data.tags, run.data.params
        trace_ids = [x for x in tags.get('cctv.trace_ids', '').split(',') if _TRACE.fullmatch(x)]
        return public_content(dict(run_id=run.info.run_id, name=tags.get('mlflow.runName'),
            status=run.info.status, started_at_ms=run.info.start_time, ended_at_ms=run.info.end_time,
            duration_seconds=_duration(run.info.start_time, run.info.end_time),
            worker_job_id=tags.get('cctv.worker_job_id'), execution_id=tags.get('cctv.execution_id'),
            model_id=params.get('model_id'), model_version=params.get('model_version'),
            input_sha256=params.get('input_sha256'), purpose=tags.get('cctv.purpose'),
            outcome=tags.get('cctv.outcome'), evaluation_scope=tags.get('cctv.evaluation_scope'),
            metrics=run.data.metrics, trace_ids=trace_ids))

    @_read_operation
    def models(self):
        versions = self.client.search_model_versions(max_results=100)
        rows = []
        for version in versions:
            tags = version.tags or {}
            if not version.name.startswith('cctv-') or not tags.get('cctv.model_id'):
                continue
            rows.append(public_content(dict(name=version.name, version=str(version.version),
                status=version.status, description=version.description,
                model_id=tags.get('cctv.model_id'), model_version=tags.get('cctv.model_version'),
                weights_sha256=tags.get('cctv.weights_sha256'),
                contract_sha256=tags.get('cctv.contract_sha256'))))
        return {'models': rows, 'truncated': bool(getattr(versions, 'token', None)),
                'read_only': True, 'source_location_omitted': True}

    @_read_operation
    def runs(self, worker_job_id=None, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ReadError('mlflow_read_invalid_limit', 400)
        filters = ''
        if worker_job_id:
            _identifier(worker_job_id, _JOB)
            filters = f"tags.cctv.worker_job_id = '{worker_job_id}'"
        runs = self.client.search_runs([self._experiment()], filter_string=filters,
                                      max_results=limit, order_by=['attributes.start_time DESC'])
        return {'runs': [self._summary(run) for run in runs],
                'truncated': bool(getattr(runs, 'token', None)), 'read_only': True}

    def _artifacts(self, run_id):
        results, directories, calls, truncated = [], [''], 0, False
        while directories and calls < 16 and len(results) < MAX_ARTIFACTS:
            folder = directories.pop(0)
            listed = self.client.list_artifacts(run_id, folder or None)
            calls += 1
            for row in listed[:MAX_ARTIFACTS + 1]:
                if row.is_dir:
                    if _artifact_path(row.path, directory=True):
                        directories.append(row.path)
                elif _artifact_path(row.path):
                    size = getattr(row, 'file_size', None)
                    results.append({'path': row.path, 'bytes': size,
                                    'readable': type(size) is int and 0 <= size <= MAX_ARTIFACT_BYTES})
                    if len(results) >= MAX_ARTIFACTS:
                        truncated = True
                        break
            truncated |= len(listed) > MAX_ARTIFACTS
        return results, truncated or bool(directories)

    @_read_operation
    def run(self, run_id):
        run = self._get_run(run_id)
        artifacts, truncated = self._artifacts(run_id)
        tags = {k: v for k, v in run.data.tags.items()
                if k.startswith('cctv.') or k in ('mlflow.runName', 'mlflow.note.content')}
        return {**self._summary(run), 'params': public_content(run.data.params),
                'tags': public_content(tags), 'description': public_content(tags.get('mlflow.note.content')),
                'artifacts': artifacts, 'artifacts_truncated': truncated, 'read_only': True,
                'duration_note': 'Duration은 Run 경과시간입니다. GPU 포함 여부와 단계별 측정 범위는 설정·출처를 함께 확인하세요.'}

    @_read_operation
    def artifact(self, run_id, path):
        self._get_run(run_id)
        if not _artifact_path(path):
            raise ReadError('mlflow_read_artifact_not_shared', 404)
        # Validate exact file metadata, not a browser-provided storage URI.
        rows = self.client.list_artifacts(run_id, str(PurePosixPath(path).parent))
        row = next((x for x in rows if x.path == path and not x.is_dir), None)
        if row is None:
            raise ReadError('mlflow_read_artifact_not_shared', 404)
        if type(row.file_size) is not int or not 0 <= row.file_size <= MAX_ARTIFACT_BYTES:
            raise ReadError('mlflow_read_artifact_too_large', 413)
        with tempfile.TemporaryDirectory(prefix='cctv-mlflow-view-') as temporary:
            downloaded = Path(self.client.download_artifacts(run_id, path, temporary))
            if (downloaded.is_symlink() or not downloaded.is_file()
                    or not downloaded.resolve().is_relative_to(Path(temporary).resolve())
                    or downloaded.stat().st_size > MAX_ARTIFACT_BYTES):
                raise ReadError('mlflow_read_artifact_unsafe', 413)
            text = downloaded.read_text(encoding='utf-8')
        value = json.loads(text) if path.endswith('.json') else text
        return {'run_id': run_id, 'path': path, 'format': 'json' if path.endswith('.json') else 'text',
                'content': public_content(value), 'read_only': True,
                'projection_note': '저장 자료 조회입니다. 비밀 값·미디어·URL·내부 경로는 제외하며 긴 내용은 생략 표시합니다.'}

    @_read_operation
    def trace(self, trace_id, run_id=None):
        _identifier(trace_id, _TRACE)
        # Mandatory Run membership prevents exposing unrelated application traces.
        run = self._get_run(run_id)
        if trace_id not in self._summary(run)['trace_ids']:
            raise ReadError('mlflow_read_trace_not_shared', 404)
        trace = self.client.get_trace(trace_id, display=False, flush=False)
        if (trace.info.trace_id != trace_id
                or (getattr(trace.info, 'trace_metadata', {}) or {}).get('mlflow.sourceRun') != run_id):
            raise ReadError('mlflow_read_trace_not_shared', 404)
        rows = []
        for span in trace.data.spans[:128]:
            rows.append(public_content(dict(name=span.name, span_id=span.span_id,
                parent_id=span.parent_id, kind=str(span.span_type), status=_status(span.status),
                duration_seconds=_duration(span.start_time_ns, span.end_time_ns, 1e9),
                inputs=_mapping(span.inputs), outputs=_mapping(span.outputs),
                token_usage=(span.attributes or {}).get('mlflow.chat.tokenUsage'))))
        return {'run_id': run_id, 'trace_id': trace_id, 'status': _status(trace.info.state),
                'spans': rows, 'truncated': len(trace.data.spans) > 128, 'read_only': True,
                'missing_note': '미기록 항목은 추정하지 않습니다. 과거 Trace 본문은 Run의 저장 자료에 보완되어 있을 수 있습니다.'}
