#!/usr/bin/env bash
# User-run, in the Pod terminal AFTER applying the new image yourself:
# bash /opt/cctv-service/apps/runpod_image/start-model-session.sh \
#   mlops-session-YYYYMMDD-NN /workspace/private/runtime.env
# runtime.env must be a private (0600) file of export KEY=VALUE assignments.
# Required: CCTV_API_TOKEN_FILE, AWS_SHARED_CREDENTIALS_FILE, S3_BUCKET,
# CCTV_MODEL_S3_BUCKET, CCTV_R2_MODEL_S3_KEY. Credentials/token files: 0600.
# No Pod creation, model execution, S3 request, or old-session reset happens here.
# Keep this process running (for example in the user's terminal multiplexer).
set +x
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo 'usage: start-model-session.sh mlops-session-YYYYMMDD-NN /absolute/private/runtime.env' >&2
  exit 2
fi
cd "$(dirname "$0")/../.."
exec /opt/cctv-image/.venv/bin/python -B - "$1" "$2" <<'PY'
import configparser
from datetime import date
import os
from pathlib import Path
import re
import shlex
import stat
import sys

def require(ok, code):
    if not ok:
        raise ValueError(code)

def private_file(value):
    path = Path(value)
    require(path.is_absolute() and '..' not in path.parts
            and not any(p.is_symlink() for p in (path, *path.parents)), 'private_path_invalid')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1 and info.st_uid == os.geteuid()
            and 0 < info.st_size <= 131072, 'private_file_invalid')
    return path.read_text()

try:
    name, source = sys.argv[1:]
    require(re.fullmatch(r'mlops-session-[0-9]{8}-[0-9]{2}', name)
            and not name.endswith('-00'), 'session_name_invalid')
    day = name.split('-')[-2]
    date.fromisoformat(day[:4] + '-' + day[4:6] + '-' + day[6:])
    allowed = {'CCTV_API_TOKEN_FILE', 'AWS_SHARED_CREDENTIALS_FILE', 'AWS_CONFIG_FILE',
               'AWS_PROFILE', 'AWS_DEFAULT_REGION', 'AWS_REGION', 'AWS_EC2_METADATA_DISABLED',
               'EVIDENCE_STORAGE', 'S3_BUCKET', 'S3_PREFIX', 'CCTV_MODEL_S3_BUCKET',
               'CCTV_R2_MODEL_S3_KEY', 'CCTV_R2_MODEL_S3_VERSION_ID'}
    settings = {}
    # Parse literal shell assignments; do not source executable shell content.
    for line in private_file(source).splitlines():
        words = shlex.split(line, comments=True)
        if not words:
            continue
        require(len(words) == 2 and words[0] == 'export' and '=' in words[1], 'private_env_syntax_invalid')
        key, value = words[1].split('=', 1)
        require(key in allowed and key not in settings and value
                and not any(c in value for c in ('\n', '\r', '\x00', '$', '`')), 'private_env_assignment_invalid')
        settings[key] = value
    require({'CCTV_API_TOKEN_FILE', 'AWS_SHARED_CREDENTIALS_FILE', 'S3_BUCKET',
             'CCTV_MODEL_S3_BUCKET', 'CCTV_R2_MODEL_S3_KEY'} <= settings.keys(), 'private_env_incomplete')
    token = private_file(settings['CCTV_API_TOKEN_FILE']).strip()
    require(32 <= len(token) <= 4096 and token.isascii() and not any(c.isspace() for c in token), 'api_token_invalid')
    credentials = configparser.RawConfigParser(interpolation=None, strict=True)
    credentials.read_string(private_file(settings['AWS_SHARED_CREDENTIALS_FILE']))
    require(credentials.sections() == ['default'] and not credentials.defaults(), 'aws_profile_invalid')
    values = credentials['default']
    require({'aws_access_key_id', 'aws_secret_access_key'} <= values.keys()
            and values.keys() <= {'aws_access_key_id', 'aws_secret_access_key', 'aws_session_token'}
            and all(v and v.isascii() and not any(c.isspace() for c in v) for v in values.values()), 'aws_credentials_invalid')
    if 'AWS_CONFIG_FILE' in settings:
        config = configparser.RawConfigParser(interpolation=None, strict=True)
        config.read_string(private_file(settings['AWS_CONFIG_FILE']))
        require(config.sections() == ['default'] and not config.defaults()
                and dict(config['default']) == {'region': 'ap-northeast-2'}, 'aws_config_invalid')
    for key in ('S3_BUCKET', 'CCTV_MODEL_S3_BUCKET'):
        require(re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', settings[key]), 's3_bucket_invalid')
    model_key = settings['CCTV_R2_MODEL_S3_KEY']
    require(not model_key.startswith('/') and '..' not in model_key.split('/')
            and 0 < len(model_key.encode()) <= 1024, 'model_key_invalid')
    fixed = {'AWS_PROFILE': 'default', 'AWS_DEFAULT_REGION': 'ap-northeast-2',
             'AWS_REGION': 'ap-northeast-2', 'AWS_EC2_METADATA_DISABLED': 'true',
             'EVIDENCE_STORAGE': 's3', 'S3_PREFIX': 'cctv-evidence'}
    require(all(key not in settings or settings[key] == value for key, value in fixed.items()), 'runtime_env_conflict')
    service_root = Path('/workspace/cctv-image-state/service') / name / 'jobs'
    require(not any(p.is_symlink() for p in (service_root, *service_root.parents))
            and not service_root.parent.exists(), 'existing_session_preserved_choose_unused_name')
    # Claim the name atomically. Even startup failure cannot silently reuse/reset it.
    service_root.parent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    service_root.parent.mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('AWS_', 'CCTV_', 'S3_'))}
    env.update(settings)
    env.update(fixed)
    env.setdefault('AWS_CONFIG_FILE', '/dev/null')
    env.update(CCTV_ANALYSIS_DEPLOYMENT='mlops-20261004-v1', CCTV_EXPECTED_GPU='auto',
               CCTV_SERVICE_ROOT=str(service_root), CCTV_GPU_MAX_JOBS='2',
               CCTV_GPU_JOB_SECONDS='300', CCTV_GPU_TOTAL_SECONDS='900')
    print('Starting B/R2 API: 2 jobs, 300 seconds/job, 900 seconds/session; no automatic inference.', flush=True)
    os.execve('/bin/bash', ['bash', 'apps/runpod_image/start-service.sh'], env)
except Exception:
    # File/parser errors may contain credentials. Do not emit their exception text.
    print('Model session startup rejected; check private files, settings, and unused session name. Existing state preserved.', file=sys.stderr)
    sys.exit(1)
PY
