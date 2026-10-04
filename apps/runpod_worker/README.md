# 최신: 전후 클립 생성·선택적 S3 업로드

[S3_SETUP.md](S3_SETUP.md)의 새 묶음 설치→기존 성공 결과로 클립 생성→S3 설정 순서를 먼저 따른다. 기본은 S3 호출 없는 local 모드다. 새 코드는 클립·PNG·evidence.json을 결과 ZIP에 포함한다. 원본 모델/추론 코드는 보존했다. 아래는 최초 배포 당시 안내이며 최초 미실행 상태와 JSON만 생성한다는 설명은 이력이다.

# Runpod A4000 — 영상 한 건 분석 API 연결

2026-09-27. 사용자가 A4000으로 변경하고 Deploy 후 생성 중이라고 보고했다.
이 묶음은 **첫 GPU/API 연결용**이다. S3 없이 Container disk를 사용한다.
Mac에서는 실행 묶음과 API 로직만 검사했다. 실제 Runpod 설치·GPU 실행·HTTP 프록시 연결은 아직 확인하지 않았다.

## 1. Pod가 Running이 되면

1. Connect → JupyterLab(HTTP8888)을 연다.
2. 파일 브라우저에서 `/workspace`에 `cctv-runpod-a4000.zip`을 업로드한다.
3. JupyterLab의 **Terminal**을 열어 아래를 실행한다. 노트북 코드 셀이 아니다.

```bash
cd /workspace
python3 -m zipfile -e cctv-runpod-a4000.zip .
cd cctv-runpod-a4000
bash setup.sh
```

같은 이름의 폴더가 이미 있다면 덮어쓰지 말고 다른 작업 폴더에 해제한다.
설치는 Runpod에서 인터넷을 사용한다. CUDA PyTorch는 선택한 이미지의 것을 재사용하며
torch2.8.0/torchvision0.23.0이 아니거나 CUDA가 없으면 중단한다.
`setup.sh`는 별도 `.venv`에 패키지를 설치하고 `runtime/setup.log`,
`environment.freeze.txt`, `environment.json`을 남긴다. 설치·모델 패키지 호환성은 실제 출력으로 확인해야 한다.

`Bundle verified`와 `Setup finished`가 나오면 설치 단계가 끝난다.
실패 시 계속 재설치하지 말고 에러 마지막 부분을 확인한다. 이 단계는 모델 추론 성공을 뜻하지 않는다.

## 2. API 실행

같은 Terminal에서:

```bash
bash start.sh
```

서버는 포트8000에서 실행된다. 이 Terminal을 유지한다.
첫 시작에 무작위 인증 토큰을 `.api-token`에600권한으로 만든다. 토큰은 화면에 출력하지 않는다.
토큰/계정 키를 채팅이나 Git에 붙여넣지 않는다. 이 토큰은 앱용이며 Runpod 계정 API 키와 다르다.

새 Terminal을 열어:

```bash
cd /workspace/cctv-runpod-a4000
.venv/bin/python client.py health
```

`status: ok`는 API 응답 확인이다. 모델 추론 확인은 다음 단계에서 한다.

## 3. 시험 영상 한 건 제출

처음에는 **사용자가 선택한30~60초 MP4 한 개**를 `/workspace/input.mp4`로 업로드한다.
미노출 최종평가 자료를 자동 선택하지 않는다. API는 최대120초/200MiB를 허용한다.

```bash
cd /workspace/cctv-runpod-a4000
.venv/bin/python client.py submit --video /workspace/input.mp4
```

이 명령부터 실제 GPU 추론을 수행한다. 응답의 `job_id`를 복사해 아래 `JOB_ID` 자리에 넣는다.

```bash
.venv/bin/python client.py status --job JOB_ID
```

`queued → running → succeeded`를 확인한다. `failed`이면 정상 영상 판정으로 처리하지 않는다.
실패 로그는 아래 명령으로 받는다.

```bash
.venv/bin/python client.py log --job JOB_ID
```

완료 결과 다운로드:

```bash
.venv/bin/python client.py result --job JOB_ID
```

생성된 `JOB_ID-results.zip`을 Jupyter 파일 브라우저에서 Mac으로 내려받는다.
성공 ZIP에는 `result.json`, `vlm_handoffs.json`, `runtime.json`, `manifest.json`, `worker.log`가 있다.
실패 시에도 `runtime/setup.log`, 설치 환경 기록과 해당 `runtime/jobs/JOB_ID/worker.log`를 보존한다.

## 4. AWS BE 또는 다른 컴퓨터에서 호출

Runpod HTTP8000이 노출돼 있고 API가 실행 중일 때:

```text
https://POD_ID-8000.proxy.runpod.net
```

모든 경로에 `Authorization: Bearer <앱 토큰>`이 필요하다.
BE는 `.api-token`을 비밀 저장소에 보관하고 서버에서만 사용한다. 브라우저에 노출하지 않는다.

