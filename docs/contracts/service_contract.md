# 공통 API·데이터 계약 — service-draft-v0.2

작성일 2026-09-26. **개발 착수를 위한 제안 명세이며 구현된 API가 아니다.** PRD의 기능 범위를 FE·BE·모델·VLM/RAG가 같은 이름으로 연결하기 위한 기준이다. 실제 저장소에 존재하는 R3D 결과 JSON과 자동 호환된다고 가정하지 않는다.

공통 필드·상태·API 변경은 이 문서에서 먼저 한다. 역할별 문서는 이를 링크한다. 예시는 [가상 응답 모음](demo-analysis-cases-v0.2.json)이며 실제 영상·예측·문서 인용을 포함하지 않는다.

## 2026-09-27 현재 연결 기준 — Runpod Pod API

외부 GPU 실행 환경은 사용자가 배포한 **Runpod A4000 Pod**다. 아래 4절의 Modal 전용 SDK 방식은 이전 설계 참고로 보존한다. 팀 서비스의 `/api/v1` 계약은 여전히 구현 제안이며, Runpod에서 동작한 `runpod-local-api-v1`과 경로·요청·상태를 혼용하지 않는다.

### 현재 구현된 Runpod API

기본 주소는 Runpod Connect의 **Port 8000 → HTTP Service** 주소다. 모든 요청에 `Authorization: Bearer <앱 토큰>`이 필요하다. `start.sh`가 생성하는 `.api-token`을 `client.py`가 자동으로 읽는다. Runpod 계정 API 키나 S3 자격증명이 아니다. AWS BE 연결 시 `RUNPOD_BASE_URL`, `RUNPOD_WORKER_TOKEN`을 서버 설정 이름으로 제안하며 실제 값을 문서·브라우저에 넣지 않는다. 이 환경변수 연결은 아직 BE에 구현하지 않았다.

| 메서드·경로 | 실제 요청·응답 |
|---|---|
| GET /health | 인증된 API 응답 확인. 모델 추론 검사는 하지 않음 |
| POST /jobs | `Content-Type: video/mp4`, 본문은 MP4 원본 바이트. JSON·multipart·S3 key 입력은 현재 미지원. 202 `{job_id, status: queued}` |
| GET /jobs/{provider_job_id} | `uploading/queued/running/succeeded/failed` 상태와 입력 SHA·크기·시각·오류 |
| GET /jobs/{provider_job_id}/result | succeeded일 때 결과 ZIP, 그 외 409 |
| GET /jobs/{provider_job_id}/log | 생성된 worker.log 회수. 실패 시에도 사용 |

URL의 `provider_job_id` 자리에는 Runpod 앱 응답의 `job_id`를 넣는다. 예를 들어 사용자 성공 작업은 `fd20cca0be53483696e6704e7c985b84`다. 이는 **AWS 서비스의 job_id/run_id와 별개 ID**다. BE는 `provider=runpod_pod`, `provider_job_id`, 서비스 job/run/source_video_id의 연결을 DB에 보관한다. Runpod Serverless의 `/run`·`/status` API와도 구분한다.

현재 한도: 영상120초·200MiB, GPU 동시1, 업로드/대기/실행 합계2, 보관작업10, worker300초, 요청 업로드90초. 인증 실패401, 입력 형식415, 선언된 요청 크기200MiB 초과413, 대기/보관/디스크 한도429, 결과 미완료409를 처리한다. 영상120초 초과는 worker 검사에서 failed가 되며 업로드 중 크기 초과/불완전 수신은400이다. 상세 오류는 응답과 작업 로그를 확인한다. 현재 POST의 중복 방지 키는 미구현이므로 응답을 잃었다고 자동으로 재제출하지 않는다.

### 결과 회수와 서비스 상태 변환

ZIP 구성은 `result.json`, `vlm_handoffs.json`, `runtime.json`, `manifest.json`, `worker.log`다. manifest의 세 JSON에 대한 크기/SHA를 검증하고, runtime/manifest/status의 입력 SHA가 등록한 원본과 같은지 확인한다. ZIP을 해제할 때 경로를 제한하고 코드로 실행하지 않는다.

