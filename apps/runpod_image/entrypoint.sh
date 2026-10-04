#!/usr/bin/env bash
set -euo pipefail
cd /opt/cctv-image
if [[ "${1:-}" == "pod" ]]; then
  if [[ "$#" != 1 ]]; then
    echo 'pod does not accept inference arguments' >&2
    exit 2
  fi
  python runtime/image_runtime.py inspect
  python runtime/image_runtime.py check-environment
  echo 'cctv_image_ready; inference_jobs_started=0; service_API_not_started'
  # Preserve the official base image's SSH/Jupyter startup for manual validation.
  exec /start.sh
fi
exec python runtime/image_runtime.py "$@"
