"""Verify the small service overlay; no imports of service, SDKs, or models."""
import argparse
import hashlib
import json
from pathlib import Path
import re


BASE_IMAGE = 'dahyeguk/cctv-runpod-ab@sha256:6cd6ee2e3f3e13a7f5d2bea5594db8206d4901f518ed3e73e6eaf4b4a2af55cb'
IMAGE_MANIFEST_SHA256 = 'ce53bcbaa27a4ff5edfc77b688756a8aae45f9a572755a19ad7ddf8430dfccd2'
RTX_BASE_IMAGE = 'dahyeguk/cctv-runpod-ab@sha256:037355788382bd35081d798b4ff6f7131f7745735097058ba58f711af8477cb6'
RTX_DEPLOYMENT = 'rtx2000ada-20261003-v1'
RTX_GPU = 'NVIDIA RTX 2000 Ada Generation'
RTX_CATALOG = 'apps/be_worker/' + RTX_DEPLOYMENT + '.json'
PORTABLE_BASE_IMAGE = RTX_BASE_IMAGE
PORTABLE_DEPLOYMENT = 'cuda-portable-20261003-v1'
PORTABLE_GPU = 'NVIDIA CUDA GPU'
PORTABLE_CATALOG = 'apps/be_worker/' + PORTABLE_DEPLOYMENT + '.json'
FILES = (
    '.dockerignore', 'Dockerfile',
    'apps/be_worker/__init__.py',
    'apps/be_worker/ab_evidence.py',
    'apps/be_worker/analysis_profiles.py',
    'apps/be_worker/common.py',
    'apps/model_compare/__init__.py',
    'apps/model_compare/runner.py',
    'apps/runpod_image/image_runtime.py',
    'apps/runpod_image/service.py',
    'apps/runpod_image/service_job.py',
    'apps/runpod_image/start-service.sh',
    'apps/runpod_image/verify_service_bundle.py',
    'apps/runpod_worker/storage.py',
    'apps/runpod_worker/verify_bundle.py',
)
RTX_FILES = FILES + (RTX_CATALOG,)
PORTABLE_FILES = RTX_FILES + (PORTABLE_CATALOG,)
MLOPS_DEPLOYMENT = 'mlops-20261004-v1'
MLOPS_CATALOG = 'apps/be_worker/' + MLOPS_DEPLOYMENT + '.json'
MLOPS_FILES = PORTABLE_FILES + (
    MLOPS_CATALOG,
    'apps/be_worker/model_catalog.py',
    'apps/runpod_image/model_artifact.py',
    'apps/runpod_image/classifier_adapter.py',
    'apps/runpod_image/lastconv_classifier.py',
    'apps/runpod_image/classifier_pipeline.py',
    'apps/runpod_image/model_prepare.py',
    'apps/runpod_image/runtime/image_runtime.py',
    'apps/runpod_image/start-model-session.sh',
)


def fingerprint(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate service manifest key')
        result[key] = value
    return result


def validate_catalog(catalog, *, deployment=RTX_DEPLOYMENT):
    expected_gpu = {RTX_DEPLOYMENT: RTX_GPU, PORTABLE_DEPLOYMENT: PORTABLE_GPU}.get(deployment)
    if (not isinstance(catalog, dict) or set(catalog) != {'schema_version', 'deployment', 'gpu',
            'image_manifest_sha256', 'execution_adapter_sha256'}
            or catalog['schema_version'] != 'cctv-analysis-deployment-v1'
            or expected_gpu is None or catalog['deployment'] != deployment or catalog['gpu'] != expected_gpu):
        raise ValueError('GPU deployment catalog identity mismatch')
    for name in ('image_manifest_sha256', 'execution_adapter_sha256'):
        value = catalog[name]
        if (not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                or len(set(value)) < 2):
            raise ValueError('GPU deployment catalog digest invalid')
    if (catalog['image_manifest_sha256'] == IMAGE_MANIFEST_SHA256
            or catalog['execution_adapter_sha256'] == '766134f063e2f2e5b0e5699078b8662e75fc3493550ad42d2612644c847c93a0'
            or catalog['image_manifest_sha256'] == catalog['execution_adapter_sha256']):
        raise ValueError('GPU deployment requires a new exact image identity')
    return catalog


def validate_mlops_catalog(catalog):
    required = {'schema_version', 'deployment', 'gpu', 'image_manifest_sha256',
                'execution_adapter_sha256', 'code_files'}
    if (not isinstance(catalog, dict) or set(catalog) not in (required, required | {'classifiers'})
            or catalog['schema_version'] != 'cctv-mlops-deployment-v1'
            or catalog['deployment'] != MLOPS_DEPLOYMENT
            or catalog['gpu'] != PORTABLE_GPU
            or not isinstance(catalog['code_files'], dict) or not catalog['code_files']):
        raise ValueError('MLOps deployment catalog identity mismatch')
    if 'classifiers' in catalog and (not isinstance(catalog['classifiers'], dict)
            or set(catalog['classifiers']) not in ({'B', 'R2'}, {'B', 'R2', 'LC'})
            or any(not isinstance(value, dict) or not value for value in catalog['classifiers'].values())):
        raise ValueError('MLOps classifier snapshots invalid')
    values = [catalog['image_manifest_sha256'], catalog['execution_adapter_sha256'],
              *catalog['code_files'].values()]
    if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for value in values):
        raise ValueError('MLOps deployment digest invalid')
    for name in catalog['code_files']:
        if (not isinstance(name, str) or not name.endswith('.py')
                or not re.fullmatch(r'(?:service/apps|runtime)/[a-zA-Z0-9_./-]+', name)
                or '..' in Path(name).parts):
            raise ValueError('MLOps code path invalid')
    if catalog['code_files'].get('runtime/job.py') != catalog['execution_adapter_sha256']:
        raise ValueError('MLOps execution adapter digest mismatch')
    return catalog


