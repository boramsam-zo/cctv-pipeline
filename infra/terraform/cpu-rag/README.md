# CPU EC2 + Docker Compose — SSH 시험 배포

## 현재 진행 기준 — 2026-09-29: 기존 EC2에서 BE 운영

사용자는 Mac 로컬 시험에서 EC2 배포 기준으로 전환했다. EC2는 실행할 서버, Docker Compose는 그 서버 안의 BE·PostgreSQL/pgvector 컨테이너 실행 도구, Terraform은 AWS 자원/설정 관리 도구다. Mac Docker는 기존 개발/검증 환경이며 EC2 배포의 필수 선행 단계가 아니다. 클라우드 변경·배포 명령은 사용자가 CLI로 실행한다.

이번 AWS 읽기 조회에서 기존 `i-02017b92281737f53`은 **running**, t3.small, 공인 IP `3.36.76.85`, 인스턴스 프로필 미연결로 확인됐다. 역할에 대응하는 기존 프로필 이름은 `hanwha-cctv-ec2-role`로 조회됐다. 이번에 서버를 시작하거나 역할을 연결한 것은 아니다. 실제 IAM 정책의 S3 허용 여부와 컨테이너 인증 성공은 별도 확인 대상이다. 근거: `artifacts/infra/cpu-rag-ec2-resume-20260929-01/inspection.json`.

현재 Terraform은 EC2/보안그룹/키/프로필 연결을 관리하고 부팅 시 Docker와 DB를 준비한다. BE 코드·Runpod 토큰·접속 설정·큐/spool은 자동 배포하지 않는다. 전원 상태 리소스가 없으므로 이미 실행 중인 이 서버를 위해 신규 apply할 필요가 없다. 이후 기존 프로필 연결 등 필요한 구성 변경만 기존 state로 plan/apply한다. public IP는 재시작 후 바뀔 수 있으므로 조회값을 사용한다.

### 기존 EC2 접속·DB 확인 — 이후 재개에도 사용하는 절차

**Mac Terminal:**

```bash
cd . &&
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh
```

도우미는 현재 공인 IP로 연결하고 같은 인스턴스의 이전 검증 host key를 HostKeyAlias로 유지한다. StrictHostKeyChecking을 해제하지 않는다. 다음에 IP가 바뀌면 AWS에서 같은 인스턴스인지 확인한 뒤 CCTV_EC2_HOST로 지정할 수 있다. 키 불일치나 접속 시간 초과면 오류부터 확인한다. 이전 스크립트는 위 점검 artifacts에 보존했다.

프롬프트가 `ubuntu@ip-...:~$`이면 EC2에 접속된 상태다. 이미 이 프롬프트가 보이면 Mac 접속 단계는 생략한다. 아래 DB 명령은 EC2에서 실행하며 `exit`하면 Mac으로 돌아온다.

**접속한 EC2 Terminal:**

```bash
sudo docker compose -f /opt/cctv-stack/compose.yaml ps
```

2026-09-29 사용자 출력에서 `cctv-db-1`, `Up 17 hours (healthy)`, `5432/tcp`를 확인했다. **기존 EC2의 DB 상태 확인은 완료**이며 같은 확인을 반복할 필요는 없다. `5432/tcp`는 이 구성의 컨테이너 내부 포트다. BE 배포·Runpod 연결·문서 데이터 확인은 별도다. 에이전트가 EC2에 직접 접속해 검증한 결과는 아니다.

다음은 **같은 EC2 터미널**에서 기존 테이블만 조회하는 명령이다. 데이터를 바꾸거나 모델을 호출하지 않는다.

```bash
sudo docker compose -f /opt/cctv-stack/compose.yaml exec -T --user postgres db \
  psql -X -v ON_ERROR_STOP=1 -d cctv -c \
  "SELECT to_regclass('cctv_jobs.queue') AS be_queue,
          to_regclass('cctv_scene.descriptions') AS scene_descriptions,
          to_regclass('cctv_rag.chunks') AS rag_chunks;"
```

테이블 이름이 표시된 열은 해당 테이블이 존재하고, 빈칸은 아직 없다는 뜻이다. 테이블 존재만으로 데이터 행 수나 최신 스키마까지 확인한 것은 아니다.

그다음 작업 순서는 기존 DB 상태 확인→Terraform의 기존 IAM 프로필 연결 계획 검토→EC2용 BE 코드/경로/비밀값 배포→EC2에서 Runpod 인증 health→필요한 영상 처리다. IMDS hop limit는 현행1이므로 역할을 쓰는 Docker 컨테이너의 메타데이터 접근도 배포 단계에서 검증해야 한다. 현재 연결 성공은 Mac에서 얻은 결과이며 EC2에서 다시 확인한다.

Mac의 기존 BE 대기열·spool은 EC2 DB에 자동으로 들어가지 않는다. 기존 EC2의 법령 벡터/비밀번호를 보존하며, 기존 사건을 이어가려면 해당 작업 상태와 spool을 함께 옮기는 절차를 준비한다. 완료한 로컬 사건은 설명·DB 저장까지 마쳤으며 이전 영상 GPU 재분석이 이관의 선행 조건은 아니다. EC2 DB의 healthy는 사용자 출력으로 확인했지만 BE 앱·테이블 조회 결과는 아직 수신하지 않았다.

## 단계1 적용 — 2026-09-30

최신 순서: 현재 단계1은 DB/RAG 조회만 확인됐고 BE테이블 준비는 남아 있다. 아래 명령은 **사용자가 실행**한다. 코드와 기존 두 스키마만 준비하며 이미지 빌드·비밀 설정·GPU/Gemini 호출은 하지 않는다. 단계2 연결검사는 성공 출력 확인 뒤 진행한다. 이전 배포 설명보다 이 절의 릴리스 경로를 우선한다.

### 1. Mac 터미널 — 코드 ZIP 전송

예상 결과: 출력 없이 종료코드0, EC2 사용자 홈에 새ZIP생성. 동일파일명이 이미 있으면 덮어쓰지 않고중단하므로전송을반복하지말고다음해시검증으로확인한다.

```bash
cd .
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh \
  'umask 077; set -C; cat > /home/ubuntu/cctv-be-stage1-20260930-01.zip' \
  < artifacts/be-worker/ec2-stage1-20260930-01/cctv-be-worker.zip
```

EC2 터미널이 아직 없다면 **Mac**에서 기존 접속 도우미를 실행한다. 예상 결과는 기존 EC2의 셸 프롬프트다.

```bash
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh
```

### 2. EC2 터미널 — 검증된 코드 설치와 스키마 적용

예상 결과: `Bundle verified: 24 files` 뒤 JSON. `status:passed`, `transaction_committed:true`, `rag_unchanged:true`, `rag_chunk_count:2`, `required_columns_and_privileges:true`가 필요하다. 큐/설명 행수는 신규환경이면0이며 이미 존재하는 기록은보존한다. 새로들어온법령이있어2행과달라지면삭제하지말고출력을확인한다. 두스키마와검증은한트랜잭션이며실패하면commit하지않는다. 코드설치파일은진단/재개를위해남는다. 출력은그대로전달하되키내용은보내지않는다.

```bash
sudo python3 - <<'PY'
import hashlib
from pathlib import Path
import subprocess
import zipfile

archive = Path('/home/ubuntu/cctv-be-stage1-20260930-01.zip')
release = Path('/opt/cctv-be-stage1-20260930-01')
expected = '9fae23f6cb1282177681e2ad681aa3ce4161376ed755223946cdef2f77b43d1a'
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise SystemExit('ZIP hash mismatch; stop without installation')
if not release.exists():
    release.mkdir(mode=0o755)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(release)
subprocess.run(['python3', str(release / 'apps/be_worker/verify_bundle.py')], check=True)
subprocess.run(['python3', str(release / 'apps/be_worker/prepare_stage1.py'),
                '--compose', '/opt/cctv-stack/compose.yaml'], check=True)
PY
```

기존 성공 영상을 다시 제출할 필요는 없다. 이번 단계에 Runpod에서 실행할 명령은 없다.

## 단계2 준비 — 환경 경로

사용자 출력으로 단계1은 통과했다. **ZIP전송/압축해제/스키마적용은 반복하지 않는다.** 실제 릴리스는 `/opt/cctv-be-stage1-20260930-01`이다. 아래는 단계2의 환경 준비이며 Runpod/S3 연결 통과와는 별개다.

**실행 위치: EC2 터미널.** 예상 결과: `be_environment_ready`. 기존 환경 파일이 다른 내용이면 덮어쓰지 않고 중단한다. 비밀값 입력·이미지 다운로드·GPU/Gemini 호출은 없다. 완료 후 토큰/현재주소·인스턴스역할과 이미지 준비를 이어간다.

```bash
sudo python3 - <<'PY'
import os
from pathlib import Path

release = Path('/opt/cctv-be-stage1-20260930-01')
if not (release / 'apps/be_worker/queue.compose.yaml').is_file():
    raise SystemExit('stage1_release_missing')
values = {
    'COMPOSE_PROJECT_NAME': 'cctv',
    'CCTV_COMPOSE_FILE': '/opt/cctv-stack/compose.yaml',
    'CCTV_DATA_DIR': '/srv/cctv',
    'CCTV_SECRETS_DIR': '/etc/cctv/secrets',
    'BE_APP_DIR': str(release / 'apps/be_worker'),
    'BE_SCENE_APP_DIR': str(release / 'apps/scene_description'),
    'BE_INPUT_DIR': '/srv/cctv/be/input',
    'BE_SPOOL_DIR': '/srv/cctv/be/spool',
    'BE_SECRETS_DIR': '/etc/cctv/be-secrets',
    'BE_CONTAINER_UID': '10001',
    'BE_CONTAINER_GID': '10001',
    'BE_TARGET_CONFIG': '/input/config-ec2-v1.json',
}
content = ''.join(f'export {key}={value}\n' for key, value in values.items())
env = Path('/etc/cctv/be-worker-v1.env')
if env.exists() and env.read_text() != content:
    raise SystemExit('existing_environment_differs; original preserved')
Path('/srv/cctv/be').mkdir(mode=0o755, parents=True, exist_ok=True)
for key in ('BE_INPUT_DIR', 'BE_SPOOL_DIR', 'BE_SECRETS_DIR'):
    directory = Path(values[key])
    if not directory.exists():
        directory.mkdir(mode=0o700)
        os.chown(directory, 10001, 10001)
if not env.exists():
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
print('be_environment_ready; model_calls=0; database_writes=0')
PY
```

