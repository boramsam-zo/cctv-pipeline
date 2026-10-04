"""User-run EC2 container activation AFTER starting the new Pod API.

python -B scripts/activate_mlops_session.py --name mlops-session-YYYYMMDD-NN \
    --input-sha256 <approved-short-video-sha256> --runpod-url https://<pod>-8000.proxy.runpod.net

Reads BE_TARGET_CONFIG, BE_SPOOL_PATH and /run/secrets/runpod_worker_token.
Only URL/selected-session/one new DB session change. No job submission, model,
VLM, S3 request, retry, old-window reset, or Pod creation. A failed/used name is
never reused automatically. Backups stay private in the existing spool.
"""
import argparse
import base64
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.be_worker import analysis_profiles as profiles, hour_session as session
from apps.be_worker.common import config_checked
from apps.be_worker.runpod_api import RunpodAPI

DEPLOYMENT = 'mlops-20261004-v1'
LIMITS = {'max_jobs': 2, 'per_job_seconds': 300.0, 'whole_service_seconds': 900.0}


class ActivationError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise ActivationError(code)


def marker_for(name, input_sha256):
    value = {'schema': session.MODEL_SESSION_SCHEMA, 'name': name, 'versions': ['B', 'R2'],
             'purpose': 'gpu_comparison', 'max_jobs': 2, 'per_job_seconds': 300,
             'window_seconds': 900, 'approved_input_sha256': input_sha256}
    return session._validate_marker(value)


def provider_url(value):
    require(isinstance(value, str) and re.fullmatch(
        r'https://[a-z0-9]{8,40}-8000\.proxy\.runpod\.net/?', value), 'runpod_proxy_url_invalid')
    return value.rstrip('/')


def verify_health(value, now):
    require(value.get('limits') == LIMITS
            and type(value['limits'].get('max_jobs')) is int
            and all(type(value['limits'].get(k)) in (int, float)
                    for k in ('per_job_seconds', 'whole_service_seconds')), 'provider_limits_differ')
    require(type(value.get('admissions')) is int and value['admissions'] == 0, 'provider_admissions_not_zero')
    deadline = value.get('deadline_at')
    require(type(deadline) in (int, float) and math.isfinite(deadline), 'provider_deadline_invalid')
    started = deadline - 900.0
    require(math.isfinite(now) and started <= now < deadline, 'provider_window_expired_or_clock_mismatch')
    require(deadline - now >= 600, 'insufficient_time_for_two_300_second_jobs')
    return started, deadline


def plain(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts
            and not any(p.is_symlink() for p in (path, *path.parents)), 'activation_path_invalid')
    return path


def read_file(path, *, optional=False):
    path = plain(path)
    if optional and not path.exists():
        return None
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_size <= 131072 and info.st_uid == os.geteuid()
            and not stat.S_IMODE(info.st_mode) & 0o022, 'activation_file_invalid')
    return path.read_bytes()


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_file(path, before, after):
    """Compare exact prior bytes; replace atomically in the existing directory."""
    require(read_file(path, optional=True) == before, 'activation_file_changed')
    if after is None:
        path.unlink()
        fsync_directory(path.parent)
        return
    mode = stat.S_IMODE(path.stat().st_mode) if before is not None else 0o600
    fd, name = tempfile.mkstemp(prefix='.mlops-activation-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        require(read_file(path, optional=True) == before, 'activation_file_changed')
        os.replace(name, path)
        name = None
        fsync_directory(path.parent)
        require(read_file(path) == after, 'activation_file_readback_failed')
    finally:
        if name is not None:
            os.unlink(name)


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()


def session_rows(conn):
    return {r['name']: r['payload'] for r in conn.execute(
        "SELECT name,payload FROM cctv_jobs.service_state WHERE name ~ "
        "'^(p6-hour|mlops)-session-[0-9]{8}-[0-9]{2}$' ORDER BY name").fetchall()}


def preflight_db(conn, candidate, now):
    require(conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',
                         (candidate['name'],)).fetchone() is None, 'existing_session_name_preserved')
    history = session_rows(conn)
    for value in history.values():
        selected = session.marker_from_state(value)
        session.validate(value, expected_marker=selected)
        require(value['stopped'] or now >= value['deadline_at'], 'active_session_preserved')
    busy = conn.execute(
        'SELECT EXISTS(SELECT 1 FROM cctv_jobs.analysis_tasks WHERE finished_at IS NULL AND NOT paused) '
        'OR EXISTS(SELECT 1 FROM cctv_jobs.flows WHERE finished_at IS NULL AND NOT paused) '
        "OR EXISTS(SELECT 1 FROM cctv_jobs.queue WHERE state->>'status' NOT IN "
        "('handoff_ready','no_candidates','provider_failed','submission_rejected',"
        "'verification_failed','storage_not_ready')) AS busy").fetchone()['busy']
    require(not busy, 'unfinished_work_preserved')
    return history


