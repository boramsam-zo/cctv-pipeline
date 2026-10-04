# Runpod 이미지 — B/R2 MLOps 배포 준비

## 2026-10-04 새 CI와 수동 Pod 연결

새 배포 식별자는 `mlops-20261004-v1`이며 신규 제출 버전은 **B/R2**입니다. 아래 P4/P6의 A/B 실행 기록과 기존 결과는 이력으로 보존합니다. 새 코드는 준비·오프라인 검사 단계이며, 실제 이미지 게시 성공은 GitHub Actions 결과와 registry digest로 확인해야 합니다.

GitHub 저장소의 Actions Secrets에 `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`을 등록합니다. 선택적인 Actions Variable `DOCKERHUB_IMAGE`의 기본값은 `dahyeguk/cctv-runpod-ab`입니다. AWS 자격 증명과 모델 파일은 GitHub 이미지 빌드에 전달하지 않습니다.

`.github/workflows/runpod-image.yml`은 관련 PR과 main 변경의 CPU 계약/서비스/MLflow 검사를 실행합니다. 검사가 성공하고 이미지 소스·계약이 바뀐 main push 또는 main의 수동 `workflow_dispatch`일 때만 Linux amd64 이미지를 빌드·게시합니다. BE 전용 변경은 검사만 수행합니다. 결과는 `sha-<전체 commit SHA>` 태그와 `저장소@sha256:<digest>`로 Actions Summary에 표시하며 기존 같은 commit 태그는 덮어쓰지 않습니다. `latest`는 사용하지 않습니다.

이미지는 고정 P6 digest의 환경·기존 가중치를 재사용하고 허용된 최신 소스만 덧씌웁니다. 신규 R2 가중치는 이미지와 Git에 포함하지 않고 Pod의 비공개 S3 설정으로 준비합니다. 소스와 카탈로그 해시가 다르면 빌드를 중단합니다. 의도한 코드 변경 후 개발자가 `python3 -B scripts/prepare_runpod_ci.py --write-catalog`로 새 계약을 검토하며, 이전 계약은 `apps/be_worker/model_releases/`에 보존합니다.

수동 적용 순서는 다음과 같습니다.

1. 사용자가 Actions Summary의 digest를 Runpod 이미지 설정에 적용하고 Pod를 시작합니다. 기본 `pod` 명령은 관리 환경을 시작하며 분석 API를 자동 실행하지 않습니다.
2. Pod에서 토큰/AWS 파일과 `runtime.env`를 소유자 전용 `0600` 파일로 준비합니다. 필수 환경 이름은 `CCTV_API_TOKEN_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `S3_BUCKET`, `CCTV_MODEL_S3_BUCKET`, `CCTV_R2_MODEL_S3_KEY`입니다. 값은 이미지·Git·로그에 넣지 않습니다. `runtime.env`는 `export KEY=VALUE` 형식이며 실행 가능한 셸 내용은 허용하지 않습니다.
3. 사용하지 않은 세션 이름으로 아래 명령을 직접 실행합니다. B/R2 합계 2건·건당 300초·세션 900초의 API 창을 열며, 이전 창을 초기화하거나 모델을 호출하지 않습니다. 기존 이름은 시작 실패 뒤에도 재사용하지 않습니다.

```bash
bash /opt/cctv-service/apps/runpod_image/start-model-session.sh \
  mlops-session-YYYYMMDD-NN /workspace/private/runtime.env
```

4. EC2의 새 코드와 카탈로그가 적용된 뒤 `scripts/activate_mlops_session.py`에 같은 세션 이름·새 Pod URL·사용자가 선택한 짧은 영상의 SHA256을 전달합니다. API와 worker는 같은 writable spool 파일을 읽도록 `BE_TARGET_CONFIG=/spool/model-target-config.json`을 함께 지정해야 합니다. 원래 `/input`은 읽기 전용으로 유지하고 기존 config를 새 spool 파일로 보존 복사합니다. 토큰은 기존 `/run/secrets/runpod_worker_token` 파일을 사용합니다.

```bash
python -B scripts/activate_mlops_session.py \
  --name mlops-session-YYYYMMDD-NN \
  --input-sha256 <승인한-영상의-64자리-SHA256> \
  --runpod-url https://<새-Pod-ID>-8000.proxy.runpod.net
