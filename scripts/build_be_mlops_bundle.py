"""Prepare the complete EC2 source/resource update offline; never build or deploy."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
COMPONENTS = ('apps/be_worker', 'apps/scene_description', 'apps/rag_index',
              'apps/cctv_ui', 'apps/team_rag', 'infra/compose/mlflow')
SOURCE_SUFFIXES = {'.py', '.sql', '.json', '.md', '.txt', '.yaml', '.yml', '.css', '.sh', '.example'}
RESOURCE_FILES = ('apps/team_rag/data/chunks.jsonl', 'apps/team_rag/data/index.sqlite')
DEPLOYMENT_HELPERS = ('scripts/activate_mlops_session.py', 'scripts/build_mlops_be_image.py')
CPU_BASE = 'cctv-be-team-rag:small-cpu-v1'
CPU_BASE_ID = 'sha256:899b3c12f948784b821d02dcc27c38bd31952e961839a3014a2b4936d3ae82c5'
INSTRUCTIONS = """# EC2 MLOps 소스/검색 자원 준비 묶음

이 묶음은 준비본입니다. 기존 EC2 서비스·DB·spool·비밀 파일·모델 mount를 변경하거나 시작하지 않습니다.

기존 build_be_worker_bundle.py와 달리 현재 BE/MLflow/MLOps/session, 전체 관제 UI, scene, RAG 코드를 포함합니다. team_rag/data의 두 필수 검색 파일을 원본 바이트로 포함하고 해시를 기록했습니다. E5/R2 가중치, 영상, 저장 사건 snapshot, 자격 증명은 포함하지 않습니다.

배포 전 현재 API/worker/UI 경로와 이미지/메모리·DB·spool·E5 RO mount·실행창/claims를 기록하고 보존하세요. 기존 저장 사건과 보고는 다시 분석하지 않습니다. RAG 자원은 기존 같은 해시를 재사용하거나 새 release 경로에서 검증한 뒤 연결합니다. 기존 자원을 덮어쓰지 않습니다.

새 Pod 연결은 포함된 scripts/activate_mlops_session.py로 수동 진행합니다. 기존 /input mount는 읽기 전용이므로 원본 target config를 보존하고, 같은 파일을 spool의 새 model-target-config.json에 복사한 뒤 API와 worker 양쪽의 BE_TARGET_CONFIG를 모두 /spool/model-target-config.json으로 지정해야 합니다. 두 서비스가 같은 호스트 spool 파일을 읽도록 mount 경로를 확인하세요. helper가 원본 /input 파일을 쓰도록 경로를 바꾸거나 읽기 전용 보호를 풀지 않습니다. 실제 API health 확인·DB session 활성화는 명시적인 수동 실행 단계입니다.

CPU 이미지의 기준은 cctv-be-team-rag:small-cpu-v1, 실제 기존 로컬 image ID sha256:899b3c12f948784b821d02dcc27c38bd31952e961839a3014a2b4936d3ae82c5 입니다. 이 ID는 registry digest가 아닙니다. 현재 EC2에서 이 태그/ID 일치를 먼저 확인하고 --pull=false를 사용해야 합니다. MLops.Dockerfile은 기존 E5 CPU 패키지를 모두 constraints로 고정하고 mlflow-skinny만 추가합니다. dependency 충돌이면 실패하며 기존 런타임 버전을 바꿔 해결하지 않습니다. 이미지 기본 명령은 패키지 검증 후 종료하고 서버나 모델을 실행하지 않습니다.

기존 Queue.Dockerfile로 실제 CPU worker를 다시 만들면 E5 의존성이 빠질 수 있습니다. 기존 CPU 이미지에서 파생하는 MLops.Dockerfile을 별도로 검토해야 합니다. 실제 build/apply helper는 이 묶음이 실행하지 않습니다.

묶음 루트에서 다음은 오프라인 계획 출력만 합니다.

```bash
python3 -B scripts/build_mlops_be_image.py --output-tag cctv-be-team-rag:mlops-v1
```

현재 EC2 자원/기존 서비스를 별도로 확인한 뒤 실행을 선택할 경우에만 같은 명령에 `--build --receipt /path/to/new-receipt.json`을 추가합니다. helper는 기존 base ID 일치·출력 tag 미존재를 검사하고 별도 local alias에 고정한 다음 빌드합니다. 성공 뒤 네트워크/마운트/GPU 없는 일회성 컨테이너에서 패키지 메타데이터만 확인합니다. 서비스 배포/재시작과 MLflow 서버 기동은 수행하지 않습니다.