## 단계2 준비 — 기존 분석 토큰 전달

환경 경로 준비 완료 출력 수신. 다음은 Mac에서 사용한 분석 API 토큰을 재사용한다. Runpod 계정 API key와 다르며 토큰 내용은 채팅에 붙이지 않는다. 이 과정은 파일 전달/설치만 하고 모델을 호출하지 않는다.

**Mac 터미널:** 예상 출력 `token_uploaded`. 기존 동명 파일은 덮어쓰지 않는다. `File exists`면 다시 전송하지 말고 다음 EC2 설치에서 기존 파일을 확인한다.

```bash
cd .
test -s artifacts/be-worker/queue-fresh-v1/secrets/runpod_worker_token &&
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh \
  'umask 077; set -C; cat > /home/ubuntu/runpod-worker-token-20260930-01 && echo token_uploaded' \
  < artifacts/be-worker/queue-fresh-v1/secrets/runpod_worker_token
```

**EC2 터미널:** 예상 출력 `runpod_token_installed`. 기존 BE 토큰이 다르면 덮어쓰지 않고 중단한다. 토큰 파일 설치는 인증 성공을 의미하지 않으며 컨테이너에서 후속 검증한다.

```bash
sudo python3 - <<'PY'
import os
from pathlib import Path

source = Path('/home/ubuntu/runpod-worker-token-20260930-01')
target = Path('/etc/cctv/be-secrets/runpod_worker_token')
data = source.read_bytes()
if not data.strip() or len(data) > 4096:
    raise SystemExit('invalid_token_file; contents not displayed')
if target.exists():
    if target.read_bytes() != data:
        raise SystemExit('existing_token_differs; original preserved')
else:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
os.chown(target, 10001, 10001)
os.chmod(target, 0o600)
print('runpod_token_installed; model_calls=0')
PY
```

## 단계2 준비 — 연결 설정·BE 이미지

토큰 설치 출력 `runpod_token_installed; model_calls=0`까지 확인한 뒤 진행한다. **실행 위치: EC2 터미널.** 기존 환경 파일을 사용하고 없는 AWS 설정 파일만 빈 파일로 준비한다. AWS 키를 새로 넣지 않으며 실제 인스턴스 역할/S3 권한은 후속 검사한다.

아래에서 URL 입력을 요청하면 현재 Runpod의 **Port 8000 HTTPS 서비스 주소**를 입력한다. 기존 config가 있으면 그대로 사용한다. 이미지 빌드는 Python 기반 이미지와 의존성을 다운로드한다. GPU/Gemini 호출과 작업 접수는 없으며 마지막 `list`는 기존 DB 작업 조회다.

예상 결과: `private_files_prepared` → 새 설정이면 `configured` → 이미지 빌드 성공 → 작업 목록 `[]`(현재 빈 큐 기준). 기존 작업이 보이면 삭제하지 않는다. 이것은 이미지/DB 조회 확인이며 Runpod/S3 연결 통과는 아니다. 오류가 나면 이 블록은 중단되며 해당 출력을 확인한다.

```bash
sudo -i
```

이후 같은 **EC2 root 셸**에 다음 블록을 붙인다.

```bash
(
  set -e
  source /etc/cctv/be-worker-v1.env
  cd "$BE_APP_DIR/../.."
  test -s "$BE_SECRETS_DIR/runpod_worker_token"
  python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR"
  chown 10001:10001 "$BE_SECRETS_DIR"/{runpod_worker_token,aws_credentials,aws_config}
  chmod 600 "$BE_SECRETS_DIR"/{runpod_worker_token,aws_credentials,aws_config}
  if [ ! -e "$BE_INPUT_DIR/config-ec2-v1.json" ]; then
    python3 apps/be_worker/worker.py configure --output "$BE_INPUT_DIR/config-ec2-v1.json"
  fi
  chown 10001:10001 "$BE_INPUT_DIR/config-ec2-v1.json"
  chmod 600 "$BE_INPUT_DIR/config-ec2-v1.json"
  bash apps/be_worker/queue.sh build
  bash apps/be_worker/queue.sh list
)
```

## 단계2 검사 — 새 Pod 인증 연결

사용자 BE 이미지 빌드·작업목록[] 성공 출력 수신. 새Pod의 파일·서버·모델·토큰은 미확인이다. **EC2 터미널**에서 아래를 실행한다. 예상 결과는 `connected`, `storage:s3`, `jobs_submitted:0`이다. 실패하면 안전한 오류 코드만 출력한다. GET1회이며 영상 제출/모델 호출은 없다. 성공해도 실제 GPU분석이나 S3읽기/해시검증 완료로 표시하지 않는다.

```bash
sudo bash -c '
  set -e
  source /etc/cctv/be-worker-v1.env
  docker compose -f "$CCTV_COMPOSE_FILE" -f "$BE_APP_DIR/queue.compose.yaml" --profile be-tools \
    run --rm -T --no-deps --entrypoint python be-queue -
' <<'PY'
import json
from pathlib import Path
from apps.be_worker.common import WorkerError, config_checked, read_json
from apps.be_worker.runpod_api import RunpodAPI
try:
    config = config_checked(read_json(Path('/input/config-ec2-v1.json')))
    if config['runpod_url'].rstrip('/') != 'https://ufpzz3c31eelb5-8000.proxy.runpod.net':
        raise WorkerError('config_does_not_match_new_pod')
    token = Path('/run/secrets/runpod_worker_token').read_text().strip()
    health = RunpodAPI(config['runpod_url'], token).health()
except (WorkerError, OSError) as exc:
    code = exc.code if isinstance(exc, WorkerError) else 'config_or_secret_file_unreadable'
    print(json.dumps({'status': 'failed', 'error': code, 'jobs_submitted': 0}))
    raise SystemExit(1)
print(json.dumps({'status': 'connected', **health, 'jobs_submitted': 0}))
PY
```

## 단계2 검사 — 기존 S3 근거 읽기·해시 검증

EC2의 새Pod 인증 connected 출력 수신 후 실행한다. **실행 위치: EC2 터미널.** 기존 완료 사건의 manifest·evidence.json·클립·프레임만 다운로드한다(기존 미디어 합계659,415bytes). GPU/Gemini호출·S3쓰기·DB쓰기는 없다. 실제BE의UID/네트워크/비밀마운트와기존 scene.fetch 검증을 재사용한다. 새UUID의spool검사폴더에결과를보존하며실패자료도삭제하지않는다.

예상 결과: `s3_evidence_verified`, `credential_source:iam-role`, `media_bytes_verified:true`, `jobs_submitted:0`. `credentials`단계실패는인스턴스역할/컨테이너메타데이터접근을확인할근거이며임의로AWS키를복사하지않는다. S3실패는오류코드에따라권한/객체를확인한다.

```bash
sudo bash -c '
  set -e
  source /etc/cctv/be-worker-v1.env
  docker compose -f "$CCTV_COMPOSE_FILE" -f "$BE_APP_DIR/queue.compose.yaml" --profile be-tools \
    run --rm -T --no-deps --entrypoint python be-queue -
' <<'PY'
import json
import uuid
from pathlib import Path
import boto3
from apps.scene_description import scene

stage = 'credentials'
try:
    credentials = boto3.Session().get_credentials()
    if credentials is None or credentials.method != 'iam-role':
        raise RuntimeError('instance_role_credentials_not_ready')
    stage = 's3_evidence'
    config = {
        'bucket': 'boram3-cctv-evidence-80bee9db7c3bca316ef395375a',
        'region': 'ap-northeast-2',
        'job_id': 'af9eaea6b94c4b8398f6290899105ea4',
        'event_id': 'event_000',
        'expected_input_sha256': 'b565e08905cdb8e380617ad05313d7b4214eb93d9d7d63d8eae5b15fc610b66a',
    }
    output = Path('/spool/connection-checks') / uuid.uuid4().hex
    result = scene.fetch(config, output, scene.InstanceRoleReader(config['region']))
except Exception as exc:
    code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
    if code not in ('AccessDenied', 'NoSuchKey', 'ExpiredToken', 'InvalidAccessKeyId', 'SignatureDoesNotMatch'):
        code = type(exc).__name__
    print(json.dumps({'status': 'failed', 'stage': stage, 'error': code, 'jobs_submitted': 0}))
    raise SystemExit(1)
print(json.dumps({'status': 's3_evidence_verified', 'credential_source': 'iam-role',
                  'media_bytes_verified': True, 'output': result['output'],
                  'jobs_submitted': 0, 'gemini_api_calls': 0}))
PY
```

## 단계2 수정 — EC2 역할 연결·컨테이너 메타데이터 접근

2026-09-30 사용자 현재 조회: ProfileAttached=false, Endpoint=enabled, Tokens=required, HopLimit=1. 이전 조회 원본의 기존 인스턴스 프로필 `hanwha-cctv-ec2-role`을 재사용한다. 코드의 `metadata_response_hop_limit` 기본값은1이며, Git제외 `be-instance-role.auto.tfvars.json`에는 해당프로필과2를 준비했다. 이설정은후속plan에도유지한다. 실제AWS적용은아직없다. validate/mock6시험은통과했다.

**Mac 터미널 — 실제 변경 계획만 생성:** AWS를조회하며적용하지않는다. 예상은기존EC2의프로필연결과hop1→2, `0 to add, 1 to change, 0 to destroy`다. IMDSv2 required를유지한다. EC2교체/삭제/디스크/보안그룹등다른변경이있으면적용하지말고확인한다. 마지막Plan요약과프로필·metadata_options변경부분을전달한다. 아직 `terraform apply`는실행하지않는다.

```bash
cd ./infra/terraform/cpu-rag
export AWS_PROFILE=cctv
export AWS_DEFAULT_REGION=ap-northeast-2
test ! -e be-role-20260930.tfplan &&
terraform plan -out=be-role-20260930.tfplan
```

같은plan파일이이미있으면덮어쓰지말고출력/생성여부를확인한다. plan성공은PassRole적용권한이나S3읽기성공의증거가아니다. 적용후컨테이너역할인증과기존근거바이트/해시를다시검증한다.

### 2026-09-30 plan 검토 결과 — 기존 plan 적용 보류

