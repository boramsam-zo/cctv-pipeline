"""Connect the existing analysis/report tasks to MLflow, without GPU retries."""
from pathlib import Path
import os

from .common import WorkerError, checked_id, read_json, save
from .model_catalog import DEPLOYMENT, contract_sha256
from . import mlflow_tracking as tracking


def enabled(snapshot):
    return isinstance(snapshot, dict) and snapshot.get('deployment') == DEPLOYMENT


def binding_for(snapshot, *, verify=False, client=None):
    from .analysis_profiles import validate_profile
    frozen = validate_profile(snapshot)
    if not enabled(frozen):
        return None
    path = os.environ.get('BE_MLFLOW_BINDINGS_FILE')
    if not path:
        raise WorkerError('mlflow_model_bindings_required')
    try:
        target = Path(path)
        if target.is_symlink() or target.stat().st_size > 16384:
            raise WorkerError('mlflow_model_bindings_invalid')
        bindings = read_json(target)
        binding = tracking.validate_binding(bindings[frozen['version']])
    except (OSError, KeyError, TypeError):
        raise WorkerError('mlflow_model_bindings_unavailable') from None
    contract = frozen['classifier']
    if any(binding[key] != contract[key] for key in ('model_id', 'model_version', 'weights_sha256')) or (
            binding['contract_sha256'] != contract_sha256(contract)):
        raise WorkerError('mlflow_model_binding_contract_mismatch')
    if verify:
        uri = os.environ.get('BE_MLFLOW_TRACKING_URI')
        if client is None and not uri:
            raise WorkerError('mlflow_tracking_uri_required')
        try:
            tracking.verify_registered_model(client or tracking.client_for_uri(uri), binding)
        except WorkerError:
            raise
        except Exception:
            raise WorkerError('mlflow_registry_unavailable') from None
    return binding


def freeze_binding(directory, snapshot, *, client=None):
    """Check the exact registered version before a new GPU submit, save identity."""
    if not enabled(snapshot):
        return None
    binding = binding_for(snapshot, verify=True, client=client)
    path = Path(directory) / 'model-binding.json'
    if path.exists():
        if read_json(path) != binding:
            raise WorkerError('mlflow_frozen_binding_changed')
    else:
        save(path, binding)
    return binding


def record_task(directory, task, row, *, reports=(), client=None):
    """Tracking errors never change the task, report or analysis state."""
    snapshot = row['state'].get('analysis_profile')
    if not enabled(snapshot):
        return {'status': 'not_enabled'}
    try:
        status = task['status']
        if status == 'no_candidates':
            outcome = 'no_candidates'
        elif status in ('completed', 'comparison_completed'):
            outcome = 'succeeded'
        elif task.get('paused') or status in ('provider_failed', 'verification_failed', 'storage_not_ready'):
            outcome = 'failed'
        else:
            return {'status': 'in_progress'}
        # The binding used at dispatch is immutable even if defaults later change.
        binding_path = Path(directory) / 'model-binding.json'
        if (not binding_path.exists() and not binding_path.is_symlink()
                and outcome == 'failed' and row['state'].get('status') == 'prepared'
                and row['state'].get('post_attempts') == 0 and not row['state'].get('provider_job_id')):
            # No registered binding was ever frozen and no POST was attempted.
            # Do not invent an executed model/version or retry inference to log
            # a pre-dispatch failure. A later task revision is reconsidered.
            return {'status': 'not_submitted', 'run_id': None,
                    'error_code': 'mlflow_execution_not_started'}
        binding = tracking.validate_binding(read_json(binding_path))
        contract = snapshot['classifier']
        if (binding['contract_sha256'] != contract_sha256(contract)
                or binding['weights_sha256'] != contract['weights_sha256']):
            raise WorkerError('mlflow_frozen_binding_changed')
        references = {'provider_job_id': row['state'].get('provider_job_id'),
                      'gpu_key': row['state']['gpu_key'], 'purpose': task['purpose'],
                      'task_status': status, 's3': row['state'].get('s3'),
                      'reports': list(reports)}
        if task['purpose'] == 'full_report' and status == 'completed' and (
                not reports or any(report.get('status') != 'report_stored' for report in reports)):
            raise WorkerError('mlflow_completed_report_reference_missing')
        durations = {}
        if task.get('finished_at') and task.get('created_at'):
            durations['pipeline_seconds'] = (task['finished_at'] - task['created_at']).total_seconds()
        record = tracking.execution_record(worker_job_id=row['worker_job_id'],
            execution_id=task['task_id'], model_binding=binding,
            input_sha256=row['state']['input_sha256'],
            config={'analysis_profile': snapshot, 'generation_settings': task.get('generation_settings'),
                    'reference_date': str(task['reference_date']) if task.get('reference_date') else None},
            outcome=outcome, result_refs=references, durations=durations,
            error_code=task.get('error_code'),
            started_at_ms=int(task['created_at'].timestamp() * 1000) if task.get('created_at') else None,
            finished_at_ms=int(task['finished_at'].timestamp() * 1000) if task.get('finished_at') else None)
        delivered = tracking.record_execution(directory, record, client=client,
                                             tracking_uri=os.environ.get('BE_MLFLOW_TRACKING_URI'))
        if delivered.get('status') == 'recorded':
            from .mlflow_tracing import link_run
            link_run(directory, task['task_id'], delivered['run_id'], client=client)
        return delivered
    except Exception as exc:
        return {'status': 'pending', 'run_id': None,
                'error_code': exc.code if isinstance(exc, WorkerError) else 'mlflow_tracking_prepare_failed'}


