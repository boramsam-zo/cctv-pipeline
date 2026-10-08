#!/usr/bin/env python3
"""Plan a finite demo API inside the Pod; --apply starts one unused session.

Run with the existing image Python. This never uploads credentials, downloads
weights, executes inference, terminates another API, or resets an old session.
The original image launcher and bundle remain byte-for-byte unchanged.
The pinned catalog and service manifest identify the published parallel S3
transfer release; a different image requires a reviewed pin update.
"""
import argparse
import ast
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

SERVICE = Path('/opt/cctv-service')
PYTHON = '/opt/cctv-image/.venv/bin/python'
SESSION_BASE = Path('/workspace/cctv-image-state/service')
LAUNCH_BASE = Path('/workspace/cctv-demo-launch')
OFFICIAL_SHA = 'af60963e6a1013ba16af1e8c375bdb07c2d476c4a709a0fb52c1ff87c0793a1f'
VERIFIER_SHA = '157ba1ec3cd612b9e8e61aeac202ed22c1cdf9c1300ff5e050b1daf192bfa072'
CATALOG_SHA = 'c0e5352b26fce8e9659026ad7afd1e4478ffd6c903c38f4922c0150f059d594a'
MANIFEST_SHA = '5844409cc8fc66432e12a7dda6cd6e29482839307baabacea84af2ae2830c9dc'
CLAIMED = False


class LaunchError(ValueError):
    """Only this finite, non-secret code may appear in stdout."""
    def __init__(self, code):
        if not re.fullmatch(r'[a-z0-9_]{1,100}', code):
            code = 'demo_launch_unverified'
        self.code = code
        super().__init__(code)


def require(ok, code):
    if not ok:
        raise LaunchError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def configuration(name, hours, max_jobs):
    require(isinstance(name, str) and re.fullmatch(r'demo-session-[0-9]{8}-[0-9]{2}', name)
            and not name.endswith('-00'), 'demo_session_name_invalid')
    day = name.split('-')[-2]
    try:
        date.fromisoformat(day[:4] + '-' + day[4:6] + '-' + day[6:])
    except ValueError:
        raise LaunchError('demo_session_name_invalid') from None
    require(type(hours) is int and 1 <= hours <= 24 and type(max_jobs) is int
            and 1 <= max_jobs <= 20, 'demo_limits_invalid')
    return {'name': name, 'hours': hours, 'max_jobs': max_jobs,
            'limits': {'max_jobs': max_jobs, 'per_job_seconds': 300.0,
                       'whole_service_seconds': float(hours * 3600)}}


def plain(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts
            and not any(p.is_symlink() for p in (path, *path.parents)), 'plain_path_required')
    return path


def private_directory(path):
    info = plain(path).stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'private_directory_invalid')


def embedded_launcher(raw, config):
    require(sha(raw) == OFFICIAL_SHA, 'official_launcher_changed')
    text = raw.decode()
    require(text.count("<<'PY'\n") == 1 and text.endswith('\nPY\n'), 'official_launcher_framing_changed')
    code = text.split("<<'PY'\n", 1)[1][:-4]
    seconds = config['hours'] * 3600
    replacements = {
        "r'mlops-session-[0-9]{8}-[0-9]{2}'": "r'demo-session-[0-9]{8}-[0-9]{2}'",
        "CCTV_GPU_MAX_JOBS='2'": f"CCTV_GPU_MAX_JOBS='{config['max_jobs']}'",
        "CCTV_GPU_TOTAL_SECONDS='900'": f"CCTV_GPU_TOTAL_SECONDS='{seconds}'",
        'Starting B/R2 API: 2 jobs, 300 seconds/job, 900 seconds/session; no automatic inference.':
            f"Starting B/R2 demo API: {config['max_jobs']} jobs, 300 seconds/job, {seconds} seconds/session; no automatic inference.",
    }
    for before, after in replacements.items():
        require(code.count(before) == 1, 'official_launcher_substitution_changed')
        code = code.replace(before, after, 1)
    ast.parse(code)
    return code


