"""Create a reviewed source-only publication tree; never alter Git or source files.

Only explicit source/document roots and suffixes are considered. Credentials,
environments, media, model bytes, operational data and generated artifacts are
not read. A separate manifest records copies and path anonymization by digest.
"""
import argparse
from collections import Counter
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ('apps', 'src', 'configs', 'infra', 'scripts', 'tests', 'docs', '.github')
ROOT_FILES = ('.gitignore', '.python-version', 'pyproject.toml', 'uv.lock',
              'requirements-pipeline-dev.txt', '.streamlit/config.toml')
SUFFIXES = {'.py', '.sh', '.md', '.json', '.yaml', '.yml', '.toml', '.sql', '.txt',
            '.tf', '.hcl', '.css', '.js', '.html', '.example'}
FILENAMES = {'Dockerfile', '.dockerignore'}
BLOCKED_DIRS = {'artifacts', 'data', 'outputs', 'output', 'private', 'secrets',
                'model_cache', 'model-cache', '__pycache__', 'node_modules',
                '.aws', '.git', '.agents', '.claude', '.codex', '.idea', '.terraform'}
BLOCKED_SUFFIXES = {'.pt', '.pth', '.ckpt', '.safetensors', '.onnx', '.bin', '.pkl',
                    '.mp4', '.mov', '.avi', '.mkv', '.jpg', '.jpeg', '.png', '.gif',
                    '.webp', '.pdf', '.zip', '.gz', '.tar', '.sqlite', '.db', '.pem',
                    '.key', '.tfstate', '.tfplan', '.tfvars', '.ipynb', '.pyc'}
SECRET_PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'),
    'aws_access_key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'openai_key': re.compile(r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}\b'),
    'google_key': re.compile(r'\bAIza[A-Za-z0-9_-]{35}\b'),
    'embedded_basic_auth': re.compile(r'https?://[^\s/:@]+:[^\s/@]+@'),
    'aws_secret_literal': re.compile(r'''(?i)(?:aws_secret_access_key|AWS_SECRET_ACCESS_KEY)["']?\s*[:=]\s*["']([A-Za-z0-9/+=]{40})["']'''),
}
PRIVATE_PATH = re.compile(r'/Users/[A-Za-z0-9][A-Za-z0-9_.-]*|/Volumes/[A-Za-z0-9][^\s/"\'`<>]*')
PUBLICATION_NOTICE = """## 이 Git 게시본의 재현 범위

이 저장소는 실행 코드·설정 예시·공유 문서를 선별한 복사본입니다. 원본 프로젝트와 기존 Git main 이력은 보존하며, 이 디렉터리 생성은 commit/push 또는 배포가 아닙니다.

- `.github/workflows/runpod-image.yml`의 CPU 계약/서비스/패키징 검사를 checkout에서 실행할 수 있습니다. 실제 GPU 추론·학습·EC2 배포는 CI 검사가 수행하지 않습니다.
- `.github/workflows/source-checks.yml`은 BE 보고 재사용/복구·공개 조회/시연 경계·관제 UI·MLflow 추적·문서 관련성의 합성 CPU 검사를 실행합니다. BE/UI/보고 코드만 바뀌면 기존 Runpod 이미지를 다시 게시하지 않습니다.
- 전체 과거 테스트는 Git에서 제외한 `artifacts/`와 평가 원본을 참조합니다. 따라서 이 checkout만으로 과거 전체 테스트나 저장 관제 화면을 모두 재현할 수 있다고 주장하지 않습니다.
- 원본 영상·저장 사건 snapshot·모델 가중치·비밀 설정·개인 가상환경·실행 결과는 제외했습니다. 개인 절대경로는 이 게시 복사본에서만 프로젝트 상대경로 또는 중립 경로로 바꿨습니다.
- 문서의 실제 EC2 식별자·공인 IP·Runpod 주소도 게시 복사본에서만 예시로 바꿨습니다. 운영 명령에는 사용 중인 서버 식별자를 넣어야 하며 원본 운영 문서는 별도로 보존합니다.
- Runpod 이미지는 고정된 기존 P6 이미지의 모델/의존성을 재사용합니다. 신규 R2 모델은 Git/이미지 context에 넣지 않고 승인한 비공개 S3에서 Pod로 준비합니다.
- `apps/team_rag/data/chunks.jsonl`·`index.sqlite`는 보고서 검색의 필수 자원입니다. Git 게시본에서는 제외했지만 EC2 배포 묶음에는 같은 경로/바이트로 포함하고 기존 EC2 자원·E5 모델 mount를 보존해야 합니다. 생략을 빈 검색 결과나 보고 성공으로 처리하지 마세요. 공개 재배포 권리를 확인한 것으로 간주하지 않습니다.
- 기존 저장 결과를 조회할 때는 기존 EC2 API/DB 또는 승인된 로컬 snapshot을 연결하세요. 제외된 데이터를 다운로드하거나 분석을 자동 실행해 대체하지 않습니다.

관련 CPU 검사:

```bash
python -m pip install -r apps/runpod_image/requirements-ci.txt -r scripts/requirements-viewer.txt jsonschema==4.25.1
python -B scripts/ci_source_checks.py
python -B -m unittest discover -s tests/runpod_image -p 'test_mlops*.py' -v
python -B -m unittest discover -s tests/be_worker -p 'test_mlops*.py' -v
python -B -m unittest tests.be_worker.test_mlflow_tracking tests.be_worker.test_register_mlops_models -v
python -B scripts/prepare_runpod_ci.py --output /path/to/new-build-directory
```

마지막 경로의 부모 디렉터리는 존재해야 하며 결과 디렉터리는 새 이름을 사용합니다. 이 준비 명령은 Docker 빌드/게시를 수행하지 않습니다.

---

"""


