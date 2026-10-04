"""Guard a commit tag against overwrites; emit only validated registry metadata.

Used after Docker's login action. Credentials stay in Docker's credential store;
this helper never reads, prints, or forwards a token. No push/build occurs here.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

IMAGE = re.compile(r'[a-z0-9][a-z0-9_-]{1,63}/[a-z0-9][a-z0-9._-]{0,127}\Z')
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


def identity(image, commit):
    if not IMAGE.fullmatch(image) or not COMMIT.fullmatch(commit):
        raise ValueError('invalid_image_or_commit')
    return image + ':sha-' + commit


def inspect(image, commit, run=subprocess.run):
    tag = identity(image, commit)
    answer = run(['docker', 'buildx', 'imagetools', 'inspect', tag, '--format', '{{json .Manifest}}'],
                 capture_output=True, text=True, timeout=60, check=False)
    if answer.returncode:
        # Network/auth/rate-limit failures must not become permission to overwrite.
        error = answer.stderr.casefold()
        missing = ('manifest unknown' in error or ('not found' in error and tag.casefold() in error))
        if not missing or any(word in error for word in ('unauthorized', 'denied', 'timeout', '429')):
            raise ValueError('registry_lookup_failed_no_publish')
        return {'tag': tag, 'exists': False, 'digest': ''}
    try:
        digest = json.loads(answer.stdout)['digest']
    except (ValueError, TypeError, KeyError):
        raise ValueError('registry_digest_missing') from None
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ValueError('registry_digest_invalid')
    return {'tag': tag, 'exists': True, 'digest': digest}


def summary(image, commit, digest, path):
    tag = identity(image, commit)
    if not DIGEST.fullmatch(digest):
        raise ValueError('published_digest_invalid')
    with Path(path).open('a') as stream:
        stream.write('### Runpod image\n\n'
                     f'- Commit tag: `{tag}`\n'
                     f'- Immutable image reference: `{image}@{digest}`\n'
                     '- Platform: `linux/amd64`\n'
                     '- Pod creation/replacement and EC2 activation are manual.\n'
                     '- R2 model bytes and runtime credentials are excluded from this build context.\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'summary'))
    parser.add_argument('--image', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--digest')
    args = parser.parse_args()
    if args.action == 'inspect':
        result = inspect(args.image, args.commit)
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
            stream.write('tag=' + result['tag'] + '\n')
            stream.write('exists=' + str(result['exists']).lower() + '\n')
            stream.write('digest=' + result['digest'] + '\n')
        print(json.dumps(result))
    else:
        summary(args.image, args.commit, args.digest or '', os.environ['GITHUB_STEP_SUMMARY'])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        # Do not expose captured process output or credential-bearing environment.
        print(json.dumps({'status': 'registry_guard_stopped',
                          'reason': str(exc) if type(exc) is ValueError else 'registry_operation_failed'}))
        raise SystemExit(1)
