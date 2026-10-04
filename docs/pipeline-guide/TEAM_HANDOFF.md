# 팀 인계와 시연 완료 기준

## 1. 저장소를 나누는 기준

| 위치 | 담을 것 |
|---|---|
| 이 문서 저장소 — 제안 이름 `cctv-pipeline-guide` | README, 아키텍처·시퀀스, 운영 절차, 팀 인계 |
| 기존 실행 코드 저장소 / 검증된 배포 묶음 | BE·Runpod·VLM·RAG 코드, 의존성, 스키마, 시험 |
| [cctv-infra](https://github.com/boramsam-zo/cctv-infra) | Terraform과 서버 운영 구성 |
| 접근이 제한된 운영 저장소 | 실제 환경 설정, 키, DB·작업 백업, 원본 실행 근거 |

기존 인프라 저장소의 원격 반영 여부는 별도로 확인한다. 이 문서 발행이 인프라 코드 push나 AWS 적용을 뜻하지 않는다.
배포 담당자는 실행 코드의 버전/묶음 SHA256, Runpod 모델 가중치 해시, DB 스키마 버전을 함께 관리한다.

## 2. 누가 무엇을 주고받나?

| 담당 | 받는 것 | 주는 것 |
|---|---|---|
| BE | 원본 MP4, Runpod 주소/분석 토큰, S3 설정, Gemini 비밀키 | 작업ID·진행 상태, 검증한 장면 결과, DB 저장·후속 호출 |
| Runpod 모델/근거 | MP4 바이트와 인증된 분석 요청 | 후보 시각, YOLO 객체, 실제 클립·프레임, manifest/S3 참조 |
| 문서/RAG | 원문 문서, 검증된 장면 결과, 사건일 메타데이터 | 청크 JSONL, 검색 문장 함수, 근거를 인용한 보고 함수 |
| 인프라 | 배포 대상·권한 범위·저장/보관 조건 | EC2/S3/IAM/디스크, 실행 경로와 운영 절차 |
| 화면 | BE 상태와 검증된 설명·근거 참조 | 진행 상태·사고 의심·설명·검토 결과 표시 |

키는 비공개 설정으로 전달한다. Runpod 계정 API key, 분석 서버의 `.api-token`, Gemini 키는 서로 다르다.
Gemini 호출은 BE에서 한다. 새 vLLM 서버를 추가하는 구성은 현재 범위에 없다.

## 3. 이어 붙일 때 유지할 식별자와 파일

| 이름 | 의미 |
|---|---|
| `worker_job_id` | BE의 접수 작업 식별자 |
| `provider_job_id` | Runpod가 발급한 분석 작업 식별자 |
| `event_id` | 그 영상 안의 후보 사건 식별자. `event_000`만으로 전역 사건을 식별하지 않음 |
| `analysis_id` | Gemini 장면 설명 시도 식별자 |
| `input_sha256` | 원본 영상이 같은지 확인할 해시 |

Runpod 완료 결과는 `cctv-evidence/<provider_job_id>/manifest.json`을 기준으로 회수한다.
manifest가 가리키는 `result.json`, `vlm_handoffs.json`, `evidence.json` 및 클립·프레임의 크기/해시를 대조한다.
BE는 실제 미디어를 다운로드해서 Gemini에 전달한다. S3 경로 문자열만 넘겨 외부 모델이 읽는 것으로 가정하지 않는다.

장면 설명은 `cctv-scene-v2`의 `result.json`을 사용한다.

- `description.accident_suspicion`: `suspected`, `not_observed`, `uncertain`.
- `description.incident_types`: `collision`, `rollover`, `off_road`, `other`, `unknown` 중 배열.
- `description.summary`, 관찰, 불확실성, 근거 참조 및 작업/사건/분석 식별자.
- 예: 단독 전도는 `suspected` + `["rollover"]`. 다른 차량과 충돌하지 않았다는 이유로 사고 없음으로 바꾸지 않는다.

BE의 `rag-handoff.json`은 설명 결과 위치/해시와 저장 여부를 연결한다.
생성 시점의 `rag_executed=false`, `report_generated=false`는 후속 검색/보고가 아직 실행되지 않았다는 뜻이다.
현재 실제 사건의 v2 성공 결과는503으로 아직 없다. 이전 v1 예시는 형식 참고로만 제공하며 이번 사건 결과로 대체하지 않는다.

## 4. 동료에게 요청할 자료

### 문서 청크

**UTF-8 JSONL, 한 줄에 청크 하나**로 요청한다.

- `document_id`, `chunk_id`, `content` — 원문 본문 유지.
- 문서 제목, 조항/페이지, `source_url`, `effective_from`, 버전 정보.
- 원문 파일 또는 확인 가능한 원문 출처.
- 현재 로더에 바로 넣으려면 `embedding_input`, `content_sha256`도 필요하다. `content_sha256`은 UTF-8 본문의 SHA256이다. 담당자가 원문 확인 후 준비해도 된다.

pgvector는 PostgreSQL의 확장이며 전달할 청크 파일 이름이 아니다.
기존 `gemini-embedding-001`·768차원·`gemini-001-768-retrieval-v1` 프로필과 입력 규칙을 유지한다.
동료가 임베딩까지 한다면 embedded JSONL과 `.complete.json`, 사용 모델/차원/버전도 받는다. 차원이 같아도 다른 모델의 벡터를 섞지 않는다.
기존 벡터는 재사용하고 새 문서/질문에 필요한 임베딩을 별도로 수행한다.

### 검색 문장과 보고 함수 — 연결할 제안 계약

1. `build_search_query(scene_result, incident_date) -> query_result`
   - 검증된 v2 장면 설명 → 검색 문장1개, 적용 날짜, 버전/상태, 연결 식별자.
   - 사건 날짜를 모르면 미확인으로 남긴다. 업로드일을 사건일로 사용하지 않는다.
   - BE가 기존 `apps/rag_index/index.py`의 `search(text, date)`를 호출한다.
2. `generate_report(scene_result, retrieval_result) -> report_result`
   - 장면 설명과 검색한 **본문·출처** → 한국어 보고 초안, 인용 청크ID, 한계, 상태/버전.
   - 관련 근거 없음, 검색 장애, 생성 실패를 구분한다.
   - 문서에 포함된 지시문을 실행하지 않고 자료로만 취급한다.

두 함수의 코드/의존성, 작은 실행 CLI, 입출력 JSON 예시, 프롬프트·모델·버전, 시험 결과를 받는다.
이 실행 산출물은 코드 담당자에게 별도로 전달하고 문서 전용 저장소에는 넣지 않는다.

현재 검색 함수는 `chunk_id`, `content`, `source_url`, `cosine_distance`를 반환한다.
보고에 필요한 제목/조항/시행 버전은 기존 청크 metadata에서 검증해 보완할 담당을 정한다. 생성 모델이 추측해 채우게 하지 않는다.
거리값은 관련 문서를 고르는 참고값이며 사실/법적 판단의 확률이 아니다.

## 5. 시연 완료로 표시할 조건

현재는 녹화 MP4 전체 분석이다. 현재 분석 API 제한은 최대120초·기본200MiB이며, 실제 CCTV 스트림/실시간 버퍼 입력은 후속이다.
시연 화면에서 영상을 재생하는 것과 모델이 실시간 입력을 처리하는 것을 구분한다.

- [x] 한 영상이 BE에서 Runpod에 전달되고 같은 작업ID로 조회되는 것 확인.
- [x] GPU 분석과 근거 생성, S3 보관·회수 확인. 이번 성공에는 수동 S3 복구가 포함됨.
- [ ] 현재 사건에서 Gemini v2 장면 설명·DB 저장 확인.
- [ ] 새 영상1건이 수동 복구 없이 접수→분석→S3→설명→DB까지 이어지는 것 확인.
- [ ] 장면 설명→검색→보고 연결 및 근거가 없는 경우 확인.
- [ ] 화면/알림 표시와 EC2 상시 실행·재시작 검증.

현재는 먼저503 제공 상태를 확인하고 기존 근거로 VLM/DB를 이어간다. GPU 재분석은 필요하지 않다.
알림은 설명/근거를 검증한 뒤 BE의 합의된 조건으로 ‘사고 의심’을 표시하는 방향이다. 실패를 사고 없음으로 표시하지 않는다.
백업·LangSmith는 별도 운영/관찰 과제다. 백업 활성화를 VLM 호출의 선행 조건으로 두지 않는다.

## 6. 문서 저장소 게시 — 담당자가 실행

`README.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, `TEAM_HANDOFF.md`, `.gitignore`만 있는 **문서용 폴더**에서 실행한다.
팀 GitHub에 빈 저장소를 먼저 만든 뒤 실제 URL을 `DOCS_REPO_URL`에 설정한다.
아래는 처음 게시하는 폴더용 명령이다. 기존 저장소에서는 새로 init/remote 설정하지 않고 변경 파일을 확인한다.

```bash
git init -b main
git add README.md ARCHITECTURE.md RUNBOOK.md TEAM_HANDOFF.md .gitignore
git diff --cached --stat
git diff --cached
```

문서만 포함된 것을 검토한 다음:

```bash
git commit -m "docs: document CCTV pipeline and operating steps"
git remote add origin "$DOCS_REPO_URL"
git push -u origin main
```

개인 작업 프로젝트의 전체 폴더를 `git add .`로 올리지 않는다. 영상·모델·키·spool·DB dump·Terraform state는 이 문서 패키지에 포함하지 않는다.

## 동료에게 30초 설명

“BE가 PostgreSQL 대기열에서 영상을 Runpod로 보내고 완료 상태를 조회합니다. Runpod는 X3D·YOLO로 후보를 찾고 클립·프레임을 만들어 S3에 저장합니다. BE가 근거를 검증한 뒤 Gemini 설명을 DB에 저장하는 코드까지 연결돼 있습니다. 현재는 Gemini503 때문에 마지막 저장을 확인하는 중이고, 문서 검색·보고 자동 연결과 EC2 상시 운영은 다음 단계입니다.”
