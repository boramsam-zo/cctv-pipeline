"""Upload verified outputs; manifest is the final S3 completion marker."""
import argparse
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

try:
    from .verify_bundle import sha256
except ImportError:  # The original Runpod bundle executes this file directly.
    from verify_bundle import sha256


def verified_files(directory):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    if manifest.get('schema_version') == 'runpod-evidence-v2':
        # Import only for the versioned service contract; standalone v1 bundles
        # do not need the BE package or any model/network libraries.
        from apps.be_worker.ab_evidence import validate_local
        return validate_local(directory)
    if {'analysis_profile', 'gpu_key', 'normalized_result_sha256'} & manifest.keys():
        raise ValueError('Versioned evidence cannot use a legacy manifest schema')
    directory = directory.resolve()
    required = {'result.json', 'vlm_handoffs.json', 'runtime.json'}
    if not required <= set(manifest['files']):
        raise ValueError('Incomplete result manifest')
    if manifest.get('schema_version') == 'runpod-evidence-v1' and 'evidence.json' not in manifest['files']:
        raise ValueError('Missing evidence index')
    for name, entry in manifest['files'].items():
        relative = PurePosixPath(name)
        path = (directory/name).resolve()
        if relative.is_absolute() or '..' in relative.parts or not path.is_relative_to(directory):
            raise ValueError('Unsafe manifest path')
        if not path.is_file() or path.stat().st_size != entry['bytes'] or sha256(path) != entry['sha256']:
            raise ValueError('Result integrity mismatch')
        if path.suffix == '.json':
            json.loads(path.read_text())
    if manifest.get('schema_version') == 'runpod-evidence-v1':
        index = json.loads((directory/'evidence.json').read_text())
        if index.get('schema_version') == 'runpod-evidence-v2':
            raise ValueError('Versioned evidence cannot use a legacy manifest schema')
        for event in index['events']:
            if not {event['clip'], event['frame']} <= set(manifest['files']):
                raise ValueError('Missing evidence asset in manifest')
    return manifest


def _s3_error(exc, *codes):
    """Recognize explicit S3 outcomes without importing the optional SDK."""
    response = getattr(exc, 'response', {})
    return (str(response.get('Error', {}).get('Code', '')) in codes or
            str(response.get('ResponseMetadata', {}).get('HTTPStatusCode', '')) in codes)


def _verify_remote(client, bucket, key, data, digest, head=None):
    head = head if head is not None else client.head_object(Bucket=bucket, Key=key)
    if head.get('ContentLength') != len(data) or head.get('Metadata', {}).get('sha256') != digest:
        raise ValueError('S3 object conflicts with verified evidence')
    response = client.get_object(Bucket=bucket, Key=key)
    body = response['Body']
    try:
        actual = body.read(len(data) + 1)
    finally:
        body.close()
    if len(actual) != len(data) or hashlib.sha256(actual).hexdigest() != digest:
        raise ValueError('S3 object bytes differ from verified evidence')


def _put_v2(client, bucket, key, name, data, digest):
    """Create once, or verify exact existing bytes; never overwrite a conflict."""
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if not _s3_error(exc, '404', 'NoSuchKey', 'NotFound'):
            raise
    else:
        _verify_remote(client, bucket, key, data, digest, head)
        return
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data, IfNoneMatch='*',
            ContentType=mimetypes.guess_type(name)[0] or 'application/octet-stream',
            ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
            Metadata={'sha256': digest}, ServerSideEncryption='AES256')
    except Exception as exc:
        # Another writer may have completed this exact immutable object after
        # our HEAD. Inspect it once; unknown outcomes are left for explicit resume.
        if not _s3_error(exc, '412', 'PreconditionFailed'):
            raise
    _verify_remote(client, bucket, key, data, digest)


def _receipt_matches(path, receipt):
    if path.exists() and json.loads(path.read_text()) != receipt:
        raise ValueError('Existing S3 receipt belongs to another publication')


