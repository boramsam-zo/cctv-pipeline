# 운영 절차: 현재 흐름 확인과 Runpod 재개

명령은 **문서 저장소가 아니라 실행 코드 저장소 루트**에서 사용한다.
`$BE_*`·`$CCTV_*`는 팀의 실제 환경값으로 준비한다. 비밀번호나 토큰을 명령 인수·문서에 넣지 않는다.
현재 구성은 단계별 CLI다. 아래 명령들을 하나의 무조건 실행 스크립트로 이어 붙이지 않는다.

## 1. 어디서 무엇을 실행하나?

| 위치 | 실행하는 것 |
|---|---|
| Mac 터미널 + Docker Desktop | BE 대기열, Runpod 제출/polling, S3 회수, Gemini 설명, DB 저장 |
| Runpod Jupyter의 Terminal | GPU 환경·모델 묶음 확인, 분석 API 시작, S3 업로드 인증 |
| 후속 EC2 터미널 | 배포 후 같은 BE/DB 업무를 상시 처리 |
| 이 문서 저장소 | 문서 열람·수정·Git 게시 |

현재 PostgreSQL은 Mac Docker에 있고 EC2에도 별도 DB 배포 이력이 있다. **Runpod를 다시 만든다고 이 DB들이 함께 초기화되지는 않는다.** 새 Pod의 로컬 작업 파일은 별개다.

## 2. 지금 어디서 멈췄는지 CLI로 보기

### A. 파일만 읽는 흐름 조회 — 모델 호출0

담당자가 제공한 비공개 환경 스크립트를 먼저 source한다. 이 스크립트는 실제 경로와 작업ID를 설정할 뿐 작업을 제출하지 않는다.

```bash
source "$CCTV_LOCAL_ENV"
python3 apps/be_worker/flow_status.py \
  --spool "$BE_SPOOL_DIR" \
  --worker-job-id "$BE_WORKER_JOB_ID"
```

`CCTV_LOCAL_ENV`는 담당자가 제공한 기존 환경 스크립트 경로다. Docker가 꺼져 있어도 파일이 있으면 조회할 수 있다.
전체 작업을 보려면 `--worker-job-id`를 빼고, JSON이 필요하면 `--json`을 추가한다.
이 명령은 **저장된 기록**만 읽는다. 현재 DB/S3 존재 여부, Pod 실행 여부, Gemini 가용성을 실시간 검증하지 않는다.

현재 확인된 출력의 요지는 다음과 같다.

```text
대기열 기록: handoff_ready
제출 의도 1회 / 상태 조회 5회
S3 업로드 기록: 있음 / 사건 1개
장면 요청 의도 3회 / 별도 텍스트 진단 1회
event_000: 미디어검증=True, 장면=description_failed, DB저장=False
오류: gemini_http_503
```

시도 횟수는 성공 건수나 청구 건수가 아니다. API 키·Pod URL·버킷 설정값은 이 출력에 포함하지 않는다.

### B. 실제 DB의 대기열 조회 — GPU/모델 호출0

Docker Desktop과 기존 DB가 실행 중이어야 한다.

```bash
docker compose -f "$CCTV_COMPOSE_FILE" ps
bash apps/be_worker/queue.sh list
```

DB 조회와 파일 기록이 다르면 제출을 멈추고 기존 영수증을 대조한다. DB를 새로 만들거나 상태를 SQL로 강제 초기화하지 않는다.

## 3. 한 건의 단계별 실행

최초 설치에는 담당자의 실행 코드 묶음, 기존 DB Compose 및 환경 스크립트, 비공개 토큰/AWS 파일이 필요하다.
처음 준비하는 DB에만 `bash apps/be_worker/queue.sh schema`, `scene-schema`로 테이블을 준비하고,
`bash apps/be_worker/queue.sh build`로 BE 이미지를 만든다. 기존 작업 재개 때 이 설치 과정을 반복할 필요는 없다.
문서 저장소에는 이 실행 파일이나 비밀 설정이 포함되지 않는다.

