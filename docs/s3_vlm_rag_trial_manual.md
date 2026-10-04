# S3 근거를 재사용하는 VLM RAG 보고 시험 매뉴얼

작성 기준: 2026년 10월 2일. 대상은 기존 S3 사건 근거를 이용해 VLM 설명부터 검색·보고까지 별도로 시험하는 동료입니다. 원본 근거를 한 번 받아 같은 입력으로 결과를 비교합니다. Runpod의 X3D·YOLO를 다시 실행할 필요는 없습니다.

이번 자료는 `message.txt`, `확인4.txt`, `accident_control_rag_server.zip`의 코드·스키마·데이터를 기준으로 확인했습니다. README의 내용은 참고하거나 수정하지 않았습니다.

## 1. 먼저 알아둘 현재 상태

**공유본 03에는 S3 다운로드·검증과 규격 2.0 VLM 실행 코드가 포함돼 있습니다.** 동료의 `accident_control_rag_server.zip`과 함께 사용하면 아래 순서로 시험할 수 있습니다. 기존 운영 VLM의 `cctv-scene-v2` 출력과 구분해 새 관찰 JSON을 만듭니다. 이전 공유본 01·02에는 이 호출 코드가 없습니다.

전체 시험 순서는 다음과 같습니다.

> S3 사건 근거 → 다운로드·해시 검증 → VLM으로 실제 장면 관찰 → 규격 2.0 JSON 검증 → 동봉 SQLite에서 검색 → 보고 JSON·Markdown → 사람 검토

이 매뉴얼은 실행 준비와 명령을 안내합니다. 새 호출부는 저장된 미디어와 가짜 API 응답을 사용한 로컬 8개 시험을 통과했습니다. **후속 사용자 실행에서 기존 B 사건의 S3 검증 → Gemini 1회 → CPU RAG 검색 → 보고 파일 저장까지 완료했습니다.** 실행 결과는 `trial-RF2Gbq/report-QKtVb4`에 보존했습니다. 6절 형식·입력 일치·문서 참조 3개를 확인했으며, 영상 판독의 정확성과 아래 10절의 보고 내용 보완점은 검토가 남아 있습니다. API 서버 방식은 아직 시험하지 않았습니다.

### 이전 자료에서 바뀐 내용

| 항목 | 이번에 확인한 내용 |
|---|---|
| 문서 자료 | 이전 CSV 509행에서 이번 JSONL 609행으로 변경 |
| 실제 검색 대상 | 통계 334행을 제외한 275개 청크, 320개 구간 벡터 |
| 검색 모델 | `intfloat/multilingual-e5-small`, 384차원, 정규화, CPU |
| 모델 revision | `614241f622f53c4eeff9890bdc4f31cfecc418b3` |
| 저장소 | 동봉 `data/index.sqlite` |
| 검색 | BM25와 임베딩 검색을 가중 RRF로 결합 |
| 보고 | 규칙·템플릿과 검색 근거에 기반한 문장 작성 |
| 생성형 LLM 보고 | 연결 없음. `generation.llm_connected: false` |
| 추가 제공물 | 입력 스키마, VLM 프롬프트, API/CLI, 예시 JSON·보고, 모델 파일 |

기존 운영 검색은 Gemini 임베딩 768차원·PostgreSQL을 사용합니다. 이번 시험은 동봉 E5 모델·384차원 SQLite를 한 쌍으로 사용합니다. 벡터 차원과 모델이 다르므로 기존 운영 벡터와 섞지 않습니다. 동봉 인덱스로 첫 시험을 할 때 문서 임베딩을 다시 만들 필요는 없습니다.

`VERIFY_RESULT.json`에는 제공자가 Windows/Python 3.9.13에서 기록한 API 시험 결과가 있습니다. Docker 빌드는 미시험으로 표시돼 있습니다. 이 기록을 이번 환경에서 재현한 결과로 취급하지 않습니다. 예시들은 `VLM_PROMPT.md`에서 가상 통합시험 입력으로 설명되므로, `확인4.txt`나 `run-*.json`을 실제 S3 영상의 VLM 판독 증거로 사용하지 않습니다.

## 2. 담당자와 준비물

| 담당 | 준비할 항목 |
|---|---|
| S3 근거 담당자 | 사건별 근거 묶음, 출처·해시·원본/클립 시각 정보, 다운로드 검증 기록 |
| VLM 담당자 | 영상 입력을 받는 실행기, 모델명, 비공개 Gemini 키, 프롬프트·출력 규격, 호출 기록 |
| RAG 시험 담당자 | 새 ZIP 전체, 별도 CPU 실행 환경, 검증된 `event-v2.json` |
| 검토 담당자 | 실제 영상과 VLM 사실 일치, 검색 근거와 보고 문장 연결 확인 |

