"""Append-only relevance review of stored reports, without model execution.

The original database row, report and scene stay immutable. A separate sidecar
holds the reviewed rendition and its source/code identifiers.
Hashes are integrity identifiers, not a human/legal approval or a signature.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from .common import WorkerError, checked_id, read_json
from .report_jobs import fingerprint

SCHEMA = 'cctv-team-report-review-v1'
LIMIT = 2 * 1024 * 1024


def require(ok, code):
    if not ok:
        raise WorkerError(code)


def policy_hash():
    from apps.team_rag import control_rag, relevance, review, runtime
    return fingerprint({'review_schema': SCHEMA,
        'review_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'report_code_sha256': {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                              for module in (control_rag, relevance, review, runtime)}})


def build(original, scene_result):
    from .team_jobs import validate_report
    from apps.scene_description import team_scene
    from apps.team_rag.review import review_saved_report
    validate_report(original, scene_result['team_event'])
    require(original['analysis_id'] == scene_result['analysis_id']
            and original['scene_sha256'] == team_scene.validate_result(scene_result)
            and original['provider_job_id'] == scene_result['provider_job_id']
            and original['event_id'] == scene_result['event_id'], 'review_source_scene_mismatch')
    source_sha, policy = fingerprint(original), policy_hash()
    review_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
        SCHEMA + '/' + original['report_id'] + '/' + source_sha + '/' + policy))
    revised = review_saved_report(original['report_json'])
    payload = dict(deepcopy(original), **revised, report_id=review_id)
    payload['report_review'] = {'schema': SCHEMA, 'source_report_id': original['report_id'],
        'source_result_sha256': source_sha, 'policy_sha256': policy,
        'model_calls': 0, 'retrieval_executed': False}
    validate_report(payload, scene_result['team_event'])
    quality = payload['report_json'].get('quality_review', {})
    return {'schema': SCHEMA, 'review_id': review_id, 'source_report_id': original['report_id'],
        'source_result_sha256': source_sha, 'scene_sha256': original['scene_sha256'],
        'input_sha256': scene_result['input_sha256'], 'policy_sha256': policy,
        'worker_job_id': original['worker_job_id'], 'event_id': original['event_id'],
        'result': payload, 'result_sha256': fingerprint(payload),
        'model_calls': 0, 'retrieval_executed': False,
        'excluded_count': len(quality.get('excluded_sources', []))}


def validate(record, original, scene_result):
    require(record == build(original, scene_result), 'stored_report_review_identity_changed')
    return record


def review_path(directory, original, review_id):
    require(str(uuid.UUID(review_id)) == review_id, 'invalid_report_review_id')
    event_id = original['event_id']
    require(re.fullmatch(r'event_[0-9]+', event_id) is not None, 'invalid_report_review_event')
    return Path(directory) / 'scenes' / event_id / 'report-team-v1' / 'reviews' / (review_id + '.json')


def load_reviews(directory, report, scene_result):
    """No writes or network calls on the UI/API read path."""
    original = report['state']['result']
    folder = review_path(directory, original, original['report_id']).parent
    require(not any(p.is_symlink() for p in (folder, *folder.parents)), 'unsafe_report_review_path')
    if not folder.exists():
        return []
    files = sorted(folder.glob('*.json'))
    require(len(files) <= 8, 'too_many_report_reviews')
    output = []
    for path in files:
        require(not path.is_symlink() and path.is_file() and path.stat().st_size <= LIMIT,
                'unsafe_report_review_file')
        record = read_json(path)
        require(path == review_path(directory, original, record['review_id']), 'report_review_filename_mismatch')
        # A reviewed source is pinned to the immutable parent; code updates do not
        # silently reinterpret a saved review. Unsupported old policies stay held.
        validate(record, original, scene_result)
        output.append({'report_id': record['review_id'],
            'settings': dict(report['settings'], report_id=record['review_id'], source_report_id=original['report_id']),
            'state': {'status': 'report_stored', 'result': record['result'],
                      'result_sha256': record['result_sha256']},
            'review': {key: record[key] for key in ('schema', 'source_report_id',
                'source_result_sha256', 'policy_sha256', 'excluded_count', 'model_calls')}})
        output[-1]['mlflow'] = tracking_status(directory, record)
    return output


def tracking_status(directory, record):
    """A saved tracking receipt is shown only when linked to this exact review."""
    from . import mlflow_tracking as tracking
    result = {'status': 'pending', 'run_id': None}
    folder = Path(directory) / 'mlflow-outbox' / record['review_id'].replace('-', '')
    try:
        receipt_path = folder / 'receipt.json'
        require(not any(p.is_symlink() for p in (receipt_path, *receipt_path.parents))
                and receipt_path.stat().st_size <= 16384, 'unsafe_review_tracking_receipt')
        receipt = read_json(receipt_path)
        digest = receipt.get('recorded_observation')
        require(receipt.get('status') == 'recorded' and isinstance(digest, str)
                and re.fullmatch('[a-f0-9]{64}', digest), 'review_tracking_pending')
        observation = folder / (digest + '.json')
        require(not observation.is_symlink() and observation.stat().st_size <= LIMIT,
                'unsafe_review_tracking_observation')
        value = read_json(observation)
        require(tracking._hash(value) == digest
                and value['worker_job_id'] == record['worker_job_id']
                and value['execution_id'] == record['review_id'].replace('-', '')
                and value['input_sha256'] == record['input_sha256']
                and value['result_refs']['result_sha256'] == record['result_sha256']
                and value['result_refs']['purpose'] == 'report_review'
                and value['config']['policy_sha256'] == record['policy_sha256']
                and re.fullmatch('[a-f0-9]{32}', receipt.get('run_id', '')),
                'review_tracking_identity_changed')
        result.update(status='recorded', run_id=receipt['run_id'])
    except (WorkerError, OSError, ValueError, TypeError, KeyError):
        pass
    return result


def source_context(conn, worker_id, report_id, expected_sha):
    from .queue_store import Store
    from .flow_service import spool
    from apps.scene_description import team_db
    checked_id(worker_id)
    require(re.fullmatch(r'[a-f0-9]{64}', expected_sha or '') is not None, 'review_source_digest_required')
    rows = [r for r in Store(conn).rows() if r['worker_job_id'] == worker_id]
    require(len(rows) == 1 and rows[0]['state']['status'] == 'handoff_ready', 'review_completed_job_required')
    row = rows[0]
    report = conn.execute('SELECT report_id,settings,state FROM cctv_scene.team_report_runs '
                          'WHERE report_id=%s AND worker_job_id=%s', (report_id, worker_id)).fetchone()
    require(report is not None and report['state']['status'] == 'report_stored', 'review_stored_report_required')
    original = report['state']['result']
    require(fingerprint(original) == report['state']['result_sha256'] == expected_sha
            and original['report_id'] == report_id and original['worker_job_id'] == worker_id,
            'review_source_report_changed')
    scene_result = team_db.show(conn, original['analysis_id'])
    require(scene_result['input_sha256'] == row['state']['input_sha256']
            and scene_result['provider_job_id'] == row['state']['provider_job_id'], 'review_source_job_changed')
    directory = spool() / checked_id(row['directory_id'])
    folder = review_path(directory, original, report_id).parent.parent
    require(read_json(folder / 'report.json') == original['report_json']
            and (folder / 'report.md').read_text() == original['report_markdown'],
            'review_original_database_file_mismatch')
    return row, directory, report, scene_result


def plan(conn, worker_id, report_id, expected_sha):
    row, directory, report, scene = source_context(conn, worker_id, report_id, expected_sha)
    record = build(report['state']['result'], scene)
    path = review_path(directory, report['state']['result'], record['review_id'])
    summary = {k: record[k] for k in ('schema', 'review_id', 'source_report_id',
        'source_result_sha256', 'policy_sha256', 'result_sha256', 'worker_job_id',
        'input_sha256', 'excluded_count', 'model_calls', 'retrieval_executed')}
    summary.update(plan_sha256=fingerprint(record), already_present=path.exists(),
                   original_report_preserved=True, database_writes=0)
    return summary, record, (row, directory, report, scene)


def save_review(path, record):
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'unsafe_report_review_path')
    if path.exists():
        require(read_json(path) == record, 'report_review_exists_with_other_content')
        return
    path.parent.mkdir(mode=0o700, exist_ok=True)
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    require(len(raw) <= LIMIT, 'report_review_too_large')
    temp = path.with_name('.pending-' + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    try:
        os.link(temp, path)  # Publish atomically, and never overwrite an original.
    finally:
        temp.unlink()
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record_tracking(directory, record, row):
    from . import mlflow_tracking as tracking
    from .model_catalog import contract_sha256
    binding = tracking.validate_binding(read_json(directory / 'model-binding.json'))
    contract = row['state']['analysis_profile']['classifier']
    require(binding['weights_sha256'] == contract['weights_sha256']
            and binding['contract_sha256'] == contract_sha256(contract),
            'review_model_binding_changed')
    execution = record['review_id'].replace('-', '')
    value = tracking.execution_record(worker_job_id=row['worker_job_id'], execution_id=execution,
        model_binding=binding, input_sha256=record['input_sha256'],
        config={'operation': 'stored_report_relevance_review', 'policy_sha256': record['policy_sha256'],
                'source_report_id': record['source_report_id'], 'source_result_sha256': record['source_result_sha256']},
        outcome='succeeded', result_refs={'purpose': 'report_review', 'new_model_calls': 0,
            'new_retrieval': False, 'review_id': record['review_id'], 'result_sha256': record['result_sha256'],
            'excluded_count': record['excluded_count']})
    return tracking.record_execution(directory, value,
        tracking_uri=os.environ.get('BE_MLFLOW_TRACKING_URI'))


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--worker-job-id', required=True)
    cli.add_argument('--report-id', required=True)
    cli.add_argument('--source-sha256', required=True)
    cli.add_argument('--apply', action='store_true')
    cli.add_argument('--approved-plan-sha256')
    args = cli.parse_args(argv)
    from .queue_store import connect, Store
    with connect() as conn:
        conn.execute('SET default_transaction_read_only=on')
        conn.execute("SET statement_timeout='15000'")
        with Store(conn).lock('flow'):
            summary, record, context = plan(conn, args.worker_job_id, args.report_id, args.source_sha256)
            if args.apply:
                require(summary['plan_sha256'] == args.approved_plan_sha256, 'review_plan_changed')
                row, directory, report, scene = context
                save_review(review_path(directory, report['state']['result'], record['review_id']), record)
                # Re-read original before metadata delivery; no original DB writes.
                source_context(conn, args.worker_job_id, args.report_id, args.source_sha256)
                try:
                    summary['mlflow'] = record_tracking(directory, record, row)
                except Exception as exc:
                    summary['mlflow'] = {'status': 'pending',
                        'error_code': getattr(exc, 'code', 'review_metadata_delivery_failed')}
                summary['status'] = 'report_review_saved'
            else:
                summary['status'] = 'report_review_read_only_plan'
            print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'report_review_stopped',
            'error_code': getattr(exc, 'code', type(exc).__name__), 'automatic_retry': False}))
        raise SystemExit(2)