다음 표에서 **현재 상태에 맞는 한 단계만** 실행한다. `$BE_WORKER_JOB_ID`는 `enqueue` 응답의 BE ID이며 Runpod ID와 다르다.

| 조건 | 명령 | 기대 결과 / 외부 사용 |
|---|---|---|
| 준비된 새 MP4 접수 | `queue.sh enqueue` | DB/입력 사본만 저장 |
| `prepared` | `queue.sh dispatch --seconds 300 --slots 1` | Runpod 제출·polling. GPU 사용 발생 가능 |
| `evidence_ready` | `queue.sh collect --seconds 300` | S3 manifest/메타데이터 대조 |
| `handoff_ready`, 미디어 미회수 | `queue.sh scene-fetch` | S3 클립·프레임 다운로드/해시 검증 |
| `media_verified`, 새 설명 요청 | `queue.sh scene-describe` | Gemini 생성1회·검증·DB 저장 |
| `database_pending`, 결과 파일 있음 | 같은 `scene-describe` | 기존 설명을 재사용하여 DB 저장만 재개 |
| `description_failed` | 오류별 확인 | 무조건 재호출하지 않음 |

전체 명령 형식은 다음과 같다. 현재503 건에는 접수/분석 명령을 다시 실행하지 않는다.

```bash
# 실행 코드 저장소의 Mac 터미널. 기존 DB와 환경 설정이 준비된 경우.
# 새 영상 A.mp4는 호스트의 BE_INPUT_DIR 아래에 둔다.
python3 apps/be_worker/worker.py configure --output "$BE_INPUT_DIR/config-demo-v1.json"
bash apps/be_worker/queue.sh enqueue \
  --video /input/A.mp4 --config /input/config-demo-v1.json --request-key demo-a-v1
```

응답의 `worker_job_id`를 `BE_WORKER_JOB_ID`에 설정한다. `request-key`는 같은 접수를 중복 생성하지 않기 위한 값이다. 다른 영상이나 새 의도적 분석에는 다른 값을 쓴다. 기존 config 파일은 덮어쓰지 않는다.

```bash
bash apps/be_worker/queue.sh dispatch --seconds 300 --slots 1
bash apps/be_worker/queue.sh list
```

300초는 관찰 창이며 완료 보장이 아니다. `waiting`이면 같은 ID의 조회를 이어간다.
여러 영상을 접수하면 BE 기본 슬롯2는 Runpod 분석1/대기1이며 `--slots 1`은 BE에서1건만 배정한다.

```bash
# evidence_ready를 확인한 다음
bash apps/be_worker/queue.sh collect --seconds 300
# handoff_ready이고 사건이 있을 때
bash apps/be_worker/queue.sh scene-fetch \
  --worker-job-id "$BE_WORKER_JOB_ID" --event-id event_000
```

`event_000`은 첫 사건 예시다. 사건 목록을 확인해 해당 ID를 사용한다. `no_candidates`는 후보 없음 기록이며 정상 확정이나 분석 실패와 다르다.

```bash
# 첫 장면 설명 요청에 필요한 키 준비: 값은 숨김 입력
python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR" --gemini
# media_verified이고 아직 요청하지 않은 사건에 한 번 실행
bash apps/be_worker/queue.sh scene-describe \
  --worker-job-id "$BE_WORKER_JOB_ID" --event-id event_000
```

완료 기준은 `description_stored`·`database_loaded: true`다. `scene-probe`는 짧은 생성 진단이며 한 실패건에 한 번만 호출한다. 재실행은 저장된 결과를 보여준다. `scene-diagnose`는 모델정보 조회여서 생성 성공까지 검증하지 않는다.

## 4. 오류가 나면 어느 단계만 다시 하나?