def verify(root):
    root = Path(root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)) or not root.is_dir():
        raise ValueError('Expected a real service directory without symlinks')
    files = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Service bundle symlink rejected')
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError('Service bundle special file rejected')
    if 'service-manifest.json' not in files:
        raise ValueError('Service bundle has missing or unregistered files')
    manifest = json.loads((root / 'service-manifest.json').read_text(), object_pairs_hook=_pairs)
    schema = manifest.get('schema_version')
    allowed = {'cctv-runpod-service-bundle-v1': FILES,
               'cctv-runpod-service-bundle-v2': RTX_FILES,
               'cctv-runpod-service-bundle-v3': PORTABLE_FILES,
               'cctv-runpod-service-bundle-v4': MLOPS_FILES}.get(schema, FILES)
    if files != set(allowed) | {'service-manifest.json'}:
        raise ValueError('Service bundle has missing or unregistered files')
    if schema == 'cctv-runpod-service-bundle-v4':
        catalog = validate_mlops_catalog(json.loads((root / MLOPS_CATALOG).read_text(), object_pairs_hook=_pairs))
        expected_base, expected_manifest = PORTABLE_BASE_IMAGE, catalog['image_manifest_sha256']
        if manifest.get('deployment') != MLOPS_DEPLOYMENT or manifest.get('platform') != 'linux/amd64':
            raise ValueError('Service bundle identity or allowlist mismatch')
        code_files = {'service/' + name: fingerprint((root / name).read_bytes())['sha256']
                      for name in allowed if name.endswith('.py')}
        if {key: value for key, value in catalog['code_files'].items() if key.startswith('service/')} != code_files:
            raise ValueError('MLOps catalog service code mismatch')
    elif schema in ('cctv-runpod-service-bundle-v2', 'cctv-runpod-service-bundle-v3'):
        deployment = PORTABLE_DEPLOYMENT if schema.endswith('v3') else RTX_DEPLOYMENT
        catalog_path = PORTABLE_CATALOG if deployment == PORTABLE_DEPLOYMENT else RTX_CATALOG
        catalog = validate_catalog(json.loads((root / catalog_path).read_text(), object_pairs_hook=_pairs),
                                   deployment=deployment)
        expected_base, expected_manifest = RTX_BASE_IMAGE, catalog['image_manifest_sha256']
        if deployment == PORTABLE_DEPLOYMENT:
            validate_catalog(json.loads((root / RTX_CATALOG).read_text(), object_pairs_hook=_pairs))
        if (manifest.get('deployment') != deployment
                or manifest.get('platform') != 'linux/amd64'):
            raise ValueError('Service bundle identity or allowlist mismatch')
    else:
        expected_base, expected_manifest = BASE_IMAGE, IMAGE_MANIFEST_SHA256
    if (schema not in ('cctv-runpod-service-bundle-v1', 'cctv-runpod-service-bundle-v2',
                       'cctv-runpod-service-bundle-v3', 'cctv-runpod-service-bundle-v4')
            or manifest.get('base_image') != expected_base
            or manifest.get('image_manifest_sha256') != expected_manifest
            or manifest.get('default_command') != 'pod'
            or manifest.get('automatic_inference') is not False
            or set(manifest.get('files', {})) != set(allowed)):
        raise ValueError('Service bundle identity or allowlist mismatch')
    for name, expected in manifest['files'].items():
        if fingerprint((root / name).read_bytes()) != expected:
            raise ValueError('Service bundle file hash mismatch: ' + name)
    return {'status': 'service_bundle_verified', 'files_verified': len(allowed),
            'manifest_sha256': fingerprint((root / 'service-manifest.json').read_bytes())['sha256'],
            'base_image': expected_base, 'model_calls': 0,
            'automatic_inference': False, 'service_started': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).absolute().parents[2])
    print(json.dumps(verify(parser.parse_args().root)))
