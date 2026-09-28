"""Prepare a separate local Compose workspace; no Docker or API requests."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex

ROOT = Path(__file__).resolve().parents[1]


def prepare(directory, project='cctv-pipeline-local', repo=ROOT):
    directory = Path(directory).resolve()
    repo = Path(repo).resolve()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,60}', project):
        raise ValueError('Use a lowercase Compose project name')
    marker = directory / 'workspace.json'
    expected = {'schema': 'cctv-local-v1', 'project': project, 'repository': str(repo)}
    if marker.exists():
        if json.loads(marker.read_text()) != expected:
            raise ValueError('Workspace belongs to another repository or project')
        for name in ('db_admin_password', 'db_app_password'):
            path = directory / 'secrets' / name
            if not path.is_file() or not re.fullmatch(r'[0-9a-f]{64}\n?', path.read_text()):
                raise ValueError('Existing workspace is missing a valid DB secret; restore it')
    elif directory.exists() and any(directory.iterdir()):
        raise ValueError('Choose an empty directory; existing data is preserved')
    for part in ('', 'secrets', 'input', 'documents', 'spool', 'data', 'data/postgresql', 'data/rag-output'):
        path = directory / part
        path.mkdir(parents=True, exist_ok=True)
        # PostgreSQL owns its data once initialized; do not change those permissions.
        if part not in ('data/postgresql',):
            path.chmod(0o700)
    for name in ('db_admin_password', 'db_app_password'):
        path = directory / 'secrets' / name
        if not path.exists():
            with path.open('x') as stream:
                stream.write(secrets.token_hex(32) + '\n')
        # Non-root DB/container users need access to the mounted file.
        # The parent secret directory remains owner-only on the host.
        path.chmod(0o444)
    key = directory / 'secrets/gemini_api_key'
    if not key.exists():
        key.touch(mode=0o600)
    variables = dict(COMPOSE_PROJECT_NAME=project,
        CCTV_COMPOSE_FILE=repo / 'infra/compose/rag/compose.yaml',
        CCTV_DATA_DIR=directory / 'data', CCTV_SECRETS_DIR=directory / 'secrets',
        CCTV_INPUT_DIR=directory / 'documents', BE_INPUT_DIR=directory / 'input',
        BE_SPOOL_DIR=directory / 'spool', BE_SECRETS_DIR=directory / 'secrets',
        CCTV_APP_UID=os.getuid(), CCTV_APP_GID=os.getgid())
    script = directory / 'use-local.sh'
    script.write_text('# Generated paths only; contains no credentials.\n' + ''.join(
        f'export {name}={shlex.quote(str(value))}\n' for name, value in variables.items()))
    script.chmod(0o600)
    marker.write_text(json.dumps(expected, indent=2) + '\n')
    return script


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'runtime/local')
    parser.add_argument('--project', default='cctv-pipeline-local')
    args = parser.parse_args()
    path = prepare(args.directory, args.project)
    print(f'Prepared. Source {path}; no service started and no API called.')


if __name__ == '__main__':
    main()