실제 plan은2변경이다. instance의역할/hop변경외에보안그룹의기존SSH허용IP한개를삭제한다. 해당접속규칙을보존하기위해 `be-role-20260930.tfplan`을apply하지않는다. public_ip출력갱신은이추가보안그룹변경과별개다.

**Mac 터미널 — 필요한 AWS 설정만 변경(사용자 실행):** 기존확인된프로필을연결하고컨테이너IMDS응답hop을2로설정한다. IMDSv2필수·SSH규칙·DB·디스크는유지한다. 이명령은실제AWS변경이며조회가아니다. 첫명령오류면두번째는실행하지않는다. 예상은역할상태associating/associated와메타데이터pending/applied,HopLimit2다. 출력확인후S3읽기를다시검증한다.

```bash
aws ec2 associate-iam-instance-profile \
  --instance-id i-02017b92281737f53 \
  --iam-instance-profile Name=hanwha-cctv-ec2-role \
  --profile cctv --region ap-northeast-2 \
  --query 'IamInstanceProfileAssociation.{State:State}' \
  --output json --no-cli-pager &&
aws ec2 modify-instance-metadata-options \
  --instance-id i-02017b92281737f53 \
  --http-tokens required --http-put-response-hop-limit 2 \
  --profile cctv --region ap-northeast-2 \
  --query 'InstanceMetadataOptions.{State:State,Tokens:HttpTokens,HopLimit:HttpPutResponseHopLimit}' \
  --output json --no-cli-pager
```

적용 후 같은 명령을 반복하지 말고 출력을 확인한다. Terraform의 의도 설정은 준비됐지만 state 갱신과 SSH 규칙 차이는 후속 확인 사항이다. 기존 saved plan은 재사용하지 않는다.

## 단계3 — 전송한 시험 영상 검증·접수

사용자 `video_uploaded` 출력 수신. **EC2 터미널**에서 아래를 실행한다. Mac에서 확인한 SHA256과 대조하고 BE 입력 경로에 설치한 뒤 기존 enqueue를 호출한다. 기존 다른 파일은 덮어쓰지 않는다. 같은 request-key 재실행은 기존 작업을 반환하며 GPU 제출은 dispatch 단계다.

예상 결과: 해시검사 `OK` → `worker_job_id`, `already_present:false`. 이미 접수했다면 `already_present:true`와 같은 작업 ID가 정상이다. 여기서는 영상 전송 무결성·접수만 검증하며 GPU·Gemini 호출은 없다.

```bash
sudo bash <<'SH'
set -euo pipefail
source /etc/cctv/be-worker-v1.env
video=/home/ubuntu/ec2-stage3-20260930-01.mp4
printf '%s  %s\n' '2e23d2495ee400fd4be685d80598cbcd134fd7db46ba520952fec0cc5c3c9fe9' "$video" | sha256sum -c -
target="$BE_INPUT_DIR/ec2-stage3-20260930-01.mp4"
if [ -e "$target" ]; then
  cmp -s "$video" "$target"
else
  install -m 600 -o 10001 -g 10001 "$video" "$target"
fi
cd "$BE_APP_DIR/../.."
bash apps/be_worker/queue.sh enqueue \
  --video /input/ec2-stage3-20260930-01.mp4 \
  --config "$BE_TARGET_CONFIG" \
  --request-key ec2-stage3-20260930-01
SH
```

## BE 배포와 영상 한 건 실행 — 기존 EC2용

2026-09-29 사용자 출력에서 기존 EC2는 DB healthy, `cctv_rag.chunks` 2행이며 `cctv_jobs.queue`와 `cctv_scene.descriptions`는 없다. 아래는 이 환경에 **같은 BE 코드**를 배포하는 사용자 실행 절차다. 로컬 DB를 EC2에 덮어쓰지 않는다. 새 코드 경로는 `/opt/cctv-be-release-20260929-01`, 작업 경로는 `/srv/cctv/be`, DB Compose는 기존 `/opt/cctv-stack/compose.yaml`을 사용한다. 상시 자동 worker 설치가 아니라 단계별 CLI 배포·연결 확인이다.

```text
한 번 준비: Mac에서 코드 전송 → EC2에서 압축 검증·경로 설정 → 없는 테이블 추가
           → 분석 토큰·Gemini 키 준비 → BE 이미지 빌드 → 컨테이너에서 연결 검사
매 영상:   enqueue → dispatch → collect → scene-fetch → scene-describe → EC2 DB 조회
```

### 1. Mac 터미널 — 준비한 CPU BE 코드 전송

기존 SSH 도우미는 동일 인스턴스의 확인된 host key를 사용한다. 공인 IP가 바뀌면 먼저 같은 인스턴스의 현재 IP를 확인해 `CCTV_EC2_HOST`에 지정한다. 아래 전송은 코드 ZIP만 보내며, 이미 같은 이름의 파일이 있으면 덮어쓰지 않고 실패한다.

```bash
cd .
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh \
  'umask 077; set -C; cat > /home/ubuntu/cctv-be-worker-20260929-01.zip' \
  < artifacts/be-worker/ec2-deploy-20260929-01/cctv-be-worker.zip
```

SSH 접속이 끊겨 전송이 불완전했다면 새 전송 파일명을 사용하고 다음 단계의 ZIP 경로도 함께 바꾼다. 토큰·영상·가중치·DB는 이 ZIP에 포함되지 않는다.

### 2. EC2 터미널 — 코드 설치와 경로 설정

이미 `ubuntu@...` 터미널에 있으면 그대로 아래를 실행한다. `sudo -i` 이후 이 절의 EC2 명령은 같은 root 셸에서 실행한다. 컨테이너 안의 BE는 별도 UID 10001로 실행하도록 설정한다.

```bash
sudo -i
test ! -e /opt/cctv-be-release-20260929-01 &&
python3 -m zipfile -e /home/ubuntu/cctv-be-worker-20260929-01.zip /opt/cctv-be-release-20260929-01 &&
python3 /opt/cctv-be-release-20260929-01/apps/be_worker/verify_bundle.py &&
chmod -R a+rX /opt/cctv-be-release-20260929-01
```

`Bundle verified`까지 확인한다. 새 경로가 이미 있으면 압축을 다시 덮어쓰지 말고 위 검증 명령만 실행한다. 여기까지는 DB나 Runpod를 변경하지 않는다.

아래는 경로 설정 파일을 처음 한 번 작성한다. 기존 파일이 있으면 내용을 바꾸지 않고 사용한다. 현재 서버의 Compose 프로젝트 이름은 `cctv`다.

```bash
if [ ! -e /etc/cctv/be-worker-v1.env ]; then
  cat > /etc/cctv/be-worker-v1.env <<'ENV'
export COMPOSE_PROJECT_NAME=cctv
export CCTV_COMPOSE_FILE=/opt/cctv-stack/compose.yaml
export CCTV_DATA_DIR=/srv/cctv
export CCTV_SECRETS_DIR=/etc/cctv/secrets
export BE_APP_DIR=/opt/cctv-be-release-20260929-01/apps/be_worker
export BE_SCENE_APP_DIR=/opt/cctv-be-release-20260929-01/apps/scene_description
export BE_INPUT_DIR=/srv/cctv/be/input
export BE_SPOOL_DIR=/srv/cctv/be/spool
export BE_SECRETS_DIR=/etc/cctv/be-secrets
export BE_CONTAINER_UID=10001
export BE_CONTAINER_GID=10001
export BE_TARGET_CONFIG=/input/config-ec2-v1.json
ENV
  chmod 600 /etc/cctv/be-worker-v1.env
fi
source /etc/cctv/be-worker-v1.env
install -d -m 755 /srv/cctv/be
install -d -m 700 -o 10001 -g 10001 "$BE_INPUT_DIR" "$BE_SPOOL_DIR" "$BE_SECRETS_DIR"
cd /opt/cctv-be-release-20260929-01
```

새 EC2 터미널에서 재개할 때는 `sudo -i` → `source /etc/cctv/be-worker-v1.env` → 위 코드 폴더로 `cd`한다. 코드 업로드·압축 해제·DB 초기화를 매번 반복하지 않는다.

### 3. EC2 터미널 — BE 테이블 두 개 추가

```bash
bash apps/be_worker/queue.sh schema &&
bash apps/be_worker/queue.sh scene-schema &&
docker compose -f "$CCTV_COMPOSE_FILE" exec -T --user postgres db \
  psql -X -v ON_ERROR_STOP=1 -d cctv \
  -c "SELECT to_regclass('cctv_jobs.queue') AS be_queue,
             to_regclass('cctv_scene.descriptions') AS scene_descriptions;" \
  -c "SELECT count(*) AS chunk_count FROM cctv_rag.chunks;"
```

두 테이블 이름이 보이고 기존 `chunk_count`가 2면 이번 준비 단계가 통과한 것이다. 새 큐·장면 스키마만 적용하며 법령 청크를 다시 임베딩하거나 RAG 테이블을 초기화하지 않는다. 아직 영상 제출과 Gemini 호출은 없다.

### 4. 비밀 설정·접속 설정 준비

**Mac 터미널:** 현재 연결에 사용한 분석 토큰을 SSH로 전송한다. 파일 내용을 화면에 출력하지 않는다. 이 토큰은 Runpod 계정 API key가 아니라 분석 서버의 `.api-token`과 일치하는 값이다.

```bash
cd .
test -s artifacts/be-worker/queue-fresh-v1/secrets/runpod_worker_token &&
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh \
  'umask 077; set -C; cat > /home/ubuntu/runpod-worker-token-20260929-01' \
  < artifacts/be-worker/queue-fresh-v1/secrets/runpod_worker_token
```

**EC2의 앞서 준비한 root 셸:** 없는 분석 토큰만 설치한다. 기존 파일과 다르면 자동 교체하지 않고 Pod 주소·토큰부터 확인한다.

```bash
if [ ! -e "$BE_SECRETS_DIR/runpod_worker_token" ]; then
  install -m 600 -o 10001 -g 10001 /home/ubuntu/runpod-worker-token-20260929-01 \
    "$BE_SECRETS_DIR/runpod_worker_token"
fi
cmp -s /home/ubuntu/runpod-worker-token-20260929-01 "$BE_SECRETS_DIR/runpod_worker_token" &&
python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR"
```

