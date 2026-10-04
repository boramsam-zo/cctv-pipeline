# PostgreSQL 대기열 → Runpod → S3 근거 인계

## 기존 EC2에 배포하여 실행 — 2026-09-29

사용자 EC2 조회 결과: DB healthy, RAG 청크2개, BE 대기열·장면 설명 테이블은 아직 없음. 로컬에서는 같은 BE 코드로 장면 설명·DB 저장까지 성공했고 EC2 앱 배포는 별도다. [EC2 배포·영상 실행 절차](../../infra/terraform/cpu-rag/README.md#be-배포와-영상-한-건-실행--기존-ec2용)에 Mac 코드 전송→EC2 검증·경로 설정→두 테이블 추가→비밀 설정/빌드→컨테이너 연결 검사→영상 접수/설명/DB 조회를 정리했다.

먼저 해당 절의 1~3번으로 코드·테이블을 준비한다. 기존 RAG 데이터와 로컬 성공 자료는 보존한다. 그다음 IAM 역할 및 컨테이너 S3 읽기 확인을 거쳐 영상 한 건을 실행한다. 단계별 CLI의 상시 자동 연결이나 RAG 보고 완료로 표시하지 않는다. CPU 배포 ZIP은 artifacts/be-worker/ec2-deploy-20260929-01/cctv-be-worker.zip이며 클라우드 배포·실행은 사용자 CLI로 진행한다.

## 새 환경 첫 사건 완료 — 2026-09-29

새 worker_job_id2254e2c45b724c9aab0878f169d9cebf/event_000은 사용자 실행으로 description_stored/database_loaded:true/media_bytes_verified:true/error:null까지 완료했다. 장면 설명 요청 의도1회, 결과는suspected/rollover다. 원본은새spool7637328787304ae989f8847bbc99b3f1/scenes/event_000에있으며database.json의committed와결과해시를대조했다. 원본result.json은DB적재전상태를보존하고후속DB영수증이저장완료를증명한다. RAG/보고는아직실행하지않았다.

한건의연결시험을마쳤으므로같은영상재접수는필수가아니다. 새영상/다건큐시험을할때같은환경에서새파일과고유request-key로추가한다. DB/스키마를다시초기화할필요없다. 다음권고는현재DB/spool보존후EC2배포준비이며,아래새시작절차는이미준비한환경의실행참고다.

## 새 로컬 대기열로 처음부터 시작 — 2026-09-29

작업 상태는 PostgreSQL의 대기열과 spool 파일에 함께 저장된다. 명령 실행용 worker 컨테이너만 지워도 기록은 남는다. 사용자의 새 시작 요청에 따라 **기존 자료를 보관하고 별도 Compose 프로젝트의 빈 DB·작업 폴더를 사용하는 환경**을 준비했다. 이전 기록을 삭제한 것은 아니다. 아래 이전 Pod 이름이 들어간 source 스크립트는 과거 환경 재개용이며 이번 새 시작에서는 사용하지 않는다.

- 새 프로젝트: cctv-be-fresh-v1
- 새 경로: artifacts/be-worker/queue-fresh-v1/
- Runpod 주소/S3 설정, 분석 토큰·Gemini 키·AWS 인증 파일은 현재 설정에서 비공개로 복사했다. DB 비밀번호는 새로 생성했다.
- 새 DB 데이터 폴더와 spool은 비어 있다. 아직 DB 서비스를 시작하거나 영상을 접수하지 않았다.
- S3/Runpod/기존 DB·spool은 보존했다. 새 DB에는 기존 법령 벡터가 자동으로 들어가지 않으므로 RAG 검증 시 저장해 둔 임베딩 파일을 별도 적재한다.

아래는 **Mac 터미널**용이다. Docker Desktop을 켜고, 영상 제출 시 Runpod API/GPU도 준비한다. 실행 중인 이전 dispatch/collect가 있다면 해당 터미널에서 먼저 종료하여 이전 큐에 새 요청을 보내지 않도록 한다.

### 1. 새 환경 선택

```bash
cd .
source artifacts/be-worker/queue-fresh-v1/use-fresh.sh
```

새 터미널에서도 이 source를 사용한다. 이전 작업 ID 환경변수는 해제하고 BE_TARGET_CONFIG는 새 /input/config.json을 선택한다. 같은 Pod를 사용하므로 configure나 토큰 입력을 반복할 필요가 없다. Pod를 실제 교체했다면 새 환경의 주소/토큰부터 갱신한다.

### 2. 새 환경에서 Runpod 인증 확인

```bash
python3 artifacts/be-worker/queue-fresh-v1/check-connection.py
```

기존 RunpodAPI 어댑터로 인증 GET1회만 보낸다. connected/storage:s3/jobs_submitted:0이면 통과다. 이것은 GPU 추론·S3 권한·Gemini 검사가 아니다. 오류 시 그 오류부터 해결하고 다음 접수는 하지 않는다.

### 3. 새 DB와 큐 준비

```bash
docker compose -f "$CCTV_COMPOSE_FILE" up -d --wait db &&
bash apps/be_worker/queue.sh schema &&
bash apps/be_worker/queue.sh scene-schema &&
bash apps/be_worker/queue.sh build &&
bash apps/be_worker/queue.sh list
```

마지막 출력이 `[]`이면 새 대기열에 등록된 작업이 없다. 이전 A/B가 보이면 새 환경 source 여부를 확인한다. 이미지 빌드는 기존 코드/이미지를 사용하며 없으면 패키지/이미지 다운로드가 필요할 수 있다. 이 단계에서는 영상/Gemini 요청이 없다.

### 4. 새 영상 한 건 접수

분석할 영상을 artifacts/be-worker/queue-fresh-v1/input/A.mp4에 복사한다. 처음부터 끝까지 설명 경로를 확인하려면 후보가 발생할 것으로 확인한 영상을 선택할 수 있으나, 이는 모델의 독립 정확도 평가와 다르다. 과거 B처럼 후보가 없으면 no_candidates로 끝나는 것이 이 파이프라인의 동작이다. 입력 상한은120초/200MiB다.

```bash
bash apps/be_worker/queue.sh enqueue \
  --video /input/A.mp4 --config "$BE_TARGET_CONFIG" \
  --request-key fresh-a-v1
```

prepared와 새 worker_job_id를 확인한다. 같은 접수 명령의 재실행은 같은 request-key로 중복을 방지한다. 다른 영상은 다른 request-key를 사용한다. 기존 작업 ID는 새 DB에 없으므로 가져와 사용하지 않는다.

### 5. Runpod 분석·S3 결과 회수

여기부터 새 GPU 분석·S3 업로드가 실행된다. 기존 동일 영상이 있더라도 새 DB의 새 접수는 새로운 분석 작업이므로 사용량이 발생한다.

```bash
bash apps/be_worker/queue.sh dispatch --seconds 300 --slots 1
bash apps/be_worker/queue.sh list
```

waiting이면 기존 새작업의 dispatch를 이어간다. evidence_ready이면:

```bash
bash apps/be_worker/queue.sh collect --seconds 60
bash apps/be_worker/queue.sh list
```

no_candidates/event_count0이면 여기서 종료한다. handoff_ready이고 사건이 있으면 다음으로 간다.

### 6. 후보 사건의 실제 미디어·Gemini 설명·DB 저장

새 목록에 나온 실제 worker_job_id와 사건 ID를 지정한다. event_000은 첫 사건이 있는 경우에 사용한다.

```bash
export WORKER_JOB_ID='여기에_새_worker_job_id'
export EVENT_ID='event_000'
bash apps/be_worker/queue.sh scene-fetch \
  --worker-job-id "$WORKER_JOB_ID" --event-id "$EVENT_ID"
```

media_verified 확인 후:

```bash
bash apps/be_worker/queue.sh scene-describe \
  --worker-job-id "$WORKER_JOB_ID" --event-id "$EVENT_ID"
```

복사해 둔 Gemini 키를 사용해 생성1회를 요청한다. description_stored/database_loaded:true면 완료다. 새 사건의 첫 설명에는 retry-failed-analysis-id가 없다. 실패하면 저장된 새 실패 기록을 확인한다. 계정 사용량이 발생하며 새 요청을 무조건 반복하지 않는다. AWS 인증이 만료된 경우 새 BE_SECRETS_DIR에서 prepare_queue.py --aws-profile cctv로 갱신한다.

이하 내용은 기존 환경의 실행 이력·복구 절차다. 새 실행을 시작할 때는 위 절차와 새 작업 ID를 기준으로 한다.

## 텍스트 생성 성공 후 영상 설명 재개 — 2026-09-29

사용자 출력과 최신 probe 원본에서 text_generation_ok/HTTP200/새생성1회/입력6·출력2·총8토큰을 확인했다. 영상은 보내지 않았고 DB는 변경하지 않았다. 현재 동일 모델/키의 텍스트 생성 성공이며 기존 사건의 description_failed 상태는 그대로다.

기존 검증 미디어를 사용하는 장면 설명1회와 결과 검증·DB 저장을 이어간다. 최신 실패 분석0a453f36-2ca0-4b15-9d91-89d9b41fcb9c, gemini-3.5-flash-lite/4FPS 설정 일치·기존 응답/결과 파일 없음·해당 재시도 폴더 없음 확인. **같은 Mac BE 터미널에서:**

```bash
bash apps/be_worker/queue.sh scene-describe \
  --worker-job-id e2a71c13191b4245a99fa1a9b1b60b6d \
  --event-id event_000 \
  --model gemini-3.5-flash-lite \
  --fps 4 \
  --retry-failed-analysis-id 0a453f36-2ca0-4b15-9d91-89d9b41fcb9c
```

Gemini 영상 생성 요청에 사용량이 발생할 수 있다. 성공 기준은 description_stored/database_loaded:true다. 기존 GPU 분석·S3 업로드를 반복하지 않고 이전 실패 원본을 보존한다. 같은 명령 반복은 새로운 추가 시도를 만들지 않는다. 텍스트 성공만으로 영상설명/DB까지 완료됐다고 하지 않으며 위 명령은 사용자 실행 대기다.

## 지금 로컬 실행 결과 확인 — 2026-09-29

사용자 dispatch 출력에서 기존 첫 작업은 handoff_ready + 저장된 description_failed/gemini_http_503, 두 번째 작업은 evidence_ready/Gemini0회다. dispatch는 Runpod 제출·조회만 수행하며 장면 설명을 호출하지 않는다. 첫 작업의 gemini_api_calls4는 기존 장면 요청 의도 누계이며 이번 실행에서4회 생성했다는 뜻이 아니다.

현재 선택된 첫 사건 실패 분석은 0a453f36-2ca0-4b15-9d91-89d9b41fcb9c다. 파일 조회 시 이 분석의 text-probes 폴더는 없었고, 이전 텍스트 진단은5914cb47…에 연결돼 있었다. 따라서 아래 명령은 현재 선택 상태에서는 새로운 고정 텍스트 생성1회를 요청한다. 같은 실패 분석에 다시 실행하면 저장된 영수증을 반환한다. 아래 과거의 “scene-probe가 기존503을 반환한다”는 안내는 당시 선택된 분석에 대한 이력이다.

**Mac의 현재 BE 환경을 불러온 터미널에서:**

```bash
bash apps/be_worker/queue.sh scene-probe \
  --worker-job-id e2a71c13191b4245a99fa1a9b1b60b6d \
  --event-id event_000
```

같은 모델·저장된 Gemini 키로 `Reply only with OK.`를 요청하며 영상 전송·GPU 재분석·업무 DB 변경은 없다. 생성 사용량은 발생할 수 있다. `generation_requests_this_run: 1`은 새 요청, 0은 저장 결과 재사용이다. `text_generation_ok`이면 짧은 텍스트 생성만 성공한 것이며 영상 입력은 별도로 확인한다. 새503이면 현재 해당 요청도 실패한 것으로 기록하고 연속 반복 대신 간격을 둔다. 에이전트가 생성 요청을 실행한 것은 아니다.

새 두 번째 작업은 이후 collect로 S3 메타데이터를 검증한 뒤 scene-fetch/describe로 이어간다. API 진단은 기존 사건으로 할 수 있으며 새 영상 제출은 필요 없다. EC2 배포 계획은 유지하되 이번 점검은 사용자 요청대로 기존 로컬 환경에서 수행한다.

## 현재 실행 기준 — EC2 배포

2026-09-29 사용자 요청으로 EC2의 Docker Compose에서 BE·DB를 실행하는 기준으로 전환했다. 사용자 출력에서 기존 EC2의 db가 `Up 17 hours (healthy)`인 것을 확인했다. 다음은 같은 EC2 터미널에서 기존 테이블을 조회한 뒤 BE 코드·경로·인증 배포를 준비하는 것이다. [EC2 배포 안내](../../infra/terraform/cpu-rag/README.md)의 접속·DB 확인·테이블 조회 절차를 따른다. 동료 공유용 접속 절차도 [공유 README의 B-1~B-4](../../docs/pipeline-guide/README.md)에 있다. 아래 Mac 명령은 기존 개발 환경 참고용이며 필수 선행 단계가 아니다. Mac DB/spool은 EC2로 자동 이관되지 않으므로 원본을 보존한다. BE와 RAG 처리 코드는 공통이고 환경별 설정을 구분한다.

## BE 작업 순서 요약 — 기존 Mac 환경 재개

2026-09-29 사용자 출력에서 `connected / runpod-local-api-v1 / storage:s3 / jobs_submitted:0`를 확인했다. 새 Pod와 Mac BE의 HTTPS·토큰 인증 연결은 성공했다. 이번 확인은 영상 제출·GPU 추론·Gemini 호출을 하지 않는다. 이전 A의 저장 기록은 handoff_ready이므로 새 Pod에 다시 제출하지 않는다. 동료가 새 환경을 준비할 때에는 [공유 가이드](../../docs/pipeline-guide/README.md)의 2~4절을 따른다. 아래 개인 경로·실제 설정은 현재 사용자의 기존 환경 재개용이다.

### 1. 작업 위치와 연결 정보

- Runpod 운영자: GPU·모델·8000 API·S3 업로드를 준비한다.
- BE 담당자: Mac/개발 PC 또는 배포된 CPU EC2에서 접수·polling·S3 회수·Gemini/DB를 실행한다.
- 받을 정보: 현재 Pod의 HTTPS 주소, 비공개 분석 API 토큰, S3 버킷/서울 리전/prefix, 해당 prefix의 S3 읽기 권한. Gemini 키는 BE에 별도 준비한다.
- Runpod 분석 토큰은 Runpod 계정 관리 API 키와 다르다. token/credentials를 Git에 넣지 않는다.

### 2. 매번 새 Mac 터미널에서

```bash
cd . &&
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
export BE_TARGET_CONFIG=/input/config-new-pod-v1.json
docker compose -f "$CCTV_COMPOSE_FILE" ps
bash apps/be_worker/queue.sh list
```

Docker Desktop을 켜고 기존 db의 healthy를 확인한다. 중지된 db는 같은 데이터 경로에서 `docker compose -f "$CCTV_COMPOSE_FILE" up -d --wait db`로 재개한다. 기존 데이터·비밀번호·spool을 유지한다. 연결 주소/토큰 변경 시 아래의 인증 GET /health 명령을 다시 실행한다. `source` 안내 문장은 경로 준비, `connected`는 외부 API 인증 성공을 뜻한다. 이미 저장된 config는 configure를 반복하지 않아도 된다.

### 3. 새 영상 접수·분석 — 새 GPU 시험을 할 때

새 MP4를 `artifacts/be-worker/queue-local-v1/input/next.mp4`에 둔다. 이 파일은 컨테이너에서 `/input/next.mp4`로 보인다. 현재 상한은120초/200MiB다. 기존 완료 영상을 복구하는 절차가 아니며, 아래 dispatch부터 실제 영상 전송/GPU 분석/S3 업로드를 수행한다.

```bash
bash apps/be_worker/queue.sh enqueue \
  --video /input/next.mp4 \
  --config "$BE_TARGET_CONFIG" \
  --request-key demo-next-20260929-v1
bash apps/be_worker/queue.sh dispatch --seconds 300 --slots 1
bash apps/be_worker/queue.sh list
```

enqueue의 worker_job_id는 BE 관리 ID다. dispatch는 Runpod에서 provider_job_id를 받아 DB에 보관하고 상태를 polling한다. request-key는 같은 접수에는 유지하고 다른 영상에는 새 값을 준다. 파일/설정을 바꾸고 같은 키를 재사용하면 충돌로 거절된다. 300초 뒤에도 waiting이면 재접수하지 말고 기존 dispatch를 이어간다. `--slots 1`로 하나씩 전달한다.

### 4. S3 결과 회수

```bash
bash apps/be_worker/queue.sh collect --seconds 60
bash apps/be_worker/queue.sh list
```

evidence_ready는 Runpod 성공과 S3 저장 참조를 받은 상태, handoff_ready는 BE가 S3 JSON/manifest를 검증해 사건별 인계 준비를 마친 상태다. no_candidates이면 보낼 사건 미디어가 없는 결과이며 분석 실패와 다르다. 아직 waiting이면 dispatch로 완료 상태를 확인한 다음 collect를 이어간다. S3 자격 증명이 만료되면 기존 AWS 프로필을 갱신하고 `prepare_queue.py --directory "$BE_SECRETS_DIR" --aws-profile cctv`로 준비한다.

### 5. 사건 근거·Gemini·DB — 새 사건의 첫 설명

list에 나온 실제 worker_job_id와 인계에 있는 event_id를 사용한다. 아래 event_000은 첫 사건 예시이며 실제 사건이 있을 때만 사용한다.

```bash
export WORKER_JOB_ID='여기에_실제_worker_job_id'
export EVENT_ID='event_000'
bash apps/be_worker/queue.sh scene-fetch \
  --worker-job-id "$WORKER_JOB_ID" --event-id "$EVENT_ID"
python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR" --gemini
bash apps/be_worker/queue.sh scene-describe \
  --worker-job-id "$WORKER_JOB_ID" --event-id "$EVENT_ID"
```

scene-fetch의 media_verified는 실제 클립·프레임 다운로드/해시 검증이다. Gemini 키가 이미 준비됐다면 prepare_queue --gemini를 반복하지 않아도 된다. scene-describe는 Gemini 생성 API를 호출하고 결과를 검증해 DB에 저장한다. description_stored/database_loaded true를 확인한다. 이전 실패 사건은 저장된 실패/응답 여부를 먼저 확인하고 명시적 재시도 ID를 선택하는 별도 절차다. 이 예제를 기존 실패 건의 자동 재호출 명령으로 쓰지 않는다.

### 6. Pod 교체·종료와 역할 경계

같은 URL/토큰이면 기존 설정으로 health를 확인한다. 새 Pod URL이면 새 config를 만들고 새 접수에 지정한다. 새 토큰이면 BE 비공개 파일을 맞춘다. 진행 중 작업의 목적지를 config 수정만으로 바꿀 수는 없다. retarget은 제출0회 prepared에 한정한다. S3 인계 완료 건은 기존 근거에서 다음 단계를 이어간다.

테스트 종료 때는 신규 접수를 멈추고 모든 요청의 S3 업로드/BE 인계를 확인한 뒤 운영자가 GPU를 중지한다. 그 뒤 Gemini/RAG는 BE에서 진행할 수 있다. BE의 DB와 spool은 Runpod 임시디스크와 별개이므로 함께 보존한다. 현재 CLI는 각 단계를 별도 실행하며, 장면 설명→문서 검색→보고 자동 연결은 후속 기능이다.

아래는 연결 설정·진단·복구의 상세 절차와 과거 실행 이력이다.

## 2026-09-29 — 새 Pod의 S3 health 확인 후 Mac BE 연결

사용자는 새 컨테이너의 Runpod 내부 health에서 ok/runpod-local-api-v1/storage=s3를 확인했다. GPU 추론이나 S3 쓰기 검사 결과는 아니다. 본인이 BE도 운영하므로 새 주소를 Mac의 연결 설정에 등록한다. 아래 명령은 **Mac 터미널의 기존 프로젝트**에서 실행한다. Runpod 서버 터미널은 그대로 둔다. 현재 EC2를 켜야 하는 단계는 아니다.

### 연결 주소 저장

Runpod 콘솔의 현재 Pod → Connect → Port 8000 → HTTP Service 주소를 복사한다. root 셸에 보이는 컨테이너 이름과 Pod ID는 다르다. `http://0.0.0.0:8000/`은 서버의 수신 주소이므로 Mac BE의 접속 주소로 쓰지 않는다.

```bash
cd . &&
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
export BE_TARGET_CONFIG=/input/config-new-pod-v1.json
python3 apps/be_worker/worker.py configure \
  --output "$BE_INPUT_DIR/config-new-pod-v1.json" \
  --s3-bucket boram3-cctv-evidence-80bee9db7c3bca316ef395375a \
  --s3-region ap-northeast-2
```

URL 입력 질문에 새 HTTP Service 주소를 넣는다. `configured / api_calls: 0`이면 파일만 준비된 상태다. source한 파일 이름은 과거 Pod ID이지만 여기서는 기존 Mac DB·spool·비밀파일 경로를 재사용하며 `BE_TARGET_CONFIG`를 새 파일로 바꾼다. source만으로 새 Pod에 연결되지 않는다. 같은 출력 파일이 이미 있다면 내용을 확인하고 재사용하거나 새 이름을 선택하며, 이름 변경 시 `BE_TARGET_CONFIG`도 맞춘다.

### Mac에서 인증된 API 연결만 확인

위 단계와 같은 Mac 터미널에서 실행한다. 기존 BE의 RunpodAPI 어댑터를 그대로 사용하여 Authorization과 전용 User-Agent를 붙인다. 토큰은 비공개 파일에서 읽고 출력하지 않는다. GET /health 1회이며 DB·작업 접수·GPU 추론·Gemini 호출은 하지 않는다.

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
    token_path = Path(os.environ["BE_SECRETS_DIR"]) / "runpod_worker_token"
    token = token_path.read_text().strip()
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

예상 출력은 `status: connected`, `storage: s3`, `jobs_submitted: 0`이다. 이것은 Mac 호스트에서 BE HTTP 코드의 외부 인증 성공이며 Docker 컨테이너/DB의 실행 확인과는 구분한다. `http_401`이면 새 Pod의 .api-token과 Mac 토큰이 일치하는지 확인한다. 기존 토큰을 새 Pod에 복원했다면 Mac 토큰을 바꾸지 않는다. 새 토큰으로 시작했다면 해당 파일을 비공개로 회수해야 한다. 토큰을 채팅에 붙이지 않는다.

### 외부 health가 `http_401`이면 현재 Pod 토큰 회수

사용자 외부 health에서 http_401/jobs_submitted0 수신. Mac 설정은 새 Pod 형태의 HTTPS 주소이며 이전 Pod 주소와 다르지만 현재 실행 Pod와의 일치는 콘솔에서 확인해야 한다. 401은 요청 대상에서 인증이 거절됐다는 뜻이다. GPU 분석·새 접수는 실행되지 않았다. 분석 API 토큰은 start.sh가 .api-token에 만드는 값이고 Runpod 계정 API 키·Gemini 키·AWS 키와 별개다.

Mac의 이전 토큰은 `artifacts/be-worker/queue-local-v1/secrets-pod-3whxkh444xl3qc-v1/runpod_worker_token`이며 파일 존재44bytes/0600만 확인했다. 새 Pod에 기존 토큰이 복원됐는지 또는 새 토큰이 생성됐는지는 미확인이다. 현재 Pod의 내부 client.py health가 성공한다면 그 Pod의 .api-token을 Mac으로 회수해 BE 파일과 맞춘다. 실행 중인 서버의 토큰 파일을 임의 교체하는 것과 달리 이 방법은 API를 재시작하지 않는다.

**Runpod Jupyter Terminal:**

```bash
cd /workspace/cctv-runpod-a4000 &&
.venv/bin/python client.py health &&
install -m 600 .api-token /workspace/runpod_worker_token-current
```

내부 health가 성공하면 같은 Jupyter 파일 목록의 `/workspace/runpod_worker_token-current`를 Mac 다운로드 폴더에 내려받는다. 토큰 내용을 터미널/채팅에 출력하지 않는다. 아래는 다운로드 이름이 정확히 `runpod_worker_token-current`일 때의 명령이며 브라우저가 중복 이름에 번호를 붙였다면 그 실제 경로를 사용한다.

**Mac Terminal:**

```bash
cd . &&
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
export BE_TARGET_CONFIG=/input/config-new-pod-v1.json
test -s "$HOME/Downloads/runpod_worker_token-current" &&
cp -p "$BE_SECRETS_DIR/runpod_worker_token" \
  "$BE_SECRETS_DIR/runpod_worker_token.before-sync-$(date +%Y%m%d-%H%M%S)" &&
install -m 600 "$HOME/Downloads/runpod_worker_token-current" \
  "$BE_SECRETS_DIR/runpod_worker_token"
```

복사가 성공하면 같은 터미널에서 위의 인증 GET /health 코드를 다시 실행한다. 기존 config 파일을 다시 configure할 필요는 없다. connected가 확인되면 업로드/다운로드용 임시 토큰 사본은 정리하고 BE의 비공개 토큰 파일과 이전 보관본은 비공개로 유지한다. 기존 dispatcher가 실행 중이었다면 다음 실행이 새 토큰을 읽게 해야 한다. 후속 사용자 복사와 외부 connected 출력은 맨 위 최신 요약에 반영했다.

### 그다음 큐 확인

Docker Desktop이 실행 중일 때 같은 Mac 터미널에서:

```bash
bash apps/be_worker/queue.sh list
```

기존 저장기록의 A 작업은 handoff_ready/제출1회/조회5회다. 이 작업을 retarget하거나 다시 제출하지 않는다. 새 영상 접수 시 `--config "$BE_TARGET_CONFIG"`로 새 설정을 지정한다. 이미 존재하는 미제출 prepared 작업만 상태 확인 후 retarget할 수 있다. 환경변수/입력 config 변경만으로 기존 DB 작업 목적지가 바뀌지는 않는다. 후속 사용자 출력으로 새 Pod의 Mac 외부 인증 연결을 확인했으며 아래 실패/복구 이력과 구분한다.

## 장면 설명 이력 — 2026-09-29 추가 재시도도503

사용자 실행과 저장 원본으로 최신 분석 `0a453f36-2ca0-4b15-9d91-89d9b41fcb9c`의
`description_failed/gemini_http_503`을 확인했다. 장면 요청 의도4회·별도 텍스트 진단1회이며
현재 결과 파일과 설명 DB 저장은 없다. 기존 GPU/S3 근거는 유지한다.
아래 명령은 이미 실행한 이력이다. 추가 호출을 반복하지 않는다.
공유 문서/ZIP v3에서는 이 오류 이력을 제외했으며 내부 원본은 그대로 보존했다.

## 이전 재개 명령 — 2026-09-29 실행 이력

이전503 중단 뒤 사용자가 재실행 방법을 요청했다. 저장된 최신 실패 분석은
`5914cb47-509f-47b4-9eed-2dfcd20a44c3`이며 그 ID의 재시도 폴더는 아직 없다.
기존 키 파일은 존재/비어 있지 않음만 확인했고 값이나 유효성은 새로 조회하지 않았다.
아래는 **사용자가 Mac 터미널에서 실행할 명령**이다. 에이전트는 생성 호출하지 않았다.

Docker Desktop을 켜고 기존 DB 상태부터 확인한다.

```bash
cd .
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
docker compose -f "$CCTV_COMPOSE_FILE" ps
```

기존 DB가 실행 중이 아니면 `docker compose -f "$CCTV_COMPOSE_FILE" up -d db`로 시작하고
`ps`에서 healthy를 확인한다. 기존 데이터 경로를 유지하며 DB/스키마를 새로 초기화하지 않는다.

```bash
bash apps/be_worker/queue.sh scene-describe \
  --worker-job-id e2a71c13191b4245a99fa1a9b1b60b6d \
  --event-id event_000 \
  --model gemini-3.5-flash-lite \
  --fps 4 \
  --retry-failed-analysis-id 5914cb47-509f-47b4-9eed-2dfcd20a44c3
```

검증된 기존 미디어로 Gemini 생성1회를 추가 요청하며 사용량이 발생할 수 있다.
성공하면 `description_stored`, `database_loaded: true`를 확인한다.
현재 건은 GPU/S3 완료이므로 Runpod/EC2 기동이나 dispatch는 필요하지 않다.
이전 `19a74046…`, `f487f6e6…`는 이미 사용한 재시도 기준 ID다.
`scene-probe` 재실행은 이전 텍스트503 기록을 보여주므로 새 진단 명령으로 안내하지 않는다.
다시503이면 출력을 보관하고 반복 실행을 멈춘다. 새 실패 ID는 그때 저장된 영수증으로 확인한다.
503은 간격을 둔 제한 재시도 대상이다([Google 공식 안내](https://ai.google.dev/gemini-api/docs/troubleshooting)).
공개 상태 페이지로 이번 모델/계정의 생성 가능 여부를 확인한 것은 아니다.

## 저장된 전체 흐름 조회 — 2026-09-29

팀 공유용 아키텍처·시퀀스·Runpod 재시작 절차는 [문서 전용 안내](../../docs/pipeline-guide/README.md)에 정리했다.
현재 작업의 파일 기록만 보려면 **Mac 터미널**에서:

```bash
cd .
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
python3 apps/be_worker/flow_status.py \
  --spool "$BE_SPOOL_DIR" --worker-job-id "$BE_WORKER_JOB_ID"
```

Docker/DB 접속·네트워크·모델 호출 없이 BE 상태 파일과 진단 영수증을 읽는다.
실시간 서버 점검이 아니며 DB/S3가 지금 존재하는지 보장하지 않는다. `--json`으로 구조화 출력,
작업ID 옵션을 빼면 전체 저장 작업을 조회한다. 업무 파일/시도 횟수는 변경하지 않는다.

## 기존 S3 결과 → Gemini 설명 → DB 저장 — 2026-09-29

### 구현과 실제 실행 범위

BE의 `dispatch`는 PostgreSQL 대기열에서 작업을 읽고 MP4를 Runpod `/jobs`에 제출한 뒤
발급된 ID로 상태를 조회한다. 현재 A는 실제 제출1회·조회5회 기록이 있다. S3 인증 오류 후
사용자 수동 업로드와 `recover-s3`를 거쳐 복구했으므로 전체 과정의 무개입 성공은 아직 아니다.

`scene-fetch`는 S3 근거 다운로드/해시 확인, `scene-describe`는 기존
`apps/scene_description/scene.py`를 재사용해 장면 설명과 DB 저장을 처리한다.
이전 단독 v1 설명은 성공했고 현재 v2/BE 경로는503 때문에 설명·DB 저장 확인 전이다.
`dispatch`가 Gemini까지 자동 호출하지는 않는다. 현재 Mac Docker의 단계별 CLI이며,
EC2 상시 통합배포/전체 자동 실행은 후속이다. 백업은 VLM 연결의 선행 단계가 아니다.

현재 A 영상은 GPU 분석·S3 업로드·BE 복구·클립/프레임 해시 검증까지 완료했다.
장면 설명은 시도 의도 누계3회이고, 최근 두 시도에서 HTTP503/UNAVAILABLE를 받았다.
응답·결과 파일과 설명 DB 저장은 아직 없다. 첫 실패의 상세 HTTP 상태는 미확인이다.

### 현재 상태: 텍스트 진단도503 — 추가 호출 중단

사용자 실행 결과 `text_generation_failed`, `http_status: 503`, `api_status: UNAVAILABLE` 확인.
진단명령은 실행됐고 텍스트 생성은 실패했다. 영상없이도 같은오류가 재현되므로
영상파일/용량만의문제로판단하지않는다. 정확한서비스/모델/프로젝트원인은아직미확인이다.
영상설명시도3회·별도텍스트진단1회, GPU/S3/BE원본보존·설명DB저장전이다.

추가생성호출을멈추고 이후재개시모델/서비스제공상태를확인한다. 아래 scene-probe는 이미실행한이력이며
다시실행하면저장된503영수증을반환한다. 이출력을새실시간진단결과로해석하지않는다.

### 텍스트 생성 진단 명령 — 실행 이력

전체 영상 재시도는 중단한다. 확인 당시 Google 공식 상태 페이지의 전체/무료 등급은
정상 표시였으나 개별 모델 요청의 성공까지 보장하지 않는다.
[서비스 상태](https://aistudio.google.com/status) · [오류 안내](https://ai.google.dev/gemini-api/docs/troubleshooting)

**Mac 터미널, Docker Desktop 실행 상태**에서 아래 명령을 실행한다. 현재 선택된 실패 시도의
같은 모델·같은 비공개 키로 `Reply only with OK.`라는 고정 텍스트만 요청한다.
생성 요청1회이므로 소량의 모델 사용량이 발생할 수 있다. 출력 상한은256토큰이며 입력/내부
처리 토큰까지 포함한 전체 사용량 상한을 뜻하지 않는다. 영상·프레임·사건 정보는 보내지 않는다.

```bash
cd .
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
bash apps/be_worker/queue.sh scene-probe \
  --worker-job-id e2a71c13191b4245a99fa1a9b1b60b6d \
  --event-id event_000
```

- `text_generation_ok`: 텍스트 생성 응답 확인. 이후 영상 입력/JSON 응답 설정을 나눠 점검한다.
  이 성공만으로 이전503의 원인이나 영상 요청의 잘못을 확정하지 않는다.
- `text_generation_failed` + `gemini_http_503`: 영상 없이도 같은 오류가 발생한다.
  추가 호출을 멈추고 서비스/모델 제공 상태를 확인한다.
- `text_generation_incomplete`: HTTP응답은 왔지만 최종 텍스트가 없어 결과를 확인한다.
- `scene_probe_incomplete_no_auto_retry`: 진단 실행이 중단돼 완료 여부가 불명이다. 자동 반복하지 않는다.

진단은 `scenes/event_000/text-probes/<실패분석ID>/`에 별도 보관한다. 같은 명령을 다시 실행하면
저장된 진단 영수증을 반환하고 `generation_requests_this_run: 0`을 표시한다. 업무 DB 상태와
기존 설명 시도 누계는 유지한다. 진단 생성 횟수는 별도 영수증에 기록하며 기존 GPU·S3 결과도 보존한다.
코드 bind mount를 사용하므로 이미지 재빌드는 필요 없다. 실제진단은사용자실행으로503을확인했다(위현재상태).

### 두 번째 명시적 재시도 — HTTP 503으로 종료된 이력

`--retry-failed-analysis-id f487f6e6-308f-4cff-b1dd-7d5813784c4f` 실행에서
새 분석ID `5914cb47-509f-47b4-9eed-2dfcd20a44c3`도503을 반환했다. 누계3회이며
전체 영상 추가 재시도를 현재 다음 명령으로 안내하지 않는다. 아래는 이전 실행 이력이다.

### 첫 명시적 재시도 — HTTP 503으로 종료된 이력

사용자 진단 결과 `metadata_access_ok`, `supports_generate_content=true` 확인.
현재 키로 모델 정보에 접근할 수 있으나 생성 할당량·영상 요청 성공은 아직 확인되지 않았다.
기존 오류에 상세 상태가 없어 최초 실패 원인은 확정하지 않는다.

아래 명령은 Mac에서 **추가 Gemini 설명 요청을 한 번** 보낸다. 모델 사용량이 발생할 수 있다.
GPU 재분석·S3 재업로드는 없으며, 기존 검증된 미디어를 사용한다.

```bash
cd .
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
bash apps/be_worker/queue.sh scene-describe \
  --worker-job-id e2a71c13191b4245a99fa1a9b1b60b6d \
  --event-id event_000 \
  --retry-failed-analysis-id 19a74046-88ef-4caa-8b47-1ba629eabd3f
```

성공 기준은 `description_stored`와 `database_loaded: true`다. 같은 Mac Docker DB에 저장한다.
새 실패는 `gemini_http_400`처럼 구분된 오류 코드를 남긴다. 실패 시 결과를 확인하고,
이 명령을 반복해도 추가 생성 요청을 자동으로 보내지 않는다. 성공 결과가 있는데 DB만
실패했다면 같은 명령은 기존 결과의 DB 저장만 재개한다.

원래 `description/`은 그대로 보존하고, 새 결과는
`description-retry-19a74046-88ef-4caa-8b47-1ba629eabd3f/`에 저장한다.
`description-selection.json`이 현재 시도를 가리키며 `retry-<이전분석ID>.json`에
이전 요청·실패 파일 해시를 남긴다. `gemini_api_calls`는 시도 의도 누계여서 성공/청구 건수와 다르다.
완료 후 `rag-handoff.json.scene_result`가 새 결과 경로를 가리킨다. 이미지 재빌드는 필요 없다.

### 키와 모델 접근 진단 — 이번 사건 완료

Mac 터미널에서 기존 Docker Desktop을 켠 상태로 실행한다. 영상 생성 요청을 반복하지 않고,
실패한 요청의 모델 ID에 대해 [models.get](https://ai.google.dev/api/models#method:-models.get)
메타데이터 조회 한 번만 수행한다. 키·오류 원문을 출력하지 않으며 영상 업로드도 없다.

```bash
cd .
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
bash apps/be_worker/queue.sh scene-diagnose \
  --worker-job-id e2a71c13191b4245a99fa1a9b1b60b6d \
  --event-id event_000
```

- `metadata_access_ok`: 현재 키로 해당 모델 정보 조회 성공. 영상 요청 형식·생성 할당량까지 검증한 것은 아니다.
- `API_KEY_INVALID`·`API_KEY_EXPIRED`: Google이 해당 키 문제를 반환한 경우만 출력한다.
- `gemini_http_403`: 접근 거부이며 키 오입력이라고 단정하지 않는다. `reasons`가 있으면 함께 확인한다.
- `gemini_http_404`: 해당 모델 메타데이터 조회 실패. 저장된 모델 ID와 계정 제공 범위를 확인한다.
- `gemini_tls_certificate_error`·`gemini_dns_error`·`gemini_timeout`: Docker 환경의 통신 계층 확인이 필요하다.

진단은 새로운 `scenes/event_000/diagnostics/<id>/`에 기록한다. 기존 `description/`과
DB 상태·설명 시도 횟수는 유지한다. 기존 폴더를 삭제하지 않는다.
생성 재시도는 위의 정확한 실패 분석 ID를 지정하는 명령으로 선택한다.
코드는 bind mount로 연결돼 이미지 재빌드 없이 사용할 수 있다. 아래는 최초 처리 절차다.

```text
handoff_ready (기존 분석 완료)
  → scene-fetch: S3 클립·프레임 다운로드, 크기·SHA256·작업 일치 확인
  → scene-describe: Gemini 설명 1회, 원본 응답·정규화 결과 보존
  → 같은 명령 안에서 PostgreSQL 설명 저장
  → description_stored + 동료에게 전달할 rag-handoff.json
```

`state.status=handoff_ready`는 GPU/S3 단계 상태로 보존한다.
`state.scene_events.event_000.status`가 장면 단계이며 `queue.sh list`에 함께 나온다.
원래 `handoff.json`의 `media_bytes_verified=false`는 메타데이터 회수 당시 영수증이다.
이번 단계의 실제 검증 여부는 `scene_events.event_000.media_bytes_verified`를 본다.

### 실행 위치: Mac 터미널, 기존 Docker Desktop 실행

EC2·Runpod를 시작하지 않아도 기존 S3 결과로 진행할 수 있다. 원격 CLI는 사용자가 실행한다.

```bash
cd .
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
bash apps/be_worker/queue.sh list
bash apps/be_worker/queue.sh scene-schema
bash apps/be_worker/queue.sh scene-fetch \
  --worker-job-id e2a71c13191b4245a99fa1a9b1b60b6d \
  --event-id event_000
```

- `list`: 기존 A가 `handoff_ready`인지 확인한다. 결과가 없다면 DB/Compose 경로부터 확인한다.
- `scene-schema`: 기존 장면 결과를 보존하면서 v2용 열·제약을 추가한다. 문서벡터는 바꾸지 않는다.
- `scene-fetch`: 해당 사건의 S3 객체 4개(manifest·근거 목록·클립·프레임)를 읽고 확인한다.
  성공 기준은 `status: media_verified`, `media_bytes_verified: true`. GPU·Gemini 호출은 없다.
- S3 인증이 만료됐다면 `python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR" --aws-profile cctv`로
  현재 Mac 프로필을 다시 준비한다. 기존 토큰을 화면에 출력하지 않는다.

미디어 검증 성공 후 Gemini 실행을 선택할 때:

```bash
python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR" --gemini
bash apps/be_worker/queue.sh scene-describe \
  --worker-job-id e2a71c13191b4245a99fa1a9b1b60b6d \
  --event-id event_000
bash apps/be_worker/queue.sh list
```

키 입력은 숨겨지고 저장만 한다. `scene-describe`가 기존 검증된 클립·프레임을
외부 Gemini에 전달하므로 모델 사용량이 발생한다. 기본 모델은 이전 계정에서 확인한
`gemini-3.5-flash-lite`, 샘플링은 4FPS를 유지한다. 모델을 바꾸려면 첫 실행에서
`--model`을 지정한다. 코드 검사는 합성 응답이며 새 스키마의 실제 모델 품질은 실행 후 확인한다.

### 실패·재개

- S3 다운로드 실패: 같은 `scene-fetch`로 다운로드만 재시도한다. 불완전한 이전 폴더는 보존한다.
- 결과가 저장됐는데 DB 쓰기 실패: `database_pending`. 같은 `scene-describe`를 실행하면
  기존 결과를 검증해 DB 저장만 재개한다. Gemini 키 없이도 재개할 수 있다.
- Gemini 타임아웃·차단·스키마 오류: `description_failed`. 기존 요청/응답/실패 파일을
  확인한다. 같은 명령은 유료 요청을 자동 재시도하지 않는다. 폴더를 지워 다시 실행하지 않는다.
- 저장된 결과의 작업/원본/근거 해시가 다르면 DB 저장을 차단한다.

결과는 기존 spool의 `<directory_id>/scenes/event_000/`에 있다:
`input/`, `description/request.json`, `description/response.raw.json`,
`description/result.json`, `database.json`, `rag-handoff.json`.
모든 파일은 비공개 spool 하위이며 묶음/Git 업로드 대상이 아니다.
`result.json.database_loaded=false`는 생성 당시 값으로 보존하고 실제 저장은
`database.json`·BE 상태로 확인한다. RAG/보고/화면/알림은 아직 자동 실행하지 않는다.


**Pod를 새로 만들었고 기존 작업이 `prepared`라면 [8번](#8-새-pod로-제출-전-작업-연결)을 따른다.** 입력 config만 바꿔도 이미 접수된 작업에는 반영되지 않는다.

## 이번 범위와 실제 실행의 차이

영상 A/B/C를 접수해 PostgreSQL에 기록하고, Runpod에는 기본 최대2건(실행1/대기1)을 배정한다. BE는 약2초 간격으로 상태를 확인한다. S3 수집기는 별도 프로세스라 A의 회수 실패가 B의 GPU 배정을 막지 않는다. `--slots 1`이면 BE에서 한 건씩만 제출한다. GPU 동시 수는 바꾸지 않았다.

이번 완료 지점은 **handoff_ready: 검증한 S3 메타데이터와 Gemini 입력 설정의 인계 준비**다. 실제 미디어 검증은 기존 scene fetch, 설명은 scene describe에서 수행한다. `handoff_ready`를 사고 확정·설명 완료·화면 표시 완료로 쓰지 않는다. 후보0건은 no_candidates, 분석 실패는 provider_failed로 구분한다. 충돌만 보는 기존 Gemini 평가를 전도 등 사고 의심/유형으로 보완하고 자동 설명·DB 저장·화면을 연결하는 작업은 남아 있다.

코드/합성 검증과 실제 서비스 검증을 구분한다. 사용자 Docker 시작 후 독립 PostgreSQL에서 큐20검사가 모두 통과했고 테스트 컨테이너를 정리했다(9.344초). 기존 BE27검사를 합쳐 고유47검사 통과다. 원격 Runpod/S3 응답은 합성이며 실제 GPU·API·배포 검증은 남아 있다. 상세 근거는 `artifacts/be-worker/be-queue-20260928-01/verification-v2.json` 및 `database-check-v2.json`. 최초 엔진 연결 실패 영수증도 보존했다. 아래 명령은 사용자가 실행할 절차이며 AWS/Runpod 자원을 자동 생성하지 않는다.

## 1. 먼저 독립 DB 검증 — 원격 API 없음

현재 Mac에서는 이 검사가 통과했으므로 바로2번으로 진행할 수 있다. 다른 환경에서 재현할 때만 Docker Desktop을 켜고 아래를 실행한다. 기존 캐시의 PostgreSQL 이미지와 `cctv-rag-tools:gemini-001-768-v1`만 사용한다. 새 이미지 다운로드/서비스 DB 변경은 하지 않는다.

```bash
python3 tests/be_worker/check_queue_database.py \
  --report artifacts/be-worker/be-queue-20260928-01/database-check-v3.json
```

`status=passed`와 `test_containers_removed=true`를 확인한 뒤 진행한다. 기존 결과를 보존하므로 이미 있는 report 파일에는 다시 쓰지 않는다. 실패하면 해당 출력을 먼저 확인하며 클라우드 영상 제출은 보류한다.

## 2. 기존 로컬 DB와 작업 폴더 준비

아래는 **Mac의 기존 cctv-rag-local DB** 기준이다. 원본 영상/기존 벡터는 유지한다. Docker 엔진은 켜져 있어야 한다.

```bash
cd .
export COMPOSE_PROJECT_NAME=cctv-rag-local
export CCTV_COMPOSE_FILE="$PWD/infra/compose/rag/compose.yaml"
export CCTV_DATA_DIR="$PWD/artifacts/rag/local-db-v1/data"
export CCTV_SECRETS_DIR="$PWD/artifacts/rag/local-db-v1/secrets"
export BE_INPUT_DIR="$PWD/artifacts/be-worker/queue-local-v1/input"
export BE_SPOOL_DIR="$PWD/artifacts/be-worker/queue-local-v1/spool"
export BE_SECRETS_DIR="$PWD/artifacts/be-worker/queue-local-v1/secrets"
mkdir -p "$BE_INPUT_DIR" "$BE_SPOOL_DIR"
chmod 700 "$BE_INPUT_DIR" "$BE_SPOOL_DIR"
python3 scripts/rag_local.py status
```

기존 DB가 중지된 경우에만 `docker compose -f "$CCTV_COMPOSE_FILE" start db` 후 상태를 다시 확인한다. 기존 DB 디렉터리/비밀번호를 지우거나 새로 만들지 않는다. 다른 프로젝트/EC2에 적용하려면 이 환경값을 해당 배포 경로로 바꿔야 한다. 특히 같은 DB에 작업을 넣는 모든 프로세스는 **같은 spool 폴더**를 사용해야 한다.

`BE_INPUT_DIR`에 분석할 영상 복사본 A.mp4/B.mp4/C.mp4를 준비한다. 새 설정 파일에는 현재 GPU Pod의 **Port8000 HTTPS 주소**를 입력한다.

```bash
python3 apps/be_worker/worker.py configure --output "$BE_INPUT_DIR/config.json"
python3 apps/be_worker/prepare_queue.py --directory "$BE_SECRETS_DIR" --aws-profile cctv
```

숨김 입력은 Runpod 계정 키가 아니라 기존 분석 API 토큰이다. Mac의 기존 cctv AWS 프로필을 컨테이너용 비밀 파일로 내보내며 값은 출력하지 않는다([AWS CLI 공식 export-credentials](https://docs.aws.amazon.com/cli/latest/reference/configure/export-credentials.html)). 임시 자격 증명이 만료되면 prepare_queue 명령으로 갱신한다. 토큰 파일이 이미 있으면 보존한다. EC2의 인스턴스 역할을 쓸 때는 `--aws-profile`을 빼고 비어 있는 자격 증명 파일을 사용한다. 실제 역할 연결 및 컨테이너에서 역할 접근 가능 여부는 별도로 확인해야 한다. EC2를 지금 켜거나 IAM/Terraform을 변경하는 절차는 아니다.

## 3. 스키마 적용·CPU 도구 이미지 준비

```bash
bash apps/be_worker/queue.sh schema
bash apps/be_worker/queue.sh build
```

schema는 `cctv_jobs.queue`와 인덱스/앱 권한만 추가한다. 기존 문서 벡터/설명 테이블은 수정하지 않는다. build는 CPU Python/psycopg/boto3 설치이며 영상 모델을 로딩하지 않는다. 컨테이너에 외부 HTTP 포트/DB 포트를 추가하지 않는다. API 호출을 위해 worker 컨테이너만 별도 외부 통신 네트워크를 사용한다.

## 4. A/B/C 접수 — GPU 요청은 아직 없음

```bash
bash apps/be_worker/queue.sh enqueue --video /input/A.mp4 --config /input/config.json --request-key demo-v1-a
bash apps/be_worker/queue.sh enqueue --video /input/B.mp4 --config /input/config.json --request-key demo-v1-b
bash apps/be_worker/queue.sh enqueue --video /input/C.mp4 --config /input/config.json --request-key demo-v1-c
bash apps/be_worker/queue.sh list
```

`prepared`는 BE 대기 상태다. 같은 request-key/영상/설정으로 다시 접수하면 기존 작업을 반환한다. 같은 키의 다른 내용은 거절한다. 각 파일은 최대200MiB이며 준비된 spool에 사본·해시를 저장한다. 모델 분석 제한은 기존 Runpod 설정을 따른다. 입력 영상은 DB에 바이너리로 넣지 않는다.

## 5. 배정·완료 조회 — 여기부터 실제 GPU 요청 발생

현재 GPU/API·S3 설정이 준비된 뒤 실행한다. Runpod 사용·영상 전송/S3 저장에 기존 과금이 적용된다. 기본5분 동안 처리하며 Ctrl-C로 멈춰도 접수 ID는 보존된다. 시간 상한은 다음 반복을 시작하는 한도이며 진행 중인 HTTP 요청의 timeout만큼 더 걸릴 수 있다.

```bash
bash apps/be_worker/queue.sh dispatch --seconds 300
```

A/B가 Runpod에 접수되고 C는 BE에 남는다. 기존 작업 완료를 확인하면 다음 건을 배정한다. 기본 최대2건은 접수 수이며 GPU 동시 분석 수가 아니다. 최대1건 제출만 원하면 `--slots 1`을 추가한다. 같은 큐의 dispatch는 하나만 실행하며 다른 프로세스는 DB 잠금으로 거절한다. 개발용 client.py로 직접 제출한 작업은 DB 큐가 알 수 없어 Runpod가429를 반환할 수 있다.

## 6. 별도 터미널에서 S3 회수

두 번째 터미널에도 2번의 환경변수를 설정한다. dispatch가 동작하는 동안 collect를 실행할 수 있다.

```bash
bash apps/be_worker/queue.sh collect --seconds 300
bash apps/be_worker/queue.sh list
```

기대 상태는 `handoff_ready` 또는 `no_candidates`다. list의 directory_id를 이용하면 호스트의 `$BE_SPOOL_DIR/<directory_id>/`에서 state.json/manifest.json/evidence.json/handoff.json과 scene-event_*.json을 확인할 수 있다. 같은 handoff를 DB에도 보관한다. 원본 영상과 작업 파일은 자동 삭제하지 않는다.

실제 미디어 확인은 Mac의 기존 도구를 재사용한다. 아래 directory_id와 사건 번호는 list/handoff에 나온 값으로 바꾼다. 출력은 새 폴더를 쓴다.

```bash
python3 apps/scene_description/scene.py fetch \
  --config "$BE_SPOOL_DIR/<directory_id>/scene-event_000.json" \
  --aws-profile cctv --output artifacts/vlm/queue-a-event000-input-v1
```

Gemini 호출·설명 DB 저장·화면은 자동 실행되지 않는다. 기존 전도 사례의 분류 범위 보완 후 연결한다.

## 7. 중단·실패·추적

- worker를 다시 실행해도 알고 있는 Runpod ID는 조회만 한다. DB와 spool을 함께 보존한다. DB 백업만으로 영상 사본을 복구할 수는 없다.
- 명확한429는30초 뒤 다시 시도하며 총5회까지만 제출한다. 5회 후에는 대기열 첫 작업을 보존하고 원격 용량/저장 한도를 확인한다. 자동으로 작업을 버리거나 무한 재시도하지 않는다.
- `submission_unknown`/검증 불일치가 있으면 새 배정을 멈춘다. 이미 접수됐는지 모르는 POST는 반복하지 않는다. Runpod에서 확인한 ID가 있을 때만 아래 recover로 원본 해시/크기를 대조한다.
- `provider_failed`는 별도 실패다. 자동 재추론하지 않는다. S3 회수 실패는 GPU를 재실행하지 않고 메타데이터만30초 간격으로 재시도한다.
- 현재 단일 호스트·공유 spool·직접 PostgreSQL 연결을 전제로 한다. DB session advisory lock을 사용하며 transaction pooling 연결은 사용하지 않는다. 원본 파일 보관과 DB 보관의 복구 범위를 맞춰야 한다.
- 로컬 trace는 작업별 기록된다. 큐는 LangSmith에 자동 전송하지 않는다. 기존 `worker.py trace --job-dir "$BE_SPOOL_DIR/<directory_id>"`로 완료된 기록만 선택해 보낼 수 있다. Mac에서는 호스트 소유 UID로 컨테이너를 실행해 private 파일을 읽을 수 있게 한다.

```bash
bash apps/be_worker/queue.sh recover \
  --worker-job-id '<list에서 확인한 BE 작업 ID>' \
  --provider-job-id '<Runpod에서 확인한 작업 ID>'
```

설계 근거: PostgreSQL의 [session advisory lock](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS). Redis/Celery나 콜백 서버는 이번 선행 조건이 아니다.

## 8. 새 Pod로 제출 전 작업 연결

2026-09-28 사용자는 새 Pod `3whxkh444xl3qc`에서 API의 `storage: s3` 응답이 나왔다고 보고했다. Mac에서 읽기 확인한 A는 `e2a71c13191b4245a99fa1a9b1b60b6d`, `prepared`, 제출0회다. 아래는 이 작업을 새 Pod로 연결하는 명령이다. 실제 S3 쓰기 권한과 새 Pod에서의 영상 분석은 아직 확인 전이다.

`retarget`은 **제출0회·provider ID 없음·prepared 상태**인 작업만 변경한다. 다른 실행/불확실 작업이 있으면 거절한다. 영상 사본/해시와 기존 S3 목적지를 확인하고 새 주소·토큰으로 `/health`에 접속한 뒤 DB와 파일의 주소를 변경한다. 기존 작업 ID/순서/영상은 유지한다. 중간 실패가 생기면 dispatch를 차단하고 같은 retarget 명령으로 복구한다. 이전 상태는 작업 폴더의 `retarget-*.json`에 비공개로 보존한다. 이 명령은 영상을 전송하지 않는다.

### Mac에서 연결 경로 설정

로컬에 준비한 설정 파일과 아래 환경변수 스크립트에는 인증 키가 없다. 기존 DB/spool을 사용하며 새 Pod 토큰은 별도 비밀 폴더에 저장한다.

```bash
cd .
source artifacts/be-worker/queue-local-v1/use-pod-3whxkh444xl3qc-v1.sh
```

새 설정 파일은 `input/config-pod-3whxkh444xl3qc-v1.json`이다. 실제 Connect의8000주소가 `https://3whxkh444xl3qc-8000.proxy.runpod.net`와 일치해야 한다. 다른 Pod를 만들었으면 이 예시를 그대로 실행하지 말고 그 주소로 새 설정을 준비한다.

### 새 Pod의 앱 토큰을 비공개 파일로 복사

아래 IP/포트는 사용자가 제공한 새 Pod의 **SSH over exposed TCP** 화면 값이다. Runpod 기본 프록시 SSH는 SCP를 지원하지 않으므로 공개 TCP 주소를 사용한다. 기존 Mac의 `runpod_cctv_ed25519` 키 파일 존재를 확인했으며 내용은 읽지 않았다. IP/포트가 현재 화면과 다르면 새 값으로 교체한다.

```bash
mkdir -p "$BE_SECRETS_DIR" &&
chmod 700 "$BE_SECRETS_DIR" &&
scp -P 32052 -i "$HOME/.ssh/runpod_cctv_ed25519" \
  root@213.173.110.196:/workspace/cctv-runpod-a4000/.api-token \
  "$BE_SECRETS_DIR/runpod_worker_token" &&
chmod 600 "$BE_SECRETS_DIR/runpod_worker_token" &&
python3 apps/be_worker/prepare_queue.py \
  --directory "$BE_SECRETS_DIR" --aws-profile cctv
```

SCP는 SSH 키 암호를 요청할 수 있다. 토큰 값은 출력하지 않는다. 토큰 파일이 준비돼 있어 prepare_queue는 토큰 입력을 다시 요청하지 않고 기존 cctv 프로필을 컨테이너용 AWS 비밀 파일에 내보낸다. 이 AWS 설정은 BE의 S3 회수용이며 Runpod 서버의 AWS 환경변수와 별도다.

### 기존 A의 목적지만 변경

```bash
bash apps/be_worker/queue.sh retarget \
  --worker-job-id "$BE_WORKER_JOB_ID" \
  --config "$BE_TARGET_CONFIG"
```

`status: retargeted`, `health_checked: true`, `jobs_submitted: 0`이면 새 주소/앱 토큰으로 health 확인과 기존 작업 설정 변경이 끝났다. 이미 같은 설정이면 `already_applied: true`로 반환한다. HTTP 오류면 입력 토큰/새Pod API를 확인하며 기존 작업은 보존된다. `retarget_incomplete_repeat_retarget`이면 **같은 작업 ID·설정으로 retarget을 다시 실행**한다. 다른 config/작업ID로 새 접수하지 않는다.

### 그 뒤 실제 분석·S3 회수

아래 dispatch부터 실제 GPU 요청이 발생한다. 첫 명령이 오류면 그 출력을 확인하고 다음 명령으로 넘어가지 않는다.

```bash
bash apps/be_worker/queue.sh dispatch --seconds 300 &&
bash apps/be_worker/queue.sh collect --seconds 300 &&
bash apps/be_worker/queue.sh list
```

서로 다른 단계의 완료를 구분한다. `evidence_ready`는 Runpod 분석/S3 업로드 완료, `handoff_ready`는 BE의 S3 메타데이터 검증/인계 준비다. 실제 미디어 fetch·Gemini 설명·DB/화면 자동 연결은 이후 작업이다. Pod 변경 뒤 기존 `input/config.json`은 이전 주소 보존용으로 남아 있으므로 추가 영상은 새 config 파일을 지정해 enqueue한다.


## 9. 수동 S3 업로드 후 실패 작업 복구

Runpod 분석은 성공했지만 S3 인증 오류로 provider_failed가 된 뒤, 사용자가 storage.py로 같은 작업의 업로드를 완료한 경우에만 사용한다. Mac Docker Desktop을 켜고 기존 환경 스크립트를 source한 같은 셸에서:

```bash
bash apps/be_worker/queue.sh recover-s3 \
  --worker-job-id "$BE_WORKER_JOB_ID"
bash apps/be_worker/queue.sh list
```

Runpod API 토큰이나 GPU 요청 없이 S3 manifest의 job ID/버킷/리전/원본 SHA와 evidence.json의 SHA/시각/장면 경로를 검증한다. 검증 실패면 기존 실패 상태를 유지한다. 성공이면 BE 파일/DB에 handoff_ready 또는 no_candidates와 인계 정보를 저장한다. 재제출 횟수와 이전 실패 기록(s3-recovery-before.json)을 보존하며 DB 쓰기 중단 후 같은 명령으로 재개할 수 있다. 완료 뒤 반복 호출은 기존 receipt를 재사용한다.

이 명령은 S3 메타데이터 검증이며 클립/프레임 바이트 다운로드 검증과 Gemini 호출은 하지 않는다. Runpod API 자체의 failed 상태도 수정하지 않는다. 이후 BE 인계 정보를 기준으로 미디어 fetch를 진행한다. 현재 Mac 소스 bind mount를 사용하므로 이미지 재빌드는 불필요하며 예전 ZIP에는 이 명령이 없다.
