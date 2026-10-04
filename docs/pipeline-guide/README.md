# CCTV 분석 파이프라인

녹화 영상의 **BE 대기열 → Runpod X3D·YOLO → S3 근거 → Gemini 장면 설명 → PostgreSQL 저장**과 **문서 청킹 → 임베딩 → pgvector 검색**을 실행하고 확장하는 코드 저장소다.

[아키텍처·시퀀스](ARCHITECTURE.md)에서 서비스 역할과 요청 순서를 볼 수 있다. AWS 자원 생성은 별도 [cctv-infra](https://github.com/boramsam-zo/cctv-infra)의 Terraform으로 관리한다.

## 실행 환경 선택

같은 코드를 두 환경에서 사용한다. 저장소나 코드 버전을 나누지 않고, 실행 환경별 설정을 구분한다. **로컬과 EC2 모두 Docker Compose를 사용한다.**

| 구분 | A. 로컬 개발용 | B. EC2 공용 실행용 |
|---|---|---|
| BE·DB 실행 위치 | 개발자 Mac/Linux의 Docker Compose | AWS CPU EC2 안의 Docker Compose |
| 용도 | 개인 개발·기능 확인 | 팀 공용 시연·서버 운영 |
| 명령 실행 위치 | 개발 PC 터미널 | EC2에 접속한 터미널 |
| AWS 인증 | 개발자의 AWS CLI 프로필 | EC2 인스턴스 역할, 컨테이너 접근 확인 |
| DB·작업 파일 | 개발 PC의 지정 폴더 | EC2의 영속 디스크에 지정한 폴더 |
| 인프라 준비 | 실행 중인 로컬 Docker 엔진 | cctv-infra의 Terraform으로 AWS 구성 관리 |

A는 아래 2절의 로컬 절차로 시작하고, B는 같은 절의 EC2 배포 항목을 준비한다. 이후 Runpod 제출·polling·S3 회수·Gemini 설명·문서 검색 코드는 공통이다. Runpod GPU 서비스는 두 환경 모두 모델 담당자가 운영한다. 실행 환경 간 DB·작업 파일은 자동으로 동기화되지 않는다.

두 환경을 따로 준비하고 실행할 수 있지만 BE와 RAG를 각각 다른 코드로 구현할 필요는 없다. 환경별 경로·인증·접속 DB를 설정한 뒤 같은 `queue.sh`와 RAG의 `compose.sh`를 사용한다. 요청 형식과 처리 로직은 공통이며, 검색 결과는 해당 DB의 문서·벡터에 따라 달라진다. 같은 요청이라도 생성 설명이 글자까지 같다는 뜻은 아니다.

## 1. 코드 구성

| 경로 | 역할 |
|---|---|
| `apps/be_worker/` | PostgreSQL 대기열, Runpod 제출·polling, S3 인계, VLM·DB 연결 |
| `apps/runpod_worker/` | 인증 API, GPU X3D·YOLO 추론, CPU 클립·프레임 생성, S3 업로드 |
| `apps/runpod_worker/handoff/` | 기존 배포 묶음에서 가져온 모델 추론·시간 구간 코드 |
| `apps/scene_description/` | 실제 근거 미디어 검증, Gemini 호출, 사고 의심·유형 검증, DB 저장 |
| `apps/rag_index/` | 청킹, Gemini 임베딩, pgvector 적재·검색 |
| `infra/compose/rag/` | PostgreSQL·pgvector와 내부 Docker 네트워크 |
| `scripts/` | 새 로컬 환경 준비, Runpod ZIP 생성, 합성 테스트 |
| `examples/` | 설정 예제와 합성 문서·청크 JSONL |
| `models/expected.json` | 모델 담당자가 관리하는 가중치 파일명·크기·SHA256. BE API 연동 시 다운로드 불필요 |

명령은 단계별 CLI다. `dispatch` 다음에 `collect`, `scene-fetch`, `scene-describe`를 각각 실행한다. 문서 검색도 별도 CLI이며, 장면 설명에서 검색 문장을 만들고 최종 보고를 생성하는 코드는 팀의 후속 연결 지점이다. 목표 도식의 웹 화면·접수 API는 이 저장소에 포함하지 않는다.

## 2. 환경별 BE·DB 준비

### A. 로컬 개발용 — Mac 또는 Linux 터미널

필요 도구: Python 3.12 이상, Docker Compose v2, 실행 중인 Docker 엔진. S3 접근에는 기존 AWS CLI v2 프로필을 사용한다. 모든 명령은 저장소 루트에서 실행한다.

아래는 **새 개발 환경**을 만드는 절차다. 기존 작업을 이어갈 때에는 그 작업의 DB·spool·비밀 설정을 함께 유지한다. 새 환경을 만들면 기존 작업 이력이 자동으로 들어오지 않는다.

```bash
python3 scripts/prepare_pipeline_local.py
source runtime/local/use-local.sh

docker compose -f "$CCTV_COMPOSE_FILE" up -d --wait db
bash apps/be_worker/queue.sh schema
bash apps/be_worker/queue.sh scene-schema
bash apps/be_worker/queue.sh build
bash apps/rag_index/compose.sh build
bash apps/rag_index/compose.sh schema
```

설정 스크립트는 `runtime/local/`에 DB 비밀번호·입력·spool 경로를 준비하며 서비스를 시작하지 않는다. 같은 경로에서 다시 실행해도 DB 비밀번호를 바꾸지 않는다. 새 터미널에서는 `source runtime/local/use-local.sh`부터 실행한다.

DB는 외부 5432 포트를 열지 않는다. DB 파일은 `runtime/local/data/postgresql`, BE 실행 기록·근거·설명은 `runtime/local/spool`에 남는다. 개발 종료 시 `docker compose -f "$CCTV_COMPOSE_FILE" stop db`로 중지할 수 있다.

### B. EC2 공용 실행용 — EC2 터미널

Terraform으로 EC2·네트워크·인스턴스 역할을 구성한 뒤, EC2에 이 저장소와 BE 설정을 배포한다. Terraform의 서버 준비와 BE 애플리케이션 배포는 각각 수행한다. 이미 DB가 있는 서버에서는 기존 Compose 프로젝트·DB 폴더·비밀번호를 그대로 사용한다.

#### B-1. 개발 PC 터미널에서 기존 EC2 접속

아래 두 설정을 현재 EC2의 공인 IP와 본인이 보관한 SSH 개인 키 경로로 바꾼다. EC2는 실행 중이어야 하며, 해당 키와 접속 권한이 준비돼 있어야 한다. 개인 키는 공유 저장소에 넣지 않는다.

```bash
export CCTV_EC2_HOST='replace-with-current-ec2-public-ip'
export CCTV_EC2_SSH_KEY="$HOME/.ssh/replace-with-your-ec2-private-key"
test -f "$CCTV_EC2_SSH_KEY" &&
ssh -i "$CCTV_EC2_SSH_KEY" "ubuntu@$CCTV_EC2_HOST"
```

접속하면 프롬프트가 `ubuntu@ip-...:~$`처럼 바뀐다. 이미 EC2 터미널에 있다면 접속 명령은 생략하고 B-2부터 실행한다. `exit`하면 개발 PC 터미널로 돌아온다. 접속 실패나 서버 키 불일치는 연결 정보부터 확인한다.

#### B-2. EC2 터미널에서 기존 DB 상태 확인

다음은 cctv-infra로 준비한 기존 DB Compose 경로다. 다른 경로에 배포했다면 실제 경로를 사용한다.

```bash
sudo docker compose -f /opt/cctv-stack/compose.yaml ps --all
```

| 출력 | 의미와 다음 행동 |
|---|---|
| `db` 서비스가 `Up ... (healthy)` | DB 상태 확인 통과. B-3으로 진행 |
| `Exited` | 중지 이유를 확인. 단순 중지 상태라면 아래 재개 명령 사용 |
| `unhealthy` 또는 `Restarting` | DB 로그를 확인한 뒤 진행 |
| 서비스 없음·Compose 파일 없음 | 기존 배포 경로·프로젝트 확인 |

이 구성에서 `PORTS`의 `5432/tcp`는 컨테이너 내부 포트이며 외부 공개 포트를 뜻하지 않는다. `healthy`는 DB 상태 확인이고, BE 배포·Runpod 연결·문서 적재를 확인한 결과는 아니다.

DB가 단순 중지 상태인 경우에만:

```bash
sudo docker compose -f /opt/cctv-stack/compose.yaml start db
sudo docker compose -f /opt/cctv-stack/compose.yaml ps --all
```

상태가 정상이 아닐 때 로그 확인:

```bash
sudo docker compose -f /opt/cctv-stack/compose.yaml logs --tail 50 db
```

기존 DB 폴더와 비밀번호를 유지한다. 이미 정상 실행 중인 DB를 위해 Terraform apply나 DB 초기화를 반복하지 않는다.

#### B-3. EC2 터미널에서 기존 테이블 확인

다음 명령은 테이블 존재 여부만 조회한다. 영상 제출·Gemini 호출·DB 변경은 없다.

```bash
sudo docker compose -f /opt/cctv-stack/compose.yaml exec -T --user postgres db \
  psql -X -v ON_ERROR_STOP=1 -d cctv -c \
  "SELECT to_regclass('cctv_jobs.queue') AS be_queue,
          to_regclass('cctv_scene.descriptions') AS scene_descriptions,
          to_regclass('cctv_rag.chunks') AS rag_chunks;"
```

각 열에 테이블 이름이 나오면 해당 테이블이 존재한다. 빈칸은 해당 테이블이 아직 없다는 뜻이며, 기존 DB 전체가 사라진 것으로 해석하지 않는다. 이 조회는 테이블 구조의 최신 버전이나 데이터 행 수까지 검증하지 않는다. 필요한 스키마는 아래 BE 코드·경로 배포 후 해당 스키마 명령으로 적용한다.

#### B-4. BE 코드와 실행 설정 준비

DB 확인 다음에는 아래 항목을 준비한다. 개발 PC의 `runtime/local/use-local.sh`를 EC2에 그대로 적용하지 않고 EC2의 실제 경로와 기존 DB 설정에 맞춘다.

| 준비 항목 | EC2에 맞출 내용 |
|---|---|
| 실행 도구 | Python 3.12 이상, Docker Engine·Compose v2, 운영 계정의 실행 권한 |
| 코드·이미지 | 같은 저장소의 BE·장면 설명·RAG 코드, EC2 CPU 아키텍처에 맞는 이미지 빌드 |
| 기존 DB 연결 | `COMPOSE_PROJECT_NAME`, `CCTV_COMPOSE_FILE`, `CCTV_DATA_DIR`, `CCTV_SECRETS_DIR` |
| 입력·작업 경로 | `BE_INPUT_DIR`, `BE_SPOOL_DIR`, `BE_SECRETS_DIR`, `CCTV_INPUT_DIR`와 컨테이너 읽기/쓰기 권한 |
| API 설정 | 현재 Runpod HTTPS 주소·분석 토큰, Gemini 키를 비공개 파일로 준비 |
| S3 읽기 | 인스턴스 역할을 연결하고 실제 BE 컨테이너에서 역할 인증·근거 읽기를 확인 |
| 재개 | 필요한 기존 DB 기록과 spool을 함께 이관하고, 기존 벡터·원본을 보존 |

현재 Compose는 `BE_SECRETS_DIR`의 `aws_credentials`·`aws_config` 파일도 마운트한다. 역할 인증을 쓸 때는 `prepare_queue.py --directory "$BE_SECRETS_DIR"`로 필요한 파일과 분석 토큰을 준비할 수 있다. 이 명령은 없는 AWS 파일을 빈 파일로 만들며, 기존 파일의 자격 증명은 지우지 않는다. 역할을 사용하려면 정적 키가 남아 있지 않은지 확인하고 컨테이너의 인스턴스 메타데이터 접근을 검증한다. 로컬용 `--aws-profile` 내보내기는 EC2 역할 인증의 필수 단계가 아니다.

EC2용 경로를 설정한 뒤에는 공통 `queue.sh`·`compose.sh` 명령을 같은 방식으로 실행한다. 아래 `runtime/local/use-local.sh`는 A 환경의 예시이므로 B에서는 배포한 EC2 설정을 불러온다. BE 설정·스키마·이미지 준비가 완료된 뒤 해당 환경에서 health → 큐 조회 → 필요한 작업을 실행한다. EC2가 켜져 있어도 단계별 CLI가 자동으로 반복 실행되는 것은 아니며, 상시 실행·재시작 정책은 배포 시 별도로 구성한다.

## 3. BE에서 운영 중인 Runpod API 연결

Runpod GPU 서비스는 모델 담당자가 운영한다. BE 담당자는 전달받은 HTTPS 분석 API를 호출한다. 아래 공통 명령은 **선택한 BE 실행 환경의 터미널**에서 실행한다. A는 개발 PC, B는 배포한 EC2다. Runpod 설치·가중치 배포·GPU 변경은 모델 담당자가 관리한다. 저장소의 Runpod 소스는 처리 흐름과 입출력을 이해하는 참고 코드로 함께 보관한다.

### 모델 담당자에게 받을 정보

| 항목 | 용도 |
|---|---|
| Port 8000의 HTTPS API 주소 | 영상 제출·작업 상태 조회 |
| 분석 API Bearer 토큰 | 해당 API 인증. 비공개 경로로 전달받아 로컬 secret에 저장 |
| S3 버킷·리전·근거 prefix | 분석 결과 회수. 리전은 `ap-northeast-2`, prefix는 `cctv-evidence` |
| S3 읽기 권한 | BE가 해당 근거 경로의 `s3:GetObject`를 수행할 계정 또는 역할 |

BE에는 분석 API 토큰을 전달한다. Runpod 콘솔 계정·관리 API key·SSH 키는 이 연동의 필수 항목이 아니다. Gemini 설명까지 실행할 때에는 별도 Gemini API 키를 BE에 설정한다.

### 요청과 결과가 오가는 방식

| 순서 | BE 요청 / Runpod 처리 | 응답·결과 |
|---|---|---|
| 1 | `GET /health`로 인증·S3 모드 확인 | `status: ok`, `storage: s3` |
| 2 | `POST /jobs`에 MP4 바이트 전송 (`Content-Type: video/mp4`) | HTTP 202, `job_id`, `status: queued` |
| 3 | Runpod: X3D 후보 → YOLO 객체 → 원본 전후 클립·프레임 → S3 업로드 | GPU 분석 1건씩 처리, 근거와 manifest 저장 |
| 4 | `GET /jobs/{job_id}`로 주기적 조회 | `queued` → `running` → `succeeded` 또는 `failed` |
| 5 | 성공 응답의 `s3.bucket`, `s3.region`, `s3.manifest_key` 사용 | BE가 S3 JSON·클립·프레임을 검증해 회수 |
| 6 | BE에서 Gemini에 실제 근거 입력 | 설명 JSON 검증·DB 저장 |

모든 분석 API 요청에 Bearer 인증이 필요하다. 첫 POST 응답은 접수 결과이고, 분석 결과는 작업 ID로 이어서 조회한다. S3에는 후보 시각·객체 정보 JSON과 실제 미디어가 함께 있으며, BE에서 원본 영상을 다시 잘라낼 필요가 없다. 기존 `RunpodAPI`가 요청 형식·인증·상태 검증을 처리한다.

### BE 연결 설정

다음은 **A. 로컬 환경의 설정 예시**다. 모델 담당자가 전달한 분석 API 토큰을 숨김 입력으로 저장한다. `cctv`는 본인의 AWS CLI 프로필 이름으로, 버킷은 실제 전달받은 이름으로 바꾼다. B에서는 EC2용 경로·역할 인증을 준비하고 같은 configure 명령으로 접속 설정을 저장한다.

```bash
source runtime/local/use-local.sh
export BE_TARGET_CONFIG=/input/config.json
python3 apps/be_worker/prepare_queue.py \
  --directory "$BE_SECRETS_DIR" --aws-profile cctv
python3 apps/be_worker/worker.py configure \
  --output "$BE_INPUT_DIR/config.json" \
  --s3-bucket 'replace-with-your-private-bucket' --s3-region ap-northeast-2
```

`configure`의 입력 안내에 Runpod 8000 HTTPS 주소를 넣는다. 기존 설정은 덮어쓰지 않는다. 참고 형식은 `examples/runpod-config.json`이다. 임시 AWS 자격 증명이 만료되면 프로필을 갱신한 뒤 준비 명령을 다시 실행한다. health 성공은 실제 GPU 추론·S3 권한 검사까지 완료했다는 의미는 아니다.

분석 토큰은 Runpod 서버의 `.api-token`과 BE의 `$BE_SECRETS_DIR/runpod_worker_token`이 같은 값이어야 한다. `prepare_queue.py`는 기존 토큰 파일이 있으면 값을 바꾸지 않는다. 새 토큰을 전달받았을 때는 기존 파일을 비공개로 보관한 뒤 새 파일로 교체하고 권한을 600으로 둔다. 토큰 값·비밀 파일은 Git에 포함하지 않는다.

### 선택한 BE 환경에서 인증 연결 확인

위 설정과 같은 터미널에서 다음을 실행한다. 기존 BE 어댑터가 Bearer 인증과 전용 User-Agent를 붙여 GET /health를 한 번 보낸다. GPU 영상 제출·Gemini 호출·DB 변경은 없다.

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path
from apps.be_worker.common import WorkerError, config_checked, read_json
from apps.be_worker.runpod_api import RunpodAPI

try:
    config_path = Path(os.environ["BE_INPUT_DIR"]) / Path(os.environ["BE_TARGET_CONFIG"]).name
    config = config_checked(read_json(config_path))
    token = (Path(os.environ["BE_SECRETS_DIR"]) / "runpod_worker_token").read_text().strip()
    health = RunpodAPI(config["runpod_url"], token).health()
except WorkerError as exc:
    print(json.dumps({"error": exc.code, "jobs_submitted": 0}))
    raise SystemExit(1)
except (OSError, KeyError):
    print(json.dumps({"error": "check_local_paths_and_secret_file", "jobs_submitted": 0}))
    raise SystemExit(1)
print(json.dumps({"status": "connected", **health, "jobs_submitted": 0}, indent=2))
PY
```

`connected / storage: s3 / jobs_submitted: 0`이면 명령을 실행한 BE 호스트에서 해당 API에 인증된 요청을 보낸 것이다. 로컬에서 성공해도 EC2에서는 다시 확인한다. Runpod 안의 client.py health 성공과 외부 BE 연결 성공을 구분한다. `http_401`이면 현재 Pod 주소와 토큰 일치를 확인한다. 여기서 연결이 성공한 뒤 영상 접수를 진행한다.

### 기존 환경에서 다시 시작할 때

기존 DB·spool·비밀 파일을 유지하고, 같은 저장소 루트의 새 터미널에서 경로를 불러온다. 아래는 A의 예시이며 B에서는 EC2용 설정을 불러온다. config.json은 실제 사용할 설정 파일명에 맞춘다.

```bash
source runtime/local/use-local.sh
export BE_TARGET_CONFIG=/input/config.json
docker compose -f "$CCTV_COMPOSE_FILE" ps
bash apps/be_worker/queue.sh list
```

DB가 중지돼 있으면 기존 환경에서 `docker compose -f "$CCTV_COMPOSE_FILE" up -d --wait db`로 재개한다. 새 설정 환경을 만들어 이전 작업이 사라진 것으로 취급하지 않는다. DB 비밀번호와 spool도 기존 것을 사용한다. BE 호스트의 HTTP 연결 검사와 Docker 컨테이너·DB 실행 확인은 별개다.

새 Pod의 주소가 바뀌었으면 다른 파일명으로 configure하고 `BE_TARGET_CONFIG`를 새 파일명에 맞춘다. 새 영상은 그 설정으로 접수한다. 이미 DB에 있는 작업은 입력 config만 바꿔도 목적지가 바뀌지 않는다. `retarget`은 제출 0회인 prepared 작업만 지원한다. 진행 중·응답 불명 작업은 기존 작업 ID/결과부터 확인하고, S3 인계 완료 작업은 기존 근거로 후속 처리한다.

| 재개 시 달라진 항목 | BE에서 할 일 |
|---|---|
| 같은 주소·같은 토큰 | 기존 설정으로 health 확인 |
| 새 Pod 주소 | 새 config 저장·선택 후 health 확인 |
| 새 분석 토큰 | 비공개 토큰 파일 교체 후 health 확인 |
| S3 자격 증명 만료 | AWS 인증을 갱신하고 prepare_queue로 다시 준비 |

테스트 시간은 GPU 운영자와 맞춘다. 새 접수를 멈추고 모든 제출 작업의 S3 업로드와 BE 인계를 확인한 뒤 운영자가 GPU를 중지한다. 이후 S3 근거를 사용하는 Gemini·RAG 단계는 BE에서 계속할 수 있다. 이 코드에는 Pod 자동 시작·중지 기능이 없다.

### 로컬 BE와 공용 EC2

동료 PC의 BE도 같은 Runpod HTTPS API를 호출할 수 있다. 공동 시연·상시 실행에서는 **CPU EC2에 BE·PostgreSQL·spool을 함께 두고** 공용 대기열과 결과를 관리한다. EC2 재시작 후에는 이 코드와 비밀 설정·DB/spool 연결을 배포하고 확인한다.

BE가 Runpod로 요청하는 데 필요한 것은 외부 HTTPS 연결이다. 이 통신만을 위해 EC2의 8000·5432를 외부에 열 필요는 없다. 별도 웹 접수 API를 제공할 때에는 그 서비스의 접근 설정을 구성한다. EC2 인스턴스 역할로 S3를 읽는 배포에서는 컨테이너의 역할 자격 증명 접근도 확인한다.

## 4. 영상 한 건 실행 — 공통

분석할 MP4를 선택한 BE 호스트의 `$BE_INPUT_DIR/A.mp4`에 둔다. A의 기본 경로는 `runtime/local/input/A.mp4`이며 B는 EC2에 배포한 입력 경로를 사용한다. 현재 입력 상한은 120초·200MiB다. 영상은 BE가 Runpod에 직접 제출한다. S3에는 Runpod가 만든 근거 클립·프레임·JSON·manifest를 저장한다.

```bash
bash apps/be_worker/queue.sh enqueue \
  --video /input/A.mp4 --config "$BE_TARGET_CONFIG" --request-key demo-a-v1
bash apps/be_worker/queue.sh dispatch --seconds 300 --slots 1
bash apps/be_worker/queue.sh list
bash apps/be_worker/queue.sh collect --seconds 60
bash apps/be_worker/queue.sh list
```

`/input`은 컨테이너 안의 경로다. `request-key`는 동일 접수의 중복을 막는 값이며 새 영상에는 다른 값을 준다. 여러 영상을 enqueue할 수 있고 `--slots 1`은 Runpod에 한 건씩 배정한다. 300초는 이번 dispatch의 대기 상한이지 GPU 작업 제한 시간이 아니다. 상한 뒤에는 저장된 상태로 dispatch를 이어간다.

| 출력 위치·상태 | 뜻 |
|---|---|
| 연결 검사: connected | 주소·토큰·S3 모드 확인. 영상 제출 없음 |
| BE: prepared | PostgreSQL 대기열에 접수됨 |
| BE: waiting / Runpod: queued·running | Runpod 접수 완료, 분석 완료 대기 |
| BE: evidence_ready | Runpod 성공·S3 저장 참조 확인 |
| BE: handoff_ready | BE가 S3 메타데이터를 검증하고 사건별 인계 준비 |
| 사건: media_verified | 실제 클립·프레임 다운로드와 해시 검증 완료 |
| 사건: description_stored | Gemini 설명 결과 검증·DB 저장 완료 |

`handoff_ready`까지는 메타데이터 인계이며, 실제 클립·프레임은 다음 scene-fetch에서 내려받는다. `dispatch`가 끝났다고 Gemini까지 자동 실행된 것은 아니다.

`handoff_ready`와 `event_count`가 있으면 출력의 실제 `worker_job_id`를 다음 명령에 사용한다. `no_candidates`이면 VLM에 보낼 사건 근거가 없어 이 단계에서 끝난다. 분석 실패를 사고 없음으로 취급하지 않는다.

```bash
export WORKER_JOB_ID='여기에_출력된_worker_job_id'
export EVENT_ID='event_000'

bash apps/be_worker/queue.sh scene-fetch \
  --worker-job-id "$WORKER_JOB_ID" --event-id "$EVENT_ID"

python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR" --gemini
bash apps/be_worker/queue.sh scene-describe \
  --worker-job-id "$WORKER_JOB_ID" --event-id "$EVENT_ID" \
  --model gemini-3.5-flash-lite
```

`scene-fetch`는 S3에서 실제 클립·프레임을 받고 SHA256을 검증한다. `scene-describe`는 Gemini API를 호출하고 검증된 결과를 `cctv_scene.descriptions`에 저장한다. 생성 모델 ID는 계정에서 사용할 영상 입력 지원 모델에 맞춘다. API 호출에는 계정 요금과 할당량이 적용된다.

설명 결과의 `accident_suspicion`과 `incident_types`는 서로 다른 필드다. 다른 차량과의 충돌이 관측되지 않아도 차량 전도 같은 사고 유형이 있을 수 있다. 설명·관측 근거를 최종 사고 확정 판정과 구분하여 사용한다.

저장된 실행 흐름은 네트워크 요청 없이 확인할 수 있다.

```bash
python3 apps/be_worker/flow_status.py \
  --spool "$BE_SPOOL_DIR" --worker-job-id "$WORKER_JOB_ID"
```

실패·응답 불명 상태에서는 저장된 요청·응답과 작업 ID를 먼저 확인한다. GPU 재접수나 Gemini 반복 호출로 복구하지 않는다. S3 업로드만 실패했다면 모델 담당자가 기존 결과를 업로드해 복구한 뒤, BE에서 `recover-s3 --worker-job-id …`로 회수한다. Gemini 성공 후 DB 저장만 실패한 경우에는 기존 결과를 재사용한다.

## 5. 문서 청킹·임베딩·검색 — 공통

선택한 BE 호스트에서 문서 입력·출력 경로와 RAG 이미지·스키마·DB 연결을 준비한 뒤 실행한다. 이미 적재한 문서 벡터는 재사용한다.

`examples/documents.jsonl`은 형식 검사에 쓰는 **합성 문서**다. 실제 법령·매뉴얼은 담당자에게 원문과 문서 ID·제목·조항/페이지·출처 URL·시행일·버전을 받는다. `document_id`는 버전별로 구분하고, 폐지·개정 전 자료에는 `effective_to`(종료일 미포함)를 지정한다.

문서 JSONL은 한 줄에 문서 하나, 각 문서의 `sections`에는 `section`과 원문 `content`를 넣는다. PDF·웹 수집/텍스트 추출은 입력 전에 수행한다. 청킹 CLI는 섹션 경계를 유지하며 긴 본문을 문자 수로 나누고 원문 위치와 SHA256을 남긴다. 법령 의미에 따른 자동 해석·OCR은 하지 않는다.

```bash
python3 apps/rag_index/chunk.py \
  --input examples/documents.jsonl --output "$CCTV_INPUT_DIR/chunks.jsonl"
bash apps/rag_index/compose.sh check --input /input/chunks.jsonl
```

이미 청크 JSONL을 전달받았다면 첫 명령을 건너뛰고 `check`로 형식을 검사한다. 필수 필드는 `chunk_id`, `document_id`, `content`, `embedding_input`, `content_sha256`, `source_url`, `effective_from`이며, 제목·조항·버전 정보도 함께 보관한다. 예시는 `examples/chunks.jsonl`이다.

Gemini 키를 준비한 뒤 문서 임베딩을 생성한다. 문서 벡터는 저장해 재사용하며 검색마다 문서 전체를 다시 임베딩하지 않는다.

```bash
python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR" --gemini
bash apps/rag_index/compose.sh embed \
  --input /input/chunks.jsonl --output /output/embedded-v1.jsonl
bash apps/rag_index/compose.sh load --input /output/embedded-v1.jsonl
bash apps/rag_index/compose.sh search \
  --query '차량 전도가 관측된 상황에서 확인할 사항' --date 2026-09-29
```

`gemini-embedding-001`, 768차원, 동일 프로필을 문서와 검색 질의에 사용한다. 마지막 명령은 **질의 임베딩 1회 → pgvector 유사도 검색 → 본문·출처·거리 JSON**을 반환한다. `--date`는 검색에 적용할 실제 기준일로 지정한다. 검색은 최대 5개이며, 자료가 2개이면 2개만 반환할 수 있다. 검색 결과는 법적 판단이나 최종 보고가 아니다.

임베딩은 기존 출력에 덮어쓰지 않는다. 성공한 JSONL과 `.complete.json`을 함께 유지하고, 부분 실패 파일을 완료된 벡터 자료로 적재하지 않는다.

## 6. 개발·확장 기준

- BE 제출·polling: `job_queue.Coordinator`, `runpod_api.RunpodAPI`.
- 미디어→설명→DB: `scene_jobs.SceneJobs` → `scene.describe` → `db` 저장 함수.
- 문서: `chunk.write_chunks` → `index.embed_file` → `index.load` → `index.search`.
- 후속 연결: 장면 결과에서 검색 문장을 구성해 `index.search`에 전달하고, 관련 본문·출처와 장면 설명으로 보고를 생성한다. 기존 결과의 ID·모델·출처를 유지한다.
- LangSmith: `tracing.py`의 허용 필드 기반 기록과 `worker.py trace`를 선택적으로 사용한다. 모든 큐 단계의 자동 추적을 의미하지 않는다.
- Pod 교체: 모델 담당자가 새 API 주소·토큰과 S3 준비 상태를 전달한다. BE DB/spool은 별도 보관한다. 아직 제출되지 않은 `prepared` 작업만 새 설정으로 `retarget`할 수 있다. 완료된 S3 근거는 기존 provider 작업 ID로 이어서 사용한다.

합성 테스트는 클라우드 호출·GPU 추론·운영 DB 변경 없이 실행한다. HTTP 테스트에는 로컬 loopback 포트가 필요하다.

```bash
python3 -m venv .venv-dev
.venv-dev/bin/pip install -r requirements-pipeline-dev.txt
.venv-dev/bin/python scripts/check_pipeline.py
```

코드를 바꾸면 테스트·실행 명령·입출력 계약도 함께 갱신한다. 키·실영상·가중치·DB/spool·Terraform state와 개인 운영 기록은 `runtime/` 등 비공개 경로에 두고 Git에 올리지 않는다. 진행 현황은 팀에서 별도로 공유한다.
