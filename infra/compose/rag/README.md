# Docker Compose로 DB와 임베딩 도구 실행

## 현재 EC2 배포 완료 — 2026-09-27

서울 t3.small/Ubuntu24.04 amd64의 Docker Compose에서 PostgreSQL16.15·pgvector0.8.6을 실행했다. 기존 실제 문서벡터2행을 그대로 적재하고 본문·출처·벡터/차원 및 문서 자기검색을 확인했다. 새 Gemini 호출은0이다. EC2 새질의 검색·BE/VLM/화면 연결은 아직이며, S3 자동 백업은 비활성화 상태다.

현재 서버의 접속·재개는 [SSH 배포 안내](../../terraform/cpu-rag/README.md)를 따른다. 현재 앱 경로는 `/opt/cctv-rag-compose-20260927`, DB는 `/srv/cctv/postgresql`이다. 아래 로컬 시험과 초기 설치 설명은 재현/이력용이며 이미 만든 문서를 다시 임베딩할 필요가 없다.

**먼저 Mac의 로컬 Docker에서 연결을 확인하고, 검증한 구성을 EC2에 배포한다.** 로컬 시험과 EC2 배포는 별개다. Mac에서는 PostgreSQL·pgvector와 CPU용 Python 명령만 실행하며 영상 모델/GPU는 사용하지 않는다.

## 역할과 데이터 위치

| 구성 | 역할 | 데이터 위치 |
|---|---|---|
| `db` 컨테이너 | PostgreSQL + pgvector 저장·검색 | EC2 `/srv/cctv/postgresql` |
| `rag` 일회성 컨테이너 | Gemini 임베딩 요청·DB 적재·검색 | 출력은 `/srv/cctv/rag-output` |
| S3 | 영상·근거·문서 파일과 DB 백업 | 기존 서울 버킷 |
| Runpod | X3D·YOLO·근거 생성 | 기존 구성 유지 |

예를 들어 법령 문장을 Gemini가 768개 숫자로 바꾸면 `rag`가 본문·출처·숫자를 DB에 함께 넣는다. 검색할 때 질문도 같은 모델로 변환하고 pgvector가 가까운 문서를 찾는다. S3에 실행 중인 PostgreSQL 데이터 디렉터리를 연결하는 방식은 아니다.

Compose의 bind mount는 **호스트 폴더와 컨테이너 폴더를 연결**한다. 현재는 EC2 기본 EBS 디스크를 쓰며 별도 DB용 EBS를 추가하지 않는다. 컨테이너 재생성 후에도 폴더는 유지되지만 디스크 손실에 대비한 S3 백업은 필요하다.

## 확인된 결과 — 2026-09-27

Mac ARM64의 Docker 29.7.2에서 PostgreSQL16.15·pgvector0.8.6으로 아래 통합 시험을 통과했다. 최종 실행31.51초, 합성2행의 중복방지/768차원/검색·날짜·권한·재생성 후 유지·dump복원을 확인했다. 초기 쉘 파일 실행 권한 문제는 SQL 초기화 파일로 바꿔 해결했고 실패 기록도 보존했다. 실제 Gemini/EC2/S3 백업 검증은 아직이다.

근거: `artifacts/infra/cpu-rag-compose-20260927-01/local-docker-test-attempt4.json`.

## 1. 로컬 Docker 시험

프로젝트 루트에서 실행한다. 약 수백 MB의 고정 PostgreSQL/Python 이미지와 psycopg 드라이버를 처음 한 번 내려받는다. Docker Desktop이 실행 중이어야 한다.

```bash
cd .
python3 tests/rag/check_compose_integration.py \
  --report /tmp/cctv-compose-local-check.json
```

시험마다 새 이름의 Compose 프로젝트와 임시 폴더를 사용한다. 테스트 종료 시 해당 컨테이너·네트워크·합성 데이터·임시 비밀번호만 정리한다. Docker 이미지 캐시는 남긴다. 다른 프로젝트나 실제 법령 원본은 변경하지 않는다.

검사 범위:

1. PostgreSQL 기동·pgvector 확장·전용 계정·원본2청크 파일 검사.
2. **시험용 숫자 벡터** 2개를 실제 DB에 적재하고 같은 파일을 재적재해 중복이 없는지 확인.
3. 코사인 검색·날짜 필터·잘못된 비밀번호 거부·앱 계정의 테이블 삭제 거부.
4. DB 컨테이너를 다시 만들고 저장된 2행 유지 확인.
5. 실제 `pg_dump`/별도 DB `pg_restore` 후 행 수와 768차원 확인.

Gemini API 호출과 AWS 자원 생성은 0회다. 합성 벡터는 DB 동작 검사에만 사용하며 법령의 실제 임베딩이나 검색 품질 결과로 취급하지 않는다.

