# S3 근거 한 건 → Gemini 장면 설명 → PostgreSQL 저장

## v2 분류와 BE 연결 — 2026-09-29

새 설명 결과는 `cctv-scene-v2`다. `accident_suspicion`은 `suspected`(사고 의심),
`not_observed`(이 클립에서 징후 미관측), `uncertain`(판단 어려움)을 구분한다.
`incident_types`는 `collision`, `rollover`, `off_road`, `other`, `unknown` 목록이다.
전도 사례는 `suspected` + `["rollover"]`로 표현할 수 있으며 다른 차량과 접촉이
없다는 이유로 전체 사고가 없다고 처리하지 않는다. 출력은 사람 검토 전 AI 관찰이다.

`schema.sql`은 기존 테이블에 두 열을 추가한다. 기존 v1의 `assessment`·본문·해시는
보존하고 자동 재분류하지 않는다. v2의 legacy `assessment` 열은 NULL이다.
DB 도구는 두 버전을 명시적으로 검증한다. 새 코드로 적재하기 전에 스키마를 적용한다.

기존 BE 작업 ID로 미디어 회수·설명·저장을 재개하는 방법은
[BE 실행 안내](../be_worker/QUEUE.md)를 따른다. 동료의 검색 문장·보고 생성 함수에는
v2 `result.json`을 전달한다. `rag-handoff.json`은 작업/사건/분석ID·결과SHA를 연결하며
검색이나 보고가 완료됐다는 뜻은 아니다.


## 현재 범위

사용자가 알려준 작업 `fd20cca0be53483696e6704e7c985b84`의 `event_000`을 대상으로 준비했다. `sample.json`의 버킷·리전·job/event와 기존 원본 영상 SHA를 고정했다. S3 파일 존재·내용은 사용자 보고이며 이 코드 준비 중 원격 파일을 읽거나 Gemini를 호출하지 않았다.

1. `fetch`: S3 manifest와 evidence.json을 읽고 선택된 clip.mp4·frame.png를 받는다. 같은 job/event/원본인지, 크기·SHA256·시각을 확인한다.
2. `describe`: 실제 클립과 그 대표 프레임을 함께 Gemini에 보내 설명 JSON을 생성한다. 한 번의 API 요청이며 자동 재시도하지 않는다.
3. `db.sh schema/load/show`: 기존 EC2 Compose DB에 설명을 저장하고 조회한다. 이 단계는 Gemini를 호출하지 않는다.

현재 버전은 BE에서 재사용할 함수와 CLI다. HTTP 조회 API·화면·RAG 보고·자동 알림은 후속이며 장면 설명 저장을 전체 서비스 완성으로 표시하지 않는다. 실제 원본/근거를 새로 자르거나 GPU 추론/문서 임베딩을 반복하지 않는다.

## 1. 지금 실행 — Mac에서 S3 근거 받기

EC2 역할 권한을 기다리는 동안 Mac의 기존 AWS CLI `cctv` 프로필로 확인할 수 있다. Mac에 별도 Python 패키지를 설치하지 않는 경로다. 이 프로필에 해당 S3 객체의 GetObject 권한이 필요하며 IAM 프로필 조회 권한과는 다르다.

```bash
cd .
python3 apps/scene_description/scene.py fetch \
  --aws-profile cctv \
  --output artifacts/vlm/fd20cca0-event000-input-v1
```

읽을 경로:

- `cctv-evidence/fd20cca0be53483696e6704e7c985b84/manifest.json`
- 같은 작업의 `evidence.json`
- `evidence/event_000/clip.mp4`
- `evidence/event_000/frame.png`

`status: verified`, `gemini_api_calls: 0`을 확인한다. S3 읽기/전송 사용량은 발생한다. `AccessDenied`는 현재 실행자의 S3 읽기 권한, `NoSuchKey`는 객체 경로를 확인한다. manifest 또는 evidence.json이 없으면 클립/이미지만으로 출처·시각을 추측하지 않고 중단한다. 부분 다운로드 폴더는 보존하므로 실패 원인을 확인한 뒤 재시도에는 새 폴더 이름을 사용한다.

## 2. Mac에서 Gemini 한 번 호출