```

이 EC2 명령은 인증된 API health와 새 유한 창을 확인하고 URL/선택 세션/DB session을 활성화합니다. 작업 제출·GPU 추론·VLM 호출은 하지 않습니다. 실제 분석 요청은 이후 화면에서 B/R2를 선택해 제출하며, EC2가 Pod에 요청하고 상태를 폴링합니다. API health 성공은 전체 분석 성공과 다릅니다. Pod 생성·교체와 EC2 배포는 CI에 자동 연결되어 있지 않습니다.

MLflow 추적/등록 코드는 별도 EC2 CPU 준비 묶음에 포함됩니다. `apps/be_worker/MLops.Dockerfile`과 `scripts/build_mlops_be_image.py`는 기존 E5 CPU 이미지 ID·패키지를 보존하며 `mlflow-skinny`만 추가하도록 준비했습니다. helper 기본 실행은 계획 출력이고 `--build`를 명시할 때만 빌드합니다. MLflow 서버 기동과 서비스 재시작은 수행하지 않으며 현재 EC2 메모리 여유 확인이 선행됩니다.

## P4/P6 완료 기록 — 아래는 당시의 A/B 절차

현재는 **P4 이미지 배포와 지정 영상의 A/B 추론 재현 검증 완료**다. A는 기존 기준으로 유지하고 B는 비교용으로 보존한다. 선택한 버전의 S3·BE·Streamlit 연결은 P6에서 검증한다.

## P6 서비스 이미지 — Runpod 서비스 파일 검증 완료, EC2 연결 준비

`Service.Dockerfile`은 게시된 P4 digest를 기반으로 `/opt/cctv-service`에 작은 서비스 코드를 추가한다. **한 이미지에 기존 A/B 모델·환경과 새 HTTP/S3 서비스가 함께 들어간다.** `/opt/cctv-image`의 모델·실행기·manifest는 그대로 사용한다. P4 태그와 기존 검증 결과를 덮어쓰지 않는다. Mac의 `cctv-runpod-ab:p6-service-v1` 빌드와 격리 컨테이너 파일/환경/import 검사는 완료했다. 로컬 이미지ID는 `sha256:037355788382bd35081d798b4ff6f7131f7745735097058ba58f711af8477cb6`, 플랫폼은 linux/amd64다. 근거 `artifacts/runpod/service-p6-20261002-01/image-build-check-01.json`. 사용자 Docker Hub 게시 digest가 위 ID와 일치해 게시도 통과했다(publication-user-result-01.json). 후속 사용자 로그에서 새 Pod의 기본 환경 기동을 확인했다. P6 서비스 15파일 해시 검사와 EC2 P6 서비스 전환도 통과했다. 분석 API는 아직 시작하지 않았으며 토큰/S3·EC2 URL 설정과 실제 연결 검증이 남아 있다.

- 기본 시작은 P4의 `pod`를 상속한다. 기존 SSH/Jupyter 접속 절차를 유지하고 분석 API나 GPU 작업은 자동 시작하지 않는다.
- API는 별도 명령으로 시작하며 토큰 파일, S3 설정, 유한 실행 상한이 있어야 한다. 서비스 기동 검사는 파일/환경/A4500 장치 조회이며 새 모델 추론을 하지 않는다.
- GPU는 고정 `/workspace/cctv-image-state`의 기존 실행기를 사용한다. 서비스와 수동 실행이 같은 GPU 잠금을 공유하고 자식 프로세스가 잠금을 유지한다.
- 영상 SHA와 A/B 프로필로 기존 작업을 찾는다. 같은 요청은 기존 ID를 반환하고, 응답이 불확실하면 조회로 확인한다. 실패·재기동이 자동 GPU 재실행 권한이 되지 않는다.
- S3 실패는 완료 입력·결과·근거를 다시 검증한 뒤 `resume-evidence`로 업로드 단계만 재개한다. GPU 분석은 다시 하지 않는다.
- 서비스의 최초 시작 시각과 작업 수·전체시간 상한을 상태 폴더에 저장한다. 재기동은 상한을 초기화하지 않는다. `/workspace`가 현재처럼 임시 디스크라면 Pod 교체 시 이 기록의 보존은 별도로 확인해야 한다.

### 완료한 서비스 파일 확인 — Runpod 새 터미널

사용자 교체 후 로그에서 P4 파일 53개·Python/14패키지 일치와 nginx·sshd·Jupyter/Pod ready를 확인했다. 이어서 아래 읽기 검사 출력도 수신했다. 근거 `artifacts/runpod/service-p6-20261002-01/pod-startup-user-result-01.json`과 `service-files-user-result-01.json`.

```bash
/opt/cctv-image/.venv/bin/python -B \
  /opt/cctv-service/apps/runpod_image/verify_service_bundle.py \
  --root /opt/cctv-service
