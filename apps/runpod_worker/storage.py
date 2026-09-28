"""Upload verified outputs; manifest is the final S3 completion marker."""
import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re

from verify_bundle import sha256


def verified_files(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory/'manifest.json').read_text())
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
        for event in index['events']:
            if not {event['clip'], event['frame']} <= set(manifest['files']):
                raise ValueError('Missing evidence asset in manifest')
    return manifest


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
    if client is None:
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', region_name=config['region'], config=Config(
            connect_timeout=10, read_timeout=30, retries={'mode':'standard','total_max_attempts':3}))
    prefix = f"{config['prefix']}/{job_id}"
    remote = dict(manifest, schema_version='runpod-s3-evidence-v1', provider_job_id=job_id,
        storage='s3', bucket=config['bucket'], region=config['region'],
        manifest_key=f'{prefix}/manifest.json',
        files={name: dict(entry, key=f'{prefix}/{name}') for name, entry in manifest['files'].items()})

    def put(name, data, digest):
        key = f'{prefix}/{name}'
        client.put_object(Bucket=config['bucket'], Key=key, Body=data,
            ContentType=mimetypes.guess_type(name)[0] or 'application/octet-stream',
            ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
            Metadata={'sha256':digest}, ServerSideEncryption='AES256')
        head = client.head_object(Bucket=config['bucket'], Key=key)
        if head['ContentLength'] != len(data) or head.get('Metadata', {}).get('sha256') != digest:
            raise ValueError('S3 object verification failed')

    import hashlib
    for name, entry in manifest['files'].items():
        path = directory/name
        if path.stat().st_size > 200*1024*1024:
            raise ValueError('Evidence asset exceeds 200 MiB')
        put(name, path.read_bytes(), entry['sha256'])
    body = json.dumps(remote, indent=2).encode()
    put('manifest.json', body, hashlib.sha256(body).hexdigest())
    receipt = dict(bucket=config['bucket'], region=config['region'],
                   manifest_key=remote['manifest_key'], s3_uploaded=True)
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