Gemini 키는 기존 EC2 키 파일에 값이 있으면 재사용하고, 없다면 숨김 입력으로 준비한다. 이 명령은 모델을 호출하지 않는다.

```bash
if [ ! -s "$BE_SECRETS_DIR/gemini_api_key" ]; then
  if [ -s /etc/cctv/secrets/gemini_api_key ]; then
    install -m 600 -o 10001 -g 10001 /etc/cctv/secrets/gemini_api_key "$BE_SECRETS_DIR/gemini_api_key"
  else
    python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR" --gemini
  fi
fi
chown 10001:10001 "$BE_SECRETS_DIR"/{runpod_worker_token,aws_credentials,aws_config,gemini_api_key}
chmod 600 "$BE_SECRETS_DIR"/{runpod_worker_token,aws_credentials,aws_config,gemini_api_key}
```

새 EC2 config를 작성할 때 현재 Pod의 Port 8000 HTTPS 주소를 입력한다. 기존 config가 있다면 재사용하거나 새 파일명으로 작성하며 덮어쓰지 않는다.

```bash
python3 apps/be_worker/worker.py configure \
  --output "$BE_INPUT_DIR/config-ec2-v1.json" \
  --s3-bucket boram3-cctv-evidence-80bee9db7c3bca316ef395375a \
  --s3-region ap-northeast-2
chown 10001:10001 "$BE_INPUT_DIR/config-ec2-v1.json"
chmod 600 "$BE_INPUT_DIR/config-ec2-v1.json"
bash apps/be_worker/queue.sh build &&
bash apps/be_worker/queue.sh list
```

처음 큐를 준비했다면 `[]`를 기대한다. 기존 작업이 있으면 그 기록을 보존한다. 이미지 빌드는 EC2용 Python·라이브러리를 다운로드하며 GPU·Gemini 호출은 하지 않는다.

### 5. EC2 BE 컨테이너에서 Runpod·S3 연결 검사

S3는 기존 `hanwha-cctv-ec2-role` 인스턴스 프로필 연결과 근거 읽기 권한이 필요하다. 이전 조회에서는 EC2에 프로필이 없었으므로 아래 IAM 절차로 확인·연결한 뒤 검사한다. **역할 연결과 실제 컨테이너 S3 읽기가 확인되기 전에는 새 영상을 제출하지 않는다.** 정적 AWS 키를 Mac에서 복사하는 방식으로 설정하지 않는다.

현행 Terraform의 IMDS 응답 hop limit는 1이다. Docker에서 역할 인증을 쓰려면 2가 필요할 수 있으므로 기존 state의 변경 계획에서 검토한다. IMDSv2 필수 설정은 유지한다. [AWS의 컨테이너 메타데이터 안내](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-data-retrieval.html).

같은 EC2 root 셸에서 아래 함수를 정의한다. 기존 BE와 같은 UID·네트워크·비밀 파일을 마운트한 컨테이너에서 Python 검사만 실행한다.

```bash
be_python() {
  docker compose -f "$CCTV_COMPOSE_FILE" -f "$BE_APP_DIR/queue.compose.yaml" --profile be-tools \
    run --rm -T --no-deps --entrypoint python be-queue "$@"
}
be_python - <<'PY'
import json
from pathlib import Path
from apps.be_worker.common import WorkerError, config_checked, read_json
from apps.be_worker.runpod_api import RunpodAPI
try:
    config = config_checked(read_json(Path('/input/config-ec2-v1.json')))
    token = Path('/run/secrets/runpod_worker_token').read_text().strip()
    health = RunpodAPI(config['runpod_url'], token).health()
except WorkerError as exc:
    print(json.dumps({'error': exc.code, 'jobs_submitted': 0}))
    raise SystemExit(1)
print(json.dumps({'status': 'connected', **health, 'jobs_submitted': 0}))
PY
```

`connected / storage:s3`를 확인한다. 이어서 동일 컨테이너에서 기존 S3 manifest 읽기를 확인한다. 아래 키는 이미 성공한 로컬 사건의 원격 근거이며 EC2 큐에 그 사건이 등록돼 있어야 하는 것은 아니다.

```bash
be_python - <<'PY'
import json
from pathlib import Path
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
try:
    config = json.loads(Path('/input/config-ec2-v1.json').read_text())
    session = boto3.Session(region_name=config['s3_region'])
    credentials = session.get_credentials()
    if credentials is None or credentials.method != 'iam-role':
        raise RuntimeError('instance_role_credentials_not_ready')
    client = session.client('s3', config=Config(connect_timeout=5, read_timeout=15, retries={'total_max_attempts': 1}))
    response = client.get_object(Bucket=config['s3_bucket'],
        Key='cctv-evidence/af9eaea6b94c4b8398f6290899105ea4/manifest.json')
    with response['Body'] as body:
        data = body.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise RuntimeError('manifest_too_large')
    json.loads(data)
except ClientError as exc:
    print(json.dumps({'status': 'failed', 'error': exc.response['Error']['Code']}))
    raise SystemExit(1)
except (BotoCoreError, OSError, ValueError, RuntimeError) as exc:
    print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
    raise SystemExit(1)
print(json.dumps({'status': 's3_read_ok', 'credential_source': 'instance_role', 'jobs_submitted': 0, 'gemini_api_calls': 0}))
PY
```

이 검사는 읽기 권한·JSON 수신만 확인한다. 실제 클립 해시 검증은 아래 scene-fetch에서 수행한다. 키 값과 원격 manifest 본문은 출력하지 않는다.

### 6. 영상 A 전송과 EC2 접수

**Mac 터미널:** 현재 로컬 입력 A를 EC2로 복사한다. 이 단계는 GPU 분석 요청이 아니다.

```bash
cd .
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh \
  'umask 077; set -C; cat > /home/ubuntu/A-ec2-demo-v1.mp4' \
  < artifacts/be-worker/queue-fresh-v1/input/A.mp4
```

**EC2의 설정을 불러온 root 셸:** 처음 한 번 입력 파일을 설치하고 접수한다.

```bash
if [ -e "$BE_INPUT_DIR/A.mp4" ]; then
  cmp -s /home/ubuntu/A-ec2-demo-v1.mp4 "$BE_INPUT_DIR/A.mp4"
else
  install -m 600 -o 10001 -g 10001 /home/ubuntu/A-ec2-demo-v1.mp4 "$BE_INPUT_DIR/A.mp4"
fi &&
bash apps/be_worker/queue.sh enqueue \
  --video /input/A.mp4 --config "$BE_TARGET_CONFIG" --request-key ec2-a-v1
```

반환된 EC2의 새 `worker_job_id`를 보관한다. 로컬에서 완료한 작업 ID와 다르다. 같은 접수 재실행은 같은 request-key를 사용하며, 실제 다른 영상은 새 키를 사용한다.

### 7. EC2에서 한 건의 분석·설명·저장

여기부터 새 GPU 분석·S3 업로드·Gemini 호출에 사용량이 발생한다. 먼저 Runpod GPU와 API가 켜져 있어야 한다. 아래 단계는 모두 같은 EC2 root 셸에서 실행한다.

```bash
bash apps/be_worker/queue.sh dispatch --seconds 300 --slots 1
bash apps/be_worker/queue.sh list
```

`waiting`이면 동일 작업의 dispatch를 이어간다. 새 영상을 다시 enqueue하는 의미가 아니다. `evidence_ready`이면:

```bash
bash apps/be_worker/queue.sh collect --seconds 60
bash apps/be_worker/queue.sh list
```

`handoff_ready`이고 사건이 있으면 출력된 실제 ID로 이어간다. `no_candidates`이면 설명할 후보가 없어 이 건의 흐름을 끝내며, 실제 정상 정답으로 확대하지 않는다. 실패 시 저장된 실패 단계를 확인하고 같은 영상을 무조건 다시 제출하지 않는다.

```bash
export WORKER_JOB_ID='여기에_EC2에서_반환된_worker_job_id'
export EVENT_ID=event_000
bash apps/be_worker/queue.sh scene-fetch --worker-job-id "$WORKER_JOB_ID" --event-id "$EVENT_ID"
```

`media_verified` 확인 후:

```bash
bash apps/be_worker/queue.sh scene-describe --worker-job-id "$WORKER_JOB_ID" --event-id "$EVENT_ID"
```

`description_stored / database_loaded:true`를 확인하고 응답의 `analysis_id`를 사용해 EC2 DB의 같은 결과를 조회한다. 설명 실패 시 기존 실패 분석 ID와 저장 응답부터 확인하며 자동 반복 호출하지 않는다.

```bash
export ANALYSIS_ID='여기에_성공_응답의_analysis_id'
docker compose -f "$CCTV_COMPOSE_FILE" exec -T --user postgres db \
  psql -X -v ON_ERROR_STOP=1 -v analysis_id="$ANALYSIS_ID" -d cctv <<'SQL'
SELECT analysis_id, provider_job_id, event_id, accident_suspicion, incident_types,
       payload->'description'->>'summary' AS summary
FROM cctv_scene.descriptions WHERE analysis_id = :'analysis_id'::uuid;
SELECT count(*) AS chunk_count FROM cctv_rag.chunks;
SQL
```

해당 분석 결과 1행과 법령 청크 2행 유지를 확인하면 이번 EC2 단계별 연결 검증을 완료한다. summary는 현행 db.store가 저장하는 result의 description.summary를 조회한다. RAG 검색·보고와 전체 상시 자동 실행은 이후 연결하며, 현재 CLI의 의미는 [BE 실행 안내](../../../apps/be_worker/QUEUE.md)를 따른다.

## 이전 재개 순서 — 2026-09-28

사용자는 기존 EC2를 중지했다고 보고했다. 새 BE 장면 연결 코드는 Mac의 기존
Docker DB에서 먼저 확인한다. 아래 과거의 EC2 검색 단계가 선행 조건은 아니다.
이번에 실제 AWS 상태를 조회하거나 서버를 시작하지 않았다.

### Mac: 기존 EC2가 중지인지 확인하고 시작

```bash
aws ec2 describe-instances --instance-ids i-02017b92281737f53 \
  --profile cctv --region ap-northeast-2 \
  --query 'Reservations[].Instances[].{ID:InstanceId,State:State.Name,IP:PublicIpAddress,Profile:IamInstanceProfile.Arn}' \
  --output table --no-cli-pager
```