```

실제 수신: `service_bundle_verified`, `files_verified:15`, manifest SHA `feeccc4dbdc79aa6304ff517f24c6cfa764af05d7c1fc86c36e22e4c839229b8`, `model_calls:0`, `service_started:false`. 로컬 고정 manifest와 정확히 같다. 파일 읽기·해시 검사만 수행했으며 같은 검사를 반복할 필요는 없다. `base_image`의 P4 digest는 상속한 이미지이며 P6 registry digest가 아니다. 분석 API 시작은 EC2 서비스·연결 준비와 새 유한 상한 확정 뒤 수행한다.

### 준비 명령과 산출물

**실행 위치: Mac 프로젝트 폴더.** 아래 도구는 새 경로에 코드만 복사하고 해시를 검증한다. 기존 경로가 있으면 보존하고 중단한다. Docker 빌드·다운로드·게시·원격 설치·모델 호출은 하지 않는다.

```bash
python3 -B scripts/build_runpod_service_bundle.py \
  --output artifacts/runpod/service-p6-20261002-01
```

예상 결과: `service_overlay_prepared_not_built`, `docker_build_performed:false`, `model_calls:0`. `context/`는 향후 서비스 이미지 빌드에 사용할 명시적 허용 목록이고, `cctv-runpod-service.zip`은 같은 파일을 묶은 보관본이다. ZIP에는 모델·영상·결과·비밀 값이 없으며, ZIP만 새 기본 PyTorch Pod에 올려서는 분석 환경이 완성되지 않는다. 검증된 P4 이미지가 전제다. 작은 ZIP 배포와 이미지 교체를 동시에 요구하는 절차가 아니다.

허용 목록은 `verify_service_bundle.py`에 고정돼 있다. 생성기는 재귀 로컬 import 누락, P4 helper 변경, symlink를 차단한다. 컨테이너 빌드와 서비스 시작 모두 같은 파일 해시 검사를 사용한다. 위 코드 묶음 생성은 완료 당시 기록이며 같은 출력 경로로 다시 실행하지 않는다. 사용자 게시 digest 수신/로컬ID 대조와 기존 완료 결과 보존 확인, 교체 후 기본 기동 로그 확인까지 통과했다. 새 서비스 파일 검증은 통과했으며 실제 연결·새 분석/생성·화면 검수는 P6-4에 남아 있다.

**완료한 사용자 게시 명령:** `artifacts/runpod/service-p6-20261002-01/publish-command-01.sh`로 위 ID를 검증하고 `dahyeguk/cctv-runpod-ab:p6-service-v1`로 게시했다. 후속 사용자 읽기에서 A4500/overlay 30G, P4 완료 A/B 각 1회, service/jobs 부재를 확인했다. 완료 state SHA는 Mac 보관본과 일치하고 원본 23파일의 백업 검증도 보존돼 있다. 전체 Pod 파일시스템을 백업한 것은 아니다. 근거 `pod-before-change-user-result-01.json`.

**직전 사용자 적용 안내(이력): Runpod 콘솔 → 기존 A4500 Pod → Edit Pod.** Container image를 `dahyeguk/cctv-runpod-ab@sha256:037355788382bd35081d798b4ff6f7131f7745735097058ba58f711af8477cb6`로 변경한다. 기존 Container disk 30GB, HTTP 8888,8000/TCP 22, 환경설정을 유지하며 Start command는 기본 `pod`를 상속한다. 저장하면 임시 컨테이너 저장소가 초기화되므로 알려진 완료 결과는 확인한 Mac 보관본으로 보존한다. 예상 결과는 해당 digest pull 및 새 컨테이너의 환경 검사·기동 로그다. 기본 `cctv_image_ready`는 P4 53파일 검사이며 P6 서비스 15파일은 교체 후 별도 확인한다. EC2와 키·연결·새 유한 상한을 준비한 뒤 분석 API를 시작한다. 서비스 전체 시간은 최초 API 시작부터 흐르며 재기동해도 초기화되지 않는다.

### 런타임에 전달할 값

| 항목 | 전달 방식과 역할 |
|---|---|
| `CCTV_API_TOKEN_FILE` | 서버 비밀 파일 경로. BE와 같은 분석 토큰을 사용하며 이미지/ZIP에 넣지 않음 |
| `EVIDENCE_STORAGE=s3` | S3 게시 활성화 |
| `S3_BUCKET`, `AWS_DEFAULT_REGION=ap-northeast-2`, `S3_PREFIX=cctv-evidence` | 기존 근거 저장소 위치 |
| AWS 읽기·쓰기 자격 증명 | Runpod 런타임의 비밀 설정/파일. EC2 IAM 역할이 Runpod에 자동 전달되는 것은 아님 |
| `CCTV_GPU_MAX_JOBS`, `CCTV_GPU_JOB_SECONDS`, `CCTV_GPU_TOTAL_SECONDS` | 사용자가 따로 확정한 작업 수/건당 시간/서비스 전체 시간. 기본 실행 허용량 없음 |

**후속 실행 위치: 새 서비스 이미지가 적용된 Runpod 터미널.** 위 설정과 유한 상한을 확정하고 파일·저장소를 확인한 다음 사용자가 실행할 시작 명령은 아래와 같다. 현재 실행 지시나 새 GPU 호출 승인이 아니다.

```bash
bash /opt/cctv-service/apps/runpod_image/start-service.sh
```

예상 결과: `service_bundle_verified` 후 인증된 API가 8000 포트에서 시작한다. 파일·환경·장치·설정 검사가 실패하면 중단한다. 실제 분석은 이후 BE가 접수한 작업에만 해당하며, health 성공만으로 GPU·S3·설명·보고 전체 성공을 판정하지 않는다. 이미지의 기본 시작 명령을 이 명령으로 바꾸지 않아도 된다.

## 실제 GPU 재현 결과 — 2026-10-01

같은 A4500에서 기존 7초 영상으로 A/B를 각각 한 번 실행하고 Mac에 원본 23개 파일을 회수했다. ZIP 전체 해시와 내부 manifest 검증을 통과했으며, 이전 A와 새 A, 이전 B와 새 B를 각각 대조했다.

| 항목 | A | B |
|---|---:|---:|
| 창 점수·시각 | 6개 모두 동일 | 6개 모두 동일 |
| 후보 수 / 대표 시각 | 1개 / 1.0초 | 1개 / 1.0초 |
| 근거 미디어 바이트 | 클립·PNG 2개 동일 | 전체·ROI클립·JPG 3개 동일 |
| 이전→이미지 처리시간(job_seconds) | 11.04→10.88초 | 10.01→9.99초 |
| PyTorch peak allocated | 270,223,360바이트, 동일 | 270,223,360바이트, 동일 |

전체 실행은 23.34초였고 승인된 2회는 모두 사용했다. Gemini·임베딩 호출은 0회다. 객체·문맥·클래스·설정도 이전과 같다. 달라진 JSON 값은 실행시간, 입력 보관 파일명, 출력 절대 경로이며 이에 따른 manifest 해시 변경을 검증했다.

이번 판정은 이 한 영상의 배포 재현에 한정한다. 독립적인 정확도나 속도 개선을 확인한 결과는 아니다. 원본 근거: [비교 JSON](../../artifacts/runpod/image-p4-20261001-01/reproduction-comparison-01.json), [회수 검증](../../artifacts/runpod/image-p4-20261001-01/retrieval-verification-01.json). 다음 TODO는 P5 Gemini 외부 설정 분리다.

## 현재 완료한 결과

- 로컬 이미지 `cctv-runpod-ab:p4-v1`: linux/amd64. inspect Size는10,901,232,296bytes, image ls는31.2GB를 표시했다. 서로 다른 크기 필드이며10.90GB를 전체 로컬 디스크 사용량으로 읽지 않는다.
- 로컬/원격 index digest `sha256:6cd6ee2e3f3e13a7f5d2bea5594db8206d4901f518ed3e73e6eaf4b4a2af55cb` 일치. Hub `dahyeguk/cctv-runpod-ab:p4-v1` 태그의 linux/amd64 manifest도 대조했다. Public은 사용자 확인 근거다. `registry-publication-01.json` 참조.
- 첫 빌드674.142초, Python3.12.3/고정14패키지와 A/B53파일 해시 대조 통과. H.264/mp4v/PNG/JPEG 합성 저장·디코딩 통과.
- 완성 이미지의 네트워크 차단·읽기 전용 컨테이너에서 inspect/check-environment/빌드 영수증 회수 통과. 모델 객체 생성·가중치 로드·추론0.
- 근거: `artifacts/runpod/image-p4-20261001-01/container-verification.json`, `build-verification.json`, `build-attempt-01.json` 및 원본 로그.
- Runpod 새 이미지의 파일/환경·터미널 접속·A4500 장치 조회까지 통과했다. 아래1~5의 통과한 검사는 반복하지 않는다. 승인된2회 실행·원본회수·동일버전비교도 위와 같이 통과했다.

## 구성과 범위

- `/opt/cctv-image/sources/`: 기존 A/B 코드·가중치를 각 원본 경로/manifest 그대로 포함한다. 원본 ZIP42파일+각 manifest2개를 검증한 뒤 복사한다.
- `/opt/cctv-image/runtime/`: P3에서 실제 사용한 job.py/runner.py와 이미지용 실행 제어기. GPU 분석·근거 생성 로직은 재사용한다.
- `/workspace/`: 새 입력과 실행 기록·결과. 이미지에 기존 영상·spool·결과·비밀을 넣지 않는다.
- 기본 시작 `pod`: 파일/환경 확인 후 베이스 이미지의 `/start.sh`를 실행해 기존 SSH/Jupyter 연결을 유지한다. 분석 API·GPU 분석은 시작하지 않는다.
- `inspect`: 바이트/해시·A/B 설정 확인. `check-environment`: Python/배포 패키지 버전 확인. 둘 다 모델 라이브러리를 import하지 않는다.
- `run --version A|B`: 별도로 확정한 유한 실행 계획이 있을 때만 해당 버전 실행. 완료 결과 재사용, 실패/불확실 자동 재시도 금지, 공유 결과 루트에서 GPU 슬롯1 유지.

이 이미지는 첫 검증 대상인 **RTX A4500×1** 계약을 유지한다. GPU 교체 지원·HTTP/S3·BE·Streamlit 선택 연결은 이번 이미지 준비만으로 완료되지 않는다. 서비스 연결은 P6이다. P3의 소진한20회 계약을 다시 실행하지 않는다.

## 환경 선택

베이스: `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`의 조회된 `linux/amd64` manifest digest `sha256:4d1721e62b56d345c83b4fd6090664be6daf9312caab5b2e76f23d8231941851`.

Python3.12.3, torch2.8.0+cu128/torchvision0.23.0+cu128, 원격에서 확인한 주요 분석 패키지와 기존 A 설치 요구값을 고정한다. 가상환경은 베이스 PyTorch를 재사용한다. 현재 Pod의 정확한 베이스 digest와 전체 의존성 freeze는 미수신이므로 이전 환경과 모든 패키지가 같다고 주장하지 않는다. 빌드된 모든 실제 배포 버전은 `build-verification.json`에 기록하고 최종 이미지는 digest로 고정한다.

기존 setup.sh는 CUDA GPU가 필요한 검사까지 하므로 Docker 빌드에서 그대로 실행하지 않는다. 빌드 검사는 모델/가중치를 로드하지 않고 라이브러리 import 및 작은 합성 H.264/mp4v/PNG/JPEG 저장·디코딩을 확인한다. 실제 CUDA·가중치·입력/근거 재현은 이후 Runpod 검사다. B의 mp4v 브라우저 재생 검증과도 구분한다.

## 단계별 명령

아래1~3은 Mac에서 수행한 검사·빌드 절차의 기록이다. 첫 빌드는 베이스 다운로드 약10.57GB를 포함해674.142초가 걸렸으며 재빌드하지 않는다. 4단계 이후는 앞 단계의 실제 통과 결과를 확인한 뒤 사용자가 실행한다. 원격 설치·게시·Pod 변경은 사용자가 진행한다.

### 1. Mac — Docker 준비 확인

Docker Desktop을 켠 뒤 실행한다.

```bash
docker version --format '{{.Client.Version}} {{.Server.Version}}'
```

예상: 클라이언트와 서버 버전 두 값. 서버 연결 오류이면 아직 빌드하지 않는다.

### 2. Mac — 준비된 context를 이미지로 빌드

해당 context의 preparation.json/검증 기록을 확인한 뒤 실행한다. build 메타데이터 파일이 이미 있으면 덮어쓰지 않는다.

```bash
cd .
test ! -e artifacts/runpod/image-p4-20261001-01/build-metadata.json &&
docker buildx build --builder desktop-linux --platform linux/amd64 --load \
  --tag cctv-runpod-ab:p4-v1 \
  --metadata-file artifacts/runpod/image-p4-20261001-01/build-metadata.json \
  artifacts/runpod/image-p4-20261001-01/context
