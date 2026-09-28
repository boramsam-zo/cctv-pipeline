"""Allowlisted local spans; optional LangSmith export AFTER business processing.

REST avoids new dependencies in this small CPU CLI. Never wraps model/HTTP inputs.
Exporting a trace does not submit a video or invoke a model.
"""
from contextlib import contextmanager
from datetime import datetime
import math
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid

from .common import VERSION, WorkerError, digest, now, read_json, save
from .runpod_api import NoRedirect

ENDPOINTS = {
    'https://api.smith.langchain.com', 'https://eu.api.smith.langchain.com',
    'https://apac.api.smith.langchain.com', 'https://aws.api.smith.langchain.com',
}
STATES = {'prepared', 'submitting', 'submission_unknown', 'submission_rejected', 'waiting',
          'provider_failed', 'evidence_ready', 'storage_not_ready', 'verification_failed',
          'handoff_ready', 'no_candidates', 'interrupted'}
STEPS = {'runpod.health', 'runpod.submit', 'runpod.poll', 's3.verify',
         'be.run', 'be.resume', 'be.collect'}


def safe_fields(value):
    """Reconstruct both local and remote telemetry; drop any unreviewed fields."""
    safe = {}
    for key, item in value.items():
        if key in ('worker_job_id', 'provider_job_id'):
            if isinstance(item, str) and re.fullmatch(r'[0-9a-f]{32}', item):
                safe[key] = item
        elif key in ('input_sha256', 'code_sha256', 'manifest_sha256'):
            if isinstance(item, str) and re.fullmatch(r'[0-9a-f]{64}', item):
                safe[key] = item
        elif key in ('input_bytes', 'poll_count', 'event_count', 'gemini_api_calls'):
            if type(item) is int and 0 <= item <= 200 * 1024 * 1024:
                safe[key] = item
        elif key == 'status' and isinstance(item, str) and item in STATES:
            safe[key] = item
        elif key == 'version' and item == VERSION:
            safe[key] = item
        elif key == 's3_reference_verified' and type(item) is bool:
            safe[key] = item
        elif key == 'duration_seconds' and type(item) in (int, float) and math.isfinite(item) and item >= 0:
            safe[key] = item
    return safe


def code_sha():
    root = Path(__file__).parent
    return digest(b''.join(path.name.encode() + path.read_bytes() for path in sorted(root.glob('*.py'))))


class Recorder:
    def __init__(self, directory, action, state):
        if 'be.' + action not in STEPS:
            raise WorkerError('invalid_trace_action')
        self.directory = Path(directory) / 'traces'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.root = self.record('be.' + action, state)
        self.rows = [self.root]
        self.path = self.directory / (self.root['id'] + '.json')
        self.persist()

    def record(self, name, inputs, parent=None):
        row = {'id': str(uuid.uuid4()), 'name': name, 'run_type': 'chain',
               'start_time': now(), 'inputs': safe_fields(inputs), 'outputs': {},
               'extra': {'metadata': {'version': VERSION, 'code_sha256': code_sha()}}}
        if parent:
            row['parent_run_id'] = parent
        return row

    def persist(self):
        save(self.path, {'schema_version': 'be-trace-v1', 'runs': self.rows})

    @contextmanager
    def span(self, name, inputs=None):
        if name not in STEPS:
            raise WorkerError('invalid_trace_step')
        row = self.record(name, inputs or {}, self.root['id'])
        self.rows.append(row)
        outputs = {}
        try:
            yield outputs
        except BaseException:
            row['error'] = 'step_failed'
            raise
        finally:
            row['end_time'] = now()
            row['outputs'] = safe_fields(outputs)
            self.persist()

    def finish(self, state):
        self.root['outputs'] = safe_fields(state)
        self.root['end_time'] = now()
        self.root['extra']['metadata'].update(safe_fields({
            'worker_job_id': state['worker_job_id'], 'provider_job_id': state.get('provider_job_id')}))
        if state.get('error_code') or state['status'] not in ('waiting', 'prepared', 'evidence_ready', 'handoff_ready', 'no_candidates'):
            self.root['error'] = 'worker_requires_attention'
        self.persist()


class LangSmith:
    def __init__(self, key, project='cctv-be-runpod', endpoint='https://api.smith.langchain.com', workspace=None):
        if endpoint not in ENDPOINTS:
            raise WorkerError('unsupported_langsmith_endpoint')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', project):
            raise WorkerError('invalid_langsmith_project')
        if not key or not key.isascii() or any(c.isspace() for c in key):
            raise WorkerError('invalid_langsmith_key')
        self.endpoint, self.project = endpoint, project
        self.headers = {'x-api-key': key, 'Content-Type': 'application/json'}
        if workspace:
            self.headers['x-tenant-id'] = str(uuid.UUID(workspace))
        self.target = digest((endpoint + '/' + project + '/' + (workspace or '')).encode())[:16]
        self.opener = urllib.request.build_opener(NoRedirect())

    def send(self, payload):
        import json
        data = json.dumps(payload, allow_nan=False).encode()
        try:
            request = urllib.request.Request(self.endpoint + '/runs', data=data, headers=self.headers, method='POST')
            with self.opener.open(request, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise WorkerError('trace_delivery_failed')
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code != 409:
                raise WorkerError('trace_delivery_failed') from None
            # Same UUID after a lost acknowledgement: update this exact span, no new ID.
            update = {key: payload[key] for key in ('outputs', 'end_time', 'error') if key in payload}
            request = urllib.request.Request(self.endpoint + '/runs/' + payload['id'],
                data=json.dumps(update).encode(), headers=self.headers, method='PATCH')
            with self.opener.open(request, timeout=10):
                pass


def export_traces(directory, sink):
    """Retry only completed spans with stable IDs; never executes workflow steps."""
    directory = Path(directory) / 'traces'
    ack_path = directory / ('delivery-' + sink.target + '.json')
    ack = read_json(ack_path) if ack_path.exists() else {'delivered': []}
    done = set(ack['delivered'])
    sent = 0
    for path in sorted(directory.glob('*.json')):
        if path.name.startswith('delivery-'):
            continue
        record = read_json(path)
        if record.get('schema_version') != 'be-trace-v1':
            raise WorkerError('invalid_trace_file')
        # A killed CLI may leave an open root. Preserve it locally, don't emit orphan children.
        if not record['runs'] or 'end_time' not in record['runs'][0]:
            continue
        for row in record['runs']:
            identifier = str(uuid.UUID(row['id']))
            if identifier in done or 'end_time' not in row:
                continue
            if row['name'] not in STEPS:
                raise WorkerError('invalid_trace_step')
            payload = {'id': identifier, 'name': row['name'], 'run_type': 'chain',
                       'session_name': sink.project, 'start_time': datetime.fromisoformat(row['start_time']).isoformat(),
                       'end_time': datetime.fromisoformat(row['end_time']).isoformat(),
                       'inputs': safe_fields(row['inputs']), 'outputs': safe_fields(row['outputs']),
                       'extra': {'metadata': safe_fields(row['extra']['metadata'])}}
            if row.get('parent_run_id'):
                payload['parent_run_id'] = str(uuid.UUID(row['parent_run_id']))
            if row.get('error'):
                payload['error'] = 'worker_step_requires_attention'
            try:
                sink.send(payload)
            except Exception:
                save(ack_path, {'delivered': sorted(done), 'last_status': 'pending'})
                return {'status': 'pending', 'sent': sent}
            done.add(identifier)
            sent += 1
            save(ack_path, {'delivered': sorted(done), 'last_status': 'delivered'})
    return {'status': 'delivered', 'sent': sent}
