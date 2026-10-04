# BE — FastAPI·상태·작업 연결

## 2026-09-28 — 실제 주소·토큰 입력 위치

사용자 제공8000주소가 `artifacts/be-worker/queue-local-v1/input/config.json`에 이미 유효하게 저장돼 있음을 확인했다. configure를 다시 실행하면 기존파일 보호 오류가 나므로 반복하지 않는다. 분석 API 인증은 Runpod 계정 Credentials 페이지의 키가 아니라 `apps/runpod_worker/start.sh`가 실행 폴더에 만든 `.api-token`이다. 현행 `service.py::create_app`이 같은 파일을 읽는다. 사용자가 Runpod의 실행 폴더에서 개인적으로 확인/복사하고 Mac `prepare_queue.py`의 숨김 입력에 붙여 넣는다. 토큰 값은 채팅/로그/문서에 공유하지 않는다. 로컬 runpod_worker_token은 아직 없음만 확인했고 값조회/원격파일조회0. 서버가 다른 폴더에서 실행됐다면 그 폴더의 토큰이어야 하며 이번 URL 확인만으로 원격 API 정상 상태를 확정하지 않는다.

## 2026-09-28 — 공용 PostgreSQL 대기열 코드 추가

사용자가 A/B/C→PG 대기→Runpod 순차분석→S3→Gemini/화면 구성을 선택해 `job_queue.py`·`queue_store.py`·`queue_schema.sql`과 Compose 실행 도구를 준비했다. [큐 실행 안내](../../apps/be_worker/QUEUE.md)를 따른다. 기존 한 작업 CLI/API·모델은 유지한다.

- `enqueue`: 요청키/원본 SHA/설정 일치 시 기존 작업 반환, 다른 내용 충돌 거절. private spool 영상 사본과 DB 순번/상태를 기록한다. 아직 GPU 호출 없음.
- `dispatch`: PG session advisory lock으로 한 배정기, DB 제출 의도 커밋 후 POST, 기본2건까지만 원격 접수·2초 polling. 현재 Runpod GPU 소비자는1이다. 명확한429만30초 간격/총5회, 불확실POST는 배정을 멈추고 known ID 복구를 기다린다. 파일과 DB 중 먼저 남은 접수 ID를 활용하되 원본·설정·ID 일치를 검사한다.
- `collect`: 별도 잠금/프로세스로 S3 메타데이터 회수·검증·handoff DB 저장. 느린 회수/실패가 다음 GPU 배정을 잡고 있지 않는다. 실제 영상 회수/Gemini 설명·설명DB/화면은 후속이며 handoff_ready를 전체 완료로 표시하지 않는다.
- LangSmith 기존 로컬 trace를 재사용하고 기존 trace 명령으로 후속 전송 가능. 큐 CLI는 자동 외부 trace 업로드하지 않는다.

사용자 Docker 시작 후 독립 PostgreSQL에서 큐20검사 모두 통과, 기존 BE27 포함 고유47통과다. DB 스키마 반복적용·앱 DELETE 거절·서로 다른 세션의 잠금·접수/복구 확인(9.344초), 임시 컨테이너를 정리했다. 원격 Runpod/S3는 합성이고 기존 서비스 DB/클라우드/모델에는 적용하지 않았다. 직접 DB 연결·단일 호스트 공유 spool 전제이며 DB·영상 파일을 함께 보존해야 한다. 다음은 사용자 CLI로 큐스키마/CPU 이미지와 GPU API를 준비하고 실제 영상 연결을 확인하는 것이다. 아래 미구현 설명은 해당 시점의 이력이다.

## 2026-09-28 — 완료 확인·상태 보관·병렬 처리의 구분

