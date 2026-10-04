# 작은 로컬 모델 연산 점검

2026-09-21 후속: 승인된실제사고2·정상3구간과공식KINETICS400_V1가중치로MPS/head5epoch학습·저장·재개·재로딩을완료했다. [실제 학습측정](training.md#2026-09-21--첫-실제-자료-제한-학습-완료-iid-참고학습-전용)은전체23.35초/루프16.90초/RSS846.89MiB/MPSdriver관측최대1090.64MiB다. 아래C-1난수/임시가중치점검과구분하며독립 검증·전체층실제 학습·장기간안정성은확인하지않았다.

## 2026-09-20 — C-1 v2의 224픽셀·분류층 연산/비용 점검

**완료:** 실제 Apple M1 Max / 64 GiB / macOS 네이티브 MPS에서 R3D-18 테스트 입력으로 분류층 업데이트 1회와 준비 실행을 제외한 eval 순전파 3회를 확인했다. 기존 112픽셀 전체 층 점검과 별도 조건이며, 실제 사고 학습이나 모델 성능 평가는 아니다.

### 입력 → 처리 → 출력과 완료 기준

- 입력: seed로 만든 uint8 테스트 픽셀 `[16,224,224,3]`, 기존 `tensor_from_array`로 정규화한 `[1,3,16,224,224]` float32. 정답 인덱스 0은 임의 연산용이며 실제 정상 영상 라벨이 아니다. ACCIDENT 실제/합성 영상 모두 읽지 않는다.
- 모델: `r3d_18(weights=None, num_classes=2)`, 임의 초기화. 전체 33,167,298개 중 `fc.weight`, `fc.bias`의 **1,026개 파라미터만** 학습 가능하다. SGD lr=0.001, momentum=0.9, batch=1, num_workers=0, CPU threads=4.
- 처리: eval/no-grad 준비 순전파 1회(업데이트 0) → head 순전파/손실/역전파/업데이트 1회 → eval/no-grad 순전파 3회 → 상태 비교. 총 순전파 5회·가중치 업데이트 1회, 전체 작업 프로세스 180초 상한.
- 출력: 새 결과 폴더의 `config.json`, `progress.json`, `report.json`, `run.json`, `worker.log`. 모델·checkpoint·영상은 저장하지 않는다.
- 완료 기준: 실제 입출력/모델 장치 mps:0, 유한 연산/gradient, fc 가중치·편향 변화, 전체 고정 상태와 BatchNorm 통계 보존, 워밍업/학습/eval 시간 분리, 메모리 측정 범위 명시. **모두 통과했다.**

기존 `check_local_model.py`, `local_model_check.json`, `training.py`는 변경하지 않았다. 새로운 제한 프로필 `configs/local_model_cost_check.json`, 실행기 `scripts/check_local_model_cost.py`, 계측 모듈 `src/boram3_cctv/model_cost_check.py`, 테스트 `tests/test_local_model_cost.py`를 추가했다. v2의 최초 제안 112픽셀과 달리 현재 C-2 입력 크기인 224픽셀·head 조건을 확인한다. 테스트 배열은 이미 224픽셀이므로 영상 디코딩/letterbox 비용은 측정하지 않는다.

### 실제 명령과 실패/성공 이력

```bash
PYTORCH_ENABLE_MPS_FALLBACK=0 .venv-model-check/bin/python scripts/check_local_model_cost.py \
  --device mps --output artifacts/model-check/cost-head224-sandbox-01

PYTORCH_ENABLE_MPS_FALLBACK=0 .venv-model-check/bin/python scripts/check_local_model_cost.py \
  --device mps --output artifacts/model-check/cost-head224-mps-01

.venv-model-check/bin/python -m unittest discover -s tests -p 'test_local_model*.py' -v
```

첫 명령은 제한 환경에서 종료 2, `mps_unavailable_no_cpu_fallback`, 모델 장치 null·업데이트 0으로 끝났다. 이를 MPS 모델 연산 실패나 정상 예측으로 바꾸지 않았다. 같은 Mac의 네이티브 실행 권한으로 두 번째 명령을 수행해 **종료 0 / passed / mps:0 / 업데이트 1 / eval 3**을 확인했다. 동일 코드·설정이며 첫 실패 로그는 보존했다. CPU로 자동 전환하거나 CPU의 실제 R3D-18 시험을 추가하지 않았다.

이번에 직접 확인한 환경: macOS 26.6.2 / arm64 / Apple M1 Max / 물리 메모리 68,719,476,736 bytes / 가상환경 Python 3.13.2 / torch 2.10.0 / torchvision 0.25.0 / PyAV 16.1.0. MPS built/available=true, CUDA build=null. 메모리 watermark 환경변수는 미설정으로 기본 보호 유지, fallback=0이다. 새 설치·가중치 다운로드·클라우드 사용은 없다.

재실행은 `--output`에 새 폴더명을 사용한다. 기존 출력이나 원본 폴더는 거부한다. 명령을 다시 실행하면 새 테스트 연산이 발생한다. 결과만 보려면 아래 읽기 명령을 사용한다.

```bash
.venv-model-check/bin/python -m json.tool artifacts/model-check/cost-head224-mps-01/report.json
.venv-model-check/bin/python -m json.tool artifacts/model-check/cost-head224-mps-01/run.json
```

### 시간 — 이번 한 번 실행의 실측

각 모델 계산은 시작 전에 이전 MPS 작업이 끝날 때까지, 종료 시 해당 계산이 끝날 때까지 동기화한다. 오류/유한값 확인·메모리 조회·상태 해시 전송 비용은 별도로 구분한다.

| 측정 | 초 | 범위/주의 |
|---|---:|---|
| 외부 실행 계측 `worker_wall_seconds` | 4.622336 | 프로세스 시작/종료와 종료 후 코드 해시·보고서 확인 포함. 출력 폴더 예약/초기 코드 해시 제외 |
| 작업 내부 전체 | 4.190934 | import·준비·모델 계산·검증 포함, 최종 보고서 쓰기 전 |
| import | 1.318317 | 기존 설치 라이브러리 로딩 |
| 테스트 입력 준비 | 0.015510 | 난수 픽셀·기존 정규화·로더 읽기, 영상 읽기 아님 |
| 임시 모델 초기화 | 0.439899 | 공식 가중치 파일 로딩/다운로드 아님 |
| MPS 전송 | 0.104929 | 모델·입력·정답 전송과 동기화 |
| 준비 순전파 1회 | 0.607899 | eval/no-grad, 업데이트 0, 학습/eval 측정에서 제외 |
| 학습 순전파 | 0.327069 | head 학습 범위 |
| 손실 계산 | 0.016215 | 연산용 임시 클래스 정답 |
| 역전파 | 0.014966 | head만. 첫 역전파는 워밍업하지 않은 값 |
| optimizer update | 0.000867 | 실제 업데이트 1회 |
| 위 학습 계산 합계 | 0.359118 | 단계별 동기화 포함, 검증·진행 기록 비용 제외 |
| 학습 step 검증 포함 | 0.396351 | 해당 step의 확인/진행 기록/메모리 조회 일부 포함 |
| eval 3회 | 0.325867 / 0.325913 / 0.324956 | 입력은 장치에 이미 있음. 순전파+동기화, 후속 유한값/메모리 확인 제외 |
| eval 중앙값 | 0.325867 | 표본 3개. 안정적인 P95·처리량 추정은 하지 않음 |
| 상태 지문 생성 / 최종 비교 | 0.118916 / 0.136698 | 고정 상태의 CPU 전송·해시 등 진단 비용 |

준비 실행은 순전파만 했으며 첫 역전파/optimizer의 초기 비용을 제거한 장기 학습 속도가 아니다. CPU 캐시·GPU 상태 등에 영향을 받는 단일 실행이다. 기존 112픽셀·전체 층 결과와 속도 개선 비교를 하지 않는다. 디코딩·실제 전처리·저장 포함 CCTV 처리 시간, RTF, 사고 탐지 지연, 본학습 소요시간은 미측정이다.

### 실제 상태·메모리·출력 확인

- `fc.weight` 변화 최대 절댓값 0.0019024094, `fc.bias` 0.0004683985. 두 gradient 텐서가 유한하고 고정 파라미터 gradient는 없다.
- fc 이외 모든 state의 SHA-256 실행 전후 일치. BatchNorm의 running mean/variance/count를 포함한 **60개 버퍼**의 해시도 일치한다. 고정 설정뿐 아니라 실제 유지 여부를 확인했다.
- 프로세스 최고 RSS: **604,946,432 bytes = 576.921875 MiB**. `getrusage`의 high-water mark로 import·CPU 입력·상태 해시 진단을 포함한다.
- 8개 시점에서 관찰한 MPS tensor 할당 최대: **143,953,664 bytes = 137.284912 MiB**. driver 할당 최대: **1,135,247,360 bytes = 1,082.656250 MiB**. 순간 GPU peak를 측정한 것이 아니다.
- `recommended_max_memory=55,662,788,608 bytes`는 API가 알려준 권장 작업 용량이며 이번 사용량이 아니다. 통합 메모리의 RSS/tensor/driver 값을 합산하지 않는다.
- 실제 모델·입력·출력 장치는 모두 `mps:0`이다. 성공 폴더의 출력은 총 **8,518 bytes**, 모델 파일 없음. 디코딩/프레임 추출/클라우드/웹/DB/큐 작업도 없다.

### 제한 테스트

60초 외부 상한에서 기존 C-1 안전 장치 8개 + 신규 13개 = **21개 통과 / 실패·건너뜀 0 / unittest 0.995초 / 외부 경과 2.013초**다. 테스트는 작은 CPU 모델/생성 입력과 mock이며 실제 R3D-18 MPS 실행과 별도다. 전체 테스트 스위트를 다시 실행한 결과가 아니다.

확인 항목: 지정 입력/횟수/180초 상한 강제, fallback·메모리 보호 해제 금지, 타이머의 동기화 순서, 준비 계산과 실제 업데이트 분리, fc 변화/특징·BN 유지, BN 변조 탐지, 역전파 오류/비유한값에서 업데이트 금지, MPS 불가/비Mac 기록, 출력 보호, 시간 초과 시 프로세스 그룹 종료, 중간 진단 파일 손상 시 실패 처리.

테스트 로그: `artifacts/model-check/cost-audit-20260920-01/tests-01.log`, `tests-01-run.json`. 원본/기존 코드/설정/출력 보존과 문서 링크 감사는 같은 폴더의 `verification.json`에 기록한다. 테스트는 첫 실행에 통과했다.

### 결과 파일과 다음 작업 하나

- `artifacts/model-check/cost-head224-mps-01/report.json`: 장치·가중치 출처·입력·측정 구간·상태 해시·메모리의 상세 근거.
- 같은 폴더 `run.json`: 최종 상태·180초 한도·종료 코드·실행 코드/설정 해시·마지막 진행 상태.
- `cost-head224-sandbox-01/report.json`: 제한 환경에서의 MPS 불가 및 업데이트 0 기록.
- [학습 기록](LEARNING_LOG.md): artifacts 역할, 워밍업·고정 학습·시간/메모리 해석을 쉬운 예시와 코드에 연결.

로컬에서는 확인한 224픽셀·head·batch=1 연산 조건으로 진행할 수 있다. 사전학습 가중치 실제 로딩, 실제 영상 학습/평가, 다른 입력/학습 범위, 저장/재개·장시간 안정성은 이번에 검증하지 않았다.

**다음 하나는 C-2의 실제 학습 자료 준비 점검**이다. 사람이 검수할 정상 영상과 현재 test 3개와 분리된 학습용 사고 영상의 경로·출처·공식 분할·원본/사건 관계를 받아 소수 후보와 정답/분할 명세를 확인한다. 추가 자료는 이번 연산 점검에 필요하지 않았지만 실제 학습에는 필요하다. 기존 세 test를 학습에 넣거나 미주석/사고 전 구간을 정상으로 만들지 않는다. 자료 목록을 받은 뒤 선택 수·실행 상한을 정하며 기존 소수 샘플 제한을 자동 확대하지 않는다. 완료 기준은 승인 정답과 원본/사건 그룹 및 학습/검증 분할의 근거가 연결되거나 미충족 항목이 명확히 기록되는 것이다. 이번에는 이 데이터 단계·다운로드·학습을 실행하지 않았다.

## 2026-09-18~19 최초 C-1 기록 (보존)

점검일: 2026-09-18. 실행 환경은 **로컬 우선, 필요 시 Colab**으로 변경했다.

2026-09-19 후속: 아래는 최초 C-1 실행 당시 기록이다. 이후 `my-mps-01.json`에도 같은 입력·전체 층의 1 step 성공이 저장되어 있으며, 세 구간의 사람 검수도 완료됐다. [현재 현황](current_status.md)에서 구분한다. v2의 분류층만 학습 제안, 별도 워밍업·eval 추론 시간 측정까지 이미 실행한 것으로 확대하지 않는다. 이번 문서 정리에서는 새 모델 연산을 하지 않았다.

**결론: 실제 M1 Max Mac의 MPS에서 R3D-18 전체 층의 순전파 → 손실 계산 → 역전파 → SGD 업데이트 1 step이 성공했다.** 로컬에서 작은 실험을 이어갈 연산 경로는 확인했다. 실제 사고 학습, 정확도 평가, 실데이터 정답 검증 결과는 아니다.

이번 승인 한도는 최대 3 step이며, 실제 모델 실행은 **MPS 1 step, CPU 모델 실행 0 step**이다. 테스트와 환경 조회는 추가 R3D-18 학습을 하지 않는다. 모델·가중치 다운로드, 체크포인트 저장, 전체 프레임 추출, 본격 학습·튜닝, 서비스 구현은 하지 않았다. 설치한 것은 모델을 구성하는 라이브러리다.

## 1. 실행 환경과 설치

| 항목 | 실제 확인 결과 |
|---|---|
| 실행 호스트 | 사용자 로컬 Mac, Darwin / macOS 26.6.2 / arm64. 별도 Linux·클라우드 아님 |
| 칩 | `sysctl -n machdep.cpu.brand_string` → Apple M1 Max |
| 물리 메모리 | `sysctl -n hw.memsize` → 68,719,476,736 bytes = 64 GiB |
| 전용 환경 | `.venv-model-check`, macOS 네이티브 가상환경 |
| Python | 3.13.2 |
| torch / torchvision | 2.10.0 / 0.25.0 |
| CPU 지원 | CPU 텐서 `2 + 1 = 3` 실제 성공. CPU의 R3D-18 역전파·속도는 미실행 |
| MPS 빌드 / 사용 가능 | `is_built()=true`, 네이티브 실행 `is_available()=true` |
| 실제 모델·입력·출력 장치 | 모두 `mps:0` |
| CUDA 빌드 | `null`. CUDA 패키지·Docker 설치 없음 |

처음 제한된 실행 환경에서는 sysctl 접근이 거부되고 `mps_available=false`였다. 같은 Mac에서 승인된 네이티브 실행으로 다시 조회하자 칩·메모리와 MPS가 확인됐다. 첫 결과를 지우지 않았다. 이는 당시 실행 권한에 따른 장치 접근 차이이며, 모델의 MPS 오류나 Linux 실행으로 기록하지 않는다.

- `artifacts/model-check/environment-sandbox.json`: 제한 환경 조회, 모델 실행 없음.
- `artifacts/model-check/environment-native.json`: 네이티브 환경 조회, 모델 실행 없음.
- `artifacts/model-check/mps-01.json`: 실제 MPS 연산 기록.

기존 `.venv`와 `.venv-inspection`, `pyproject.toml`, `uv.lock`은 보존했다. 새 환경에 13개 패키지를 설치했다. 주요 버전은 `scripts/requirements-model-check.txt`, 실제 전체 설치 목록은 `artifacts/model-check/installed-packages.txt`에 있다. torch/torchvision 조합은 [공식 호환 표](https://github.com/pytorch/vision#installation)를 확인해 고정했다. 최신 버전이라는 주장은 아니다.

설치 전에 종류와 규모를 안내했다. PyPI 배포 메타정보의 CPython 3.13/macOS arm64 파일 크기는 torch 약 **75.8 MiB**, torchvision 약 **1.8 MiB**였다. 보조 패키지까지 약 100 MiB는 사전 추정치이며 총 네트워크 전송량 실측은 아니다. 설치 후 `du -sk .venv-model-check`는 494,572 KiB, 약 **483 MiB**였다. 캐시를 포함한 전체 디스크 사용량은 따로 측정하지 않았다.

## 2. 무엇을 실행했나

설정 파일: `configs/local_model_check.json`. 코드: `scripts/check_local_model.py`.

| 설정 | 이번 연산 점검 |
|---|---|
| 모델·출력 | `r3d_18(weights=None, num_classes=2)` |
| 가중치 | seed 20260918의 무작위 초기화, 사전학습 없음 |
| 학습 범위 | 전체 층, 파라미터 33,167,298개 모두 `requires_grad=True` |
| 입력 | 고정 seed의 테스트 난수, `[1,3,16,112,112]` / NCTHW / float32 |
| 배치·프레임·크기 | batch_size=1, 16프레임, 112×112 |
| 로더 | DataLoader, shuffle=false, num_workers=0 |
| 임시 목표값 | `[0]`, 연산용 `test_class_0`. 실제 정상 정답이 아님 |
| 손실·업데이트 | CrossEntropyLoss, SGD, lr=0.001, momentum=0 |
| 모델 모드 | `train()`; 분류기만 실행하거나 backbone을 고정하지 않음 |
| 수행 횟수 | 1 step, 추가 warm-up 학습 없음 |
| CPU 자동 전환 | `PYTORCH_ENABLE_MPS_FALLBACK=0` |
| 메모리 보호 | high/low watermark 환경변수 미설정, 라이브러리 기본 제한 유지 |

입력과 정답은 실제 파일·시점 주석과 무관한 테스트 fixture다. 원본 CSV 값이나 사고 시각을 예측값으로 복사하지 않는다. 사고 점수·확률·정확도·혼동행렬은 출력하지 않는다. 임시 목표값 0을 기존 전처리의 `normal=0`과 연결하지 않는다.

[공식 R3D-18 API](https://docs.pytorch.org/vision/0.25/models/generated/torchvision.models.video.r3d_18.html)의 `weights=None`을 사용했으며 pretrained transforms도 적용하지 않았다. 미래의 사전학습 모델 사용은 전처리와 함께 별도 검토해야 한다.

## 3. 기존 전처리와의 차이

| 항목 | 기존 샘플 전처리 | 이번 모델 연산 점검 |
|---|---|---|
| 입력 출처 | 선택된 실제 영상 구간 | 재현 가능한 난수 텐서 |
| 공간 크기 | 224×224, 전체 화면 비율 유지·검은 여백 | 112×112의 생성 입력 |
| 프레임 수 | PTS로 고른 16개 | 연산 형태 확인용 16개 |
| 중간 배열 | 승인 시 THWC uint8 RGB 0~255 | NCTHW float32 0~1 |
| 시간·정답 연결 | 원본 ID·시각·승인 근거 저장 | 실제 영상 ID·정답 연결 없음 |
| 정규화 | 모델별 정규화 미적용 | 데이터셋 평균/표준편차 미적용 |

112픽셀은 작은 연산 점검의 출발점이며, 실제 사고 장면에 충분한 해상도로 결정한 것이 아니다. 기존 설정과 미리보기는 바꾸지 않았다. **실제 224픽셀 출력 → 112픽셀 모델 입력의 변환과 실제 정답 로딩은 이번에 구현·실행하지 않았다.** 그 연결을 만들 때는 축·범위 변환, 시간순서, 작은 충돌 장면 보존을 다시 확인해야 한다.

## 4. 실측 결과

실제 JSON 일부:

```json
{
  "status": "passed",
  "actual_model_device": "mps:0",
  "completed_steps": 1,
  "optimizer_updates": 1,
  "elapsed_seconds_including_imports": 7.655709874990862,
  "real_accident_training": false,
  "performance_evaluation": false,
  "pretrained_weights_downloaded": false
}
```

| 항목 | 측정값 / 확인 결과 |
|---|---|
| 전체 실행 시간 | 약 7.656초, imports·환경 조회·초기화 포함. 셸 시작 비용 제외 |
| 1 step 시간 | 약 5.909초, 동기화·검증 비용 포함 |
| 순전파 / 역전파 | 약 2.286초 / 1.577초 |
| 손실 | 0.5747075, 유한값. 테스트 입력의 계산 결과이며 성능 지표 아님 |
| gradient | 학습 대상 파라미터 텐서 62개 모두 gradient 존재·유한값 확인 |
| 첫 convolution 가중치 변화 | 최대 절대 변화 약 0.000096963 |
| 마지막 fc 가중치 변화 | 최대 절대 변화 약 0.000424829 |
| MPS 오류 / CPU fallback | 없음 / 없음 |

시간은 MPS 동기화로 대기 작업 완료를 반영했다. 최초 실행의 커널 준비 비용과 추가 검증 비용이 포함된다. 한 번의 결과로 지속 처리 속도, epoch 시간 또는 CPU 대비 배속을 추정하지 않는다.

| 메모리 항목 | 값 | 의미·한계 |
|---|---|---|
| 프로세스 최대 RSS | 약 567.47 MiB | `ru_maxrss` 실측. 프로세스의 고수위 값 |
| MPS 현재 텐서 할당량, 관찰 시점 중 최대 | 약 287.69 MiB | 단계 끝의 snapshot. 실제 GPU 순간 최대값 아님 |
| MPS 드라이버 할당량, 관찰 시점 중 최대 | 약 1,141.70 MiB | allocator/driver가 보고한 값. 시스템 전체 사용량 아님 |
| MPS 권장 최대 작업 메모리 | 약 51.84 GiB | API가 반환한 권장값. 이번에 사용한 양이나 가용 여유 메모리가 아님 |
| 모델 파라미터 저장량 | 약 126.52 MiB | 원소 수×float32 4바이트로 계산. 학습 전체 메모리 추정치가 아님 |

위 값은 [torch.mps 메모리 API](https://docs.pytorch.org/docs/2.10/mps.html)와 프로세스 통계로 기록했다. 통합 메모리 환경에서 RSS·MPS 수치를 더해 총 사용량이라고 보고하지 않는다. 시스템 전체 메모리 압박·swap·장시간 안정성은 측정하지 않았다. [MPS 환경변수 설명](https://docs.pytorch.org/docs/2.10/mps_environment_variables.html)에 따라 메모리 제한 해제나 CPU fallback 설정을 사용하지 않았다.

## 5. 재실행 명령

프로젝트 루트에서 실행한다. 현재 Mac에는 설치를 완료했으므로 환경을 다시 만들 필요가 없다. 새 Mac에 준비할 경우에만:

```bash
UV_CACHE_DIR=/private/tmp/boram3-model-uv-cache uv venv .venv-model-check --python .venv-inspection/bin/python --no-project
UV_CACHE_DIR=/private/tmp/boram3-model-uv-cache uv pip install --python .venv-model-check/bin/python -r scripts/requirements-model-check.txt
```

이는 기존 검사 환경의 Python을 사용하는 프로젝트 기준 명령이다. 다른 Mac에 그 환경이 없다면 설치된 macOS arm64 Python 3.13을 지정한다. CUDA index나 Docker 환경을 사용하지 않는다.

실제 실행한 명령:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=0 .venv-model-check/bin/python scripts/check_local_model.py --device mps --steps 1 --output artifacts/model-check/mps-01.json
.venv-inspection/bin/python -m unittest discover -s tests -v
```

직접 재실행할 때는 기존 결과를 보존하도록 **새 이름**을 사용한다:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=0 .venv-model-check/bin/python scripts/check_local_model.py \
  --device mps --steps 1 --output artifacts/model-check/my-mps-01.json
```

실패하면 종료 코드 2와 JSON의 `stage`, `error`, `completed_steps`, `optimizer_updates`를 먼저 확인한다. MPS를 사용할 수 없다고 자동 CPU 성공으로 바뀌지 않는다. 필요할 때만 같은 설정·seed의 CPU 실행을 별도로 명시한다(이번에는 미실행):

```bash
PYTORCH_ENABLE_MPS_FALLBACK=0 .venv-model-check/bin/python scripts/check_local_model.py \
  --device cpu --steps 1 --output artifacts/model-check/my-cpu-01.json
```

단일 실행은 1~3 step으로 제한한다. 여러 명령을 실행한다면 이번 점검 목적의 전체 step 합도 3 이하로 유지한다. 스크립트는 서로 다른 과거 실행의 예산을 자동 합산하지 않는다. 워터마크 override가 환경에 있으면 실행을 거부하므로, 오류를 읽고 해당 override를 제거해 라이브러리 기본 보호값으로 실행한다. 값을 0으로 바꾸어 보호를 해제하지 않는다.

## 6. 코드·테스트·보존 확인

- 추가: 전용 requirements, 모델 점검 설정·스크립트, 안전/오류 처리 테스트 8개.
- 최종 **56개 테스트 통과, 실패·건너뜀 0**: 기존 48개 + 신규 8개. 로그: `artifacts/model-check/tests-final.log`.
- 신규 테스트는 step 한도, 입력 크기·사전학습 거부, CPU fallback 금지, 메모리 보호 override 금지, 비정상 학습률, MPS 미지원/다른 OS 처리, 출력 보호를 검사한다. R3D-18 학습을 추가 수행하지 않는다.
- 첫 테스트 실행은 macOS 임시 경로의 `/var`와 `/private/var` 비교로 1개 실패했다. 테스트 기대 경로를 `resolve()`해 수정했고 전체 재실행이 통과했다. 초기 로그도 보존했다.
- 모델 실행 이후 CLI 사전검사 거부 사유의 표시를 보완했다. 연산 경로·설정은 동일하며 JSON의 코드 해시는 실제 실행 당시 버전을 나타낸다.
- 원본 CSV·YAML·영상 3개와 기존 코드·설정·`pyproject.toml`·`uv.lock` 등 작업 전 기록한 18개 파일 해시가 그대로다.
- 기존 문서의 Colab 필수 전제만 로컬 우선으로 수정했다. 기존 단계별 승인·범위·보안·테스트 규칙은 보존했고 최신 승인과 결과는 진행 기록 상단에 덧붙였다. 과거 S0/S1 기록은 당시 이력이다.
- 데이터·가상환경·결과는 Git 제외 대상이다. 커밋·푸시·원격 공유는 하지 않았다.

## 7. 다음 완료 기준은 실제 구간과 정답의 연결

사용자가 세 원본을 사고 영상으로 확인했다고 설명했다. 이는 유용한 **영상 전체 수준의 확인**이다. 하지만 현재 후보 구간 `[7.533,9.533)`, `[2.292,4.292)`, `[12.533,14.533)` 자체의 경계·가시성과 사고 정답을 확정한 기록은 아직 없다. 원본 전체가 사고 영상이라고 모든 부분 구간을 사고로 바꾸지 않았다.

다음 가장 작은 작업은 **구간 하나를 사람이 확인해, 정확한 범위와 판정 근거를 기록하고 전처리 배열·정답·원본 ID가 같이 읽히는지 확인하는 것**이다. 현재 세 영상은 공식 두 분할 모두 test이므로 이 연결 점검과 학습 사용은 분리한다.

완료 기준:

1. 검수자가 구간 안에 사고 발생 장면이 보이는지 또는 판독 불가인지 기록한다. 범위를 바꾸면 변경한 구간을 다시 확인한다.
2. 승인된 경우 배열 1개와 target, 원본 해시·시작/끝·근거가 같은 기록으로 연결된다. 미확정이면 계속 null이다.
3. 실제 모델용 해상도·축 변환을 적용할 때도 순서·가시성·정답 연결을 확인한다. 사고 확률 출력은 완료 조건이 아니다.

실제 사고/정상 학습에는 **사람이 전체 구간을 확인한 무사고 CCTV 자료**, 현재 test 샘플과 분리된 **train 사고 자료**, 원본·사건 그룹 및 분할 근거가 더 필요하다. 사고 전 구간을 자동 정상으로 만들지 않는다. 일단 사고 1구간·정상 1구간을 준비하면 두 클래스의 로딩 경로 점검은 가능하지만 학습량·성능 검증에 충분하다는 뜻은 아니다.

아직 검증하지 않은 것: 실제 구간/정답을 모델까지 읽는 연결, 학습용 분할, 검수된 정상 자료, 사전학습 가중치와 그 전처리, 다른 해상도·배치·optimizer, 장시간 학습·저장/재개·성능·일반화. CPU의 전체 모델 학습도 미검증이다. 작은 로컬 실험은 계속 가능하며 Colab 전환은 현재 필수가 아니다.
