"""Metadata-only CPU image checks; never import a model or connect to a service."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import re

REQUIRED_BASE = {'torch': '2.8.0+cpu', 'sentence-transformers': '5.1.2',
                 'transformers': '4.57.6', 'numpy': '2.0.2', 'jsonschema': '4.25.1'}
CLIENT = {'mlflow-skinny': '3.16.1'}


def installed():
    result = {}
    for item in importlib.metadata.distributions():
        name, version = item.metadata['Name'], item.version
        if not re.fullmatch('[A-Za-z0-9_.-]+', name) or not re.fullmatch('[A-Za-z0-9_.+!-]+', version):
            raise ValueError('package_metadata_invalid')
        key = re.sub('[-_.]+', '-', name).lower()
        if key in result and result[key] != version:
            raise ValueError('duplicate_distribution_versions')
        result[key] = version
    return dict(sorted(result.items()))


def snapshot(directory, packages=None):
    packages = installed() if packages is None else packages
    if any(packages.get(name) != version for name, version in REQUIRED_BASE.items()):
        raise ValueError('expected_existing_cpu_runtime_missing')
    directory = Path(directory)
    directory.mkdir(exist_ok=True)
    with (directory / 'base-packages.json').open('x') as stream:
        json.dump(packages, stream, sort_keys=True, indent=2)
    with (directory / 'base-constraints.txt').open('x') as stream:
        stream.write(''.join(name + '==' + version + '\n' for name, version in sorted(packages.items())))
    return {'status': 'cpu_base_packages_recorded', 'packages': len(packages), 'model_calls': 0}


def verify(directory, packages=None):
    packages = installed() if packages is None else packages
    previous = json.loads((Path(directory) / 'base-packages.json').read_text())
    if any(packages.get(name) != version for name, version in previous.items()):
        raise ValueError('existing_cpu_runtime_changed')
    if any(packages.get(name) != version for name, version in {**REQUIRED_BASE, **CLIENT}.items()):
        raise ValueError('mlops_cpu_requirements_missing')
    return {'status': 'cpu_mlflow_packages_verified', 'base_packages_unchanged': True,
            'model_calls': 0, 'network_requests': 0, 'service_started': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('snapshot', 'verify'))
    parser.add_argument('--directory', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps((snapshot if args.action == 'snapshot' else verify)(args.directory)))
