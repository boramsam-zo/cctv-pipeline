#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Run setup.sh in the Runpod terminal, not on the Mac." >&2
  exit 1
fi
python3 verify_bundle.py
python3 - <<'PY'
import torch, torchvision
assert torch.__version__.split('+')[0] == '2.8.0', 'Image must provide torch 2.8.0'
assert torchvision.__version__.split('+')[0] == '0.23.0', 'Image must provide torchvision 0.23.0'
assert torch.cuda.is_available(), 'CUDA GPU is unavailable'
print('Base image:', torch.__version__, torchvision.__version__, torch.cuda.get_device_name(0))
PY
mkdir -p runtime
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -c constraints.txt -r requirements.txt 2>&1 | tee runtime/setup.log
.venv/bin/python -m pip freeze > runtime/environment.freeze.txt
.venv/bin/python worker.py --check-only | tee runtime/environment.json
echo "Setup finished. Start the API with: bash start.sh"
