"""Plan, or explicitly apply, one CPU report from a preserved paid VLM response."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.be_worker import report_recovery
from apps.be_worker.queue_store import connect, Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('worker-job-id','task-id','session-name','execution-id','input-sha256',
                 'request-sha256','response-file-sha256','failure-file-sha256'):
        parser.add_argument('--'+name)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true')
    mode.add_argument('--plan',action='store_true')
    mode.add_argument('--status',action='store_true')
    mode.add_argument('--retry-tracking', action='store_true',
                      help='Send completed recovery metadata only; no analysis or report execution')
    parser.add_argument('--finalize-storage',action='store_true')
    parser.add_argument('--storage-attempt-id')
    parser.add_argument('--approved-plan-sha256')
    parser.add_argument('--normalization', choices=report_recovery.NORMALIZATIONS,
                        default=report_recovery.NORMALIZATION,
                        help='Explicit saved-response derivation included in the reviewed plan')
    args = vars(parser.parse_args(argv))
    apply, approved = args.pop('apply'), args.pop('approved_plan_sha256')
    status = args.pop('status'); args.pop('plan')
    retry_tracking = args.pop('retry_tracking')
    storage,storage_id=args.pop('finalize_storage'),args.pop('storage_attempt_id')
    if retry_tracking and storage:
        parser.error('--retry-tracking cannot be combined with --finalize-storage')
    if storage:
        if not args['worker_job_id'] or not storage_id:
            parser.error('--worker-job-id and --storage-attempt-id are required')
    elif status or retry_tracking:
        parser.error('--worker-job-id is required') if not args['worker_job_id'] else None
    elif any(value is None for value in args.values()):
        parser.error('all source identity arguments are required for plan/apply')
    with connect() as conn:
        conn.execute("SET statement_timeout='15000'")
        if retry_tracking:
            conn.execute('SET default_transaction_read_only=on')
            result = report_recovery.retry_tracking(conn, Store(conn), args['worker_job_id'])
        elif storage:
            if apply:
                result=report_recovery.finalize_storage(conn,Store(conn),args['worker_job_id'],storage_id,approved)
            else:
                conn.execute('SET default_transaction_read_only=on')
                result=dict(status='read_only_report_storage_plan',**report_recovery.plan_storage(
                    conn,Store(conn),args['worker_job_id'],storage_id))
        elif status:
            conn.execute('SET default_transaction_read_only=on')
            result = report_recovery.status(conn,Store(conn),args['worker_job_id'])
        elif apply:
            result = report_recovery.apply(conn,Store(conn),args,approved)
        else:
            conn.execute('SET default_transaction_read_only=on')
            result = dict(status='read_only_report_recovery_plan',**report_recovery.plan(conn,Store(conn),**args))
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status':'report_recovery_stopped_inspect_receipts',
            'error_code':getattr(exc,'code','report_recovery_failed'),
            'new_model_calls':0,'automatic_retry':False}))
        raise SystemExit(2)
