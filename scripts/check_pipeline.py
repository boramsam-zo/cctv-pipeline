"""Run synthetic tests. No cloud, GPU inference or operational DB access."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    # PostgreSQL integration must be run separately against an isolated test DB.
    env.pop('CCTV_QUEUE_ISOLATED_POSTGRES', None)
    for directory in ('pipeline', 'rag', 'scene_description', 'be_worker', 'runpod'):
        result = subprocess.run([sys.executable, '-m', 'unittest', 'discover',
            '-s', f'tests/{directory}', '-p', 'test_*.py', '-v'], cwd=ROOT, env=env)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
