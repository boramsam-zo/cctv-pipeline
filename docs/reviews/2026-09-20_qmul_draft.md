# QMUL 짧은 구간 검수 초안 — 사람 확인 대기

> 후속 응답: 다혜가 2026-09-20에 세 구간을 정상으로 판정하고 재생 가능하다고 답했다. [사람 검수 기록](2026-09-20_qmul_dahye.md)에 반영했으며 잘림·순서·추가 확인 항목은 미응답이다. 아래 본문과 출력 JSON의 null은 **초안 생성 당시 이력**으로 보존한다. 학습 승인이나 모델 입력 검수 완료는 아니다.

작성: Codex, 2026-09-20. **정상 후보를 제안하는 초안이며 승인 정답이 아니다.** 사람 검수자·검수일은 미기입이다. `label=null`, `target=null`, `human_review_status=unreviewed`, `training_authorized=false`를 유지했다.

## 먼저 열어볼 파일

각 MP4는 약 6초다. 기존 ACCIDENT 001~003과 혼동하지 않도록 `qmul_` 접두사를 붙였다. 아래 초는 **전달받은 로컬 AVI의 재생 시각**이고 MP4는 각 구간의 첫 프레임을 0초로 표시한다.

| 구간 ID | 원본 검토 범위 | 재생 파일 / 요약 화면 | Codex가 표본 화면에서 본 내용 | 제안 |
|---|---|---|---|---|
| qmul_001 | [30, 36)초, 00:30~00:36 | [MP4](../../artifacts/data-check/qmul-review-01/qmul_001/review.mp4) / [16장 요약](../../artifacts/data-check/qmul-review-01/qmul_001/stills/preview.png) | 차량들이 교차로를 통과하고 일부 차량은 대기·회전한다. 표본에서 뚜렷한 접촉·전복은 보이지 않는다. 중앙 회전 차량과 근접 차량의 관계는 연속 재생으로 확인 필요 | 정상 후보, 미확정 |
| qmul_002 | [1500, 1506)초, 25:00~25:06 | [MP4](../../artifacts/data-check/qmul-review-01/qmul_002/review.mp4) / [16장 요약](../../artifacts/data-check/qmul-review-01/qmul_002/stills/preview.png) | 대기 중인 차량 줄과 교차로를 통과하는 차량이 보이며 뒤에는 이륜차와 차량들이 움직인다. 표본에서 뚜렷한 충돌·전복은 보이지 않는다 | 정상 후보, 미확정 |
| qmul_003 | [2700, 2706)초, 45:00~45:06 | [MP4](../../artifacts/data-check/qmul-review-01/qmul_003/review.mp4) / [16장 요약](../../artifacts/data-check/qmul-review-01/qmul_003/stills/preview.png) | 승용차·픽업 형태 차량·흰색 소형 버스 형태 차량이 교차로를 통과한다. 표본에서 뚜렷한 접촉·전복은 보이지 않는다 | 정상 후보, 미확정 |

Codex가 눈으로 확인한 범위는 **각 16장, 총 48장 요약 화면**이다. MP4의 모든 프레임은 코드로 다시 디코딩했지만 모든 순간을 사람이 연속 재생 검수한 것과 다르다. 가림·작은 차량·표본 사이 순간은 남은 한계다. 신호 위반·위험 운전 여부를 이 과제의 사고 정답으로 바꾸지 않는다. [기존 라벨 기준](../label_policy.md)을 적용해 실제 충돌·전복 여부와 판독 가능성을 확인한다.

## 사람이 확인하고 답할 내용

1. 각 MP4를 처음부터 끝까지 재생한다. 충돌·접촉·전복이 보이는지, 앞뒤 장면이 더 필요한지 확인한다.
2. 정상 / 사고 / 판단 불가 중 판정하고 실제 보인 근거를 적는다. 애매하면 판단 불가 또는 앞뒤 구간 재검토로 남긴다.
3. 재생 멈춤·순서 이상·화면 잘림 여부, 검수자·날짜를 답한다. 원본 CSV나 JSON을 직접 편집할 필요는 없다. 아래 내용을 채워 보내면 이후 승인된 작업에서 기록할 수 있다.

```text
구간 ID: qmul_001 / qmul_002 / qmul_003 중 하나
확인한 범위: 위 표의 해당 원본 시작 초 ~ 끝 초
판정: 미기입 (정상 / 사고 / 판단 불가)
판정 근거: 미기입
영상 재생 가능: 미기입
잘림·순서·앞뒤 장면 추가 확인 필요: 미기입
검수자 / 확인 날짜: 미기입
```