```

예상: runtime의 파일/환경 검사, `image_import_and_synthetic_codec_checks_passed`, 이미지 빌드 성공. 모델 객체 생성/가중치 로드/추론0. Dockerfile 파일 존재만으로 성공 처리하지 않는다.

### 3. Mac — 실제 빌드 산출물 확인

```bash
docker image inspect cctv-runpod-ab:p4-v1 \
  --format '{{.Os}}/{{.Architecture}} {{.Id}}'
docker run --rm --platform linux/amd64 --network none \
  cctv-runpod-ab:p4-v1 inspect
docker run --rm --platform linux/amd64 --network none \
  cctv-runpod-ab:p4-v1 check-environment
docker run --rm --platform linux/amd64 --network none \
  --entrypoint cat cctv-runpod-ab:p4-v1 /opt/cctv-image/build-verification.json
```

예상: linux/amd64·이미지ID, manifest/환경 검사 성공, 실제 빌드된 패키지·합성 코덱 검사 기록. Mac GPU 모델 연산은 없다.

### 4. Mac — 사용자 저장소에 게시 (완료 기록)

사용자가 Public 저장소 `dahyeguk/cctv-runpod-ab`에 게시했다. 앞서 제안한 Private과 사용자 실제 선택을 구분한다. Mac에 Hub용 태그를 추가하고 기존 이미지 ID·linux/amd64를 대조한 뒤 사용자가 아래 명령을 실행했다. 다시 push할 필요가 없다.

```bash
docker --context desktop-linux push dahyeguk/cctv-runpod-ab:p4-v1
```

수신 결과: `p4-v1: digest: sha256:6cd6ee2e3f3e13a7f5d2bea5594db8206d4901f518ed3e73e6eaf4b4a2af55cb size: 856`. 에이전트가 원격 digest와 태그의 index를 읽어 linux/amd64 실행 manifest도 대조했다. 게시 단계 통과, 근거는 `artifacts/runpod/image-p4-20261001-01/registry-publication-01.json`이다. size856은 index JSON 크기이며 전체 이미지 크기가 아니다.

이전 insufficient_scope 오류 후 namespace 태그를 준비했고 이번 push는 성공했다. 최초 실패 명령/인증 계정은 미수신이므로 최초 원인을 소급 확정하지 않는다. 토큰/비밀번호를 읽거나 기록하지 않았다. [공식 태그·push 절차](https://docs.docker.com/docker-hub/repos/manage/hub-images/push/).

### 5. Runpod — 적용 전 저장소 확인 → 이미지 기동 → 모델 없는 검사

사용자가 기존 A4500 Pod 실행 중임을 확인했다. 우선 같은 Pod 재사용을 검토한다. 이전 `/workspace`가 `/` overlay였으므로 [Pod 변경 시 임시 데이터 초기화](https://docs.runpod.io/pods/manage-pods)를 고려해 현재 보존 범위를 확인한다. 이전 P3 결과는 Mac 회수·해시 대조 완료지만 추가 보존 파일은 미확인이다.

**완료한 조회 위치: 현재 A4500 Runpod 터미널.** 아래 명령의 결과를 받았으므로 다시 실행할 필요는 없다.

```bash
df -h / /workspace
findmnt -T /workspace -o TARGET,FSTYPE
```

수신 결과: `/`·`/workspace` 모두 동일 `/` overlay30G/사용1.4G/여유29G/5%다. 별도 보존 볼륨으로 취급하지 않는다. 두 줄은 동일 파일시스템을 조회한 결과이며 다운로드가 아니다. 근거: pod-storage-user-result-01.json. Mac의 기존 결과203파일은 해제 폴더에서 manifest 크기/SHA 재대조 통과(local-recovery-check-01.json). 이전 ZIP 경로는 현재 없지만 검증된 해제 결과는 보관돼 있다.

**Edit Pod 화면 확인 완료:** 기본PyTorch 이미지·Container Disk30GB·HTTP8888,8000/TCP22·시작명령 추가 버튼·환경변수 접힘을 확인했다. 화면에 볼륨 항목은 없고 실제workspace도overlay다. Mac의 기존 결과203파일 보존을 확인했다. 사용자에게 받은 기존 자료 이외 추가 Pod 파일 전체의 목록은 미확인이며, 교체는 기존 임시파일을 보존하는 동작이 아니다.

**사용자 적용 기록: Runpod 웹 콘솔의 Edit Pod.** Container image 변경을 안내했고, 이후 아래 고정 digest의 실제 pull 완료·컨테이너 시작 요청 로그를 수신했다. 다시 Save하지 않는다. 안내한 설정은 아래와 같다.

| 항목 | 적용값 |
|---|---|
| Container disk | 현재30GB 유지. 실제 새 이미지 기동 후 여유 확인 |
| Start command | 추가하지 않고 이미지 기본 CMD `pod` 유지 |
| Registry authentication | Public이므로 추가하지 않음 |
| HTTP / TCP | 현재8888,8000 / 22 유지 |
| Environment variables | 기존값 유지, 이번 P4에 Gemini/AWS 키 추가 없음 |

사용자 로그에서 고정 digest 다운로드 완료와 `start container … begin`을 확인했다(`pod-pull-user-result-01.json`). **다음 사용자 작업: 해당 Pod → Logs → Container Logs 확인.** 내부 기동 성공은 아직 미검증이다. 로그에서 `cctv_image_ready; inference_jobs_started=0; service_API_not_started`와 뒤의 `Start script(s) finished, Pod is ready to use.`를 확인한 후 실제 Jupyter/SSH 접속까지 확인한다. 첫 문구는 베이스서비스 시작 전 출력이므로 그것만으로 성공 판정하지 않는다. 치명 오류가 있으면 같은 변경을 반복하기 전에 그 오류를 확인한다. 근거: `pod-edit-preparation-01.json`.


적용할 고정 이미지:

```text
dahyeguk/cctv-runpod-ab@sha256:6cd6ee2e3f3e13a7f5d2bea5594db8206d4901f518ed3e73e6eaf4b4a2af55cb
```

- GPU: 현재 계약인 RTX A4500×1 유지.
- Entrypoint/시작 명령: 이미지 기본 `/opt/cctv-image/entrypoint.sh`와 `pod` 유지. 기존 수동 설치 폴더의 `start.sh` 명령으로 덮어쓰지 않는다.
- 코드: `/opt/cctv-image`; 입력/실행 상태/결과: `/workspace`, 고정 상태 루트 `/workspace/cctv-image-state`. 현재 별도 보존 볼륨은 없고 용량은 유지한다. 이번 기동 단계에서는 분석을 실행하지 않으며 실제 GPU 실행 전에 상태/결과 보존 방식을 확정한다.
- 연결: 기존 관리 접속 설정을 대조한다. 베이스 스크립트는 PUBLIC_KEY가 있으면 SSH를, JUPYTER_PASSWORD가 있으면8888 Jupyter를 시작한다. 실제 값은 열람하지 않았으며 서비스 기동/접속은 새 Pod 환경에서 확인한다. 현재 P4는 분석 API8000을 시작하지 않는다.
- Public 이미지의 pull에는 개인 레지스트리 인증을 추가할 필요가 없다. 이번 오프라인 검사에는 Gemini/AWS 키를 추가하지 않는다.

현재 새 컨테이너에서 파일53개/manifest·Python3.12.3/14패키지 검사와 nginx/sshd 시작 로그가 확인됐다. `pod-startup-user-result-01.json`에 로컬 영수증과 대조 결과를 기록했다. 이전 컨테이너 ID의 No such container는 교체 전 조회 잔재로 추정하며 실제 접속으로 확인한다. SSH 지문 원문은 기록하지 않는다. inspect의 gpu 필드는 manifest 계약이며 실제 GPU 결과가 아니다. 파일/패키지 검사를 다시 실행할 필요 없다.

사용자 새 터미널 출력으로 접속·A4500 한 행/20470MiB/580.126.20 확인 완료. `/`와 `/workspace`는 같은 overlay/30G/사용16M/여유30G/1%다. 근거: `pod-device-user-result-01.json`. 위 파일/환경 검사와 장치/디스크 조회를 반복하지 않는다. 모델 추론은 미검증이다.

### 6. Runpod — 새 상한을 확정한 A/B 실제 재현

사용자 “그러자 2회 가자”로 기존 input_001(7초) A/B 각1회 총2회, 건당300초·전체600초, 현재 A4500 Pod/GPU1·Gemini/임베딩/자동재시도0을 승인했다. 새 `validation-plan.approved-01.json`은 approved=true이며 기존 proposed/pending은 보존했다. 전체600초는 첫 실행부터 A/B 사이 대기를 포함하고 Pod을 자동 정지하지 않는다. `cctv-image-validation-20261001-01.zip`에 입력/계획/launch.sh/SHA256SUMS 4파일을 준비했으며 ZIP CRC/바이트·계획 스키마·로컬대역5상황 통과, 이후 사용자 ZIP SHA 검사 OK를 받아 전송은 통과했다. 후속 사용자 해제·파일SHA검사와 A/B 각1회 completed/event1·completed_plan/attempts2/API0 출력까지 수신했다. 승인2회는 소진됐고 새 결과 회수·재현비교가 남아 있다. 전송 크기/해시는 validation-transfer-preparation-01.json 참조.

해제와 실행을 연결한 명령은 `artifacts/runpod/image-p4-20261001-01/extract_and_launch-01.py`에 보존한다. Mac 임시폴더/가짜실행으로3검사 통과했으며 사용자는 현재 Runpod에서 해당 inline 명령을 실행한다. 정상 완료의 런너 출력은 completed_plan/attempts2이며 근거/기존결과 대조와 회수는 별도로 확인한다.

실행 스크립트는 현재 컨테이너 확인·파일 해시 대조 후 단회 시작 표시를 먼저 남기고 기존 image_runtime을 A→B로 호출한다. 첫 실패면 B를 시작하지 않는다. 시작 표시나 기존 실행기록이 있으면 그대로 보존하고 재실행을 막으므로 출력이 불확실할 때 스크립트를 반복하거나 표시를 지우지 않는다.

현재 overlay에서 고정 경로만으로 Pod 교체 후 상태가 보존되지는 않는다. 이번 단회 검증은 같은 컨테이너/고정 상태 경로 유지, 입력·계획 Mac 보관, 변경/Stop 전 새 plan/state/log/output 전체 Mac 회수·해시검증을 조건으로 제안한다. 상태가 사라지거나 실행 여부가 불확실하면 재접수하지 않고 멈춰 확인한다. 별도 영속 볼륨을 연결하거나 운영용 내구성을 검증한 것은 아니다.

확정된 계획은 입력 SHA·A/B·이미지 manifest SHA와 묶고, 같은 `/workspace/cctv-image-state`를 사용한다. 선택한 버전의 자식 프로세스가 모델을 실행하며 나머지 단계에서 모델을 부르지 않는다. 완료/후보없음/실패/시간초과/불확실을 별도로 기록한다. 성공 결과는 저장·해시·미디어·기존 후보/점수/설정 대조까지 확인하고 P4 통과 여부를 판단한다. 이미지 환경 변화가 있어 결과가 다르면 차이를 기록하고 다음 단계로 넘기지 않는다.

### 7. Runpod — 완료 결과 내보내기와 Mac 회수

`artifacts/runpod/image-p4-20261001-01/export-validation-results-01.py`는 기존 runner의 내보내기 함수만 사용한다. helper SHA/승인계획/완료상태/2회·API0/저장입력·결과 해시 확인 후 새plan ID의 전체 plan/state/log/input/output을 ZIP으로 보관한다. 모듈의 TRIAL만 이번ID로 지정하며 이미지/원본파일은 변경하지 않는다. 명령 출력의 archive·bytes·sha256·files를 기록하고 Mac 다운로드 결과를 대조한다. 실제 Runpod export는 사용자 출력으로 확인했다: 원본23파일/13,935,950바이트/모델0, ZIP `image-validation-20261001-01-results-932f8bdc1eeff8d0.zip`, SHA d39b2333cac3a843a06e58c47fa08d55b039d39a316e9f3d02f59e68d7e81b64. Mac `artifacts/runpod/image-p4-20261001-01/received/` 다운로드·전체SHA/내부원본·이전결과 대조까지 완료했다.

동일ZIP이 이미 있으면 보존하며 중단하므로 상태·ZIP을 삭제하거나 추론을 재실행하지 않는다. Mac 회수까지 Pod의 임시 저장소를 유지한다. 로컬3검사는 기존P3파일을 복사한 대역이고 새2회 결과 검증과 구분한다. 이후 버전별 기존대비 입력/설정·창 점수·후보·객체/문맥·미디어·시간/메모리 차이를 기록한다.

## 근거와 한계

- 고정 입력/조건: 기존 P2 계약, P3 원본20개 결과와 `analysis-summary.json`.
- 같은 모델 파일·코드를 유지했고 새 이미지의 라이브러리 버전·합성 코덱 검사는 통과했다. 실제 A4500 장치 인식과 지정한7초영상의추론재현은확인했다. 과거Pod전체의존성의완전동일성이나다른입력·GPU재현은미확인이다.
- 저장 결과는 Pod 외부로 회수·해시 확인해야 한다. 이미지에는 새 결과가 자동 저장되지 않는다.
- 기본 A가 정확하다는 뜻은 아니다. B의 당사자/클래스/ROI 한계와 학습 노출 자료의 평가 한계를 유지한다.

공식 참고: [Runpod 커스텀 템플릿](https://docs.runpod.io/pods/templates/create-custom-template), [Docker 플랫폼 지정](https://docs.docker.com/build/building/multi-platform/), [베이스 digest 고정](https://docs.docker.com/build/building/best-practices/).
