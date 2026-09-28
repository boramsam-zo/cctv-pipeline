"""Build a private Runpod ZIP from source and separately supplied model weights."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    'worker.py', 'service.py', 'evidence.py', 'storage.py', 'client.py',
    'verify_bundle.py', 'setup.sh', 'start.sh', 'constraints.txt', 'requirements.txt',
    'local_hub/hubconf.py', 'handoff/pipeline.py', 'handoff/timeline.py',
)


def record(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return {'bytes': Path(path).stat().st_size, 'sha256': digest.hexdigest()}


def build(weights, output, repo=ROOT):
    repo, weights, output = Path(repo), Path(weights), Path(output)
    expected = json.loads((repo / 'models/expected.json').read_text())['files']
    for name, entry in expected.items():
        if Path(name).name != name or not name.endswith('.pt'):
            raise ValueError('Invalid model filename')
        if record(weights / name) != entry:
            raise ValueError(f'Model checksum mismatch: {name}')
    for name in RUNTIME_FILES:
        if not (repo / 'apps/runpod_worker' / name).is_file():
            raise ValueError(f'Missing runtime source: {name}')
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / 'cctv-runpod-a4000'
    for name in RUNTIME_FILES:
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / 'apps/runpod_worker' / name, target)
    for name in expected:
        target = bundle / 'handoff/models' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(weights / name, target)
        if record(target) != expected[name]:
            raise ValueError('Model changed during packaging')
    files = {p.relative_to(bundle).as_posix(): record(p)
             for p in sorted(bundle.rglob('*')) if p.is_file()}
    (bundle / 'bundle-manifest.json').write_text(json.dumps({
        'schema_version': 'runpod-bundle-v1', 'profile': 'single-gpu-evidence-s3',
        'files': files}, indent=2) + '\n')
    archive = output / 'cctv-runpod-a4000.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as zip_file:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                zip_file.write(path, path.relative_to(output))
    with zipfile.ZipFile(archive) as zip_file:
        if zip_file.testzip() is not None:
            raise ValueError('ZIP integrity check failed')
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    path = build(args.weights_dir, args.output)
    print(json.dumps({'archive': str(path), 'models_loaded': False, 'network_calls': 0}))


if __name__ == '__main__':
    main()
