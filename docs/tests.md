# 테스트 구성과 실행 방법

2026-09-21 실제 학습연결변경: `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 .venv-model-check/bin/python -m unittest test_training test_training_readiness -v` — **최종60개통과**, 학습기존24+신규9+준비27, 내부6.335초/외부7.519008초/각시도60초상한. 새시험은train-only목적/승인·IIDtest제외·보수적/같은 원본그룹·입력변경·epoch/시간한도·프로브시간 초과·자원 중단후저장·2+23step재개/재로딩이다. 생성자료/작은CPU모델을사용했다. 첫59개fixture저장오류4개와둘째60개0특징으로인한기존시험실패1개는수정후로그를보존했다. [최종테스트 로그](../artifacts/training-code-check/limited-real-audit-20260921-01/tests-03.log), [실제MPS학습결과](training.md#2026-09-21--첫-실제-자료-제한-학습-완료-iid-참고학습-전용)를구분한다. 전체저장소테스트/모델일반화평가가아니다.

2026-09-21 IID 선택 반영: **31개 첫 시도 통과/실패·건너뜀0**, 내부0.068초/외부1.010927초, 상한60초다. `test_training_readiness` 27개(기존16+신규11)와 기존 `test_training.TrainingTests` 보호4개를 실행했다. 신규 시험은 명시적 IID 선택, train 후보/test 제외, A/B 준비 상태 분리, 최대3개·중복/부적격 거부, 미확인 사건 유지, 후보 파일/CSV 버전 비교, 승인 배열/원본 해시 연결·변경 거부다. 기존 보호4개는 원본/사건/내용의 분할 교차, 공식 test 이동, 계획/전처리/그룹에 묶인 승인, 기존 출력/가중치 미준비 시 학습 미실행을 검사한다. JSON/CSV/작은 생성 fixture를 사용했으며 모델 생성·학습과 실제 교통 영상 디코딩은 없다. [정확한 명령](../artifacts/training-code-check/iid-readiness-audit-20260921-01/tests-01.json), [통과 로그](../artifacts/training-code-check/iid-readiness-audit-20260921-01/tests-01.log), [실제 자료 결과와 한계](experiments/2026-09-19_readiness.md#2026-09-21--iid-선택-반영과-a-학습-후보-목록-확인-완료). 이전55개·전체 모델 시험을 다시 실행한 것이 아니다.

2026-09-21 최신 준비 점검: `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 .venv-model-check/bin/python -m unittest test_training_readiness test_segment_reviews.InputQualityReviewTests test_train_candidates -v` — **최종55개 통과(신규16+기존 품질21+CSV 후보18), 실패·건너뜀0, 내부3.846초/외부3.936853초**. 각 시도60초 상한이다. 신규 시험은 승인 정답/품질 보존, 두 공식 프로토콜 비교·test 보호, 같은 정상 원본 묶기, 미확인 사건/외부 공식 분할 보존, 시간 규칙 차이, 중복/변조/누락/용량/경로/출력 보호, timeout, 기존 학습 모드와의 분리를 JSON/CSV/mock으로 확인한다. 첫 시험의 fixture 라벨 모순·코드 루트 기록 오류2개를 고쳐 관련55개 재시험에 성공했다. [최종 로그](../artifacts/training-code-check/reviewed-readiness-audit-20260921-01/tests-02.log), [실제 여섯 구간 점검](experiments/2026-09-19_readiness.md#2026-09-21--승인-입력의-학습평가-분리-준비-점검-완료). 전체 모델 테스트·학습·사고 성능 검증은 아니다.

2026-09-21 실제 품질 답변 수신 후: 기존 `InputQualityReviewTests`만 **21개 통과 / 실패·건너뜀 0 / 내부 4.015초·외부 4.109652초**, 60초 상한. 실행은 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 .venv-model-check/bin/python -m unittest test_segment_reviews.InputQualityReviewTests -v`다. 새 프로그램/단위 테스트 수정 없이 실제 답변이 처음 들어온 경로에 관련된 기존 테스트를 선택했다. 정답 보존·미응답·입력 변경/누락·그림/배열 대조·응답 형식·출력 보호를 생성 입력으로 검사한다. [로그](../artifacts/label-review/input-quality-received-20260921-01/tests-01.log). 실제 사고/QMUL 각 3개 답변은 별도 명령으로 확인·저장했고 모두 confirmed다. [실제 자료 대조](../artifacts/label-review/input-quality-received-20260921-01/verification.json)와 단위 테스트는 구분한다. 아래 41개 등은 이전 실행이며 이번 전체 스위트·모델/사고 성능 시험은 아니다.

2026-09-21 최신 읽기 전용 준비 확인: `test_segment_reviews.py` **41개 통과(기존 36+신규 5), 실패·건너뜀 0, 내부 3.952초/외부 4.068756초**, 60초 상한·첫 시도 통과. 새 시험은 저장 없는 파일 보존, 대기 3/기록 가능 0/문제 2 종료 코드, 잘못된 모드·출력 인수 보호, QMUL 정답 보존과 입력 변조 구분이다. [로그](../artifacts/label-review/input-quality-readiness-20260921-01/tests-01.log). 실제 여섯 항목은 아직 사람 답변 대기이며 성공/품질 문제 응답은 생성 테스트로만 확인했다. 코드 변경에 관련된 테스트만 실행했다.

2026-09-21 최신 품질 기록: `test_segment_reviews.py` **36개 통과(기존 20+신규 16), 실패·건너뜀 0, 내부 2.726초/외부 2.855615초**, 60초 상한·첫 시도 통과. 기존 승인 라벨 유지, 미응답/부분 답변, 식별 어려움, QMUL 별도 품질 항목, 응답과 그림/구간/부모 연결, 값/날짜/변조·누락·경로/용량/출력 보호와 CLI 왕복을 생성 픽셀/JSON/mock으로 확인했다. [로그](../artifacts/label-review/input-quality-codecheck-20260921-01/tests-01.log). 실제 여섯 그림은 양식 생성만 실행했고 사람 품질 답변은 아직 대기다. 전체 스위트나 모델 검증은 아니다.

2026-09-21 최신 224픽셀 연결: `test_preprocessing_link.py` **35개 통과(기존 22+신규 13), 실패·건너뜀 0, 내부 1.438초/외부 1.745초**, 60초 상한·첫 시도 통과. 새 시험은 생성 픽셀로 명시적 크기 변경, 시간 설정/구간 변경 거부, 부모/미리보기 변경, 미승인 정답 거부, PTS·순서·crop 오류, 배열/미리보기가 함께 잘못돼도 원본 참고 프레임으로 탐지, 누락 배열과 사고 정답 보존, 공식 test 보호, 실행 상한을 확인한다. [로그](../artifacts/preprocessing/input224-audit-20260921-01/tests-01.log) · [실제 세 영상 결과](preprocessing_link_check.md#007009의-224픽셀-입력-연결--2026-09-21). 교통 장면 정답이나 모델 성능을 테스트한 것은 아니다. 전체 스위트·모델 테스트는 다시 실행하지 않았다.

2026-09-21 최신 입력 품질 확인: `test_preprocessing_link.py` **22개 통과(기존 12+추가 10), 실패·건너뜀 0, 1.622초/외부 1.930초**, 60초 상한·첫 시도 통과. 기존 생성 픽셀 fixture로 PNG 교환과 미리보기 표시 순서, 시각 변조/역전, ID 불일치, 누락 PNG, 변경된 미리보기/부모 기록, 품질 미응답, 검수 크기와 학습 크기 구분을 확인했다. 검사 실패가 기존 사고 판정을 취소하거나 정상 0으로 바꾸지 않는지도 확인했다. [테스트 로그](../artifacts/preprocessing/input-quality-audit-20260921-01/tests-01.log) · [실제 512픽셀 로더 검사](preprocessing_link_check.md#새007009의-입력-품질과-검수본-재사용--2026-09-21). 아래는 이전 실행 이력이며 전체 스위트 재실행은 아니다.

2026-09-21 최신 검수 기록: `.venv-model-check/bin/python -m unittest discover -s tests -p 'test_segment_reviews.py' -v` — **20개 통과/실패·건너뜀0/0.066초(외부0.139초)**, 상한60초, 첫 시도 통과. JSON/바이트/mock 테스트로 빈답·부분답·판단불가, 정답과품질 분리, 정상 전체구간 확인, ID/순서·구간/원본/미리보기 변경·잘못된 날짜/시각·분할 보존·출력/용량 보호·변조·실제CLI 왕복을 확인했다. 새 테스트에는 실제 교통 영상/모델을 쓰지 않았다. [로그](../artifacts/label-review/received-review-codecheck-01/tests-01.log), [실제 사용자 답변 연결](reviews/2026-09-21_dahye.md). 아래37개 등은 이전 작업 이력이며 전체스위트를 이번에 다시 실행한 것은 아니다.

2026-09-21 최신 소수 수신 검사: `.venv-model-check/bin/python -m unittest discover -s tests -p 'test_inspection.py' -v` — **37개 통과 / 실패·건너뜀0 / 1.387초(외부1.574초)**, 외부 상한60초, 첫 시도 통과. 기존25개에 외부 파일 수신12개를 추가했다. CSV 없는 외부 모드의 정답/그룹 null, 선택 외 파일 미열기, 누락 시 worker 미실행, 경로 이탈/중복/합성 경로 거부, 알려진 ACCIDENT CSV 모드 우회 거부,512MiB·20초 상한, 변조/접근 오류, 손상 파일과 작은 AVI의 실제 부분 디코딩을 확인했다. 새 모드는 생성 테스트 파일로만 검증했고 실제 새 ACCIDENT3개는 기존 CSV 모드로 검사했다. [로그](../artifacts/data-check/file-receipt-codecheck-01/tests-01.log) · [실제 영상 검사와 미리보기](data_inventory.md#17-새로-받은-사고-후보-007009-수신과-부분-검사--2026-09-21). 전체 스위트·사고 정답·모델 성능 검증이 아니다.

2026-09-21 공식 train 후보 검사: `test_train_candidates.py` **18개 통과/0.154초(외부0.329초)**, 재사용 전처리 `test_preprocessing.py` **23개 통과/3.570초(외부3.666초)**. 각각 외부60초 상한, 실패·건너뜀0이며 이번 합계41개다. 신규 테스트는 임시 CSV/파일명과 mock만 사용한다. 공식 test 보호·날씨/충돌 유형을 정답으로 바꾸지 않음·미확보와 오류/점검 제외 구분·그룹 null·경로/출력 보호·중복 행·입력 변조·30초 worker timeout·실제 subprocess 왕복을 확인했다. 원본 교통 영상의 정답이나 모델 성능 검증이 아니다. [명령·로그·실제 목록 점검](data_inventory.md#15-공식-train-후보-목록과-미확보-자료--2026-09-21).

최신 외부 정상 입력 연결: 2026-09-20 `.venv-model-check/bin/python -m unittest discover -s tests -p 'test_*preprocessing*.py' -v` — **52개 통과 / 실패·건너뜀 0 / 6.984초(외부 7.536초)**. 기존 전처리 23+연결 12+신규 외부 연결 17개다. 전체 스위트가 아닌 관련 테스트만 실행했다. 신규 테스트는 실제 교통 자료가 아닌 작은 색상 AVI·검토 MP4와 mock을 사용하며 원본/검수 해시, 구간 불일치, 미확정 정답, 누락/손상/짧은 영상/잘못된 시간, PTS·PNG 대조, 범위/경로/출력 보호, timeout을 검사한다. 로그의 `supervisor_timeout` JSON은 의도한 오류 시험 출력이다. [실제 영상 연결 결과](preprocessing_link_check.md#qmul-외부-정상-구간-입력-연결--2026-09-20)는 별도다.

최근 전체 스위트 기록: 2026-09-19 **80개 통과 / 9.073초**. 2026-09-20에는 관련 전처리·연결 검사만 **35개 통과 / 실패·건너뜀 0 / 3.883초**로 확인했다. 신규 연결 테스트는 12개이며 전체 스위트는 이번에 재실행하지 않았다. 코드 동작 확인이며 사고 인식 정확도 검증이 아니다. [상세 결과와 초기 실패 이력](preprocessing_link_check.md)을 참고한다.

2026-09-20 후속 C-1 비용 점검: `test_local_model*.py` **21개 통과 / 실패·건너뜀 0 / 0.995초**, 외부 상한 60초. 작은 CPU 모델/mock으로 안전 장치·계측 흐름을 확인했으며 실제 R3D-18 MPS 실행은 별도 로그다. [조건과 결과](local_model_check.md)를 참고한다.

## 파일별 역할

| 파일 | 개수 | 확인 내용 |
|---|---:|---|
| `tests/test_segment_reviews.py` | 41 | 기존 판정 기록 20 + 입력 품질 기록 16 + 읽기 전용 준비 확인 5: 파일·정답 보존·상태/종료 코드·오류·CLI 왕복 |
| `tests/test_inspection.py` | 37 | 기존25개: 메타정보 연결·누락·손상·단위·일부 디코딩. 추가12개: 외부 수신·미확정 보존·경로/용량/시간 보호·CSV 우회 거부 |
| `tests/test_train_candidates.py` | 18 | 공식 train 후보 최대3개·두 test 분할 보호·라벨/그룹 미확정·누락/미선택 구분·경로/중복 행/출력/시간 상한 |
| `tests/test_preprocessing.py` | 23 | 라벨 연결·미확정 보존, 짧은 영상·시간 오류, PTS 순서·경계·화면 가장자리, 출력 보호 |
| `tests/test_preprocessing_link.py` | 35 | 기존 연결 12 + PNG 재사용 10 + 명시적 크기 변경 13: ID·PTS·정답·표시/입력 순서·변조·미응답·실행 상한 |
| `tests/test_external_preprocessing.py` | 17 | 외부 AVI+검수 기록 연결, 기존 로더/정상 라벨 0, 미확정·에러 분리, 원본 PTS/PNG 검증, 경로·해시·출력·시간 상한 |
| `tests/test_local_model_check.py` | 8 | C-1 입력/step 제한, 자동 CPU 전환·메모리 보호 해제 금지, MPS 미지원·출력 보호 |
| `tests/test_local_model_cost.py` | 13 | 224/head 예산 제한, 동기화 순서, 준비/업데이트 분리, 고정 상태 변조·오류·출력·시간 상한 |
| `tests/test_training.py` | 24 | 라벨·그룹·분할 검사, 입력 변환, 분류층 학습, 저장·재개, 실행 제한 |

Python 표준 unittest로 **작은 입력 준비 → 함수 실행 → 기대 결과와 비교**한다. 오류도 예상된 종류로 발생해야 통과한다. 누락 파일에서 정상 0을 반환하면 통과가 아니다.

## 안전 장치와 분류층 테스트를 쉬운 말로 설명하면

| 용어 | 의미와 테스트에서 확인한 것 |
|---|---|
| C-1 입력 제한 | 무작위 영상 한 묶음, 16장, 112×112, float32로만 작은 연산을 검사한다. 실수로 큰 입력·사전학습 다운로드·실제 학습을 시작하는 설정을 거부한다. |
| step 제한 | 입력 → 오차 계산 → 역전파 → 가중치 수정의 한 번을 1 step이라고 한다. C-1은 1~3번만 허용하며 4번 요청은 거부한다. 새 C-2 학습 코드의 20 step/300초 한도와 별개다. |
| 자동 CPU 전환 금지 | Mac GPU(MPS)를 요청했는데 지원하지 않는 계산이 있으면, 숨겨서 CPU로 바꿔 성공처럼 보이지 않게 한다. 필요하면 CPU를 직접 지정한 별도 실행으로 확인한다. |
| 메모리 보호 해제 금지 | 메모리를 지나치게 쓰지 않도록 둔 PyTorch의 제한을 유지한다. 오류를 없애려고 제한을 없애거나 올리는 환경변수 설정을 거부한다. |
| MPS 미지원 처리 | GPU가 사용 불가인 상황을 테스트에서 흉내 내고, 모델 계산 전 명확하게 실패하는지 확인한다. 이 테스트가 현재 M1 Max에서 MPS가 안 된다는 뜻은 아니다. 실제 C-1 MPS 실행은 성공했다. |
| 출력 보호 | 원본 데이터 위치에 결과를 쓰거나 이미 있는 결과 파일을 덮어쓰는 요청을 거부한다. 이전 기록을 보존하려면 새 결과 이름을 사용한다. |

CPU 전환 및 메모리 제한 환경변수의 의미는 [PyTorch 2.10 MPS 문서](https://docs.pytorch.org/docs/2.10/mps_environment_variables.html)와 대조했다. 각 단위 테스트는 금지된 설정이나 가짜 미지원 상태를 넣어 안전 장치가 작동하는지 검사한다. 8개의 C-1 단위 테스트가 GPU 학습 8회라는 뜻이 아니다.

`fc`는 영상에서 얻은 특징을 최종 정상/사고 점수로 연결하는 마지막 분류층이다. 특징 추출부는 모양·움직임에 관한 숫자 표현을 만드는 앞부분이다. `head` 학습은 앞부분을 유지한 채 마지막 분류층의 연결 가중치만 바꾸는 방식이다.

BatchNorm은 모델 내부 특징의 평균·분산 통계를 보관한다. 앞부분 가중치만 학습 금지로 설정해도, 학습 모드에서는 이 통계가 바뀔 수 있다. 그래서 앞부분을 `eval()`로 두어 통계도 유지한다. 이는 입력 RGB 채널 정규화와는 별개다. [PyTorch 2.10 BatchNorm3d 문서](https://docs.pytorch.org/docs/2.10/generated/torch.nn.BatchNorm3d.html)

작은 CPU 모델의 학습 전후 값을 비교해 **fc의 가중치는 바뀌고, 앞부분 가중치와 BatchNorm 통계는 그대로인지** 확인했다. 실제 사전학습 R3D-18로 사고를 학습했다는 뜻은 아니다. C-1은 이 테스트와 다르게 무작위 112픽셀 입력으로 전체 층 역전파·업데이트를 확인했으며, 새 코드의 사전학습 224픽셀 입력 경로까지 검증한 것은 아니다.

## 새 학습 테스트

| 시험 | 완료 조건 |
|---|---|
| 현재 실제 자료 | 라벨 0개에서 held, 디코딩·모델·다운로드 시작 안 함 |
| 미확정 라벨 | null 유지, 정상 0으로 대체 안 함 |
| 원본·사건·그룹·내용 중복 | 분할을 넘는 같은 ID/해시 차단 |
| 공식 test | train 이동 차단 |
| 외부 정상 자료 | 없는 공식 분할을 만들지 않고 별도 검수 요구. 기존 test 우회 불가 |
| 승인 범위 | plan/전처리 해시 변경·그룹 미확인 차단 |
| 구간 준비 | train 두 구간만 디코딩. 미리보기·원본 ID/경로·라벨 근거 보존 |
| 입력 변환 | 역정규화·축 복원 시 원래 픽셀과 시간순서 일치 |
| 누락·손상·순서 역전·시간 초과 | 명시적 오류. 자동 정상 판정·조용한 건너뛰기 없음 |
| 분류층 학습 | fc는 변하고 특징 추출부 가중치·BatchNorm 통계는 유지 |
| 저장·추론 | 재구성한 작은 모델 출력과 저장 직전 출력 일치 |
| 재개 | 2 step 연속과 1 step→저장→재개 1 step 결과가 정확히 같음. SGD momentum 복원 |
| 변경된 자료·클래스 | checkpoint 재개 거부 |
| 시간 제한 | 시작 전/역전파 후 기한 소진 시 업데이트 없음. 프로세스 강제 시간 제한 확인 |
| 공식 가중치 로더 | 누락 시 다운로드 안 함. 로딩 후 fc 교체 순서는 mock으로 확인 |
| 실제 R3D-18 구조 | 다운로드 없이 구조만 생성, head 학습 파라미터 1,026개와 BN eval 확인 |

임시 폴더에 짧은 색상 영상과 테스트 라벨을 만들며 실제 ACCIDENT 정답이 아니다. 학습 연산 테스트에는 Conv3d·BatchNorm·fc로 구성한 작은 CPU 모델을 사용한다. 실제 R3D-18은 이번에 구조·고정 설정만 검사했고 새 순전파·학습은 하지 않았다.

공식 가중치 실제 로딩, 224픽셀 실제 입력 학습, MPS checkpoint 재개, Colab 실행은 미검증이다. C-1의 MPS 난수 1 step과 이번 단위 테스트를 구분한다.

## 실행

프로젝트 루트에서 모델+영상 패키지가 있는 환경으로 실행한다.

```bash
.venv-model-check/bin/python -m unittest discover -s tests -v
```

새 학습 코드만 확인하려면:

```bash
.venv-model-check/bin/python -m unittest discover -s tests -p 'test_training.py' -v
```

끝의 `Ran ... tests`와 `OK`를 확인한다. 80개는 신규 연결 테스트 추가 전 이력이며 학습 파일만 실행하면 24개다. 이번 명령 `-p 'test_preprocessing*.py'`는 35개였다. FAILED는 실패, skipped는 실행하지 못한 검사다. 건너뛴 검사는 실제 검증으로 세지 않는다. torch가 없는 `.venv-inspection`은 학습 테스트 모듈을 건너뛰므로 전체 검증에는 모델 환경을 사용한다.

최종 로그는 `artifacts/training-code-check/tests-03.log`이다. 초기 74개는 `tests-01.log`, 중간 78개는 `tests-final.log`, 앞선 80개 통과는 `tests-02.log`로 보존했다. 마지막 재실행은 잘못된 음수 momentum의 설정 단계 거부를 보완한 뒤 수행했다.

테스트가 통과해도 구간에 실제 사고가 보인다는 뜻은 아니다. [미리보기 확인](preprocessing_review.md)은 사람이 수행해야 한다.