| 오류·상태 | 조치 |
|---|---|
| Runpod `health` 연결 실패 | Pod와 :8000 API 프로세스 확인. 화면의 Ready만으로 추론 성공을 판단하지 않음 |
| 무인증401 | 인증이 필요하다는 응답. 분석 API 토큰을 사용하며 Runpod 계정 API key와 구분 |
| `storage: temporary_container_disk` | S3 환경변수를 설정하고 동일 터미널에서 API 프로세스 재시작 |
| GPU 성공·S3 업로드만 실패 | Runpod에서 기존 output에 `storage.py`만 실행 → BE `recover-s3` |
| `scene_attempt_incomplete...` | 이전 요청/응답/실패를 조사. 같은 실패ID 명령 반복은 새 호출을 만들지 않음 |
| `gemini_http_503` | 생성 가용성 확인. 최근 영상/짧은텍스트 모두503이라 추가반복 중단 |
| `database_pending` | 결과 파일을 유지하고 DB 저장만 재개 |

S3 업로드만 복구할 때, **Runpod Terminal**에서 원래 결과와 인증 설정이 있는 경우:

```bash
cd /workspace/cctv-runpod-a4000
.venv/bin/python storage.py \
  --output "runtime/jobs/$PROVIDER_JOB_ID/output" --job "$PROVIDER_JOB_ID"
```

성공 영수증을 받은 뒤 **Mac 터미널**에서:

```bash
bash apps/be_worker/queue.sh recover-s3 --worker-job-id "$BE_WORKER_JOB_ID"
```

이 복구는 GPU 분석을 다시 하지 않는다. 수동 업로드만으로 과거 Runpod API의 실패 상태가 자동 성공으로 바뀌지는 않는다.

## 5. Runpod 재시작·GPU 변경 시 판단표

| 변경 상황 | 보존 여부 / 필요한 조치 |
|---|---|
| API 프로세스만 재시작 | 파일이 남아 있으면 재사용. 이 구현은 진행 중 작업을 `service_restarted` 실패로 기록하고 자동 재추론하지 않음 |
| Pod stop/start, 임시 container disk만 사용 | 설치·가중치·토큰·로컬 결과가 사라질 수 있으므로 보존을 전제하지 않음 |
| 영속 볼륨이 연결된 기존 Pod | 실제 마운트·파일을 확인하고 환경/API를 점검. `/workspace`라는 경로 이름만으로 영속성을 판단하지 않음 |
| 다른 GPU/새 Pod | 검증된 묶음 설치 → GPU 확인 → AWS/S3 설정 → API 시작 → 새 URL/토큰 연결 |
| S3 근거까지 완료된 기존 작업 | 새 Pod로 옮길 필요 없음. 기존 S3 근거로 VLM/DB를 이어감 |
| 접수 전 `prepared`, 제출0회 | 인증된 새 Pod health 확인 후 `retarget` 가능 |
| 이미 접수한 작업이 진행 중/완료 여부 불명 | 새 Pod로 retarget 불가. 기존 작업의 종료·S3 결과부터 확인 |

