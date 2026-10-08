"""Allowlisted CPU worker release; no keys, videos, receipts, states or model weights."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAMES = tuple('apps/be_worker/' + name for name in (
    '__init__.py', 'common.py', 'runpod_api.py', 'tracing.py', 'worker.py',
    'config.example.json', 'README.md', 'verify_bundle.py',
    'job_queue.py', 'queue_store.py', 'queue_schema.sql', 'queue_requirements.txt',
    'Queue.Dockerfile', 'queue.compose.yaml', 'queue.sh', 'prepare_queue.py', 'QUEUE.md',
    '.dockerignore', 'scene_jobs.py', 'flow_status.py', 'prepare_stage1.py', 'report_jobs.py', 'report_schema.sql', 'prepare_stage5.py', 'flow_schema.sql', 'flow_service.py', 'ui_read.py', 'ui_validation.py', 'web_api.py', 'daemon.py', 'service.compose.yaml', 'prepare_stage6.py',
    'generation_settings.py', 'generation.env.example', 'prepare_generation_settings.py',
    'analysis_profiles.py', 'ab_evidence.py', 'browser_preview.py', 'selection_jobs.py',
    'selection_schema.sql', 'prepare_selections.py', 'trial_limits.py')) + tuple('apps/scene_description/' + name for name in (
        'scene.py', 'ab_scene.py', 'db.py', 'schema.sql')) + ('apps/rag_index/index.py',) + tuple('apps/cctv_ui/' + name for name in ('app.py', 'client.py', 'Dockerfile', '.dockerignore'))


def build(output):
    files = {name: (ROOT / name).read_bytes() for name in NAMES}
    manifest = {'schema_version': 'be-code-bundle-v1',
                'files': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    files['be-bundle-manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            item = zipfile.ZipInfo(name)
            item.external_attr = 0o100644 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, data)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(files)
        for name, data in files.items():
            assert archive.read(name) == data
    return {'files': len(files), 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), indent=2))