MLflow 서버는 현재 EC2 메모리 여유가 충분하다는 근거가 없어 기동하지 않았습니다. Compose 예시는 검토용이며 자동 적용하지 않습니다. DB schema migration/새 Pod/API/session 활성화도 별도 검증 단계입니다. 모형 정확도와 실제 GPU/새 VLM 포함 종단 실행은 이 소스 묶음만으로 검증되지 않습니다.
"""


def digest(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def plain(path):
    path = Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('bundle_symlink_or_parent_path')
    return path


def selected_files(root=ROOT):
    root = plain(root)
    names = set(RESOURCE_FILES)
    for directory in COMPONENTS:
        for path in plain(root / directory).iterdir():
            if path.is_symlink():
                raise ValueError('bundle_source_symlink')
            if path.is_file() and (path.suffix in SOURCE_SUFFIXES or path.name in {'Dockerfile', '.dockerignore'}
                                   or path.name.endswith(('.Dockerfile', '.Dockerfile.dockerignore'))):
                if path.name == 'secrets.toml' or path.name.startswith('.env'):
                    continue
                names.add(path.relative_to(root).as_posix())
    archives = root / 'apps/be_worker/model_releases'
    if archives.exists():
        plain(archives)
        for path in archives.iterdir():
            if path.is_symlink() or not path.is_file() or not re.fullmatch(r'[0-9a-f]{64}\.json', path.name):
                raise ValueError('release_archive_roster_invalid')
            names.add(path.relative_to(root).as_posix())
    for name in DEPLOYMENT_HELPERS:
        if (root / name).exists():
            names.add(name)
    if (root / '.streamlit/config.toml').is_file():
        names.add('.streamlit/config.toml')
    return sorted(names)


def payloads(root=ROOT):
    from scripts.prepare_git_publication import excluded, secrets
    result = {}
    for name in selected_files(root):
        path = plain(root / name)
        if not path.is_file():
            raise ValueError('bundle_source_missing:' + name)
        if name not in RESOURCE_FILES and excluded(Path(name)):
            raise ValueError('bundle_private_or_unsupported_file:' + name)
        result[name] = path.read_bytes()
        if name not in RESOURCE_FILES:
            categories = secrets(result[name].decode('utf-8'), name)
            if categories:
                raise ValueError('bundle_secret_scan_rejected:' + name + ':' + ','.join(categories))
        if name.endswith('.py'):
            compile(result[name], name, 'exec')
    result['EC2_MLOPS_PREPARATION.md'] = INSTRUCTIONS.encode()
    return result


def build(output, root=ROOT):
    output = plain(output)
    if output.exists() or not output.parent.is_dir():
        raise ValueError('bundle_output_must_be_new_with_existing_parent')
    files = payloads(root)
    # Validate local import completeness without executing app modules.
    from scripts.build_runpod_service_bundle import verify_local_imports
    verify_local_imports(files)
    for name, raw in files.items():
        if name != 'EC2_MLOPS_PREPARATION.md' and plain(root / name).read_bytes() != raw:
            raise ValueError('bundle_source_changed_before_write:' + name)
    manifest = {'schema_version': 'ec2-mlops-preparation-v1',
                'files': {name: digest(data) for name, data in sorted(files.items())},
                'expected_existing_cpu_image': CPU_BASE, 'expected_existing_cpu_image_id': CPU_BASE_ID,
                'rag_resources_included': list(RESOURCE_FILES), 'model_weights_included': False,
                'secrets_included': False, 'service_changes': False, 'docker_builds': 0,
                'mlflow_server_started': False, 'database_writes': 0, 'model_calls': 0}
    files['ec2-mlops-manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    output.mkdir(mode=0o700)
    tree = output / 'release'
    tree.mkdir(mode=0o700)
    for name, data in files.items():
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    archive = output / 'cctv-ec2-mlops.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 4, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or set(bundle.namelist()) != set(files):
            raise ValueError('bundle_archive_roster_or_crc')
        for name, data in files.items():
            if bundle.read(name) != data or (tree / name).read_bytes() != data:
                raise ValueError('bundle_readback_mismatch')
    receipt = {'status': 'ec2_mlops_bundle_prepared_not_deployed', 'files': len(files),
               'archive': archive.name, **digest(archive.read_bytes()),
               'expected_existing_cpu_image_id': CPU_BASE_ID,
               'source_bytes_preserved': True, 'rag_resources_included': list(RESOURCE_FILES),
               'docker_builds': 0, 'remote_changes': 0, 'model_calls': 0}
    (output / 'preparation.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))