- `result.json`: X3D 창·판정·후보와 후보 장면의 YOLO 객체. 후보가 없으면 YOLO 추론은 생략될 수 있다.
- `vlm_handoffs.json`: 후보 시각·객체 메타데이터. 실제 VLM 응답이나 영상·이미지 파일이 아니다.
- `runtime.json`: 환경·전처리 검사·단계별 시간·GPU 메모리. 이번 성공 상태만으로 이 수치나 후보 수를 추정하지 않는다.
- Runpod `succeeded`는 **GPU 분석 작업과 로컬 결과 묶음 생성 완료**다. 서비스 전체의 `completed`로 바로 바꾸지 않는다. 근거/저장/후속 단계 조건을 확인해 `enriching`, `completed`, `partial`을 결정한다.
- `failed` 또는 결과 없음은 `no_candidates`가 아니다. `Normal` 문자열만 복사하지 않고 5절의 범위·창 수 검사를 거친다. `event_000` 같은 ID는 run과 함께 식별한다.

### S3 연결 시 추가할 계약 — 아직 미구현

목표는 기존 아키텍처대로 **BE가 등록한 S3 원본 참조를 제출 → Runpod이 원본을 읽고 분석/근거 생성 → S3 결과·manifest 저장 → BE가 회수·검증·DB 등록**이다. 현재 POST에 S3 JSON을 보내면 작동하지 않으므로 새 요청 형식과 버전을 구현한 뒤 연결한다.

요청에 서비스 job/run/source_video_id, 고정 분석 프로필, 입력 bucket/key/가능한 version_id/SHA, 허용된 결과 prefix를 연결한다. 등록된 객체·허용된 prefix만 받고 임의 URL을 그대로 다운로드하지 않는다. 결과는 run/attempt별 새 경로에 기록하고 업로드·해시 확인 후 manifest를 마지막에 저장한다. 반환할 manifest 참조와 provider_job_id를 BE가 DB에 보관한다. GPU 앱은 DB를 직접 수정하지 않는다.

자동 재시도를 붙이기 전 요청 중복 방지와 접수 여부 불명확 상태를 구현한다. S3의 기존 run manifest 및 BE의 provider_job_id를 대조해 회수하며 이전 run 결과를 현재 run에 덮어쓰지 않는다. Pod 임시 디스크의 상태는 영구 기록으로 사용하지 않는다.

## 1. 식별자와 저장 책임

| 항목 | 정의·책임 |
|---|---|
| `source_video_id` | BE가 등록한 원본 영상 ID. 파일 이름은 표시용이며 모델 입력에 사고 암시 정보로 전달하지 않음 |
| `job_id` | 사용자의 한 분석 요청. 원본·분석 범위·요청자·선택한 설정 버전 연결 |
| `run_id` | 실제 처리 시도. 재분석은 새 ID. 이전 run의 결과·실패·검토는 보존 |
| `provider_job_id` | 현재 Runpod 앱이 반환한 job_id. provider와 함께 BE 내부 보관. 서비스 job/run과 별개 |
| `call_id` | 이전 Modal 설계의 호출 ID. 현재 Runpod 연결에는 사용하지 않음 |
| `event_id` | run 안에서 생성한 검토 후보 ID. 실제 사고 사건의 확정 ID가 아님 |
| `asset_id` | 원본·클립·프레임·manifest 등 파일의 ID. DB가 bucket/key/version/checksum과 연결 |
| `report_id`, `revision` | 후보에 연결된 보고 초안과 버전. 후속 단계 재시도로 새 버전 생성 |
| `review_id` | 사람이 특정 event/run/report revision을 보고 남긴 판단 기록 |

모든 서버 시각은 UTC ISO 8601, 영상 시각은 원본 시작 기준 초(double)다. `recorded_at`과 `camera_id`는 알 때만 채운다. 실행 경과시간, 영상 상대시각, 실제 촬영 시계를 혼합하지 않는다.