## 1-B. 실제 법령을 보존하는 로컬 DB — 현재 사용 중

2026-09-27 사용자 생성 임베딩2개를 **cctv-rag-local** 프로젝트에 실제 적재했다. 이전 일회성 합성시험 프로젝트와 분리했다. DB는 현재 healthy이고 Gemini를 다시 호출하지 않았다.

- 입력: `artifacts/rag/law-153-20260927-01/embedded-001-768-v1.jsonl`과 완료영수증. 원본 SHA를 유지했다.
- 로컬 DB 데이터: `artifacts/rag/local-db-v1/data/postgresql`.
- 비밀번호: 같은 runtime의 `secrets/` 아래 자동 생성, 값은 출력하지 않는다. `artifacts/` 전체는 Git에서 제외된다.
- 결과 기록: 같은 runtime의 `receipts/` 아래 실행별 JSON. 비밀번호/API키는 기록하지 않는다.
- 확인된 결과: 2행/각768차원, 본문·전체메타데이터 일치, 벡터저장 반올림오차 최대1.90e-8 미만, 저장벡터를 질의로 넣었을 때 자기 문서 반환. 후속 새질문1건도 실제 질의임베딩/본문·출처·거리 반환에 성공했다. 단일질문의동작검증이며전체검색품질합격은아니다.

**지금은 up/load를 다시 실행할 필요가 없다.** 아래는 재개·상태확인 명령이다.

```bash
cd .
python3 scripts/rag_local.py status
python3 scripts/rag_local.py verify
```

아래 질문 검색은 후속 사용자 실행으로 이미1회 성공했다. 영수증은 `artifacts/rag/local-db-v1/receipts/20260927T085025037875Z-search.json`이며 제153조1항/2항 거리0.266609/0.339306을반환했다. 다른질문을시험할때아래명령을수정해사용한다. 키를 숨김 입력하며 저장하지 않는다. 질문을 Google에 보내 **질의 임베딩1회**를 만들고 같은 DB에서 본문/출처/코사인거리를 반환한다. 계정요금/한도가 적용된다.

```bash
python3 scripts/rag_local.py search \
  --query '교통사고 발생 후 조치나 신고를 방해한 경우 관련 조문' \
  --date 2026-09-27
```

입력 안내 `Gemini API key (hidden):`가 나오면 키를 붙여 넣고 Enter를 누른다. 키는 화면·파일·명령 인수·컨테이너 환경설정에 남기지 않고 실행 프로세스에만 전달한다. 결과의 cosine_distance는 작을수록 벡터가 가깝다는 뜻이며 법적 적용 확정이나 검색 품질 합격을 의미하지 않는다. 현재 문서는 제153조 두 항뿐이다.

작업 후 DB를 중지하거나 다시 켤 때:

```bash
python3 scripts/rag_local.py stop
python3 scripts/rag_local.py up
```

`stop`은 컨테이너를 멈추고 DB 파일을 유지한다. `up`은 기존 비밀번호와 파일을 재사용한다. 다른 디렉터리/기존 DB에 비밀번호를 임의로 다시 생성하지 않는다. 초기 설치·복구 후 같은 검증 파일을 다시 적재해야 할 때만 `python3 scripts/rag_local.py load`를 쓴다. 이 명령은 기존 문서를 다시 임베딩하지 않는다.

실제 클라우드 배포는 다음 EC2 절의 별도 단계다. 이번 로컬 DB 실행으로 AWS 자원이 만들어지지는 않았다.

## 2. EC2 준비

[Terraform 안내](../../terraform/cpu-rag/README.md)로 계획과 비용을 확인하고 배포한다. EC2 부팅 스크립트는 Docker·DB까지 준비한다. 현재 저장소의 기본값은 20 GiB root disk이며 DB 포트를 외부에 공개하지 않는다.

실행 묶음 `cctv-rag-compose.zip`을 EC2 `/tmp/cctv-rag-compose.zip`에 전달했다고 가정한다. **현재 자동 업로드된 파일은 아니다.** 다음 명령은 EC2 안에서 실행한다. 같은 경로에 기존 배포가 있다면 새 경로를 사용한다.

```bash
sudo mkdir /opt/cctv-rag-compose
sudo python3 -m zipfile -e /tmp/cctv-rag-compose.zip /opt/cctv-rag-compose
cd /opt/cctv-rag-compose
sudo bash rag.sh build
sudo bash rag.sh schema
sudo bash rag.sh check --input /input/chunks.jsonl
```