Runpod는 container disk를 stop/restart 때 보존하지 않으며, 영속성은 선택한 볼륨에 따라 다르다.
[공식 저장소 설명](https://docs.runpod.io/pods/storage/types) · [Pod 관리](https://docs.runpod.io/pods/manage-pods)

### 5-A. 새 GPU Pod 준비 — Runpod Jupyter Terminal

검증된 `cctv-runpod-a4000.zip`을 `/workspace`에 업로드한다. `fd20cca0-recovery.zip` 같은 이전 결과 복구 ZIP은 실행 코드·가중치 설치 묶음을 대신하지 못한다.
폴더 이름의 a4000은 기존 배포 이름이며 GPU 선택을 하드코딩한 뜻은 아니다.
기존 폴더가 있으면 아래 해제 명령을 반복해 덮어쓰지 않고 먼저 내용을 확인한다.

```bash
cd /workspace
# 기존 /workspace/cctv-runpod-a4000 폴더가 없는 새 Pod에서만
python3 -m zipfile -e cctv-runpod-a4000.zip /workspace
cd /workspace/cctv-runpod-a4000
python3 verify_bundle.py
nvidia-smi --query-gpu=name,memory.total --format=csv
bash setup.sh
```

현재 배포는 Linux/CUDA, torch2.8.0·torchvision0.23.0, 동시 GPU 분석1건, batch8을 확인한다.
RTX A4000 및 RTX2000 Ada에서 실제 실행 이력이 있지만 모든 GPU의 속도·메모리 적합성을 검증한 것은 아니다.
GPU가 바뀌어도 같은 가중치를 사용할 수 있으며 재학습은 필수 단계가 아니다. 다른 CUDA 호환성이나 OOM이 생기면 별도 설정 변경·새 묶음 검증이 필요하다. CPU fallback은 현재 코드에서 비활성화돼 있다.

아래 인증 입력은 **Runpod의 Bash 터미널**에서 한다. 값은 채팅/문서에 보내지 않는다.

```bash
read -rsp 'AWS Access Key ID: ' AWS_ACCESS_KEY_ID; echo
read -rsp 'AWS Secret Access Key: ' AWS_SECRET_ACCESS_KEY; echo
read -rsp 'Session Token (임시 키일 때만): ' AWS_SESSION_TOKEN; echo
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
if [ -n "$AWS_SESSION_TOKEN" ]; then export AWS_SESSION_TOKEN; else unset AWS_SESSION_TOKEN; fi
read -rp 'S3 버킷 이름: ' S3_BUCKET
export S3_BUCKET
export AWS_DEFAULT_REGION=ap-northeast-2
export S3_PREFIX=cctv-evidence
export EVIDENCE_STORAGE=s3
.venv/bin/python - <<'PY_CHECK'
import boto3
from botocore.exceptions import BotoCoreError, ClientError
try:
    boto3.client('sts', region_name='ap-northeast-2').get_caller_identity()
    print('AWS 인증 성공; S3 쓰기 권한은 별도 확인 필요')
except (BotoCoreError, ClientError):
    raise SystemExit('AWS 인증 실패; 키 쌍·세션 토큰 확인')
PY_CHECK
```

`AWS 인증 성공`을 확인한 뒤 **같은 터미널**에서 시작한다. 인증 실패면 키 쌍/세션 토큰부터 확인한다.

```bash
bash start.sh
```

AWS/S3 환경변수는 API를 시작하는 **같은 셸**에 있어야 자식 업로드 프로세스도 사용한다.
이미 같은 API가 실행 중이면 원래 터미널에서 Ctrl+C로 종료 후 위 설정으로 다시 시작한다.
서비스 잠금 파일을 지워서 두 프로세스를 띄우지 않는다. 이 명령은 터미널 전경 실행이며 상시 서비스 등록은 후속이다.

별도 **Runpod Terminal**에서:

```bash
cd /workspace/cctv-runpod-a4000
.venv/bin/python client.py health
.venv/bin/python worker.py --check-only
```

`health`의 `status: ok`, `storage: s3`, `gpu_concurrency: 1`을 확인한다. health는 GPU 추론/S3 쓰기를 검증하지 않는다.
새 GPU 환경 확인 후 새 영상 한 건의 분석·S3 검증은 사용자가 별도로 실행한다.

### 5-B. 새 Pod 주소와 토큰 연결 — Mac 터미널

Runpod Connect 화면에서 HTTP8000 URL과 **직접 TCP SSH** 호스트·포트를 확인한다.
새 Pod의 분석 API 토큰은 `/workspace/cctv-runpod-a4000/.api-token`에 있다.
Runpod 계정 Credentials의 API key를 이 토큰 대신 사용하지 않는다.
[접속 방식](https://docs.runpod.io/pods/connect-to-a-pod)

새 Pod별 비밀 폴더를 선택해 기존 토큰을 보존한다. 아래 `secrets-new-pod-v1` 폴더가 이미 있으면 새 이름을 선택한다.
아래 변수의 실제 값은 콘솔/담당자에게 받는다.
`RUNPOD_SSH_KEY`는 Mac 개인 SSH 키 파일 경로이며 문서에 내용을 기록하지 않는다.

```bash
export BE_SECRETS_DIR="$PWD/artifacts/be-worker/queue-local-v1/secrets-new-pod-v1"
mkdir -m 700 "$BE_SECRETS_DIR" &&
scp -P "$RUNPOD_SSH_PORT" -i "$RUNPOD_SSH_KEY" \
  "root@${RUNPOD_SSH_HOST}:/workspace/cctv-runpod-a4000/.api-token" \
  "$BE_SECRETS_DIR/runpod_worker_token" &&
chmod 600 "$BE_SECRETS_DIR/runpod_worker_token" &&
python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR" --aws-profile cctv
```

토큰 복사와 비공개 파일 준비가 성공한 뒤 새 연결 설정을 만든다.

```bash
python3 apps/be_worker/worker.py configure --output "$BE_INPUT_DIR/config-new-pod-v1.json"
```

`cctv`는 기존 AWS CLI profile의 예시다. 설정 질문에는 새 :8000 HTTPS URL과 기존 버킷/서울 리전을 입력한다.
새 비밀 폴더에서 VLM도 호출할 경우 Gemini 키를 안전하게 준비한다(`prepare_queue.py --gemini`).
기존 DB·spool 경로와 Compose 프로젝트 이름은 유지하고, 비공개 환경 스크립트의 경로만 맞춘다.

**제출0회·prepared인 기존 대기 작업에만:**

```bash
bash apps/be_worker/queue.sh retarget \
  --worker-job-id "$BE_WORKER_JOB_ID" --config /input/config-new-pod-v1.json
```

`retargeted`, `health_checked: true`, `jobs_submitted: 0`을 확인한 후 dispatch한다.
이 명령은 S3 목적지와 입력을 바꾸지 않으며, 다른 활성 작업이 남아 있으면 중단된다.
새 영상은 새 설정 파일로 `enqueue`한다.

**이미 작업 ID를 받은 건에는 retarget하지 않는다.** 기존 S3 근거가 있으면 복구/회수 후 VLM만 진행한다.
기존 Pod의 작업 파일이 사라지고 처리 여부도 불명이라면 현재 CLI는 그 작업을 새 Pod로 이관해 중간부터 이어가는 기능을 제공하지 않는다.
원본/이전 영수증을 보존하고 종료 상태를 확인해야 한다. 작업이 영구 유실돼 active 상태로 남으면 상태 정리 기능 보완 전 자동 재접수를 보류한다.

## 6. EC2로 옮길 때

Runpod 변경과 BE 호스트 변경은 다른 작업이다. Mac→EC2 이관 때는 다음을 확인한다.

1. Linux/CPU 아키텍처에 맞는 BE 이미지와 코드 버전.
2. EC2 경로의 Compose·DB·작업 폴더. **기존 작업 재개에는 DB와 spool이 함께 필요하다.**
3. Runpod 토큰·Gemini 키와 EC2의 S3 접근 권한. Mac 개인 profile을 그대로 운영 배포 방식으로 가정하지 않는다.
4. 제출/polling/회수/설명의 상시 실행과 재시작 정책. 현재 CLI 명령이 자동 서비스 등록을 뜻하지 않는다.
5. 기존 S3 근거1건으로 VLM/DB 연결, 별도 새 영상1건으로 제출부터 검증.

AWS 생성·역할·네트워크·디스크는 인프라 저장소, 단계 처리/재개는 실행 코드 저장소가 담당한다.
Terraform 파일만으로 기존 DB 내용·비밀키·작업 파일이 복구되지는 않는다. DB 백업은 VLM 선행조건이 아닌 운영 과제다.
