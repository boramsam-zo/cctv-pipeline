"""Mac build/check of one frozen portable CUDA image. Publishing and API startup are separate."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import zipfile

IMAGE = 'cctv-runpod-ab:p6-cuda-portable-v1'
BASE = 'dahyeguk/cctv-runpod-ab@sha256:037355788382bd35081d798b4ff6f7131f7745735097058ba58f711af8477cb6'
DEPLOYMENT = 'cuda-portable-20261003-v1'
GPU = 'NVIDIA CUDA GPU'
ARCHIVE = 'cctv-runpod-cuda-portable.zip'
PREFIX = 'cctv-runpod-cuda-portable/'
CATALOG = 'apps/be_worker/' + DEPLOYMENT + '.json'
RTX_CATALOG = 'apps/be_worker/rtx2000ada-20261003-v1.json'
RUNTIME = {'runtime/image_runtime.py', 'runtime/job.py', 'runtime/runner.py'}
SERVICE = {'.dockerignore', 'Dockerfile', CATALOG, RTX_CATALOG,
           'apps/be_worker/__init__.py', 'apps/be_worker/ab_evidence.py',
           'apps/be_worker/analysis_profiles.py', 'apps/be_worker/common.py',
           'apps/model_compare/__init__.py', 'apps/model_compare/runner.py',
           'apps/runpod_image/image_runtime.py', 'apps/runpod_image/service.py',
           'apps/runpod_image/service_job.py', 'apps/runpod_image/start-service.sh',
           'apps/runpod_image/verify_service_bundle.py', 'apps/runpod_worker/storage.py',
           'apps/runpod_worker/verify_bundle.py'}
CONTEXT = {'.dockerignore', 'Dockerfile', 'image-manifest.json'} | RUNTIME | {
    'service/' + name for name in SERVICE | {'service-manifest.json'}}
RECEIPT = 'image-build-receipt.json'
SHA = re.compile(r'[0-9a-f]{64}\Z')
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}\Z')
INSPECT_FORMAT = ('{"image_id":{{json .Id}},"os":{{json .Os}},'
                  '"architecture":{{json .Architecture}},'
                  '"entrypoint":{{json .Config.Entrypoint}},"cmd":{{json .Config.Cmd}}}')

# Only identity/metadata modules are imported. The service factory and GPU
# startup guard are deliberately absent; no secrets or host directories mounted.
PROBE = r'''import hashlib, importlib.util, json, os, sys
from pathlib import Path
expected = json.loads(sys.argv[1])
image_root, service_root = Path('/opt/cctv-image'), Path('/opt/cctv-service')
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module
runtime = load('_image_identity', image_root / 'runtime/image_runtime.py')
manifest, digest = runtime.inspect_image(image_root)
assert len(manifest['files']) == 53 and digest == expected['image_manifest_sha256']
assert manifest['gpu'] == 'NVIDIA CUDA GPU'
assert manifest['deployment'] == 'cuda-portable-20261003-v1'
environment = runtime.check_environment(manifest)
assert environment['status'] == 'environment_matches' and not environment['mismatches']
verifier = load('_service_identity', service_root / 'apps/runpod_image/verify_service_bundle.py')
service = verifier.verify(service_root)
assert service['files_verified'] == 17
assert service['manifest_sha256'] == expected['service_manifest_sha256']
assert os.environ.get('CCTV_ANALYSIS_DEPLOYMENT') == 'cuda-portable-20261003-v1'
assert os.environ.get('CCTV_EXPECTED_GPU') == 'auto'
profiles = load('_profile_identity', service_root / 'apps/be_worker/analysis_profiles.py')
adapter = manifest['files']['runtime/job.py']['sha256']
assert adapter == expected['execution_adapter_sha256']
summaries = {}
for version in ('A', 'B'):
    value = profiles.profile(version)
    assert profiles.validate_profile(value, expected_version=version) == value
    assert value['image_manifest_sha256'] == digest and value['execution_adapter_sha256'] == adapter
    assert value['runtime']['gpu'] == manifest['gpu']
    assert value['runtime']['python'] == manifest['python_version']
    assert all(manifest['packages'][name] == package for name, package in value['runtime']['packages'].items())
    assert value['source_archive_sha256'] == manifest['source_archives'][version]
    assert all(manifest['files'][name]['sha256'] == sha for name, sha in value['code_files'].items())
    prefix = 'sources/' + ('cctv-runpod-a4000/' if version == 'A' else 'deployment_handoff_1001/')
    assert all(manifest['files'][prefix + name] == spec for name, spec in value['model_files'].items())
    summaries[version] = {'profile_sha256': profiles._sha(value),
                          'image_manifest_sha256': digest, 'execution_adapter_sha256': adapter,
                          'full_manifest_links_verified': True}
for forbidden in ('torch', 'torchvision', 'pytorchvideo', 'ultralytics', 'cv2', 'av', 'numpy', 'boto3'):
    assert not any((name == forbidden or name.startswith(forbidden + '.')) and module is not None
                   for name, module in list(sys.modules.items()))
print(json.dumps({'status':'cuda_portable_image_contents_verified','image_files_verified':53,
 'service_files_verified':17,'image_manifest_sha256':digest,
 'service_manifest_sha256':service['manifest_sha256'],'environment_matches':True,
 'deployment':'cuda-portable-20261003-v1','gpu_contract':manifest['gpu'],'profiles':summaries,
 'expected_gpu_setting':'auto',
 'model_libraries_imported':False,'model_calls':0,'gpu_calls':0,'service_started':False,
 'gpu_inference_verified':False}))
'''


class CheckError(Exception):
    """Only constant error codes, never subprocess output or environment values."""


def require(value, code):
    if not value:
        raise CheckError(code)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def parse(raw):
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(CheckError('invalid_json_number')))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(data):
    return {'bytes': len(data), 'sha256': sha(data)}


def plain(path):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
            'symlink_or_parent_path_rejected')
    return path


def verify_release(release, archive_sha256, image_manifest_sha256, service_manifest_sha256):
    release = plain(release)
    require(release.is_dir(), 'release_directory_missing')
    for value in (archive_sha256, image_manifest_sha256, service_manifest_sha256):
        require(isinstance(value, str) and SHA.fullmatch(value), 'expected_sha256_invalid')
    context, archive = plain(release / 'context'), plain(release / ARCHIVE)
    require(context.is_dir() and archive.is_file(), 'release_files_missing')
    archive_bytes = archive.read_bytes()
    require(sha(archive_bytes) == archive_sha256, 'archive_sha256_mismatch')
    paths = list(context.rglob('*'))
    require(all(not p.is_symlink() and (p.is_file() or p.is_dir()) for p in paths), 'context_special_file')
    require({p.relative_to(context).as_posix() for p in paths if p.is_file()} == CONTEXT,
            'context_roster_mismatch')
    data = {name: (context / name).read_bytes() for name in CONTEXT}
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        require(len(entries) == len(CONTEXT) and {p.filename for p in entries} == {PREFIX + n for n in CONTEXT},
                'archive_roster_mismatch')
        require(all(stat.S_IFMT(p.external_attr >> 16) in (0, stat.S_IFREG) for p in entries),
                'archive_special_file')
        require(bundle.testzip() is None and all(bundle.read(PREFIX + name) == raw for name, raw in data.items()),
                'archive_context_mismatch')
    require(sha(data['image-manifest.json']) == image_manifest_sha256 and
            sha(data['service/service-manifest.json']) == service_manifest_sha256, 'manifest_sha256_mismatch')
    image, service = parse(data['image-manifest.json']), parse(data['service/service-manifest.json'])
    require(image['schema_version'] == 'cctv-runpod-image-v3' and image['gpu'] == GPU and
            image['deployment'] == DEPLOYMENT and image['base_image'] == BASE and len(image['files']) == 53,
            'image_manifest_contract_mismatch')
    require(service['schema_version'] == 'cctv-runpod-service-bundle-v3' and
            service['deployment'] == DEPLOYMENT and service['base_image'] == BASE and
            service['platform'] == 'linux/amd64' and service['default_command'] == 'pod' and
            service['automatic_inference'] is False and service['image_manifest_sha256'] == image_manifest_sha256 and
            set(service['files']) == SERVICE, 'service_manifest_contract_mismatch')
    for name in RUNTIME:
        require(fingerprint(data[name]) == image['files'][name], 'runtime_file_mismatch')
    for name in SERVICE:
        require(fingerprint(data['service/' + name]) == service['files'][name], 'service_file_mismatch')
    require(data['Dockerfile'] == data['service/Dockerfile'] and
            data['.dockerignore'] == data['service/.dockerignore'], 'build_file_copies_differ')
    catalog = parse(data['service/' + CATALOG])
    adapter = image['files']['runtime/job.py']['sha256']
    require(catalog == {'schema_version': 'cctv-analysis-deployment-v1', 'deployment': DEPLOYMENT,
            'gpu': GPU, 'image_manifest_sha256': image_manifest_sha256,
            'execution_adapter_sha256': adapter}, 'catalog_identity_mismatch')
    preparation = parse(plain(release / 'preparation.json').read_bytes())
    require(preparation['sha256'] == archive_sha256 and preparation['files'] == 24 and
            preparation['image_manifest_sha256'] == image_manifest_sha256 and
            preparation['service_manifest_sha256'] == service_manifest_sha256 and
            preparation['execution_adapter_sha256'] == adapter, 'preparation_identity_mismatch')
    return {'archive_sha256': archive_sha256, 'image_manifest_sha256': image_manifest_sha256,
            'service_manifest_sha256': service_manifest_sha256, 'execution_adapter_sha256': adapter,
            'context_sha256': sha(json.dumps({name: fingerprint(raw) for name, raw in data.items()},
                                           sort_keys=True, separators=(',', ':')).encode()), 'context_files': 24}


def docker(args, *, timeout=30, log=None):
    try:
        result = subprocess.run(['docker', *args], text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if log is not None:
            write_new(log, {'status': 'build_command_unavailable_or_timed_out',
                            'error_type': type(exc).__name__})
        raise CheckError('docker_command_unavailable_or_timed_out') from None
    if log is not None:
        write_new(log, {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    require(result.returncode == 0, 'docker_command_failed')
    return result.stdout


def find_image():
    lines = set(docker(['image', 'ls', '--no-trunc', '--quiet', '--filter', 'reference=' + IMAGE]).split())
    require(len(lines) <= 1 and all(IMAGE_ID.fullmatch(item) for item in lines), 'image_listing_invalid')
    return next(iter(lines), None)


def check_image(image_id, identity):
    metadata = parse(docker(['image', 'inspect', '--format', INSPECT_FORMAT, image_id]))
    require(metadata == {'image_id': image_id, 'os': 'linux', 'architecture': 'amd64',
            'entrypoint': ['/opt/cctv-image/entrypoint.sh'], 'cmd': ['pod']}, 'image_config_mismatch')
    result = parse(docker(['run', '--rm', '--network', 'none', '--read-only', '--pull=never',
        '--workdir', '/opt/cctv-service', '--entrypoint', '/opt/cctv-image/.venv/bin/python', image_id,
        '-B', '-c', PROBE, json.dumps(identity, sort_keys=True)], timeout=300))
    expected = {'status': 'cuda_portable_image_contents_verified', 'image_files_verified': 53,
        'service_files_verified': 17, 'image_manifest_sha256': identity['image_manifest_sha256'],
        'service_manifest_sha256': identity['service_manifest_sha256'], 'environment_matches': True,
        'deployment': DEPLOYMENT, 'gpu_contract': GPU, 'expected_gpu_setting': 'auto', 'model_libraries_imported': False,
        'model_calls': 0, 'gpu_calls': 0, 'service_started': False, 'gpu_inference_verified': False}
    require(all(result.get(k) == v and type(result.get(k)) is type(v) for k, v in expected.items()),
            'container_verification_mismatch')
    require(set(result.get('profiles', {})) == {'A', 'B'}, 'profile_verification_missing')
    for value in result['profiles'].values():
        require(value.get('image_manifest_sha256') == identity['image_manifest_sha256'] and
                value.get('execution_adapter_sha256') == identity['execution_adapter_sha256'] and
                value.get('full_manifest_links_verified') is True and
                isinstance(value.get('profile_sha256'), str) and SHA.fullmatch(value['profile_sha256']),
                'profile_verification_mismatch')
    return metadata, result


def write_new(path, value):
    path = plain(path)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def next_record(release, prefix):
    index = 1
    while (release / f'{prefix}-{index:03d}.json').exists():
        index += 1
    return plain(release / f'{prefix}-{index:03d}.json')


@contextmanager
def release_lock(release):
    path = plain(release / 'image-build.lock')
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CheckError('another_image_build_or_check_is_running') from None
        yield


def build_verify(release, archive_sha256, image_manifest_sha256, service_manifest_sha256):
    require(sys.platform == 'darwin', 'run_on_mac')
    release = plain(release)
    args = (release, archive_sha256, image_manifest_sha256, service_manifest_sha256)
    identity = verify_release(*args)
    with release_lock(release):
        previous_path = plain(release / RECEIPT)
        previous = parse(previous_path.read_bytes()) if previous_path.exists() else None
        if previous is not None:
            require(previous.get('image_tag') == IMAGE and previous.get('release_identity') == identity,
                    'recorded_release_identity_changed')
        image_id = find_image()
        if previous is not None:
            require(image_id is not None and previous.get('image_id') == image_id,
                    'recorded_image_missing_or_changed_no_rebuild')
        built = False
        if image_id is None:
            # A new user invocation may retry a failed build with Docker's cache.
            # Preserve every attempt; image builds do not consume inference time.
            intent_path = next_record(release, 'image-build-attempt')
            write_new(intent_path, {'status': 'image_build_requested', 'image_tag': IMAGE,
                                   'release_identity': identity, 'created_at': datetime.now(timezone.utc).isoformat()})
            print(json.dumps({'status': 'cuda_portable_image_building', 'image_tag': IMAGE,
                              'attempt': intent_path.name,
                              'progress': 'Building with the current Docker builder; output is saved after this attempt.'}),
                  file=sys.stderr, flush=True)
            docker(['buildx', 'build', '--load',
                '--platform', 'linux/amd64', '--network', 'none', '--pull=false', '--progress=plain',
                '--tag', IMAGE, str(release / 'context')], timeout=1800,
                log=intent_path.with_name(intent_path.stem + '-output.json'))
            built = True
            image_id = find_image()
            require(image_id is not None, 'built_image_tag_missing')
        metadata, inspection = check_image(image_id, identity)
        require(verify_release(*args) == identity, 'release_changed_during_image_check')
        require(find_image() == image_id, 'image_tag_changed_during_check')
        if previous is None:
            path = previous_path
            status = 'cuda_portable_image_built_and_verified' if built else 'cuda_portable_existing_image_verified'
        else:
            path = next_record(release, 'image-recheck')
            status = 'cuda_portable_image_reverified_no_build'
        result = {'status': status, 'image_tag': IMAGE, 'image_id': image_id,
                  'release_identity': identity, 'metadata': metadata, 'inspection': inspection,
                  'docker_build_performed': built, 'existing_images_removed_or_retagged': False,
                  'created_at': datetime.now(timezone.utc).isoformat(), 'receipt': path.name,
                  'registry_pushes': 0, 'remote_changes': 0, 'model_calls': 0, 'gpu_calls': 0,
                  'service_started': False, 'session_started': False, 'gpu_inference_verified': False}
        write_new(path, result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--image-manifest-sha256', required=True)
    parser.add_argument('--service-manifest-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        result = build_verify(args.release, args.archive_sha256,
                              args.image_manifest_sha256, args.service_manifest_sha256)
        print(json.dumps(result, indent=2))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({'status': 'cuda_portable_image_build_check_stopped',
            'error_code': str(exc) if type(exc) is CheckError else 'local_build_or_check_failed',
            'success_receipt_written_by_this_attempt': False, 'automatic_retry': False,
            'registry_pushes': 0, 'model_calls': 0, 'gpu_calls': 0, 'service_started': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
