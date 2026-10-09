"""Operator-only demo lifecycle; every mutation requires --apply.

Run inside the existing BE container with its private spool, DB and secrets:
  python -m apps.be_worker.demo_control status
  python -m apps.be_worker.demo_control start --name demo-session-YYYYMMDD-NN \
      --hours 8 --max-jobs 5 --models B R2 --max-upload-mib 50 --max-video-seconds 60
  python -m apps.be_worker.demo_control start ... --apply
  python -m apps.be_worker.demo_control stop --apply

start never submits a job, starts a Pod, changes its URL, resets a session or
invokes a model. Its authenticated provider GET verifies the separately
prepared Pod window. stop closes the admission ledger, not EC2/Pod power.
"""
import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

from . import analysis_profiles as profiles, demo_session as demo, hour_session as session
from . import generation_settings, report_continuation, selection_jobs, team_settings
from .common import WorkerError, checked_id

require = session.require


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def plain(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts
            and not any(p.is_symlink() for p in (path, *path.parents)), 'demo_control_path_invalid')
    return path


def read_private(path, *, optional=False):
    path = plain(path)
    if optional and not path.exists():
        return None
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
            and info.st_size <= 1024 * 1024 and not stat.S_IMODE(info.st_mode) & 0o022,
            'demo_control_file_invalid')
    return path.read_bytes()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_marker(path, before, after):
    require(read_private(path, optional=True) == before, 'demo_marker_changed')
    if after is None:
        path.unlink()
        sync_directory(path.parent)
        return
    fd, name = tempfile.mkstemp(prefix='.demo-marker-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        require(read_private(path, optional=True) == before, 'demo_marker_changed')
        os.replace(name, path)
        name = None
        sync_directory(path.parent)
        require(read_private(path) == after, 'demo_marker_readback_failed')
    finally:
        if name is not None:
            os.unlink(name)


def write_exclusive(path, value):
    path = plain(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def historical_marker(value):
    if report_continuation.selected(value):
        marker = {key: value[key] for key in report_continuation.FIELDS}
        report_continuation.validate_marker(marker, historical=True)
        return marker
    return session.marker_from_state(value)


def validate_history(conn, value):
    """Read old settings against saved task receipts, never execute them."""
    marker = historical_marker(value)
    if report_continuation.selected(value):
        report_continuation.validate_state(value, marker, historical=True)
    else:
        session._validate(value, expected_marker=marker, stored=True)
    for worker_id, binding in value['bindings'].items():
        if not team_settings.is_team(binding.get('settings')):
            continue
        expected_id = selection_jobs.identifier(dict(gpu_key=binding['gpu_key'], purpose='full_report',
            generation_settings=binding['settings'], reference_date=binding['reference_date']))
        task = conn.execute('SELECT * FROM cctv_jobs.analysis_tasks WHERE task_id=%s', (expected_id,)).fetchone()
        row = conn.execute('SELECT * FROM cctv_jobs.queue WHERE worker_job_id=%s', (worker_id,)).fetchone()
        require(task is not None and row is not None and row['worker_job_id'] == worker_id
                and task['worker_job_id'] == worker_id
                and all(row['state'].get(key) == binding[key] for key in ('input_sha256', 'input_bytes', 'gpu_key')),
                'demo_history_task_identity_changed')
        selection_jobs.validate_stored(task, row,
            directory=session.marker_path().parent / checked_id(row['directory_id']))
    return marker


def history_rows(conn):
    return {row['name']: row['payload'] for row in conn.execute(
        "SELECT name,payload FROM cctv_jobs.service_state WHERE name ~ "
        "'^(p6-hour-session|mlops-session|demo-session)-[0-9]{8}-[0-9]{2}$' ORDER BY name").fetchall()}


def preflight_history(conn, name, now):
    require(conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',
                         (name,)).fetchone() is None, 'demo_session_name_already_used')
    history = history_rows(conn)
    for stored_name, value in history.items():
        marker = validate_history(conn, value)
        require(marker['name'] == stored_name and (value['stopped'] or now >= value['deadline_at']),
                'demo_active_session_preserved')
    busy = conn.execute(
        'SELECT EXISTS(SELECT 1 FROM cctv_jobs.analysis_tasks WHERE finished_at IS NULL AND NOT paused) '
        'OR EXISTS(SELECT 1 FROM cctv_jobs.flows WHERE finished_at IS NULL AND NOT paused) '
        "OR EXISTS(SELECT 1 FROM cctv_jobs.queue WHERE COALESCE(state->>'status','') NOT IN "
        "('handoff_ready','no_candidates','provider_failed','submission_rejected')) AS busy").fetchone()['busy']
    require(not busy, 'demo_unfinished_work_preserved')
    return history


def prepare_runtime(marker):
    """Only hash/read existing local artifacts and secrets; no inference/download."""
    from . import team_model
    require(team_settings.enabled(), 'demo_team_pipeline_required')
    expected = {}
    for version in marker['versions']:
        profile = profiles.profile(version)
        require(profiles.deployment_for_profile(profile) == profiles.MLOPS_DEPLOYMENT,
                'demo_mlops_deployment_required')
        value = generation_settings.from_environment(analysis_profile=profile)
        require(team_settings.is_team(value) and value['report_model'] == 'cpu-template',
                'demo_cpu_report_required')
        expected[version] = sha(canonical(value))
    require(team_model.preflight(os.environ.get('RAG_MODEL_PATH')), 'demo_cpu_model_not_prepared')
    generation_settings.read_generation_key()
    return {'generation_settings_sha256': expected, 'cpu_model_files_verified': True,
            'vlm_key_readable': True, 'model_calls': 0}


def provider_ready(api, marker, now):
    expected = profiles.profile('B')
    require(profiles.deployment_for_profile(expected) == profiles.MLOPS_DEPLOYMENT,
            'demo_mlops_deployment_required')
    health = api.health(expected)
    versions = list(profiles.supported_versions())
    require(all(version in versions for version in marker['versions']), 'demo_model_not_packaged')
    limits = {'max_jobs': marker['max_jobs'], 'per_job_seconds': marker['per_job_seconds'],
              'whole_service_seconds': marker['window_seconds']}
    require(health.get('limits') == limits and type(health['limits'].get('max_jobs')) is int
            and all(type(health['limits'].get(k)) in (int, float)
                    for k in ('per_job_seconds', 'whole_service_seconds')), 'demo_provider_limits_differ')
    require(health.get('api_schema') == 'runpod-ab-api-v1' and health.get('storage') == 's3'
            and health.get('supported_versions') == versions
            and health.get('profiles') == {version: profiles.gpu_identity('0' * 64,
                profiles.profile(version))['profile_sha256'] for version in versions},
            'demo_provider_catalog_differ')
    require(type(health.get('admissions')) is int and health['admissions'] == 0,
            'demo_provider_window_already_used')
    deadline = health.get('deadline_at')
    require(type(deadline) in (int, float) and math.isfinite(deadline)
            and max(30, marker['window_seconds'] - 120) <= deadline - now <= marker['window_seconds'] + 5,
            'demo_provider_fresh_window_required')
    return {'authenticated_health_verified': True, 'provider_deadline_at': deadline,
            'provider_limits': limits, 'provider_admissions': 0}


def start(conn, store, marker, *, apply=False, api=None, prepare=prepare_runtime):
    marker = demo.validate_marker(marker)
    path = plain(session.marker_path())
    require(path.parent.is_dir() and path.parent.stat().st_uid == os.geteuid()
            and not stat.S_IMODE(path.parent.stat().st_mode) & 0o077, 'demo_private_spool_required')
    intent = path.parent / ('.demo-start-' + marker['name'] + '.json')
    require(not intent.exists() and not intent.is_symlink(), 'demo_start_intent_preserved_no_retry')
    with ExitStack() as stack:
        for kind in ('admission', 'dispatch', 'flow', 'collect'):
            stack.enter_context(store.lock(kind))
        old_marker = read_private(path, optional=True)
        history = preflight_history(conn, marker['name'], session.clock(conn))
        if old_marker is not None:
            old = json.loads(old_marker)
            require(old.get('name') in history and old == historical_marker(history[old['name']]),
                    'demo_previous_marker_history_mismatch')
        runtime = prepare(marker)
        if api is None:
            from . import flow_service
            api = flow_service.api_for({'config': flow_service.config()})
        provider = provider_ready(api, marker, session.clock(conn))
        plan = dict(status='demo_start_plan', apply=False, marker=marker,
            previous_marker_sha256=None if old_marker is None else sha(old_marker),
            preserved_history_sha256={name: sha(canonical(value)) for name, value in history.items()},
            runtime=runtime, **provider, new_gpu_calls=0, new_vlm_calls=0,
            automatic_retry=False, power_changed=False)
        if not apply:
            return plan
        require(read_private(path, optional=True) == old_marker, 'demo_marker_changed')
        require(preflight_history(conn, marker['name'], session.clock(conn)) == history,
                'demo_history_changed_before_start')
        # This durable name reservation is never overwritten, even after a failure.
        write_exclusive(intent, {'schema': 'cctv-demo-start-intent-v1', 'plan': plan,
            'previous_marker_base64': None if old_marker is None else base64.b64encode(old_marker).decode()})
        new_marker = canonical(marker) + b'\n'
        try:
            replace_marker(path, old_marker, new_marker)
            started = session.clock(conn)
            require(max(30, marker['window_seconds'] - 120) <= provider['provider_deadline_at'] - started,
                    'demo_provider_window_expired_during_start')
            saved = session.initialize(conn, started, started + marker['window_seconds'])
            require(history_rows(conn) == dict(history, **{marker['name']: saved}), 'demo_history_changed_after_start')
            require(read_private(path) == new_marker, 'demo_marker_changed_after_start')
            answer = dict(plan, status='demo_started', apply=True, started_at=saved['started_at'],
                          deadline_at=saved['deadline_at'], previous_state_preserved=True)
            write_exclusive(intent.with_suffix('.ready.json'), answer)
            return answer
        except BaseException:
            # A committed/ambiguous new DB row stays for investigation. Restoring
            # the old marker removes the grant; the name/intent cannot be reused.
            try:
                current = read_private(path, optional=True)
                if current == new_marker:
                    replace_marker(path, new_marker, old_marker)
                else:
                    require(current == old_marker, 'demo_marker_restore_unverified')
            except Exception:
                raise WorkerError('demo_activation_failed_marker_restore_unverified') from None
            raise WorkerError('demo_activation_failed_intent_preserved') from None


def stop(conn, *, apply=False):
    marker = session.current_marker()
    require(demo.selected(marker), 'demo_not_selected')
    value = session.get(conn)
    if apply:
        value = session.stop(conn)
    return {'status': 'demo_stopped' if apply else 'demo_stop_plan', 'apply': apply,
            'session_name': value['name'], 'stopped': value['stopped'],
            'bindings_preserved': len(value['bindings']), 'claims_preserved': len(value['claims']),
            'power_changed': False, 'inflight_calls_may_finish': True, 'new_model_calls': 0}


def marker_from_args(args):
    seconds = float(args.hours) * 3600
    require(math.isfinite(seconds) and seconds.is_integer(), 'demo_hours_invalid')
    return demo.validate_marker(dict(schema=demo.SCHEMA, name=args.name, versions=args.models,
        purpose='full_report', max_jobs=args.max_jobs, per_job_seconds=300,
        window_seconds=int(seconds), max_upload_bytes=args.max_upload_mib * 1024 * 1024,
        max_video_seconds=args.max_video_seconds))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    launch = commands.add_parser('start')
    launch.add_argument('--name', required=True)
    launch.add_argument('--hours', type=float, default=8)
    launch.add_argument('--max-jobs', type=int, default=demo.DEFAULT_MAX_JOBS)
    launch.add_argument('--models', nargs='+', choices=('B', 'R2', 'LC'), default=['B', 'R2'])
    launch.add_argument('--max-upload-mib', type=int, default=50)
    launch.add_argument('--max-video-seconds', type=int, default=demo.DEFAULT_VIDEO_SECONDS)
    launch.add_argument('--apply', action='store_true')
    commands.add_parser('stop').add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        # Validate the public policy before opening the private DB or network.
        marker = marker_from_args(args) if args.command == 'start' else None
        from .queue_store import Store, connect
        with connect() as conn:
            if args.command == 'start':
                answer = start(conn, Store(conn), marker, apply=args.apply)
            elif args.command == 'stop':
                answer = stop(conn, apply=args.apply)
            else:
                from .demo_access import status
                answer = status(conn)
        print(json.dumps(answer, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        code = getattr(exc, 'code', 'demo_control_failed')
        if not isinstance(code, str) or not re.fullmatch('[a-z_]{1,100}', code):
            code = 'demo_control_failed'
        print(json.dumps({'status': 'demo_control_failed', 'error': code,
                          'automatic_retry': False, 'new_model_calls': 0}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
