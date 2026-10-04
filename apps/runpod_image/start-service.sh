#!/usr/bin/env bash
set -euo pipefail
# Run from the separately verified service bundle, outside frozen image sources.
cd "$(dirname "$0")/../.."
/opt/cctv-image/.venv/bin/python -B apps/runpod_image/verify_service_bundle.py
exec /opt/cctv-image/.venv/bin/python -B -m uvicorn \
  apps.runpod_image.service:create_app --factory --host 0.0.0.0 --port 8000 --workers 1
