"""Strict, read-only routes; callable only after the demo window check."""
import re
from urllib.parse import parse_qs, urlsplit
from .common import WorkerError
from .mlflow_read import MLflowReader


def read_mlflow(target, reader=None):
    parts = urlsplit(target)
    query = parse_qs(parts.query, keep_blank_values=True)
    if any(len(v) != 1 for v in query.values()):
        raise WorkerError('invalid_demo_read_query')
    query = {k: v[0] for k, v in query.items()}
    path = parts.path.removeprefix('/demo')
    reader = reader or MLflowReader()
    if path == '/mlflow/models' and not query:
        return reader.models()
    if path == '/mlflow/runs' and set(query) <= {'worker_job_id'}:
        return reader.runs(worker_job_id=query.get('worker_job_id'))
    match = re.fullmatch(r'/mlflow/runs/([a-f0-9]{32})(/artifact)?', path)
    if match:
        if match[2] and set(query) == {'path'}:
            return reader.artifact(match[1], query['path'])
        if not match[2] and not query:
            return reader.run(match[1])
    match = re.fullmatch(r'/mlflow/traces/((?:tr-)?[a-f0-9]{32})', path)
    if match and set(query) == {'run_id'} and re.fullmatch('[a-f0-9]{32}', query['run_id']):
        return reader.trace(match[1], run_id=query['run_id'])
    raise WorkerError('invalid_demo_read_route')