## 2. 작업 상태와 판정 분리

`status`: `queued`, `dispatching`, `running`, `enriching`, `completed`, `partial`, `failed`.

| 상태 | 정의 |
|---|---|
| queued | 요청·run이 DB에 저장되어 처리 대기 |
| dispatching | BE가 제출 중. 접수 여부가 불명확하면 여기서 사유와 확인 기한 유지 |
| running | 외부 GPU 작업 접수 확인, 결과 회수 전. GPU 실행 시작 시각은 별도로 기록하며 호출 접수를 실행 시작으로 표시하지 않음 |
| enriching | 후보·영상 근거가 등록됐고 설명·검색·보고를 처리 중 |
| completed | 요청 범위 처리·필요 단계가 끝남. 후보 없음, RAG 근거 부족도 정상 종료일 수 있음 |
| partial | 사용할 결과가 있으나 미분류/미처리 또는 필수 단계 실패가 남음 |
| failed | 사용할 모델 판정·근거가 없거나 안전하게 결과를 등록할 수 없음 |

기본 전이: queued→dispatching→running→enriching→completed/partial. 후보 0이면 enriching을 생략할 수 있다. 처리 중 실패 시 usable 결과가 있으면 partial, 없으면 failed다. terminal 상태의 재분석은 새 run으로 queued부터 시작한다.

`detection_outcome`: `pending`, `candidates_found`, `no_candidates`, `unknown`. `no_candidates`는 예정된 모든 창의 유효 판정이 있고 후보가 0일 때만 사용한다. 후보 0이라도 미분류/미처리가 있으면 unknown이다. 모델이 판정한 결과이며 실제 사고 부재를 확정하지 않는다.

단계 키: `objects`, `accident`, `evidence`, `vlm`, `rag`, `report`. 각 값은 `{status, reason_code}`이며 단계 상태는 `pending/running/completed/skipped/failed`; RAG만 `insufficient_evidence` 추가 허용. 이유 없는 skipped는 금지한다.

- 후보 0: objects/VLM/RAG/report를 `skipped/no_candidates`로 종료. 작업 요약은 계속 제공한다.
- 판정 불가: VLM/RAG/report `skipped/no_usable_candidate`이며 사고 없음으로 표시하지 않는다.
- VLM 실패: 후보는 유지, RAG `skipped/vlm_unavailable`, 보고는 실패 사실과 영상 근거만으로 조립; 작업 partial.
- RAG 근거 부족: 오류와 구분해 `insufficient_evidence`와 빈 인용, 보고에 근거 부족 표시; 다른 결함이 없다면 completed.
- RAG 연결 실패 또는 보고 실패: partial, 단계 오류 표시. UI는 기존 근거를 계속 보여준다.

사람 검토 상태는 `unreviewed/confirmed_accident/not_accident/uncertain`이며 job 상태와 독립이다. AI 완료를 사람 검토 완료로 바꾸지 않는다. 후보별 상태가 여러 개면 job의 단계 상태는 failed 우선, 다음 running, pending, insufficient_evidence, completed 순으로 집계하되 전부 skipped이면 skipped다. 원인 상세는 event에 남긴다.

## 3. API 초안

기본 경로 `/api/v1`. 모든 요청은 팀 서비스의 접근 통제 대상이다. UI에서는 DB/S3/Modal 장기 자격증명을 직접 쓰지 않는다. 초기 Streamlit 서버가 API로 파일을 전송하는 방식을 제안한다. 대용량 직업로드가 필요해지면 별도 업로드 계약을 버전 관리한다.

