"""Prepare an exact configurable CUDA derived-image context; no Docker or model calls."""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service_builder = load_module('gpu_service_builder', ROOT / 'scripts/build_runpod_service_bundle.py')
verifier = service_builder.verifier
runtime = load_module('gpu_image_runtime', ROOT / 'apps/runpod_image/image_runtime.py')
FROZEN = service_builder.FROZEN
ARCHIVE = 'cctv-runpod-cuda-portable.zip'
ARCHIVE_ROOT = 'cctv-runpod-cuda-portable/'
DOCKERIGNORE = (b'**\n!Dockerfile\n!.dockerignore\n!image-manifest.json\n'
                b'!runtime/\n!runtime/**\n!service/\n!service/**\n')
RUNTIME_SOURCES = {
    'runtime/image_runtime.py': 'apps/runpod_image/image_runtime.py',
    'runtime/job.py': 'apps/model_compare/job.py',
    'runtime/runner.py': 'apps/model_compare/runner.py',
}


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()


def read_source(name):
    return runtime.plain_path(ROOT / name).read_bytes()


def derive_image_manifest():
    """Verify every old byte, then bind only the three new executors in v3."""
    manifest_path = runtime.plain_path(FROZEN / 'image-manifest.json')
    if runtime.file_hash(manifest_path) != verifier.IMAGE_MANIFEST_SHA256:
        raise ValueError('Frozen image manifest mismatch')
    old, old_sha = runtime.read_json(manifest_path), runtime.file_hash(manifest_path)
    if len(old['files']) != 53:
        raise ValueError('Frozen image file roster mismatch')
    # Read exactly the pinned roster. Local inspection caches are never copied,
    # removed, or promoted to image sources by this derived context.
    for name, spec in old['files'].items():
        runtime.check_file(runtime.relative_file(FROZEN, name), spec)
    payloads = {target: read_source(source) for target, source in RUNTIME_SOURCES.items()}
    for path, data in payloads.items():
        compile(data, path, 'exec')
    manifest = deepcopy(old)
    manifest.update(schema_version='cctv-runpod-image-v3', gpu=verifier.PORTABLE_GPU,
                    base_image=verifier.PORTABLE_BASE_IMAGE,
                    parent_image_manifest_sha256=old_sha,
                    deployment=verifier.PORTABLE_DEPLOYMENT,
                    scope='Manual CUDA service with automatic GPU detection; original GPU provenance is retained.')
    for path, data in payloads.items():
        manifest['files'][path] = verifier.fingerprint(data)
    catalog = {'schema_version': 'cctv-analysis-deployment-v1',
               'deployment': verifier.PORTABLE_DEPLOYMENT, 'gpu': verifier.PORTABLE_GPU,
               'image_manifest_sha256': verifier.fingerprint(json_bytes(manifest))['sha256'],
               'execution_adapter_sha256': manifest['files']['runtime/job.py']['sha256']}
    verifier.validate_catalog(catalog, deployment=verifier.PORTABLE_DEPLOYMENT)
    return manifest, payloads, catalog