선택 기준: 현재 영상2~3건 시연에서 완료 감지에 약2초의 조회 간격을 허용한다면 기존 polling이 구현·운영 부담이 적다. 콜백은 완료 후 바로 통지하고 반복 조회를 줄이는 장점이 있으므로 polling의 보편적 우위를 주장하지 않는다. 현행 Mac BE 시험에는 외부에서 접근 가능한 수신 API가 없고, 사용자 Pod API의 콜백은 별도 구현이 필요하다. 서비스가 상시 운영되고 지연/조회량이 중요해지면 완료 콜백+누락 확인용 저빈도 polling을 검토한다. 이는 선택 기준 설명이며 변경 승인이 아니다. [Runpod Pod 공식 문서](https://docs.runpod.io/pods/configuration/expose-ports)는 긴 작업에 작업ID·진행 조회·백그라운드 큐와 polling을 안내한다. Serverless의 기능을 현행 사용자 Pod API의 구현으로 간주하지 않는다.

현재 완료 확인은 callback/webhook이 아니라 BE의 polling이다. `worker.py::submit_once`가 접수 ID를 저장하고 `poll`이 기본2초 간격으로 상태를 조회한다(기본 대기 상한300초, 이후 resume). Runpod는 자체 status.json을 갱신하고 GET 요청에 응답한다. BE는 state.json/provider-status.json에 기록하며 succeeded와 S3 참조를 검증해 evidence_ready로 바꾼다. 조회는 새 추론 요청이 아니다. S3 근거 준비와 Gemini 설명/보고 완료는 별도 단계다.

서비스의 공용 대기 순서는 향후 PostgreSQL 작업 테이블의 접수시각/상태/배정 정보로 관리한다. 콜백을 추가하면 Runpod가 BE 수신 API로 완료를 알리지만 인증·중복 통지 처리·유실 시 상태 조회가 필요하다. 현재 소규모 연결은 기존 polling을 유지하며 콜백 구현을 선행 조건으로 삼지 않는다.

한 GPU의 여러 영상 동시 실행은 원천적으로 불가능하다고 단정하지 않는다. 다만 현재는 소비자1개이며 병렬 프로세스 실행 시 모델/입력 메모리와 GPU 경합, 처리량/지연 실측이 필요하다. max_active를 올리는 것은 접수 용량 변경일 뿐 추론 병렬화가 아니다. 별도 GPU Pod에 한 건씩 배정하는 병렬 처리도 추가 배정 코드와 GPU 비용이 필요하다. GPU가 B를 분석하는 동안 Gemini가 A를 설명하는 단계 간 병행은 별도 BE 후속 처리로 구현할 수 있다. 이번에는 설명만이며 설정·코드·원격 실행을 변경하지 않았다.

## 2026-09-28 — 영상 2~3건과 두 worker의 역할

BE worker는 CPU에서 제출·진행 조회·결과 회수·후속 처리 순서를 관리하고, Runpod worker는 X3D/YOLO 추론·원본 클립/프레임 생성·S3 업로드를 실행한다. 현재 BE CLI는 한 작업마다 실행하는 방식이며 공유 대기열과 상시 배정 프로세스는 아직 없다.

현행 `service.Jobs`는 소비자 하나가 분석과 S3 저장을 순서대로 끝낸다. `max_active=2`는 업로드 중/대기/실행의 합계다. A 실행 중 B가 대기하면 C 제출은429로 거절된다. 기존 `tests/runpod/test_service.py::test_one_worker_and_bounded_queue`는 합성 runner로 접수2/거절1, 최대 동시실행1을 검증했다. 이번 설명에서는 코드를 읽었으며 GPU 성능/처리시간을 측정하거나 시험을 다시 실행하지 않았다.

여러 영상을 받는 서비스에서는 BE가 먼저 각 작업을 PostgreSQL 대기 목록에 기록하고 Runpod 용량에 맞춰 배정한다. 첫 구성은 GPU 실행1/Runpod 대기 최대1, 나머지는 BE에서 기다리게 한다. 각 서비스 작업과 provider_job_id를 연결해 근거·설명을 섞지 않는다. 제출/상태 조회와 Gemini 후속 처리를 나누면 A의 설명 생성 중 B의 GPU 분석을 진행할 수 있으나, 이 자동 배정/병렬 후속 처리는 미구현이다. 명확한429는 대기 후 재시도, 접수 여부가 불확실한 timeout은 기존 ID/제출 기록 조사로 처리한다. LangSmith는 이런 단계의 기록을 보여주며 작업 대기열을 대신하지 않는다.

바로 다음 확인은 GPU 복구 후 기존 한 영상의 BE→Runpod→S3 연결이다. 다중 영상 구현은 기존 후속 범위로 남기며 이번 문의에서는 설정·모델·클라우드를 변경하지 않았다.

## 2026-09-28 — BE→Runpod 선행 구현·LangSmith 기본 연결

사용자가 기존 분류 보완보다 **BE worker의 Runpod 제출 흐름을 먼저** 진행하고 LangSmith를 함께 연결하도록 요청했다. [CPU worker 실행 안내](../../apps/be_worker/README.md)에 흐름·현재 계약·LangSmith 프로젝트/키 설정·사용자 CLI를 준비했다. 계정은 있고 프로젝트/키 안내가 필요하다는 답변을 반영했다.

- `apps/be_worker/worker.py`: MP4 사본/해시와 제출 의도를 먼저 기록→`POST /jobs`1회→ID 저장→제한된 상태 조회→S3 완료 참조. `resume`은 기존 ID 조회만 하며 접수 응답 유실은 자동 재제출하지 않는다.
- `collect`: 기존 scene 검증 함수를 재사용해 S3 manifest/사건 목록을 검사하고 `handoff.json`·`scene-event_*.json` 생성. 기존 `scene.py fetch --config ...`로 실제 미디어 검증을 이어갈 수 있다. collect 자체는 메타데이터 검증까지다.
- `tracing.py`: 허용 필드만 로컬 기록, 사용자 `--langsmith` 선택 시 공식 REST API로 완료된 기록 전송. 키/미디어/URL/개인 경로 제외, 실패는 로컬 기록을 보존하고 `trace`로 전송만 재개한다. 각 CLI 실행은 별도 trace, 같은 worker_job_id로 연결한다. GPU 내부 계측/모델 사용량 자동 수집은 아직이다.
- **배포 상태:** Python CLI/재사용 함수와 파일 기반 한 작업 재개를 구현했다. 웹 API·PG 작업 큐·상시 EC2 worker/화면·Gemini/RAG 보고 자동 연결은 이번에 배포하지 않았다. 기존 Runpod API·모델·Terraform·DB와 원본은 수정하지 않았다.
- 검증: 신규27시험(기존 Runpod ASGI를 통한 실제 루프백 CLI HTTP 포함)·scene18·Runpod API13 통과. GPU/S3 업로드/Gemini/LangSmith 실제 호출0. 첫 루프백 포트 바인딩은 샌드박스에서 거부되어 테스트에 한해 허용된 실행으로 검증했고 HTTP 오류 응답 정리 경고는 close 처리 후 사라졌다.

다음 사용자 확인은 Mac에서 설정 생성→원본 한 건 run→S3 collect→LangSmith 기록 확인이다. 현재 EC2를 켤 필요는 없다. 실제 출력 확인 뒤 사고 의심/유형 계약 보완과 Gemini·DB 연결로 이어간다. 상세 원본 근거: `artifacts/be-worker/be-runpod-20260928-01/verification.json`.

## 2026-09-28 — BE가 Runpod에 보내는 내용과 회수 순서

현재 구현의 근거는 `apps/runpod_worker/client.py`, `service.py`, `storage.py`다. 아래는 이 API에 BE를 연결할 설계이며, 서비스 전체의 자동 연결이 완료됐다는 뜻은 아니다. 기존 수동 제출·S3 저장·Mac Gemini 설명 실행과 구분한다.

| 구분 | 현행 계약 |
|---|---|
| 제출 | BE worker가 Runpod HTTPS 주소의 `POST /jobs` 호출 |
| 요청 본문 | 원본 MP4 파일 바이트. JSON이나 S3 주소를 받는 구현은 아직 없음 |
| 헤더 | `Authorization: Bearer <분석 API 토큰>`, `Content-Type: video/mp4`, `Content-Length: <바이트 수>` |
| 인증 | Pod 앱의 `.api-token`에 해당하는 토큰. Runpod 계정 API 키와 구분하며 화면에 공개하지 않음 |
| 미리 설정할 것 | X3D/YOLO 가중치·실행 설정, S3 버킷/리전과 업로드 자격 증명은 Runpod에 설정. 매 제출마다 모델/키를 보내지 않음 |
| 접수 응답 | HTTP202, `job_id`와 `status=queued` |
| 완료 조회 | BE worker가 `GET /jobs/{job_id}`로 확인. 조회는 새 GPU 추론을 만들지 않음 |
| S3 업로드 완료 | `status=succeeded`, `s3_uploaded=true`, `s3.bucket/region/manifest_key`. manifest에서 사건별 파일 위치·해시·시각 확인 |

1. 화면의 영상 업로드를 BE가 접수하고 자체 작업 식별자를 만든다. BE worker는 BE의 백그라운드 처리 코드이며 이 설계에서 EC2의 CPU를 사용한다.
2. worker가 원본 MP4를 Runpod에 전송하고 반환된 provider `job_id`를 자체 작업과 연결해 저장한다. 현행 API에는 멱등 제출 키가 없으므로 응답 지연 때 동일 POST를 무조건 반복하면 중복 분석이 생길 수 있다. 이 복구 처리는 BE 구현 항목이다.
3. Runpod의 GPU가 X3D 후보→YOLO 객체를 처리하고 CPU가 원본 전후 클립·프레임·JSON을 만든다. S3 업로드 설정이 있으면 근거 파일을 올리고 완료 manifest를 마지막에 올린다.
4. BE는 완료 상태와 S3 참조를 확인하고 필요한 사건의 실제 클립·프레임을 S3에서 회수·검증한다. 이 단계에서 클립을 다시 자르거나 모든 결과 ZIP을 다시 받을 필요는 없다.
5. BE가 실제 미디어와 객체/시간 정보를 Gemini에 전달한다. 응답 구조를 검증하고 사고 의심 여부·유형·설명을 DB에 저장한 뒤 정해진 조건에 따라 설명이 붙은 의심 알림을 표시한다. 충돌 없음과 사고 없음은 구분하는 보완이 선행돼야 한다.
6. 이후 사건 설명을 검색 문장으로 임베딩→기존 pgvector 검색→본문/출처를 Gemini에 전달해 보고를 생성한다. 보고 완성을 기다려 최초 의심 알림을 늦추는 새 요구는 추가하지 않는다.

기존 S3 근거로 VLM/검색/기록을 확인할 때는 4번부터 시작한다. GPU 추론을 다시 수행할 필요가 없다. 현재 제한은 MP4 200MiB/120초, GPU 동시 처리1, 업로드·대기·실행 합계 기본2이며 초과는429다. 후보 없음·판정 불가·처리 실패는 별도 상태로 유지한다. 전체 흐름과 LangSmith 기록 위치는 [VLM/RAG 문서](vlm_rag.md)를 따른다.

## 2026-09-27 — 실제 설명 접수와 사고 유형 범위 보완

사용자 Mac에서 S3 회수→실제 Gemini 설명까지 성공했고 원문/요청·미디어 해시·코드 SHA·DB 입력 형태를 로컬 대조했다. 결과는 `artifacts/vlm/fd20cca0-event000-description-v1/result.json`, 분석 ID는 `0b043cde-e3be-49a9-a6c9-2bf0d3f10c74`다. DB 저장/화면/보고는 아직이다.

응답은 트럭 전도를 설명하면서 `no_visible_collision`을 반환했다. 현행 충돌 중심 enum으로는 비충돌 사고 여부를 표현할 수 없으므로 **이 값을 사고 없음이나 모든 사고 알림 보류로 매핑하지 않는다.** 아래 이력의 collision_suspected만 알림/no_visible_collision이면 보류라는 초기 제안은 이 문제를 해결하기 전 적용하지 않는다. 사고 의심 평가와 관찰된 사건 유형(충돌·전도 등)을 분리하는 보완이 다음 작업이며 현재 원본 JSON/스키마를 임의로 바꾸지 않았다. 실제 영상 정답 검수는 미완료이고 설명 텍스트의 단어만으로 사고를 확정하지 않는다.

## 2026-09-27 — 한 사건의 설명·저장 CLI 준비

[scene_description 실행 안내](../../apps/scene_description/README.md)에 기존 S3 `fd20cca0be53483696e6704e7c985b84/event_000`의 실제 클립/프레임을 활용하는 코드와 사용자 명령을 준비했다. 아래 '첫 연결 작업'의 회수·설명·저장 부분을 CLI/함수로 구현한 것이며 상시 BE/API/화면 배포는 아직이다.

- `scene.py fetch/load_bundle`: 원격 완료 manifest·원본 SHA·event·파일 크기/해시·시각 확인, 선택된 파일만 받기. Mac은 기존 AWS CLI 프로필, IAM 연결 후 EC2는 호스트 boto3 자격 증명 경로를 사용한다.
- `scene.py describe/build_request`: 실제 MP4+PNG와 YOLO/시각 메타데이터를 Gemini에 전달. 요청 전 기록을 저장하고 응답 원문·검증 결과를 별도로 보존한다. 자동 재시도 없이 실패를 구분한다.
- `db.py store`, `schema.sql`, `db.sh`: 기존 Compose DB의 별도 설명 테이블에 저장·조회. 같은 analysis_id로 다른 설명을 덮어쓰지 않는다. 자료 임베딩과 별개이며 이 단계는 Gemini를 호출하지 않는다.
- 출력은 `cctv-scene-v1`이며 provider_job_id/event_id/analysis_id, source 참조, description, 모델·프롬프트·사용량을 포함한다. 서비스 job/run 매핑과 HTTP 응답 어댑터는 후속이다.

합성 단위18시험과 별도 실제 로컬 PostgreSQL 저장/조회 시험은 통과했다. 원격 S3 읽기·실제 Gemini 설명·EC2 적재는 아직 실행하지 않았으며 사용자 CLI 결과로 확인한다. 다음 한 작업은 Mac의 fetch 성공 확인이다. 설명을 검토한 다음 화면에 연결하고, 이후 기존 문서 검색과 출처 있는 보고를 붙인다. 기존 'VLM 설명 후 첫 의심 알림' 결정을 유지한다.

## 2026-09-27 — 클라우드 기반 이후 첫 연결 작업

최신 배포 기록 기준 서울 EC2의 Compose PostgreSQL/pgvector와 실제 문서벡터2행은 준비됐다. 사용자 출력으로 DB healthy와 Gemini 키 저장을 확인했고 EC2의 새 질문 검색 결과는 미수신이다. Runpod→S3 성공은 앞선 사용자 보고로 유지한다. 아래 이력의 '버킷 아직 없음'이나 'DB 미배포'를 현재 상태로 사용하지 않는다.

다음 서비스 작업은 기존 S3 근거 한 건을 재사용해 **BE 회수→Gemini 장면 설명→DB 저장→화면 표시→문서 검색·보고**를 연결하는 것이다. 권한 준비와 독립적으로 로컬 코드/계약/실패 처리를 준비할 수 있고, 실제 S3·Gemini 연결은 사용자 CLI에서 실행해 확인한다.

1. BE가 검증한 manifest 참조를 입력받아 event/원본 시각/해시를 대조하고 클립·프레임·객체 JSON을 회수한다.
2. 실제 영상 지원 Gemini 생성 모델에 미디어와 객체 메타데이터를 보내 구조화된 설명·불확실성을 받는다. 임베딩001을 영상 설명 모델로 사용하지 않는다. 생성 모델 ID/프롬프트는 설정과 결과에 기록한다.
3. BE가 run/event 일치·응답 구조를 검증하고 설명/실패를 DB에 저장한다. 화면에는 근거와 설명을 연결하고 기존 사용자 선택인 VLM 설명 후 사고 의심 표시를 유지한다.
4. 검증한 설명을 질의로 기존 pgvector 문서 검색을 연결하고 본문·출처로 보고 초안을 생성한다. 현재 두 법령 조각의 검색은 기능 확인용이며 사례/법령 근거가 충분하다고 표시하지 않는다.

첫 완료 기준은 한 건의 실제 근거와 설명이 같은 event에 연결돼 DB에 저장·조회되는 것이다. 화면·보고 전체가 이미 구현됐다는 뜻은 아니다. Terraform은 필요한 AWS 접근 연결을 맡고 이 흐름 자체는 BE·화면 코드/Compose 배포로 구현한다. IAM 요청 항목은 [인프라 실행 안내](../../infra/terraform/cpu-rag/README.md)의 일괄 요청 목록을 따른다.

## 2026-09-27 — 여러 영상의 비동기 접수와 병렬 배정

[인프라 문서의 최신 시연 계획](infrastructure.md)을 따른다. 현재 Runpod API는 비동기202/job_id·단일 GPU 소비 작업이며 기본 max_active2는 업로드/대기/실행 합계다. 실제 GPU 동시 수는1이고 초과 요청은429다. 최신 재개 요약의 S3 연결 성공은 사용자 보고이며 여러 영상·BE/Gemini 통합 완료는 아직 아니다.

- BE DB가 서비스의 대기 목록을 관리하고 작업별 provider_job_id와 실제 실행/완료 시각을 기록한다. 여러 요청을 접수한 것과 여러 추론이 동시에 실행된 것을 구분한다.
- dispatcher의 제출, 완료 조회, Gemini/RAG 후속 처리를 나눠 한 영상의 느린 응답이 다른 영상 전체를 막지 않게 한다. 실행 슬롯과 lease를 DB에서 점유하며 새 Redis/Celery 설치는 이번 선행 조건이 아니다.
- provider가 명확히 거절한429는 용량이 생길 때까지 대기한다. 접수 여부가 불확실한 timeout은 기존 중복 방지·조사 절차를 유지한다.
- 같은 GPU라면 첫 시연은 두 영상 접수/한 건 실행/한 건 대기다. 실제 병렬을 선택하면 별도 작업자2개에 배정하고, 세 번째 작업은 BE에서 대기시킨다. 현재 Pod 재시작 시 미완료 작업은 실패로 표시되므로 DB 기록만으로 자동 재추론이 구현됐다고 하지 않는다.
- 결과 등록은 job/run/input SHA 기준으로 격리한다. Gemini 설명 이후 의심 알림이라는 사용자 선택을 유지하며, 다른 작업의 설명/근거를 섞지 않는다.

위 항목은 추가 구현 기준이다. 현재 GPU 동시 수·API·실행 설정은 변경하지 않았다.


## 2026-09-27 최신 구현: Runpod 근거·S3

Runpod worker의 전후 클립·PNG/evidence.json 생성과 선택적 S3 업로드 코드/로컬 검사를 완료했다. 기본 local 모드이며 실제 버킷은 아직 없다. [실행·S3 권한·BE 전달 계약](../../apps/runpod_worker/S3_SETUP.md)을 따른다. BE는 status의 s3.manifest_key→원격 manifest→evidence.json과 미디어를 회수하고 Gemini API를 호출한다. 원격 manifest가 완료 marker다. 후보 첫 알림은 설명과 함께 전달하는 사용자 선택을 유지한다. 실제 Runpod 새버전/S3/BE/Gemini/재생 시연 통합은 미검증이다. 아래 과거 미구현/Modal 상태는 이력이다.


기준: [PRD](../../PROJECT_BRIEF.md) · [공통 계약](../contracts/service_contract.md). API/enum을 이 문서에서 별도로 재정의하지 않는다.

## 2026-09-27 인수인계 — Runpod 연결

**Runpod 분석 결과의 첫 전달 대상은 BE worker다.** 한 건의 succeeded 상태와 후속 해제결과5파일을 접수해30일관성검사를 통과했다. [실제 결과 샘플](../../artifacts/runpod/a4000-20260927-01/received/fd20cca0be53483696e6704e7c985b84/outputs/result.json)과 [검증 기록](../../artifacts/runpod/a4000-20260927-01/received/fd20cca0be53483696e6704e7c985b84/verification.json)을 사용할 수 있다. 후보1개/Car탐지박스3개지만 겹침이 커 실제차량3대로 세지 않는다. [현재 배치·담당별 전달 항목](infrastructure.md)과 [실제 Runpod API 계약](../contracts/service_contract.md)을 먼저 따른다.

BE가 받을 묶음은 ①HTTP8000 주소 ②앱 토큰 전달 방식 ③API 명세 ④성공 결과 ZIP ⑤모델/분석 프로필·제한·실패 사례다. `.api-token` 값은 서버 비밀 설정에 보관하며 UI·문서에 넣지 않는다. 기존 `client.py`는 이 파일을 자동으로 사용한다.

서비스 `job_id/run_id`→`provider=runpod_pod, provider_job_id`를 DB에 연결한다. 현재 Runpod `POST /jobs`는 MP4 바이트를 받으므로 팀 API의 JSON POST와 동일하게 취급하지 않는다. S3 객체 참조 방식은 추가 구현한다. Runpod의 succeeded 이후 결과 스키마·입력 SHA·manifest 파일 SHA·범위를 확인하고, 근거/S3/후속 단계 상태를 확인해 서비스 상태를 정한다. succeeded를 곧바로 보고 완료로 매핑하지 않는다.

## 책임

### 2026-09-27 후속 — VLM 설명을 포함한 알림

사용자는 X3D후보→YOLO→실제근거→VLM설명→BE조건판단→알림 순서를원한다. 내부후보접수와사용자알림을별도로저장하고VLM검토이전에사고알림을자동발송하지않는다. 현재Runpod한건API에는재생중후보/근거상태회수가없어이를추가해야한다. 알림채널은미정이며외부메시지발송은이번작업에포함하지않는다.

BE는VLM의구조화응답·실제근거ID·run/event일치를검사하고합의한정책으로알림여부를결정한다. `collision_suspected`이면설명/클립과함께의심알림, `uncertain`/실패는검토대기, `no_visible_collision`은이번알림보류와기존X3D후보보존을초기정책으로제안한다. 최종정책의미탐/오탐은실제검수자료로검증해야한다. 사람의사고확정과AI알림조건을구분하며텍스트에‘사고’라는단어가있는지만검사하지않는다. 알림ID·revision·생성/표시시각·근거/정책버전을기록하고같은run/event의중복알림을막는다.

인프라에서받을등록영상참조와사용자Runpod API를연결하고, FE에는같은원본·재생세션시계·준비된알림/근거/설명을제공한다. 재생제어(시작/정지)와추론진행은서버가관리하며화면재접속으로새GPU요청을만들지않는다.

영상 접수·검증·S3 저장, 영구 작업 기록, Runpod 제출/회수, 결과 등록, VLM/RAG/보고 순서 관리, 화면 조회, 사람 검토 저장을 담당한다. API 요청과 장시간 작업을 분리하되 초기에는 같은 저장소·DB를 쓰는 별도 작업 프로세스로 구성한다.

## 구현 단위

| 단위 | 입력→출력 | 확인할 실패 |
|---|---|---|
| 영상 API | 파일→검증된 video·S3 참조 | 손상/형식/크기/저장 실패, 중복 접수 |
| job API | video+profile→job/run 대기 기록 | 미등록 프로필, 동일 키의 다른 요청 |
| dispatcher/reconciler | DB 대기/미완료 run→Runpod API 호출/검증된 결과 | POST 접수 불확실, 중복·지연 결과, 재시작 |
| enrichment worker | 후보·장면→VLM/RAG/report | API timeout·형식 오류·검색 실패·근거 부족 |
| 조회 API | job/run/asset→허용된 결과·URL | 다른 run 혼합, 만료 URL, 접근 불가 |
| review API | 사람 판단→변경 이력 | 오래된 revision·중복 제출·다른 event |

## DB 제안

PostgreSQL에 videos, jobs, runs, events, assets, reports, reviews와 stage_tasks를 둔다. 문서/청크/검색 인덱스는 RAG 담당과 연결한다. 큰 영상·프레임 bytes는 S3에 두고 DB에는 참조·해시·버전을 저장한다.

run 상태와 active_run_id, 모델/입력 버전, provider/provider_job_id, stage 상태, lease 만료·시도 수·오류를 저장한다. 요청자+endpoint+Idempotency-Key, run+event, run+manifest 식별자에는 유일성 검사를 둔다. 임의의 URL을 실행 입력으로 받지 않고 등록된 S3 객체만 연결한다.

## 작업 안정성

- POST /jobs는 DB 커밋 후 202를 반환한다. GPU 완료까지 HTTP 연결을 유지하지 않는다.
- 별도 프로세스는 DB lease로 작업을 점유한다. 장애 후 만료된 lease를 회수하되 외부 호출 접수 여부를 먼저 확인한다.
- Runpod provider_job_id 저장 직전 장애처럼 불확실한 상태를 명시적으로 다룬다. 현재 GPU POST는 중복 방지 키를 지원하지 않으므로 동일 요청을 무작정 재제출하지 않는다.
- S3 manifest를 검증한 뒤 결과를 등록한다. 파일 목록만 있다고 completed로 표시하지 않는다.
- 후속 단계 실패 시 후보를 보존하고 부분 보고를 만든다. 재처리는 필요한 단계만 실행하도록 stage_tasks와 보고 revision을 사용한다. 첫 버전의 사용자 재시도 API는 전체 새 run이며 단계별 수동 재시도는 내부 운영 경로로 제안한다.
- VLM/RAG는 GPU 함수가 끝난 뒤 BE에서 호출한다. 응답 원문·검증 오류는 민감 내용을 줄여 기록하고 사용량을 남긴다.

## 받을 것·넘길 것

모델 담당에게 manifest와 model profile, FE에게 API 명세·가상 응답·오류 사례, VLM/RAG에게 후보별 입력 및 저장 필드, 인프라에게 배포 프로세스/환경변수 이름/권한 범위를 전달한다. `.env` 실제 값은 문서·저장소에 넣지 않는다.

## 첫 작업과 완료 기준

BE 내부 접수→상태→후보→검토 저장은 가상 worker 결과로 연결할 수 있다. 실제 Runpod 한 건의 성공 상태는 수신했으므로 다음에는 그 결과 ZIP을 검증하고 AWS BE에서 외부 HTTPS로 제출·회수하는 통합을 확인한다. 기존 로컬 성공을 외부 BE 연결 성공으로 바꾸어 기록하지 않는다.

완료 확인: 프로세스 재시작 후 작업 복구, 중복 요청 하나의 job 반환, 동일 결과 중복 등록 방지, 옛 run 결과 격리, 손상 manifest 거절, 실패를 정상으로 표시하지 않음, 검토 충돌 감지. [QA 문서](validation.md)에 실제 결과·로그를 남긴다.