| 메서드·경로 | 요청 | 응답·기준 |
|---|---|---|
| POST /videos | multipart `file`; 선택 `camera_id`, `recorded_at`; `Idempotency-Key` | 201 `{source_video_id, upload_status: ready}`. API가 파일 검증·S3 저장을 마친 후 반환; 저장 실패 시 job 생성 안 함 |
| GET /videos | `cursor`, `limit` | 200 `{items, next_cursor}`. 허용된 원본만 조회 |
| POST /jobs | JSON `{source_video_id, analysis_profile_id}`; `Idempotency-Key` | 202 `{job_id, run_id, status: queued}`. DB 커밋 후 반환, GPU 완료를 기다리지 않음 |
| GET /jobs | `cursor`, `limit` | 200 `{items, next_cursor}`. ID·원본·상태·생성시각·후보 수 |
| GET /jobs/{job_id} | 선택 `run_id` | 200 JobResult. 생략하면 active run. 존재하지 않거나 접근 불가한 ID 구분 정책은 BE에서 일관되게 적용 |
| POST /jobs/{job_id}/runs | `{analysis_profile_id}`; `Idempotency-Key` | 202 새 run. 실행 중 중복 재분석은 409. 이전 run·검토 유지 |
| GET /assets/{asset_id}/url | 없음 | 200 `{url, expires_at}`. 접근 확인 후 단기 URL 발급. DB에는 URL을 영구 보관하지 않음 |
| POST /events/{event_id}/reviews | `{run_id, report_revision, decision, note, expected_review_revision}`; `Idempotency-Key` | 201 `{review_id, review_revision, reviewed_at}`. 검토자 식별은 서버 인증 문맥으로 기록; 초안이 없으면 report_revision=null |
| GET /events/{event_id}/reviews | 없음 | 200 `{items}`. 새 검토는 이력을 추가, 기존 행 덮어쓰기 금지 |

동일 Idempotency-Key+동일 내용은 최초 응답을 반환한다. 같은 키에 다른 내용은 409. 키의 범위는 요청자+endpoint, 보존 기간은 job 보존 기간 이상으로 제안한다. API 오류 형식은 `{error: {code, message, retryable}, request_id}`다. 413 크기 초과, 415 형식 미지원, 422 입력·계약 위반, 409 상태/버전 충돌, 503 서비스 일시 불가를 구분한다.

검토 저장 시 expected_review_revision이 현재값과 다르면 409 후 새 기록을 다시 확인하게 한다. 예전 report_revision에 대한 검토는 보존하고 최신 초안에서는 ‘이전 버전 검토’로 표시한다. 별도 검토자 입력만 있는 시연은 인증된 신원으로 표시하지 않는다.

## 4. 이전 Modal 입력과 결과 회수 — 현재 Runpod은 문서 상단 기준

입력 계약: `schema_version`, `job_id`, `run_id`, `source_video_id`, `input_object`(bucket/key/version_id/sha256), `output_prefix`, `analysis_profile`(모델·전처리·판정·후처리 버전, 분석 범위), `requested_at`. 모델에 원본 정답이나 사고를 암시하는 파일 이름을 넣지 않는다. 내부 입력에 S3 경로가 필요한 경우 모델 메타데이터로는 사용하지 않는다.

1. BE 작업 프로세스가 DB의 queued run을 lease(유효기간 있는 작업 점유)로 가져와 dispatching으로 기록한다.
2. 배포된 Modal 함수를 `spawn()`으로 호출하고 `call_id`를 저장한다. async SDK를 쓸 경우 `.spawn.aio()`를 사용한다.
3. BE 프로세스는 저장된 호출 ID로 `FunctionCall.get(timeout=0)` 또는 async 대응 API를 주기적으로 확인한다. 결과 대기 timeout은 실행 실패로 바꾸지 않는다. 프로세스 재시작 후 DB의 미완료 run부터 회수한다.
4. Modal은 run별 경로에 파일을 저장한 뒤 manifest를 마지막에 기록하고 `{run_id, manifest_key, manifest_sha256}`를 반환한다.
5. BE는 입력 ID·스키마·해시·실제 파일 존재·영상 범위·모델 버전·창 수를 검증하고 DB 트랜잭션으로 등록한다. 등록 이후 VLM/RAG 작업을 DB에 기록한다.