def _save_v2_receipt(directory, receipt):
    path = directory / 's3_receipt.json'
    _receipt_matches(path, receipt)
    if path.exists():
        return
    # The completed file appears atomically, and a racing different receipt is
    # never replaced. A temporary sibling is outside the immutable file package.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                dir=directory.parent, prefix='.s3-receipt-', delete=False) as out:
            temporary = Path(out.name)
            json.dump(receipt, out, indent=2)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            _receipt_matches(path, receipt)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def settings():
    mode = os.environ.get('EVIDENCE_STORAGE', 'local')
    if mode not in ('local', 's3'):
        raise ValueError('EVIDENCE_STORAGE must be local or s3')
    if mode == 'local':
        return None
    bucket = os.environ.get('S3_BUCKET', '')
    region = os.environ.get('AWS_DEFAULT_REGION', '')
    prefix = os.environ.get('S3_PREFIX', 'cctv-evidence').strip('/')
    if not bucket or not region or not re.fullmatch(r'[A-Za-z0-9/_-]+', prefix):
        raise ValueError('Set S3_BUCKET, AWS_DEFAULT_REGION and a safe S3_PREFIX')
    return dict(bucket=bucket, region=region, prefix=prefix)


def publish(directory, job_id, config, client=None):
    directory = Path(directory)
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Expected provider job ID')
    manifest = verified_files(directory)
    versioned = manifest.get('schema_version') == 'runpod-evidence-v2'
    if versioned:
        prefix_path = PurePosixPath(config['prefix'])
        if (not re.fullmatch(r'[A-Za-z0-9/_-]+', config['prefix']) or
                prefix_path.is_absolute() or str(prefix_path) != config['prefix'] or
                any(part in ('', '.', '..') for part in prefix_path.parts) or
                config['prefix'] != 'cctv-evidence' or not config['bucket'] or not config['region']):
            raise ValueError('Unsafe S3 evidence location')
        if any(entry['bytes'] > 200*1024*1024 for entry in manifest['files'].values()):
            raise ValueError('Evidence asset exceeds 200 MiB')
    if client is None:
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', region_name=config['region'], config=Config(
            connect_timeout=10, read_timeout=30, retries={
                'mode':'standard','total_max_attempts':1 if versioned else 3}))
    prefix = f"{config['prefix']}/{job_id}"
    remote = dict(manifest, schema_version='runpod-s3-evidence-v2' if versioned else 'runpod-s3-evidence-v1', provider_job_id=job_id,
        storage='s3', bucket=config['bucket'], region=config['region'],
        manifest_key=f'{prefix}/manifest.json',
        files={name: dict(entry, key=f'{prefix}/{name}') for name, entry in manifest['files'].items()})
    body = json.dumps(remote, indent=2, sort_keys=versioned).encode()
    receipt = dict(bucket=config['bucket'], region=config['region'],
                   manifest_key=remote['manifest_key'], s3_uploaded=True)
    if versioned:
        receipt.update(manifest_sha256=hashlib.sha256(body).hexdigest(),
                       gpu_key=manifest['gpu_key'], input_sha256=manifest['input_sha256'],
                       analysis_profile=manifest['analysis_profile'])
        _receipt_matches(directory / 's3_receipt.json', receipt)
        try:
            head = client.head_object(Bucket=config['bucket'], Key=remote['manifest_key'])
        except Exception as exc:
            if not _s3_error(exc, '404', 'NoSuchKey', 'NotFound'):
                raise
        else:
            _verify_remote(client, config['bucket'], remote['manifest_key'], body,
                           receipt['manifest_sha256'], head)

    def put(name, data, digest):
        key = f'{prefix}/{name}'
        if versioned:
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Evidence changed after local verification')
            return _put_v2(client, config['bucket'], key, name, data, digest)
        client.put_object(Bucket=config['bucket'], Key=key, Body=data,
            ContentType=mimetypes.guess_type(name)[0] or 'application/octet-stream',
            ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
            Metadata={'sha256':digest}, ServerSideEncryption='AES256')
        head = client.head_object(Bucket=config['bucket'], Key=key)
        if head['ContentLength'] != len(data) or head.get('Metadata', {}).get('sha256') != digest:
            raise ValueError('S3 object verification failed')

    for name, entry in manifest['files'].items():
        path = directory/name
        if path.stat().st_size > 200*1024*1024:
            raise ValueError('Evidence asset exceeds 200 MiB')
        put(name, path.read_bytes(), entry['sha256'])
    put('manifest.json', body, hashlib.sha256(body).hexdigest())
    if versioned:
        _save_v2_receipt(directory, receipt)
    else:
        (directory/'s3_receipt.json').write_text(json.dumps(receipt, indent=2))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Retry upload of existing outputs without GPU inference')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    config = settings()
    if config is None:
        parser.error('Set EVIDENCE_STORAGE=s3 before publishing')
    print(json.dumps(publish(args.output, args.job, config), indent=2))
