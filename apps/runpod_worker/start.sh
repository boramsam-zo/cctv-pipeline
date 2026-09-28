#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Start this service on the Runpod Linux host." >&2
  exit 1
fi
.venv/bin/python verify_bundle.py
.venv/bin/python - <<'PY'
import os, secrets
from pathlib import Path
path = Path('.api-token')
if not path.exists():
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(secrets.token_urlsafe(32) + '\n')
os.chmod(path, 0o600)
print('API token is stored privately in .api-token; it is not printed.')
PY
exec .venv/bin/python -m uvicorn service:create_app --factory --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