Modal 출력 조회는 영구 결과 저장소가 아니다. 만료되면 저장된 S3 manifest를 확인해 복구하며, 파일도 없으면 오류로 기록한다. [호출과 결과 조회](https://modal.com/docs/guide/job-queue), [FunctionCall API](https://modal.com/docs/sdk/py/latest/FunctionCall).

**중복·지연 처리 규칙:** DB 요청 기록과 외부 spawn은 단일 트랜잭션이 아니다. 호출 직후 장애로 call_id를 잃었다면 자동으로 새 GPU 작업을 무한 생성하지 않는다. 같은 run의 manifest·로그를 확인해 복구하고, 확인 기한을 넘기면 실패/수동 확인으로 전환한다. 재시도에도 `(run_id, event_id)`와 manifest 식별자로 결과를 한 번만 등록한다. 뒤늦은 옛 run 결과가 active run을 덮어쓰지 못하게 검사한다. Modal이 한 번만 실행한다고 가정하지 않는다. [실패·재시도 동작](https://modal.com/docs/guide/retries).

초기 구현은 API와 별도 BE 작업 프로세스+DB 작업 레코드를 제안한다. GPU dispatch/reconcile, VLM, RAG, report 단계마다 lease·시도 수·다음 실행시각·마지막 오류를 기록한다. FastAPI 요청 메모리나 Streamlit 세션만으로 작업을 이어가지 않는다. Redis/Celery는 현재 필수 의존성으로 추가하지 않는다.

## 5. 결과 데이터의 공통 필드

### JobResult

| 필드 | 형식·의미 |
|---|---|
| schema_version, is_demo | `service-draft-v0.2`, 실제 응답 false/가상 fixture true |
| job_id, run_id, source_video_id | 서로 다른 역할의 문자열 ID |
| status, detection_outcome | 2절 enum |
| video | duration_seconds, camera_id(null 허용), recorded_at(null 허용), original_asset_id |
| coverage | requested_start_seconds, requested_end_seconds, scheduled_windows, predicted_windows, unclassified_windows, pending_windows, unknown_ranges |
| models | objects/accident별 family, weights_sha256, preprocessing_version, class_map_version; candidate_policy_version·decision_rule_version |
| stages | 단계별 상태·이유 |
| candidates | Event 배열. pending/failed의 빈 배열은 정상 판정이 아님 |
| errors | `{stage, code, message, retryable}` 배열 |

`scheduled_windows = predicted_windows + unclassified_windows + pending_windows`여야 한다. 계획된 창이 0이면 no_candidates 금지. 범위 시작·끝/워밍업/영상 꼬리·미처리 부분을 unknown_ranges 또는 처리 범위 설명으로 남긴다. 전체 영상 분석을 표방하면 범위 밖 구간도 명시한다. 알 수 없는 값은 null이며 0으로 바꾸지 않는다.

### 모델별 역할

사용자 확정: X3D-S는 사고 후보 구간, YOLO는 사고 영상의 객체 정보를 제공한다. 개발 제안: X3D-S 후보 정리 후 해당 원본 장면에 YOLO를 실행해 시간 기준으로 결합한다. YOLO 출력은 X3D-S 입력이 아니다. objects 실패 시에도 X3D 후보·실제 장면을 보존하고 누락을 표시해 partial로 처리한다.

### 수신 handoff schema 1.0의 변환

수신 코드와 서비스 계약 사이 어댑터는 아직 미구현이다. 수신 파일 자체를 서비스 결과로 그대로 등록하지 않는다.

| 수신 필드 | 서비스 변환 | 주의 |
|---|---|---|
| video_id/video_filename | 등록한 source_video_id | 파일 이름으로 권한/동일성 판단 금지 |
| event_id | run 안에서 유일한 event_id | 여러 실행의 event_000 충돌 방지 |
| events.start_s/end_s | start_seconds/end_seconds | result.json의 병합 구간 사용 |
| candidate_time_s | candidate_time_seconds | 첫 양성 창 중간 시각, 정확한 충돌시각 아님 |
| available_after_video_time_s | decision_source_time_seconds | 첫 양성 창 끝, 실제 wall-clock 지연과 다름 |
| candidate_window_s | trigger_window_seconds | 첫 창 범위; 전체 후보 구간으로 대체하지 않음 |
| x3d_probability_at_trigger | trigger_score | 보정된 사고 확률로 표시하지 않음 |
| events.peak_probability | score, score_type=max_window_score | 최초 점수와 최고 점수 분리 |
| yolo_frame_time_s | 요청한 후보 프레임 시각 | 실제 디코딩한 PTS를 추가 확인해야 함 |
| yolo_objects.class_id/class_name/confidence | object_observations의 class_id/class_name/score | 7개 클래스 맵 검증 |
| box_xyxy_normalized | bbox, xyxy_normalized_original | 원본 기준 좌표 검증 |

수신 handoff에는 영상/프레임 파일이 없다. 서비스가 S3 원본에서 후보 주변 클립·프레임을 생성하고 asset_id, 실제 PTS, coverage, 모델/설정 버전과 오류 정보를 추가해야 한다. `handoff_example.json`은 가상 예시이므로 실제 모델 결과로 등록하지 않는다. 예시 모음 v0.2의 null asset/해시는 화면 상태용 허용값이며 실제 completed 결과의 검증에서는 거절한다.

### Event와 영상 근거

- `event_id`, `start_seconds`, `end_seconds`, `label=suspected_accident`, `score`, `score_type`, `prediction_ids`.
- `0 ≤ start_seconds < end_seconds ≤ duration_seconds`. 점수 집계 규칙은 candidate_policy_version에 포함한다.
- `evidence`: `clip_asset_id`, `clip_start_seconds`, `clip_end_seconds`, `frames[{asset_id, source_time_seconds}]`. 클립 시작 0초가 원본 몇 초인지 보존한다. 대표 프레임만으로 설명할 경우 입력 장면 수와 한계를 기록한다.
- `object_observations`: `source_time_seconds`, `class_id`, `class_name`, `score`, `bbox`, `bbox_format=xyxy_normalized_original`. 좌표는 원본 기준 [0,1], x1<x2/y1<y2. YOLO 입력 resize/letterbox 좌표를 그대로 넘기지 않는다.
- 수신 YOLO .pt의 ID/name은 **0 Pedestrian, 1 Car, 2 Truck, 3 Bus, 4 Motorcycle, 5 Bicycle, 6 Dynamic**이다. 그림의 6개 표기는 실제 7개로 수정한다. class_map_version은 `received-yolo-7-v1`로 제안한다. Dynamic의 라벨 의미는 미확인이므로 원래 ID/name을 보존하고 다른 클래스에 합치거나 삭제하지 않는다. [정적 확인 근거](received-model-metadata-v0.2.json). 탐지 박스만으로 동일 차량의 지속 추적 ID를 만들지 않는다.
- `vlm`, `retrieval`, `report`, `human_review`는 다음 절을 따른다. 후보가 먼저 저장되고 설명 필드는 단계가 끝날 때 추가된다.

원본 S3 경로 예: `videos/{source_video_id}/original.mp4`. 결과 경로 예: `jobs/{job_id}/runs/{run_id}/...`. 재시도 산출물은 attempt 하위에 쓰고 채택한 manifest를 DB에 연결한다. 기존 경로에 재시도 파일을 무조건 덮어쓰지 않는다.

## 6. VLM·RAG·보고 계약

### 2026-09-27 실제 응답에서 확인한 보완 사항

첫 실제 Gemini 결과는 트럭 단독 전도를 설명하면서 `assessment=no_visible_collision`을 반환했다. 이 값은 충돌 관찰 범위의 평가이며 사고 전체가 없다는 의미로 사용할 수 없다. 아래 초기 알림 제안의 'no_visible_collision→보류'는 비충돌 사고를 포괄하도록 개정하기 전 적용하지 않는다. 일반 사고 의심 여부와 관찰 사건 유형을 구분하는 프롬프트·스키마·검증기·저장 계약의 후속 수정이 필요하다. 기존 `cctv-scene-v1` 원문/결과는 보존했으며 이 확인만으로 새 enum을 배포하거나 실제 사고 정답을 확정하지 않았다.

### 2026-09-27 구현 상태 — 단일 사건 설명 CLI

`apps/scene_description`은 기존 Runpod S3 계약을 읽어 `cctv-scene-v1` 결과 파일을 만든다. 실제 클라우드 호출 전이며 합성 응답/로컬 DB 검사를 완료했다. 공개 HTTP API나 전체 서비스 계약이 배포된 상태는 아니다.

- 입력: 지정 bucket/region/provider job/event/원본 SHA, 완료 manifest, evidence.json, 선택된 실제 MP4·PNG. 원격 manifest의 job·해시·시간 기준을 확인한다.
- 결과 식별: `analysis_id`는 한 설명 시도의 UUID, `provider_job_id`는 Runpod ID, `event_id`는 근거 사건 ID다. 아직 없는 서비스 job_id/run_id를 추측해 생성하지 않는다.
- 결과 본문: `description.{assessment,summary,observations,uncertainties}`. observation은 `text`, `clip_times_seconds`, `source_times_seconds`, `asset_refs`, `evidence_asset_ids`를 포함한다. 모델 시각은 추정치이며 원본 기준 변환만 검증한다.
- 출처·실행: `source`에 S3 manifest/미디어 키·해시·시각, 최상위에 input SHA·요청/응답/코드 SHA·모델/실제 모델 버전·프롬프트 버전·FPS·사용량을 보존한다. `human_review=unreviewed`다.
- 저장: `cctv_scene.descriptions`는 analysis_id를 기본키로 사용한다. 같은 ID/내용은 중복하지 않고 같은 ID/다른 내용은 거부한다. 파일의 `database_loaded=false`는 생성 당시 상태이며 현재 적재 상태는 load 영수증과 show 응답으로 확인한다.
- 실패는 별도 `failure.json`으로 보존하며 성공한 '사고 없음'으로 처리하지 않는다. 이번 DB 도구는 완료 설명 저장만 지원한다. 서비스의 실패/재시도/알림 상태 테이블과 HTTP 어댑터는 후속이다.

아래는 전체 서비스의 목표 계약이며 CLI의 중첩 결과를 화면/API로 변환할 때 같은 근거와 ID를 유지해야 한다.

VLM 입력은 event ID, 실제 장면, 원본 시각, 탐지 메타데이터, 입력 근거 ID다. 출력:

- `status`, `summary`, `observations[{text, evidence_asset_ids, source_times_seconds}]`, `uncertainties`, `provider_model`, `prompt_version`.
- 객체·충돌·피해·원인은 확인한 것과 추정을 구분한다. 서버가 JSON 형식과 근거 ID를 검증한다.

RAG 입력은 검증된 관찰 요약·질의·문서 종류/적용 지역 등 확인된 조건이다. 출력:

- `status`, `query`, `corpus_version`, `retrieval_version`, `citations[{document_id, title, source_url, section, excerpt, published_at, retrieved_at, relevance_note}]`.
- 법령·판례·사례·지침은 corpus_kind로 구분한다. 실제 판례를 확보하지 않았다면 ‘판례 검색 준비 중/자료 미확보’를 표시하며 일반 지침을 판례로 바꾸지 않는다.
- 검색 결과가 있어도 적용 근거가 약하면 insufficient_evidence. VLM이 추측한 과실·신호 위반을 확정 검색 조건으로 고정하지 않는다.

보고 출력은 `report_id`, `revision`, `status`, `text`, `source_event_id`, `evidence_asset_ids`, `citation_document_ids`, `limitations`, `generated_at`다. 영상 관찰/모델 추정/문서 근거/사람 검토를 구분한 템플릿 조립을 첫 구현으로 제안한다. 별도 LLM 재작성은 선택하며 사용 시 모델·프롬프트·근거 검증 기록을 추가한다.

## 7. 프로필과 미정 설정

### 2026-09-27 후속 제안 — 재생 시뮬레이션과 VLM 검토 후 알림

사용자선택은 **X3D내부후보→후보장면YOLO→원본근거생성/S3→BE의VLM호출→설명/평가검증→의심알림**이다. `job.status`, 모델후보, 사용자알림은서로다른기록이다. 후보생성만으로알림상태를완료로바꾸지않고, VLM설명도사람의`confirmed_accident`로변환하지않는다. 아래필드는연결초안이며현재Runpod API에구현된것이아니다.

- 재생입력: `source_video_id`, 원본객체/SHA, `run_id`, `stream_id`, 세션시계/재생속도/입력원점. 첫제안은한영상·1배속·시작/정지이며시크/루프/재시작은새세션식별과중복방지정책이필요하다.
- 내부후보: 고유event ID/sequence, 후보시각·첫양성창·`decision_source_time_seconds`, YOLO객체, 처리한입력끝시각, 생성/회수UTC, `evidence_status`, `vlm_status`. 새분석창에서만이벤트상태를갱신한다. 후보조회/근거준비 API는추가구현하며현재최종결과ZIP의schema를슬쩍바꾸지않는다.
- 근거: 실제원본클립시작/끝·frame별PTS/크기/좌표기준, 파일SHA와S3참조, 누락/EOF잘림사유. 후행장면은설명용이며최초탐지시입력범위와분리한다. 버퍼는추론의2초프레임큐와별도로전후근거에필요한원본을유지한다.
- VLM응답추가초안: `assessment=collision_suspected/no_visible_collision/uncertain`, 관찰설명·근거asset/time·불확실성·모델/프롬프트버전. 이평가는영상근거에대한AI판단이며정답/책임판단으로취급하지않는다.
- 알림추가초안: `alert_id`, run/event/revision, `decision_reason`, `policy_version`, 설명/근거참조, 생성UTC와FE표시UTC. VLM응답형식/근거검증후사고의심조건에맞으면한번생성한다. VLM실패/불확실은검토대기, 관찰근거없음은알림보류로두되모든원래후보·오류·평가를보존하는초기정책제안이다. 이조건의미탐/오탐검수는미실행이다.

녹화영상전체를미리분석해재생시간에알림만표시하는방식은‘분석결과재생’으로표시한다. 재생중판단시연은해당시각까지공급된프레임만추론해야한다. 이번파일의대표3초는2–4초입력에서계산됐으므로4초입력과추론후내부후보가나오고, 후행근거/업로드/VLM/BE검증후알림이가능하다. 표시상대표시각과실제지연을분리한다. 전체파일의꼬리창/배치와순차추론의창/배치가달라동일출력을사전보장하지않는다.

첫알림은VLM설명까지포함하고RAG/완성보고는후속으로붙이는것을제안한다. 알림채널(화면/알림음/외부메시지)은미확정이다. 실제알림전송·추적기추가·재생GPU실행은하지않았다.

`analysis_profile_id`는 객체/사고 가중치·해시, 모델별 입력 크기·샘플링·정규화·클래스 맵, 창 길이·간격, 판정/후처리, 근거 전후 길이, 최대 파일·영상 범위, VLM/RAG 버전을 참조한다. 미등록 프로필은 제출을 거절한다. 모델 이름만으로 R3D의 2초·16장·224 설정을 X3D-S에 이식하지 않는다.

개발 제안값: 한 요청 한 영상, UI 상태 조회 3초 간격, BE 회수 5초 간격·오류 시 backoff, GPU 동시 작업 1. 이 값들은 품질·응답시간 보장이 아니다. 최대 영상 길이·파일 크기·GPU timeout·재시도 상한·금액은 비용 측정 전 미정이며 실제 실행 프로필에 값을 채워야 한다.
