"""Prepare a code-only Runpod service overlay and image context; no Docker/API calls."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'service_bundle_verifier', ROOT / 'apps/runpod_image/verify_service_bundle.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
FROZEN = ROOT / 'artifacts/runpod/image-p4-20261001-01/context'
ARCHIVE = 'cctv-runpod-service.zip'
DOCKERIGNORE = b'**\n!Dockerfile\n!.dockerignore\n!service-manifest.json\n!apps/\n!apps/**\n'


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()


def verify_local_imports(payloads):
    """Check recursive app imports, including imports inside functions, before shipping."""
    modules = {name[:-3].replace('/', '.') for name in payloads if name.endswith('.py')}
    namespaces = {'.'.join(name.split('.')[:i]) for name in modules
                  for i in range(1, len(name.split('.')))}
    for path, data in payloads.items():
        if not path.endswith('.py'):
            continue
        module = path[:-3].replace('/', '.')
        parent = module.rsplit('.', 1)[0]
        for node in ast.walk(ast.parse(data, filename=path)):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                source = node.module or ''
                if node.level:
                    prefix = parent.split('.')[:len(parent.split('.')) - node.level + 1]
                    source = '.'.join(prefix + ([source] if source else []))
                imports = [source]
                if source in namespaces and source not in modules:
                    imports += [source + '.' + alias.name for alias in node.names]
            for imported in imports:
                if imported.startswith('apps.') and imported not in modules | namespaces:
                    raise ValueError('Missing local import in service bundle: ' + imported)


def build(output):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Use a new output directory; existing files are preserved')
    if not output.parent.is_dir() or any(p.is_symlink() for p in (output.parent, *output.parent.parents)):
        raise ValueError('Expected an existing real parent directory')
    manifest_bytes = (FROZEN / 'image-manifest.json').read_bytes()
    if verifier.fingerprint(manifest_bytes)['sha256'] != verifier.IMAGE_MANIFEST_SHA256:
        raise ValueError('Frozen image manifest mismatch')
    frozen_manifest = json.loads(manifest_bytes)
    helpers = {'apps/model_compare/runner.py': 'runtime/runner.py',
               'apps/runpod_image/image_runtime.py': 'runtime/image_runtime.py'}
    payloads = {}
    for name in verifier.FILES:
        if name == '.dockerignore':
            payloads[name] = DOCKERIGNORE
            continue
        source = (FROZEN / helpers[name] if name in helpers else
                  ROOT / ('apps/runpod_image/Service.Dockerfile' if name == 'Dockerfile' else name))
        if any(p.is_symlink() for p in (source, *source.parents)):
            raise ValueError('Source symlink rejected')
        payloads[name] = source.read_bytes()
        if name in helpers and verifier.fingerprint(payloads[name]) != frozen_manifest['files'][helpers[name]]:
            raise ValueError('Frozen image helper changed: ' + name)
    verify_local_imports(payloads)
    # These helpers are copied only to the separate service tree. The actual GPU
    # command still executes the unchanged runtime inside the pinned base image.
    manifest = {'schema_version': 'cctv-runpod-service-bundle-v1',
                'base_image': verifier.BASE_IMAGE,
                'image_manifest_sha256': verifier.IMAGE_MANIFEST_SHA256,
                'platform': 'linux/amd64', 'default_command': 'pod',
                'automatic_inference': False,
                'files': {name: verifier.fingerprint(data) for name, data in sorted(payloads.items())}}
    payloads['service-manifest.json'] = json_bytes(manifest)
    output.mkdir(mode=0o700)
    context = output / 'context'
    context.mkdir(mode=0o700)
    for name, data in payloads.items():
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as target:
            target.write(data)
    verified = verifier.verify(context)
    archive = output / ARCHIVE
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(payloads.items()):
            item = zipfile.ZipInfo('cctv-runpod-service/' + name, (2026, 10, 2, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100600 << 16
            bundle.writestr(item, data)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or any(
                bundle.read('cctv-runpod-service/' + name) != data for name, data in payloads.items()):
            raise ValueError('Service ZIP readback mismatch')
    record = {'status': 'service_overlay_prepared_not_built',
              'base_image': verifier.BASE_IMAGE, 'platform': 'linux/amd64',
              'files': len(payloads), 'manifest_sha256': verified['manifest_sha256'],
              'archive': ARCHIVE, **verifier.fingerprint(archive.read_bytes()),
              'includes_model_weights': False, 'includes_inputs_results_or_secrets': False,
              'frozen_image_helpers_unchanged': True, 'default_command': 'pod',
              'service_started': False, 'docker_build_performed': False,
              'registry_uploads': 0, 'remote_changes': 0, 'model_calls': 0}
    with (output / 'preparation.json').open('xb') as target:
        target.write(json_bytes(record))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False, indent=2))
