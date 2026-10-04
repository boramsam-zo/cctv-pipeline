"""Decide whether an approved main push changes the Runpod image; no network."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

SHA = re.compile('[0-9a-f]{40}\\Z')
ZERO = '0' * 40
IMAGE_PREFIXES = ('apps/runpod_image/', 'apps/model_compare/')
IMAGE_FILES = {
    '.github/workflows/runpod-image.yml',
    'apps/runpod_worker/storage.py', 'apps/runpod_worker/verify_bundle.py',
    'apps/be_worker/analysis_profiles.py', 'apps/be_worker/model_catalog.py',
    'apps/be_worker/ab_evidence.py', 'apps/be_worker/common.py',
    'scripts/prepare_runpod_ci.py', 'scripts/runpod_ci_registry.py',
    'scripts/runpod_ci_changes.py', 'scripts/build_runpod_service_bundle.py',
}


def affects_image(name):
    return (name in IMAGE_FILES or name.startswith(IMAGE_PREFIXES)
            or (name.startswith('apps/be_worker/') and '/' not in name[len('apps/be_worker/'):]
                and name.endswith('-v1.json')))


def decide(event, before, after, run=subprocess.run):
    if event == 'pull_request':
        return False
    if event == 'workflow_dispatch':
        return True
    if event != 'push' or not SHA.fullmatch(before) or not SHA.fullmatch(after) or after == ZERO:
        raise ValueError('unsupported_ci_event_or_commit')
    if before == ZERO:
        return True
    # Checkout fetch-depth: 0 makes both revisions locally available, including
    # multi-commit pushes and force pushes. A missing revision fails closed.
    result = run(['git', 'diff', '--name-only', '-z', '--no-renames', before, after, '--'],
                 capture_output=True, text=True, check=False, timeout=30)
    if result.returncode:
        raise ValueError('ci_change_inspection_failed')
    return any(affects_image(name) for name in result.stdout.split('\0') if name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', required=True)
    parser.add_argument('--before', default='')
    parser.add_argument('--after', required=True)
    args = parser.parse_args()
    needed = decide(args.event, args.before, args.after)
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with Path(output).open('a') as stream:
            stream.write('publish_required=' + str(needed).lower() + '\n')
    print(json.dumps({'publish_required': needed, 'model_calls': 0, 'remote_operations': 0}))


if __name__ == '__main__':
    main()