한 사람이 모두 진행해도 됩니다. 운영 EC2의 큐·DB·spool과 분리된 시험 폴더/환경을 사용합니다. 결과는 파일로 확인하며 현재 Streamlit 작업 목록에 자동 등록되지는 않습니다.

첫 시험은 **기존 B 사건 1개, VLM 생성 1회, 검증된 JSON으로 RAG 보고 1회**입니다. VLM 명령은 기본적으로 사전 검사만 하며 `--run`을 붙여 실행하면 유료 요청 1회를 보냅니다. 자동 재요청은 하지 않습니다. 기존 P6의 호출 예약·제한 행과 별도 파일 시험입니다.

필요한 비밀 설정은 서로 다릅니다.

- S3 다운로드: 해당 사건 객체의 `s3:GetObject` 권한. 기존 AWS 프로필 또는 별도 실행 환경의 자격증명 사용.
- VLM: Gemini API 키. 키 파일 또는 비공개 환경변수로 주입.
- RAG CLI: API 키 불필요. 동봉 모델로 CPU 임베딩을 계산.
- RAG API: 자체 인증용 `RAG_API_KEY` 필요. Gemini 키와 별개.

키·비밀번호·만료 전 서명 URL은 공유 ZIP이나 결과 JSON에 넣지 않습니다.

## 3. 실행 자료 준비

준비할 파일은 두 개입니다.

1. `s3-vlm-rag-trial-share-03.zip`: 이 매뉴얼, S3/VLM 실행 코드, 완료 B의 `source-b.json`.
2. 동료의 `accident_control_rag_server.zip`: RAG 코드·스키마·프롬프트·문서 인덱스·E5 모델.

둘 다 새 시험 폴더에 풉니다. 공유본 03에는 모델·영상·키가 들어 있지 않습니다.

`accident_control_rag_server.zip`을 **새 시험 폴더**에 풀고 Python 3.11 CPU 환경을 준비합니다. 압축 파일은 303,591,541바이트이며 전체 압축 해제 크기는 약 499MB입니다. 패키지 설치에는 추가 공간과 다운로드가 필요합니다. 운영체제에 맞는 아래 한 가지 절차를 사용합니다. 명령은 사용자가 실행합니다.

### Mac + T9에서 진행하는 경우

사용자 `T9/한화미니프로젝트/test_file` 아래의 두 해제 폴더를 확인했습니다. Mac은 arm64이고 Python 3.11.16이 있습니다. T9는 exFAT이므로 코드·동봉 모델·문서 인덱스는 T9에 두고, 심볼릭 링크·권한 처리를 사용하는 **가상환경은 Mac 내부 프로젝트의 `.venv-rag-trial`에 만듭니다.** 기존 프로젝트의 `.venv`와 별개입니다.

```bash
cd "/path/to/data/한화미니프로젝트/test_file/accident_control_rag_server"
python3.11 -m venv "$HOME/edu_project/boram3-cctv/.venv-rag-trial"
source "$HOME/edu_project/boram3-cctv/.venv-rag-trial/bin/activate"
python -m pip install -r requirements.txt
python -m pip check

export HF_HUB_OFFLINE=1
export RAG_MODEL_PATH="$PWD/model"
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=2
```

