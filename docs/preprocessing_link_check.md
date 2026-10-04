# 승인 구간의 실제 입력·정답 연결 확인

> 2026-09-21 최신: [검수 준비 상태 확인](#검수-준비-상태만-확인하기--2026-09-21)을 기존 코드에 추가해 41개 테스트와 실제 읽기 전용 점검을 완료했다. 두 묶음 모두 답변 대기이며 기존 검수 파일은 변경하지 않았다.

> 2026-09-21 최신: [224픽셀 품질 검수 기록](#224픽셀-입력-품질-검수-기록--2026-09-21)의 코드·36개 테스트·실제 양식 생성을 완료했다. 사고/정상 정답은 보존했고 사람 식별성 응답은 아직 대기다. 원본 재디코딩·새 미리보기·학습은 없다.

> 2026-09-21 최신: [007~009의 224픽셀 입력 연결](#007009의-224픽셀-입력-연결--2026-09-21)까지 완료했다. 같은 원본·구간·PTS·정답을 유지하고 학습 설정 크기로 읽었다. 사람의 축소 후 식별성·학습 준비·모델 성능과는 구분한다. 아래 실행 기록은 보존한다.

> 2026-09-21 최신: 새007~009의 **검수본512픽셀 PNG를 그대로 재사용한 로더 연결**을 완료했다. 원본 재디코딩0/새미리보기0, 사고 정답/원본 시각/프레임 순서 일치. [이번 범위와 학습224픽셀의 차이](#새007009의-입력-품질과-검수본-재사용--2026-09-21)를 참고한다. 아래001~003/QMUL 실행을 반복한 것이 아니다.

> 최신 후속 작업: QMUL 정상 3구간도 원본 AVI에서 배열·정답을 함께 읽는 연결을 구현하고 실제 실행했다. [QMUL 결과](#qmul-외부-정상-구간-입력-연결--2026-09-20)를 참고한다. 아래 첫 ACCIDENT 결과는 기존 이력으로 보존한다. 모델 입력 형식 연결 성공과 사람의 224픽셀 식별성 승인·학습 준비 완료는 별개다.

확인일: 2026-09-20. C~E v2의 A/B 선행 조건을 확인한 뒤 **B의 남은 연결 점검 하나**를 실행했다. 세 구간 검수는 완료되어 있었고, 승인 후 배열 생성·실제 로더 읽기는 미실행이었다. 기존 전처리·학습 코드를 재구현하지 않고 검사 모듈·CLI·테스트만 추가했다.

## 입력 → 처리 → 결과물과 완료 기준

- 입력: `data/local/accident/`의 선택 영상 3개와 원본 CSV, `configs/preprocessing_samples.review-20260919.json`, 사람이 검수했던 `artifacts/preprocessing/s1-samples-02/`.
- 기존 `configs/train_r3d18.local.json`에서는 입력 변환 설정만 읽는다. 학습 데이터·device 설정을 실행하지 않는다. 명령에 지정한 검수 계획으로 배열을 만들며 기존 pending 학습 계획은 그대로다.
- 처리: 기존 PTS 선택·16프레임·224×224 letterbox → 배열 저장 → 기존 `SegmentDataset`으로 CPU 텐서·정답·ID 읽기 → 기존 PNG·PTS와 비교. 정규화는 기존 float32/255·Kinetics 채널 평균/표준편차를 유지한다.
- 결과물: 새 배열 3개, 프레임 PNG 48장, 모음 미리보기 3장, 원본 해상도 참고 이미지 3장, 시각·원본 ID·검수 근거와 검증 보고서.
- 완료 기준: 라벨 `[1,1,1]`, 배열 `[16,224,224,3]` uint8, 변환 `[3,16,224,224]` float32, 유한값·엄격히 증가하는 구간 내 시각·PNG 픽셀 일치, test·학습 미승인·원본 보존. **모두 통과했다.**

모델 생성·예측·역전파·다운로드·설치·원격 전송은 하지 않았다. 이는 전처리 입력 연결 확인이며 학습·성능 평가가 아니다. 이 세 구간 밖이나 전체 데이터의 검증 완료를 뜻하지 않는다.

## 실제 실행 명령

프로젝트 루트에서 기존 macOS 네이티브 환경을 사용했다.

```bash
.venv-model-check/bin/python scripts/check_preprocessing_link.py \
  --data-root data/local/accident \
  --plan configs/preprocessing_samples.review-20260919.json \
  --reference artifacts/preprocessing/s1-samples-02 \
  --config configs/train_r3d18.local.json \
  --output artifacts/preprocessing/reviewed-link-01
```

종료 코드 0 / `passed`. 재실행은 `--output`에 **새 폴더 이름**을 지정해야 한다. 기존 경로를 다시 쓰거나 입력과 겹치는 경로를 지정하면 거부한다.

제한은 영상별 45초, 선택 1~3개, 전체 작업 프로세스 180초다. 전체 제한에는 디코딩·저장·torch import·입력 검증이 포함되며 시작 전 보호 파일 해시와 종료 후 보고서 작성은 별도다. 시간 초과 시 디코더 자식까지 같은 프로세스 그룹을 종료하고 `needs_attention`으로 남긴다. 기존 디코더의 구간 최대 10초·900 디코딩 프레임·2,000 패킷·내부 35초 제한도 유지한다. 이번 구간은 각각 2초, 저장 프레임은 각각 16장이다.

## 실제 결과

| 샘플 | 검수한 입력 구간(클립 내 초) | 선택 첫/마지막 프레임 시각 | 정답 | 내부 디코딩 수 / 저장 수 |
|---|---|---|---:|---:|
| 001 | [7.533, 9.533) | 7.600000 / 9.466667 | 사고 1 | 143 / 16 |
| 002 | [2.292, 4.292) | 2.375000 / 4.250000 | 사고 1 | 64 / 16 |
| 003 | [12.533, 14.533) | 12.600000 / 14.466667 | 사고 1 | 183 / 16 |

`prepared_labeled=3`, `unconfirmed=0`, `error=0`.배열의 각 프레임과 신규 PNG, 사람이 검수했던 기존 PNG의 픽셀이 정확히 같다. 실제 PTS·요청 시각·공간 변환·원본 해상도 참고 이미지도 같다. 모음 그림의 제목은 미확정에서 승인 라벨 표시로 바뀌므로 모음 그림 파일 전체 해시의 동일성을 요구하지 않는다. 002 새 모음 그림의 배치·시각 표시도 열어 확인했다. 사고 정답 근거는 기존 사람 검수이며 새로운 자동 사고 판정이 아니다.

기존 `manifest.json`의 `preview_review_status=not_reviewed`는 생성기의 기본 필드로 그대로 보존한다. 새 결과의 기존 검수 출력과의 동일성은 별도 `verification.json`의 `reviewed_pixels_and_times_equal=true`로 기록한다. 검수 JSON의 `window_basis`는 최초 후보 위치를 만든 이력이며 현재 정답은 별도의 `label_review`에 근거한다. 검수 원문이나 원본 CSV는 수정하지 않았다.

실제 배열에 기존 로더의 축 변환·정규화를 적용하고 시간순서와 정답을 확인했다. 정규화 후 값 범위는 약 -1.895189~2.873648로, 0~1 범위를 벗어나는 것이 정상이다. 값 범위는 사고 확률이 아니다. 데이터 로더를 호출했지만 학습 루프는 호출하지 않았다.

## 시간·메모리·환경

모두 이번 한 번 실행의 실측이며 장시간 안정성·학습 속도·모델 추론 속도로 해석하지 않는다.

| 측정 | 값 | 범위 |
|---|---:|---|
| 작업 프로세스 경과시간 | 3.369초 | 프로세스 시작~종료. 시작 전 해시·최종 보고서 쓰기 제외 |
| 기존 전처리 호출 | 2.187초 | 세 영상 읽기·선택·배열/미리보기/manifest 저장 포함 |
| 연결 검증 | 0.947초 | torch import·배열/PNG 읽기·CPU 변환/대조 포함 |
| 연결 검사 프로세스 최고 RSS | 327,172,096 bytes (312.02 MiB) | `getrusage(RUSAGE_SELF).ru_maxrss`, 디코더 자식 제외 |
| 디코더 자식 중 최대 RSS | 305,381,376 bytes (291.23 MiB) | `RUSAGE_CHILDREN.ru_maxrss`. 동시 전체 메모리나 합계가 아님 |
| 최종 결과 폴더 | 13,761,084 bytes (13.12 MiB), 69파일 | `run.json` 포함, 별도 테스트/보존 감사 폴더 제외 |

직접 측정한 시스템은 Darwin/macOS 26.6.2/arm64, Python 3.13.2, torch 2.10.0, torchvision 0.25.0, PyAV 16.1.0, NumPy 2.2.6, Pillow 11.3.0이다. 현재 사용자 Mac의 네이티브 환경이며 M1 Max/64 GiB 하드웨어 확인은 기존 로그를 근거로 한다. 이번 실제 계산은 CPU 디코딩·입력 변환이다. MPS 연산·GPU peak는 이번에 측정하지 않았고 CPU 자동 전환이 발생한 것도 아니다.

## 결과물 보는 방법

루트: `artifacts/preprocessing/reviewed-link-01/`

| 상대 경로 | 의미 |
|---|---|
| `run.json` | 최종 성공/실패·시간 상한·환경·코드 해시·입력 보존 |
| `verification.json` | 샘플별 라벨·시각·텐서·기존 검수 출력과의 동일성, 메모리 |
| `prepared/manifest.json`, `prepared/labeled.json` | 원본 영상 ID/해시·구간·정답 근거·공식 분할과 전체 집계 |
| `prepared/labeled/sample_00N/frames.npy` | 모델 입력 변환 전 16프레임 배열. N=1,2,3 |
| `prepared/labeled/sample_00N/frames.json` | 요청 시각·실제 PTS·변환·정답 |
| `prepared/labeled/sample_00N/preview.png` | 왼쪽→오른쪽, 위→아래 순서의 16장 미리보기 |
| `prepared/labeled/sample_00N/frames/000.png`~`015.png` | 배열과 픽셀이 일치하는 개별 프레임 |
| `inputs_before.json`, `worker.*.log` | 보호 입력 해시와 실행 진단 |

터미널에서 결과를 읽거나 macOS 미리보기로 열 수 있다. 읽기만 하는 명령이다.

```bash
.venv-model-check/bin/python -m json.tool artifacts/preprocessing/reviewed-link-01/verification.json
open artifacts/preprocessing/reviewed-link-01/prepared/labeled/sample_002/preview.png
```

`run.json`과 `verification.json`의 status를 함께 확인한다. 오류가 있으면 성공 샘플이 일부 있어도 전체 완료로 취급하지 않는다.

## 테스트와 실패 기록

```bash
.venv-model-check/bin/python -m unittest discover -s tests -p 'test_preprocessing*.py' -v
```

60초 외부 상한을 두고 실행해 **35개 통과 / 실패·건너뜀 0 / unittest 3.883초 / 외부 경과 4.175초**다. 기존 전처리 23개와 신규 연결 검사 12개이며 전체 테스트 스위트를 다시 실행한 결과는 아니다. 기존 C-2의 80개 통과/9.073초 기록과 구분한다.

- 기존 23개: 짧은 영상, 누락·손상, 잘못된 시각·구간 밖·PTS, 미확정 라벨, 출력/원본 보호 등.
- 신규 12개: 실제 로더 연결, PNG 변화·프레임 역순·PTS 변화·같은 시각 중복, 누락 배열·미확정, 라벨/분할/승인 변경, 설정·경로 불일치, 기존 출력 보호, 제한 초과 프로세스 그룹 종료.
- 첫 실행 35개 중 7개 오류: 오류 상황을 만드는 테스트에서 덮어쓰기를 금지한 `write_json`으로 임시 fixture를 변경하려 했다. 임시 테스트 파일만 명시적으로 수정하도록 고쳤다. 제품의 원본/출력 보호는 완화하지 않았다.
- 로그: `artifacts/preprocessing-link-audit/20260920-01/tests-01.log`, `tests-02.log`, 각 `tests-*-run.json`. 실패 로그를 삭제하지 않았다. 실제 자료 실행은 한 번 성공했고 시간 초과·재시도는 없었다.

## 남은 조건과 다음 작업 하나

이번 완료는 선택 3구간의 B 연결 검사다. 정상 승인 0, 공식 test 3이며 모두 `training_authorized=false`다. 원본/사건/재편집 그룹은 미확인이고 별도 train/validation·공식 사전학습 가중치·실제 사고 모델도 준비되지 않았다. 실제 사고 학습과 평가 구간 수는 여전히 0이다. 전체 B의 데이터 준비 완료나 C-2 실제 학습 완료로 승격하지 않는다.

다음 하나로 **C-1 v2의 남은 비용 계측 보완**을 제안한다. 실제 C-2 입력 조건인 16×224×224·batch=1·float32·head 학습을 별도 설정으로 두고, 명시적 테스트 입력/임시 가중치로 워밍업·학습 1 update·eval 3회 시간과 메모리를 구분하는 작업이다. 기존 112픽셀 전체 층 성공 기록은 보존한다. 전체 상한 180초 제안이며 설치·다운로드 없이 진행 가능 여부를 먼저 확인한다. 이 제안은 이번에 실행하지 않았다. 사고 데이터 학습·사전학습 품질 검증은 별도다.

## QMUL 외부 정상 구간 입력 연결 — 2026-09-20

**완료:** 사용자가 정상으로 판정한 QMUL 3구간을 원본 AVI에서 읽어 기존 데이터 로더에서 정답 0과 함께 반환하는지 실제 확인했다. 이 작업은 v2 A/B 및 C-2의 자료 준비 일부다. 실제 학습·모델 예측·분할 완료는 아니다.

### 입력과 변경 파일

- 원본: 사용자가 지정한 `qmul_junction.avi` 한 파일. 원본 경로는 명령으로 받고 코드/공유 문서에 개인 절대 경로를 하드코딩하지 않는다.
- 정상 판정·순서 확인: [후속 검수 JSON](../configs/qmul_segments.review-20260920-02.json). 기존 JSON을 덮어쓰지 않고 부모 해시와 최신 발언을 추가했다. 순서 문제 없음만 새로 확인됐으며 잘림/누락·추가 문맥 필요 여부는 null로 유지한다.
- 전처리 설정: 기존 [train_r3d18.local.json](../configs/train_r3d18.local.json)의 16장/224픽셀/전체 화면+여백/최근접 PTS/채널 정규화만 사용한다. 해당 파일의 `device=mps`나 학습 계획은 실행하지 않는다. CPU 입력 변환을 명시한 검사다.
- 코드: [external_preprocessing.py](../src/boram3_cctv/external_preprocessing.py)의 `validate_review`, `prepare_one`, `verify_inputs`; [check_external_preprocessing.py](../scripts/check_external_preprocessing.py)의 `run`, `worker`, `supervise`; [테스트 17개](../tests/test_external_preprocessing.py). 기존 전처리·학습 코드는 변경하지 않았다.

### 코드 흐름과 선택 이유

검수 기록 → 원본/검토 MP4/부모 manifest 해시와 구간 대조 → 기존 `sample_frames()`로 원본 PTS 최근접 프레임 16장 선택 → `save_frames()`로 배열/PNG 저장 → `SegmentDataset`이 CPU 텐서·정답·ID 반환 → `verify_inputs()`가 PNG 픽셀·시간 순서·정규화 결과를 대조한다.

사람이 본 MP4의 모든 프레임 시각 대응표에 새로 고른 48개 원본 PTS가 들어 있는지도 검사했다. **MP4의 재압축 픽셀을 학습 입력으로 사용하지 않았고, 기존 360픽셀 요약 16장과 같은 프레임이라고 주장하지 않는다.** 기존 요약은 목표 시각 이후의 첫 프레임, 모델용 sampler는 가장 가까운 PTS라 일부 선택 시각이 다르다.

기존 `real_videos/*.mp4`+ACCIDENT CSV 진입점은 보존했다. 새 외부 진입점은 지정된 원본 한 파일과 검수 기록만 읽는다. ACCIDENT 라벨 파일에 QMUL 행을 임의로 추가하거나 전체 전처리를 새로 작성하지 않았다. 원본 픽셀·출처/시각 근거를 유지하면서 기존 모델 변환과 연결하기 위한 선택이다.

### 실제 실행과 테스트 명령

기존 `.venv-model-check`를 사용했다. 아래 원본 위치만 환경 변수로 표시했다. 사용자 지정 AVI 위치를 `QMUL_VIDEO_PATH`에 설정한다. 결과 폴더는 이미 있으므로 다시 실행할 때는 새 이름을 지정한다.

```sh
.venv-model-check/bin/python scripts/check_external_preprocessing.py \
  --source "$QMUL_VIDEO_PATH" \
  --review configs/qmul_segments.review-20260920-02.json \
  --config configs/train_r3d18.local.json \
  --output artifacts/preprocessing/qmul-reviewed-link-01

.venv-model-check/bin/python -m unittest discover -s tests -p 'test_*preprocessing*.py' -v
```

실제 CLI 출력(시간 반올림):

```json
{"status":"passed","elapsed_seconds":2.360,"inputs_unchanged":true,"training_run":false}
```

전체 CLI 상한 90초는 입력 해시·자식 디코더·torch import·검증을 포함한다. worker별 40초이며 기존 디코더의 최대 900프레임·2,000패킷·35초 제한이 추가로 있다. 시간 초과는 전체 프로세스 그룹을 종료하고 성공 보고서를 만들지 않는다. 오류/미확정 표본은 정상 0으로 바꾸지 않고 명시적으로 남긴다. 기존 출력을 덮어쓰거나 원본 옆에 출력하지 않는다.

### 실제 결과와 직접 확인

| 구간 | 원본 구간 | 저장 첫~마지막 PTS 초 | 원본 디코딩 / 저장 | 정답 / 미리보기 |
|---|---|---|---|---|
| qmul_001 | [30,36) | 30.196833~35.802433 | 325 / 16 | 정상 0 / [224픽셀 요약](../artifacts/preprocessing/qmul-reviewed-link-01/qmul_001/preview.png) |
| qmul_002 | [1500,1506) | 1500.198700~1505.804300 | 381 / 16 | 정상 0 / [224픽셀 요약](../artifacts/preprocessing/qmul-reviewed-link-01/qmul_002/preview.png) |
| qmul_003 | [2700,2706) | 2700.197500~2705.803100 | 345 / 16 | 정상 0 / [224픽셀 요약](../artifacts/preprocessing/qmul-reviewed-link-01/qmul_003/preview.png) |

세 배열은 `[16,224,224,3]` uint8, 로더 텐서는 `[3,16,224,224]` float32/CPU다. 검은 여백을 포함한 전체 화면을 모델 입력으로 사용한다. 실제 공간 변환은 360×288→224×179, 위22/아래23픽셀 여백이며 crop은 없다. 텐서로 변환할 때 색상 축 이동·255로 나누기·기존 Kinetics 채널 정규화를 적용했다. 정규화 후 값을 사고 확률로 표시하지 않는다.

- **실제 실행:** 내부 2.359673초, 외부 전체 2.637764초, 성공 3개·오류/미확정 0개, 정답 `[0,0,0]`.
- **테스트:** 기존 35+신규 17 = 52개 통과, 6.984초/외부 7.536초, 실패·건너뜀 0. 전체 스위트 재실행 아님. 시험용 색상 영상과 mock을 사용했으며 log의 `supervisor_timeout`은 의도한 오류 시험 출력이다.
- **파일/메모리 실측:** 결과 14,285,076 bytes(약13.62 MiB). 부모 최고 RSS 294,322,176 bytes(280.69 MiB, torch import·CPU 입력 검증 포함). 디코더별 최고 RSS 중 최대 91,013,120 bytes(86.80 MiB). 전체 시스템/동시 총 peak·GPU 메모리는 미측정, 합산하지 않는다.
- **환경:** 로컬 Darwin/arm64, Python 3.13.2·PyAV 16.1.0·NumPy 2.2.6·Pillow 11.3.0·torch 2.10.0. 모델/MPS 계산은 하지 않았다.
- **기계 근거:** [report.json](../artifacts/preprocessing/qmul-reviewed-link-01/report.json), [테스트 로그](../artifacts/preprocessing/external-link-audit-20260920-01/tests-01.log), [외부 시간 계측](../artifacts/preprocessing/external-link-audit-20260920-01/real-run-01.json), [보존·결과 감사](../artifacts/preprocessing/external-link-audit-20260920-01/verification.json).

구간 폴더의 `frames.npy`는 로더가 읽은 픽셀 배열, `frames.json`은 16장의 원본 시각·변환 정보, `preview.png`는 사람이 볼 그림, `provenance.json`은 원본 ID·구간·정답 근거·분할/그룹/승인 상태다. 그림의 `APPROVED SEGMENT LABEL`은 **구간 라벨 승인** 표시이며 모델 입력 식별성 승인이나 학습 허가가 아니다.

Codex가 새 요약 그림 세 장을 열어 배치·시각·원본 전체 화면 구성을 확인했다. 원본 비율을 유지해 잘라낸 부분은 없지만, 축소로 작은 차량이 흐려질 수 있다. 사람이 224픽셀에서도 판단 가능한지는 아직 미확인이다. 새 그림은 정규화 전 RGB 픽셀을 보여 주며 숫자 축/정규화만 로더에서 바뀐다.

### 아직 학습할 수 없는 이유와 다음 하나

입력/정답 연결 자체는 성공했다. 다만 QMUL은 **같은 원본 1개에서 나온 6초 구간 3개**이며 공식 분할·독립 사건 관계·시간 정보 차이는 미확인이다. 사람이 검수한 정상 정답은 유지하되 새 모델 입력의 식별성·잘림/추가 문맥 응답을 자동 완료 처리하지 않는다.

기존 사고 입력은 2초에서 16장(목표 간격0.125초), 이번 정상은 6초에서 16장(0.375초)이다. 화면 크기는 같아도 시간 간격이 다르다. **이는 검수된 구간을 보존한 연결 점검 설정이며 실제 학습의 두 클래스 입력 규칙으로 확정한 것이 아니다.** 시간 규칙을 정하고 구간을 바꾸면 필요한 라벨/입력 검수를 다시 확인한다. 현재 실제 학습용 사고 파일은 준비되지 않았고 기존 여섯 ACCIDENT 파일은 test다. 정상 하나를 여러 분할에 나눠 독립 평가 자료를 만들지 않는다.

다음 하나(당시 제안): 현재 test 파일과 별개의 **공식 train 사고 후보 최대 3개를 로컬 metadata/제공 경로와 연결해 확인**한다. 이후 진행은 아래 최신 기록과 PROGRESS를 따른다.

## 새007~009의 입력 품질과 검수본 재사용 — 2026-09-21

보고서의 `order_problem=null`은 **사람의 답변이 없었다는 뜻**이며, 실제 순서 오류가 발견됐다는 뜻이 아니다. 이전에는 16장의 원본 시각(PTS)이 증가함을 확인했다. 이번에는 아직 실행하지 않았던 새007~009의 로더 연결을 보완했다.

검수한 그림의 순서와 로더의 시간 축이 같아야 한다. 예를 들어 007의3.083초 그림이3.208초 그림보다 먼저 읽혀야 한다. 이런 기술 검사는 코드로 확인하며 사람에게 사고 여부나 배열 순서를 다시 묻지 않는다.

### 입력 → 처리 → 결과물

- 입력: 기존 사고 승인 계획, 기존512픽셀 PNG48장·미리보기·PTS 기록, [이번 사람 품질 답변](../configs/received_segments.quality-20260921.json).
- 처리: 기존 `check_preprocessing_link.py`의 재사용 모드 → `verify_saved_preview_link()`에서 검수 계획·manifest·미리보기 해시 대조 → 각 PNG와 승인 미리보기의 같은 칸 픽셀 대조 → 그대로 배열화 → 기존 `SegmentDataset`의 ID·정답·시각 검사와 축 변환·정규화 → 역변환 픽셀 대조. 기존 `approved_target()`으로 사고 정답을 연결한다.
- 결과물: `artifacts/preprocessing/received-quality-link-01/`의 보고서와 `diagnostic_arrays/sample_007~009/frames.npy,frames.json`. 원본 재디코딩·새 PNG·새 미리보기는0이다.
- 완료 기준: 검수한 **512픽셀 입력**의 ID·시각·표시/로더 순서·정답 보존. 학습 설정은224픽셀이므로 `configured_training_input_verified=false`로 구분한다. 구간·크기·학습 설정을 바꾸지 않았다.

### 실제 결과와 사람 확인

| 구간 | 승인 범위 | 실제16장 처음~끝 시각(초) | 코드 확인 | 사람 확인 |
|---|---|---|---|---|
| 007 | [3.000,5.000) | 3.083333~4.916667 | ID·정답1·PTS·표시/로더 순서 일치 | 식별 가능, 잘림·누락 없음, 추가 장면 불필요 |
| 008 | [2.583,4.583) | 2.666667~4.541667 | 동일 | 동일 |
| 009 | [4.833,6.833) | 4.900000~6.766667 | 동일 | 동일 |

배열은 `[16,512,512,3]` uint8, 로더 텐서는 `[3,16,512,512]` float32/CPU, 정답은 `[1,1,1]`이다. 기존 PNG의 전체 화면과 여백을 그대로 읽었다. 새crop·크기 변환·구간 확장은 없다. 이 진단 배열은 현재224픽셀 학습 설정의 배열을 대신하지 않는다.

사용자의 새 답변은 “세 구간 모두 잘림·누락 문제 없고 추가 장면이 필요하지 않음”이다. **이 명확한 후속 답변**으로 사람 품질 항목을 보완했다. 앞선 “없는거 같아” 원문과 기존 사고 판정·검수자 다혜·2026-09-21은 보존했다. 사람의 순서 답변을 추정해 채우지 않고 기술적인 순서 확인은 코드 결과로 따로 남겼다. 검사 실패 시에도 승인 라벨을 보존하는 오류 처리를 테스트했다.

기존 미리보기: [007](../artifacts/data-check/received-three-01/prepared/unconfirmed/sample_007/preview.png) · [008](../artifacts/data-check/received-three-01/prepared/unconfirmed/sample_008/preview.png) · [009](../artifacts/data-check/received-three-01/prepared/unconfirmed/sample_009/preview.png). 이번 답변으로 추가로 보여줄 앞뒤 구간은 필요 없다.009의 원본 주석과 프레임÷평균FPS 차이 원인은 여전히 미확인이며, 이번에는 실제 PTS 연결을 확인했다.

### 실제 실행 명령·측정

```bash
.venv-model-check/bin/python scripts/check_preprocessing_link.py \
  --data-root data/local/accident \
  --plan artifacts/label-review/received-dahye-20260921-01/reviewed_plan.json \
  --reference artifacts/data-check/received-three-01/prepared \
  --config configs/train_r3d18.local.json \
  --reuse-reviewed-frames \
  --quality-review configs/received_segments.quality-20260921.json \
  --output artifacts/preprocessing/received-quality-link-01

.venv-model-check/bin/python -m unittest discover -s tests -p 'test_preprocessing_link.py' -v
```

재실행할 경우에만 출력 폴더를 새 이름으로 지정한다. 이번 실제 실행은1회/첫 시도 성공이었다. 기존 디코딩 모드는 보존하고 새 재사용 모드에 worker60초 상한을 적용했다. 외부 실행과 테스트에도 각각60초 상한을 뒀다.

- [검증 결과](../artifacts/preprocessing/received-quality-link-01/verification.json)·[실행 기록](../artifacts/preprocessing/received-quality-link-01/run.json): 종료0/passed3. 외부 전체1.675초, worker1.528초, 검증1.291초. 로컬 Darwin/arm64·Python3.13.2·torch2.10.0에서 CPU의 PNG 읽기/텐서 변환만 실행했다. `run.json`의 공통 ‘CPU decode’ 표기는 이번 모드에서 PNG 읽기를 뜻하며 영상 디코딩은0이다.
- 프로세스 최고 RSS580,943,872bytes(554.03MiB), `getrusage(RUSAGE_SELF).ru_maxrss` 실측이다. torch import·PNG·배열·변환을 포함하며 디코더 자식은0, GPU/전체 시스템 peak는 미측정이다. 출력37,781,439bytes는 진단 배열3개와 JSON/로그다.
- [테스트 로그](../artifacts/preprocessing/input-quality-audit-20260921-01/tests-01.log): **22개 통과(기존12+추가10), 실패·건너뜀0,1.622초/외부1.930초**, 첫 시도 통과. PNG 교환·시각 변조·다른 ID·누락 PNG·변경된 미리보기·품질 미응답·512/224 구분을 검증했다. 모델 성능 시험은 아니다.

### 종료 시 준비 상태

| 대상 | 확인 완료 | 미확인 | 추가 자료 필요 |
|---|---|---|---|
| 세 구간 입력 품질 | 사고 라벨 보존,512검수본의 ID·PTS·표시/로더 순서, 사람의 식별성·잘림·누락·문맥 확인 | 실제224픽셀 입력 연결·축소 후 식별성 | 같은 구간의224검사 결과와 그 크기 확인. 새 원본 영상은 불필요 |
| 원본·사건/공식 분할 | 007 test/test,008·009 IID train/지역 test, test 노출 이력 | 사건 독립성·상위 원본 관계·평가 프로토콜 선택 | 출처·사건·재편집 관계 근거, 승인된 그룹별 분할 |
| 정상 자료 | QMUL 정상3구간/같은 원본1개, 기존224로더 연결·순서 확인 | QMUL224 식별성·잘림/추가 문맥, 독립 정상 평가 자료 | QMUL과 다른 원본의 정상 자료·출처·관계·지정 구간 검수. 이번 답변을 QMUL에 복사하지 않음 |
| 작은 기준 학습 | 학습 코드/단위 테스트·과거 C-1 연산 점검 | 실제 사고 학습·성능 평가, 두 클래스의 학습/검증 준비 | 시간 규칙(사고2초/정상6초),224입력 확인, 그룹/공식 분할, 두 클래스 목록, 공식 가중치 및 다운로드 별도 승인 |

다음 하나(제안만): **동일007~009의224픽셀 입력 연결 점검**. 같은 원본·구간·16개 PTS와 현재 학습 설정을 입력으로 받아224배열/미리보기를 만들고,512검수본 대비 시각·장면 유지를 확인한다. 사고 정답·검수자·날짜는 유지하고 축소 후 식별성만 확인한다. 최대3개·전체60초를 제안하며, 학습·구간 확장·다운로드는 포함하지 않는다. 이번에 그 단계를 실행하지 않았다.


## 007~009의 224픽셀 입력 연결 — 2026-09-21

**완료:** 기존 검수본과 같은 원본·구간·16개 프레임 시각·사고 정답을 학습 설정 크기의 입력으로 읽었다. 실제 모델을 만들거나 학습한 결과는 아니다. 512픽셀 때의 사람 식별성 확인을 224픽셀 승인으로 자동 승격하지 않았다.

### 입력 → 처리 → 결과물과 변경 이유

입력은 기존 `reviewed_plan.json`, 세 원본 영상, 512픽셀 검수 미리보기와 PTS 기록, `configs/train_r3d18.local.json`의 전처리 설정이다. 기존 `check_preprocessing_link.py`에 `--configured-size`를 추가했다. `configured_size_plan()`은 부모 기록·승인 정답·미리보기 해시를 확인하고 **크기와 점검 목적만 바꾼 사본**을 새 결과 폴더에 쓴다. 원래 계획의 사용자 응답·라벨·공식 분할·그룹 필드는 그대로 유지한다. 사본의 `human_submission`은 예전 512픽셀 검수 이력이며 새 미리보기 승인 기록이 아니다.

기존 `preprocess_samples.run()` → `sample_frames()` → `letterbox()` → `save_frames()`를 그대로 재사용했다. 원본에서 같은 시각을 다시 선택해 224픽셀로 직접 줄인다. 이미 줄인 512픽셀 PNG를 다시 축소하는 방법은 이중 리사이즈로 픽셀이 달라지므로 선택하지 않았다. 원본 한 구간 전체를 메모리에 쌓거나 모든 프레임을 저장하지 않는다.

`verify_link(..., reviewed_plan=...)`는 같은 ID·원본 해시·구간·전체 PTS 목록·정답과 공식 분할을 확인한다. 새 배열은 새 PNG·요약 그림의 같은 칸과 비교하며, 기존 `SegmentDataset`의 축 변환과 정규화 결과를 대조한다. 원본 해상도로 저장된 중간 참고 프레임도 두 실행 사이에 일치해야 하며, 그 프레임을 직접 줄인 결과와 변환 정보가 맞는지 확인한다. 크기가 다르므로 **512/224 픽셀 전체가 동일하다고 주장하지 않는다.** 기존 같은 크기 비교 모드의 엄격한 픽셀 일치 검사는 유지했다.

### 파일별 실제 결과

모든 시각은 각 MP4 시작 기준 초이며 구간은 `[시작,끝)`이다. 원본 편집 전 절대 시각이나 사고 종료 정답을 새로 알아낸 것이 아니다.

| ID / 원본 파일 | 승인 구간 | 실제 첫~마지막 PTS | 공간 변환 | 정답·코드 결과 / 사람 검수 | 공식 IID / geographic |
|---|---|---|---|---|---|
| 007 / `-_J5xdxCZZU_00.mp4` | 3.000~5.000초 | 3.083333~4.916667초 | 1280×720 → 224×126 + 위/아래 49픽셀 | 사고 1, 연결 통과 / 224 식별성 미확인 | test / test |
| 008 / `-jZ5Rlhzzi4_00.mp4` | 2.583~4.583초 | 2.666667~4.541667초 | 1080×720 → 224×149 + 위 37/아래 38픽셀 | 사고 1, 연결 통과 / 224 식별성 미확인 | train / test |
| 009 / `-yLuZ5wxAsc_00.mp4` | 4.833~6.833초 | 4.900000~6.766667초 | 1280×720 → 224×126 + 위/아래 49픽셀 | 사고 1, 연결 통과 / 224 식별성 미확인 | train / test |

세 구간 모두 16개 PTS가 기존과 일치하고 시간순이다. 다혜/2026-09-21 검수와 사고 정답은 그대로다. 512픽셀의 잘림·누락 없음/추가 장면 불필요 후속 답변도 보존했다. 세 입력의 배열은 `[16,224,224,3]` uint8, 로더 텐서는 `[3,16,224,224]` float32/CPU다. `target=1`은 사람의 사고 정답이며 모델 예측값이 아니다.

### 직접 확인할 결과물

- 224픽셀 미리보기: [007](../artifacts/preprocessing/received-input224-link-01/prepared/labeled/sample_007/preview.png), [008](../artifacts/preprocessing/received-input224-link-01/prepared/labeled/sample_008/preview.png), [009](../artifacts/preprocessing/received-input224-link-01/prepared/labeled/sample_009/preview.png). 왼쪽에서 오른쪽, 위에서 아래 순서다. 각 그림 아래 `actual`이 실제 MP4 시각이다. 검수 배너와 글자는 입력 배열에 포함되지 않는다.
- [연결 결과 verification.json](../artifacts/preprocessing/received-input224-link-01/verification.json): `status=passed`, `configured_training_input_verified=true`, `target=approved_target=1`, 원본 ID·16개 시각·크기·해시·시간·메모리. `human_visibility_status=pending_at_configured_size`, `model_input_human_approval=false`, `training_authorized=false`를 함께 읽는다.
- [전처리 manifest.json](../artifacts/preprocessing/received-input224-link-01/prepared/manifest.json): 원본 CSV 행, 구간, 라벨 근거, 공식 분할, 배열/미리보기 경로와 변환 정보. `prepared_labeled`는 정답이 붙은 배열 생성 성공이지 학습 승인이나 사고 예측 성공이 아니다.
- [configured_plan.json](../artifacts/preprocessing/received-input224-link-01/configured_plan.json): 크기만 바꾼 점검용 계획 사본. 기존 학습 설정 파일은 여전히 준비되지 않은 분할/가중치를 가리키며 자동 변경하지 않았다.
- 각 `prepared/labeled/sample_00N/`의 `frames.npy`는 로더 입력, `frames.json`은 시각·축·변환·정답, `frames/`는 선택한 16장의 확인용 PNG, `source_reference.png`는 원본 크기 참고 1장이다. 참고 1장으로 모든 프레임 픽셀 대응이나 장면 식별성을 증명한다고 하지 않는다.

Codex가 새 요약 그림 3개를 실제 열어 화면과 시각 배치를 확인했다. 특히 먼 차량이 작게 보이므로 자동으로 ‘사람 식별 가능’이나 ‘사고 인식 성공’으로 판정하지 않았다. 필요한 추가 확인은 **같은 장면을 224픽셀에서도 구분할 수 있는지**이며 기존 사고 여부·구간 승인을 다시 요청하는 것이 아니다.

### 실제 명령·측정·테스트

프로젝트 루트에서 실제 실행한 명령:

```bash
.venv-model-check/bin/python -m unittest discover -s tests -p 'test_preprocessing_link.py' -v

.venv-model-check/bin/python scripts/check_preprocessing_link.py \
  --data-root data/local/accident \
  --plan artifacts/label-review/received-dahye-20260921-01/reviewed_plan.json \
  --reference artifacts/data-check/received-three-01/prepared \
  --config configs/train_r3d18.local.json \
  --configured-size \
  --output artifacts/preprocessing/received-input224-link-01
```

이미 생성한 폴더에 재실행하면 덮어쓰기 없이 거부된다. 새 검사가 필요한 경우에만 출력 경로를 아직 없는 폴더로 지정한다. 결과 확인만 하려면 위 JSON과 미리보기를 열면 되며 영상을 다시 추출할 필요가 없다.

- 실제 전처리/로더: 종료 0, 3개 통과/오류 0/시간 초과 없음. worker 전체 상한 60초, 영상별 15초. **외부 명령 전체 3.666939초, worker 3.539658초, 전처리 2.350494초, 검증 0.932488초**다. 나머지는 시작·기록 등 부대 시간이다. [명령과 외부 시간](../artifacts/preprocessing/input224-audit-20260921-01/run-01.json), [실행 보고](../artifacts/preprocessing/received-input224-link-01/run.json).
- 내부 디코딩 수는 007/008/009 각각 76/68/199, 합계 343프레임이다. 이전 키프레임부터의 선행 읽기를 포함한다. 저장한 처리 프레임은 각 16장·총 48장, 배열 3개·요약 그림 3개·원본 참고 3장이다. 전체 영상 재생/전체 프레임 무결성 검사는 아니다.
- 새 결과 폴더 70개 파일 **13,947,795 bytes(약 13.30 MiB)**. `run.json`의 13,946,449 bytes는 그 보고서 자체를 쓰기 전 값이다. 감사 로그 폴더는 이 크기에 포함하지 않았다.
- 측정 메모리: worker 최고 RSS **327,827,456 bytes(312.64 MiB)**, 디코더 자식 중 최대 RSS **191,512,576 bytes(182.64 MiB)**. `getrusage` 실측이며 두 수치는 동시 합계가 아니다. GPU/MPS 메모리는 미측정이고 모델도 생성하지 않았다.
- 실제 로컬 Darwin/arm64, macOS 26.6.2, 기존 가상환경 Python 3.13.2 / torch 2.10.0 / torchvision 0.25.0 / PyAV 16.1.0 / NumPy 2.2.6 / Pillow 11.3.0. CPU 영상 처리·배열 변환이며 학습 장치를 CPU로 몰래 바꾼 것이 아니다. 학습/MPS 실행 자체가 없다.
- 관련 테스트 **35개 통과(기존 22+신규 13)**, 실패·건너뜀 0, 내부 1.438초/외부 1.745초, 60초 상한·첫 시도 통과. [테스트 로그](../artifacts/preprocessing/input224-audit-20260921-01/tests-01.log). 작은 생성 픽셀로만 오류를 시험했고 실제 교통 라벨의 타당성·성능 검증은 아니다. 누락·시간 변경·배열 순서·crop·기준 미리보기 변조·승인 정답 보존·test 분할 보호·상한을 확인했다. 전체 스위트나 모델 테스트는 반복하지 않았다.

### 확인 완료 / 미확인 / 추가 자료 필요

| 항목 | 확인 완료 | 미확인 | 추가 자료·확인 필요 |
|---|---|---|---|
| 007~009 입력 | 승인 구간·정답·512 검수/품질 답변, 224 입력의 ID·PTS·순서·정규화 연결 | 224픽셀에서 사람 식별성 | 새 미리보기에서 차량/움직임을 구분할 수 있는지. 이 확인에는 새 원본 불필요 |
| 원본/사건·분할 | 위 공식 두 분할 열과 원본 파일 해시 유지 | 같은 원본·사건/재편집 관계, 사용할 공식 프로토콜 | 원본 출처/사건 근거와 IID·geographic 중 평가 기준. 다르다고 자동 독립 그룹 생성 금지 |
| 정상 자료 | QMUL 3구간 정상 승인, 같은 원본 1개, 기존 224 로더 연결 | QMUL 잘림·추가 문맥과 224 식별성, 독립 검증용 정상 자료 | 기존 미리보기 검수와 별개 정상 원본. 같은 QMUL 원본을 잘라 학습/평가 양쪽에 넣지 않기 |
| 작은 기준 학습 | 공통 코드·C-1 제한 연산·선택 구간 입력 연결 | 두 클래스의 승인된 학습/검증 목록과 그룹 분리·시간 샘플링 규칙·공식 가중치 준비 | 공식 test를 학습에 쓰지 않는 자료 구성, 독립 정상/사고 자료, 가중치 다운로드 별도 승인 |

007은 두 프로토콜 모두 test라 학습용으로 옮기지 않는다. 008·009는 IID train/geographic test이므로 하나를 임의로 선택해 학습용 2개 확보로 선언하지 않는다. 이미 열어본 자료의 노출 이력은 유지한다. 사고 승인 구간은 기존 001~003 포함 총 6개, 정상은 QMUL 3구간/1원본이다. 학습용 표본 수나 독립 사건 수가 아니며 실제 학습·평가 수는 0이다. 사고 2초/정상 6초 시간 규칙, 009의 시점/평균 FPS 불일치 원인도 이번에 해결되지 않았다.

**다음 하나(제안만): 기존 224픽셀 미리보기의 사람 식별성 확인.** 새 사고 3구간과 기존 QMUL 3구간의 이미 생성된 미리보기를 사용한다. QMUL만 남아 있는 잘림·추가 문맥도 해당 원본에 대해 확인한다. 답변한 항목만 기존 검수 기록에 연결하고 원본 재추출·모델 실행은 하지 않는다. 코드/기록 점검은 60초 이내를 제안하며 이번에는 후속 검수를 실행하거나 답변을 대신 작성하지 않았다. 전체 학습에 필요한 자료 검색·다운로드도 자동 진행하지 않았다.


## 224픽셀 입력 품질 검수 기록 — 2026-09-21

**현재 상태: 코드 구현·제한 테스트·실제 검수 양식 생성 완료 / 사람 답변 대기.** 기존 사고 007~009의 정답 `[1,1,1]`과 QMUL 정상 `[0,0,0]`은 유지한다. 사용자의 “앞 작업 결과를 확인했어”는 각 224픽셀 그림의 식별성·QMUL 잘림/문맥에 대한 구체적 응답으로 확대하지 않았다.

### 이번에 확인할 그림과 질문

아래 그림은 모두 이미 저장된 224픽셀 입력의 요약 미리보기다. 각 칸은 왼쪽→오른쪽, 위→아래 순서이며 `actual`이 원본 파일 기준 시각이다. 구간 정답이나 순서를 다시 판정할 필요는 없다.

| 대상 | 원본 기준 구간 | 기존 정답 | 이번에 남은 사람 확인 |
|---|---|---|---|
| [사고 007](../artifacts/preprocessing/received-input224-link-01/prepared/labeled/sample_007/preview.png) | 3.000~5.000초 | 사고 1 | 224픽셀에서도 필요한 차량·사고 움직임을 식별할 수 있는가 |
| [사고 008](../artifacts/preprocessing/received-input224-link-01/prepared/labeled/sample_008/preview.png) | 2.583~4.583초 | 사고 1 | 동일 |
| [사고 009](../artifacts/preprocessing/received-input224-link-01/prepared/labeled/sample_009/preview.png) | 4.833~6.833초 | 사고 1 | 동일 |
| [QMUL 001](../artifacts/preprocessing/qmul-reviewed-link-01/qmul_001/preview.png) | 30~36초 | 정상 0 | 224픽셀 차량·움직임 식별 가능 여부, 잘림·중요 장면 누락, 추가 문맥 필요 여부 |
| [QMUL 002](../artifacts/preprocessing/qmul-reviewed-link-01/qmul_002/preview.png) | 1500~1506초 | 정상 0 | 동일 |
| [QMUL 003](../artifacts/preprocessing/qmul-reviewed-link-01/qmul_003/preview.png) | 2700~2706초 | 정상 0 | 동일 |

007~009의 기존 잘림·누락 없음/추가 장면 불필요 답변은 보존하고 다시 묻지 않는다. 그 답변을 다른 원본인 QMUL에 적용하지 않는다. 답변할 때는 `007: 식별 가능/어려움/미확인`처럼 적고, QMUL에는 `잘림·누락 있음/없음/미확인, 추가 장면 필요/불필요/미확인`을 더하면 된다. 모두 같으면 대상 번호를 묶어서 답해도 된다. 검수자와 확인 날짜도 함께 기록한다. **아직 못 봤다면 미확인 그대로** 둔다. JSON을 사용자가 직접 수정할 필요 없이 대화로 답해도 된다.

### 코드 흐름과 보존 방식

기존 `scripts/record_segment_reviews.py`에 `--input-quality accident|qmul`만 추가했다. `quality_inputs()`가 성공한 이전 연결 보고와 승인 계획을 대조하고, 원본 ID·구간·정답·공식 분할/그룹·배열/시각 파일 해시를 연결한다. 현재 미리보기의 16개 그림이 이미 검사한 배열과 같은지 확인한다. QMUL의 과거 보고서에는 요약 그림 해시가 없어, 이 작은 픽셀 대조로 현재 그림을 결합했다. 원본 AVI/MP4·검토 MP4 재디코딩이나 모델 로더 실행은 하지 않는다.

`apply_quality_answers()`는 현재 미리보기 해시에 맞는 응답만 받아 `quality_status`를 기록한다. 응답하지 않은 검사/검수자/날짜/원문은 채우지 않는다. 다른 ID·구간·미리보기·부모 보고서, 문자열로 적힌 참/거짓, 잘못된 날짜, 답변에 추가한 라벨 변경 필드는 거부한다. 기존 사고 여부 판정용 `apply_answers()` 경로는 유지했다.

`run_quality()`는 새 폴더에 `answers.template.json`과 `quality_review.json`을 저장한다. `original_segment_record`에는 원래 검수 기록을 그대로 보존하고, `quality_submission`과 `quality_status`에 후속 품질 답변을 별도로 둔다. 원래 기록 안의 과거 pending/false 필드는 이력이므로 후속 기록으로 덮어쓰지 않는다. `confirmed`가 되더라도 학습/독립 평가 승인은 false다.

| 상태 | 의미 | 기존 정답 |
|---|---|---|
| `awaiting_review` | 필요한 답변이나 검수자·날짜·원문이 미기입 | 유지 |
| `reported_issue` | 출처가 있는 완전한 응답에서 식별 어려움/잘림/추가 문맥 필요가 보고됨 | 유지 |
| `confirmed` | 이번에 요청한 품질 항목에 명시적으로 문제없다고 응답함 | 유지, 학습·성능 승인 아님 |

이 코드는 **응답의 파일 연결과 기록 규칙**을 검사한다. 사람이 정말 그림을 보았는지, 답변이 내용상 정확한지 자동으로 증명하는 기능은 아니다. Codex가 기존 QMUL 요약 그림 3개를 열어 배치·시각을 확인했지만 사용자 검수로 기록하지 않았다.

### 실제 명령과 결과

```bash
.venv-model-check/bin/python -m unittest discover -s tests -p 'test_segment_reviews.py' -v

.venv-model-check/bin/python scripts/record_segment_reviews.py \
  --input-quality accident \
  --plan artifacts/preprocessing/received-input224-link-01/configured_plan.json \
  --manifest artifacts/preprocessing/received-input224-link-01/verification.json \
  --output artifacts/label-review/input224-accident-quality-01

.venv-model-check/bin/python scripts/record_segment_reviews.py \
  --input-quality qmul \
  --plan configs/qmul_segments.review-20260920-02.json \
  --manifest artifacts/preprocessing/qmul-reviewed-link-01/report.json \
  --output artifacts/label-review/input224-qmul-quality-01
```

`--manifest`는 이번 품질 모드에서는 **이전 연결 검사 보고서**다. 정답 판정 모드에서는 기존 용도가 유지된다. 이미 만든 출력 폴더에는 재실행하지 않는다. 답변을 받으면 채운 별도 응답 파일을 `--answers`로 지정하고 새 출력 경로에 기록한다. 위 실제 실행은 답변 파일을 지정하지 않아 빈 양식만 만들었다.

- 테스트: **36개 통과(기존 20+신규 16), 실패·건너뜀 0**, 내부 2.726초 / 외부 2.855615초. 60초 상한, 첫 시도 통과. 생성 JSON·픽셀·mock과 별도 CLI 왕복으로 정상 경로/미응답/일부 응답/품질 문제/변조·누락·출력 보호를 검사했다. 완전한 답변 저장 경로는 **명시적 테스트 답변으로만 실행**했다. [테스트 로그](../artifacts/label-review/input-quality-codecheck-20260921-01/tests-01.log).
- 실제 사고 양식: 종료 0, 내부 0.123562초 / 외부 0.179121초, `awaiting_review:3`, 기존 정답 `[1,1,1]` 유지.
- 실제 QMUL 양식: 종료 0, 내부 0.123758초 / 외부 0.182960초, `awaiting_review:3`, 기존 정답 `[0,0,0]` 유지.
- 두 실제 명령 합계 **0.362671초**, 합계 상한 60초, 시간 초과 없음. [명령·시간·환경](../artifacts/label-review/input-quality-codecheck-20260921-01/runs.json). 로컬 Darwin/arm64, 기존 Python 3.13.2 가상환경. 새 설치 없음.
- 두 기록 프로세스 중 최대 RSS **59,932,672 bytes(57.16 MiB)**: macOS `RUSAGE_CHILDREN` 실측이며 동시 합계/GPU 메모리가 아니다. 배열을 디스크에서 읽어 비교했으며 원본 영상 디코딩·새 프레임/미리보기·모델·학습은 0이다.
- 새 산출물은 사고 2JSON 14,465 bytes, QMUL 2JSON 13,143 bytes, 합계 **27,608 bytes**다. 별도 감사/테스트 로그는 이 크기에서 제외했다. 종료 0은 기록 코드 실행 성공이며 사람 검수 완료를 뜻하지 않는다.

직접 확인: [사고 품질 기록](../artifacts/label-review/input224-accident-quality-01/quality_review.json) · [QMUL 품질 기록](../artifacts/label-review/input224-qmul-quality-01/quality_review.json). 각 `approved_target`, 보존된 `original_segment_record`, 미기입 `quality_submission.checks`, `quality_status=awaiting_review`를 함께 보면 된다. [사고 응답 양식](../artifacts/label-review/input224-accident-quality-01/answers.template.json)과 [QMUL 응답 양식](../artifacts/label-review/input224-qmul-quality-01/answers.template.json)은 파일/구간/그림 지문만 자동 기입했으며 사람의 답변·검수자는 null이다.

**남은 상태:** 224픽셀 사람 식별성 6개와 QMUL 잘림·문맥은 응답 대기다. 기존 사고/정상 판정과 코드 연결 완료는 그대로 유지한다. 원본/사건 관계·공식 분할 기준·독립 정상 자료·사고 2초/정상 6초 시간 규칙·공식 가중치도 아직 미완료다. 이번에는 실제 학습·성능 평가·새 영상 확보를 하지 않았다.

**다음 작업 하나(제안만): 사용자 검수 답변을 이번 코드로 연결해 이 입력 품질 단계를 마무리한다.** 입력은 위 그림별 답변과 검수자/날짜, 출력은 새 품질 기록이다. 필요한 항목만 채우고 기존 라벨/기록을 보존하는 것이 완료 기준이다. 새 추출·학습 없이 기록 처리 60초 이내이며, 응답이 오기 전 대신 승인하지 않는다.


## 검수 준비 상태만 확인하기 — 2026-09-21

> 아래는 답변 수신 전 실행 이력이다. 이후 실제 답변을 받은 결과는 [224픽셀 품질 답변 반영 완료](#224픽셀-품질-답변-반영-완료--2026-09-21)를 따른다.

**코드 보완과 테스트는 완료, 실제 사람 검수는 여전히 답변 대기다.** 기존 빈 양식을 반복 생성하지 않고 다음 기록이 가능한지 읽기만 하는 `--check-only`를 기존 기록기에 추가했다. 기존 저장 모드의 동작은 유지했다. 이 점검은 같은 품질 단계의 보완이며 다음 모델 단계로 넘어간 것이 아니다.

입력은 기존 계획·연결 보고·미리보기/배열·응답 양식이고, 처리는 기존 `quality_inputs()`와 `apply_quality_answers()` 재사용, 출력은 터미널의 상태/미응답 목록이다. `run_quality(..., check_only=True)`는 새 검수 폴더나 양식을 쓰기 전에 반환한다. `--output`과 함께 쓰면 거부한다. 별도 확인 도구를 만들거나 라벨을 변경하지 않는다.

| 상태 / 종료 코드 | 의미 |
|---|---|
| `ready_to_record` / 0 | 이번 품질 응답이 명시적으로 채워져 기록할 수 있음. 아직 저장한 것은 아니며 학습 준비 완료도 아님 |
| `awaiting_review` / 3 | 응답이나 검수자·날짜 등이 빠짐. 이번 실제 결과이며 오류·정상 예측이 아님 |
| `needs_attention` / 2 | 출처가 있는 응답에서 품질 문제를 보고함 |
| `invalid_request` / 2 | 파일 변경·누락·잘못된 값 등. 위 품질 문제와 JSON 상태로 구분 |

`reported_issues=[]`라도 `awaiting_review`이면 검수자가 문제없다고 확인한 뜻이 아니다. 입력이 달라지지 않고 답변도 없으면 이 확인이나 테스트를 반복 실행할 필요가 없다.

프로젝트 루트에서 이번에 실제 실행한 읽기 전용 명령:

```bash
.venv-model-check/bin/python scripts/record_segment_reviews.py \
  --input-quality accident --check-only \
  --plan artifacts/preprocessing/received-input224-link-01/configured_plan.json \
  --manifest artifacts/preprocessing/received-input224-link-01/verification.json \
  --answers artifacts/label-review/input224-accident-quality-01/answers.template.json

.venv-model-check/bin/python scripts/record_segment_reviews.py \
  --input-quality qmul --check-only \
  --plan configs/qmul_segments.review-20260920-02.json \
  --manifest artifacts/preprocessing/qmul-reviewed-link-01/report.json \
  --answers artifacts/label-review/input224-qmul-quality-01/answers.template.json

.venv-model-check/bin/python -m unittest discover -s tests -p 'test_segment_reviews.py' -v
```

위 `answers.template.json`은 이전 단계의 빈 양식이다. 사용자 답변을 적은 별도 파일이 준비되면 `--answers`에 그 파일을 지정한다. 대화로 답하면 에이전트가 원문을 보존해 연결할 수 있으며 사용자가 JSON을 직접 다룰 필요는 없다.

실제 결과:

- 사고와 QMUL 각각 **종료 3 / awaiting_review 3개**, 사고 정답 `[1,1,1]`, 정상 `[0,0,0]` 보존. `output_written=false`, `labels_changed=false`, `training_run=false`. 사고는 식별성, QMUL은 식별성·잘림/누락·추가 문맥, 두 묶음의 검수자/날짜/원문이 아직 미기입이다.
- 외부 실행 시간 사고 **0.186916초**, QMUL **0.185666초**, 합계 **0.372647초**. 합계 60초 상한 안에서 기대한 답변 대기 상태로 종료했다. [정확한 명령·출력·시간](../artifacts/label-review/input-quality-readiness-20260921-01/checks.json). CLI 자체는 검수 결과를 저장하지 않았으며, 재현성용 표준출력 로그는 별도 감사 폴더에만 남겼다.
- 관련 테스트 **41개 통과(기존 36+신규 5), 실패·건너뜀 0**, 내부 **3.952초**, 외부 **4.068756초**, 60초 상한·첫 시도 통과. [실제 로그](../artifacts/label-review/input-quality-readiness-20260921-01/tests-01.log). 신규 시험은 읽기 전용 파일 보존, 대기/기록 가능/문제의 종료 코드, 출력 인수/잘못된 모드 보호, QMUL 정답 보존·변조 탐지다. 기록 가능/문제 응답은 생성 테스트 입력으로만 실행했고 실제 사람 답변으로 기록하지 않았다.
- 두 검사 프로세스 중 최대 RSS **64,503,808bytes(61.52MiB)**, macOS `RUSAGE_CHILDREN` 실측. GPU·모델 비용·동시 총 메모리는 아니다. 로컬 Darwin/arm64의 기존 Python 가상환경을 사용했다. 원본 영상 디코딩·새 미리보기·모델·학습·다운로드는 없다.

**다음 하나:** 위 여섯 미리보기에 대한 구체적인 답변을 기존 기록 코드로 반영해 현재 품질 단계를 마무리한다. 아직 답변이 없어 그 단계의 실제 반영은 실행하지 않았다. 이미 승인한 사고/정상 판정을 다시 요구하지 않는다. 코드 추가나 빈 양식 재생성보다 사용자 답변이 필요한 상태이며, 현재 작업에는 새 영상이 필요하지 않다.

## 224픽셀 품질 답변 반영 완료 — 2026-09-21

**다혜의 새 답변으로 여섯 구간의 요청된 품질 항목을 확인 완료했다.** 기존 사고/정상 정답과 구간·원본·검수 이력은 보존했다. 위의 답변 대기/미확인 표시는 수신 전 이력이다. 기존 `record_segment_reviews.py`의 품질 모드를 그대로 실행했으며 추가 프로그램 수정은 필요하지 않았다.

받은 답변은 사고 007·008·009의 “224픽셀에서도 차량·사고 움직임 식별 가능”, QMUL 001·002·003의 “식별 가능 / 잘림·중요 장면 누락: 없음 / 추가 장면: 불필요”, 검수자 “다혜 260921”이다. `raw_response`에 원문(공백을 나타내는 `&#x20;` 포함)을 보존하고, 기계 판독 날짜는 `2026-09-21`로 기록했다. 사고의 잘림/추가 장면은 [앞서 받은 명시적 답변](../configs/received_segments.quality-20260921.json)을 유지하며 이번에 다시 판정하지 않았다.

| 구간 | 유지한 원본 범위(초) | 기존 정답 | 224 식별성 | 잘림·누락 / 추가 장면 | 이번 품질 기록 |
|---|---|---|---|---|---|
| 007 | [3.000, 5.000) | 사고 1 | 가능 | 기존 확인 보존: 없음 / 불필요 | confirmed |
| 008 | [2.583, 4.583) | 사고 1 | 가능 | 기존 확인 보존: 없음 / 불필요 | confirmed |
| 009 | [4.833, 6.833) | 사고 1 | 가능 | 기존 확인 보존: 없음 / 불필요 | confirmed |
| qmul_001 | [30, 36) | 정상 0 | 가능 | 없음 / 불필요 | confirmed |
| qmul_002 | [1500, 1506) | 정상 0 | 가능 | 없음 / 불필요 | confirmed |
| qmul_003 | [2700, 2706) | 정상 0 | 가능 | 없음 / 불필요 | confirmed |

사람이 확인한 것은 위 미리보기의 식별성/필요 장면이다. 코드가 확인한 것은 해당 그림·16프레임 배열·시각 파일이 기존 성공한 연결 보고와 같다는 사실이다. 이번에 원본 영상을 다시 재생하거나 사고 정답을 새로 추론한 것이 아니다.

### 실제 코드 흐름과 확인할 파일

1. 기존 빈 양식의 원본 ID·구간·그림 지문을 유지하고 답변 칸만 채웠다: [사고 답변](../configs/received_segments.input224-quality-20260921.answers.json), [QMUL 답변](../configs/qmul_segments.input224-quality-20260921.answers.json).
2. `quality_inputs()`가 기존 계획/성공 보고와 현재 배열·시각·그림 해시 및 그림 16칸을 대조한다. `apply_quality_answers()`는 명시된 응답만 검사하며 기존 라벨을 보존한다.
3. `run_quality()`의 읽기 전용 확인 두 번은 `ready_to_record`, 실제 저장 두 번은 `recorded`로 끝났다. [사고 품질 결과](../artifacts/label-review/input224-accident-dahye-20260921-01/quality_review.json), [QMUL 품질 결과](../artifacts/label-review/input224-qmul-dahye-20260921-01/quality_review.json)의 `quality_submission`과 `quality_status`가 새 답변이다.

`original_segment_record`는 과거 기록의 사본이므로 그 안의 예전 미응답/미승인 필드는 그대로 남아 있다. 이것을 현재 답변 대기로 읽지 않는다. 각 출력 폴더의 `answers.template.json`은 재사용 가능한 **빈 양식**이고, 실제 답변 파일은 위 configs 두 개다. 기존 결과를 덮어쓰는 방법 대신 후속 기록을 선택해 언제 무엇을 확인했는지 보존했다. 학습 사용 승인과 사건별 독립성은 새 품질 기록에서도 `false`다.

### 실제 명령과 제한 테스트

프로젝트 루트의 기존 가상환경에서 아래 저장 명령을 각각 한 번 실행했다. 먼저 같은 인수에서 `--output …` 대신 `--check-only`로 검사했다. **지금 직접 재확인할 때도 `--check-only`를 사용**하면 기존 파일을 쓰지 않는다. 같은 출력 폴더로 다시 저장하면 보존 규칙에 따라 거부된다.

```bash
.venv-model-check/bin/python scripts/record_segment_reviews.py \
  --input-quality accident \
  --plan artifacts/preprocessing/received-input224-link-01/configured_plan.json \
  --manifest artifacts/preprocessing/received-input224-link-01/verification.json \
  --answers configs/received_segments.input224-quality-20260921.answers.json \
  --output artifacts/label-review/input224-accident-dahye-20260921-01

.venv-model-check/bin/python scripts/record_segment_reviews.py \
  --input-quality qmul \
  --plan configs/qmul_segments.review-20260920-02.json \
  --manifest artifacts/preprocessing/qmul-reviewed-link-01/report.json \
  --answers configs/qmul_segments.input224-quality-20260921.answers.json \
  --output artifacts/label-review/input224-qmul-dahye-20260921-01

PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 .venv-model-check/bin/python \
  -m unittest test_segment_reviews.InputQualityReviewTests -v
```

두 실제 저장 명령은 각각 종료 0, 출력 예시는 다음과 같다. `recorded`는 품질 기록 저장 성공이며 학습 완료가 아니다.

```json
{"status": "recorded", "counts": {"confirmed": 3}, "training_run": false}
```

- 이번 관련 테스트 **21개 통과 / 실패·건너뜀 0**, 내부 4.015초/외부 4.109652초, 60초 상한. 기존 품질 테스트만 실행했다. 정답 보존·부분 답변·잘못된 날짜/값·변조/누락·그림 대조·읽기 전용/출력 보호를 생성 입력으로 검사했다. 기존 41개 전체나 모델 테스트를 재실행하지 않았다. [실제 로그](../artifacts/label-review/input-quality-received-20260921-01/tests-01.log).
- 실제 사전 확인 0.147256초/0.170550초, 실제 저장 0.155917초/0.173256초. 네 명령 묶음 전체 **0.654962초**, 합계 상한 60초/시간 초과 0. [정확한 명령·상태·환경·외부 시간](../artifacts/label-review/input-quality-received-20260921-01/runs.json).
- 테스트와 네 CLI 자식 프로세스 중 최고 RSS **124,846,080bytes / 119.06MiB**, macOS `RUSAGE_CHILDREN` 실측이다. 동시 합계·GPU 메모리·모델 처리 비용이 아니다. 실제 환경 Darwin/arm64, macOS 26.6.2, Python 3.13.2. 새 설치 없이 실행했다.
- [저장 결과·보존 대조](../artifacts/label-review/input-quality-received-20260921-01/verification.json) 11항목 통과. 사전 목록 339개 중 문서 11개만 변경하고 328개는 해시가 같다. 사후 대조식에서 사건 필드명을 잘못 가정한 오류 1회를 실제 스키마에 맞게 고쳐 재확인했으며 상세 이력은 PROGRESS에 남겼다. 실제 저장 명령·단위 테스트는 첫 시도에 성공했다. 원본 영상 재디코딩·새 프레임/미리보기·모델·학습은 0이다.

### 현재 조건과 다음 하나

| 항목 | 상태 | 확인 범위 또는 남은 근거 |
|---|---|---|
| 위 여섯 구간 입력 품질 | 확인 완료 | 다혜/2026-09-21 응답, 기존 그림·배열 연결. 전체 데이터 검증은 아님 |
| 공식 분할 | 값 확인 완료 / 평가 방식 미확인 | 007=test/test, 008·009=IID train/지리 test. 하나를 자동 선택하지 않음 |
| 원본·사건 관계 | 일부 확인 / 추가 자료 필요 | QMUL 3개는 같은 원본. 사고 3개의 상위 원본·사건 관계는 미확인 |
| 정상 자료 | 지정 3구간 검수 완료 / 추가 자료 필요 | 독립 학습·평가를 위한 다른 정상 원본이 부족. 같은 QMUL 원본 구간끼리 나눌 수 없음 |
| 작은 기준 학습 | 아직 보류 | 평가 방식·사건 그룹·양쪽 클래스 분리, 사고 2초/정상 6초의 시간 규칙, 공식 가중치 준비가 남음 |

**다음 작업 하나(제안만): 기존 자료의 C-2 학습·평가 분리 준비 점검.** 입력은 승인 구간·이번 품질 기록·CSV 공식 분할·알려진 원본 관계다. 기존 준비 검사 코드를 우선 읽고 이를 대조해 사용 보류 사유와 필요한 자료/결정 목록을 만든다. 완료 기준은 공식 test를 학습용으로 옮기지 않고, 미확인 그룹·평가 방식을 확정한 것처럼 만들지 않는 명세다. 우선 기록 기반 확인 60초·관련 테스트 60초 이내로 제안하며 원본 재추출·학습·다운로드는 포함하지 않는다. 이번에는 이 후속 점검을 실행하지 않았다.