`stopped`이면 같은 인스턴스를 시작한다. `terminated`/ID 없음은 재개가 아니므로
기존 디스크·state부터 확인한다. `running`이면 시작을 반복하지 않는다.

```bash
aws ec2 start-instances --instance-ids i-02017b92281737f53 --profile cctv --region ap-northeast-2 --no-cli-pager
aws ec2 wait instance-running --instance-ids i-02017b92281737f53 --profile cctv --region ap-northeast-2
aws ec2 describe-instances --instance-ids i-02017b92281737f53 \
  --profile cctv --region ap-northeast-2 \
  --query 'Reservations[0].Instances[0].{State:State.Name,IP:PublicIpAddress}' --output table --no-cli-pager
```

현재 Terraform에는 `aws_ec2_instance_state`가 없다. 기존 서버를 켜기 위해
`terraform apply`를 반복하거나 새 서버를 만들 필요가 없다. 중지 시 EBS 데이터는
보존되지만 자동 할당 공인 IP는 시작 후 달라질 수 있다.
[AWS 중지·시작 설명](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-ec2-instance-stop-start-works.html)

SSH 도우미의 IP가 이전 값이면 새 IP로 갱신하고 호스트키를 검증한다.
Mac의 위치가 바뀌어 공인 IP도 달라졌다면 `ssh_source_cidr`만 새 `/32`로 변경하고
기존 state 위치에서 새 Terraform plan을 검토한다. DB5432를 공개하지 않는다.
EC2에 접속한 다음:

```bash
sudo docker compose -f /opt/cctv-stack/compose.yaml ps
```

기존 `db`가 정지된 경우만 `sudo docker compose -f /opt/cctv-stack/compose.yaml up -d db`로
기동한다. 기존 DB 폴더·비밀번호·벡터를 다시 생성하지 않는다.

### IAM 권한을 설정할 때

기존 역할 `hanwha-cctv-ec2-role`을 재사용한다. 아래 JSON은 **편집용 예시**이며
적용된 정책이 아니다. `123456789012`, `i-REPLACE_WITH_EXISTING_ID`,
`REPLACE_WITH_BUCKET`을 실제 값으로 바꾼다.

| 대상 | 파일 또는 설정 | 목적 |
|---|---|---|
| Mac CLI 사용자/그룹 | [운영 권한 예시](iam-operator.example.json) | 기존 EC2 시작·중지, 역할·프로필 조회, 지정 역할만 EC2에 전달 |
| EC2 역할 | [근거 읽기](iam-evidence-reader.example.json) | 해당 버킷 `cctv-evidence/*`의 GetObject |
| EC2 역할, 백업 단계에서 추가 | [백업 읽기·쓰기](iam-backup.example.json) | `cctv-db-backups/*`의 PutObject/GetObject |
| EC2 역할, SSM 접속을 사용할 때 | AWS 관리형 `AmazonSSMManagedInstanceCore` | SSM Agent의 서버 등록·연결 |

운영 권한 예시는 기존 Terraform의 EC2/S3 관리 권한에 **추가**할 범위다. 전체
Terraform 생성·삭제 권한이나 IAM 역할 생성 권한을 모두 포함하지 않는다.
`GetInstanceProfile`은 이름을 확인한 뒤 해당 프로필 ARN 하나로 더 제한할 수 있다.
위 S3 코드는 정확한 key로 접근하므로 버킷 목록 권한은 요구하지 않는다.
SSE-S3 기준이며 KMS 암호화를 추가하면 별도 KMS 권한 검토가 필요하다.

역할의 신뢰 정책은 EC2 서비스(`ec2.amazonaws.com`)의 `sts:AssumeRole`을 허용해야 한다.
역할과 인스턴스 프로필 이름은 다를 수 있으므로 Mac에서 확인한다:

```bash
aws iam list-instance-profiles-for-role --role-name hanwha-cctv-ec2-role \
  --profile cctv --query 'InstanceProfiles[].{Name:InstanceProfileName,Arn:Arn}' \
  --output json --no-cli-pager
```

확인된 이름을 기존 로컬 tfvars의 `existing_instance_profile_name`에 넣고
기존 state 위치에서 새 `terraform plan`을 검토한다. 역할 연결은 기존
`aws_instance.host.iam_instance_profile` 설정이 담당한다. 이 작업이 DB 백업을
활성화하지는 않는다. 역할이 이미 프로필에 들어 있다면 역할을 새로 만들지 않는다.
[AWS EC2 역할 안내](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_switch-role-ec2.html)

현재 IMDS hop limit는 1이다. 이후 Docker 안의 BE가 인스턴스 역할을 직접 쓸 때에는
컨테이너의 자격증명 접근을 별도 검증하고 필요한 hop limit 변경을 plan으로 검토한다.
Mac에서 쓰던 AWS 키 파일을 그대로 EC2 배포 표준으로 삼지 않는다.

### GitHub 저장소 반영과 실제 배포

앱 저장소의 `infra/terraform/cpu-rag` → 인프라 저장소의 `terraform/cpu-rag`,
`infra/compose/rag` → `compose/rag`로 배치한다. 상대 경로를 유지해야 한다.
state/tfvars/키/DB/영상은 복사·게시 대상에서 제외한다. 기존 state는 현재 위치에서
계속 사용하며 새 clone의 빈 state로 apply하지 않는다. Git 커밋·푸시,
Terraform 적용, EC2 내부 앱/스키마 배포는 각각 다른 작업이다.

## 이후 실행 방식 — 사용자가 CLI 실행

2026-09-27 사용자 결정에 따라 에이전트가 로컬 코드·검사·명령을 준비하고, 사용자가 클라우드 생성·변경·원격 배포 명령을 실행한다. 사용자가 전달한 실제 출력으로 다음 단계를 판단한다.

목표는 기존 **CPU EC2 한 대의 Docker Compose + Runpod + S3 + 외부 Gemini API**다. Terraform은 AWS 자원을, Compose는 서버에서 실행할 컨테이너를 관리한다. 배포 기록상 EC2·Compose DB·문서벡터2행 적재는 완료됐으며, 아래 안내를 준비하면서 원격 상태를 다시 조회하지는 않았다.

| 순서 | 작업 | 현재 상태 |
|---|---|---|
| 1 | EC2·Compose PostgreSQL/pgvector·기존 벡터 적재 | 완료 기록 있음 |
| 2 | EC2에서 새 질문→Gemini 질문 임베딩→문서 검색 | 아래 명령을 사용자가 실행한 뒤 확인 |
| 3 | BE가 S3 실제 클립/프레임 회수→Gemini 설명→DB 저장·화면 | 후속 앱 구현·배포 |
| 4 | BE의 문서 검색→근거·출처를 포함한 보고 생성 | 후속 앱 연결 |
| 별도 | IAM 역할 연결→S3 DB 백업·복원 시험 | 역할 생성 보고 수신·실제 프로필/권한/연결 확인 대기 |

아래 2번은 당시 남겨 둔 EC2 검색 확인 절차다. 현재 재개 순서는 문서 맨 위를 따른다. 새 EC2 생성이나 기존 문서의 재임베딩을 반복하지 않는다.

사용자가 EC2의 `db` 서비스 `Up (healthy)`와 `Gemini key saved privately; no API called` 출력을 전달했다. DB 가동·키 저장은 사용자 실행 출력으로 확인했고, 새 질문 검색 결과는 아직 수신하지 않았다. 키 저장만으로 임베딩 호출이나 검색이 실행되지는 않는다.

## 기존 EC2에서 새 질문 검색 — 미실행 항목의 참고 절차

### 1. Mac 터미널: 기존 EC2 접속

```bash
cd .
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh
```

접속되면 프롬프트가 EC2의 `ubuntu@...`로 바뀐다. 아래 명령은 그 EC2 터미널에서 실행한다. 접속 실패 시 오류 출력만 확인하고 다음 단계로 넘어가지 않는다. IP 변경에 관한 안내는 아래 접속 절을 참고한다.

### 2. EC2 터미널: DB 상태 확인

```bash
sudo docker compose -f /opt/cctv-stack/compose.yaml ps
```

`db` 서비스에 `Up`과 `(healthy)`가 보이면 다음으로 진행한다. `unhealthy`, `Exited`이거나 서비스가 보이지 않으면 출력으로 원인을 확인한다. 기존 DB 폴더를 삭제하거나 초기화하지 않는다.

### 3. EC2 터미널: Gemini 키 비공개 설정

```bash
sudo python3 /opt/cctv-infra/prepare-host.py --gemini
```

`Gemini API key (hidden):`에 기존 Gemini 키를 붙여 넣고 Enter를 누른다. 입력 문자는 표시되지 않는다. 성공 시 `Gemini key saved privately; no API called`가 출력된다. 키는 서버의 접근 제한된 비밀 파일에 저장되며 기존 DB 비밀번호는 보존한다. 채팅에 키를 보내지 않는다.

### 4. EC2 터미널: 새 질문 한 건 검색

```bash
cd /opt/cctv-rag-compose-20260927
sudo bash rag.sh search \
  --query '교통사고 발생 후 조치나 신고를 방해한 경우 관련 조문' \
  --date 2026-09-27
```

이 명령은 `gemini-embedding-001`/768차원으로 **질문 임베딩 API를 1회 호출**하고, DB의 기존 문서 벡터와 비교한다. API 사용량이 발생하며 과금 여부는 해당 계정의 요금제·한도에 따른다. 문서2개는 다시 임베딩하지 않는다.

성공 시 JSON 배열에 `chunk_id`, `content`, `source_url`, `cosine_distance`가 나온다. 기존 두 조각이 검색 대상이며 실제 반환 내용은 실행 출력으로 확인한다. 이는 검색 본문·출처를 반환하는 단계로, Gemini의 최종 답변·영상 설명 생성은 후속이다. 오류 시 해당 출력부터 확인하고 API 호출을 반복하지 않는다.

확인할 출력은 DB 상태와 검색 JSON 또는 오류다. 키·비밀번호는 포함하지 않는다. 이 안내 작성 중에는 원격 명령이나 Gemini 호출을 실행하지 않았다.

## 현재 구성 — 2026-09-27

사용자가 IAM 역할 없이 SSH로 시험 배포하는 방식을 선택했다. 기존 서울 VPC와 서브넷을 사용하며 Terraform은 **EC2·SSH 공개키·보안그룹**을 관리한다. IAM 역할과 인스턴스 프로필은 만들지 않는다. SSM 접속과 S3 자동 백업은 현재 사용하지 않는다.

