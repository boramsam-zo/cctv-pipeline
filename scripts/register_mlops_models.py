#!/usr/bin/env python3
"""Register B/R2 and opt-in lastconv631 references, with explicit per-model uploads.

No training, checkpoint deserialization, model imports, or Runpod requests.
The CLI performs real S3/MLflow calls only when explicitly invoked. Credentials
come from existing SDK providers, never command arguments or printed errors.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.be_worker.common import WorkerError, read_json, save
from apps.be_worker.model_catalog import contract_for_version, contract_sha256
from apps.be_worker import mlflow_tracking as tracking

BASE_IMAGE_DIGEST = '037355788382bd35081d798b4ff6f7131f7745735097058ba58f711af8477cb6'
X3D_SOURCE_URI = ('baseimage://sha256:' + BASE_IMAGE_DIGEST +
    '/opt/cctv-image/sources/deployment_handoff_1001/models/x3d_s_real_so_tad_epoch7_best_model.pt')
REGISTERED_NAMES = {'B': 'cctv-x3d-s', 'R2': 'cctv-r2plus1d-18', 'LC': 'cctv-r3d-18-lastconv631'}
PUBLIC_BLOCK_FIELDS = ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')


def _require(value, code):
    if not value:
        raise WorkerError(code)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def verify_checkpoint(path, contract):
    """Stream bytes only and ensure that the selected original did not change."""
    path = Path(path)
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and info.st_size == contract['weights_bytes'],
             'mlops_checkpoint_size_or_type_mismatch')
    expected = _signature(info)
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        _require(_signature(os.fstat(source.fileno())) == expected, 'mlops_checkpoint_changed')
        while block := source.read(4 * 1024 * 1024):
            hasher.update(block)
        _require(_signature(os.fstat(source.fileno())) == expected, 'mlops_checkpoint_changed')
    _require(_signature(path.lstat()) == expected, 'mlops_checkpoint_changed')
    _require(hasher.hexdigest() == contract['weights_sha256'], 'mlops_checkpoint_sha256_mismatch')
    return expected


def _locations(bucket, key, version_id, contract, *, version='R2'):
    _require(isinstance(bucket, str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)
             and '..' not in bucket, 'mlops_invalid_s3_bucket')
    layout = {'R2': ('r2plus1d_18', 'candidate-epoch-7.pt'),
              'LC': ('r3d_18', 'lastconv631-epoch3.pt')}
    _require(version in layout, 'mlops_unknown_model_version')
    directory, filename = layout[version]
    required_key = 'models/' + directory + '/' + contract['weights_sha256'] + '/' + filename
    _require(key == required_key, 'mlops_' + version.lower() + '_key_not_content_addressed')
    if version_id is not None:
        _require(isinstance(version_id, str) and 1 <= len(version_id) <= 1024
                 and version_id.isascii() and all(32 < ord(c) < 127 for c in version_id),
                 'mlops_invalid_s3_version_id')


def _head(client, bucket, key, version_id):
    args = dict(Bucket=bucket, Key=key, ChecksumMode='ENABLED')
    if version_id is not None:
        args['VersionId'] = version_id
    try:
        return client.head_object(**args)
    except Exception as exc:
        response = getattr(exc, 'response', {})
        code = str(response.get('Error', {}).get('Code', ''))
        if code in ('404', 'NoSuchKey', 'NotFound', 'NoSuchVersion'):
            return None
        raise WorkerError('mlops_s3_head_failed') from None


def _check_head(head, contract, version_id):
    expected_checksum = base64.b64encode(bytes.fromhex(contract['weights_sha256'])).decode()
    _require(isinstance(head, dict) and head.get('ContentLength') == contract['weights_bytes']
             and head.get('Metadata', {}).get('sha256') == contract['weights_sha256'],
             'mlops_s3_model_metadata_mismatch')
    _require(head.get('ServerSideEncryption') == 'AES256', 'mlops_s3_encryption_mismatch')
    checksum = head.get('ChecksumSHA256')
    # Existing objects may only have the verified original SHA metadata. Never
    # describe metadata HEAD as a new independent download/byte verification.
    if checksum is not None:
        _require(checksum == expected_checksum, 'mlops_s3_checksum_mismatch')
    if version_id is not None:
        _require(head.get('VersionId') == version_id, 'mlops_s3_version_mismatch')
    return {'sha256_metadata_matches': True, 'size_matches': True,
            's3_checksum_matches': checksum is not None, 'remote_bytes_downloaded': False,
            'version_id': head.get('VersionId'), 'server_side_encryption': 'AES256'}


def prepare_checkpoint(*, checkpoint, bucket, key, version_id, upload, receipt_dir, client, contract,
                       version='R2'):
    """Never overwrite an existing object; conditional Put is limited to one key."""
    _locations(bucket, key, version_id, contract, version=version)
    signature = verify_checkpoint(checkpoint, contract)
    try:
        access = client.get_public_access_block(Bucket=bucket)['PublicAccessBlockConfiguration']
    except Exception:
        raise WorkerError('mlops_s3_private_bucket_unverified') from None
    _require(all(access.get(name) is True for name in PUBLIC_BLOCK_FIELDS),
             'mlops_s3_private_bucket_unverified')
    receipt_dir = Path(receipt_dir)
    receipt_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    spec = dict(bucket=bucket, key=key, requested_version_id=version_id,
                weights_sha256=contract['weights_sha256'], weights_bytes=contract['weights_bytes'])
    receipt_name = 's3-' + hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest() + '.json'
    receipt_path = receipt_dir / receipt_name
    receipt = read_json(receipt_path) if receipt_path.exists() else {
        'schema_version': 'cctv-mlops-model-publication-v1', 'spec': spec, 'upload_attempted': False}
    _require(receipt.get('spec') == spec, 'mlops_s3_receipt_identity_changed')
    head = _head(client, bucket, key, version_id)
    if head is None:
        _require(version_id is None, 'mlops_s3_requested_version_missing')
        _require(upload is True, 'mlops_s3_model_missing_upload_not_requested')
        _require(not receipt['upload_attempted'], 'mlops_s3_upload_outcome_unknown')
        receipt.update(upload_attempted=True, status='upload_pending')
        save(receipt_path, receipt)
        try:
            with Path(checkpoint).open('rb') as source:
                _require(_signature(os.fstat(source.fileno())) == signature, 'mlops_checkpoint_changed')
                client.put_object(Bucket=bucket, Key=key, Body=source,
                    ContentLength=contract['weights_bytes'], ContentType='application/octet-stream',
                    ServerSideEncryption='AES256', IfNoneMatch='*',
                    Metadata={'sha256': contract['weights_sha256'], 'model-id': contract['model_id']},
                    ChecksumAlgorithm='SHA256',
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(contract['weights_sha256'])).decode())
                _require(_signature(os.fstat(source.fileno())) == signature, 'mlops_checkpoint_changed')
        except Exception as exc:
            # Even an uncertain Put is reconciled through HEAD, never a second
            # Put. If a concurrent create wins, matching bytes may be reused.
            if isinstance(exc, WorkerError):
                raise
        head = _head(client, bucket, key, None)
        _require(head is not None, 'mlops_s3_upload_outcome_unknown')
    receipt.update(_check_head(head, contract, version_id), status='verified_reference')
    _require(_signature(Path(checkpoint).lstat()) == signature, 'mlops_checkpoint_changed')
    save(receipt_path, receipt)
    return receipt


def prepare_r2(**kwargs):
    return prepare_checkpoint(**kwargs, version='R2')


def prepare_lc(**kwargs):
    return prepare_checkpoint(**kwargs, version='LC')


def _write_new(path, value):
    """Atomically publish a private file without replacing any existing target."""
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _require(not path.is_symlink(), 'mlops_bindings_output_unsafe')
    if path.exists():
        _require(read_json(path) == value, 'mlops_existing_bindings_preserved')
        return False
    fd, temporary = tempfile.mkstemp(prefix='.mlops-bindings-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write((json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            _require(not path.is_symlink() and read_json(path) == value, 'mlops_existing_bindings_preserved')
            return False
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True
    finally:
        os.unlink(temporary)


def register_models(args, *, s3_client=None, mlflow_client=None):
    """Injection seam for offline tests; the normal CLI uses existing SDK auth."""
    output = Path(args.bindings_output)
    _require(not output.is_symlink(), 'mlops_bindings_output_unsafe')
    existing = read_json(output) if output.exists() else None
    include_lc = any(getattr(args, key, None) for key in
                     ('lc_checkpoint', 'lc_key', 'lc_version_id', 'upload_lc'))
    _require(not include_lc or (getattr(args, 'lc_checkpoint', None) and getattr(args, 'lc_key', None)),
             'mlops_lc_checkpoint_and_key_required')
    versions = ('B', 'R2', 'LC') if include_lc else ('B', 'R2')
    if existing is not None:
        _require(isinstance(existing, dict) and set(existing) == set(versions),
                 'mlops_existing_bindings_preserved')
    prior = existing or {}
    bindings_input = getattr(args, 'bindings_input', None)
    if bindings_input is not None:
        supplied = read_json(bindings_input)
        _require(isinstance(supplied, dict) and set(supplied) in ({'B', 'R2'}, set(versions)),
                 'mlops_existing_bindings_preserved')
        _require(all(key not in prior or prior[key] == value for key, value in supplied.items()),
                 'mlops_existing_bindings_preserved')
        prior = {**supplied, **prior}
    r2 = contract_for_version('R2')
    _locations(args.s3_bucket, args.r2_key, args.r2_version_id, r2)
    if include_lc:
        _locations(args.s3_bucket, args.lc_key, getattr(args, 'lc_version_id', None),
                   contract_for_version('LC'), version='LC')
    specs = {}
    for version in versions:
        contract = contract_for_version(version)
        key = args.r2_key if version == 'R2' else getattr(args, 'lc_key', None)
        specs[version] = dict(registered_model_name=REGISTERED_NAMES[version],
            model_id=contract['model_id'], model_version=contract['model_version'],
            weights_sha256=contract['weights_sha256'], contract_sha256=contract_sha256(contract),
            source_uri=X3D_SOURCE_URI if version == 'B' else 's3://' + args.s3_bucket + '/' + key)
        if version in prior:
            saved = tracking.validate_binding(prior[version])
            _require(all(saved[key] == value for key, value in specs[version].items()),
                     'mlops_existing_bindings_preserved')
    if s3_client is None:
        import boto3
        from botocore.config import Config
        s3_client = boto3.client('s3', region_name='ap-northeast-2',
            config=Config(connect_timeout=10, read_timeout=180,
                          retries={'total_max_attempts': 1, 'mode': 'standard'}))
    publication = prepare_r2(checkpoint=args.r2_checkpoint, bucket=args.s3_bucket,
        key=args.r2_key, version_id=args.r2_version_id, upload=args.upload_r2,
        receipt_dir=args.receipt_dir, client=s3_client, contract=r2)
    lc_publication = None
    if include_lc:
        lc = contract_for_version('LC')
        lc_publication = prepare_lc(checkpoint=args.lc_checkpoint, bucket=args.s3_bucket,
            key=args.lc_key, version_id=getattr(args, 'lc_version_id', None),
            upload=getattr(args, 'upload_lc', False), receipt_dir=args.receipt_dir,
            client=s3_client, contract=lc)
    client = mlflow_client or tracking.client_for_uri(args.tracking_uri)
    bindings = {}
    for version in versions:
        if version in prior:
            bindings[version] = tracking.verify_registered_model(client, prior[version])
        else:
            bindings[version] = tracking.register_model_reference(args.receipt_dir, specs[version], client=client)
    written = _write_new(output, bindings)
    # Do not print bucket names, keys, host paths, URLs, credentials, or SDK errors.
    result = {'status': 'mlops_model_references_registered', 'bindings_written': written,
            'r2_local_sha256': r2['weights_sha256'], 'r2_bytes': r2['weights_bytes'],
            'r2_upload_attempted': publication['upload_attempted'],
            'remote_sha_metadata_matches': publication['sha256_metadata_matches'],
            'remote_bytes_downloaded': False,
            'models': {v: {'model_id': b['model_id'], 'model_version': b['model_version'],
                          'registered_model_name': b['registered_model_name'],
                          'registered_model_version': b['registered_model_version']} for v, b in bindings.items()},
            'model_loaded': False, 'inference_calls': 0}
    if lc_publication is not None:
        result.update(lc_local_sha256=lc['weights_sha256'], lc_bytes=lc['weights_bytes'],
                      lc_upload_attempted=lc_publication['upload_attempted'],
                      lc_remote_sha_metadata_matches=lc_publication['sha256_metadata_matches'])
    return result


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--tracking-uri', required=True)
    cli.add_argument('--bindings-output', required=True, type=Path)
    cli.add_argument('--bindings-input', type=Path,
                     help='Reuse verified B/R2 references while writing a separate extended output.')
    cli.add_argument('--receipt-dir', required=True, type=Path)
    cli.add_argument('--s3-bucket', required=True)
    cli.add_argument('--r2-key', required=True)
    cli.add_argument('--r2-version-id')
    cli.add_argument('--r2-checkpoint', required=True, type=Path)
    cli.add_argument('--upload-r2', action='store_true',
                     help='Upload only if the exact immutable key is absent; never overwrite.')
    cli.add_argument('--lc-key')
    cli.add_argument('--lc-version-id')
    cli.add_argument('--lc-checkpoint', type=Path,
                     help='Opt in to the recorded lastconv631 checkpoint; requires --lc-key.')
    cli.add_argument('--upload-lc', action='store_true',
                     help='Upload LC only if its exact immutable key is absent; never overwrite.')
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        print(json.dumps(register_models(args), sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'stopped', 'error_code': exc.code if isinstance(exc, WorkerError)
                          else 'mlops_model_registration_failed'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