def payloads_for_release():
    manifest, payloads, expected_catalog = derive_image_manifest()
    catalog_data = read_source(verifier.PORTABLE_CATALOG)
    catalog = verifier.validate_catalog(json.loads(catalog_data, object_pairs_hook=verifier._pairs), deployment=verifier.PORTABLE_DEPLOYMENT)
    if catalog != expected_catalog:
        raise ValueError('Portable CUDA deployment catalog differs from derived image/adapter')
    dockerfile = read_source('apps/runpod_image/GPU.Dockerfile')
    service = {}
    for name in verifier.PORTABLE_FILES:
        if name == 'Dockerfile':
            data = dockerfile
        elif name == '.dockerignore':
            data = DOCKERIGNORE
        elif name == verifier.PORTABLE_CATALOG:
            data = catalog_data
        else:
            data = read_source(name)
        if name.endswith('.py'):
            compile(data, name, 'exec')
        service[name] = data
    for overlay, target in (
            ('apps/runpod_image/image_runtime.py', 'runtime/image_runtime.py'),
            ('apps/model_compare/runner.py', 'runtime/runner.py')):
        if service[overlay] != payloads[target]:
            raise ValueError('Service and image runtime copies differ: ' + overlay)
    if service['apps/runpod_image/service_job.py'] != read_source('apps/runpod_image/service_job.py'):
        raise ValueError('Service job source changed while preparing release')
    service_builder.verify_local_imports(service)
    service_manifest = {
        'schema_version': 'cctv-runpod-service-bundle-v3',
        'deployment': verifier.PORTABLE_DEPLOYMENT, 'base_image': verifier.PORTABLE_BASE_IMAGE,
        'image_manifest_sha256': catalog['image_manifest_sha256'],
        'platform': 'linux/amd64', 'default_command': 'pod', 'automatic_inference': False,
        'files': {name: verifier.fingerprint(data) for name, data in sorted(service.items())},
    }
    service['service-manifest.json'] = json_bytes(service_manifest)
    payloads.update({'Dockerfile': dockerfile, '.dockerignore': DOCKERIGNORE,
                     'image-manifest.json': json_bytes(manifest)})
    payloads.update({'service/' + name: data for name, data in service.items()})
    return payloads, catalog


def build(output):
    output = Path(output).absolute()
    runtime.plain_path(output)
    if output.exists():
        raise ValueError('Use a new output directory; existing files are preserved')
    if not output.parent.is_dir():
        raise ValueError('Expected an existing real parent directory')
    payloads, catalog = payloads_for_release()
    output.mkdir(mode=0o700)
    context = output / 'context'
    context.mkdir(mode=0o700)
    for name, data in payloads.items():
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
    if {p.relative_to(context).as_posix() for p in context.rglob('*') if p.is_file()} != set(payloads):
        raise ValueError('Derived context file roster mismatch')
    for name, data in payloads.items():
        if (context / name).read_bytes() != data:
            raise ValueError('Derived context readback mismatch: ' + name)
    checked = verifier.verify(context / 'service')
    archive = output / ARCHIVE
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(payloads.items()):
            item = zipfile.ZipInfo(ARCHIVE_ROOT + name, (2026, 10, 3, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100600 << 16
            bundle.writestr(item, data)
    with zipfile.ZipFile(archive) as bundle:
        if (bundle.testzip() is not None
                or set(bundle.namelist()) != {ARCHIVE_ROOT + name for name in payloads}
                or any(bundle.read(ARCHIVE_ROOT + name) != data for name, data in payloads.items())):
            raise ValueError('Derived release ZIP readback mismatch')
    record = {
        'status': 'cuda_portable_release_prepared_not_built',
        'deployment': verifier.PORTABLE_DEPLOYMENT, 'base_image': verifier.PORTABLE_BASE_IMAGE,
        'platform': 'linux/amd64', 'gpu': verifier.PORTABLE_GPU,
        'parent_image_manifest_sha256': verifier.IMAGE_MANIFEST_SHA256,
        'image_manifest_sha256': catalog['image_manifest_sha256'],
        'execution_adapter_sha256': catalog['execution_adapter_sha256'],
        'service_manifest_sha256': checked['manifest_sha256'],
        'files': len(payloads), 'archive': ARCHIVE,
        **verifier.fingerprint(archive.read_bytes()),
        'frozen_image_files_verified': 53, 'inherited_files_unchanged': 50,
        'updated_image_files': sorted(RUNTIME_SOURCES),
        'includes_model_weights': False, 'includes_inputs_results_or_secrets': False,
        'default_command': 'pod', 'automatic_inference': False,
        'service_started': False, 'docker_build_performed': False,
        'registry_uploads': 0, 'remote_changes': 0, 'model_calls': 0,
        'gpu_inference_verified': False, 'gpu_selection_default': 'auto',
        'gpu_selection_environment': 'CCTV_EXPECTED_GPU',
        'minimum_vram_mib': None, 'observed_gpu_saved_per_execution': True,
        'completed_evidence_reuse_scope': 'same_portable_profile_and_input',
    }
    with (output / 'preparation.json').open('xb') as stream:
        stream.write(json_bytes(record))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False, indent=2))