- EC2: 서울 `t3.small`, 2 vCPU / 2 GiB, Canonical Ubuntu 24.04 amd64.
- 기본 디스크: 암호화 gp3 20 GiB. DB 경로 `/srv/cctv/postgresql`. 별도 DB 디스크는 없다.
- 관리 접속: SSH 공개키 + 현재 Mac의 공인 IPv4 `/32` 한 곳만22번 허용. DB5432·웹 포트는 외부 공개하지 않는다.
- DB: 기존 고정 PostgreSQL16/pgvector 이미지와 Compose를 재사용한다. RAG 앱은 기존 두 벡터를 적재하며 재임베딩하지 않는다.
- S3: 기존 Runpod 근거 저장은 유지한다. EC2의 자동 백업은 비활성화한다.
- 미구현: BE·VLM·화면 통합. 이번 서버는 DB/RAG 실행 기반이며 전체 CCTV 서비스 배포 완료를 뜻하지 않는다.

`t3.medium`의 생성 권한 dry-run은 통과했지만 실제 생성 시 계정의 Free Tier 유형 제한으로 거부됐다. AWS의 eligible 목록에 있는 더 작은 `t3.small`을 선택했다. 이는 과금이 무조건0이라는 뜻은 아니다. 할인 전 서울 기준 EC2 $0.026/시간 + IPv4 $0.005/시간 + gp3 20GiB $1.824/월 = 약 **$24.45/730시간**. 세금·전송·S3·Gemini·Runpod는 별도이며 계정 크레딧/실청구액은 확인하지 않았다.

처음 IAM 방식으로 만든 보안그룹을 재사용한다. 그 그룹의 최초 설명에는 SSM/인바운드없음이라는 과거 문구가 남아 있지만 실제 ingress는 SSH 단일IP이고 `AccessMode=ssh-single-ip` 태그를 붙였다. 설명 변경에 따른 불필요한 그룹 교체를 피했다.

## 현재 서버에 접속

프로젝트 루트의 아래 실행 도우미는 전용 개인키와 검증한 known_hosts를 사용한다. 개인키는 Git에서 제외된 `artifacts/infra/cpu-rag-ssh-20260927-01/private/`에 있고 출력하거나 업로드하지 않았다. 해당 파일을 잃으면 현재 방식으로 접속할 수 없으므로 보존한다.

```bash
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh
```

서버에서 확인:

```bash
sudo cloud-init status --long
sudo docker compose -f /opt/cctv-stack/compose.yaml ps
sudo cat /srv/cctv/backup-status.txt
```

Mac의 IP가 바뀌면 `terraform.tfvars.json`의 `ssh_source_cidr`를 새 공인IPv4 `/32`로 바꾸고 새 plan을 검토해 적용한다. 접속 범위를 전체 인터넷으로 넓히지 않는다. 서버 재시작 후 공인 IP는 위 AWS 조회 명령으로 확인한다. `terraform output`은 마지막 state의 값이므로 refresh 전에는 이전 IP가 나올 수 있다.

## Terraform 재개

현재 `infra/terraform/cpu-rag/terraform.tfstate`를 계속 사용한다. 기존 S3 state는 `../evidence-s3`에 별도로 보존한다. 다른 clone의 빈 state에서 apply하지 않는다.

```bash
cd infra/terraform/cpu-rag
export AWS_PROFILE=cctv
export AWS_DEFAULT_REGION=ap-northeast-2
terraform fmt -check
terraform validate
terraform plan -out=next.tfplan
terraform show next.tfplan
```

코드와 실제 변화가 필요한지 검토한 뒤 saved plan을 적용한다. `user_data_replace_on_change=true`와 `prevent_destroy=true`가 있으므로 부팅 스크립트를 변경한 뒤 기존 서버를 자동 교체하지 않는다. 실제 서버를 갱신할 때는 별도의 배포/마이그레이션 범위를 검토한다.

공개키만 `ssh_public_key`로 전달한다. 개인키를 Terraform 변수/state/user-data에 넣지 않는다. 초기 IP·AMI·서브넷·계정 값은 로컬 tfvars.json에 고정했고 Git에서 제외된다.

## 기존 임베딩 적재

전송 묶음은 `artifacts/infra/cpu-rag-cloud-20260927-01/cctv-rag-compose-with-embeddings.zip`이다. 기존 법령2조각과 실제 `gemini-embedding-001`/768차원 벡터2개·완료영수증을 포함하며 인증키는 없다.

새 서버에 묶음을 검증해 풀었다면 서버의 앱 폴더에서 다음 순서를 사용한다. 이미 성공한 적재는 다시 실행할 필요가 없다.

```bash
cd /opt/cctv-rag-compose-20260927
sudo bash rag.sh build
sudo bash rag.sh schema
sudo bash rag.sh check --input /input/chunks.jsonl
sudo bash rag.sh load --input /input/embedded.jsonl
```

문서벡터 재생성 API는 호출하지 않는다. 새로운 질문으로 Gemini 검색을 하려면 EC2에서 키를 비공개 입력하는 별도 설정이 필요하다. 이번 배포는 기존 벡터 적재·본문/출처/값 대조·저장된 문서 벡터 자기검색까지 검증한다. 새로운 질문의 검색 품질 평가와 구분한다.

## S3 백업과 IAM 후속

### 담당자에게 한 번에 요청할 권한 — 2026-09-27

요청 목적은 기존 CCTV EC2의 역할 연결·운영과 BE의 근거 읽기, DB 백업/복원이다. 실제 거부가 확인된 작업은 `iam:ListInstanceProfilesForRole`이며 아래 나머지는 설정 확인/후속 운영을 위한 요청 목록이다. 실제 정책을 조회하거나 허용 여부를 확인한 목록이 아니다. 현재 성공한 EC2·S3 Terraform 관리 권한은 유지한다.

**요청 대상 정보**

- 명령 실행 사용자: `dahye-dev` 또는 해당 사용자의 소속 그룹.
- 서버 역할: `hanwha-cctv-ec2-role`. 인스턴스 프로필의 정확한 이름·ARN 및 이 역할의 포함 여부도 회신 요청.
- 서버: 서울 `ap-northeast-2`, `i-02017b92281737f53` (`cctv-cpu-rag`).
- 버킷: `boram3-cctv-evidence-80bee9db7c3bca316ef395375a`.

#### A. Mac CLI를 실행하는 사용자/그룹에 필요한 권한

| 용도 | 작업 | 범위 |
|---|---|---|
| 역할 확인·프로필 찾기 | `iam:GetRole`, `iam:ListInstanceProfilesForRole` | 위 EC2 역할 |
| 프로필 내용 확인 | `iam:GetInstanceProfile` | 담당자가 만든 해당 인스턴스 프로필 |
| EC2에 역할 전달 | `iam:PassRole` | 위 역할 ARN에 한정, `iam:PassedToService = ec2.amazonaws.com` |
| 연결·교체·연결 해제 | `ec2:AssociateIamInstanceProfile`, `ec2:ReplaceIamInstanceProfileAssociation`, `ec2:DisassociateIamInstanceProfile` | 위 서울 EC2; API가 지원하는 리소스/조건 범위 적용 |
| 연결 결과 조회 | `ec2:DescribeIamInstanceProfileAssociations`, `ec2:DescribeInstances` | 조회 작업에 필요한 `Resource: *`; 기존 Terraform의 나머지 EC2 조회 권한 유지 |

`iam:PassedToService` 조건은 PassRole 문장에만 적용한다. 역할/프로필 조회 문장까지 같은 조건으로 묶으면 조회 요청에 이 조건 키가 없어 거부될 수 있다. 역할의 S3 권한과 사용자의 PassRole 권한은 각각 해당 주체에 설정한다.

**SSM으로 접속할 예정이면 함께 요청할 운영 권한**

- `ssm:StartSession`: 위 EC2와 사용할 세션 문서에 한정.
- `ssm:ResumeSession`, `ssm:TerminateSession`, `ssmmessages:OpenDataChannel`: 본인의 세션에 한정.
- `ssm:DescribeInstanceInformation`, `ssm:DescribeSessions`, `ssm:GetConnectionStatus`: 접속 상태 확인.
- `ssm:SendCommand`는 Run Command 배포나 SSM Agent 갱신을 사용할 때 추가하고 대상 EC2·허용 문서를 제한한다. 현재 SSH 배포의 선행 조건은 아니다.

콘솔에서 목록 선택도 필요하면 `iam:ListInstanceProfiles`를 추가한다. `iam:ListRoles`/`iam:ListPolicies`는 해당 콘솔 화면을 사용할 때 검토한다. 현재 정확한 이름을 사용하는 CLI 흐름의 필수 권한으로 요청하지 않는다. 이 목록은 새 EC2/보안그룹/버킷의 전체 생성·삭제 권한을 대체하지 않으며 현재 확보한 관리 권한을 유지한다.

#### B. 서버 역할 hanwha-cctv-ec2-role에 필요한 권한

신뢰 관계는 `ec2.amazonaws.com`이 역할을 맡을 수 있도록 설정하고 해당 역할을 인스턴스 프로필에 포함한다.

| 용도 | 권한 | 범위 |
|---|---|---|
| EC2를 SSM 관리 대상으로 등록 | 관리형 정책 `AmazonSSMManagedInstanceCore` | 서버 역할에 연결 |
| BE가 영상 근거 읽기 | `s3:GetObject` | 위 버킷의 `cctv-evidence/*` |
| DB 백업 저장·복원 | `s3:PutObject`, `s3:GetObject` | 위 버킷의 `cctv-db-backups/*` |
| 근거·백업 목록 확인 | `s3:ListBucket` | 버킷 ARN, `s3:prefix`를 위 두 경로의 정확한 prefix 및 하위 경로로 제한 |
| 버킷 리전 확인 | `s3:GetBucketLocation` | 해당 버킷 ARN |

`GetObject`/`PutObject`는 객체 ARN(`/경로/*`), `ListBucket`/`GetBucketLocation`은 버킷 ARN에 적용한다. `s3:prefix` 조건은 ListBucket 문장에만 둔다. 현재 S3 설정은 SSE-S3(AES256)여서 이 구성만을 위해 KMS 권한을 요청하지 않는다. 기존 Runpod 업로드 주체의 권한은 별도로 유지한다.

