# 첫 법령 청크 임베딩·PostgreSQL 적재 준비

2026-09-27 사용자 Docker Compose 선택 반영. [Compose 실행 안내](../../infra/compose/rag/README.md)에 로컬 DB 시험과 EC2 배포 순서를 정리했다. 후속 사용자 실행으로 법령2청크의 실제 Gemini 임베딩 파일이 생성됐고, 001/768·해시·본문/출처 보존 검증을 완료했다. 결과는 artifacts/rag/law-153-20260927-01/embedded-001-768-v1.jsonl이며 후속 작업에서 로컬 Compose DB(cctv-rag-local)에 실제2행 적재·값 검증까지 완료했다. 후속 새질문1건도 Gemini질의임베딩1회→문서2항반환까지 완료했다(artifacts/rag/local-db-v1/receipts/20260927T085025037875Z-search.json). 답변/보고 생성과 EC2 배포는 아직이다.

## 역할 구분

- Gemini VLM: 실제 클립/프레임을 보고 장면 설명을 생성한다.
- Gemini 임베딩: 법령 문장 또는 검색 질문을 숫자 벡터로 변환한다. VLM 텍스트 응답을 embedding 열에 넣지 않는다.
- PostgreSQL+pgvector: 본문·출처·법령 버전과 벡터를 같은 행에 저장하고 검색한다.

준비 프로필은 **텍스트 전용 gemini-embedding-001 / 768차원**이다. 장면 설명 모델 선택과 독립적이며 실제 사용 모델 최종 확정/계정 가용성 검증 전이다. 문서에는 RETRIEVAL_DOCUMENT, 검색 질문에는 RETRIEVAL_QUERY를 사용하고 둘 다 같은 모델/차원으로 정규화한다. 모델을 바꾸면 문서와 질의 프로필을 함께 바꿔야 한다. Gemini Embedding 2는 task_type 규칙이 달라 이 코드의 모델명만 바꿔 사용할 수 없다.

## 입력

저장소 루트의 `artifacts/rag/law-153-20260927-01/chunks.jsonl` 두 행을 그대로 사용한다. source_url/법령번호/시행일/항/본문 SHA를 보존한다. 원본은 덮어쓰지 않는다.

```bash
python3 apps/rag_index/index.py check \
  --input artifacts/rag/law-153-20260927-01/chunks.jsonl
```

이 검사는 표준 라이브러리만 사용하고 모델/API/DB에 연결하지 않는다.

## 임베딩 생성 명령 — 기존2청크는 완료

기존2청크는 embedded-001-768-v1.jsonl로 생성 완료했으므로 다시 실행하지 않는다. 아래는 새 문서를 준비할 때 사용하는 일반 명령이다. 실행 환경에 GEMINI_API_KEY를 비공개로 설정한 뒤 사용한다. 아래 명령부터 법령 텍스트가 Google API로 전송되며 계정 요금/한도가 적용된다. 호출은 청크당1회, 현재총2회다. Mac에서 로컬 모델을 실행하지 않는다.

```bash
python3 apps/rag_index/index.py embed \
  --input artifacts/rag/law-153-20260927-01/chunks.jsonl \
  --output artifacts/rag/law-153-20260927-01/embedded-001-768.jsonl
```

전체 완료 시 `.complete.json` 영수증을 생성한다. 중간 실패 출력은 적재하지 않으며 자동 재호출하지 않는다. 현재는 부분 재개 기능 없이 새 경로로 다시 실행한다. 부분결과/원본을 보존하고 중복 API 비용을 고려한다.

## DB 준비 후 적재

DB 서버에 pgvector 확장이 설치되어 있어야 한다. 승인된 대상 DB에서 관리자가 `schema.sql`을 실행한다. 이 파일은 기존 테이블을 삭제하지 않으며 신규 cctv_rag.chunks에 vector(768)을 정의한다. CREATE EXTENSION 권한은 일반 적재 계정과 다를 수 있다. 운영 EC2의 DB는 아직 배포하지 않았다. Compose에서는 관리자 schema 명령이 이 파일과 grants.sql을 함께 적용한다.

Compose 도구 이미지가 고정된 psycopg[binary]==3.3.6을 설치한다. 아래는 같은 index.py를 직접 실행할 때의 명령이다. RAG_DATABASE_URL을 비공개 환경변수로 설정하고:

```bash
python3 apps/rag_index/index.py load \
  --input artifacts/rag/law-153-20260927-01/embedded-001-768.jsonl
```

완료영수증/파일SHA/차원/유효숫자 검증 후 트랜잭션으로 적재한다. 같은 chunk/profile/input hash는 중복 삽입하지 않고 기존 원문을 검증한다. DB에서 예외가 나면 트랜잭션을 롤백한다. 원본 JSON의 database_loaded=false는 원본 상태이며 DB 완료는 load 명령의 committed 출력으로 구분한다.

## 질의 검색

```bash
python3 apps/rag_index/index.py search \
  --query '교통사고 발생 후 조치나 신고를 방해한 경우 관련 조문' \
  --date 2026-09-27
```

질의임베딩1회 API 호출 후 정확 코사인 검색으로 본문/출처/거리를 반환한다. 두 청크뿐이라 HNSW 등 근사 인덱스는 아직 만들지 않는다. 모델 출력으로 법적 적용을 자동확정하지 않는다. 검색상위5개는 관련성 합격을 뜻하지 않으며 실제 검색 품질 검증이 필요하다.

현재 유일한 법령 버전은 시행2026-07-01이다. effective_to=null은 종료일 미수집 상태이므로 무기한 유효하다는 법적 확인이 아니다. 날짜필터는 기초 필터일 뿐이며 실제 사건 적용 전 법령 개정이력과 참조 조문을 확인해야 한다. 내용 수정으로 같은청크ID의 새input hash를 넣으면 두버전이 남으므로 운영코퍼스로 확대할 때 활성버전 관리가 필요하다.

## 검증 범위

표준라이브러리5시험(기존2행/hash, 벡터차원·NaN·0검사, 대역API/원본보존, 실패시완료영수증없음) 통과. 후속 Compose DB 통합 시험은 Mac ARM64에서 PG16.15/pgvector0.8.6·합성2행으로 통과했다. 드라이버 연결·중복방지·코사인검색·날짜/권한검사·컨테이너 재생성 후 유지·dump/restore를 확인했다. 근거는 artifacts/infra/cpu-rag-compose-20260927-01/local-docker-test-attempt4.json이다. 후속 실제 법령 임베딩2개는 별도 파일/영수증 검증을 완료했다. 로컬 실제 DB2행의 본문/메타데이터/768차원·벡터 반올림오차/저장벡터 자기검색도 확인했다. 새질문1건에서제153조1항/2항이본문·출처·거리와함께반환되는동작을확인했다. 여러질문의검색품질·법적적용검수와운영EC2적재는아직이다. 시험 데이터는 법령 원본과 분리하고 테스트 종료 후 정리한다.

이 코드는 애플리케이션 RAG 처리이며 cctv-infra에 게시하지 않았다. 코드·자료 GitHub 게시도 이번 범위에 포함하지 않는다.

공식참고: [Gemini 임베딩](https://ai.google.dev/gemini-api/docs/embeddings), [pgvector](https://github.com/pgvector/pgvector), [법령 원문](https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1028651417).