1번이 성공한 뒤 같은 Mac 터미널에서 실행한다.

```bash
python3 apps/scene_description/scene.py describe \
  --input artifacts/vlm/fd20cca0-event000-input-v1 \
  --output artifacts/vlm/fd20cca0-event000-description-v1
```

`Gemini API key (hidden):`에 키를 비공개로 입력한다. 이미 EC2에 저장한 키가 Mac으로 자동 전달되지는 않는다. 환경변수 GEMINI_API_KEY가 있으면 그것을 사용하며 키를 결과에 저장하지 않는다.

준비 기본값은 영상 입력을 지원하는 `gemini-3.5-flash-lite`, 샘플링4FPS다. 임베딩001과 다른 생성 모델이다. 다른 영상 지원 모델을 사용하려면 `--model`을 명시한다. 모델 계정 가용성·실제 지연/품질/비용은 첫 응답에서 확인해야 하며, 모델 접근 오류 시 자동으로 다른 모델을 호출하지 않는다.

이번 요청은 클립·프레임·선택된 객체/시각 메타데이터를 Google Gemini API로 전송한다. API 사용량이 발생한다. S3 콘솔 URL이나 비공개 s3://경로를 모델에 보내는 방식이 아니다. 짧은 클립의 움직임과 같은 장면의 PNG를 함께 전달하며 같은 객체를 두 사건으로 세지 않도록 안내한다.

성공 출력은 `status: completed`, `analysis_id`, `assessment`, `summary`, `result_file`이다. 이때 `database_loaded: false`는 아직 결과 파일을 DB에 적재하지 않았다는 뜻이다.

생성 파일:

- `request.json`: 사건 ID·모델·프롬프트/요청 SHA·요청 시각. 키와 미디어 본문은 저장하지 않는다.
- `response.raw.json`: 실제 Gemini 응답과 사용량. 원문을 보존한다.
- `result.json`: 검증한 설명·관찰/불확실성·미디어 참조·원본 상대시각·버전·해시.
- 실패 시 `failure.json`: 실패 단계와 응답 수신 여부. 실패를 사고 없음으로 처리하지 않는다.

출력이 이미 있으면 재호출을 거부한다. 정상 result.json이 있으면 DB 단계만 진행한다. 응답을 받지 못한 timeout은 공급자 처리 완료 여부가 불확실하므로 기록부터 확인한다. 결과의 시각은 모델 관찰을 원본 좌표로 환산한 참고값이며 실제 충돌 정답 시각이 아니다. AI assessment는 사람의 사고 확정/법적 책임 판단이 아니다.

## 3. 결과를 EC2 DB에 저장

Mac에서 실행했어도 생성한 결과를 EC2로 옮겨 저장하면 된다. Gemini 재호출은 하지 않는다. 다음 코드 묶음과 result.json만 전송하고 AWS/Gemini 키는 전송하지 않는다. 기존 배포 SSH 도우미와 이미 설치된 RAG 도구 Docker 이미지를 재사용한다.

### 3-1. Mac: 코드 묶음과 결과 파일 전송

아래는 각 명령이 성공한 뒤 다음 명령을 실행한다. 이미 파일/폴더가 있으면 덮어쓰지 않으므로 해당 상태를 확인한다.

```bash
cd .
bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh \
  'umask 077; set -C; cat > /tmp/cctv-scene-description-v1.zip' \
  < artifacts/vlm/scene-description-20260927-01/cctv-scene-description-v1.zip

bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh \
  'umask 077; mkdir /home/ubuntu/cctv-scene-results-v1 && cat > /home/ubuntu/cctv-scene-results-v1/result.json && chmod 644 /home/ubuntu/cctv-scene-results-v1/result.json' \
  < artifacts/vlm/fd20cca0-event000-description-v1/result.json

bash artifacts/infra/cpu-rag-ssh-20260927-01/ssh.sh
```

결과 폴더는0700이며 파일 하나만 읽기 전용으로 DB 도구 컨테이너에 연결한다. 전용 SSH 키/known_hosts는 기존 위치를 유지한다.

### 3-2. EC2: 새 코드 확인·테이블 생성·적재·조회