백업의 GetObject는 S3에서 백업을 내려받아 복원하기 위한 후속 권한이다. 이전에 요청한 PutObject만으로 복원까지 가능하다고 설명하지 않는다. 애플리케이션은 근거 key를 알면 GetObject로 접근할 수 있고, ListBucket은 운영 시 목록을 확인하기 위한 권한이다.

#### C. 기능을 붙일 때 적용할 항목

- **CloudWatch 로그 수집:** 서버 역할에 `logs:CreateLogStream`, `logs:PutLogEvents` 및 사용하는 수집기의 조회 권한을 프로젝트 로그 그룹으로 제한한다. 로그 그룹 생성은 Terraform 배포 사용자 또는 담당자가 맡고 `logs:CreateLogGroup`/보존기간 설정 권한은 그 주체에 둔다. 아직 로그 수집기는 배포하지 않았다.
- **BE의 원본 영상 업로드·보고 파일 저장:** 사용할 S3 경로를 확정한 뒤 그 경로의 PutObject/GetObject를 서버 역할에 추가한다. 현재 구현된 경로는 cctv-evidence/와 예정 백업 cctv-db-backups/이며 원본·보고 경로를 임의로 구현 완료로 표시하지 않는다. 보고를 DB에만 저장하면 별도 보고 파일 쓰기는 필요 없다.
- **Gemini·PostgreSQL:** 현재 Gemini API 호출은 Gemini 키, DB 연결은 DB 계정으로 인증한다. AWS IAM에 Gemini/pgvector 권한을 추가하는 방식이 아니다.

위 목록은 담당자에게 검토를 요청할 범위이며 계정에 적용한 정책이 아니다. 확인된 프로필로 사용자 plan/apply가 성공하고 서버에서 필요한 S3·SSM 작업까지 검증해야 완료다. 현재 Docker의 IMDSv2 hop limit은1이므로 역할 연결 후 BE 컨테이너의 자격 증명 전달 경로도 검증한다. 장기 AWS 키를 컨테이너에 복사하는 방식을 기본으로 삼지 않는다.

