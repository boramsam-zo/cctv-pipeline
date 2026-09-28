"""Create private queue secret files; never show credentials. User-run only."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def private_write(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def credential_text(value):
    fields = [('AccessKeyId', 'aws_access_key_id'), ('SecretAccessKey', 'aws_secret_access_key')]
    if value.get('SessionToken'):
        fields.append(('SessionToken', 'aws_session_token'))
    lines = ['[default]']
    for source, target in fields:
        item = value.get(source)
        if not isinstance(item, str) or not item or not item.isascii() or any(c.isspace() for c in item):
            raise ValueError('Invalid exported credentials')
        lines.append(target + ' = ' + item)
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--aws-profile', help='Mac: export this existing AWS CLI profile into the private container secret')
    parser.add_argument('--gemini', action='store_true', help='Privately save a Gemini key; does not call any model')
    args = parser.parse_args()
    try:
        args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        args.directory.chmod(0o700)
        token_path = args.directory/'runpod_worker_token'
        if not token_path.exists() and not args.gemini:
            token = getpass.getpass('Runpod analysis API token (hidden): ').strip()
            if not token or not token.isascii() or any(c.isspace() for c in token):
                raise ValueError('Invalid token')
            private_write(token_path, token + '\n')
        for name in ('aws_credentials', 'aws_config'):
            path = args.directory/name
            if not path.exists():
                private_write(path, '')
        if args.aws_profile:
            result = subprocess.run(['aws', 'configure', 'export-credentials', '--profile', args.aws_profile,
                                     '--format', 'process'], capture_output=True, timeout=30, check=True)
            value = json.loads(result.stdout)
            private_write(args.directory/'aws_credentials', credential_text(value))
        if args.gemini:
            key = getpass.getpass('Gemini API key (hidden): ').strip()
            if not key or not key.isascii() or any(c.isspace() for c in key):
                raise ValueError('Invalid Gemini key')
            private_write(args.directory/'gemini_api_key', key + '\n')
        print(json.dumps({'status': 'private_files_prepared', 'values_displayed': False,
                          'aws_profile_exported': bool(args.aws_profile), 'gemini_key_saved': args.gemini, 'model_calls': 0}))
        return 0
    except Exception:
        print('Private setup failed; values were not displayed. Check token/profile locally.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