`rag.sh`는 Terraform이 설치한 `/opt/cctv-stack/compose.yaml`과 묶음의 앱 설정을 합친다. `check`는 `chunks: 2`, `api_calls: 0`, `db_writes: 0`을 반환해야 한다. 스키마는 별도 관리자 명령으로 적용하며 앱 계정은 DDL 권한을 받지 않는다.

실행 묶음 없이 저장소를 배포할 때는 다음 두 환경변수를 설정하고 `apps/rag_index/compose.sh`에 같은 명령을 전달한다.

- `CCTV_COMPOSE_FILE=/opt/cctv-stack/compose.yaml`
- `CCTV_INPUT_DIR=<EC2에서 chunks.jsonl이 들어 있는 폴더>`

## 3. Gemini 실제 임베딩 — API를 사용하는 단계

이 절은 새 문서를 추가할 때의 방법이다. 현재 법령2청크는 실제 임베딩과 EC2 적재가 완료됐으므로 다시 실행하지 않는다.

계정에서 `gemini-embedding-001` 사용 가능 여부와 요금/한도를 확인한다. 현재 준비 프로필은 **001·768차원**이다. 키는 아래 숨김 입력에만 넣는다.

```bash
sudo python3 /opt/cctv-infra/prepare-host.py --gemini
```

그다음 문서2청크에 대해 API를 호출한다. 기존 출력 이름이 있으면 `v2`처럼 새 이름으로 바꾼다.

```bash
cd /opt/cctv-rag-compose
sudo bash rag.sh embed \
  --input /input/chunks.jsonl \
  --output /output/embedded-001-768-v1.jsonl
sudo bash rag.sh load --input /output/embedded-001-768-v1.jsonl
sudo bash rag.sh search \
  --query '교통사고 발생 후 조치나 신고를 방해한 경우 관련 조문' \
  --date 2026-09-27
```

`embed`는 문서 임베딩2회, `load`는 DB 적재만, `search`는 질의 임베딩1회와 DB 검색이다. 부분 실패 파일에는 완료 영수증이 없어 적재되지 않는다. 모델을 변경하면 문서/질의/DB 프로필을 함께 변경해야 한다.

실제 출력은 EC2 `/srv/cctv/rag-output/embedded-001-768-v1.jsonl`과 `.complete.json`에 남는다. 컨테이너 내부의 `/output`이 이 경로다. 출력의 본문·출처·768차원과 DB의 `committed: true`를 확인하고 별도 질문으로 검색 품질을 검토한다.

## 4. 백업 확인과 다음 연결

현재 SSH 배포에는 백업 service/timer가 설치되지 않았다. 아래 명령은 담당자가 역할을 준비하고 백업 프로그램을 연결한 뒤 사용하는 후속 절차다.

EC2에서 첫 적재 후:

```bash
sudo systemctl start cctv-db-backup.service
sudo journalctl -u cctv-db-backup.service --no-pager
```

로컬의 dump/restore 통과와 EC2의 S3 업로드·다운로드 복원 성공은 다르다. 실제 S3 백업 복원은 배포 후 별도로 확인한다.

다음 서비스 연결은 **BE가 S3 근거를 회수 → Gemini 장면 설명 → 검색 질문 임베딩 → pgvector 검색 → 보고 작성**이다. 이번 Compose에는 BE·worker·화면 코드가 아직 포함되지 않았다.

## 파일 흐름·선택 이유·한계

- `compose.yaml` → DB 이미지·내부 네트워크·폴더/비밀번호 연결.
- `prepare-host.py` → EC2 폴더·비밀번호 생성과 보존, Gemini 키 비공개 설정.
- `init-db.sql` → 빈 DB의 최초 계정·pgvector 초기화. PostgreSQL 이미지가 SQL 파일을 읽어 적용한다.
- 앱 `compose.sh` → Dockerfile의 Python 도구 → `container_entrypoint.configure()` → 기존 `index.py`의 `check/embed/load/search`.
- 직접 설치보다 실행 환경을 재현하기 쉽고, 같은 EC2 구성에서 BE를 추가하기 편해 Compose를 선택했다. RDS는 관리형 대안이다.
- 단일 EC2의 장애·용량 관리는 남는다. 로컬 ARM64 검증만으로 EC2 amd64 부팅, IAM, Gemini 가용성이나 실제 검색 품질이 확인되지는 않는다.

30초 공유: “DB와 pgvector를 Docker Compose로 먼저 로컬에서 확인하고 같은 구성을 EC2에 배포합니다. DB는 EC2 기본 디스크에 남기고 S3에 백업합니다. 문서 벡터는 Gemini API가 만들며, Runpod는 계속 영상 분석을 맡습니다.”

[Docker 설치](https://docs.docker.com/engine/install/ubuntu/) · [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) · [pgvector Docker](https://github.com/pgvector/pgvector#docker)