def fingerprint(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def plain(path):
    path = Path(path).absolute()
    if '..' in path.parts or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('symlink_or_parent_path_rejected')
    return path


def excluded(path):
    parts = path.parts
    if any(part in BLOCKED_DIRS or part.startswith('.venv') for part in parts):
        return 'private_generated_or_operational_directory'
    name = path.name.lower()
    if path.suffix.lower() in BLOCKED_SUFFIXES or name.endswith(('.tfstate.backup', '.tfvars.json')):
        return 'binary_media_weight_or_private_state'
    if (name == '.env' or (name.startswith('.env.') and name != '.env.example')
            or name in {'credentials', 'config.local.json', 'id_rsa', 'id_ed25519', 'secrets.toml'}):
        return 'private_configuration'
    if (path.suffix.lower() not in SUFFIXES and path.name not in FILENAMES
            and not path.name.endswith(('.Dockerfile', '.Dockerfile.dockerignore'))):
        return 'not_source_allowlisted'
    return None


def candidates(root):
    counts = Counter()
    selected = set()
    for name in ROOT_FILES:
        if (root / name).exists() or (root / name).is_symlink():
            selected.add(name)
    selected.update(path.name for path in root.glob('*.md'))
    for base in ROOTS:
        start = root / base
        if not start.exists():
            continue
        plain(start)
        for folder, directories, files in os.walk(start, followlinks=False):
            current = Path(folder)
            for name in list(directories):
                path = current / name
                relative = path.relative_to(root)
                if any(part in BLOCKED_DIRS or part.startswith('.venv') for part in relative.parts):
                    directories.remove(name)
                    counts['excluded_directories'] += 1
                elif path.is_symlink():
                    raise ValueError('source_symlink_rejected:' + relative.as_posix())
            for name in files:
                path = current / name
                relative = path.relative_to(root)
                reason = excluded(relative)
                if reason:
                    counts[reason] += 1
                    continue
                if path.is_symlink():
                    raise ValueError('source_symlink_rejected:' + relative.as_posix())
                selected.add(relative.as_posix())
    return sorted(selected), dict(sorted(counts.items()))


def sanitize(text, root):
    # Replacement is applied only to the publication copy, never its source.
    text, project = re.subn(re.escape(str(root)), '.', text)
    text, homes = re.subn(r'/Users/[A-Za-z0-9][A-Za-z0-9_.-]*', '/path/to/user', text)
    text, volumes = re.subn(r'/Volumes/[A-Za-z0-9][^\s/"\'`<>]*', '/path/to/data', text)
    return text, project + homes + volumes


def sanitize_operational_docs(text, name):
    """Keep operational destinations out of shared docs, without editing code."""
    if not name.endswith('.md'):
        return text, 0
    count = 0
    def address(match):
        nonlocal count
        try:
            public = ipaddress.ip_address(match.group()).is_global
        except ValueError:
            public = False
        if public:
            count += 1
            return '203.0.113.10'
        return match.group()
    text = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', address, text)
    text, instances = re.subn(r'\bi-[a-f0-9]{8,17}\b', 'YOUR_EC2_INSTANCE_ID', text)
    # Replace a concrete Pod ID wherever this document also identifies its
    # endpoint. Template/example IDs remain documentation, not live targets.
    pods = set(re.findall(r'https?://([a-z0-9]{13,14})-\d+\.proxy\.runpod\.net', text))
    pods -= {'syntheticpod', 'yourpodid', 'examplepod'}
    for pod in sorted(pods):
        text, replaced = re.subn(r'(?<![A-Za-z0-9])' + re.escape(pod) + r'(?![A-Za-z0-9])',
                                'YOUR_RUNPOD_ID', text)
        count += replaced
    return text, count + instances


def secrets(text, name=''):
    result = []
    for kind, pattern in SECRET_PATTERNS.items():
        matches = list(pattern.finditer(text))
        if kind == 'embedded_basic_auth' and name.startswith('tests/'):
            # Two existing negative URL-validation tests use short placeholder
            # credentials at reserved example.com. This is not a general bypass
            # for test files, real hosts, or credential-shaped long strings.
            remaining = []
            for match in matches:
                tail = re.match(r'''[^\s"'<>]+''', text[match.start():])
                try:
                    url = urlsplit(tail.group() if tail else '')
                    synthetic = (url.hostname == 'example.com'
                                 and re.fullmatch('[a-z]{3,6}', url.username or '') is not None
                                 and re.fullmatch('[a-z]{3,6}', url.password or '') is not None)
                except ValueError:
                    synthetic = False
                if not synthetic:
                    remaining.append(match)
            matches = remaining
        if matches:
            result.append(kind)
    return result


def protected_sources(root):
    path = root / 'apps/be_worker/mlops-20261004-v1.json'
    if not path.exists():
        return set()
    catalog = json.loads(path.read_text())
    names = set()
    for name in catalog['code_files']:
        names.add(name.removeprefix('service/') if name.startswith('service/') else
                  'apps/runpod_image/' + name)
    return names


def prepare(output, root=ROOT):
    root, output = plain(root), plain(output)
    if output.exists():
        raise ValueError('existing_output_preserved')
    if not output.parent.is_dir():
        raise ValueError('output_parent_missing')
    names, exclusions = candidates(root)
    protected = protected_sources(root)
    payloads, records, findings = {}, {}, []
    for name in names:
        source = plain(root / name)
        if not source.is_file():
            raise ValueError('source_not_regular:' + name)
        before = source.stat()
        raw = source.read_bytes()
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('source_changed_during_read:' + name)
        try:
            original = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('non_utf8_source_rejected:' + name) from None
        cleaned, replacements = sanitize(original, root)
        cleaned, operational_replacements = sanitize_operational_docs(cleaned, name)
        if replacements and name in protected:
            raise ValueError('catalog_source_requires_review:' + name)
        if name == 'README.md':
            cleaned = PUBLICATION_NOTICE + cleaned
        categories = secrets(cleaned, name)
        if categories:
            findings.append({'path': name, 'categories': categories})
        if PRIVATE_PATH.search(cleaned):
            raise ValueError('private_path_remaining:' + name)
        payloads[name] = cleaned.encode('utf-8')
        records[name] = {'source': fingerprint(raw), 'publication': fingerprint(payloads[name]),
                         'private_path_replacements': replacements,
                         'operational_identifier_replacements': operational_replacements,
                         'mode': '0755' if os.access(source, os.X_OK) and source.suffix == '.sh' else '0644'}
    if findings:
        # Names/categories only. Never return matched text or secret values.
        raise ValueError('secret_scan_rejected:' + json.dumps(findings, separators=(',', ':')))
    for name, record in records.items():
        if fingerprint(plain(root / name).read_bytes()) != record['source']:
            raise ValueError('source_changed_before_publication:' + name)
    output.mkdir(mode=0o700)
    tree = output / 'tree'
    tree.mkdir(mode=0o700)
    for name, data in payloads.items():
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(int(records[name]['mode'], 8))
    if {path.relative_to(tree).as_posix() for path in tree.rglob('*') if path.is_file()} != set(payloads):
        raise ValueError('publication_roster_mismatch')
    for name, data in payloads.items():
        if (tree / name).read_bytes() != data:
            raise ValueError('publication_readback_mismatch:' + name)
    manifest = {'schema_version': 'cctv-git-publication-v1', 'status': 'prepared_not_committed_or_pushed',
                'files': records, 'file_count': len(records), 'bytes': sum(map(len, payloads.values())),
                'exclusions': exclusions, 'secret_scan_findings': 0,
                'scanner_synthetic_exception': 'negative URL tests only: short lowercase placeholders at reserved example.com',
                'changed_copy_count': sum(row['source'] != row['publication'] for row in records.values()),
                'source_files_modified': False, 'git_operations': 0, 'remote_operations': 0,
                'operational_rag_data_in_git': False, 'full_historical_tests_reproducible': False}
    (output / 'publication-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return {key: value for key, value in manifest.items() if key != 'files'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.output), ensure_ascii=False, indent=2))
    except ValueError as exc:
        print(json.dumps({'status': 'publication_preparation_stopped', 'reason': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