`정상`에 동의하는 것은 **이 짧은 범위에 대한 검수**다. 원본 50분 전체의 정상 판정이나 학습 사용 승인이 아니다. 검수용 6초가 모델 입력 길이로 확정된 것도 아니다. 224픽셀·16프레임 모델 입력은 이번에 만들거나 검수하지 않았다.

## 출처·시각·같은 원본 관계

- 파일명: `qmul_junction.avi`. 사용자가 지정한 원래 위치에서 읽었고 ACCIDENT 폴더로 복사하지 않았다. 개인 절대 경로는 이 문서/보고서에 기록하지 않는다.
- 출처: 사용자가 QMUL 교차로 영상으로 전달. [제작자 페이지](https://personal.ie.cuhk.edu.hk/~ccloy/downloads_qmul_junction.html)의 자료 설명을 참고했다. 배포자 체크섬과 일치 여부는 미확인이다. 앞선 [출처·이용 조건 기록](../data_inventory.md#10-정상-영상-추가-출처-조사--2026-09-20)을 유지한다.
- 파일 SHA-256: `2add2fa0207a9c903e424f76ead3c749bb02a66994c0f4a9e64fc60cc51652bb`.
- 정상/사고 원본 정답: 이번 입력에는 없음. 공식 이상행동 주석을 받거나 적용하지 않았다. 미주석을 정상으로 처리하지 않는다.
- 공식 분할·사건 ID·다른 파일과의 관계: 미확인. 세 구간은 **동일 로컬 파일 한 그룹**임이 확인되어 같은 `source_group_id`를 기록했다. 세 독립 원본이 아니며 서로 다른 학습/검증/테스트 분할에 흩어놓지 않는다.
- 원본은 360×288, 30000/1001≈29.970fps, 헤더 3,003초다. 제작자 설명의 1시간/25fps와 차이의 원인은 미확인이다. 이번에는 로컬 파일의 PTS만 사용하고 25fps로 속도를 바꾸거나 공식 주석 시각을 복사하지 않았다.
- MP4 첫 프레임의 원본 시각은 각각 **30.030000 / 1500.031867 / 2700.030667초**다. 원하는 시작 시각 이후의 첫 실제 프레임을 사용하므로 약 0.03초 차이가 있다. 각 프레임의 원본 PTS·원본 초·MP4 초 대응을 `manifest.json`과 각 `segment.json`에 보관했다. 마지막 원본 프레임 시각은 35.969267 / 1505.971133 / 2705.969933초다. 끝 경계는 포함하지 않는다.

## 실제 구현·실행 결과

입력 AVI + [선택 구간 설정](../../configs/qmul_review_candidates.json) → [CLI의 run/worker/supervise](../../scripts/prepare_review_previews.py) → [create_preview/verify_movie](../../src/boram3_cctv/review_preview.py) → MP4·요약 그림·시각 대응 JSON 순서다. 기존 `preprocessing.validate_window`, `save_frames`, 해시/JSON 함수를 재사용했다. 기존 ACCIDENT 전처리와 학습 코드는 바꾸지 않았다.

구간은 사고 주석이나 이미 알고 있는 정상 정답이 아닌 앞·중간·뒤 위치로 미리 정했다. AVI 원본 대신 짧은 H.264/MP4만 재생용으로 만들면 작은 범위로 사람이 확인할 수 있기 때문이다. MP4는 원본 360×288을 유지하고 crop·프레임 순서 변경·의도적인 속도 변경을 하지 않았다. H.264 재압축은 손실 방식이며 학습용 원본의 대체물이 아니다. PNG 요약은 원본 프레임 16장을 골라 상하 검은 여백을 더한 360×360 타일이다. GPU/MPS·모델을 실행하지 않았다.

| 실측 항목 | 결과 |
|---|---|
| 상태 | `completed_unreviewed`, 성공 3 / 오류 0 / 승인 정답 0 |
| 환경 | 로컬 Darwin/arm64, Python 3.13.2, PyAV 16.1.0, Pillow 11.3.0, CPU·코덱 스레드 1 |
| 실행 상한 | 구간 worker 30초, 전체 CLI 90초; 구간별 소프트 작업 한도 25초 |
| 실행 시간 | 작업 내부 2.579초 / 외부 프로세스 계측 2.677초 |
| 원본 디코딩 | 330 / 386 / 350프레임, 총 1,066프레임. 각 시작 전 키프레임부터의 읽기와 끝 경계 확인 포함 |
| MP4 출력 및 재확인 | 구간별 179프레임, 총 537프레임 전체 읽기 성공. PTS 순서 엄격 증가, 기록 대비 시각 오차 0초 |
| MP4 용량 | 445,251 / 379,154 / 365,665 bytes, 합계 1,190,070 bytes |
| 출력 전체 용량 | 17,443,877 bytes (PNG·요약·JSON 포함) |
| worker 최고 RSS 중 최대 | 120,602,624 bytes ≈115.02 MiB. 부모 포함 전체 실행 peak나 전체 시스템 메모리가 아님 |
| 원본·설정 보존 | 실행 전후 SHA-256 일치 |

위 시간과 메모리는 이번 소수 구간 **변환 작업의 실측**이다. 전체 데이터 처리 속도·모델 성능·학습 시간으로 외삽하지 않는다. 사용자 플레이어 재생은 아직 미확인이다.

실제로 실행한 명령에서 원본 위치만 환경 변수로 표시했다. 프로젝트 루트 터미널에서 사용자가 전달한 AVI 경로를 `QMUL_VIDEO_PATH`에 지정한 다음 실행할 수 있다. **`qmul-review-01`은 이미 있으므로 재실행 시 존재하지 않는 새 출력 폴더명을 사용한다.** 확인만 하려면 아래 `open` 명령이면 된다.

```sh
.venv-model-check/bin/python scripts/prepare_review_previews.py \
  --source "$QMUL_VIDEO_PATH" \
  --plan configs/qmul_review_candidates.json \
  --output artifacts/data-check/qmul-review-01

open artifacts/data-check/qmul-review-01/qmul_001/review.mp4
open artifacts/data-check/qmul-review-01/qmul_002/review.mp4
open artifacts/data-check/qmul-review-01/qmul_003/review.mp4

.venv-model-check/bin/python -m unittest discover -s tests -p 'test_review_preview.py' -v
```

실제 표준 출력(시간 소수 자릿수만 줄임):

```json
{"status":"completed_unreviewed","elapsed_seconds":2.579,"inputs_unchanged":true,"approved_targets":0,"training_run":false}
```

## 테스트와 남은 한계

[13개 테스트](../../tests/test_review_preview.py)는 짧은 색상 영상으로 검사하며 실제 교통 정답이나 ACCIDENT 합성 자료가 아니다. 누락·손상 파일, 짧은 영상/잘못된 시각, 기존 출력 보호, 해시 불일치, 최대 3구간·중복 구간 제한, 정답 주입 거부, worker/전체 시간 상한, 같은 원본 그룹, 불규칙 PTS 보존, MP4 다시 읽기·프레임 순서/시각을 확인한다.

첫 실행은 12개 통과·1개 실패였다. 소수 `0.2`를 이진 부동소수로 비교하면 정확한 원본 `1/5`초 프레임이 경계 이전으로 잘못 분류되는 문제였다. `Fraction(str(초))`로 정확한 분수 경계를 사용해 수정했고 **다시 실행한 13개 모두 통과, 2.003초(외부 2.236초), 실패·건너뜀 0**이다. 실패 로그도 보존했다. 전체 테스트 스위트 재실행은 아니다.

- [실제 결과 JSON](../../artifacts/data-check/qmul-review-01/manifest.json)
- [성공 테스트 로그](../../artifacts/data-check/qmul-preview-audit-20260920-01/tests-02.log), [첫 실패 로그](../../artifacts/data-check/qmul-preview-audit-20260920-01/tests-01.log)
- [실행 계측](../../artifacts/data-check/qmul-preview-audit-20260920-01/real-run-01.json), [보존·출력 확인](../../artifacts/data-check/qmul-preview-audit-20260920-01/verification.json)

다음 하나(제안): **사용자가 위 세 MP4를 보고 구간별 판정·근거·재생 상태·검수자·날짜를 답하면 별도 검수 기록으로 반영한다.** 원본 한 그룹, 공식 분할/시간 대응 미확인, 기존 ACCIDENT 여섯 영상은 공식 test라는 제약을 유지한다. 이번에는 검수용 결과까지 완료했고 C-2 실제 학습·D/E·전체 추출·다운로드·클라우드는 실행하지 않았다.