def validation_code(code):
    marker = '    # Claim the name atomically.'
    require(code.count(marker) == 1, 'official_validation_boundary_changed')
    prefix = code.split(marker, 1)[0] + "except Exception:\n    raise ValueError('private_runtime_validation_failed') from None\n"
    ast.parse(prefix)
    return prefix


def validate_private_without_start(code, name, runtime_env):
    """Execute only the original pinned launcher's pre-mutation validations."""
    prefix = validation_code(code)
    original = sys.argv
    namespace = {'__name__': 'cctv_demo_readonly_validation'}
    try:
        sys.argv = ['validated-demo-session', name, str(runtime_env)]
        exec(compile(prefix, '<pinned-demo-validation>', 'exec'), namespace)
    except Exception:
        raise LaunchError('private_runtime_validation_failed') from None
    finally:
        sys.argv = original
    return namespace['token']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_health(token):
    request = urllib.request.Request('http://127.0.0.1:8000/health',
        headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'boram3-cctv-be-worker/1.0'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=2) as response:
        raw = response.read(131073)
        require(len(raw) <= 131072, 'api_health_too_large')
        result = json.loads(raw)
    require(isinstance(result, dict) and result.get('status') == 'ok'
            and result.get('api_schema') == 'runpod-ab-api-v1'
            and result.get('storage') == 's3' and result.get('gpu_concurrency') == 1
            and result.get('supported_versions') == ['B', 'R2'], 'api_health_identity_changed')
    return result


def health_summary(health, now):
    limits, admissions, deadline = health.get('limits'), health.get('admissions'), health.get('deadline_at')
    require(isinstance(limits, dict) and set(limits) == {'max_jobs', 'per_job_seconds', 'whole_service_seconds'}
            and type(limits['max_jobs']) is int and 1 <= limits['max_jobs'] <= 100
            and all(type(limits[k]) in (int, float) and math.isfinite(limits[k]) and limits[k] > 0
                    for k in ('per_job_seconds', 'whole_service_seconds'))
            and type(admissions) is int and 0 <= admissions <= limits['max_jobs']
            and type(deadline) in (int, float) and math.isfinite(deadline), 'api_health_limits_invalid')
    return {'authenticated_health': True, 'limits': limits, 'admissions': admissions,
            'deadline_at': deadline, 'expired': deadline <= now,
            'exhausted': admissions >= limits['max_jobs']}


def existing_api(token, *, proc=Path('/proc')):
    processes = []
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            argv = (process / 'cmdline').read_bytes().split(b'\0')
        except OSError:
            continue
        if b'apps.runpod_image.service:create_app' in argv:
            processes.append({'pid': int(process.name)})
    try:
        with socket.create_connection(('127.0.0.1', 8000), timeout=1):
            listening = True
    except ConnectionRefusedError:
        listening = False
    summary = {'processes': processes, 'port8000_listening': listening,
               'present': bool(processes) or listening, 'automatic_stop': False}
    if summary['present']:
        try:
            summary.update(health_summary(local_health(token), time.time()))
        except Exception:
            summary.update(authenticated_health=False, expired=None, exhausted=None,
                           health_status='not_verified')
        summary['instruction'] = 'separate_verified_graceful_shutdown_required_before_apply; no stop signal is sent by this helper'
        # Read only safe execution statuses through the existing process's fixed service root.
        counts, readable = {}, True
        for item in processes:
            try:
                env = dict(v.split(b'=', 1) for v in (proc / str(item['pid']) / 'environ').read_bytes().split(b'\0') if b'=' in v)
                root = plain(env[b'CCTV_SERVICE_ROOT'].decode())
                require(root.parent.parent == SESSION_BASE and root.name == 'jobs', 'existing_service_root_unverified')
                require(root.is_dir(), 'existing_service_root_unverified')
                for status_path in root.glob('*/status.json'):
                    plain(status_path)
                    require(status_path.stat().st_size <= 1024 * 1024,
                            'existing_job_status_unverified')
                    value = json.loads(status_path.read_bytes())
                    status = value.get('status')
                    require(status in ('uploading', 'queued', 'running', 'completed', 'succeeded', 'failed', 'rejected'),
                            'existing_job_status_unverified')
                    counts[status] = counts.get(status, 0) + 1
            except Exception:
                readable = False
        summary['job_status_counts'] = counts if readable else None
        summary['in_flight_jobs'] = sum(counts.get(k, 0) for k in ('uploading', 'queued', 'running')) if readable and processes else None
    return summary


