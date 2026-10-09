"""Prepare a source-only Runpod build context from a Git checkout, entirely offline.

The approved base manifest stores metadata, never model bytes. Docker's inspect
step verifies its inherited files against the pinned base image during the build.
This preparation cannot claim that a base image was pulled or tested locally.
"""
import argparse
from copy import deepcopy
import importlib.util
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = load('_ci_service_verifier', ROOT / 'apps/runpod_image/verify_service_bundle.py')
import_checker = load('_ci_import_checker', ROOT / 'scripts/build_runpod_service_bundle.py')
BASE_MANIFEST = 'apps/runpod_image/base-image-manifest.json'
RUNTIME_SOURCES = {f'runtime/{name}.py': f'apps/runpod_image/runtime/{name}.py'
                   for name in ('image_runtime', 'job', 'runner')}
# Legacy service imports retain their original modules; the new service imports
# apps.runpod_image.runtime.image_runtime explicitly for the B/R2 release.
SERVICE_OVERRIDES = {}
DOCKERIGNORE = (b'**\n!Dockerfile\n!.dockerignore\n!image-manifest.json\n'
                b'!runtime/\n!runtime/**\n!service/\n!service/**\n')


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()


def plain(path):
    path = Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink or parent traversal rejected')
    return path


def read_source(name, root=ROOT):
    path = plain(root / name)
    if not path.is_file() or not path.is_relative_to(root):
        raise ValueError('Required source file missing: ' + name)
    raw = path.read_bytes()
    if path.suffix == '.py':
        compile(raw, name, 'exec')
    return raw


def payloads_for_release(root=ROOT, *, include_lastconv=False):
    root = plain(root)
    raw_base = read_source(BASE_MANIFEST, root)
    if verifier.fingerprint(raw_base)['sha256'] != verifier.IMAGE_MANIFEST_SHA256:
        raise ValueError('Approved base manifest mismatch')
    base = json.loads(raw_base, object_pairs_hook=verifier._pairs)
    if len(base['files']) != 53 or base['platform'] != 'linux/amd64':
        raise ValueError('Approved base file roster mismatch')
    runtime = {target: read_source(source, root) for target, source in RUNTIME_SOURCES.items()}
    versions = ['B', 'R2', 'LC'] if include_lastconv else ['B', 'R2']
    manifest = deepcopy(base)
    manifest.update(schema_version='cctv-runpod-image-v4', gpu=verifier.PORTABLE_GPU,
                    supported_versions=versions, default_version='B',
                    base_image=verifier.PORTABLE_BASE_IMAGE,
                    parent_image_manifest_sha256=verifier.IMAGE_MANIFEST_SHA256,
                    deployment=verifier.MLOPS_DEPLOYMENT,
                    scope=('B/R2/LC opt-in runtime overlay; R2/LC weights are fetched privately on Pod, never in the image.'
                           if include_lastconv else
                           'B/R2 runtime overlay; R2 weights are fetched privately on Pod, never in the image.'))
    for name, raw in runtime.items():
        manifest['files'][name] = verifier.fingerprint(raw)
    dockerfile = read_source('apps/runpod_image/MLops.Dockerfile', root)
    service = {}
    for name in verifier.MLOPS_FILES:
        if name == verifier.MLOPS_CATALOG:
            continue  # The catalog binds sources without recursively hashing itself.
        if name == 'Dockerfile':
            raw = dockerfile
        elif name == '.dockerignore':
            raw = DOCKERIGNORE
        else:
            raw = read_source(SERVICE_OVERRIDES.get(name, name), root)
        service[name] = raw
    import_checker.verify_local_imports(service)
    code_files = {name: verifier.fingerprint(raw)['sha256'] for name, raw in runtime.items()}
    code_files.update({'service/' + name: verifier.fingerprint(raw)['sha256']
                       for name, raw in service.items() if name.endswith('.py')})
    models = load('_ci_model_contracts', root / 'apps/be_worker/model_catalog.py')
    catalog = {
        'schema_version': 'cctv-mlops-deployment-v1', 'deployment': verifier.MLOPS_DEPLOYMENT,
        'gpu': verifier.PORTABLE_GPU,
        'image_manifest_sha256': verifier.fingerprint(json_bytes(manifest))['sha256'],
        'execution_adapter_sha256': manifest['files']['runtime/job.py']['sha256'],
        'code_files': dict(sorted(code_files.items())),
        'classifiers': {version: deepcopy(models.contract_for_version(version)) for version in versions},
    }
    verifier.validate_mlops_catalog(catalog)
    service[verifier.MLOPS_CATALOG] = json_bytes(catalog)
    service_manifest = {
        'schema_version': 'cctv-runpod-service-bundle-v4', 'deployment': verifier.MLOPS_DEPLOYMENT,
        'base_image': verifier.PORTABLE_BASE_IMAGE, 'platform': 'linux/amd64',
        'image_manifest_sha256': catalog['image_manifest_sha256'],
        'default_command': 'pod', 'automatic_inference': False,
        'files': {name: verifier.fingerprint(raw) for name, raw in sorted(service.items())},
    }
    service['service-manifest.json'] = json_bytes(service_manifest)
    payloads = {'Dockerfile': dockerfile, '.dockerignore': DOCKERIGNORE,
                'image-manifest.json': json_bytes(manifest), **runtime}
    payloads.update({'service/' + name: raw for name, raw in service.items()})
    return payloads, catalog