공식 근거: [EC2 역할 연결 권한](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/permission-to-pass-iam-roles.html), [PassRole 조건](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_passrole.html), [SSM 서버 정책](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonSSMManagedInstanceCore.html), [SSM 사용자 접속 정책](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-restrict-access-quickstart.html), [S3 API별 권한](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html), [S3 prefix 조건](https://docs.aws.amazon.com/AmazonS3/latest/userguide/amazon-s3-policy-keys.html), [CloudWatch Logs 권한](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/permissions-reference-cwl.html).

`cctv-db-backups/`는 기존 S3 버킷 안에 PostgreSQL의 논리 백업을 저장할 객체 경로다. 복구 가능한 본문·출처·벡터와 DB 구조 등을 담은 dump를 보관할 예정이다. 현재 부팅 스크립트에 `false`를 전달해 백업 서비스/timer를 설치하지 않는다. 기본 EC2 디스크 보존은 별도 백업을 대신하지 않는다.

담당자가 역할을 제공한다면 다음 범위를 요청한다.

- EC2를 신뢰하는 역할 및 인스턴스 프로필.
- `AmazonSSMManagedInstanceCore` 정책.
- 기존 버킷의 `cctv-evidence/*`에 `s3:GetObject`.
- `cctv-db-backups/*`에 `s3:PutObject`.
- 배포 계정에 해당 역할의 `iam:PassRole`과 필요한 조회 권한.

역할과 프로필 이름을 받으면 해당 프로필을 기존 EC2에 연결하는 변경과 백업 프로그램 설치를 따로 검토한다. 실제 백업 업로드·별도 DB로 복원하는 시험까지 끝나야 백업 완료로 표시한다.

### 받은 역할을 기존 EC2에 연결 — 사용자 Mac에서 실행

담당자 답변으로 `hanwha-cctv-ec2-role` 역할 생성과 사용자 소속 그룹의 `passCctvEc2Role` 정책 설정을 전달받았다. 정책 이름만으로 실제 권한 허용 여부는 확인되지 않는다. 현재 코드에는 기존 프로필 이름을 받는 `existing_instance_profile_name` 변수를 추가했으며, 실제 AWS 적용은 아직 하지 않았다. 빈 기본값은 기존 역할 없는 상태를 유지한다.

**1. Mac 터미널에서 역할에 속한 인스턴스 프로필 조회**

현재 EC2 터미널과 별도로 Mac 터미널을 열고 실행한다.

```bash
aws iam list-instance-profiles-for-role \
  --role-name hanwha-cctv-ec2-role \
  --profile cctv \
  --query 'InstanceProfiles[].{ProfileName:InstanceProfileName,Roles:Roles[].RoleName}' \
  --output json \
  --no-cli-pager
```

반환된 `ProfileName`이 Terraform에 넣을 값이다. 역할 이름과 같을 수 있지만 추측하지 않는다. 빈 배열이면 담당자에게 EC2 인스턴스 프로필 생성 및 역할 포함 여부를 확인한다. `AccessDenied`라면 `iam:ListInstanceProfilesForRole` 조회가 거부된 것이며 PassRole 실패와 구분한다. 후보가 여러 개면 사용할 프로필을 확인한 뒤 진행한다.

2026-09-27 사용자 Mac 출력에서 실제 `iam:ListInstanceProfilesForRole` 거부를 확인했다. 담당자는 이 조회 권한을 명령 실행자인 `dahye-dev` 사용자 또는 소속 그룹에 해당 역할 범위로 부여해야 한다. EC2 역할에 이 조회 권한을 추가해도 Mac 사용자의 권한 부족이 해결되지는 않는다. 또는 담당자가 `hanwha-cctv-ec2-role`이 포함된 정확한 인스턴스 프로필 이름을 직접 알려주면 그 값을 아래 설정에 사용할 수 있다. 이름을 받아도 실제 역할 연결 권한 검증은 plan/apply 단계에 남는다.

**2. 확인한 프로필 이름을 로컬 변수 파일에 보존**

아래 Python은 키나 비밀번호를 입력받지 않는다. 조회 결과의 실제 `ProfileName`을 입력한다. 기존 `terraform.tfvars.json`과 state를 보존하고 별도 Git 제외 설정 파일을 만든다. 같은 파일이 이미 있으면 덮어쓰지 않고 오류로 중단한다.

```bash
cd ./infra/terraform/cpu-rag
python3 -c 'import json,re; from pathlib import Path; name=input("확인한 ProfileName: ").strip(); assert re.fullmatch(r"[A-Za-z0-9_+=,.@-]{1,128}",name), "프로필 이름을 확인하세요"; path=Path("instance-profile.auto.tfvars.json"); stream=path.open("x"); json.dump({"existing_instance_profile_name":name},stream,indent=2); stream.write("\n"); stream.close(); print("프로필 설정 저장 완료")'
```

이 파일은 이후 plan에서도 유지한다. 연결 후 파일을 없애거나 변수를 빈 값으로 바꾸면 다음 plan이 프로필 분리를 제안할 수 있다.

**3. 같은 Mac 터미널에서 실제 변경 계획 확인**

```bash
export AWS_PROFILE=cctv
export AWS_DEFAULT_REGION=ap-northeast-2
terraform fmt -check
terraform validate
terraform plan -out=attach-role.tfplan
terraform show -no-color attach-role.tfplan
```

외부 변경이 없다면 예상은 `0 to add, 1 to change, 0 to destroy`, 기존 `aws_instance.host`의 `iam_instance_profile` 수정이다. EC2/디스크 재생성, `user_data`나 다른 예상 밖 변경이 있으면 적용 전 확인한다. 기존 saved plan을 재사용하지 않고 현재 설정으로 만든 계획을 사용한다. **plan 성공은 실제 PassRole 권한 통과를 보장하지 않는다.**

**4. 검토한 변경이 위 내용과 일치하면 사용자가 적용**

```bash
terraform apply "attach-role.tfplan"
aws ec2 describe-iam-instance-profile-associations \
  --filters Name=instance-id,Values=i-02017b92281737f53 \
  --profile cctv --region ap-northeast-2 \
  --query 'IamInstanceProfileAssociations[].{State:State,Profile:IamInstanceProfile.Arn}' \
  --output json --no-cli-pager
```

`Apply complete`와 대상 프로필의 `State: associated`까지 확인하면 이 EC2에 대한 역할 연결 성공으로 기록한다. `associating`이면 반영 중이다. 실제 연결 요청은 `iam:PassRole`과 관련 EC2 연결 권한이 필요하므로 실패 시 오류의 작업 이름·사유를 담당자에게 전달한다. 프로필 조회 성공만으로 PassRole이 검증됐다고 보고하지 않는다.

현재 변경은 기존 EC2의 프로필 연결만 준비한다. 담당자 소유 IAM 역할·정책·프로필 자체를 Terraform 리소스로 생성하거나 가져오지 않는다. 부팅 스크립트/DB/디스크/SSH 설정은 유지하며 S3 자동 백업은 여전히 비활성이다. 역할 연결 후 호스트의 실제 S3 접근·SSM 연결을 확인하고 백업 설치/복원을 이어간다. BE 컨테이너의 AWS 인증 방식은 앱 연결 단계에서 별도로 검증한다.

근거: [실행 중 EC2의 역할 연결·프로필 이름](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/attach-iam-role.html), [PassRole 권한 확인 원리](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_passrole.html), [프로필 조회 CLI](https://docs.aws.amazon.com/cli/latest/reference/iam/list-instance-profiles-for-role.html).

## 기록·한계

- SSH 배포 원본 설정/이전 state/실패 로그/최종 계획/부팅·적재 결과: `artifacts/infra/cpu-rag-ssh-20260927-01/`.
- 이전 IAM 실패: `artifacts/infra/cpu-rag-cloud-20260927-01/`.
- 이전 README와 Terraform/부팅 코드: SSH 기록의 `before/`에 보존했다.
- 종료 보호와 기본 디스크 보존이 적용됐다. EC2를 종료해도 남겨진 디스크에는 비용이 발생할 수 있다. 중지/종료 작업은 실제 자원과 보존 범위를 확인해 수행한다.
- 2GiB 서버에서 전체 BE·화면·VLM/RAG 동시 처리 용량은 검증되지 않았다. 먼저 DB와 작은 문서 집합의 동작을 확인한다.

근거: [AWS SSH 접속](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-to-linux-instance.html), [서버 지문 확인](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connection-prereqs-general.html), [IPv4 요금](https://aws.amazon.com/vpc/pricing/). EC2 단가는 같은 날짜의 AWS Pricing API 응답으로 보관했다.

## 단계5 — 기존 장면 설명에서 검색·출처 보고 생성 (2026-09-30)

현재 EC2 단계4 통과 건을 재사용한다. 문서2개/768차원/앱 SELECT 권한 확인 완료.
실제 검색·보고 생성은 아래 사용자 실행 전까지 미검증이다. 첫 준비 ZIP
`cctv-be-worker.zip`은 CLI 등록 누락 보존본이므로 쓰지 않고 **v2**를 사용한다.
추가 패키지나 이미지 빌드는 필요하지 않다. 기존 BE 이미지의 psycopg를 재사용한다.

### 1. Mac — 코드 ZIP 전송만

```bash
cd .
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh \
  'umask 077; set -C; cat > /home/ubuntu/cctv-be-stage5-20260930-01.zip && echo stage5_uploaded' \
  < artifacts/be-worker/ec2-stage5-20260930-01/cctv-be-worker-v2.zip
```

예상: `stage5_uploaded`. 모델/DB 호출 없음. 같은 경로가 이미 있으면 덮어쓰지 않고 중단한다.

### 2. EC2 — 별도 릴리스 검증·추가 보고 테이블

```bash
sudo python3 - <<'PY'
import hashlib
from pathlib import Path
import subprocess
import zipfile

archive = Path('/home/ubuntu/cctv-be-stage5-20260930-01.zip')
release = Path('/opt/cctv-be-stage5-20260930-01')
expected = 'c9a3fb74b7e6aa408381399eb32d8494a001145616cfdecc6475cc966473cda8'
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise SystemExit('ZIP hash mismatch; stop')
if not release.exists():
    release.mkdir(mode=0o755)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(release)
subprocess.run(['python3', str(release/'apps/be_worker/verify_bundle.py')], check=True)
subprocess.run(['python3', str(release/'apps/be_worker/prepare_stage5.py'),
                '--compose', '/opt/cctv-stack/compose.yaml'], check=True)
PY
```

예상: 28파일검증, `report_schema_ready`, `existing_data_unchanged:true`,
`transaction_committed:true`, 법령2행/장면1행. 기존 문서·벡터·장면을 한 트랜잭션에서
전후 비교하며 보고 테이블만 추가한다. 모델 호출0. 실패하면 다음 생성 명령으로 진행하지 않는다.

### 3. EC2 — 기존 설명에서 보고 1건

문서 검색 기준일은 2026-09-30(이번 검증일)이며 **사고 발생일은 미확인**이다.
새 질의 임베딩1회와 보고 텍스트 생성1회가 발생한다. GPU/장면 설명 재생성은 없다.
기존 DB/spool/비밀 파일을 그대로 사용한다. 새 queue.sh가 자기 릴리스 경로를 설정하므로
기존 환경 파일의 릴리스 경로를 아직 덮어쓸 필요 없다.

```bash
sudo bash <<'SH'
set -euo pipefail
set -a
source /etc/cctv/be-worker-v1.env
set +a
cd /opt/cctv-be-stage5-20260930-01
bash apps/be_worker/queue.sh scene-report \
  --worker-job-id 5718c53095f4439581e1466e648d08b4 \
  --event-id event_000 --reference-date 2026-09-30
SH
```

예상: `report_stored`, `database_readback_verified:true`, 호출수 embedding1/generation1,
보고 해시·문서 수·한국어 초안. `insufficient`/`no_documents`는 별도 근거 부족 상태다.
검색 문서를 사건에 자동 적용하거나 사고·과실·위법을 확정하지 않는다.
실패/불확실 응답은 출력과 저장된 요청부터 확인한다. 자동 재시도하지 않는다.
성공 후 동일 명령을 실행하면 저장된 결과와 호출수1/1을 유지해야 한다.

### 4. EC2 — DB에서 최종 산출물 읽기 확인

```bash
sudo docker compose -f /opt/cctv-stack/compose.yaml exec -T --user postgres db \
  psql -X -v ON_ERROR_STOP=1 -d cctv <<'SQL'
SELECT report_id, analysis_id, state->>'status' AS status,
       state->'calls' AS calls, state->>'result_sha256' AS result_sha256,
       jsonb_pretty(state->'result' - 'query_vector' - 'scene') AS report_and_sources
FROM cctv_scene.report_runs
WHERE analysis_id = 'c1c77f52-3012-4ce7-a724-90ebf316aa9b';
SQL
```

예상: 1행과 앞선 보고 해시/호출수 일치, 검색문장·검색기준일·문서본문/출처·초안 표시.
DB 실검증과 반복 호출 검증 결과를 기록한 뒤에만 단계5 통과로 표시한다.
영상·설명·인용 해석·보고 내용의 적합성은 사용자 검수 대상이다.
Runpod 서버의 AWS 인증환경 영구 반영과 Streamlit/독립 상시 worker는 별도 남은 작업이다.

## 단계6 — Streamlit 접수·조회와 독립 worker (2026-09-30 준비)

단계5의 완료 영상·설명·보고를 초기 UI 확인에 재사용한다. 새 영상 버튼 시험과 구분한다.
UI는 API 접수/조회만 한다. API가 PostgreSQL 큐에 저장하고 별도 be-worker가 기존
Coordinator/SceneJobs/ReportJobs를 순서대로 실행한다. GPU 슬롯1이다.
같은 영상 해시의 기존 CLI 작업은 조회로 재사용하며 자동 재분석하지 않는다.
처리중 불확실 요청은 중복 제출/생성하지 않고 멈춘다. 근거 없는 정상 판정은 하지 않는다.

### 1. Mac — UI 코드 전송

```bash
cd .
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh \
  'umask 077; set -C; cat > /home/ubuntu/cctv-be-stage6-20260930-01.zip && echo stage6_uploaded' \
  < artifacts/be-worker/ec2-stage6-20260930-01/cctv-be-worker-ui.zip
```

예상 `stage6_uploaded`. 설치/모델호출 없음.

### 2. EC2 — 별도 릴리스·흐름 상태 테이블 준비

```bash
sudo python3 - <<'PY'
import hashlib
from pathlib import Path
import subprocess
import zipfile
archive = Path('/home/ubuntu/cctv-be-stage6-20260930-01.zip')
release = Path('/opt/cctv-be-stage6-20260930-01')
if hashlib.sha256(archive.read_bytes()).hexdigest() != 'c6aaf6b61d207b1649fb88dc50b6d080718a57ad9bd1e8b20e9778fd93e59605':
    raise SystemExit('ZIP hash mismatch; stop')
if not release.exists():
    release.mkdir(mode=0o755)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(release)
subprocess.run(['python3', str(release/'apps/be_worker/verify_bundle.py')],check=True)
subprocess.run(['python3', str(release/'apps/be_worker/prepare_stage6.py'),
                '--compose','/opt/cctv-stack/compose.yaml'],check=True)
PY
```

예상 번들38파일/flow_schema_ready/기존데이터불변/commit true. 기존 DB·벡터·spool 유지.
API·worker는 실제 기동 전이다. 테이블은 UI 요청의 진행상태와 worker 상태를 추가 저장한다.

### 3. EC2 — UI 이미지 빌드·서비스 기동

기존 BE 이미지를 재사용하고 Streamlit1.64.0 UI 이미지만 새로 빌드한다. 빌드는
패키지 다운로드를 포함하며 모델 가중치 다운로드/모델 호출은 없다.
worker는 UI가 접수한 작업만 자동 실행한다. 기존 완료 CLI 작업을 자동 재생성하지 않는다.

```bash
sudo bash <<'SH'
set -euo pipefail
set -a
source /etc/cctv/be-worker-v1.env
set +a
cd /opt/cctv-be-stage6-20260930-01
bash apps/be_worker/queue.sh service-build
bash apps/be_worker/queue.sh service-start
bash apps/be_worker/queue.sh service-status
SH
```

예상 be-api/be-worker/cctv-ui 실행. healthy/파일 존재만으로 기능통과로 표시하지 않는다.
DB·API는 호스트 포트를 공개하지 않고 UI만127.0.0.1:8501에 바인딩한다.

### 4. Mac — SSH 터널과 기존 완료 결과 화면 확인

```bash
cd .
bash artifacts/be-worker/ec2-stage6-20260930-01/tunnel.sh
```

성공 시 터미널은 조용히 열린 채 유지된다. Mac 브라우저에서 http://127.0.0.1:8501 을 연다.
포트가 사용 중이면 터널은 종료하며 다른 서버를 종료하지 않는다. 보안그룹 변경은 없다.
처음에는 기존5718c530… 작업을 선택해 검증된 클립/프레임·충돌 의심 설명·문서/출처·근거부족 보고를 확인한다.
새로고침/작업선택은 GET 조회만 발생해야 한다. 기존 영상을 다시 접수해도 기존 작업ID를 반환해야 한다.

### 단계6 남은 실제 검증

- 기존 영상 선택/버튼·새로고침/worker 재기동 후 작업·보고ID/호출수 불변.
- 새 시험 영상의 버튼1회 → 실제 Runpod 자동 S3 업로드 → 미디어검증 → 설명/보고/DB/화면까지 완료.
- 실패/응답 불확실/후보 없음은 각각 표시. 정상으로 단정하지 않음.
- Runpod 중지 시 대기열 유지·POST0, 복구 뒤 동일 요청1회만 제출.
- 생성 원문/인용/영상 의미와 화면 이해 가능성은 사용자 검수.
- 기존 완료 결과 재사용과 새 유료 모델 실행을 별도 기록한다.

동일 queue/service Compose와 Python 모듈을 로컬에서도 사용한다. EC2 검증 후 로컬용
BE_APP_DIR/BE_INPUT_DIR/BE_SPOOL_DIR/비밀 파일/DB 경로만 별도로 지정하며 DB/spool을 공유하거나 초기화하지 않는다.
현재 로컬 Compose 실행·실제 EC2 버튼 검증은 미실행이다. LangSmith는 처리의 필수 조건이 아니다.