def inspect(config, runtime_env):
    require(sys.platform.startswith('linux') and Path('/proc/1/environ').is_file(), 'run_inside_runpod_required')
    init = dict(v.split(b'=', 1) for v in Path('/proc/1/environ').read_bytes().split(b'\0') if b'=' in v)
    pod = init.get(b'RUNPOD_POD_ID', os.environ.get('RUNPOD_POD_ID', '').encode()).decode()
    require(re.fullmatch(r'[a-z0-9]{10,32}', pod), 'runpod_identity_missing')
    runtime_env = plain(runtime_env)
    private_directory(runtime_env.parent)
    session, launch = plain(SESSION_BASE / config['name']), plain(LAUNCH_BASE / config['name'])
    require(not session.exists() and not launch.exists(), 'existing_session_or_launch_preserved')
    if LAUNCH_BASE.exists():
        private_directory(LAUNCH_BASE)
    launcher = plain(SERVICE / 'apps/runpod_image/start-model-session.sh')
    verifier = plain(SERVICE / 'apps/runpod_image/verify_service_bundle.py')
    catalog = plain(SERVICE / 'apps/be_worker/mlops-20261004-v1.json')
    require(sha(verifier.read_bytes()) == VERIFIER_SHA, 'bundle_verifier_changed')
    require(sha(catalog.read_bytes()) == CATALOG_SHA, 'model_catalog_changed')
    code = embedded_launcher(launcher.read_bytes(), config)
    token = validate_private_without_start(code, config['name'], runtime_env)
    checked = subprocess.run([PYTHON, '-B', str(verifier), '--root', str(SERVICE)],
        cwd=SERVICE, capture_output=True, text=True, timeout=35)
    require(checked.returncode == 0, 'service_bundle_verification_failed')
    bundle = json.loads(checked.stdout)
    require(bundle.get('status') == 'service_bundle_verified' and bundle.get('manifest_sha256') == MANIFEST_SHA
            and bundle.get('files_verified') == 25 and bundle.get('model_calls') == 0, 'service_bundle_identity_changed')
    existing = existing_api(token)
    plan = {'status': 'demo_pod_api_readonly_plan', 'pod_id': pod, 'session': config['name'],
        'limits': config['limits'], 'can_apply': not existing['present'], 'existing_api': existing,
        'original_launcher_sha256': OFFICIAL_SHA, 'effective_launcher_sha256': sha(code.encode()),
        'helper_sha256': sha(Path(__file__).read_bytes()),
        'service_manifest_sha256': MANIFEST_SHA, 'model_catalog_sha256': CATALOG_SHA,
        'overrides': ['session_name_pattern', 'max_jobs', 'whole_service_seconds', 'human_startup_banner'],
        'owner_private_runtime_validated': True, 'session_and_launch_unused': True,
        'image_catalog_files_modified': False, 'model_calls': 0, 'model_downloads': 0,
        'credential_values_exposed': False, 'automatic_retry': False, 'remote_mutations': 0}
    # Stable identity excludes the changing process/deadline observation.
    review = {k: plan[k] for k in ('pod_id', 'session', 'limits', 'original_launcher_sha256',
              'effective_launcher_sha256', 'helper_sha256', 'service_manifest_sha256', 'model_catalog_sha256')}
    plan['plan_sha256'] = sha(json.dumps(review, sort_keys=True, separators=(',', ':')).encode())
    return plan, code, token


