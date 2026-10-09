"""Explicit bounded S3 preparation, executed before the offline GPU child."""
import argparse
import json
import os
from pathlib import Path

from apps.runpod_image.classifier_adapter import CHECKPOINT_SHA256, CHECKPOINT_BYTES
from apps.runpod_image.lastconv_classifier import CHECKPOINT_SHA256 as LC_SHA256, CHECKPOINT_BYTES as LC_BYTES
from apps.runpod_image.model_artifact import ensure_cached_artifact, verify_file

CACHE_ROOT = Path('/workspace/cctv-model-cache')


def cached_r2():
    return verify_file(CACHE_ROOT / (CHECKPOINT_SHA256 + '.pt'), CHECKPOINT_SHA256, CHECKPOINT_BYTES)


def cached_lc():
    return verify_file(CACHE_ROOT / (LC_SHA256 + '.pt'), LC_SHA256, LC_BYTES)


def prepare_lc():
    """Explicit preparation only; no guessed S3 location or automatic download at startup."""
    target = CACHE_ROOT / (LC_SHA256 + '.pt')
    if target.exists() or target.is_symlink():
        return cached_lc()
    bucket = os.environ.get('CCTV_MODEL_S3_BUCKET')
    key = os.environ.get('CCTV_LC_MODEL_S3_KEY')
    if not bucket or not key:
        raise ValueError('lastconv_model_source_not_configured')
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', region_name='ap-northeast-2',
                          config=Config(connect_timeout=5, read_timeout=30,
                                        retries={'total_max_attempts': 1}))
    return ensure_cached_artifact(client, bucket=bucket, key=key, sha256=LC_SHA256,
        size=LC_BYTES, cache_root=CACHE_ROOT,
        version_id=os.environ.get('CCTV_LC_MODEL_S3_VERSION_ID'))


def prepare_r2():
    target = CACHE_ROOT / (CHECKPOINT_SHA256 + '.pt')
    if target.exists() or target.is_symlink():
        return cached_r2()
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', region_name='ap-northeast-2',
                          config=Config(connect_timeout=5, read_timeout=30,
                                        retries={'total_max_attempts': 1}))
    return ensure_cached_artifact(client,
        bucket=os.environ['CCTV_MODEL_S3_BUCKET'], key=os.environ['CCTV_R2_MODEL_S3_KEY'],
        sha256=CHECKPOINT_SHA256, size=CHECKPOINT_BYTES, cache_root=CACHE_ROOT,
        version_id=os.environ.get('CCTV_R2_MODEL_S3_VERSION_ID'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=['R2', 'LC'], required=True)
    args = parser.parse_args()
    (prepare_lc if args.version == 'LC' else prepare_r2)()
    print(json.dumps({'status': 'model_artifact_verified', 'version': args.version,
                      'weights_sha256': LC_SHA256 if args.version == 'LC' else CHECKPOINT_SHA256,
                      'model_calls': 0}))