def view_status(directory, task, snapshot):
    """Read a task receipt without contacting MLflow or modifying report state."""
    if not enabled(snapshot):
        return None
    contract = snapshot['classifier']
    result = {key: contract[key] for key in ('model_id', 'model_version')}
    result['status'] = 'pending' if task.get('finished_at') or task.get('paused') else 'in_progress'
    path = Path(directory) / ('mlflow-' + checked_id(task['task_id']) + '.json')
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return result
        receipt = read_json(path)
        if receipt.get('revision') == str(task['updated_at']):
            if receipt.get('status') == 'recorded':
                result.update(status='recorded', run_id=receipt.get('run_id'))
            elif receipt.get('status') == 'not_submitted':
                result.update(status='not_submitted', run_id=None)
    except (OSError, ValueError, TypeError, WorkerError):
        pass
    return result


def sync_tasks(conn, rows, spool, *, limit=2):
    """Fair bounded metadata sweep, under the caller's existing flow lock.

A durable round-robin cursor advances even after a permanent metadata error.
Two broken recent tasks therefore cannot starve older successful tasks. The
cursor and receipts never reset queue state or request another GPU execution.
"""
    if type(limit) is not int or not 1 <= limit <= 20:
        raise WorkerError('mlflow_invalid_sync_limit')
    by_id = {row['worker_job_id']: row for row in rows if enabled(row['state'].get('analysis_profile'))}
    if not by_id:
        return []
    tasks = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE finished_at IS NOT NULL OR paused ORDER BY updated_at DESC').fetchall()
    tasks = [task for task in tasks if task['worker_job_id'] in by_id]
    if not tasks:
        return []
    cursor_path = Path(spool) / '.mlflow-sync-cursor.json'
    if cursor_path.is_symlink():
        raise WorkerError('mlflow_sync_cursor_invalid')
    if cursor_path.exists():
        cursor = read_json(cursor_path)
        if (not isinstance(cursor, dict) or set(cursor) != {'schema', 'last_task_id'}
                or cursor['schema'] != 'cctv-mlflow-sync-v1'):
            raise WorkerError('mlflow_sync_cursor_invalid')
        last = checked_id(cursor['last_task_id'])
        position = next((i for i, task in enumerate(tasks) if task['task_id'] == last), None)
        if position is not None:
            tasks = tasks[position + 1:] + tasks[:position + 1]
    results = []
    for task in tasks:
        row = by_id.get(task['worker_job_id'])
        if row is None:
            continue
        directory = Path(spool) / row['directory_id']
        task_id = checked_id(task['task_id'])
        status_path = directory / ('mlflow-' + task_id + '.json')
        revision = str(task['updated_at'])
        try:
            previous = read_json(status_path) if status_path.exists() else {}
            if previous.get('revision') == revision and previous.get('status') in ('recorded', 'not_submitted'):
                continue
            reports = []
            if task['purpose'] == 'full_report':
                from .team_settings import is_team
                table = 'cctv_scene.team_report_runs' if is_team(task.get('generation_settings')) else 'cctv_scene.report_runs'
                for report in conn.execute('SELECT report_id,state FROM ' + table + ' WHERE worker_job_id=%s',
                                           (row['worker_job_id'],)).fetchall():
                    reports.append({'report_id': str(report['report_id']),
                                    'status': report['state']['status'],
                                    'result_sha256': report['state'].get('result_sha256')})
            result = record_task(directory, task, row, reports=reports)
            save(status_path, dict(result, revision=revision))
        except Exception:
            result = {'status': 'pending', 'error_code': 'mlflow_sync_failed'}
        save(cursor_path, {'schema': 'cctv-mlflow-sync-v1', 'last_task_id': task_id})
        results.append(result)
        if len(results) >= limit:
            break
    return results
