"""Run as root on the target Linux host. Creates secrets locally, never prints their values."""
import argparse
import getpass
import os
from pathlib import Path
import re
import secrets

def prepare(data: Path, secret_dir: Path, *, app_uid=10001, app_gid=10001):
    marker = data / '.compose-prepared'
    if (data / 'postgresql/PG_VERSION').exists() and not marker.exists():
        raise RuntimeError('Existing database belongs to another setup; preserve it and review migration first')
    data.mkdir(mode=0o755, parents=True, exist_ok=True)
    secret_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    secret_dir.chmod(0o700)
    # Existing passwords are validated and preserved; never silently rotate a populated DB.
    for name in ('db_admin_password', 'db_app_password'):
        path = secret_dir / name
        if path.is_symlink():
            raise RuntimeError('Secret symlinks are not accepted')
        if path.exists():
            if not re.fullmatch('[a-f0-9]{64}', path.read_text().strip()):
                raise ValueError(f'Invalid existing secret file: {name}')
        elif (data / 'postgresql/PG_VERSION').exists():
            raise RuntimeError('Existing database has a missing secret; restore it, do not regenerate it')
        else:
            with path.open('x') as stream:
                stream.write(secrets.token_hex(32) + '\n')
        # Compose mounts these files read-only; parent 0700 restricts host access.
        # Non-root postgres/app users inside the assigned containers can read their own mounts.
        path.chmod(0o444)
    (data / 'postgresql').mkdir(mode=0o700, exist_ok=True)
    output = data / 'rag-output'
    output.mkdir(mode=0o700, exist_ok=True)
    os.chown(output, app_uid, app_gid)
    gemini = secret_dir / 'gemini_api_key'
    if gemini.is_symlink():
        raise RuntimeError('Secret symlinks are not accepted')
    if not gemini.exists():
        with gemini.open('x') as stream:
            stream.write('')
    gemini.chmod(0o444)
    marker.touch(exist_ok=True)

def set_gemini(secret_dir: Path):
    value = getpass.getpass('Gemini API key (hidden): ').strip()
    if not value or any(c.isspace() for c in value):
        raise ValueError('Enter one non-empty API key')
    temp = secret_dir / ('.gemini-' + secrets.token_hex(8))
    with temp.open('x') as stream:
        stream.write(value + '\n')
    temp.chmod(0o444)
    temp.replace(secret_dir / 'gemini_api_key')
    print('Gemini key saved privately; no API called')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=Path('/srv/cctv'))
    parser.add_argument('--secrets-dir', type=Path, default=Path('/etc/cctv/secrets'))
    parser.add_argument('--gemini', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo on the target EC2')
    os.umask(0o077)
    prepare(args.data_dir, args.secrets_dir)
    if args.gemini:
        set_gemini(args.secrets_dir)
    print('Compose directories and secret files prepared; values not displayed')
