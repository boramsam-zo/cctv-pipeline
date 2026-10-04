"""Prepare an allowlisted image context from existing verified A/B ZIPs; no Docker."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('frozen_ab_sources', ROOT / 'scripts/build_ab_comparison_bundle.py')
frozen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frozen)
BASE_TAG = 'runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404'
BASE_DIGEST = 'sha256:4d1721e62b56d345c83b4fd6090664be6daf9312caab5b2e76f23d8231941851'
IMAGE_FILES = ('Dockerfile', '.dockerignore', 'requirements.txt', 'constraints.txt',
               'entrypoint.sh', 'build_check.py')


def fingerprint(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()


def forbidden(name):
    parts = Path(name).parts
    return any(p.startswith('.env') or p in {'secrets', '.api-token', '.aws', '.venv',
               '__pycache__', 'spool', '.git'} or p.endswith(('.pem', '.key')) for p in parts)


def build(output):
    if output.exists() or output.is_symlink():
        raise ValueError('Use a new context directory; existing files are preserved')
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ValueError('Expected an existing real parent directory')
    sources = {v: frozen.source_files(v) for v in ('A', 'B')}
    payloads = {}
    for version, members in sources.items():
        source = frozen.SOURCES[version]
        archive = ROOT / 'artifacts/runpod/ab-transfer-20261001-01' / source['archive']
        with zipfile.ZipFile(archive) as zipped:
            for name, identity in members.items():
                relative = source['root'] + '/' + name
                data = zipped.read(relative)
                if forbidden(relative) or fingerprint(data) != identity:
                    raise ValueError('Unapproved or changed source file')
                payloads['sources/' + relative] = data
    for name in IMAGE_FILES:
        payloads[name] = (ROOT / 'apps/runpod_image' / name).read_bytes()
    for name, source in {'image_runtime.py': 'apps/runpod_image/image_runtime.py',
                         'job.py': 'apps/model_compare/job.py',
                         'runner.py': 'apps/model_compare/runner.py'}.items():
        payloads['runtime/' + name] = (ROOT / source).read_bytes()
    # The model adapters are exactly the versions used by the completed trial.
    with zipfile.ZipFile(ROOT / 'artifacts/runpod/ab-runner-20261001-02/cctv-ab-runner-20261001-01.zip') as z:
        for name in ('job.py', 'runner.py'):
            if payloads['runtime/' + name] != z.read('cctv-ab-runner-20261001-01/' + name):
                raise ValueError('Completed-trial adapter changed')
    for name, data in payloads.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')
    versions = {'torch': '2.8.0+cu128', 'torchvision': '0.23.0+cu128'}
    for line in payloads['requirements.txt'].decode().splitlines():
        if line and not line.startswith('#'):
            name, version = line.split('==')
            versions[name] = version
    manifest = {
        'schema_version': 'cctv-runpod-image-v1', 'default_version': 'A',
        'supported_versions': ['A', 'B'], 'gpu': 'NVIDIA RTX A4500',
        'python_version': '3.12.3', 'packages': versions,
        'base_image': BASE_TAG + '@' + BASE_DIGEST, 'platform': 'linux/amd64',
        'source_archives': {v: frozen.SOURCES[v]['sha256'] for v in ('A', 'B')},
        'files': {name: fingerprint(data) for name, data in sorted(payloads.items())},
        'scope': 'A/B offline image validation. HTTP/S3/BE selection integration remains P6.',
        'old_trial_attempts_remaining': 0, 'new_inference_approved': False,
    }
    payloads['image-manifest.json'] = json_bytes(manifest)
    output.mkdir(mode=0o700)
    context = output / 'context'
    context.mkdir()
    for name, data in payloads.items():
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as target:
            target.write(data)
    for name, data in payloads.items():
        if fingerprint((context / name).read_bytes()) != fingerprint(data):
            raise ValueError('Prepared context readback mismatch')
    record = {
        'status': 'image_context_prepared_not_built', 'files': len(payloads),
        'bytes': sum(len(data) for data in payloads.values()),
        'manifest_sha256': fingerprint(payloads['image-manifest.json'])['sha256'],
        'source_files_including_manifests': {v: len(names) for v, names in sources.items()},
        'base_image': manifest['base_image'], 'platform': manifest['platform'],
        'includes_model_weights': True, 'includes_videos_or_secrets': False,
        'original_trial_adapters_unchanged': True, 'docker_build_performed': False,
        'registry_uploads': 0, 'inference_calls': 0,
        'original_full_dependency_freeze_available': False,
        'remaining': ['Docker build and synthetic codec checks', 'registry destination and user push',
                      'new bounded GPU validation plan', 'actual Runpod A/B reproduction'],
    }
    with (output / 'preparation.json').open('xb') as target:
        target.write(json_bytes(record))
    pending_plan = {
        'schema_version': 'cctv-image-run-plan-v1', 'approved': False,
        'plan_id': 'image-validation-pending', 'state_root': '/workspace/cctv-image-state',
        'image_manifest_sha256': record['manifest_sha256'], 'jobs': [],
        'limits': {'max_jobs': None, 'max_attempts_per_job': 1, 'gpu_concurrency': 1,
                   'per_job_timeout_seconds': None, 'whole_plan_timeout_seconds': None,
                   'automatic_retries': 0, 'gemini_calls': 0, 'embedding_calls': 0},
    }
    with (output / 'validation-plan.pending.json').open('xb') as target:
        target.write(json_bytes(pending_plan))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False, indent=2))
