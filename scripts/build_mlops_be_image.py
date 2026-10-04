"""Plan, or explicitly build, one local EC2 CPU image without starting services.

Default is offline plan-only. --build verifies the existing base image ID,
creates a non-overwriting local alias, and builds without pulling that base.
No environment secrets, application logs, model imports or host volumes are used.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE_TAG = 'cctv-be-team-rag:small-cpu-v1'
BASE_ID = 'sha256:899b3c12f948784b821d02dcc27c38bd31952e961839a3014a2b4936d3ae82c5'
CONTEXT_FILES = ('MLops.Dockerfile', 'MLops.Dockerfile.dockerignore',
                 'mlflow_requirements.txt', 'mlops_image_check.py')
TAG = re.compile(r'[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z')
IMAGE_ID = re.compile(r'sha256:[a-f0-9]{64}\Z')


def run_command(args, run=subprocess.run, timeout=30):
    return run(args, capture_output=True, text=True, timeout=timeout, check=False)


def inspect(tag, run=subprocess.run, missing=False):
    answer = run_command(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], run)
    if answer.returncode:
        if missing and 'no such image' in answer.stderr.casefold():
            return None
        raise ValueError('docker_image_inspection_failed')
    image_id = answer.stdout.strip()
    if not IMAGE_ID.fullmatch(image_id):
        raise ValueError('docker_image_id_invalid')
    return image_id


def context_identity(context):
    context = Path(context).absolute()
    if any(path.is_symlink() for path in (context, *context.parents)):
        raise ValueError('context_symlink_rejected')
    result = {}
    for name in CONTEXT_FILES:
        path = context / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('build_context_file_missing_or_symlink')
        raw = path.read_bytes()
        result[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    return result


def plan(base_tag, expected_id, output_tag, context=None):
    if (not TAG.fullmatch(base_tag) or not TAG.fullmatch(output_tag)
            or not IMAGE_ID.fullmatch(expected_id) or base_tag == output_tag):
        raise ValueError('invalid_or_conflicting_image_identity')
    context = Path(context or ROOT / 'apps/be_worker').absolute()
    frozen_tag = 'cctv-be-mlops-base:sha-' + expected_id.removeprefix('sha256:')
    if output_tag == frozen_tag:
        raise ValueError('output_conflicts_with_base_alias')
    command = ['docker', 'build', '--pull=false', '--build-arg', 'BASE_IMAGE=' + frozen_tag,
               '--file', str(context / 'MLops.Dockerfile'), '--tag', output_tag, str(context)]
    return {'status': 'mlops_be_image_plan', 'base_image_tag': base_tag,
            'expected_base_image_id': expected_id, 'frozen_local_base_tag': frozen_tag,
            'output_tag': output_tag, 'context_files': context_identity(context),
            'build_command': command, 'docker_build_performed': False,
            'base_image_pulled': False, 'service_changes': False, 'model_calls': 0}


def build(prepared, run=subprocess.run):
    base, expected = prepared['base_image_tag'], prepared['expected_base_image_id']
    frozen, output = prepared['frozen_local_base_tag'], prepared['output_tag']
    if inspect(base, run) != expected:
        raise ValueError('existing_cpu_base_id_mismatch_no_build')
    if inspect(output, run, missing=True) is not None:
        raise ValueError('existing_output_image_preserved')
    aliased = inspect(frozen, run, missing=True)
    if aliased is not None and aliased != expected:
        raise ValueError('existing_base_alias_conflict')
    if aliased is None:
        if run_command(['docker', 'image', 'tag', expected, frozen], run).returncode:
            raise ValueError('base_alias_creation_failed')
    if inspect(frozen, run) != expected or inspect(base, run) != expected:
        raise ValueError('base_identity_changed_before_build')
    context = Path(prepared['build_command'][-1])
    if context_identity(context) != prepared['context_files']:
        raise ValueError('build_context_changed')
    answer = run_command(prepared['build_command'], run, timeout=1200)
    if answer.returncode:
        raise ValueError('cpu_image_build_failed_existing_services_unchanged')
    if inspect(frozen, run) != expected or inspect(base, run) != expected:
        raise ValueError('base_identity_changed_during_build')
    output_id = inspect(output, run)
    # Run by immutable image ID, with no network, mount, GPU, or service command.
    checked = run_command(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                           '--memory', '128m', '--memory-swap', '128m', '--cpus', '1',
                           output_id], run, timeout=60)
    if checked.returncode:
        raise ValueError('cpu_image_metadata_probe_failed')
    try:
        metadata = json.loads(checked.stdout)
    except (ValueError, TypeError):
        raise ValueError('cpu_image_metadata_probe_invalid') from None
    if (not isinstance(metadata, dict)
            or metadata.get('status') != 'cpu_mlflow_packages_verified'
            or metadata.get('base_packages_unchanged') is not True
            or metadata.get('model_calls') != 0 or metadata.get('network_requests') != 0
            or metadata.get('service_started') is not False):
        raise ValueError('cpu_image_metadata_probe_contract_mismatch')
    return dict(prepared, status='mlops_be_image_built_not_deployed', docker_build_performed=True,
                output_image_id=output_id, base_alias_created=aliased is None,
                base_identity_preserved=True, package_probe=metadata,
                checked_at=datetime.now(timezone.utc).isoformat())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image-tag', default=BASE_TAG)
    parser.add_argument('--expected-image-id', default=BASE_ID)
    parser.add_argument('--output-tag', required=True)
    parser.add_argument('--build', action='store_true', help='Explicitly build on the existing EC2 Docker host')
    parser.add_argument('--receipt', type=Path, help='New JSON receipt path; existing files are preserved')
    args = parser.parse_args()
    if args.build and args.receipt is None:
        raise ValueError('build_requires_new_receipt_path')
    if args.receipt is not None:
        if args.receipt.exists() or not args.receipt.parent.is_dir() or any(
                p.is_symlink() for p in (args.receipt, *args.receipt.parents)):
            raise ValueError('receipt_must_be_new_regular_path')
    result = plan(args.base_image_tag, args.expected_image_id, args.output_tag)
    if args.build:
        result = build(result)
    if args.receipt is not None:
        with args.receipt.open('x') as stream:
            json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.TimeoutExpired):
        # Never echo captured Docker output, credentials, or the host environment.
        print(json.dumps({'status': 'mlops_be_image_stopped', 'reason': str(sys.exc_info()[1])
                          if type(sys.exc_info()[1]) is ValueError else 'local_build_operation_failed'}))
        raise SystemExit(1)