```bash
sudo mkdir /opt/cctv-scene-description-v1
sudo python3 -m zipfile -e /tmp/cctv-scene-description-v1.zip /opt/cctv-scene-description-v1
python3 /opt/cctv-scene-description-v1/verify_bundle.py

sudo bash /opt/cctv-scene-description-v1/db.sh schema
sudo bash /opt/cctv-scene-description-v1/db.sh load /home/ubuntu/cctv-scene-results-v1/result.json
sudo bash /opt/cctv-scene-description-v1/db.sh show /home/ubuntu/cctv-scene-results-v1/result.json
```

`schema`는 새 cctv_scene.descriptions 테이블과 앱의 SELECT/INSERT 권한을 준비한다. 기존 문서 벡터와 원본 결과는 보존한다. `load`에서 `committed: true`, 처음에는 `inserted: true`를 확인한다. 같은 analysis_id·내용을 다시 적재하면 `already_present: true`이며, 같은 ID로 내용을 바꾼 경우 기존 행을 보존하고 실패한다.

`show`의 최상위 `database_loaded: true`와 저장된 summary를 확인한다. 내부 result.database_loaded는 생성 시점의 false 기록을 보존한 것이므로 현재 DB 상태는 load 영수증·show 최상위 값으로 확인한다. 이 도구 이미지는 기존 EC2의 cctv-rag-tools:gemini-001-768-v1을 재사용하며 새 이미지를 자동 다운로드하지 않는다.

## 4. IAM 연결 후 EC2에서 직접 실행하는 경우

Mac 실행에서 이미 정상 설명을 받았다면 재실행할 필요 없다. 이후 새 작업은 같은 코드를 EC2에서 실행할 수 있다. EC2 호스트의 기존 python3-boto3와 역할 자격 증명을 사용하고, Gemini 키도 기존 서버 비밀 파일을 읽는다. 컨테이너에 AWS 장기 키를 복사하지 않는다.

```bash
python3 /opt/cctv-scene-description-v1/scene.py fetch \
  --output /home/ubuntu/cctv-scene-input-v2

sudo python3 /opt/cctv-scene-description-v1/scene.py describe \
  --input /home/ubuntu/cctv-scene-input-v2 \
  --output /home/ubuntu/cctv-scene-description-v2 \
  --key-file /etc/cctv/secrets/gemini_api_key
```

S3 권한이 없으면 fetch에서 중단한다. 위 경로는 호스트에서 S3를 읽으므로 현재 Docker IMDS hop limit을 변경하지 않는다. 앞으로 BE를 상시 컨테이너로 통합할 때는 별도로 AWS 자격 증명 경로를 검증해야 한다.

## 검증·제한

- 합성 응답으로 성공/다른 event/손상·초과 크기/응답 차단·잘림/시각 오류/실패 보존/중복 호출 방지를 검사한다. 실제 CCTV 설명 품질 시험은 아니다.
- 클립30초 이하, 클립+이미지12MiB 이하, 전체 inline 요청18MiB 이하로 제한한다. 더 큰 영상은 Files API 전송을 별도 구현한다. 현재 파일 업로드 API나 자동 압축으로 임의 전환하지 않는다.
- 파일 시그니처와 manifest 해시는 확인하지만 이 도구가 영상을 전체 디코딩해 재생 품질을 보증하는 것은 아니다. 원본 timestamp 기준은 기존 Runpod evidence 생성기의 검증을 따른다.
- 생성 모델·입력FPS·요청/응답·코드 SHA를 기록한다. 기본4FPS의 사고 장면 품질/감지 성능은 검증 전이며 사용량·실제 관찰 결과로 조정한다.
- 실제 S3/Gemini/EC2 DB 실행은 사용자가 CLI에서 수행한다. 아직 화면·보고·알림을 자동으로 제공하지 않는다.

공식 API 확인: [영상 입력](https://ai.google.dev/gemini-api/docs/generate-content/video-understanding), [generateContent REST](https://ai.google.dev/api/generate-content), [모델 목록](https://ai.google.dev/gemini-api/docs/models), [Gemini 3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite). 현재 generateContent 문서는 Legacy로 표시되지만 영상 입력/샘플링을 제공한다. 이 릴리스의 실제 계정 호출은 미검증이다.
