# 팀원 PC에서 실행하는 사고 RAG

Python 코드로 입력 JSON을 읽고 RAG 결과 JSON과 사고 리포트(.md)를 만듭니다.
CPU에서 실행하며 GPU, API 키, Docker, 웹서버가 필요하지 않습니다.
모델 가중치는 ZIP에 넣지 않았습니다. 첫 실행에는 인터넷 연결이 필요하고,
intfloat/multilingual-e5-small 모델 약 500MB를 model_cache에 내려받습니다.
벡터 DB와 기존 청크 파일은 포함되어 있어 다시 임베딩하지 않아도 됩니다.

## 1. 준비

압축을 해제하고 rag_team_code 폴더로 이동합니다. Python 3.11을 권장합니다.
아래 설치는 최초 한 번만 합니다.

Windows PowerShell:

```powershell
cd "압축을 푼 경로\rag_team_code"
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS / Linux:

```bash
cd /압축을/푼/경로/rag_team_code
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Python 3.11 명령이 없으면 해당 버전을 먼저 설치하세요.

## 2. 예시 실행

Windows:

```powershell
.\.venv\Scripts\python.exe run.py
```

macOS / Linux:

```bash
.venv/bin/python run.py
```

기본 예시는 산길 사고 후 화재가 보이는 상황입니다.
터널 예시는 입력 경로를 지정합니다.

```powershell
.\.venv\Scripts\python.exe run.py examples/example_tunnel_fire.json
```

macOS/Linux에서는 위 python 경로를 `.venv/bin/python`으로 바꾸면 됩니다.

## 3. 실제 VLM 결과 입력

event.schema.json에 맞는 JSON을 input.json으로 저장하고 실행합니다.

```powershell
.\.venv\Scripts\python.exe run.py input.json
```

출력 위치를 지정할 수도 있습니다.

```powershell
.\.venv\Scripts\python.exe run.py input.json --output results
```

기본 저장 위치는 outputs 폴더입니다.
`<event_id>.json`: 결과·기관별 대응·근거·검색 쿼리와 순위.
`<event_id>.md`: 사고 요약 → 관제 전달·요청 → 현장 대응 참고 → 확인 사항 → 관련 근거.
같은 event_id와 출력 폴더로 다시 실행하면 해당 결과 파일을 덮어씁니다.

입력 버전은 2.0입니다. Van은 제외하며 승합차는 Car로 기록합니다.
operator_confirmed 및 근거의 asset_id/start_s/end_s는 사용하지 않습니다.
observed에는 유효한 evidence_ids가 필요하고 evidence 항목은 id/observation만 받습니다.
예시 파일은 가상 상황 또는 저장된 VLM 결과이며, 실제 영상을 이 프로그램이 판독하지 않습니다.

## 4. 검색 방식

공통 초동조치와 관찰된 상황별로 쿼리를 나눕니다.
기관·조치 주체·적용 조건을 점검한 청크에서 BM25 및 E5 임베딩 검색을 실행합니다.
각 Top 20의 순위를 Weighted RRF(BM25 0.6, 임베딩 0.4)로 결합합니다.
검색된 본문 발췌·정해진 요약으로 대응을 구성하고 근거를 연결합니다.
생성 LLM/VLM은 연결되어 있지 않습니다. 관할 연락처는 contacts.json에 등록합니다.
실제 신고·전화는 수행하지 않습니다.

## 5. 청크를 변경한 경우에만 DB 재구축

```powershell
.\.venv\Scripts\python.exe control_rag.py index --chunks data/chunks.jsonl --revision 614241f622f53c4eeff9890bdc4f31cfecc418b3
```

모델 다운로드 후에는 동일 PC의 model_cache를 사용합니다. 이미 준비된 별도 모델 폴더가 있으면:

```powershell
.\.venv\Scripts\python.exe run.py input.json --model-path "C:\models\multilingual-e5-small"
```

별도 모델 폴더는 DB와 동일한 E5 revision이어야 합니다.
Windows Python 3.9.13에서 로컬 모델을 사용한 CLI 실행을 검증했습니다.
macOS/Linux와 첫 다운로드 실행은 이번 검증에 포함되지 않았습니다.
