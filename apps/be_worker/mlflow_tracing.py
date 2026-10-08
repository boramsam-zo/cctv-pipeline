"""Bounded report traces with actual text, provenance and measured stage timing.

Collect locally; export once after the operation ends. Media, credentials and
signed URLs are omitted. Tracking never changes business/model work or retries it.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import os
from pathlib import Path
import re
import time

from .common import read_json, save
from . import mlflow_trace_content as readable

SCHEMA = 'cctv-report-trace-v1'
_ACTIVE = ContextVar('cctv_report_trace', default=None)
_ID = re.compile(r'[A-Za-z0-9_.-]{1,160}')
_FIELDS = {'worker_job_id', 'execution_id', 'event_id', 'input_sha256', 'model_id',
           'model_version', 'analysis_id', 'report_id', 'result_sha256',
           'scene_sha256', 'provider_model', 'provider_model_version',
           'chunk_id', 'reference_id', 'agency_key', 'topic', 'rank', 'score',
           'rrf', 'bm25', 'embedding', 'accepted', 'reason', 'reasons',
           'selected', 'checks', 'sources', 'excluded', 'search', 'documents',
           'document_count', 'query_count', 'dimensions', 'status', 'reused',
           'promptTokenCount', 'candidatesTokenCount', 'totalTokenCount',
           'thoughtsTokenCount', 'cachedContentTokenCount', 'usage',
           'error_type', 'error_code', 'new_model_calls', 'request', 'response',
           'report_markdown', 'scene_json', 'queries', 'title', 'excerpt',
           'agency', 'query', 'generation', 'retrieval_config', 'report_summary',
           'section', 'source_version', 'report_use', 'content_version',
           'omitted_span_count', 'capture_status', 'truncated', 'omitted_items'}
_CONTENT_FIELDS = readable.READABLE_FIELDS


def _safe(value, key=None):
    if key in _CONTENT_FIELDS:
        return readable.content(value)
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value if _ID.fullmatch(value) else None
    if isinstance(value, (list, tuple)):
        result = [_safe(item, key) for item in value[:256]]
        if len(value) > 256:
            result.append({'truncated': True, 'omitted_items': len(value) - 256})
        return result
    if isinstance(value, dict):
        return {k: _safe(v, k) for k, v in value.items() if k in _FIELDS}
    return None


class Stage:
    def __init__(self, name, kind, parent, metadata):
        started_ns = time.time_ns()
        try:
            inputs = _safe(metadata)
        except Exception:
            inputs = {'capture_status': 'input_projection_failed'}
        self.value = dict(name=name, kind=kind, parent=parent,
                          started_ns=started_ns, status='OK', inputs=inputs, outputs={})

    def output(self, **metadata):
        try:
            self.value['outputs'].update(_safe(metadata))
        except Exception:
            self.value['outputs']['capture_status'] = 'output_projection_failed'


class Collector:
    def __init__(self, identity):
        self.identity = _safe(identity)
        self.spans = []
        self.stack = []
        self.started_ns = time.time_ns()
        self.status = 'OK'
        self.omitted_span_count = 0

    @contextmanager
    def stage(self, name, kind='CHAIN', **metadata):
        item = Stage(name, kind, self.stack[-1] if self.stack else None, metadata)
        index = len(self.spans)
        # The configured report has few queries; prevent accidental unbounded logs.
        if index >= 128:
            self.omitted_span_count += 1
            yield Stage('omitted', 'CHAIN', None, {})
            return
        self.spans.append(item.value)
        self.stack.append(index)
        try:
            yield item
        except BaseException as exc:
            item.value['status'] = 'ERROR'
            item.output(error_type=type(exc).__name__)
            raise
        finally:
            item.value['ended_ns'] = max(item.value['started_ns'], time.time_ns())
            self.stack.pop()

    def report_summary(self, report, *, report_markdown=None):
        """Record actual retrieval queries, selected excerpts and rule exclusions."""
        source_rows = report.get('references', report.get('sources', []))
        def source(row):
            return dict(chunk_id=row.get('chunk_id'), reference_id=row.get('reference_id'),
                title=row.get('title'), section=row.get('section'), excerpt=row.get('content'),
                source_version=row.get('source_version'), report_use=row.get('report_use'))
        sources = [source(row) for row in source_rows if isinstance(row, dict)]
        by_id = {row.get('chunk_id'): row for row in report.get('sources', []) if isinstance(row, dict)}
        by_id.update({row.get('chunk_id'): row for row in source_rows if isinstance(row, dict)})
        excluded = [{key: row.get(key) for key in ('chunk_id', 'title', 'agency_key', 'reasons')}
                    for row in report.get('excluded_references', []) if isinstance(row, dict)]
        selected, checks, queries = [], [], []
        for trace in report.get('retrieval', {}).get('trace', []):
            for topic in trace.get('topics', []):
                queries.append(dict(agency=trace.get('agency'), topic=topic.get('topic'),
                    query=topic.get('query'), selected_chunk_ids=topic.get('selected_chunk_ids'),
                    bm25_top20=topic.get('bm25_top20'), embedding_top20=topic.get('embedding_top20')))
            if trace.get('query') is not None:  # Original single-query records.
                queries.append(dict(agency=trace.get('agency'), query=trace['query']))
            for row in trace.get('selected', []):
                if isinstance(row, dict):
                    selected.append(dict(source(by_id.get(row.get('chunk_id'), row)),
                        agency=trace.get('agency'), rrf=row.get('rrf'), search=row.get('search')))
            for cid, check in trace.get('condition_actor_checks', {}).items():
                if check.get('accepted') is False:
                    checks.append(dict(chunk_id=cid, accepted=False, reason=check.get('reason')))
        retrieval = report.get('retrieval', {})
        summary = dict(document_count=len(sources), query_count=len(queries), sources=sources,
            excluded=excluded, selected=selected, checks=checks, queries=queries,
            generation=report.get('generation'), report_summary=report.get('accident_summary'),
            retrieval_config={k: retrieval[k] for k in ('model', 'fusion', 'weights', 'rrf_k',
                'experimental_cosine_floor', 'relevance_revision', 'date_filter_supported', 'reference_date')
                if k in retrieval})
        if report_markdown is not None:
            summary['report_markdown'] = report_markdown
        return _safe(summary)


@contextmanager
def stage(name, kind='CHAIN', **metadata):
    current = _ACTIVE.get()
    if current is None:
        yield Stage(name, kind, None, {})
    else:
        with current.stage(name, kind, **metadata) as item:
            yield item


def observer():
    return _ACTIVE.get()


def gemini_caller(caller, *, provenance=None):
    def call(model, data, key):
        # Parse/logging errors must neither cancel nor duplicate the actual call.
        try:
            request = readable.gemini_request(data, provenance, secrets=(key,))
        except Exception:
            request = {'capture_status': 'request_projection_failed'}
        with stage('gemini_video_observation', 'LLM', provider_model=model, request=request) as item:
            raw = caller(model, data, key)
            try:
                usage = raw.get('usageMetadata') or {}
                counts = {k: v for k, v in usage.items()
                          if k in _FIELDS and k.endswith('TokenCount') and type(v) is int and v >= 0}
                item.output(provider_model_version=raw.get('modelVersion'), usage=counts,
                            new_model_calls=1)
            except Exception:
                pass
            try:
                item.output(response=readable.gemini_response(raw, secrets=(key,)))
            except Exception:
                item.output(response={'capture_status': 'response_projection_failed'})
            return raw
    return call


def _path(directory, identity):
    execution, event = identity['execution_id'], identity['event_id']
    if not _ID.fullmatch(execution) or not _ID.fullmatch(event):
        raise ValueError('trace_identity_invalid')
    return Path(directory) / ('trace-' + execution + '-' + event + '.json')


@contextmanager
def report_trace(directory, row, event, settings, reference_date, *, client=None):
    """One actual report attempt; cached lookups never synthesize past spans."""
    collector = None
    token = None
    path = None
    if os.environ.get('BE_MLFLOW_TRACKING_URI') or client is not None:
        try:
            from .selection_jobs import identifier
            state = row['state']
            contract = state['analysis_profile']['classifier']
            identity = dict(worker_job_id=row['worker_job_id'], event_id=event,
                input_sha256=state['input_sha256'], model_id=contract['model_id'],
                model_version=contract['model_version'], execution_id=identifier(dict(
                    gpu_key=state['gpu_key'], purpose='full_report',
                    generation_settings=settings, reference_date=reference_date)))
            path = _path(directory, identity)
            # Never replace or resend an earlier trace on reconciliation/resume.
            if not path.exists() and not path.is_symlink():
                collector = Collector(identity)
                token = _ACTIVE.set(collector)
        except Exception:
            collector = None
    try:
        yield collector
    except BaseException:
        if collector:
            collector.status = 'ERROR'
        raise
    finally:
        if token is not None:
            _ACTIVE.reset(token)
        if collector and collector.spans:
            try:
                record = dict(schema=SCHEMA, identity=collector.identity,
                    started_ns=collector.started_ns, ended_ns=time.time_ns(),
                    status=collector.status, spans=collector.spans,
                    omitted_span_count=collector.omitted_span_count,
                    delivery={'status': 'pending', 'trace_id': None, 'attempted': False})
                save(path, readable.bounded_record(record))
            except Exception:
                pass  # Tracking cannot reverse a successful business operation.


def deliver(record, run_id, *, client=None):
    """Send the saved measured spans once. No inference or transport retries."""
    try:
        from .mlflow_tracking import client_for_uri, EXPERIMENT
        if client is None:
            uri = os.environ['BE_MLFLOW_TRACKING_URI']
            # The explicit span client API also uses MLflow's global provider.
            os.environ['MLFLOW_ENABLE_ASYNC_TRACE_LOGGING'] = 'false'
            client = client_for_uri(uri)
            import mlflow
            mlflow.set_tracking_uri(uri)
        experiment = client.get_experiment_by_name(EXPERIMENT)
        if experiment is None:
            raise ValueError('trace_experiment_missing')
        return _deliver_spans(client, str(experiment.experiment_id), record, run_id)
    except Exception:
        return {'status': 'pending', 'trace_id': record.get('delivery', {}).get('trace_id'), 'attempted': True,
                'error_code': 'trace_delivery_failed'}


def _deliver_spans(client, experiment_id, record, run_id):
    identity = record['identity']
    root = client.start_trace('cctv_video_report', span_type='CHAIN',
        inputs=identity, experiment_id=experiment_id, run_id=run_id,
        tags={'cctv.' + key: value for key, value in identity.items()},
        start_time_ns=record['started_ns'])
    record.setdefault('delivery', {})['trace_id'] = root.trace_id
    opened = []
    for row in record['spans']:
        parent = opened[row['parent']] if row['parent'] is not None else root
        child = client.start_span(row['name'], trace_id=root.trace_id,
            parent_id=parent.span_id, span_type=row['kind'], inputs=row['inputs'],
            start_time_ns=row['started_ns'])
        opened.append(child)
    # Close children before parents, with the original actual boundaries.
    for child, row in reversed(list(zip(opened, record['spans']))):
        attributes = {}
        if row['kind'] == 'LLM':
            attributes['mlflow.chat.model'] = row['inputs'].get('provider_model')
        usage = row['outputs'].get('usage', {})
        if row['kind'] == 'LLM' and usage:
            tokens = {}
            for source, target in (('promptTokenCount', 'input_tokens'),
                                   ('candidatesTokenCount', 'output_tokens'),
                                   ('totalTokenCount', 'total_tokens')):
                if source in usage:
                    tokens[target] = usage[source]
            if tokens:
                attributes['mlflow.chat.tokenUsage'] = tokens
        client.end_span(root.trace_id, child.span_id, outputs=row['outputs'],
                        attributes=attributes, status=row['status'], end_time_ns=row['ended_ns'])
    report_outputs = dict(next((row['outputs'] for row in reversed(record['spans'])
                           if row['name'] == 'report_storage'), {}))
    if record.get('content_truncation'):
        report_outputs['content_truncation'] = record['content_truncation']
    if record.get('omitted_span_count'):
        report_outputs['omitted_span_count'] = record['omitted_span_count']
    client.end_trace(root.trace_id, outputs=report_outputs,
                     status=record['status'], end_time_ns=record['ended_ns'])
    verified = client.get_trace(root.trace_id, display=False, flush=True)
    if (verified.info.trace_id != root.trace_id
            or len(verified.data.spans) != len(record['spans']) + 1):
        return {'status': 'pending', 'trace_id': root.trace_id, 'attempted': True,
                'run_id': run_id, 'error_code': 'trace_readback_mismatch'}
    return {'status': 'recorded', 'trace_id': root.trace_id, 'attempted': True,
            'run_id': run_id, 'span_count': len(record['spans']) + 1}


def link_run(directory, execution_id, run_id, *, client=None):
    """Best-effort cross-reference after the existing Run outbox commits."""
    try:
        from .mlflow_tracking import client_for_uri
        ids = []
        for path in sorted(Path(directory).glob('trace-' + execution_id + '-*.json'))[:16]:
            if path.is_symlink() or path.stat().st_size > 256 * 1024:
                continue
            record = read_json(path)
            if record.get('schema') != SCHEMA or record['identity']['execution_id'] != execution_id:
                continue
            if not record['delivery'].get('attempted'):
                record['delivery']['attempted'] = True
                save(path, record)  # Unknown delivery must not create a second trace.
                record['delivery'] = deliver(record, run_id, client=client)
                save(path, record)
            if record['delivery'].get('status') == 'recorded':
                ids.append(record['delivery']['trace_id'])
        if ids:
            client = client or client_for_uri(os.environ['BE_MLFLOW_TRACKING_URI'])
            client.set_tag(run_id, 'cctv.trace_ids', ','.join(ids))
    except Exception:
        pass