def write_catalog(root=ROOT):
    """Explicit developer action; changes metadata only, never weights or a release."""
    _, catalog = payloads_for_release(root)
    target = plain(root / verifier.MLOPS_CATALOG)
    archived = False
    if target.exists():
        previous_raw = target.read_bytes()
        previous = json.loads(previous_raw, object_pairs_hook=verifier._pairs)
        verifier.validate_mlops_catalog(previous)
        if previous != catalog:
            canonical = json.dumps(previous, sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False, allow_nan=False).encode()
            identity = hashlib.sha256(canonical).hexdigest()
            archive_dir = plain(target.parent / 'model_releases')
            archive_dir.mkdir(exist_ok=True)
            archive = plain(archive_dir / (identity + '.json'))
            if archive.exists():
                # Existing descriptors are immutable. Formatting differences also
                # require review rather than overwriting a previous receipt.
                if archive.read_bytes() != previous_raw:
                    raise ValueError('Existing release archive differs; preserved without overwrite')
            else:
                with archive.open('xb') as stream:
                    stream.write(previous_raw)
                archived = True
    target.write_bytes(json_bytes(catalog))
    return {'status': 'mlops_catalog_written', 'model_calls': 0,
            'previous_catalog_archived': archived,
            'image_manifest_sha256': catalog['image_manifest_sha256']}


def build(output, root=ROOT, *, include_lastconv=False):
    output = plain(output)
    if output.exists():
        raise ValueError('Use a new output directory; existing files are preserved')
    if not output.parent.is_dir():
        raise ValueError('Output parent must exist')
    payloads, catalog = payloads_for_release(root, include_lastconv=include_lastconv)
    # A third-model release is staged in its own directory. The current B/R2
    # descriptor and all archived release identities remain untouched.
    if not include_lastconv and read_source(verifier.MLOPS_CATALOG, root) != json_bytes(catalog):
        raise ValueError('Source/catalog drift: run prepare_runpod_ci.py --write-catalog and review the change')
    output.mkdir(mode=0o700)
    context = output / 'context'
    context.mkdir(mode=0o700)
    for name, raw in payloads.items():
        target = context / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    actual = {p.relative_to(context).as_posix() for p in context.rglob('*') if p.is_file()}
    if actual != set(payloads) or any((context / name).read_bytes() != raw for name, raw in payloads.items()):
        raise ValueError('Build context readback mismatch')
    checked = verifier.verify(context / 'service')
    pins = {
        'schema_version': 'cctv-demo-release-pins-v1',
        'official_launcher_sha256': verifier.fingerprint(
            payloads['service/apps/runpod_image/start-model-session.sh'])['sha256'],
        'verifier_sha256': verifier.fingerprint(
            payloads['service/apps/runpod_image/verify_service_bundle.py'])['sha256'],
        'catalog_sha256': verifier.fingerprint(payloads['service/' + verifier.MLOPS_CATALOG])['sha256'],
        'service_manifest_sha256': checked['manifest_sha256'],
        'files_verified': checked['files_verified'],
        'supported_versions': list(catalog['classifiers']),
    }
    (output / 'release-pins.json').write_bytes(json_bytes(pins))
    record = {
        'status': 'mlops_ci_context_prepared', 'deployment': verifier.MLOPS_DEPLOYMENT,
        'platform': 'linux/amd64', 'base_image': verifier.PORTABLE_BASE_IMAGE,
        'image_manifest_sha256': catalog['image_manifest_sha256'],
        'execution_adapter_sha256': catalog['execution_adapter_sha256'],
        'service_manifest_sha256': checked['manifest_sha256'], 'files': len(payloads),
        'context_bytes': sum(map(len, payloads.values())),
        'inherited_files_verification': 'deferred to Docker build inspect',
        'includes_model_weights': False, 'includes_secrets': False,
        'model_calls': 0, 'docker_build_performed': False, 'registry_uploads': 0,
        'supported_versions': pins['supported_versions'], 'release_pins': 'release-pins.json',
        'source_catalog_modified': False,
    }
    (output / 'preparation.json').write_bytes(json_bytes(record))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--output', type=Path)
    action.add_argument('--write-catalog', action='store_true')
    parser.add_argument('--include-lastconv', action='store_true',
                        help='Stage B/R2/LC in a new --output directory; preserves the active source catalog.')
    args = parser.parse_args()
    if args.write_catalog and args.include_lastconv:
        parser.error('--include-lastconv requires --output; the current catalog is preserved')
    print(json.dumps(write_catalog() if args.write_catalog else
                     build(args.output, include_lastconv=args.include_lastconv), indent=2))