| 요청 | 역할 |
|---|---|
| GET /health | API 응답 확인; 모델 연산 없음 |
| POST /jobs | Content-Type: video/mp4, 본문에 MP4 바이트. 202와job_id 반환 |
| GET /jobs/{job_id} | 상태 조회 |
| GET /jobs/{job_id}/result | 성공한 결과 ZIP 다운로드 |
| GET /jobs/{job_id}/log | 작업 로그 다운로드 |

첨부된 `client.py --url <HTTPS주소> --token-file <비밀파일>`로 같은 요청을 보낼 수 있다.
multipart 형식이 아니라 원본 MP4 바이트를 보내는 첫 연결 계약이다.
입력 업로드 후202를 반환하고 추론은 별도 프로세스에서 실행해 프록시100초 제한을 피한다.
업로드 자체는90초 안에 끝나야 하며, 큰 영상은 후속 S3 객체 참조 방식으로 바꾼다.
HTTP 요청 자동 재시도는 하지 않는다. 업로드 응답을 잃었을 때 바로 재전송하면 중복 작업이 생길 수 있다.

## 5. 실행 범위와 제한

- GPU동시1, 실행+대기+업로드 합계2개, 저장작업 최대10개, 작업별 모델 프로세스 최대300초.
- 첫 연결에서는 작업마다 모델을 다시 로딩한다. 지속 로딩/성능 최적화는 실측 후 별도 단계다.
- 원본 `handoff/pipeline.py`, `timeline.py`와 두 체크포인트는 수정하지 않았다.
  원래 X3D16프레임/182×182/2초창·1초간격/batch8/threshold0.5,
  YOLO640/conf0.25/후보당한장, CUDA FP16을 유지한다.
- PyTorchVideo0.1.5의 로컬 factory에서 공식hub X3D-S 생성 기본값13/160을 재현한다.
  이는 모델 생성 인자이며 전처리16/182를 바꾸는 설정이 아니다. strict state_dict 로딩을 유지한다.
  원본 torch.hub의 미고정 GitHub 기본 브랜치를 런타임에 내려받지 않는다.
- YOLO 체크포인트에 기록된Ultralytics8.4.155를 사용한다. 제공한 코드/모델이 기준이며
  사용자가 성공시킨 Colab 실행본/패키지 목록은 미수신이므로 완전히 같은 환경이라고 주장하지 않는다.
- 디코딩 프레임 수와 선언된 프레임 수/120초 상한을 추가 검사한다. 맞지 않으면 실패한다.
  원본 코드의 FPS 기반 시각을 유지한다. VFR/PTS 정확성, 충돌 시각 정확성은 검증하지 않았다.
- `runtime.json`은 전체worker/로딩/분석·JSON시간과PyTorch GPU최대메모리를 남긴다.
  순수 GPU forward시간이나 장치 전체 메모리/영상 사건 지연 측정값은 아니다.
- S3 업로드, 근거 클립/이미지 생성, VLM/RAG, 공통서비스계약 변환, 영구DB는 아직 없다.
  이 API는`runpod-local-api-v1`이며 기존`service-draft-v0.2` 완료 구현으로 취급하지 않는다.
- API 재시작 때 남은미완료작업은failed로 표시하며 자동재추론하지 않는다.
  서버는한프로세스로실행한다. Pod중지로디스크가삭제되면기록도사라진다.
- 모델/원본/수정코드/설치로그/결과를 내려받고 나서 Pod를 중지한다.
  Jupyter Terminal 실행은 첫 확인용이다. 서비스 자동시작·재시작 관리는 후속 배포 단계다.

## 포함 자료와 검증

`bundle-manifest.json`에 코드/모델 SHA256과 원본 인계 파일과의 일치 여부를 기록한다.
원본 모델 두 개 합계약55.5MB이며 영상·인증 키는 ZIP에 포함하지 않는다.
표준 API/상태 로직 시험은 합성 worker를 사용하며 실제 예측을 만든 시험이 아니다.
Mac에서 torch/Ultralytics import·모델 추론·설치를 하지 않았다.

공식 참고:
- https://docs.runpod.io/pods/configuration/expose-ports
- https://docs.runpod.io/pods/connect-to-a-pod
- https://docs.runpod.io/pods/storage/types
- https://github.com/facebookresearch/pytorchvideo/blob/main/pytorchvideo/models/hub/x3d.py

이해 확인: `health: ok`와 영상분석`succeeded`는 어떤 차이가 있을까?
동료30초요약: “Runpod에원본두모델과코드를올리고인증된API로영상한개를접수한다.
작업ID로완료를조회하고결과JSON·로그·측정값을ZIP으로회수한다. 먼저임시디스크로연결을확인하고S3와근거파일은이후붙인다.”