def write_exclusive(path, value, *, text=False):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        if text:
            stream.write(value)
        else:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def apply(config, runtime_env, approved=None):
    global CLAIMED
    plan, code, token = inspect(config, runtime_env)
    require(plan['can_apply'], 'existing_api_preserved_separate_shutdown_required')
    require(approved is None or approved == plan['plan_sha256'], 'reviewed_pod_start_plan_changed')
    os.umask(0o077)
    LAUNCH_BASE.mkdir(mode=0o700, exist_ok=True)
    private_directory(LAUNCH_BASE)
    launch = LAUNCH_BASE / config['name']
    launch.mkdir(mode=0o700)
    CLAIMED = True
    write_exclusive(launch / 'intent.json', {'status': 'demo_pod_api_start_intent', 'plan': plan,
                                           'automatic_retry': False})
    script = launch / 'launcher.py'
    write_exclusive(script, code, text=True)
    require(sha(script.read_bytes()) == plan['effective_launcher_sha256'], 'private_launcher_write_changed')
    with (launch / 'api.log').open('xb') as log:
        process = subprocess.Popen([PYTHON, '-B', str(script), config['name'], str(runtime_env)],
            cwd=SERVICE, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
        write_exclusive(launch / 'pid.json', {'pid': process.pid, 'session': config['name']})
    until = time.monotonic() + 60
    while time.monotonic() < until:
        require(process.poll() is None, 'api_start_failed_preserve_private_log')
        try:
            health = local_health(token)
            summary = health_summary(health, time.time())
            require(health['limits'] == config['limits'] and health['admissions'] == 0,
                    'started_api_health_changed')
            remaining = health['deadline_at'] - time.time()
            require(config['hours'] * 3600 - 90 <= remaining <= config['hours'] * 3600,
                    'started_api_window_invalid')
            budget = json.loads(plain(SESSION_BASE / config['name'] / 'jobs/budget.json').read_bytes())
            require(budget['limits'] == config['limits'] and budget['deadline_at'] == health['deadline_at']
                    and budget['deadline_at'] - budget['started_at'] == config['hours'] * 3600,
                    'started_api_budget_mismatch')
            answer = {'status': 'demo_pod_api_started', 'pod_id': plan['pod_id'], 'session': config['name'],
                'pid': process.pid, 'plan_sha256': plan['plan_sha256'], 'limits': config['limits'],
                'deadline_at': health['deadline_at'], 'admissions': 0, 'authenticated_local_health': True,
                'model_calls': 0, 'model_downloads': 0, 'automatic_retry': False,
                'pod_automatic_stop': False, 'image_catalog_files_modified': False}
            write_exclusive(launch / 'ready.json', answer)
            return answer
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    raise LaunchError('api_health_unverified_inspect_without_restart')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--hours', type=int, default=8)
    parser.add_argument('--max-jobs', type=int, default=5)
    parser.add_argument('--runtime-env', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approved-plan-sha256')
    args = parser.parse_args()
    config = configuration(args.name, args.hours, args.max_jobs)
    answer = (apply(config, args.runtime_env, args.approved_plan_sha256) if args.apply
              else inspect(config, args.runtime_env)[0])
    print(json.dumps(answer, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'demo_pod_api_start_unverified' if CLAIMED else 'demo_pod_api_plan_not_ready',
            'error': exc.code if isinstance(exc, LaunchError) else 'demo_launch_unverified',
            'launch_intent_written': CLAIMED, 'automatic_retry': False, 'model_calls': 0,
            'instruction': 'inspect_existing_process_and_private_log_without_restarting'}))
        raise SystemExit(2)