`requirements.txt`에 `torch==2.8.0`이 포함돼 있습니다. Mac은 기본 패키지 저장소에서 설치하므로 Linux용 `--index-url https://download.pytorch.org/whl/cpu` 줄을 추가하지 않습니다. [PyTorch 2.8 공식 설치 안내](https://pytorch.org/get-started/previous-versions/#v280)를 확인했습니다. RAG 코드는 `device="cpu"`를 명시합니다.

설치 후 **4절 변수 설정에서는 다음 경로를 사용합니다.** 4절 예시의 `$RAG_DIR/.venv/bin/python`으로 다시 바꾸지 않습니다.

```bash
CCTV_CODE_DIR="/path/to/data/한화미니프로젝트/test_file/s3-vlm-rag-trial-share-03"
RAG_DIR="/path/to/data/한화미니프로젝트/test_file/accident_control_rag_server"
CCTV_PYTHON="$HOME/edu_project/boram3-cctv/.venv-rag-trial/bin/python"
```

설치·첫 실행은 아직 확인 전입니다. T9를 연결한 상태로 사용하고, 결과와 실패 기록을 다음 시험에서 덮어쓰지 않습니다. 이미 해제한 공유본03의 코드는 그대로 사용하며 이 문서의 Mac 안내를 적용합니다.

### Linux CPU 환경에서 진행하는 경우

```bash
cd /path/to/accident_control_rag_server
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt

export HF_HUB_OFFLINE=1
export RAG_MODEL_PATH="$PWD/model"
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=2
```

Windows에서는 해당 운영체제의 설치·가상환경 명령을 적용해야 합니다. 이번 전달본의 Docker도 선택지지만 첫 파일 시험에는 CLI가 단순합니다.

- `RAG_MODEL_PATH`와 `HF_HUB_OFFLINE`은 반드시 설정합니다. CLI는 이를 생략하면 외부 모델을 받을 수 있습니다.
- 제공된 `data/index.sqlite`를 재사용합니다. `control_rag.py index`는 목적지 DB를 교체하므로 첫 시험에서 실행하지 않습니다.
- `control_rag.py schema`도 스키마 파일을 덮어쓰므로 실행하지 않습니다.
- README는 이 매뉴얼의 근거에서 제외했습니다. 다만 ZIP 내부 `model/README.md`는 API 기동 시 manifest 해시 검사 대상이므로 원본 묶음에서 삭제하지 않습니다.

다운로드한 ZIP의 SHA256이 아래와 같은지 먼저 확인합니다.

```text
6eff11ed330b2cf84bf03c088bdc6b8202f26c5d3776b3757bd8f93e5773a297
```

## 4. S3에서 기존 B 근거 받기

이 단계는 공유본 03의 코드로 수행할 수 있습니다. 동료에게 검증된 로컬 근거 묶음을 전달하면 동료는 S3 자격증명 없이 다음 단계를 진행할 수 있습니다.

함께 제공한 `source-b.json`은 완료된 B의 사건 하나를 지정합니다.

- provider job: `6efe62e3bdce4fbdb8270980332db872`
- event: `event_000`
- 원본 입력 SHA256: `6c8233026b1393dd1a7287fb73127fa8799b11337985b444b31f72091295e459`
- 설정에는 고정 B 프로필과 GPU 결과 식별값이 포함돼 있습니다.

**실행 위치: 공유본 03을 푼 폴더.** 아래 `/path/to/...` 두 경로를 실제 위치로 바꿉니다. 3절에서 패키지를 설치한 Python을 함께 사용하며, 이후 7절까지 같은 터미널에서 실행합니다. AWS CLI 프로필 `cctv`는 실제 보유한 프로필명으로 바꿉니다.

```bash
CCTV_CODE_DIR="/path/to/s3-vlm-rag-trial-share-03"
RAG_DIR="/path/to/accident_control_rag_server"
# Linux 예시. Mac + T9에서는 3절의 Mac 내부 가상환경 경로를 사용합니다.
CCTV_PYTHON="$RAG_DIR/.venv/bin/python"
CCTV_SOURCE_CONFIG="$CCTV_CODE_DIR/source-b.json"
cd "$CCTV_CODE_DIR"
umask 077
CCTV_TRIAL_DIR="$(mktemp -d "$PWD/trial-XXXXXX")"

"$CCTV_PYTHON" -B -m apps.scene_description.scene fetch \
  --config "$CCTV_SOURCE_CONFIG" \
  --aws-profile cctv \
  --output "$CCTV_TRIAL_DIR/input"

"$CCTV_PYTHON" -B -m apps.scene_description.scene check \
  --input "$CCTV_TRIAL_DIR/input"
```

기존 CCTV 저장소를 사용할 수도 있습니다. 그때는 `CCTV_CODE_DIR`을 저장소 루트로, `CCTV_SOURCE_CONFIG`를 `$CCTV_CODE_DIR/artifacts/rag/s3-vlm-rag-manual-20261002-01/source-b.json`으로 지정합니다.

`--aws-profile` 방식은 AWS CLI가 설치돼 있어야 합니다. 이를 생략하면 boto3 자격증명 체인을 사용하므로 선택한 Python 환경에 boto3를 설치하고 자격증명을 제공해야 합니다. 동료 requirements에는 boto3가 없습니다. 명령 기본값인 `sample.json`은 다른 과거 사건을 가리킬 수 있으므로 사용하지 않습니다.

이 명령은 manifest와 근거 JSON, 전체 클립, 주석 프레임, ROI 클립, context를 읽습니다. B 기준 S3 논리 GET은 6개이며 전송 오류 시 자격증명 제공 방식에 따라 SDK 재시도가 있을 수 있습니다. 모델 호출·S3 쓰기는 없습니다.

검증 후 로컬 폴더 구조는 다음과 같습니다.

```text
input/
  source.json
  manifest.json
  evidence.json
  fetch.complete.json
  evidence/event_000/
    clip.mp4
    frame.jpg
    roi_clip.mp4
    context.json
```

`check`의 `verified: true`와 사건 ID를 확인한 후 **input 폴더 전체**를 전달합니다. 클립 하나만 복사하면 기존 검증기의 출처·해시 연결이 끊깁니다. 실패한 다운로드 폴더는 완료 근거로 쓰지 않습니다.

현재 검증은 바이트 길이·SHA256·파일 signature와 사건 계약 검사입니다. 전체 영상 디코딩·장면 품질 검수는 별도로 실제 재생해 확인합니다. 미디어 합계 12MiB, inline 요청 18MiB 등 기존 제한도 적용됩니다.

## 5. VLM에서 새 규격 JSON 생성

`apps.scene_description.rag_trial`이 검증된 미디어와 동료의 `VLM_PROMPT.md`·`event.schema.json`을 사용합니다. 계약 파일의 SHA256도 확인하므로 파일을 바꾸면 사전 검사에서 멈춥니다.

### 먼저 호출 없는 검사

```bash
cd "$CCTV_CODE_DIR"
"$CCTV_PYTHON" -B -m apps.scene_description.rag_trial \
  --input "$CCTV_TRIAL_DIR/input" \
  --rag-dir "$RAG_DIR" \
  --output "$CCTV_TRIAL_DIR/vlm" \
  --model gemini-3.5-flash-lite --fps 4
```

기대 출력은 `vlm_v2_preflight_passed`, `generation_calls: 0`입니다. 이 검사는 키·Gemini 연결을 확인하지 않고 근거·규격·요청 구성을 검사합니다. 출력 폴더도 만들지 않습니다.

### 검사 통과 후 생성 1회

```bash
"$CCTV_PYTHON" -B -m apps.scene_description.rag_trial \
  --input "$CCTV_TRIAL_DIR/input" \
  --rag-dir "$RAG_DIR" \
  --output "$CCTV_TRIAL_DIR/vlm" \
  --model gemini-3.5-flash-lite --fps 4 --run
```

키는 `GEMINI_API_KEY`가 설정돼 있으면 사용하고, 없으면 화면에 표시하지 않는 입력창에서 받습니다. 기존 키 파일을 쓰려면 `--key-file /private/path/gemini-key`를 추가합니다. 실제 키를 명령 본문에 쓰지 않습니다.

기대 출력은 `event_v2_validated`, `generation_calls: 1`, `rag_executed: false`입니다. 이때 생성된 `$CCTV_TRIAL_DIR/vlm/event-v2.json`이 7절의 실제 입력입니다. 성공 후 같은 생성 명령을 반복하지 않습니다.

호출부의 동작은 다음과 같습니다.

1. 검증된 전체 클립·주석 프레임·ROI 클립을 입력합니다. 전체 클립을 우선 관찰하고 나머지 자료를 같은 사건의 보조 근거로 설명합니다.
2. 박스 겹침·객체 라벨을 실제 충돌이나 확정 차량 수로 복사하지 않도록 지시합니다. 현재 탐지 클래스 매핑의 미검증 상태도 명시합니다.
3. 기존 운영 프롬프트를 새 규격 2.0 전체 계약으로 교체합니다. 후보 시각과 등록 위치 정보는 미제공이므로 null, 도로 구분은 unknown으로 제한합니다.
4. 비밀을 제외한 요청 식별/설정/해시를 `request.json`에 먼저 기록하고 Gemini 요청을 한 번 보냅니다. 요청의 이미지·영상 base64 본문은 중복 저장하지 않습니다.
5. `response.raw.json`에 응답 원문을 보존한 뒤 JSON Schema, 근거 ID/참조, 사건 ID와 미제공 값의 처리를 검증합니다.
6. 통과하면 `event-v2.json`, 모델·usage·해시·출처를 담은 `result.json`을 저장합니다. RAG 실행과 운영 DB 적재는 별도입니다.

실패하면 `failure.json`과 존재하는 경우 `response.raw.json`을 확인합니다. **같은 출력 폴더가 있으면 재실행은 호출 전에 거절됩니다.** 타임아웃은 서버에서 요청을 처리했을 수 있으므로 새 폴더를 만들어 바로 재시도하지 않고 기존 기록부터 확인합니다. 잘못된 응답을 가상 예시나 기존 운영 JSON으로 대체하지 않습니다.

`event_id`는 원본의 `provider_job_id + "_" + event_id`로 고정합니다. 이번 B는 `6efe62e3bdce4fbdb8270980332db872_event_000`입니다. 실행마다 별도 `trial_id`와 새 출력 폴더로 구분하므로 같은 사건을 비교할 수 있습니다. S3 키·asset 해시·원본 구간·탐지 후보 시각은 `request.json`/`result.json`에 보관합니다. RAG 입력에는 스키마가 허용하지 않은 추가 필드를 넣지 않습니다.

## 6. 필수 항목과 알 수 없는 값

모든 최상위 항목이 필수입니다.

```text
schema_version, event_id, candidate_time_s, description,
location, scene, participants, accident, hazards,
people, evidence, uncertainties
```

| 상황 | 기록 방법 |
|---|---|
| 카메라 ID·등록 위치 없음 | `null` |
| 후보 시각·사고 차량 수·충돌 횟수 모름 | 해당 숫자 필드에 `null` |
| 날씨·도로 종류·주야간 판단 불가 | 허용 enum의 `"unknown"` |
| 화재·연기 등 판단 불가 | `{"state":"unknown","evidence_ids":[]}` |
| 충분히 보이는 구간에서 해당 현상 미관찰 | `not_observed`; 실제 위험의 부재를 보장하지 않음 |
| 현상을 관찰함 | `observed`와 실제 `evidence[].id` 하나 이상 |
| 사고 형태 판단 불가 | `accident.forms: ["unknown"]` |
| 객체·수량을 기록할 근거 부족 | `participants: []`, `uncertainties`에 이유 |
| 승합차 | 허용 타입 `Car`, 외형은 description에 설명. `Van`은 거절됨 |

객체 타입의 미확인은 대문자 `Unknown`입니다. participant의 count는 1 이상 정수이며 null은 허용되지 않습니다. 모르는 수량을 임의로 1로 채우지 않습니다.

`candidate_time_s`의 시각 기준은 제공 스키마에 정의돼 있지 않습니다. 원본 영상/클립 상대 시각과 변환을 확정하기 전에는 null을 사용합니다. 원래 탐지 후보 시각은 별도 `request.json`/`result.json`에 보존합니다.

S3 key, `asset_id`, `start_s`, `end_s`, `operator_confirmed` 등 추가 필드는 허용되지 않습니다. 근거 ID는 중복할 수 없고 모든 참조가 실제 evidence 배열에 있어야 합니다.

**실행 위치: 새 RAG 폴더, 설치한 가상환경 활성화 상태.** 다음 검사는 모델을 로딩하지 않습니다.

```bash
python - /path/to/event-v2.json <<'PY'
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
from control_rag import validate
value = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
schema = json.loads(Path('event.schema.json').read_text(encoding='utf-8'))
Draft202012Validator(schema).validate(value)
validate(value)
print('event_v2_validated; model_calls=0')
PY
```

정적 JSON Schema 검사 외에 `validate()`가 observed의 근거 존재·참조·ID 중복을 검사합니다. 입력 형식 통과가 영상 관측의 정확성을 뜻하지는 않습니다. 이 RAG는 사고 후보를 입력받는다는 전제로 기본 기관 안내를 만듭니다. 위험 신호가 모두 unknown이어도 보고가 생성될 수 있으므로, 보고 생성 성공만으로 사고 발생을 확인했다고 판단하지 않습니다.

## 7. RAG와 보고를 파일로 실행

5절에서 실제 생성한 JSON을 입력으로 사용합니다. 아래 실행은 서버/API 키 없이 동봉 모델로 CPU 검색을 수행합니다.

```bash
cd "$RAG_DIR"
export HF_HUB_OFFLINE=1
export RAG_MODEL_PATH="$RAG_DIR/model"
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=2
EVENT="$CCTV_TRIAL_DIR/vlm/event-v2.json"
OUT="$(mktemp -d "$CCTV_TRIAL_DIR/report-XXXXXX")"

"$CCTV_PYTHON" control_rag.py report "$EVENT" \
  --db data/index.sqlite \
  --contacts contacts.json \
  --output "$OUT"
```

제공된 `examples/demo_mountain_accident_fire_001.json`은 선택적인 환경 점검용 가상 입력입니다. 실제 영상 판독 결과와 혼동하지 않도록 이번 첫 사건 시험에서는 위 `event-v2.json`을 사용합니다.

생성 파일은 `$OUT/<event_id>.json`, `$OUT/<event_id>.md`입니다. 동일 폴더와 event_id로 실행하면 덮어쓰므로 매 시험 새 OUT을 사용합니다. CLI는 서버를 띄우지 않고 실행 후 종료합니다.

CPU에서 검색 문장의 임베딩을 계산하며 기관·주제에 따라 횟수가 달라질 수 있습니다. 따라서 이 실행을 모델 연산 0회라고 기록하지 않습니다. 외부 Gemini 보고 생성은 없고 동봉 문서 벡터는 재사용합니다. 기관 이름·연락 문구가 보고에 포함돼도 실제 기관에 메시지를 보내는 코드는 아닙니다.

## 8. HTTP로 요청하는 선택 경로

첫 파일 시험 후 API 연동이 필요할 때 사용합니다. 기존 서비스와 충돌하지 않도록 별도 환경의 로컬 포트 18000을 예시로 사용합니다. 아래 두 명령은 같은 가상환경을 활성화한 각각의 터미널에서 실행합니다.

터미널 1:

```bash
cd /path/to/accident_control_rag_server
source .venv/bin/activate
export HF_HUB_OFFLINE=1
export RAG_MODEL_PATH="$PWD/model"
export PORT=18000
python - <<'PY'
import getpass, os, sys
key = getpass.getpass('별도 RAG API 토큰 24자 이상: ')
if len(key) < 24:
    raise SystemExit('토큰 길이 확인 필요')
os.execve(sys.executable, [sys.executable, 'api_server.py'], dict(os.environ, RAG_API_KEY=key))
PY
```

API는 시작할 때 모델과 인덱스를 확인하고 준비용 임베딩을 계산합니다. `GET /health`의 `ready: true`를 확인합니다.

터미널 2:

```bash
cd /path/to/accident_control_rag_server
source .venv/bin/activate
python - /path/to/event-v2.json <<'PY'
import getpass, json, urllib.request
from pathlib import Path
from tempfile import mkdtemp
import sys
with urllib.request.urlopen('http://127.0.0.1:18000/health', timeout=10) as r:
    print(r.read().decode())
key = getpass.getpass('동일한 RAG API 토큰: ')
request = urllib.request.Request(
    'http://127.0.0.1:18000/report',
    data=Path(sys.argv[1]).read_bytes(),
    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=180) as r:
    response = json.load(r)
out = Path(mkdtemp(prefix='outputs-api-', dir='.'))
(out/'response.json').write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
(out/'report.md').write_text(response['report_markdown'], encoding='utf-8')
print(out)
PY
```

POST 본문은 이벤트 JSON 자체입니다. `{"event": ...}`로 감싸지 않습니다. 최대 1MiB이며 성공 응답은 `result`와 `report_markdown`으로 구성됩니다. CLI의 JSON 파일은 API 응답의 result에 해당합니다. 400은 입력, 401은 인증, 503은 준비 상태를 확인합니다. 실패 시 자동 재요청하지 않습니다.

동봉 compose는 호스트 8000번을 사용하고 빌드 때 패키지를 다운로드합니다. 위 Python 시험의 PORT=18000과 별개의 설정이며 이번에 compose/운영 포트를 바꾸지 않았습니다.

## 9. 결과 검토와 완료 기준

### 사용할 보고 형식

사용자가 제공한 `보고서초안.txt`의 다음 순서와 구성을 출력 기준으로 사용합니다. 예시의 산길·화재·기관·인용 문구를 모든 사건에 고정해서 넣는 것은 아닙니다. 실제 영상 관찰과 검색 근거에 따라 각 절의 내용을 채웁니다.

| 순서 | 보고 절 | 넣을 내용 | 결과 JSON 연결 |
|---|---|---|---|
| 1 | 사고 개요 | 장면 요약, 카메라·등록 위치, 후보 시각, 사고 형태·관련 차량 수, 현장 확인이 필요한 정보 | accident_summary, input.location/candidate_time_s/accident |
| 2 | 영상 관찰 및 판단 불가 항목 | 관찰됨·미관찰·판단 불가와 영상 근거 ID | input.hazards, input.evidence |
| 3 | 관제의 전달·요청 사항 | 기관별 연락 대상/조건, 이유, 등록 연락 경로, 전달 요약, 요청, 참고 근거 번호 | agency_responses의 notification/contact/control_handoff |
| 4 | 현장 대응 참고 | 기관별 검색 근거에 기반한 대응 문장과 [R1] 같은 참조 번호 | agency_responses의 field_response_reference |
| 5 | 확인 필요 사항 | 위치·차량·위험 상태 등 미확인 항목 | verification_needed |
| 6 | 관련 근거 | 참조 번호, 제목, 청크 ID, 본문, 종류, 적용 조건, 본문 성격, 조치 주체, 출처 | references |

검수 기준은 **영상 관찰 → 관제 전달·요청 → 현장 대응 참고 → 문서 출처의 연결**입니다. 영상 근거 ev-1과 문서 참조 R1은 서로 다른 근거이므로 구분합니다. [R번호]마다 관련 근거 절의 문서가 있어야 하며, 근거가 없으면 ‘현장 대응 근거 미확인. 구체 조치 생성 없음.’을 표시합니다. 연락처나 사고 시각을 추측해 채우지 않습니다.

첨부 초안은 동봉 `example_report.md` 및 API 예시의 `report_markdown`과 줄바꿈 정규화 후 정확히 같습니다. 출력 형식 버전은 2.1입니다. 동봉 `control_rag.py`의 `markdown()`은 이미 이 여섯 절을 출력합니다. 보고 JSON에 해당 값이 제대로 채워지는지와 실제 영상의 사실 여부를 검증하는 것이 다음 연결 작업입니다. 현재 운영 Streamlit에 이 서식을 적용한 상태는 아닙니다. 기관별 안내는 보고 내용이며 실제 연락·출동 실행을 의미하지 않습니다.

한 사건의 입력부터 결과까지 다음 자료를 함께 보관합니다.

```text
trial-XXXXXX/
  input/                검증된 S3 근거 묶음
  vlm/
    request.json        비밀 제외 요청 기록·원본/시험 ID·해시
    response.raw.json   Gemini 원문 응답·사용량
    event-v2.json       검증된 RAG 입력
    result.json         결과 해시·모델·출처·시각 기준
    failure.json        실패한 경우에만 저장
  report-XXXXXX/
    <event_id>.json     검색 과정과 보고 원문
    <event_id>.md       사람이 읽는 보고
  review.json           담당자가 기록할 내용 검수 결과·미확인 사항
```

`review.json`은 자동 생성하지 않습니다. 내용 검토 후 담당자가 따로 기록합니다.

확인할 순서:

1. **VLM 사실:** 실제 클립에서 보이는 내용과 description·객체·위험 상태가 맞는가. 가림/주석/ROI를 확정 사실로 오해하지 않았는가.
2. **검색:** `retrieval.trace`의 쿼리·후보·조건 검사·선택 문서가 관찰 사실과 연결되는가.
3. **보고:** `agency_responses[].control_handoff`와 `field_response_reference`를 구분하고, 후자의 reference_ids가 `references`의 실제 문서에 연결되는가.
4. **근거 부족:** `evidence_status: insufficient_evidence`와 불확실성이 결과에 남는가.
5. **출처:** source_based_summary 등 편집 요약과 원문을 구분했는가. 문장 일부는 코드의 요약 템플릿이므로 모두 직접 인용으로 표시하지 않는다.
6. **결과 상태:** `draft_requires_operator_review`, 보고 형식 2.1, `generation.mode: rule_based_templates`를 확인한다.

이번 RAG에는 시행일 기준 필터가 확인되지 않았습니다. 관련 문서가 검색됐다는 이유만으로 사고 날짜에 적용 가능한 법이나 책임 판단이 검증됐다고 표시하지 않습니다. `sources`는 검색 후보 전체, `references`는 최종 표시 근거이므로 개수가 달라도 바로 오류는 아닙니다.

**전체 시험 완료:** 실제 S3 근거를 본 새 VLM의 원문·규격 2.0 JSON·검색 기록·보고 파일·사람 검토가 모두 연결돼야 합니다. 가상 예시 보고 성공만으로 전체 완료로 표시하지 않습니다.

## 10. 이번 확인 결과와 다음 작업

처음에는 동료 ZIP의 코드 구문, SQLite 청크/벡터 수와 모델 설정, README를 제외한 manifest 대상 11개의 SHA256을 읽기 확인했습니다. 후속으로 S3 규격 검증 코드를 재사용하는 별도 VLM 규격 2.0 호출부를 추가했습니다. 저장된 미디어와 가짜 응답으로 로컬 8개 시험을 통과했습니다. 정상 저장, 중복 호출 차단, 타임아웃, 불완전/차단 응답, 잘못된 JSON/참조, 계약·미디어 변조를 검사했습니다.

### 첫 실제 실행 결과

사용자는 Mac 내부 Python 가상환경과 T9 자료로 기존 B의 S3 근거를 검증하고 Gemini 생성 1회, 동봉 E5의 CPU 검색·보고 저장을 완료했습니다. 결과는 `trial-RF2Gbq/report-QKtVb4`의 사건 ID별 `.json`과 `.md`입니다. 보고 입력은 실제 VLM JSON과 같고, Markdown은 결과 JSON의 renderer 출력과 같습니다. 요청한 6절과 R1~R3의 ID·본문·제목·출처 연결을 확인했습니다. 이는 제공된 청크와의 일치 검사이며 원출처 법령의 정확성·적용성 검증은 아닙니다.

이번 출력에서 확인한 보완점은 세 가지입니다. 원본 결과와 동료 코드는 수정하지 않았습니다.

- `확인 필요 사항`이 비어 있습니다. 입력의 `uncertainties: []`가 그대로 전달돼 위치·카메라·후보 시각 미제공과 끼임 여부 `unknown`이 목록에 보충되지 않습니다.
- 기관별 선정 이유가 모두 같은 `hazards.lane_blocked` 문구입니다. 소방의 조건부 분기 이유인 탑승자 상태 확인 필요가 해당 이유에서 사라집니다.
- R3의 사고유형 정의가 현장 대응 항목에도 출력됩니다. 분류용 참고 문서와 실제 대응 지침을 구분할 필요가 있습니다.

**다음 한 작업은 저장된 영상·VLM JSON·보고를 함께 읽는 내용 검수입니다.** 특히 승용차 2대·트럭 1대라는 관찰, 사고 관련 차량 수 3과 다중 충돌 분류가 실제 클립에 맞는지 확인합니다. 추가 Gemini 호출 없이 기존 결과로 검토할 수 있습니다. 보완 코드는 다음 작업 범위를 정한 뒤 적용하며 현재 실행 성공을 내용 품질 최종 승인으로 표시하지 않습니다. 검증 기록은 프로젝트의 `artifacts/rag/s3-vlm-rag-manual-20261002-01/rag-report-verification-01.json`입니다.

동료에게 전달할 짧은 설명:

> 이미 분석된 S3 클립·프레임과 출처 정보를 재사용합니다. 새 VLM이 규격 2.0의 관찰 JSON을 만들면, 동봉 E5·SQLite 검색기로 문서를 찾고 규칙 기반 보고를 파일로 확인합니다. Runpod 재분석 없이 독립 시험하며, VLM 원문부터 근거·보고까지 함께 검수합니다.

검토 질문: 영상에서 보이는 내용과 문서의 대응 안내가 구분돼 있는가? 보고가 생성됐다는 사실만으로 사고나 책임이 확정됐다고 오해할 부분은 없는가?

## 11. 10월 3일 받은 `rag_team_code`로 실행 경로 변경

새 폴더를 사용할 수 있습니다. 실제 비교 결과 `control_rag.py`, `event.schema.json`, `VLM_PROMPT.md`, `contacts.json`, `data/chunks.jsonl`, `data/index.sqlite`는 이전 시험판과 SHA256이 같습니다. 검색 대상 275청크·320벡터, E5-small 384차원도 같습니다. **이번 묶음에서 보고 로직의 개선은 확인되지 않았습니다.** 10절의 세 보완점도 그대로 남아 있습니다.

달라진 점은 간편 실행용 `run.py`가 추가되고, 모델 폴더와 API 서버 관련 파일이 이 묶음에 포함되지 않은 점입니다. requirements의 5패키지는 기존 Mac 가상환경에 같은 버전으로 설치돼 있습니다. 기존에 생성한 `event-v2.json`은 새 파일 스키마와 코드의 입력 검사를 모두 통과했습니다.

동일한 입력·코드·인덱스이므로 기존 보고를 검토하는 데 재실행은 필요하지 않습니다. **새 진입점으로 저장까지 확인하고 싶을 때만**, 아래 명령을 Mac 터미널에서 실행합니다. 이 명령은 기존 모델로 CPU 검색을 수행합니다. Runpod·S3·Gemini는 호출하지 않으며 보고를 새 폴더에 저장합니다. 에이전트는 이 명령을 실행하지 않았습니다.

```bash
(
set -e
CCTV_T9="/path/to/data/한화미니프로젝트"
CCTV_RAG_NEW="$CCTV_T9/청크/1003/rag_team_code"
CCTV_RAG_MODEL="$CCTV_T9/test_file/accident_control_rag_server/model"
CCTV_TRIAL="$CCTV_T9/test_file/s3-vlm-rag-trial-share-03/trial-RF2Gbq"
CCTV_PYTHON="./.venv-rag-trial/bin/python"

test -x "$CCTV_PYTHON"
test -f "$CCTV_RAG_NEW/data/index.sqlite"
test -d "$CCTV_RAG_MODEL"
test -f "$CCTV_TRIAL/vlm/event-v2.json"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=2
CCTV_REPORT_OUT="$(mktemp -d "$CCTV_TRIAL/report-1003-XXXXXX")"

"$CCTV_PYTHON" -B "$CCTV_RAG_NEW/run.py" \
  "$CCTV_TRIAL/vlm/event-v2.json" \
  --model-path "$CCTV_RAG_MODEL" \
  --output "$CCTV_REPORT_OUT"
)
```

성공하면 `JSON:`과 `REPORT:` 뒤에 새 결과 경로가 출력됩니다. 입력을 생략하면 산지 화재 데모를 실행하므로 위의 실제 JSON 경로를 유지합니다. DB 존재 확인은 `run.py`의 자동 인덱스 생성을 피하고, `--model-path`는 보유 모델을 사용하도록 지정합니다. 이 파일 실행만으로 Streamlit/EC2에 결과가 등록되지는 않습니다.

비교 근거: `artifacts/rag/team-version-20261003-01/source-comparison-01.json`. 모델 호출 없이 8개 파일 비교, SQLite 읽기 검사, 기존 실제 입력 검증, 설치 버전 확인을 수행했습니다. README는 검토에서 제외했습니다.