def activate(conn, api, store, *, candidate, url, config_path, marker_path):
    """All IO is injected for offline tests; production caller supplies real clients."""
    candidate = marker_for(candidate['name'], candidate['approved_input_sha256'])
    url = provider_url(url)
    config_path, marker_path = plain(config_path), plain(marker_path)
    require(marker_path == session.marker_path(), 'selected_marker_path_changed')
    require(marker_path.parent.is_dir() and marker_path.parent.stat().st_uid == os.geteuid()
            and not stat.S_IMODE(marker_path.parent.stat().st_mode) & 0o077, 'private_spool_required')
    backup = marker_path.parent / ('.mlops-activation-' + candidate['name'] + '.json')
    require(not backup.exists() and not backup.is_symlink(), 'activation_backup_exists_manual_review_required')
    expected = profiles.profile('B', deployment=DEPLOYMENT)
    require(profiles.deployment_for_profile(profiles.profile('B')) == DEPLOYMENT,
            'ec2_mlops_deployment_not_selected')
    with ExitStack() as stack:
        for kind in ('admission', 'dispatch', 'flow', 'collect'):
            stack.enter_context(store.lock(kind))
        old_config = read_file(config_path)
        config = config_checked(json.loads(old_config))
        old_marker = read_file(marker_path, optional=True)
        if old_marker is not None:
            session._validate_marker(json.loads(old_marker))
        require(old_marker is None or json.loads(old_marker)['name'] != candidate['name'], 'existing_session_name_preserved')
        history = preflight_db(conn, candidate, session.clock(conn))
        health = api.health(expected)  # Authenticated GET only; no submission/retry.
        started, deadline = verify_health(health, session.clock(conn))
        new_config = encoded(dict(config, runpod_url=url))
        new_marker = encoded(candidate)
        record = {'schema': 'cctv-mlops-activation-backup-v1', 'candidate': candidate,
                  'config_path': str(config_path), 'marker_path': str(marker_path),
                  'previous_config_base64': base64.b64encode(old_config).decode(),
                  'previous_marker_base64': None if old_marker is None else base64.b64encode(old_marker).decode(),
                  'status': 'prepared', 'deadline_at': deadline}
        # Name reservation/backup is durable and exclusive, including on failure.
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded(record)); stream.flush(); os.fsync(stream.fileno())
        fsync_directory(backup.parent)
        changed_marker = changed_config = False
        committed = False
        try:
            # The new marker initially has NO DB grant: interruptions fail closed.
            replace_file(marker_path, old_marker, new_marker)
            changed_marker = True
            replace_file(config_path, old_config, new_config)
            changed_config = True
            with conn.transaction():
                require(preflight_db(conn, candidate, session.clock(conn)) == history, 'session_history_changed')
                verify_health(health, session.clock(conn))
                result = session.initialize(conn, started, deadline)
                require(session_rows(conn) == dict(history, **{candidate['name']: result}), 'previous_sessions_changed')
                require(read_file(config_path) == new_config and read_file(marker_path) == new_marker,
                        'activation_files_changed_before_commit')
            committed = True
        except BaseException:
            # Commit failures can be ambiguous. Restore only after proving no new
            # row exists; otherwise preserve files/backups for manual inspection.
            absent = False
            try:
                absent = not committed and conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',
                    (candidate['name'],)).fetchone() is None
            except Exception:
                pass
            if absent:
                if changed_config:
                    replace_file(config_path, new_config, old_config)
                if changed_marker:
                    replace_file(marker_path, new_marker, old_marker)
            raise ActivationError('activation_failed_backup_preserved_manual_review_required') from None
        return {'status': 'mlops_session_ready', 'name': candidate['name'], 'versions': ['B', 'R2'],
                'purpose': 'gpu_comparison', 'limits': LIMITS, 'deadline_at': deadline,
                'approved_input_sha256': candidate['approved_input_sha256'],
                'previous_state_preserved': True, 'new_vlm_calls': 0, 'jobs_submitted': 0,
                's3_permissions_verified': False, 'clock_offset_verified': False,
                'automatic_retry': False, 'pod_automatic_stop': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--input-sha256', required=True)
    parser.add_argument('--runpod-url', required=True)
    args = parser.parse_args(argv)
    try:
        candidate = marker_for(args.name, args.input_sha256)
        url = provider_url(args.runpod_url)
        config_path = plain(Path(os.environ.get('BE_TARGET_CONFIG', '/input/config-ec2-v1.json')))
        token = read_file(Path('/run/secrets/runpod_worker_token')).decode().strip()
        require(len(token) >= 32, 'api_token_invalid')
        from apps.be_worker.queue_store import connect, Store
        with connect() as conn:
            answer = activate(conn, RunpodAPI(url, token), Store(conn), candidate=candidate,
                              url=url, config_path=config_path, marker_path=session.marker_path())
        print(json.dumps(answer, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception:
        # Exceptions may contain URLs, tokens, DSNs, or provider messages.
        print(json.dumps({'status': 'activation_failed',
                          'error': 'activation_rejected_or_incomplete_check_private_backup_and_session_state',
                          'jobs_submitted': 0, 'automatic_retry': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
