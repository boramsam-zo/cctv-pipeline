# C-2 첫 학습 코드와 실행 결과

## 2026-09-27 — X-CLIP zero-shot 평가 준비 · 추가 학습 없음

사용자가 모델선정 후 튜닝/학습을 하려는 방향에서 X-CLIP 첫시험을 요청했다. [실행 안내](../artifacts/colab/xclip-sotad-dev236-20260927-01/README.md), [Colab 노트북](../artifacts/colab/xclip-sotad-dev236-20260927-01/evaluate_xclip_sotad_colab.ipynb), [작은 코드 ZIP](../artifacts/colab/xclip-sotad-dev236-20260927-01/xclip-sotad-code.zip), [개발검증 영상 ZIP](../artifacts/colab/xclip-sotad-dev236-20260927-01/SO-TAD-dev236.zip)을 준비했다.

공개 `microsoft/xclip-base-patch16-zero-shot` revision6746d6a…/32프레임/224중앙crop, 고정6문장·클래스별최대값 비교·동점정상·영상any-positive. 공식train에서 미리분리한 개발236(사고32·정상204), 총4106창만평가한다. 새학습·프롬프트탐색·공식test479사용0. 기존R3D학습준비안/원본631은보존한다.

기존Drive train원본/학습ZIP이있으면그대로재사용한다. 없으면약2.85GB의새개발ZIP을Drive datasets폴더에업로드하고노트북코드셀하나를실행한다. 최초Colab가중치다운로드약780MB/Colab전용패키지설치. 오류시에도로그와결과ZIP저장, 동일코드·모델·목록·장비/환경에서완료원본재개지원. 실제소요시간은미측정.

준비검사17개와실제2원본32프레임/PTS·ZIP검사통과. **제공 코드만완료이며실제Colab모델로딩/추론/성능/가중치불변/재개는미실행**이다. 반환ZIP접수후실제결과로기록한다. 기존R3D479/동료X3D400과목록/입력방식이다르며직접우열·운영합격을판정하지않는다. 원본수치근거는준비폴더의data-package-receipt/bundle-verification/verification JSON, 실행명령·이유는PROGRESS의같은날짜항목을따른다.

## 2026-09-23 — Colab 확대400구간 파일 연결 셀 구현

사용자후속진행승인으로기존train_candidates.py에colab_expansion_request/inspect_colab_expansion을추가했다. 기존200사고후보를재선정하지않고공식Train60정상ID중정렬앞50개×[0,2),[2,4),[4,6),[6,8)의4구간=200개를연결한다. 뒤10ID는예약하며공식Test40은제외한다. 이는실행목록준비제안이지독립카메라분할이아니다. 사고개발검수20예약은그대로유지하고새학습후보와family교집합을거부한다. 기존학습33개를이번400목록에자동합치지않는다.

사고target1/source_annotation_derived,정상target0/user_assumed_normal,human_reviewed=false. split=unassigned/training_authorized=false/evaluation_authorized=false,두자료의원본·사건관계미확인보수묶음을유지한다. 실제CSV분할/행/시점/길이를Colab에서대조하고,MP4존재와ZIP의필요50프레임이름만확인한다. 파일누락은blocked,존재는file_connected_not_decoded로표시하며디코딩완료로표현하지않는다. 이미지/모델/학습0.

기존 [DETRAC 노트북](../notebooks/prepare_detrac_inputs_colab.ipynb)의9단계만실행한다. 1~8단계반복불필요. [작은코드ZIP](../artifacts/colab/expansion-preparation-20260923-01/expansion-preparation-code.zip) 24,202bytes/4파일(코드3+요청1),원본/모델/토큰없음. 사용자PC파일선택으로Colab에작은코드만전송하며9.17GiB ZIP을복사/전체해제하지않는다. Drive원본경로는기존사용자경로이며존재하지않으면명시적으로중단한다.

상한: Colab자식명령60초(내부55초검사),디코드0,MP4최대200파일존재확인/ZIP최대200000항목/정상200구간의이름검사. Drive마운트/코드전송/결과Drive저장은상한·측정과별도다. 전처리배열본문추정은400×16×224×224×3bytes=약919MiB이나이번에는생성하지않는다. 디코딩처리량·시간/학습epochs/시간상한은실제연결결과를받은뒤정한다.

실행한테스트: `PYTHONPATH=src .venv-model-check/bin/python -m unittest discover -s tests -p test_train_candidates.py -q` →32개/0.162초/OK. 새가상자료시험은후보수/예약분리/라벨출처/공식test거부/누락과파일연결을검사하며실제영상·torch없음. 노트북신규셀ast.parse통과/고정요청400개생성확인. 코드ZIP SHA256 89b3e0b352c13e3ec36fa3d252457cebb31d5f9d1ec0f5d70a39dcf4566238b7. 실제Colab9단계/400입력디코딩/학습미실행,사용자확인대기.

다음하나는사용자file_connection.json을받아누락을구분하고제한된실제Colab전처리명세로연결하는것이다. 정상소수가정검수를다시요구하지않으며이미준비한동일구간배열은후속에서재사용대조한다. 이번코드는학습코드가아니므로GPU필요없다.


## 2026-09-22 — UA-DETRAC 다운로드 대기 중 확대 후보 목록 구현

사용자는 UA-DETRAC 다운로드 중이며 다른 준비 작업 진행을 요청했다. 다운로드 완료/파일 내용/이용 조건/정상 라벨은 아직 미확인이다. 로컬 모델 학습·추론 금지를 유지했고 이번에는 기존 CSV와 저장목록만 읽었다. 별도 검사 도구 대신 기존 `train_candidates.py`에 `plan_expansion`을 추가했다.

입력: 기존 metadata-real.csv, 실제33구간 prepared_inputs.json, 기존 evaluation.colab.reference.json. 처리: 모델점수 없이 CSV순으로 개발검수 예약후보20개를 먼저 선택하고 추가학습후보70개를 선택한다. 기존 bounded selector를25개이하씩 재사용해 공식IIDtest·기존학습·기존평가예약·해당파일명family 힌트를 제외한다. 공식test와같은family 힌트도추가차단한다. 파일명family는사건ID가아니고 알려지지않은재편집관계는검출하지못한다.

실제 결과: 추가학습후보70/개발검수예약후보20/수량부족0, 메타정보처리0.028890초. 영상파일존재·재생·모델실행0. [후보 목록](../artifacts/training/expansion-intake-20260922-01/expansion-plan.json)은 기존 intake 폴더에 저장해 과거목록을보존했다. 출처파일/코드SHA를함께기록했다. 90개는실제학습완료나90독립사건이아니다.

모든후보 `target=null`, `split=unassigned`, training/evaluation_authorized=false, event_group=null이다. 개발검수예약은계획상제안이며기존학습명령이이새파일을자동소비하지않는다. **다음실행명세 연결시20개후보및같은원본그룹의학습제외를명시적으로반영해야하며**, 그전까지이90개추가학습은금지다. 독립성을확인하지못한채무작위80:20분할로확정하지않는다. 사고학습라벨은연결단계에서기존CSV-point정책을활용하고 개발평가정답은검수/독립성확인과분리한다.

변경: `src/boram3_cctv/train_candidates.py`, `tests/test_train_candidates.py`. 시험명령 `PYTHONPATH=src .venv-model-check/bin/python -m unittest discover -s tests -p test_train_candidates.py` →22개/0.123초/OK. 신규4시험은선정재현성·역할간중복차단·test가족/기존학습/예약제외·잘못된시각/부족분/범위오류를검사한다. 테스트는작은가상CSV이며영상/torch실행없다. 실제실행은Python API `plan_expansion(pp.read_metadata(metadata_path), json.loads(training_path.read_text()), json.loads(reserved_path.read_text())["segments"])`로기본70/20을사용했다.

준비작업완료/사용자확인대기. 다음하나는 다운로드된UA-DETRAC의폴더구조·시퀀스ID·프레임순서/FPS·공식분할·출처/사용조건을연결하여정상검수후보를만드는작업이다. 소수서로다른촬영원본부터선택하며차량검출용주석을정상라벨로바꾸지않는다. 사용자에게Drive저장경로와압축해제후최상위목록을받고Colab에서제한처리한다. 학습실행상한/최종정상수/사건분리미확정; 새학습/추론/업로드/추가다운로드0.

Colab33구간평가는 사용자 제공결과에서 completed/3예측/FP1/FN0/루프0.729347초/가중치BN유지true를확인했다. 이를다시실행하지않았으며다른원본일반화는여전히미확인이다.


## 2026-09-22 — 사용자 Colab 실행 전용 요청·33구간 모델 평가 코드 공유

앞으로 모델 학습/추론은 Colab에서 실행하고 Mac은 코드/기록/전송묶음 준비만 수행한다. 이번 로컬 모델연산0/추가학습0/업로드0. 기존 평가노트북의 기본ZIP을04로수정했고, 실제33구간모델·평가3입력·기존공통모듈을포함한19파일+영수증 묶음을 만들었다. 기존03과결과를보존한다. 이ZIP은평가용이며 원격Colab8구간모델평가나 추가학습 코드가 아니다.

- [평가 노트북](../notebooks/evaluate_r3d18_colab.ipynb)
- [평가 ZIP04](../artifacts/colab/evaluation-20260922-04/colab-evaluation.zip): 143,383,443bytes, 약143.4MB. 원본영상/환경/캐시/토큰 제외, 저장모델·검수자별칭·3입력 포함.
- Drive 업로드 위치: `boram3-cctv-evaluation/incoming/evaluation-20260922-04/colab-evaluation.zip`. Drive에서 압축해제하지 않는다.
- Colab GPU 선택→노트북1~7단계순서실행. 3단계 `CONFIRM_TRANSFER=True`는사용자가전송내용확인후변경한다. 6단계평가최대3구간/루프300초, 모델준비별도120초. 7단계출력은`results/reference-<UTC시각>/report.html`와JSON/미리보기다.
- 코드공유: `src/boram3_cctv/evaluation.py`의run_evaluation을호출하며노트북에평가로직을복제하지않는다. 기존 `notebooks/prepare_training_inputs_colab.ipynb`는별도8구간학습용이고이미완료된40업데이트를재실행하도록안내하지않는다.
- 실행한명령: `.venv-model-check/bin/python scripts/prepare_colab_bundle.py --manifest artifacts/training-review/t9-reference-20260922-01/evaluation.json --output artifacts/colab/evaluation-20260922-04`.
- 검증: 노트북JSON/비매직Python셀구문확인,ZIP19개허용파일/크기/SHA256대조통과. 새모델forward/단위모델시험/Colab실행미실행. 실행준비완료와사용자실행완료를구분한다.

다음은Colab에서현재33구간모델을같은3입력으로참고비교하고출력을공유하는작업이다. 독립성미확인·정상부족상태와추가학습별도승인을유지한다.


## 2026-09-22 — 확대 전 33구간 모델 참고 비교·학습 풀 배정

사용자 후속 진행 요청에 따라 기존 로컬33구간 모델을 예약3구간으로 **CPU 명시 실행**했다. 원격 Colab8구간 이어학습 모델은 수신하지 않았으므로 이번 대상이 아니다. 기존 평가 코드와 검수 배열을 재사용했고 전처리·판정·모델·원본·기존 결과를 변경하지 않았다. 새 결과 경로는 이전 실험 보존을 위한 것이며 새 검사 도구를 만들지 않았다.

| 구간 | 정답 | 이전5구간 모델 사고 점수 | 이번33구간 모델 사고 점수 | 이번 판정 |
|---|---|---:|---:|---|
| devval_accident_01 | 사고 | 0.521091 | 0.986133 | 사고·맞음 |
| devval_accident_02 | 사고 | 0.648433 | 0.997040 | 사고·맞음 |
| devval_normal_01 | 정상 | 0.497496 | 0.983239 | 사고·오탐 |

이전 점수는 사용자 제공 Colab 결과 파일을 재사용했다. 이번3개 모두 예측 성공, FP1/FN0, Accuracy/사고Precision=0.6667, Recall=1.0, F1=0.8, 최종 고정모델 평균 CE=1.368538. **독립성 미확인 참고 비교이며 서비스 성능이 아니다.** 두 실제파일 중F0에서정상/사고를함께추출했다. 기존학습목록33개와 알려진파일/그룹중복은 차단했으나 상위원본/재편집/사건 독립성은미확인이다. 초기화·자료·장치가다른모델비교로, 학습량만의 인과 실험은 아니다.

실측: 추론루프3.861363초, 자식시작~종료6.285423초, 평가준비~보고서저장7.036396초. 마지막 역사비교표를 HTML에 덧붙인 시간·문서작성·테스트는7.036396초에 포함하지 않는다. CPU는 처음부터 명시했으며 MPS 실패 후 자동 전환하지 않았다. eval/추론 전용, 모델/BN 유지 confirmed, 추가학습0. 사용자 확인은 pending.

기존 CSV와 실제33입력을 대조한 배정표: 공식IIDtest1,520 제외, 이미학습한 사고원본30, 평가예약2, 미배정IIDtrain475 = 총2,027행. 475는 실제재생/독립성검수/학습승인이 끝난 목록이 아니다. 정상 학습은QMUL1원본3구간뿐이다. 기존학습원본을검증으로옮기거나 미배정475개를 독립원본이라고 가정하지 않는다. CSV 행번호는 헤더포함2부터다.

[미리보기·예측·신구비교 보고서](../artifacts/training-review/t9-reference-20260922-01/run/report.html), [배정표](../artifacts/training-review/t9-reference-20260922-01/expansion-allocation.csv), [고정 실행명세](../artifacts/training-review/t9-reference-20260922-01/evaluation.json), [측정 영수증](../artifacts/training-review/t9-reference-20260922-01/run/timing.json).

실행 명령(프로젝트 루트, 기존 결과에는 재실행 금지):
```python
# PYTHONPATH=src .venv-model-check/bin/python 에서 실행
from pathlib import Path
from boram3_cctv.evaluation import run_evaluation
run_evaluation(Path.cwd(), "artifacts/training-review/t9-reference-20260922-01/evaluation.json",
               "artifacts/training-review/t9-reference-20260922-01/run", device="cpu", execution_location="local")
```
관련 시험: `PYTHONPATH=src .venv-model-check/bin/python -m unittest discover -s tests -p test_evaluation.py` →38개/4.250초/OK. 생산코드 변경 없음. 기존 평가 및 strict 독립성 검사를 재사용했다.

**다음 하나:** 정상 자료 다양성과 그룹 근거를 확보해 확대 실험의 train/dev 목록을 고정한다. 학습 사고100구간 정도와 정상도 비슷한 규모는 출발안이며 수 자체가 충분함을 보장하지 않는다. 현재30사고에서100으로 늘리려면 추가70구간이 필요하지만, 정상3개 그대로 사고만 늘리는 실행은 보류한다. 우선 QMUL과다른 촬영원본2개 이상과 출처·정상으로검수가능한시간범위를 제공받고, 그 중 학습/개발검증 용도는 원본별로 분리한다. 이는 최소 착수자료이지 충분한 서비스 검증수는 아니다. ACCIDENT 내부 정상 구간도 사람이 확인하면 후보가 되지만 예약F0/Xaq와 학습용 원본을 섞지 않는다. 사고 주석은 기존 CSV 정책으로 파생하고 모든 사고의 육안 재승인을 요구하지 않는다. 약80:20은 미사용·학습허용 풀의 그룹분할 출발안으로만 사용한다. 사건근거가 없으면 독립검증 미확인을 유지한다.

Precision/Recall 각90%는 목표 제안으로만 남긴다. 서로다른원본의 고정개발검증목록, 표본수와 오탐/미탐·처리시간을 함께 정한 뒤 확정하고, 작은3개 결과로 달성 여부를 선언하지 않는다. 시간규칙통일(양클래스2초16장)은 별도 한변수 개선후보로 남기며 이번에 수정/재학습하지 않았다. 확대학습은 실제구간수·측정추정시간·실행상한을 제시한 뒤 승인받는다.


## 2026-09-22 — Colab T4 이어학습 완료 로그 수신·서비스 목표 확대 문의

사용자가 `training-results/colab-continue-20260922T110615444919Z`의 출력과result JSON을메시지로제공했다. 다음은**사용자제공실행로그에서확인한값**이며에이전트가Drive모델파일을다운로드/재실행해확인한것은아니다. 기존로컬33구간실험과합치지않는다.

| 항목 | 사용자 제공 Colab 실행 결과 |
|---|---|
| 장치/초기화 | Tesla T4/CUDA, 기존2026-09-21모델 SHA2b0c4818… 유지·새optimizer |
| 데이터/업데이트 | 사고5·정상3, 사람검수5/CSV파생3, 5epoch·40업데이트(3+37) |
| 성공/보존 | completed_limited_real_training, frozen_state.unchanged/resume_verified/reload_verified=true, fc weight최대변화0.0356579/bias0.00295822 |
| epoch별 학습 평균loss | 0.260946 / 0.225765 / 0.163007 / 0.101751 / 0.077042, 각8step. 학습중모델손실이며독립검증/정확도아님 |
| 시간 | 누적루프31.563606초, worker38.349455초. Drive준비/복사·셀대기포함전체시간은미제공 |
| 기록된 단계별 합 | input1.958674초, compute3.806084초, 매step checkpoint25.034744초. 초기/최종저장등은이세합외범위가있음 |
| 메모리 | 재로딩포함 CUDA최대allocated560,036,352bytes(534.09MiB)/reserved650,117,120bytes(620MiB), RSS관측최대1,470,541,824bytes(약1.37GiB) |
| 환경 | torch2.11.0+cu128/torchvision0.26.0+cu128/Python3.13.15/NumPy2.1.3/Pillow11.3.0, PyAV미설치·저장배열실행 |
| 원격 모델 | 위Drive실행폴더의 loop/model.pt. 사용자저장완료출력수신,모델파일원문/최종파일해시는미수신 |

**확대 가능성과 현재 한계:** 사용자는가벼운시연에머무르지않고약2400개보유영상의많은부분으로서비스모델을만들고싶다고문의했다. 현재실제CSV는2,027행/IIDtrain507·test1,520이며,기존예약F0/Xaq2파일제외505행이학습허용후보다. 이는기존학습자료포함수·후보수이며새로검증된505개가아니다. 2400개와차이는새파일추가/다른자료/추정치등어느것인지미확인이다. 전체파일재검색이나새다운로드는하지않았다. 기존IIDtest는학습에옮기지않는다.

지금은head학습경로가검증된단계이며서비스완료기준은다양한정상환경의오탐,사고미탐,원본그룹분리검증,시간당오경보·처리지연을별도로측정하는것이다. 정상QMUL1원본을고정한채사고수만늘리면출처/클래스불균형한계가커진다. 사고전/미주석자동정상화는하지않으며추가정상자료확보를병행한다. 학습허용자료내원본그룹약80:20 분할은출발제안이고기존예약/공식test를보존한다. 원본관계가미확인이면독립검증이라고표시하지않는다.

**시간은 추정만:** 동일T4/head/16장224/배치1/현재매step저장·준비된배열이라는가정에서31.563606초/40step을단순환산하면,총100구간5epoch약6.6분/총200약13.2분/총500약32.9분이다. 새영상전처리/Drive복사/평가/환경변동은제외하며완료보장·실행상한이아니다. 전체특징부역전파시간/메모리로외삽하지않는다. 저장빈도개선은모델을가볍게만드는것과별개이며매epoch/정해진주기·종료시저장으로바꾸는안을제안하되이번수정/실행0이다.

**다음 제안:** 기존33구간학습을반복하지않고학습허용후보에서사고100구간규모의명시목록과추가정상/개발용검증원본을연결하는확대계획을준비한다. 예약3구간참고비교는현재모델간변화확인용이고독립서비스평가를대체하지않는다. 이후한가지씩데이터규모→시간샘플링일치→필요시마지막특징블록학습을검토하며동시에모두변경하지않는다. 이번문의만으로대량처리/장시간학습/새다운로드/전체층학습을승인받은것으로쓰지않는다.

## 2026-09-22 — T9 확대33구간 실제 학습 완료

**오늘 승인한 작업 묶음은 실제 학습까지 완료했다.** 기존 사고008~012와 QMUL정상3을 유지하고 T9에서 새 사고25개(sample_013~037)를 추가했다. 모든 새 후보가 입력 생성에 성공했고 준비 실패0이다. 원본 전체를 메모리에 올리거나 전체프레임을 추출하지 않았다.

| 항목 | 실제 결과 |
|---|---|
| 입력/정답 | 사고30·정상3=33구간. 사람검수5 / CSV 사고시점 파생28 |
| 원본/분할 | 파일SHA31개, 독립 사건수 미확인. 정상3개는QMUL 한 원본. 사고관계미확인 묶음은train전용; IIDtest/F0·Xaq 평가예약 제외 |
| 새 입력 처리 | T9 새25파일, 각 사고시점±1초·16장224. 400장 저장, 오류0. 후보와 실제 처리목록 고정 |
| 모델/초기화 | R3D-18, 공식 KINETICS400_V1 + 같은seed의새fc. 이전 프로젝트모델 이어학습 아님 |
| 유지한 학습 설정 | head만, MPS, batch1, float32, workers0, 학습률0.001. 특징부/BN 유지. 정상6초/사고2초 유지 |
| 학습량 | 5epoch·165업데이트, probe3 + 재개162. 최대165/300초 안에 완료 |
| 시간 | 준비24.777256초 + 학습 명령109.564782초 =134.342038초. 누적학습루프103.937406초 |
| 메모리 | 최고RSS930,480,128bytes. 기록된 MPS tensor/driver 최대143,953,664/1,143,619,584bytes. 겹치는 지표이므로 합산하지 않음 |
| 저장/확인 | fc 최대변화 weight0.106997/bias0.004566, 특징부/BN지문동일. 체크포인트재개/최종모델재로딩[1,2] 일치 |
| 아직 하지 않은 것 | 새 모델 예측 비교·독립검증·사용자 보고서 확인·300파일학습·Colab/업로드 |

**실제 손실:** unweighted cross_entropy, 배치1. 전체평균은각epoch33개/사고평균30개/정상평균3개 실제step의평균이다. 그때마다가중치가달라지므로최종모델고정손실/검증손실이아니다. 정확도로환산하지않는다.

| epoch | step | 전체 평균loss | 사고 평균loss | 정상 평균loss |
|---|---|---|---|---|
|1|1–33|0.226733|0.156333|0.930727|
|2|34–66|0.577924|0.101054|5.346622|
|3|67–99|0.265810|0.083436|2.089554|
|4|100–132|0.065437|0.024421|0.475604|
|5|133–165|0.009432|0.006584|0.037907|

2epoch의정상손실상승도그대로보존했다. 사고30:정상3 불균형은일반도로나정상오탐문제가해결됐다는뜻이아니다. 최종손실이낮아도이전8구간모델보다새영상성능이좋다고아직말할수없다.

실제 명령(완료된출력경로이므로재실행불필요,기존출력덮어쓰기거부):

```bash
PYTHONPATH=src:tests .venv-model-check/bin/python -m unittest \
  test_training test_training_readiness test_preprocessing \
  test_train_candidates test_segment_reviews -v

# T9의 사용자가 지정한 accident 폴더를 읽기 전용으로 사용
ACCIDENT_ROOT='/path/to/data/한화미니프로젝트/accident'
.venv-model-check/bin/python scripts/preprocess_samples.py \
  --data-root "$ACCIDENT_ROOT" \
  --training-expansion configs/train_expansion.t9.json \
  --output artifacts/training/t9-expansion-20260922-01/prepared

.venv-model-check/bin/python scripts/train_r3d18.py \
  --config configs/train_r3d18.t9-expansion.json \
  --output artifacts/training/t9-expansion-20260922-01/run-mps
```

부모프로세스가준비300초/학습300초상한을각각감독했다. 학습은실제호스트MPS접근을사용했고CPU자동전환/새설치/다운로드는없다. 3step측정의남은루프102.936681초는추정값,위시간은실측이다. 전체명령은준비/가중치로딩/학습/저장/재로딩을포함하고개발/시험/보고서/권한대기는제외한다. MPS메모리는기록시점최대이며시스템전체최고값은아니다. 기존8GiB메모리보호·step30초지연제한을유지했다.

확인 순서: [보고서](../artifacts/training/t9-expansion-20260922-01/report.html)에서 새/기존입력과정답출처 →33개미리보기/원본시각 →165step/클래스별손실 →시간/메모리/저장을본다. 코드/실행확인은완료,사용자직접확인은pending이다.

- [새 모델](../artifacts/training/t9-expansion-20260922-01/run-mps/loop/model.pt), [재개 체크포인트](../artifacts/training/t9-expansion-20260922-01/run-mps/loop/checkpoint.pt).
- [실제33입력](../artifacts/training/t9-expansion-20260922-01/run-mps/prepared_inputs.json), [실행 결과](../artifacts/training/t9-expansion-20260922-01/run-mps/result.json), [학습 요약](../artifacts/training/t9-expansion-20260922-01/learning-summary.json).
- [선정·제외 근거](../artifacts/training/t9-expansion-20260922-01/prepared/selection.json), [25파일 처리범위](../artifacts/training/t9-expansion-20260922-01/source-processing-summary.json), [보존 확인](../artifacts/training/t9-expansion-20260922-01/preservation-after.json).
- [학습설정](../configs/train_r3d18.t9-expansion.json), [학습목록/실행승인](../configs/train_segments.t9-expansion.json), [추가 입력 준비범위](../configs/train_expansion.t9.json).

**다음 하나:** 현재예약3구간에서같은2초16장224/argmax로이번모델과이전모델의참고비교. 최대3구간·추론5분으로제안하며이번미실행이다. 사고파일이나정상파일을자동추가하지않는다. 정상다양성보강과시간규칙통일은이비교후정할후속선택이며한번에모두바꾸지않는다.

## 2026-09-22 — 승인된 Colab 이어 학습: 기존 노트북 5~7단계

사용자는 Colab 입력 연결 `connected-20260922T105109627033Z`, 사고5/정상3/추가학습없음을 보고했고, 기존model.pt/head 사전3step·60초 후 총5epoch·누적학습5분 제안에 ‘그렇게 해줄래?’라고 승인했다. 같은 준비를 반복하지 않고 기존 준비노트북에 학습/저장 셀을 추가했다. **별도 로컬8구간40step 학습 완료 기록은 보존한다. 그 실행은 공식가중치+새head, 이번은 2026-09-21 기존모델의 head도 보존한 Colab 이어학습이다. 두 결과를 같은 실행으로 합치지 않는다.**

### 사용자가 실행할 순서

1. 작은 [코드 수정 ZIP](../artifacts/colab/training-inputs-20260922-01/code-update-01/colab-training-code.zip)을 Drive `boram3-cctv-evaluation/` 바로 아래 올린다. **35,833bytes(약36KB)**, 코드6개+설정1개+목록영수증. 기존26MB 입력ZIP/평가ZIP/원본은 재업로드할 필요가 없다.
2. 수정한 [기존 노트북](../notebooks/prepare_training_inputs_colab.ipynb)을 사용한다. 이미 열어둔 Colab 사본은 로컬 파일 수정이 자동 반영되지 않으므로 새5~7단계 셀을 기존사본에 복사하거나, 수정본을 업로드한다. GPU 런타임을 선택한다. 같은런타임에서 ROOT가 유지되면5부터, 런타임을 새로 만들거나GPU전환으로초기화됐다면1~4로저장입력을복원한다. 영상재추출은없다.
3. **5단계:** 코드7파일만 임시런타임에갱신한다. 기존 `incoming/evaluation-20260922-03/colab-evaluation.zip`에서 `artifacts/training/limited-real-iid-20260921-01/loop/model.pt` 한파일(132,747,787bytes)을 읽는다. 실제Drive위치가다르면 MODEL_ARCHIVE만수정한다. SHA `2b0c4818f5aa442ce6eff11a240f319e47ae966b52644c9a0c37c851089f5947`를고정하며다른모델로자동교체하지않는다. 외부가중치다운로드0.
4. **6단계:** CUDA확인후기존CLI의 `--colab-continuation`을한번실행한다. 기존8입력과클래스/설정해시고정, 새optimizer/head/lr0.001/배치1/num_workers0/float32, 특징부·BN고정이다. 사전3업데이트는총40에포함하고checkpoint로37개를이어간다. 사전60초/명령전체300초(준비·로딩·학습·저장·재로딩포함)중단감시를두어승인된학습루프5분보다엄격히제한한다. 한step30초/RSS8GiB/CUDA예약메모리8GiB초과는중단한다. CUDA오류시CPU전환/설치하지않는다.
5. **7단계:** 성공·실패기록을 Drive `training-results/colab-continue-…/`에저장한다. 성공시 `loop/model.pt`, `loop/checkpoint.pt`, result/summary/config/versions/class_mapping/prepared_inputs 및 probe/loop학습로그를보관한다. 큰파일은최종모델+체크포인트약266MB만복사하고사전단계모델중복은피한다. 실패시가능한최신체크포인트를보존한다. 실행중손실을step번호로합쳐중복없이표시하고일부epoch는일부라고표시한다.

**완료판정:** `completed_limited_real_training`, 최대40업데이트/5epoch(시간상한이면더적을수있음), `frozen_state.unchanged=true`, `resume_verified=true`, `reload_verified=true`와실제GPU/시간/메모리를확인한다. 원격실행결과와사용자확인은아직미수신이다. 독립검증지표/정확도는산출하지않는다. 정상6초/사고2초·각16장224의기존규칙유지, 정상은QMUL1원본이다.

**코드/시험:** 기존 `train_r3d18.py` worker/감독기·`training.run_loop` 재사용, `build_saved_model`은학습된fc까지복원하고새optimizer를사용한다. 공개Colab진입점은승인모델/입력지문·8목록·CUDA·예산을고정한다. 기존공식초기화/사람전용/독립평가기준은그대로다. 관련87시험통과(새4개): 작은CPU시험모델의3+37업데이트/재개·head변경·BN유지·재로딩,느린사전시험차단,잘못된모델/설정/범위거부,PyAV미설치시저장배열사용을확인했다. 실제R3D학습재실행0/Colab0. [시험시간·명령](../artifacts/colab/training-inputs-20260922-01/code-update-01/tests-result.json), [노트북시험](../artifacts/colab/training-inputs-20260922-01/code-update-01/notebook-check.json).

새파일이필요한이유: 약36KB코드ZIP은이미올린26MB입력/143MB평가묶음을다시전송하지않기위함이다. Colab전용config는공식초기화로실제실행한기존config·승인지문을보존하기위함이다. 노트북과설명문서는기존파일을수정했다. 실제모델/로그만실험별새폴더에보존한다.

## 2026-09-22 — 원본 주석 기반 8구간 실제 학습 완료

**준비만 한 단계에서 실제 학습까지 진행했다.** `metadata-real.csv`의 사고 시점에서 연결한010~012 사고1과 기존 사람 검수5구간을 사용했다. 정답을 모델 예측에서 복사하지 않았으며 CSV 파생 라벨을 사람 검수 완료로 바꾸지 않았다.

| 항목 | 실제 결과 |
|---|---|
| 입력 | 사고008·009·010·011·012 / 정상QMUL001·002·003, 총8구간 |
| 출처 | 사람 검수5 / 원본 사고 시점 파생3 |
| 원본 | 서로 다른 파일SHA6개, 독립 사건 수 미확인. 사고는 보수적 한 관리묶음, QMUL도 한 묶음. 모두train 전용 |
| 초기화 | 첫 실험과 같은 공식 KINETICS400_V1 + 같은 seed의 새2분류층. 이전 프로젝트 모델 이어학습 아님 |
| 유지한 설정 | R3D-18/head만, MPS, batch1, float32, workers0, 16장224 letterbox, 학습률0.001. 정상6초·사고2초 유지 |
| 학습량 | 5epoch·40업데이트. 사전3step + 체크포인트 재개37step, 공통40step 예산 |
| 시간 | 성공 명령 전체30.892121초, 누적 학습 루프25.142250초. 최초장치실패1.918364초까지 합32.810485초 |
| 메모리 | 프로세스 최고RSS868,024,320bytes, 관측 MPS tensor 최대143,953,664bytes/driver 최대1,143,619,584bytes. 서로 겹치므로 합산하지 않음 |
| 확인 | 특징부/BN 지문 동일, fc 가중치 최대변화0.071891/편향0.002606, 저장·재개·최종재로딩 성공 |
| 새 평가 | 미실행. 재로딩 때 학습 입력1개 shape[1,2] 확인은 성능 평가가 아님 |

**손실:** 실제 `cross_entropy`에는 클래스 가중치가 없다. batch1의 각step은 그때의 모델로 계산한 한 구간 손실이다. 아래 평균은 각epoch의 저장된8step 전체 평균이며 최종 모델을 고정한 손실이나 검증 손실이 아니다.

| epoch | 기록된 업데이트 | 평균 학습 loss |
|---|---|---|
|1|1–8|0.819596|
|2|9–16|0.575287|
|3|17–24|0.246234|
|4|25–32|0.147152|
|5|33–40|0.103968|

손실 감소를 정확도 향상으로 환산하지 않는다. 첫5구간 실험과 표본이 달라 평균 손실만으로 우열을 정할 수도 없다. 입력 추가 외에 학습률/모델/시간규칙/임계값은 바꾸지 않았으나, 구간 증가로5epoch의 총업데이트는25→40이다.

**실행한 명령(이미 완료, 재실행 불필요):**

```bash
.venv-model-check/bin/python -m unittest discover -s tests -p 'test_training*.py' -v
.venv-model-check/bin/python scripts/train_r3d18.py \
  --config configs/train_r3d18.source-real.json \
  --output artifacts/training/source-real-iid-20260922-01/run-mps
```

테스트75개 통과(부모7.758초/60초 제한). 학습은 최초3step/60초 비용측정 후 같은 체크포인트로 총5epoch·40step 또는 전체5분 제한 안에서 완료했다. 처음 샌드박스에서는MPS접근 불가로0step 중단했고, 읽기 전용 호스트확인 후 실제MPS로 성공했다. 실패기록은`run/`, 성공기록은`run-mps/`에 각각 보존했다. 보호 제한 해제/자동CPU전환은 하지 않았다. 부모 성공명령 상한298초로 최초실패와 합쳐300초 상한을 유지했다. 시간에는 준비·모델로딩·학습·저장·재로딩이 포함되고 코드작성/시험/보고서/권한대기는 포함되지 않는다.

**직접 확인할 순서:** [보고서](../artifacts/training/source-real-iid-20260922-01/report.html)에서 8입력 미리보기 → 라벨 출처 →40step/epoch손실 → 시간·저장 결과를 본다. 루트에서 `open artifacts/training/source-real-iid-20260922-01/report.html`로 열 수 있다. 이번 결과 사용자 확인은pending이다.

- [새 모델](../artifacts/training/source-real-iid-20260922-01/run-mps/loop/model.pt), [재개 체크포인트](../artifacts/training/source-real-iid-20260922-01/run-mps/loop/checkpoint.pt).
- [실행 결과](../artifacts/training/source-real-iid-20260922-01/run-mps/result.json), [설정](../configs/train_r3d18.source-real.json), [학습 명세](../configs/train_segments.source-real.json), [실제8입력](../artifacts/training/source-real-iid-20260922-01/run-mps/prepared_inputs.json).
- [모델·자료·코드 버전](../artifacts/training/source-real-iid-20260922-01/run-mps/versions.json), [원본 결과 보존/전체 시간](../artifacts/training/source-real-iid-20260922-01/execution-mps.json), [요약](../artifacts/training/source-real-iid-20260922-01/learning-summary.json).

**한계/다음 하나:** 정상 다양성 부족(QMUL1원본), 사고 사건 관계 미확인, 시간규칙 차이를 유지했다. IIDtest·F0/Xaq 비교예약은 학습에 넣지 않았다. 새 모델 독립 평가는 없다. 다음은 동일하게 예약된3구간의 새/이전 모델 **참고 비교**만 제안하며 아직 실행하지 않는다. 기존 Colab 준비ZIP/노트북은 준비전용 그대로이고 이번 학습 결과를 자동 업로드하지 않았다. 아래 새학습0 표시는 과거 이력이다.

## 2026-09-22 — Colab 다음 셀: 기존 QMUL 입력까지 연결

사용자가 CSV 구간 목록3개 생성 성공을 보고했다. 그 출력은 사고 정답 후보와 파일명의 연결 성공이며 새 학습 결과는 아니다. 기존5구간(사고008·009/정상QMUL001~003)과 이미 준비된 CSV 기반010~012를 재사용하는 [준비 노트북](../notebooks/prepare_training_inputs_colab.ipynb)을 제공한다. Drive 원문을 에이전트가 직접 읽은 것은 아니다.

1. [colab-training-inputs.zip](../artifacts/colab/training-inputs-20260922-01/colab-training-inputs.zip)을 Drive의 `boram3-cctv-evaluation/` 바로 아래 올린다. **25,974,355bytes / 약25.97MB(24.77MiB)**, 원본영상·모델 제외. 준비 코드4개, 배열/원본시각/미리보기8세트, 통합목록, 허용목록 영수증이다. 검수자별칭은 포함한다. 새 노트북은 ZIP과 별도 파일이다.
2. Colab의 **파일 → 노트북 업로드**에서 `prepare_training_inputs_colab.ipynb`를 연다. GPU가 필요 없는 CPU 입력 준비다. 기존 평가 노트북을 다시 실행하지 않는다.
3. 셀을 위에서 아래로 실행한다. 이미 생성된 `training-preparation/20260922T094256295860Z/source_annotation_segments.json`과 ZIP을 찾는다. ZIP 해시·허용목록을 검사한 뒤 새 런타임 폴더에만 풀고 기존 로더로 입력8개를 읽는다. 자식 실행상한60초, 새설치/영상추출/모델/학습0.
4. 마지막 `연결 완료: 사고 5 / 정상 3`과 저장 위치를 확인한다. `training-preparation/connected-…/`에 통합목록·사용자 원문 사본·loader-check·연결영수증을 저장한다. 프레임은 ZIP에 있으므로 Drive에 중복 저장하지 않는다. 통합목록의 상대경로는 ZIP을 푼 루트 기준이다.

| 자료 | 구간 / 정답 | 정답 출처 | 기존 입력 시간 규칙 |
|---|---|---|---|
| 사고008·009 | 2 / 사고1 | 사람 검수 | 각2초에서16장 |
| 사고010~012 | 3 / 사고1 | CSV accident_time 파생, 사람검수 아님 | 각2초에서16장 |
| QMUL001~003 | 3 / 정상0 | 기존 사람 검수 | 각6초에서16장 |

224 letterbox·RGB·float32·채널정규화는 기존 로더 그대로다. 정상/사고의 시간규칙 차이를 이번에 바꾸지 않았다. QMUL3개는 원본1개이며, 새사고 사건독립성은 미확인이다. IIDtest/F0/Xaq 평가예약은 포함하지 않았다. `training_execution_authorized=false`를 사용자가 True로 바꾸거나 기존5구간 전용 학습 CLI에 이목록을 넣으면 안 된다. 신규 실행 목적/초기화/상한을 연결하는 것은 다음 구현이다.

**시험 구분:** 관련34시험 통과. 로컬에서 동일 노트북 셀+실제8개 배열+생성된 Colab JSON 형식으로 연결 성공(자식0.801695초). 실제 사용자의 원격 JSON·Colab 실행은 아직 확인하지 않았다. [로컬 실행근거](../artifacts/colab/training-inputs-20260922-01-check/notebook-check.json). 모델연산/학습/성능시험은 아니다.

아래 이력의 ‘CSV21컬럼’은 잘못 적힌 수이며 실제 현재 파일은22컬럼이다. CSV 자체나 주석 값은 수정하지 않았다.

## 2026-09-22 — CSV 원본 사고 주석으로 학습 입력 연결

사용자가 원본 주석 활용을 선택했으므로 새 사고010~012를 매번 육안 승인해야 한다고 요구하지 않는다. 기존 2초·16장224 PNG와 원본PTS를 재사용하고, CSV의 `accident_time`으로 **출처가 명시된 사고 라벨1**을 연결했다. 사람이 검수한 정답과 모델예측은 별개다. 원본 이진정답/사고종료시각이 생겼다는 뜻도 아니다. [정책의 적용범위](label_policy.md)를 따른다.

| 구간 | 원본 | CSV행 / 사고 시점(초) | 기존 입력범위(초) | 연결 정답 / 출처 |
|---|---|---|---|---|
|010|UarP8qU1S-c_00.mp4|4 / 8.667|[7.667,9.667)|1 / source_annotation_derived|
|011|maJsgJsapH4_00.mp4|16 / 7.000|[6,8)|1 / source_annotation_derived|
|012|ApyekBAgfg8_00.mp4|29 / 4.000|[3,5)|1 / source_annotation_derived|

모두IIDtrain/지리test이며 현재IID 방침으로 준비했다. 원본 사건수는 확정하지 않고 기존의 미확인 관리묶음을 유지했다. F0/Xaq 평가예약과 IIDtest는 제외한다. 정상 라벨을 새로 만들지 않았고 기존QMUL정상3구간을 보존한다.

**실제 실행 명령:** 기존CLI를 사용하며 출력을 새폴더에만 만든다. 아래경로는 이미생성됐으므로 다시실행하면 덮어쓰기방지 오류가정상이다.

```bash
.venv-model-check/bin/python -m unittest discover -s tests -p test_segment_reviews.py -v
.venv-model-check/bin/python scripts/record_segment_reviews.py \
  --plan artifacts/training/expansion-intake-20260922-01/preview-plan.json \
  --manifest artifacts/training/expansion-intake-20260922-01/previews/manifest.json \
  --source-annotation-policy configs/train_annotations.accident.json \
  --output artifacts/training/source-annotations-20260922-01/prepared
```

기록 명령 출력은 `source_annotation_inputs_prepared`, source_annotation_accident=3, normal_created=0, human_reviews_created=0, training_run=false다. 관련53시험 통과(신규12 포함, 내부5.307초/부모5.624422초,60초제한). 실제명령0.381985초/60초제한, 자식최고RSS73,449,472bytes약70.05MiB(모델GPU메모리 아님). 기존학습로더로세배열을읽어float32[3,16,224,224]/target1을확인한0.655674초는import/기록읽기를포함하고최종검사JSON저장전까지다. 모델forward/backward0, 영상새디코딩0.

**확인할 파일:** [입력·정답·CSV근거](../artifacts/training/source-annotations-20260922-01/prepared/prepared_inputs.json), [기존 장면 미리보기](../artifacts/training/expansion-intake-20260922-01/report.html), [실제 로더 확인](../artifacts/training/source-annotations-20260922-01/loader-check.json), [실행로그](../artifacts/training/source-annotations-20260922-01/run.json). 옛미리보기의UNCONFIRMED 문구/옛target=null은생성당시의사람미검수상태로보존했고,최신출처기반target은새입력JSON에있다. 새사용자품질검수를완료했다고쓰지않는다.

**아직 하지 않은 것:** 모델학습/평가/300파일처리/클라우드실행. 기존 `train_r3d18.limited-real.json`은5구간전용이므로이파일경로만바꿔8구간을학습할수는없다. source_annotation_derived를받는명시적학습준비경로·8구간manifest가다음작업이며,기존human/evaluation검사를몰래완화하지않는다. 제안실행범위는3step/60초사전비용측정후최대5epoch·전체5분중먼저종료(head/기존학습률/입력규칙유지),새모델별도저장이다. 실행시간예측은새자료측정후제시하고이번에는학습명령을주지않는다.

## 2026-09-22 정정 — 사용자는 metadata-real.csv의 원본 정답 활용을 뜻함

사용자 “metadata-real.csv 파일에 답이 적혀있는거 비교하면될까?”를 받아 앞서result를Colab3구간결과로해석한잠정선택을정정했다. 기존결과는이력으로보존하며사용자가그파일을기준선으로지정했다고확정하지않는다. candidates.json의comparison_baseline 상태를정정했다.

실제CSV에는 path/type/rollover/accident_time/accident_frame/좌표/분할등21컬럼이있다. 새010=CSV4행8.667초/single,011=16행7.000초/rear-end,012=29행4.000초/head-on. 각각제안2초구간안에주석시점이들어가며이미이값을미리보기위치선정에사용했다. weather=normal은무사고정답이아니다. 정상클래스·사고종료구간의명시적인이진라벨컬럼은없다.

**설명 보완:** CSV의공식사고주석을활용할수있고,모든사고영상을처음부터사람이재라벨링해야만학습가능한것은아니다. 다만현재의코드는사람이승인한2초입력만허용하므로,전체자료확대를하려면원본주석에서파생라벨을만드는규칙과그검수범위를별도로정해야한다. 제안은사고시점포함2초를원본주석기반후보로자동구성하고표본품질·시간연결을점검하되,정상은기존승인자료또는별도근거를사용하는것이다. 파생라벨을수동검수완료로기록하지않고,시간/화질/잘림등예외는보류한다. 이번질문만으로기존라벨정책을변경하거나새3개정답을자동승인하지않았다.

현재새구간은여전히미검수다. 원본CSV비교/의미확인만수행했고영상재추출·코드변경·학습0이다. 다음학습준비에서는‘CSV원본주석활용’과‘전수수동검수강제’를동일시하지않는다.

## 2026-09-22 — result를 다음 실험의 비교 기준으로 연결

사용자가 “result를 기반으로 잡고 진행하면될거같아.”라고 요청했다. 방금 수신한 Colab3구간 참고비교를 뜻하는지 확인 질문 중이며, 기존 candidates.json의 comparison_baseline에 해당결과/해시/model.pt 참조를 **잠정 비교 기준**으로 연결했다. 다른 result를 뜻하면 참조만 정정한다. 실행/라벨 승인을 만들어 넣지 않았다.

`comparison.user-provided.json`은 모델 답·정답 비교·시간을 담은 결과이고 `model.pt`는 실제 모델가중치다. 다음 모델은 같은 고정3구간/전처리/판정으로 참고비교할 수 있지만 독립 검증 성능은 아니다. 이 선택만으로 현재가중치 이어학습이나 공식가중치 재시작 중 하나를 확정하지 않는다. 비교용 F0/Xaq는 학습에 넣지 않는다.

새010~012의 구간 정답은 이result에 없으며 target=null/unreviewed를 유지했다. 정답/입력품질 확인을 받으면 승인된 새구간과 기존자료를 학습목록에 연결하고, 기존소수학습 코드에 새자료용Colab 실행 연결을 준비한다. 현재노트북은 평가전용이고 새학습은 미실행이다. 같은결과 계산/미리보기/장치시험을 반복하지 않았다.

## 2026-09-22 후속 — 새 구간 정답 없음 확인 / 첫3개 검수 미리보기 준비 완료

사용자가 **영상과 metadata-real.csv만 있다**고 답했다. 새 구간 라벨 파일은 없으므로 원본 사고시점을 이진 구간정답으로 자동변환하지 않았다. 기존 T9 경로에서 후보3파일만 기존 전처리로 읽어 검수용16장224 미리보기를 준비했다. 이는 Drive 파일을 에이전트가 확인한 것이 아니다.

**지금 볼 파일은 [첫3구간 검수 보고서](../artifacts/training/expansion-intake-20260922-01/report.html)**다. 실제 사고 발생이 보이는지/정상인지/판단 불가·사고 후 장면 보류인지, 식별 가능·잘림/누락·추가 장면 필요를 해당 새 구간에만 답하면 된다. 기존008·009·QMUL은 재검수하지 않는다. UNCONFIRMED는 아직 사용자 정답을 받지 않았다는 표시다.

| 검수ID | 원본 파일 | 제안 구간(초) | 실제16장 PTS 처음–끝(초) | 정답 |
|---|---|---|---|---|
| 010 | UarP8qU1S-c_00.mp4 | [7.667,9.667) | 7.733333–9.600000 | 미확정 |
| 011 | maJsgJsapH4_00.mp4 | [6.000,8.000) | 6.100000–7.900000 | 미확정 |
| 012 | ApyekBAgfg8_00.mp4 | [3.000,5.000) | 3.083333–4.958333 | 미확정 |

모두 IIDtrain/지리test이며 현재 선택한 IID 기준의 후보다. CSV순10목록에서 같은 파일명계열의00/01을 함께 독립표본으로 세지 않도록 먼저 계열당1파일씩 골랐다. 이름계열이 다르다고 사건독립성이 확인된 것은 아니다. 그룹은 미확인 보수적묶음이며 학습/검증에 나누지 않았다.

**실행:** 기존 `preprocess_samples.py`, 최대3구간/48장, 파일별20초·전체90초 상한. 부모4.625461초, 종료0, 오류0/미확정3/학습입력배열0, 원본·CSV·계획 불변. 부모가 수집한 자식최고RSS803,471,360bytes(약766.25MiB), GPU사용0. [명령·실측](../artifacts/training/expansion-intake-20260922-01/preview-run.json). 출력 연결 assertion으로16개시각순서/target=null/학습배열없음/IIDtrain/원본불변/HTML링크존재를 확인했다. 생산코드 변경이 없어 기존 단위시험은 재실행하지 않았다. 전체영상 재생 검증이나 학습 완료가 아니다.

```bash
.venv-model-check/bin/python scripts/preprocess_samples.py \
  --data-root "$ACCIDENT_ROOT" --metadata data/local/accident/metadata-real.csv \
  --plan artifacts/training/expansion-intake-20260922-01/preview-plan.json \
  --worker-timeout 20 --output artifacts/training/expansion-intake-20260922-01/previews
```

ACCIDENT_ROOT는 앞서 사용자가 지정한 T9 accident 경로다. 기존 출력은 재실행으로 덮어쓰지 않는다. **다음 하나는 이3개 구간의 답변을 기존 정상/사고 승인 자료와 함께 새 학습 명세에 연결하는 것**이다. Colab 학습 연결·구체적 시간/step상한은 그 명세 기준으로 준비하며, 이번에 새학습/새다운로드/업로드/모델변경은 하지 않았다. 다양한 정상 원본이 필요하다는 한계는 유지하지만, 기존 정상3구간을 없는 자료로 취급하지 않는다.

## 2026-09-22 최신 — Colab 참고 비교 결과 수신 / 새 학습 자료 접수 시작

**현재 상태:** 사용자가 Colab 비교 JSON·Drive 저장 시간과 “이제 확인되었고” 답변을 전달했다. 기존 실행목록/모델/코드 해시·정답·구간·16개 PTS와 대조해 일치했다. 에이전트가 Colab을 다시 실행한 것은 아니다. 기존 JSON의 user_review=pending 필드는 원문 그대로 보존하고 사용자 확인 보고를 별도 수신 기록에 남겼다. HTML 원본·정확한 Drive 실행폴더·GPU 기종은 전달되지 않았다.

| 구간 | 정답 / 예측 | 사고 점수 | loss |
|---|---|---:|---:|
| devval_accident_01 | 1 / 1 | 0.521091 | 0.651831 |
| devval_accident_02 | 1 / 1 | 0.648433 | 0.433197 |
| devval_normal_01 | 0 / 0 | 0.497496 | 0.688152 |

완료3/3·실패0·오탐0·미탐0, 선택구간 Accuracy/사고 Precision/Recall/F1=1.0, 평균loss0.591060. **독립성 미확인 참고값이며 독립 검증 성능이 아니다.** 파일2개/정상원본1개이며 F0 정상·사고의 시간 일부가 겹친다. 경계에 가까운 점수가 있고, 점수는 보정된 실제 사고 확률이 아니다.

사용자 전달 로그: CUDA 추론루프1.129673초, 자식9.835563초, 복사·준비·보고서/Drive저장 포함 활성시간 합14.958635초. 인증/패키지준비/셀간대기/마지막영수증쓰기는 제외한다. CUDA 최대allocated417,740,288bytes/reserved438,304,768bytes, 모델/BN 불변true. 학습 시간 측정으로 확대하지 않는다. [수신한 비교](../artifacts/training/expansion-intake-20260922-01/comparison.user-provided.json), [시간](../artifacts/training/expansion-intake-20260922-01/drive_export_timing.user-provided.json), [수신·연결 근거](../artifacts/training/expansion-intake-20260922-01/result-receipt.json).

### Drive real_videos를 다음 학습에 사용하려면

현재 `20260922-03-evaluate_r3d18_colab.ipynb`는 **평가 전용**이다. Drive에 영상을 추가하거나 경로만 바꿔 학습을 시작하지 않는다. 기존 `training_data.limited_real_preflight`는 이전 승인2~5구간/최대25step/5epoch·1800초를 확인한다. 새자료 규모로 바꾸려면 승인 라벨·그룹 명세와 별도 실행설정을 연결해야 하며 기존 제한을 조용히 삭제하지 않는다. `training.restore`는 같은 데이터/설정의 체크포인트 재개용으로, 새데이터를 과거 실행의 재개처럼 넣을 수 없다. 초기화/새자료 학습 방식은 후속 실험에서 명시한다.

- 공식 IID train507행 중 비교에 사용한 F0NHENn-HS4_00.mp4·XaqA-uzqeNc_00.mp4는 계속 비교용으로 예약한다. 다른 시간대도 임의로 학습에 넣지 않는다.
- 예약2파일 제외505행에는 기존 학습008·009 파일2개가 포함된다. 이들까지 제외하면 **새 후보503행**이다. 이는 로컬 CSV/기존T9 파일명 근거이지 Drive의503파일 존재·재생·정답 확인 수가 아니다.
- 첫10파일 후보를 CSV순으로 [목록](../artifacts/training/expansion-intake-20260922-01/candidate_paths.txt)에 준비했다. [CSV행·원본주석·분할 근거](../artifacts/training/expansion-intake-20260922-01/candidates.json). 전부 target=null/구간미승인/학습실행false다. filename_family_hint는 이름이 비슷한 파일을 함께 검토하는 단서이며 실제 사건ID가 아니다. 지리test/IIDtrain은 IID 선택상 후보가 될 수 있으며 공식IIDtest는 모두 제외했다.
- 기존 사고008·009와 QMUL 정상3구간은 검수 근거를 재사용한다. 새 정상·사고 구간 정답이 없다면 CSV 사고시점 주변과 정상 검수 후보를 기존 방식으로 보여주고 해당 범위만 승인받는다. 영상전체=사고/사고이전=정상으로 만들지 않는다.
- 사용자에게 새 구간 라벨 파일이 이미 있는지 질문했다. Drive 경로의 `accidnet` 표기는 입력 그대로 취급하며 `accident`로 자동 수정하지 않는다. 실제 경로/CSV 버전은 미확인이다.

Colab의 기존 Drive 연결 후 새 코드셀에서 아래만 실행하면 위치를 읽기 전용으로 확인할 수 있다. 영상/모델을 열거나 파일을 바꾸지 않는다. 에이전트는 아래 셀을 Drive에서 실행하지 않았다.

```python
from pathlib import Path
base = Path('/content/drive/MyDrive/boram3-cctv-evaluation')
for name in ('accidnet', 'accident'):
    folder = base / name
    print({'folder': name,
           'real_videos_exists': (folder / 'real_videos').is_dir(),
           'metadata_real_csv_exists': (folder / 'metadata-real.csv').is_file()})
```

**다음 작업 하나:** 선택한 새 학습 후보를 실제 Drive 경로·CSV·이미 있는 구간 정답과 연결한다. 정답 파일이 없다면 기존 검수 도구로 첫 소수 후보부터 구간을 준비한다. 새 학습실행은 정답/목록/초기화와 실행상한을 정한 뒤 시작한다. 먼저 최대3step/60초의 비용 측정을 제안하며 이후 학습량은 실측을 보고 정한다(아직 실행 승인/완료 아님). 이번 자료 확대와 별개인 시간규칙 통일/학습률/학습층/임계값 변경은 자동 적용하지 않는다. 전체폴더 일괄학습·새영상디코딩·Colab재실행0.

## 2026-09-22 최신 — 승인한 세 구간의 참고 비교 실행용 Colab 묶음

**사용자 입력 품질 승인 완료 / 참고 비교 코드·로컬 전달 검사 완료 / 실제 Colab 예측·결과 확인은 미실행.** 사용자는 “세 구간 모두 식별 가능하고 잘림·누락 없음. 독립성 미확인을 표시한 참고용 예비 비교로 진행해줘.”라고 승인했다. 같은 검수 질문을 반복하지 않는다. 기존 정답과 검수자·날짜는 보존했다. 이번 품질/실행 답변의 날짜는 수신일2026-09-22이며 새 촬영/관찰 날짜를 주장하지 않는다.

입력은 기존 모델과 아래 세 배열, 처리는 eval·추론 전용 예측 저장 후 정답 비교, 출력은 comparison.json/report.html/실측시간이다. **독립성 미확인 참고 비교**이므로 지표가 생겨도 독립 검증 성능이라고 쓰지 않는다.

| 구간 ID | 실제 원본 | 승인 범위(초) / 정답 | 모델이 읽을 기존16장 PTS 처음–끝 | 미리보기 |
|---|---|---|---|---|
| devval_accident_01 | F0NHENn-HS4_00.mp4 | [1.533,3.533) / 사고1 | 1.600000–3.466667 | [기존 입력](../artifacts/colab/evaluation-20260922-02/inputs/devval_accident_01/preview.png) |
| devval_accident_02 | XaqA-uzqeNc_00.mp4 | [1.250,3.250) / 사고1 | 1.333333–3.208333 | [기존 입력](../artifacts/colab/evaluation-20260922-02/inputs/devval_accident_02/preview.png) |
| devval_normal_01 | F0NHENn-HS4_00.mp4 | [0,2) / 정상0 | 0.066667–1.933333 | [기존 입력](../artifacts/colab/evaluation-20260922-02/inputs/devval_normal_01/preview.png) |

세 구간은 **2파일**이다. F0의 정상·사고는 같은 그룹이며 범위 일부가 겹친다. 정상 원본1개이고 독립 사건 수는 모른다. Xaq 정상후보02는 제외한다. 공식 IID train CSV15·23행 근거를 유지하고 공식 test/학습용 전환은 금지한다. 독립성 pending을 approved로 바꾸지 않았다.

### 지금 직접 실행하는 순서

1. [새 ZIP](../artifacts/colab/evaluation-20260922-03/colab-evaluation.zip)을 Drive의 **내 드라이브/boram3-cctv-evaluation/incoming/evaluation-20260922-03/** 폴더에 업로드한다. 파일명은 `colab-evaluation.zip`. **Drive에서 압축을 풀지 않는다.** 이미 올린02 준비 ZIP은 보존한다. 이번 ZIP에는 새 참고 비교 코드와 승인 명세가 있어 이전 ZIP으로 대체할 수 없다. 전체 ACCIDENT/QMUL 영상 재업로드는 필요 없다.
2. [최신 노트북](../notebooks/evaluate_r3d18_colab.ipynb)을 Colab의 노트북 업로드로 연다. 첫 제목이 **R3D-18 참고용 예비 비교 — 독립성 미확인 / 학습 없음**인지 확인한다. 이전 코드를 실행했던 런타임은 다시 시작하고 무료 GPU 런타임을 선택한다. 무료 GPU 미할당/패키지 오류면 멈추고 메시지를 공유한다. 유료 전환·추가 설치를 자동 실행하지 않는다.
3. 노트북 **1 환경 → 2 패키지 확인 → 3 Drive 연결**을 차례로 실행한다. 3단계의 `CONFIRM_TRANSFER = False`만 `True`로 바꾼다. 기본 경로는 위 업로드 위치와 같다. 다른 곳에 올렸다면 `ARCHIVE`를 실제 ZIP 위치에 맞춘다.
4. **4 복사·검사 → 5 기존 모델 확인 → 6 참고 비교 → 7 결과 저장** 순서로 한 번씩 실행한다. 4단계는 `connected_inputs=3`, `evaluation_ready=True`, target=[1,1,0]이어야 한다. independence_review=pending은 이번 목적에서 허용한 미확인 사실이므로 직접 수정하지 않는다. 6단계는 최대3구간/추론300초, 모델 시작 준비120초이며 새 학습이 없다.
5. 결과는 **내 드라이브/boram3-cctv-evaluation/results/reference-실행시각/**에 저장된다. `report.html`과 `previews/`를 같은 폴더로 보관한다. 미리보기 → 정답·예측·오탐/미탐 표 → loss 설명 → timing/drive_export_timing 순서로 확인한다. 출력 파일/오류 메시지를 공유하면 실제 결과를 해석할 수 있다. 지금 이 문서는 예측을 미리 만들어 표시하지 않는다.

새ZIP **143,283,342bytes = 약143.28MB(136.65MiB)**, payload143,273,986bytes / 파일22개+영수증1개다. 모델·검수 별칭·세 입력배열·원본시각·미리보기·CSV·필요코드/설정을 포함하고 원본 영상·학습입력배열·환경·캐시·토큰은 제외했다. [허용 목록](../artifacts/colab/evaluation-20260922-03/transfer_manifest.json). 사용자 이전ZIP 업로드는 사용자 보고이며 새ZIP 업로드/Colab GPU는 에이전트 미확인이다.

### 실제 확인과 한계

`evaluation.preflight`는 **reference_comparison_unconfirmed_independence** 목적·별도 승인 지문을 확인한다. 모델/입력/정답/상한이 달라지면 기존 승인이 적용되지 않는다. 기존 development_validation의 독립성 검사는 유지한다. 알려진 학습 SHA/그룹/원본·사건 중복, 공식 test, 미승인 라벨/품질/사용, 파일 해시 오류도 두 경로 모두 거부한다. 이번 사용승인은 새 법적 이용조건 검증을 뜻하지 않는다.

관련 **38개 테스트 통과**(7개 추가, unittest4.463초/부모5.570575초, 상한60초). 묶음 생성 내부0.474022초/부모0.518909초, 자식최고RSS42.83MiB. 노트북4단계를 실제ZIP으로 로컬 임시폴더에서 실행해 해시/목록/명세 통과(0.384740초) 및 엄격한 독립 검증 경로 차단을 확인했다. **이 값은 모델 평가 시간·Colab 시험 결과가 아니다.** 기존 모델/입력/02ZIP 해시·정답·구간 보존. 새 원본 디코딩·R3D-18 forward·학습·다운로드·도구 업로드0.

실행 명령(프로젝트 루트):

```bash
.venv-model-check/bin/python -m unittest discover -s tests -p test_evaluation.py -v
.venv-model-check/bin/python scripts/prepare_colab_bundle.py --manifest configs/evaluation.colab.reference.json --output artifacts/colab/evaluation-20260922-03
```

두 명령은 각60초 감독 제한으로 실행했다. 같은 출력 경로를 다시 생성하면 기존 결과 보호를 위해 실패한다. [실제 명령·테스트·시간 기록](../artifacts/colab/reference-approval-20260922-01/tests-result.json), [전달 검사](../artifacts/colab/reference-approval-20260922-01/portable-check.json).

**다음 하나:** 이 최종 묶음으로 Colab에서 현재 모델의 세 구간 참고 비교를 한 번 실행하고 저장 결과를 확인한다. 아직 성능 지표/오탐/미탐 숫자와 Colab 처리시간은 없다. 아래는 이전 단계 이력이다.

## 2026-09-22 — 5 epoch의 의미와 학습 자료 확대 제안

현재 모델은 사고2구간·QMUL 정상3구간, batch_size=1로 5 epoch/25업데이트를 수행한 제한 학습 모델이다. `result.json`의 completed_epochs=5, training_updates=25, validation_run=false와 기존 train manifest를 다시 읽어 확인했다. epoch는 전체 학습 목록을 한 번 도는 횟수이며, 5개의 장면을 100번 반복해도 독립적인 장면 수가 늘지는 않는다. 5 epoch 자체가 부족하다고 일반화할 수는 없지만, 이 자료 구성으로 일반화 성능이 충분하다고 판단할 근거는 없다.

T9 지정 real_videos 파일명과 기존 metadata-real.csv만 대조했다. CSV 2,027행에 대응하는 일반 MP4 파일명 2,027개를 확인했고 IID train 507/test 1,520행이다. 검증 예약 F0NHENn-HS4_00.mp4·XaqA-uzqeNc_00.mp4를 제외하면 train 후보 505행이다. 기존 학습 파일도 포함되므로 모두 새 학습자료라는 뜻은 아니다. 별도의 `._` 접두사 항목 2,027개는 영상 수에서 제외했다. 최초 확장자 단순 집계 4,054는 이 항목을 포함한 수이며, 보존한 영수증의 filename_count_clarification에 정정 근거를 추가했다. 파일 전체 무결성·재생·사건 독립성·구간 정답을 검증한 수가 아니다.

**다음 작업 하나(제안):** 505행 범위에서 기존 학습·검증 원본/사건 관계를 고려해 학습 확대용 첫 10파일 이내 후보 목록을 선정한다. 입력은 기존 CSV·예약/학습 목록·T9 파일명, 출력은 기존 검수 방식에 연결할 후보와 주석/원본그룹 근거다. 완료 기준은 IID test·예약 검증 원본을 제외하고 불확실한 관계를 표시한 목록이며, 정상/사고 정답은 사람 검수 전 확정하지 않는다. 이번에는 후보 선정·새 미리보기·학습을 실행하지 않았다.

확대 학습 전에는 사고뿐 아니라 여러 촬영 원본의 정상 구간이 필요하다. ACCIDENT 안에서도 실제로 사고가 없는 구간을 사람이 확인해 사용할 수 있으나 사고 이전/미주석을 자동 정상 처리하지 않는다. 같은 원본의 정상·사고는 같은 분할에 둔다. 새 승인 자료가 모이면 기존 검증 예약은 고정하고 나머지 학습 허용 자료에 원본그룹 기준 약80:20 분할을 출발안으로 적용한다. 이는 아직 확정된 파일별 분할이 아니다.

실행은 R3D-18/head와 기존 학습률·판정 기준을 유지하는 자료 확대 실험부터 제안한다. 과거 정상6초/사고2초 규칙은 보존하고, 2초 통일은 별도 비교 변경으로 명시한 뒤 승인받는다. 더 많은 epoch나 특징부 해제·학습률 변경을 동시에 적용하지 않는다. 새 자료의 짧은 비용 측정 후 epoch/시간 상한을 제시하며, 이전 5구간의 시간을 전체 T9 학습 시간으로 그대로 외삽하지 않는다. 로컬 Mac 실행 가능 경로는 기존에 확인했지만 확대 학습 비용은 미측정이며 Colab은 선택 사항이다. 추가 학습은 이번에 실행하지 않았다.

## 2026-09-22 — T9 자료로 Mac에서 실행하려면

사용자는 로컬 실행과 추가 학습 가능 여부를 질문했다. 지금 환경은 Darwin arm64이고, 지정 T9 accident 폴더와 F0NHENn-HS4_00.mp4/XaqA-uzqeNc_00.mp4 존재를 확인했다. 전체 자료 목록·정답·분할을 검증한 것은 아니다. T9는 저장장치이며 계산은 Mac의 CPU/MPS에서 한다.

- **현재 모델 시험(권장 다음 작업):** 기존 R3D-18은 이미5epoch/25업데이트 학습됐다. 최신 정상1·사고2는 검증 전용이다. 배열은 프로젝트 artifacts/colab/evaluation-20260922-02/inputs에 있으므로 ZIP업로드/압축해제·원본영상재추출 없이 로컬에서도 읽을 수 있다.
- **새 학습:** T9 안의 별도 학습허용 파일·정답·원본그룹·IID분할과 새실험범위를 정해야 한다. 검증용3구간을 학습에 넣지 않는다. 기존 train_r3d18.limited-real.json의 data_root만 바꾸거나 과거학습명령을 재실행하는 방식은 이번 단계의 안내가 아니다.
- **현재 실행 상태:** evaluation.json은 품질·사건독립성·사용/실행승인 pending이다. local 실행에는 cloud승인 대신 local_evaluation_allowed 근거가 필요하고, 나머지 검증규칙은 유지한다. 이번에는 명령/상태를 읽었으며 모델/검사/학습을 다시 실행하지 않았다.

아래는 **남은 근거가 반영된 후 사용할 로컬 평가 호출 방식**이다. 현재 명세로는 human_review_required에서 멈추며, 바로 실행 가능한 승인 명령이라고 안내하지 않는다. 후속 확정 명세가 새 경로에 저장되면 MANIFEST도 그 경로로 바꾼다. 프로젝트 루트의 터미널에서 실행한다.

```bash
PYTHONPATH=src .venv-model-check/bin/python - <<'PYCODE'
from datetime import datetime, timezone
from pathlib import Path
from boram3_cctv.evaluation import run_evaluation

root = Path.cwd()
MANIFEST = 'artifacts/colab/evaluation-20260922-02/evaluation.json'
run_id = datetime.now(timezone.utc).strftime('local-devval-%Y%m%dT%H%M%S%fZ')
output = root / 'artifacts/evaluation' / run_id
result = run_evaluation(root, MANIFEST, output,
                        device='mps', execution_location='local')
print('결과 위치:', output)
print('처리 상태:', result['completion'])
PYCODE
```

기존 함수는 manifest의 추론상한300초와 준비상한120초를 유지하고 모델/BN을 바꾸지 않는다. 이 코드는 학습 명령이 아니다. 새 결과폴더만 생성하며 실패를 정상판정으로 바꾸거나 MPS오류 때 자동CPU전환하지 않는다. 이번에 실행한 것은 경로 존재/기록 읽기뿐이므로 새 장치·성능 측정값은 없다.

## 2026-09-22 — 정상1·사고2 입력 연결 및 최신 Colab 묶음

**배열/평가 목록/최신ZIP 연결 완료, 실제 모델 평가·Colab 실행은 미실행이다.** 사용자 요청으로 기존 저장PNG만 재사용했다. 이번ZIP은 비어 있던 예전ZIP과 달리 아래 실제 입력3개를 포함하지만, 품질·독립성·사용/실행 승인은 pending이라 `evaluation_ready=false`다. 자동 승인하거나 실행 검사를 해제하지 않았다.

| 구간 ID | 실제 원본 | 승인 구간(초) / 정답 | 선택한 원본PTS 범위(16장) | 원본 그룹 |
|---|---|---|---|---|
| devval_normal_01 | real_videos/F0NHENn-HS4_00.mp4 | [0,2) / 정상0 | 0.066667–1.933333 | F0 같은 파일 그룹 |
| devval_accident_01 | real_videos/F0NHENn-HS4_00.mp4 | [1.533,3.533) / 사고1 | 1.600000–3.466667 | 위와 같은 그룹 |
| devval_accident_02 | real_videos/XaqA-uzqeNc_00.mp4 | [1.250,3.250) / 사고1 | 1.333333–3.208333 | Xaq 파일 그룹 |

3구간/파일2개이며 정상 출처는1개다. F0 두 구간은 시간상 일부 겹친 같은원본이므로 독립 표본2개라고 하지 않는다. IID train CSV15·23행을 사용하고 공식IIDtest는 제외했다. 파일해시가 학습자료와 다른 것은 확인했으나 사건·재편집 독립성의 확정 근거로 대체하지 않는다. 실제 사고 사건 수는 미확인이다. 정상 후보02는 포함하지 않았다.

### 지금 확인할 파일

- [최신 ZIP](../artifacts/colab/evaluation-20260922-02/colab-evaluation.zip): **143,295,133bytes / 약143.3MB(136.66MiB)**. 파일22개+허용목록 영수증1개. 원본영상·학습입력배열·환경·캐시·토큰 제외. 모델·검수자 별칭은 포함한다.
- [파일 목록/실행 대기 항목](../artifacts/colab/evaluation-20260922-02/transfer_manifest.json): `connected_segments=3`, 정상1/사고2, `source_files=2`, `evaluation_ready=false`, `pending_reviews`.
- [실제 연결한 평가 목록](../artifacts/colab/evaluation-20260922-02/evaluation.json): 모델·원본SHA·CSV/분할·구간/정답·입력해시·같은파일그룹. `input_connection`은 저장PNG를 어떻게 재사용했는지 보여준다.
- [노트북](../notebooks/evaluate_r3d18_colab.ipynb): 실행 로직은 기존 Python모듈에서 가져온다. 모델·전처리·판정은 유지한다.
- [정상 미리보기](../artifacts/colab/evaluation-20260922-02/inputs/devval_normal_01/preview.png), [사고1](../artifacts/colab/evaluation-20260922-02/inputs/devval_accident_01/preview.png), [사고2](../artifacts/colab/evaluation-20260922-02/inputs/devval_accident_02/preview.png): 기존 PNG를 그대로 복사했다. 그림 속 UNCONFIRMED 문구는 생성 당시 상태이고 최신 정답은 위 표/명세를 따른다. 새 검수를 위해 같은 사고 여부를 반복 질문하지 않는다.

### 무엇이 끝났고 무엇이 남았나

| 조건 | 상태 / 필요한 근거 |
|---|---|
| 세 구간 정답 | 정상1·사고2 사용자 답변 연결 완료. 재질문 없음 |
| 입력 기술 연결 | 각16장PNG가 기존 미리보기 타일과 픽셀/순서 일치. 시각 동일. 원본 재디코딩0, 로더에서 float32/[3,16,224,224]·정답 읽기 성공 |
| 입력 품질 | 사람의 중요한 장면 누락/잘림 없음 등 최종 기록 미완료. 코드의 시간순서 검사는 완료했으며 사람이 다시 프레임 순서를 판정할 필요 없음. 정상01의 별도224 식별 답변도 만들어 넣지 않음 |
| 학습 원본과 사건/재편집 분리 | 파일SHA/관리그룹 직접 중복은 없음. 상위원본/사건 관계 확인 근거 부족. 저장 참고사진을 보았으나 서로 다른 파일/장면만으로 확정 승인하지 않음 |
| 이용·전송·실행 | 로컬 묶음 작성 승인과 구별해 `usage_review`/`evaluation_approval`은 pending. 실제 클라우드 업로드·실행0, Colab/CUDA 미검증 |

정상2원본·사고2사건은 수집 목표이지 평가 코드의 최소 개수 조건이 아니다. 이번3구간으로 예비 비교는 가능하지만 위 조건이 채워져야 기존 preflight를 통과한다. 더 작은 평가를 ‘독립 일반화 성능 검증 완료’라고 확대하지 않는다.

### 로컬 재현 명령과 측정
```bas
.venv-model-check/bin/python scripts/prepare_colab_bundle.py \
  --manifest configs/evaluation.colab.pending.json \
  --connect-reviewed-previews \
  --output artifacts/colab/evaluation-20260922-02

.venv-model-check/bin/python -m unittest discover -s tests -p test_evaluation.py -v
```

위 결과 폴더는 이미 존재하므로 지금 재실행할 필요 없다. 이후 근거가 바뀌어 새 묶음을 만들 때는 새 출력 경로를 사용한다. `--connect-reviewed-previews`는 저장된 검수PNG를 배열로 연결하는 로컬 준비 옵션이다. Colab의 실행승인 검사를 우회하는 옵션이 아니다.

관련31개 시험 통과(신규5개 포함): unittest 내부4.233초, 프로세스포함5.732초/상한60초. 실제묶음 생성 부모0.670664초/상한60초(내부0.615957초), 자식최고RSS80.98MiB. ZIP/원본모델SHA/실제로더 확인0.208194초는 import/최종확인JSON쓰기 제외이며 모델 속도가 아니다. [명령/측정](../artifacts/colab/bundle-link-20260922-01/build-result.json), [연결 확인](../artifacts/colab/bundle-link-20260922-01/connection-check.json), [시험 로그](../artifacts/colab/bundle-link-20260922-01/tests.log).

### Colab에서는 언제 무엇을 누르나

1. **지금은 위 명세·미리보기·pending항목을 확인한다.** 준비 묶음을 실행 가능하다고 보고 바로 올리거나 셀을 모두 누를 단계는 아니다.
2. 남은 근거를 연결하고 업로드/실행 범위를 승인하면 해당 시점의 최종ZIP만 제안 위치 `내 드라이브/boram3-cctv-evaluation/incoming/colab-evaluation.zip`에 직접 올린다. 이미 올린 전체ACCIDENT/QMUL을 추가 복사하거나 다시 업로드할 필요는 없다.
3. 노트북을 열어1환경→2기존패키지→3Drive/경로→4허용목록복사/승인검사 순서다. 없는패키지는 자동설치하지 않으며 `CONFIRM_TRANSFER`는 내용/범위를 확인한 후에만 변경한다.
4. **현재ZIP은4단계에서 `human_review_required`로 멈추는 것이 정상**이다. pending을 임의 수정하지 않는다. 준비된 승인이 통과한 최종묶음에서는5모델읽기→6최대10구간/추론300초(준비120초 별도)→7Drive저장으로 진행한다. 평가 직전 목록을 고정하고 예측저장 후 정답비교한다.

이전 묶음/미확보 문구는 아래 날짜별 이력이다. 새로운 정상 정답을 아직 없다고 되돌리지 않는다.

## 2026-09-22 M1 — 평가 연결 보완 완료 / 실제 독립 평가는 대기

파일 전송 SHA와 학습 내용 지문을 분리해 실제 저장 모델의 설정 연결을 확인했다. `preflight`/`run_evaluation`에 `execution_location="local"`을 명시하면 signed usage_review의 `local_evaluation_allowed=true`를 요구하며, cloud 기본 경로는 `cloud_evaluation_allowed=true`를 유지한다. 부모와 자식이 같은 장소로 재검사한다. 장치 `cpu/mps/cuda`와 실행 장소는 별개다. 기존 정답·입력 품질·분할·독립성 검사는 유지했다.

이 API는 **향후 승인 목록이 갖춰졌을 때** 사용하는 방식이며 현재 평가 실행 명령이 아니다. 현재 명세는 승인된 segments가 없어 여전히 차단된다. 실제 모델은 CPU 설정/지문 읽기만0.662초, 추론0회였다. 관련52개 시험과 두 영상 미리보기는 [M1 보고서](../artifacts/data-review/t9-devval-20260922-01/report.html)를 본다.

기존 Colab ZIP/노트북의 과거 출력은 보존했다. ZIP은 이번 수정 코드와 새 미리보기를 포함하지 않는다. 사용자가 Colab에 accident를 넣었다는 보고만으로 평가 준비 완료라고 하지 않으며 새 승인 입력·새 묶음·전송/실행 범위를 확인한 뒤 진행한다. 추가 학습·실제 독립 평가·Colab 실행은 미실행이다.

> **이전 기획 시점 안내:** T9에서 예약사고두파일의존재/크기는확인했지만 새평가정답/품질/독립성은미승인이다. 새평가준비코드에는파일SHA와내용fingerprint비교,로컬평가에도클라우드허용을요구하는두보완점이있어[현재기술차이](current_status.md#planning-technical-gaps)를먼저본다. 기존Colab노트북/ZIP과18개CPU시험기록은보존하며,실제모델의독립평가/Colab완료로표시하지않는다. 이번은[기획초안](../PROJECT_BRIEF.md)작성으로새학습/추론/코드수정은없다.

## 2026-09-21 — 개발용 독립 검증 예약과 시간 샘플링 통일 비교안

현재 모델은 `artifacts/training/limited-real-iid-20260921-01/loop/model.pt`이며 기존 확인 SHA-256은 `2b0c4818f5aa442ce6eff11a240f319e47ae966b52644c9a0c37c851089f5947`이다. 파일 존재와 저장 설정을 읽었고 모델 로딩/연산/기존 무결성 시험은 반복하지 않았다. 승인된 독립 검증 목록은 비어 있어 이번 평가는 미실행이다. 후보 예약은 [data_inventory](data_inventory.md#2026-09-21--첫-개발용-독립-검증-후보-예약-파일정답-확보-전)에 있으며 학습 명세는 변경하지 않았다.

### 승인 자료가 들어왔을 때의 첫 비교 평가

- 실제 원본/사건이 겹치지 않는 정상2원본·사고2사건에서 우선1구간씩, 필요하면 총8구간까지 검수하고 점수 전에 목록/정답/그룹을 고정한다. 상한은10구간, 새 추론 루프300초다. 실패/미확인 항목도 예약 목록에 남긴다.
- **현재 승인 입력은 모든 검증 구간2초·16프레임**, 원본PTS의균등16칸중심최근접프레임, RGB224 letterbox/crop없음, CTHW float32·기존Kinetics정규화다. 기존 `preprocessing.sample_frames`/`letterbox`, `training.SegmentDataset`/`tensor_from_array`/`reload_inference`를 재사용한다. `train_r3d18.py`를 검증 목적으로 실행하지 않는다.
- 현재 모델 eval/inference 전용, normal0/accident1, softmax[1]와기존argmax(동점정상),배치1/MPS/workers0를유지한다. 오류시자동CPU전환없음. 정답/사고시점/유형을모델입력에넣지않고, 저장한구간영상만예측한다. 예측ID·모델/입력설정·원본PTS·logits·점수·예측을 먼저 별도 저장한 뒤 동결된 정답과 ID로 연결해 비교한다. 기존 로더의 정답 검증은 무결성 검사이며 예측 생성의 입력으로 넘기지 않는다.
- 비교표에는 모든 지정 구간의 ID/원본/시간/정답/예측/사고점수/맞음·틀림/loss/상태·실패사유를 남긴다. 성공한 구간의 혼동행렬·FP/FN·Accuracy·사고Precision/Recall/F1과 클래스별 구간수/실제 원본수/관리그룹수를 함께 기록한다. 누락/실패는 정상으로 바꾸지 않고 전체예정수·성공수·보류수·실패수를별도로표시한다. 일부실패시전체목록성능이아니라성공부분의조건부결과임을표시한다. 분모가0인 지표는 미정의로 남긴다. 여러구간이같은원본이면원본수를늘리지않는다. 이 작은 구간 평가를 사건 탐지 성능이나 장시간 오탐률로 보고하지 않는다.
- 시간은 **준비·저장을 포함한 실행 전체**, **자식의 분석 구간**, **추론 루프**를 구분해 측정해야 한다. 예정된 전체 측정은 외부 감시에서 작업 자식을 시작하기 직전부터 입력 준비·모델 로딩·추론·정답 비교·미리보기/표 저장을 마친 자식의 종료 직후까지다. 감시 프로그램 자체의 import/마지막 타이밍 영수증 쓰기는 제외한다고 명시한다. 자식 분석/모델 호출 측정은각각시작·끝을따로기록하고합산하지않는다. 이번에는실행하지않아측정값없음이며기존15.10초를새전체시간으로옮겨쓰지않는다. 이 평가 흐름의 새 자료 연결/외부 감시 통합은 자료 확보 후 필요한 부분만 구현한다.

### 첫 개선 후보: 학습 시간 규칙 한 가지만 통일 (미승인·미실행)

| 항목 | 기존 첫 학습 | 비교 실험 제안 |
|---|---|---|
| 바꿀 한 가지 | 사고2초/정상6초에서각16장 | 사고2초유지, 정상만검수한2초에서16장 |
| 학습 자료/그룹 | 사고008·009, QMUL001·002·003 / 기존2관리묶음 | 같은5입력/같은묶음; 새검증자료학습편입없음 |
| 정상2초후보 | 기존[30,36),[1500,1506),[2700,2706) | 각중앙[32,34),[1502,1504),[2702,2704) **검수전제안**, 이번추출없음 |
| 공간/모델/학습범위 | 224·R3D-18·head,특징부/BN고정 | 유지 |
| 초기화/optimizer | 공식KINETICS400_V1·seed20260918,SGD lr0.001/momentum0.9 | 같은초기화·seed·optimizer; 완료모델에서추가학습하지않고비교실험을동일출발점에서시작 |
| 학습량 | 5epoch·25업데이트,배치1,workers0 | **동일5epoch·25업데이트**. 학습량을동시에늘리지않음 |
| 판정/평가 | argmax,normal0/accident1 | 같은고정독립검증목록·2초입력·argmax;임계값튜닝없음 |
| 결과 저장 | 기존학습폴더 | 새결과경로,원래모델/설정/결과보존 |

새 정상2초 구간은 기존6초 전체 정상 검수의 근거를 참조하되, 바뀐 입력 시각/16장/품질을 확인하고 해당 범위에 연결한 뒤 사용한다. 008/009의 기존2초 검수를 반복하지 않는다. 초기화·배치·표본순서·학습률·총업데이트를 같게 두어 시간 규칙 효과와 추가학습량 효과를 섞지 않는다. 기존 사전학습 파일을 사용하므로 새 다운로드 계획은 없다.

**추가 승인에 제시할 실행 범위:** 실제5입력목록과고정검증목록을먼저제시한다. 준비2step을총25업데이트에포함하고최대5epoch/25업데이트 또는학습루프5분중먼저도달하면종료한다. 시간/메모리로25번을완료하지못하면부분실행으로보고하고동일학습량비교로합격시키지않는다. 기존8GiB중단/메모리보호설정은유지한다. **5분은안전상한제안이며실측예상이아니다.** 기존25업데이트실행은기록상명령23.35초/누적학습루프16.90초였지만새전처리/검증/준비시간까지보장하지않는다. 현재요청은재학습승인이아니므로이번에는이실험을시작하지않는다.

**현재 단계(2026-09-21 사용자 결정): 첫 학습과 연속 추론을 완료한 뒤의 초기 모델 검증.** 학습5입력에서정상/사고를구별했던결과와008연속29창의사고판정이지닌한계를기존기록으로확인한다. 독립검증자료는없다. 사용자가지목한표시11/12와4.000/4.125초는기존학습용008[2.583,4.583)미리보기와연결됐다. 기존사고1승인은유지하며정확한발생시점이나다른연속창의정답으로전용하지않는다. 원본CSV시점3.583초도보존한다. [최신현재상태/다음행동](current_status.md), [추가검수설명](../artifacts/training-review/cause-20260921-01/report.html#review-update). 추가학습은자료·변경한가지·실행상한을제시하고승인받은뒤에만진행한다.

## 2026-09-21 — 저장된29개 사고 판정의 원인 점검(추가 학습/추론 없음)

[새 원인 점검 보고서](../artifacts/training-review/cause-20260921-01/report.html)는기존예측을대조한결과다.29개모두사고이지만같은학습008원본의겹치는2초창이며새정답이없어오탐/성능은미확인이다. 저장클래스normal0/accident1,softmax[1],argmax는일치했다. 처음3개저장픽셀은서로다르고같은PTS는같았으며점수캐시재사용증거는없었다. 나머지26개당시입력텐서는미저장으로완전대조불가다. QMUL학습정상3개의기존사고점수0.085–0.110은정상판정으로008연속0.924–0.953과다르다.

공간전처리는동일하지만사고학습2초/정상학습6초/연속추론2초에서16장을선택해시간밀도가다르다. 배경/출처편향·소량학습·시간밀도는가능한원인이며이번에튜닝하거나원인으로확정하지않았다. 지정자료에는독립검증용정상/사고승인목록이없다. 기존15.095540초의‘명령전체’표현은**자식실행을감싼부모측정구간**으로정정한다(부모준비/command.json쓰기제외). 원래로그/HTML은보존하고[PROGRESS](PROGRESS.md)에코드상측정경계를기록했다. 처음/중간/끝네새장면의정답은사용자답변전미검수다.

## 2026-09-21 — 연속 영상 동작 확인과 손실 설명 통합

최신 [통합 HTML](../artifacts/training-review/continuous-20260921-01/report.html)은 **A기존 학습 기록 / B이번 연속 영상 분석 / C독립 검증 미실행**을 구분한다. 이전 HTML·모델·학습결과는 보존했다.

```bash
open artifacts/training-review/continuous-20260921-01/report.html
```

사용자가 선택을 맡겨 사고008 원본 처음부터최대60초, 최근2초16장/1초마다 판단하도록 에이전트가 선택했다. 실제파일은30초이며 MPS에서29회추론, 전체15.095540초/루프12.914523초였다. 추가학습0회·가중치/BN유지다. **29개 모두사고쪽으로 판정됐지만 새창의 정답은미확정**이므로 맞음틀림·손실·정확도를 만들지 않았다. 기존승인2.583–4.583초의 정답을 경계가다른 창으로 복사하지 않는다.

실제손실함수는 `F.cross_entropy(logits, targets)`이며 클래스/샘플가중치없음,smoothing0,reductionmean,ignore_index=-100(이번 무시표본0개)다. 배치1의loss는 샘플1개값이다. probe1–2/재개3–25의 마지막epoch평균0.121698과 최종모델의 저장5개logits 평균0.090683을 별도 표시했다. 후자는 새추론없이 CPU손실계산만 수행했다. 둘다 학습자료 손실이며 검증loss가 아니다.

독립검증 목록이 없어 평균loss·Accuracy·사고Precision/Recall/F1·혼동행렬은 미실행이다. 관련17개테스트·실제입력/파일보존 확인은 완료했고 HTML화면렌더링과 사용자직접확인은 미확인/대기다. [명령·코드·시간·한계](PROGRESS.md), [개념 설명](LEARNING_LOG.md), [29개 예측](../artifacts/inference/008-sequential-20260921-01/predictions.jsonl), [손실 설정/분자/분모](../artifacts/training-review/continuous-20260921-01/loss-explanation.json)를 참고한다. 결과를 보여준 뒤 추가튜닝은 멈춘다.

## 2026-09-21 — 기존 학습 결과 함께 보기 / 사용자 확인 대기

[로컬 HTML 보고서](../artifacts/training-review/limited-real-iid-20260921-01/report.html)에 실제 학습 입력5개·원본16시각·미리보기·정답/최종 예측·25개 loss를 모았다. 프로젝트 루트에서 아래 명령으로 기본 브라우저에 열 수 있다. 서버나 추가 설치는 필요 없다.

```bash
open artifacts/training-review/limited-real-iid-20260921-01/report.html
```

기존 학습 배열·미리보기를 재사용했다. 사고2초/정상6초 모두16등분의 중심에 가까운 PTS를 선택했지만 목표 간격은0.125초/0.375초로 다르다. 검수 시간 범위와 실제16개 프레임 시각을 보고서에서 구분한다. 전처리 재설계·영상 재추출은 하지 않았다.

저장된 것은 정규화 전 RGB uint8 배열이다. 모델 직전의 float32 정규화 텐서는 별도 저장본이 없어 같은 배열·코드·학습 설정으로 재구성했다. HTML에서도 이 구분을 표시한다.

저장된 다섯 최종 점수가 없어 기존 추론 함수를 eval/inference 전용으로 각1회만 실행했다. 실제 MPS 추론 루프6.004931초, 순전파5회·추가 학습0회이며 매 호출 모델 로딩·상태 대조 시간 포함이다. 사고 점수008=0.953181,009=0.897214,QMUL001=0.109866,002=0.085405,003=0.087297, argmax 비교에서5개 모두 정답과 일치했다. **학습 자료의 결과 확인이며 독립 검증 성능이 아니다.** 점수는 보정된 사고 확률이 아니고 학습 전 예측은 기록이 없어 미확인이다.

[새 예측 원문](../artifacts/training-review/limited-real-iid-20260921-01/predictions.json), [명령·실제 시간](../artifacts/training-review/limited-real-iid-20260921-01/inference-command.json), [기존 파일 보존·보고서 자료 대조](../artifacts/training-review/limited-real-iid-20260921-01/report-checks.json), [진행 기록](PROGRESS.md)을 확인할 수 있다. 모델·설정·기존 결과는 그대로다. 내장 브라우저의 로컬 URL 정책 때문에 화면 렌더링은 확인하지 못했고 자료·이미지·링크를 파일 수준에서 점검했다. **사용자가 이번 보고서를 직접 확인한 상태는 아직 대기**이며 후속 작업은 시작하지 않는다. 아래 내용은 기존 학습 실행의 근거와 이전 이력이다.

## 2026-09-21 — 첫 실제 자료 제한 학습 완료: IID 참고·학습 전용

**실제 M1 Max MPS에서 승인된 사고2·정상3구간으로5 epoch/25업데이트를 완료하고 최종 모델을 저장·재로딩했다.** 첫2업데이트 후 저장한 체크포인트에서 나머지23번을 이어서 수행했다. 독립 검증·일반화 성능 평가는 하지 않았다. 아래 ‘가중치 미확보/실제 학습 미실행’은 이번 실행 전 이력이다.

### 실제로 사용한 자료와 실행 목적

| 학습 구간 | 실제 원본 | 원본 시간(초, 끝 제외) | 정답 | 공식 IID / 지리 | 관리 묶음 |
|---|---|---|---|---|---|
| sample_008 | `data/local/accident/real_videos/-jZ5Rlhzzi4_00.mp4` | [2.583,4.583) | 사고1 | train / test, CSV87행 | accident_008_009_unresolved |
| sample_009 | `data/local/accident/real_videos/-yLuZ5wxAsc_00.mp4` | [4.833,6.833) | 사고1 | train / test, CSV92행 | 같은 보수적 묶음 |
| qmul_001 | 사용자 제공 `qmul_junction.avi` | [30,36) | 정상0 | 외부 자료, 공식 분할 없음 | qmul_junction_same_original |
| qmul_002 | 같은 AVI | [1500,1506) | 정상0 | 동일 | 같은 원본 묶음 |
| qmul_003 | 같은 AVI | [2700,2706) | 정상0 | 동일 | 같은 원본 묶음 |

학습은5구간/실제 파일3개/관리 묶음2개다. **독립 사고 사건 수는 미확인**이다. 파일이 다른008·009를 독립 사건2개로 주장하지 않는다. QMUL은 정상 원본1개다. 두 묶음 모두 train 전용이고 validation/test에 나누지 않았다. 007과 기존 공식 IID test는 제외했다. 과거 train/train 안내와 새007~009가 다른 파일이며 CSV 해시는 같다는 [기존 대조표](experiments/2026-09-19_readiness.md#2026-09-21--iid-선택-반영과-a-학습-후보-목록-확인-완료)를 재사용했다.

새 실행 목적은 `limited_real_training_no_validation`이다. 기존 독립 분할용 `preflight()`를 끄지 않고 `limited_real_preflight()`가 이번 사용자 승인·IID train·정답/품질·보수적 관리 묶음·기존 입력 파일 해시를 확인한다. 미확인 사건 ID는null로 유지하며 관리 묶음 이름은 실제 사건 ID가 아니다. 기존 검수/전처리 결과의 false/null은 당시 이력으로 보존하고, 이번 학습 사용 승인은 [학습 명세](../configs/train_segments.limited-real.json)에 별도로 연결했다. B의 독립 검증 자료가 부족해도 A 학습 전용 실행은 가능하도록 목적을 명시했다.

입력은 기존16장·224×224·전체 화면+여백 배열을 재사용했다. 로더는 `[시간,높이,너비,색상]` uint8 → `[색상,시간,높이,너비]` float32·채널 정규화로 변환한다. batch1, workers0, head, SGD lr0.001/momentum0.9, seed20260918을 유지했다. 사고2초/정상6초의 승인 범위를 자동 변경하지 않았고 새 프레임/미리보기·원본 재디코딩도 없다. 이 시간 차이와 정상 출처1개는 성능 해석의 한계다.

사용자가 받은 공식 `r3d_18-b3b3357e.pth`는133,546,016 bytes/전체SHA-256 `b3b3357ead25631ec9c57362ff2128a92d0427e01e2cd184951a44380c3f2e9d`로 공식 접두 해시와 일치했다. 다운로드 원본을 보존하고 `artifacts/weights/`에 로컬 사본을 만들었다. 에이전트의 새 다운로드/설치/업로드는 없다. Kinetics400 가중치를 불러온 뒤 마지막층만2클래스로 바꿨다. UCF101 추가 학습 모델이나 난수 특징부를 쓰지 않았다.

### 실행 명령과 제한

프로젝트 루트에서 실제 실행한 명령:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv-model-check/bin/python scripts/train_r3d18.py --config configs/train_r3d18.limited-real.json --output artifacts/training/limited-real-iid-20260921-01
```

현재 출력 폴더는 이미 존재하므로 같은 명령을 다시 실행하면 덮어쓰기 보호로 거부한다. 새 이름으로 실행하면 추가 학습이 되므로 결과 확인을 위해 재실행하지 않는다. 설정의 목적을 빼거나 기존 `train_r3d18.local.json`로 바꾸면 다른 경로/이전 대기 입력이다.

첫 준비 순전파1회는 업데이트0회다. 이어2업데이트를 측정하고 저장한 후 같은 optimizer/RNG/진행 상태로23업데이트를 재개했다. 프로브도전체25번/5 epoch 한도에 포함한다. 외부 감시는 프로브60초·worker1800초이며 초기화/저장/재로딩까지 포함하므로 학습 루프30분 한도보다 엄격하다. 개별step30초, 프로세스RSS/MPS driver 각8GiB를 추가 중단 기준으로 설정했다. GPU memory watermark와 CPU fallback은 변경하지 않았다.

제한된 도구 실행에서 `mps_available=false`였고, 승인된 네이티브 실행으로 실제 MPS를 사용했다. 이 사전 접근 상태는 모델 연산 실패가 아니다. 실제 학습 명령은 첫 실행에 종료0으로 성공했다. [명령/외부 시간](../artifacts/training-code-check/limited-real-audit-20260921-01/run-01.json), [최종 결과](../artifacts/training/limited-real-iid-20260921-01/result.json).

### 실제 시간·메모리·상태 확인

| 측정 항목 | 실제 값 | 범위 |
|---|---|---|
| 명령 전체 | 23.351251초 | 입력 준비 검사·프로세스 시작/종료 포함 |
| worker 전체 | 21.426374초 | 장치/모델 준비부터 재로딩까지 |
| 모델 로딩 | 0.699602초 | 공식 로컬 가중치 로딩·장치 전송 |
| 준비 순전파 | 1.427587초 | 입력 읽기/전송 포함, 업데이트 없음 |
| 첫2step 단계 | 3.100893초 | 초기/최종 저장 포함 |
| 재개23step 단계 | 14.759261초 | 체크포인트 복원·최종 저장 포함 |
| 누적 학습 루프 | 16.902196초 | 입력·계산·주기적 체크포인트 등 포함 |
| 최종 재로딩 확인 | 1.196365초 | 새모델 생성/복원·같은 입력 logits 대조 |
| 프로세스 최고 RSS | 846.89MiB | CPU 프로세스 high-water mark |
| MPS driver 관측 최대 | 1,090.64MiB | 단계별 관측값, 순간 peak 미측정 |
| MPS tensor 관측 최대 | 137.28MiB | driver와 합산하지 않음 |

프로브2step에서 계산한 나머지23step 예상은27.121초(최종저장/재로딩 제외)였다. 실제 재개 단계는14.759초였다. 처음2개 표본의 초기 실행 비용을 포함한 추정이 과대했으며 큰 데이터/장시간 학습 예상으로 일반화하지 않는다. 동기화된 계산 합계는9.664462초, 입력 처리0.337880초, step별 체크포인트 저장6.332435초다. 마지막 저장/복원/진단 비용이 따로 있어 이 항목만 더해 전체 시간이라고 하지 않는다.

25개 로그에서 각sample이정확히5회 사용됐다. 가장 긴step은1.521676초로 중단 기준 이하다. 자원/시간 중단 없이 끝났으며 이번 작은 실행에서 Colab 이동을 요구할 문제는 관측되지 않았다. 시스템 전체 메모리 압박·온도·장시간 안정성을 측정한 것은 아니다. 정상적인 보호 설정을 계속 유지한다.

학습 전후 전체 고정 상태 해시와 BatchNorm buffer60개 해시가 동일했다. `fc.weight`/`fc.bias`의 최대 절댓값 변화는 각각0.0416650/0.000425075다. **마지막층은 실제 변경, 특징부와BN 통계는 유지**를 확인했다. 최종 모델을 새로 읽은 logits가 메모리상의 모델과 rtol1e-4/atol1e-5로 일치했다. 독립 평가용 추론이 아니라 저장/복원 무결성 확인이다.

### 모델과 기록 위치

- [최종 모델](../artifacts/training/limited-real-iid-20260921-01/loop/model.pt): 가중치·클래스 순서·설정·자료/코드 버전 포함,132,747,787 bytes.
- [재개용 체크포인트](../artifacts/training/limited-real-iid-20260921-01/loop/checkpoint.pt): 위 정보와 optimizer/RNG/25step·5epoch 진행 상태 포함,132,759,067 bytes. 이번 승인 횟수를 이미 소진했으므로 추가 재개 학습은 새 범위가 필요하다.
- [최종 결과 JSON](../artifacts/training/limited-real-iid-20260921-01/result.json): 실제 장치·시간·메모리·고정 상태·재로딩/재개 결과.
- [초기2step 로그](../artifacts/training/limited-real-iid-20260921-01/probe/train.jsonl)와 [이후23step 로그](../artifacts/training/limited-real-iid-20260921-01/loop/train.jsonl): 합쳐서25번이며 sample ID·loss·시간이 있다. `generalization_metric=false`다.
- [실행 설정](../artifacts/training/limited-real-iid-20260921-01/config.json), [클래스 매핑](../artifacts/training/limited-real-iid-20260921-01/class_mapping.json), [실제 입력 목록](../artifacts/training/limited-real-iid-20260921-01/prepared_inputs.json), [버전/해시](../artifacts/training/limited-real-iid-20260921-01/versions.json), [학습 전 파일/라벨 확인](../artifacts/training/limited-real-iid-20260921-01/preflight.json).

결과는42개 파일/531,082,466 bytes이며 프로브 모델/체크포인트도 보존한다. 기존 모델/원본/검수는 덮어쓰지 않았다. [원본·입력·모델 대조](../artifacts/training-code-check/limited-real-audit-20260921-01/training-verification.json)12항목 통과이며 원본QMUL/사용자다운로드 가중치의 현재 해시도 기존 근거와 같았다.

### 테스트·오류 이력·성능 해석

기존 `test_training.py`에9개 시험을 추가했다. 실제 명령은 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 .venv-model-check/bin/python -m unittest test_training test_training_readiness -v`다. **최종60개 통과(학습 기존24+신규9, 준비27), 내부6.335초/외부7.519008초**, 각시도60초 상한. 생성입력/작은CPU모델로 train-only 목적·공식test제외·보수적그룹·미승인/입력변경 거부·epoch/시간/자원 중단·2+23 재개/재로딩을 확인했다. 기존 독립 평가 분할 보호 시험도 통과했다.

첫59개 시험에서는 생성 fixture를 기존 파일 보호 함수로 반복 저장해4개 오류가 났다. fixture만 수정했고 실제 출력 보호는 유지했다. 검토 중 발견한 진행기록의 같은 파일 재작성도 step별 새파일로 바꾸고 통합시험을 추가했다. 둘째60개 시험에서는 기존 작은모델의 무작위 ReLU가 모두0이 되어 `fc.weight` 변경을 기대한 시험1개가 실패했다. 해당 시험의 특징이0이 되지 않도록 생성모델 초기조건을 명확히 해 최종 통과했다. 실제 데이터/학습 실패로 보고하지 않으며 [세시도 로그](../artifacts/training-code-check/limited-real-audit-20260921-01/tests-03.log)의 이전 파일들도 보존했다.

학습epoch 평균loss는0.934712→0.427780→0.335585→0.214018→0.121698이었다. **같은5구간에서 계산한 학습 손실**이며 검증성능/사고확률 보정의 증거가 아니다. 검증 F1·혼동행렬·오탐/미탐·지리 일반화·공식 시점/위치/유형 벤치마크·사건 단위 평가는 모두 미수행이다. 정상원본1개, 사고사건관계미확인, 클래스별출처/시간길이차이·고정읽기순서·극소표본이라는 한계가 있다.

**다음 하나(제안만): 학습 원본/사건과 겹치지 않는 검증용 정상·사고 자료 묶음 확보·검수.** QMUL의다른시간/복사본이나008·009의재편집본을 독립 검증으로 쓰지 않는다. 최소 정상1원본·사고1원본을 추가해 구조상 분리할 수 있지만 충분한 성능 표본 수는 아니다. 출처·이용조건·원본/사건관계·IID 분할·검수 구간을 함께 받아야 한다. 공식IIDtest는 튜닝에 쓰지 않는다. 이번에는 추가 학습·검증·클라우드·순차영상추론을 자동 진행하지 않았다.

**2026-09-21 최신: 사용자 IID 선택 반영과 실제 A 후보 입력 목록 점검 완료.** 과거 train/train 후보와 007~009는 파일명이 다르고, 당시 두 기록과 현재 CSV 해시는 동일하다. 008·009는 실제 IID train, 007은 test로 학습·튜닝 제외다. A에는 사고2·QMUL 정상3구간이 있으며 처음에는 008·009·qmul_001만 제안한다. QMUL3구간은 원본1개, 사고 사건 관계는 미확인으로 함께 유지한다. 관련31개 테스트 통과/실제0.467초, 학습은 실행하지 않았다.

A는 **입력 후보 확보**, B는 **독립 평가 자료 부족**으로 나눠 기록했다. A의 최신 두 출처 연결·명세/공식 가중치·실행 승인은 남아 있지만 B 부족 자체로 A를 막지 않는다. 현재 학습 설정은 예전 pending 자료를 그대로 가리키므로 바로 학습할 수 있는 명령이라고 안내하지 않는다. 다음 하나는 A 전용 입력 연결과 제한 실행 준비(제안만)다. [파일별 분할·A/B 조건·명령·상한](experiments/2026-09-19_readiness.md#2026-09-21--iid-선택-반영과-a-학습-후보-목록-확인-완료). 아래 ‘평가 방식 선택 대기’ 등은 이전 이력이다.

**2026-09-21 최신:** 기존 CLI에 `--review-readiness configs/review_readiness.local.json` 기록 전용 모드를 추가했다. 승인 사고007~009/QMUL3구간의 정답·품질·공식 분할/원본 관계를 실제 점검했고 **55개 관련 테스트 통과 / 실제0.473초 / inspection_status=completed·held**다. [실행 명령·여섯 구간 표·보류 이유](experiments/2026-09-19_readiness.md#2026-09-21--승인-입력의-학습평가-분리-준비-점검-완료)를 따른다. 새 진단 설정은 실제 학습용 manifest가 아니고 기존 `preflight()`를 대체하지 않는다. 기존 학습 설정은 pending 계획을 유지하며 최신 두 출처 연결·분할 승인 등이 남아 있다. **아래 준비 상태 표와 ‘최종 확인’은 이전 실행 당시 이력**이다.

최종 확인: 2026-09-19. C-1은 2026-09-18의 [로컬 연산 점검](local_model_check.md)이다.

**공통 학습 코드와 단위 테스트는 작성했다. 실제 ACCIDENT 학습·공식 가중치 다운로드·실제 학습 모델의 저장·재개는 수행하지 않았다.** 사용자가 지정한 조건대로 정답·분할이 준비되지 않아 실제 학습을 보류했다.

## 실제 준비 상태

자료 목록·라벨 초안·C-1 보고서·진행 기록과 기존 코드, pending 설정, 저장된 전처리 manifest를 확인했다. 이후 세 구간의 사람 검수가 완료되어 아래 준비 상태를 갱신했다. 이 문서 뒤의 실행 로그·테스트는 C-2 수행 당시 이력이다.

| 항목 | 결과 |
|---|---|
| 선택 자료 | 기존 실제 영상 3개. 다른 영상·synthetic 자료 미사용 |
| 원본 주석 | 사고 시점·유형·위치. 이진 정답으로 치환하지 않음 |
| 승인된 구간 정답 | 별도 검수 파일에 **사고 3개**, 정상 0개. 기존 pending 계획과 과거 출력은 null 유지 |
| 정상 자료 | 검수된 정상 영상·구간 없음 |
| 공식 분할 | 세 영상 모두 IID/지리 분할에서 test |
| 원본·사건 그룹 | 상위 원본·사건·재편집 관계 미확인 |
| 모델 입력 검수 | 세 구간의 기존 미리보기 식별·순서 확인 완료. 승인 라벨 연결 배열·정규화 후 실제 로더 연결은 미실행 |

검수 파일은 `configs/preprocessing_samples.review-20260919.json`이다. 라벨 정책은 여전히 `draft-0.1`이고 개별 세 구간만 검수했으며, 시점 ±1초를 모든 사고의 정답 구간으로 확장하지 않는다. 현재 학습 설정의 `segment_plan`은 기존 pending 계획 그대로이므로 과거 준비 검사에서 보인 승인 0개와 최신 검수 파일의 사고 3개를 구분한다. 정상 자료·그룹·학습/검증 분할이 없어 실제 학습은 계속 보류하며 test 영상을 학습용으로 이동하지 않는다.

## 코드와 설정

| 파일 | 역할 |
|---|---|
| `src/boram3_cctv/training_data.py` | 라벨·분할·그룹·해시 검사, 승인된 train 구간만 제한적으로 준비 |
| `src/boram3_cctv/training.py` | 입력 변환, R3D-18 구성·학습 범위, 루프·저장·재개·재로딩 추론 |
| `scripts/train_r3d18.py` | 로컬/Colab 공통 CLI와 별도 프로세스 시간 제한 |
| `configs/train_r3d18.local.json` | 경로·장치·배치·프레임·학습 범위·한도 |
| `configs/train_split.template.json` | 비어 있는 분할 형식. 실제 승인·분할·정답 아님 |
| `scripts/requirements-training.txt` | 기존 모델/전처리 요구 패키지를 결합 |
| `tests/test_training.py` | 새 학습 코드 테스트 24개 |

기존 검사·전처리·C-1 코드는 수정하지 않았다. 원본 5개와 기존 코드·설정·환경 명세 등 사전 해시를 기록한 **21개 파일이 동일**하다. Git은 첫 커밋이 없으며 커밋·푸시·업로드하지 않았다.

## 준비 검사와 승인 파일

모델 생성·가중치 읽기·영상 디코딩 전에 다음을 확인한다.

1. 구간 정답이 파일 해시, 정확한 시작/끝, 정책 버전, 근거·검수자·날짜와 연결돼 있어야 한다.
2. train에 검수된 정상 0과 사고 1이 모두 있어야 한다. 미검수·판단 불가·읽기 실패를 정상 0으로 만들지 않는다.
3. 분할 파일은 선택 plan의 샘플을 정확히 한 번씩 포함하고 plan·전처리 설정 해시에 연결돼야 한다.
4. 내용 해시·상위 원본 ID·사건 ID·묶음 ID 중 하나라도 분할을 넘으면 차단한다. 이는 **선택 목록과 제공된 대응표 범위의 검사**이며 전체 데이터 중복 부재를 보장하지 않는다.
5. 공식 test를 train/validation으로 옮길 수 없다. IID/지리 분할 중 사용할 기준을 명시한다.
6. 공식 분할이 원래 없는 외부 정상 자료는 공식 train 값을 만들어 넣지 않는다. 외부 출처·그룹·별도 분할 검수를 요구하며 기존 공식 test 값을 무시할 수 없다.

현재 `split_manifest=null`, `pretrained_weights_path=null`을 유지했다. `--check-only`는 자료 준비만 확인한다. 가중치·MPS·실제 디코딩 성공까지 확인하는 명령은 아니다.

분할 파일의 최상위 `review`에는 `status`, `basis`, `reviewer`, `reviewed_at`이 필요하다. 실제 검수 후에만 approved로 기록한다. `plan_sha256`은 선택 plan 파일의 SHA-256, `preprocessing_sha256`은 `training_data.fingerprint(config['preprocessing'])`다. 자료 버전·분할 버전도 명시한다. `segments`의 각 항목은 아래 필드를 갖는다. 다음은 **형식 설명**이며 값을 임의로 채우지 않는다.

```json
{
  "sample_id": null,
  "source_sha256": null,
  "split": null,
  "source_group_id": null,
  "upstream_original_id": null,
  "event_id": null,
  "group_review": {
    "status": "unreviewed", "basis": null,
    "reviewer": null, "reviewed_at": null
  },
  "training_authorized": false
}
```

split은 검수된 train/validation/test 중 하나다. 정상 자료의 event_id는 존재하지 않는 사고를 뜻하는 ID로 만들지 말고, 같은 촬영·사건 맥락을 묶는 검수된 기록 단위를 사용하며 근거를 남긴다. 출처에 ID가 없다면 확인한 관계를 기록한 별도 관리 ID임을 명시한다. 미확인이면 학습을 보류한다.

외부 자료에 공식 분할이 없다면 `source_origin=external_real`과 위 review와 같은 구조의 `external_source_review`가 추가로 필요하다. 원본 두 분할 값이 모두 없는 경우에만 적용한다. 원본 CSV는 수정하지 않고 별도 자료 목록 파일을 `metadata_path`로 지정할 수 있다. 현재 로더는 데이터 루트 아래 `real_videos/`의 MP4 경로를 지원한다.

## 모델과 입력

공식 [R3D-18 KINETICS400_V1](https://docs.pytorch.org/vision/0.25/models/generated/torchvision.models.video.r3d_18.html)의 400종 모델 가중치를 먼저 읽고 마지막 fc를 정상/사고 2종으로 교체한다. Kinetics 사전학습은 사고 학습 완료가 아니다.

가중치는 명시된 로컬 파일만 읽고 공식 SHA-256 접두사 `b3b3357e`와 전체 해시를 기록한다. 다운로드 API는 없다. 공식 파일은 문서상 약 **127.4 MB**이며 향후 받기 전에 별도 승인받아야 한다. 이번에는 받지 않았다.

기본 `train_scope=head`는 특징 추출부 파라미터를 고정하고 전체 모델을 eval, 마지막 fc만 train으로 둔다. 따라서 BatchNorm의 running mean/variance도 유지된다. `all`도 설정으로 지원하지만 범위 변경은 후속 승인과 자원 점검 대상이다.

기본값은 **batch=1 / 16프레임 / 224×224 / float32 / num_workers=0**이다. C-1의 112픽셀 난수와 다르며 기존 실제 전처리의 224픽셀 설정을 유지했다. 112로 바꾸려면 plan과 모델 설정을 함께 변경하고 새 입력을 검수해야 한다.

기존 디코더를 재사용해 실제 PTS로 16개 프레임을 고르고 화면 전체를 비율 유지 resize + 검은 여백으로 맞춘다. 중앙 crop·반전·증강은 없다. 승인된 train 구간만 준비하며 validation/test는 모델 선택이나 디코딩에 사용하지 않는다. 구간 최대 10초·plan 최대 3개·프레임 2~32개·영상별 별도 프로세스 45초 제한이다.

승인 배열 `[T,H,W,C]` uint8 RGB 0~255를 로더에서 `[C,T,H,W]` float32로 바꾸고 255로 나눈다. 공식 채널 평균 `[0.43216,0.394666,0.37645]`, 표준편차 `[0.22803,0.22145,0.216989]`로 정규화한다. 공식 기본 transform의 중앙 자르기는 적용하지 않았으므로 공간 전처리가 다르며 그 성능 영향은 미검증이다.

미리보기는 정규화 직전 RGB다. 역정규화하면 픽셀·시간순서가 복원되는지 테스트했다. 실제 작은 사고의 가시성은 사람이 원본과 비교해야 한다. 이번 실제 자료에는 승인 정답이 없어 이 학습 입력 변환을 적용하지 않았다.

## 실행 한도·저장·재개

최대 20 step 또는 300초 중 먼저 도달하면 중단한다. 내부 시간 검사 외에 CLI의 별도 프로세스 제한이 초기화·루프·저장·재로딩까지 전체 300초로 제한한다. 구간 준비는 이 시간 밖이며 영상별 45초 제한이다. 전체 명령이 반드시 5분 안에 끝난다는 뜻은 아니다.

장치는 cpu/mps/cuda 중 명시하며 사용 불가 시 오류를 기록한다. 자동 CPU 전환과 메모리 보호 override를 허용하지 않는다. Mac에 CUDA 설치를 강제하지 않는다.

실제 학습 준비가 됐을 때 생성할 결과:

```text
출력 폴더/
  preflight.json / config.json / versions.json
  inputs/sample_NNN/
    frames.npy / frames.json / frames/*.png
    preview.png / source_reference.png / provenance.json
  loop/
    train.jsonl       step, 구간 ID, loss, 시간, 장치
    checkpoint.pt     모델+optimizer+RNG+다음 구간 위치+step+설정/버전
    model.pt          추론용 모델 상태+설정/버전/클래스 매핑
  result.json / summary.json
  worker.stdout.log / worker.stderr.log
```

checkpoint는 임시 파일을 완성한 후 교체한다. 중단 시 마지막 완전히 저장된 checkpoint까지만 사용한다. 첫 저장 전에 중단되면 재개 파일이 없을 수 있다. `.tmp`를 완료 파일로 사용하지 않는다. 자료·클래스·전처리·학습 설정·코드 해시가 바뀌면 재개를 거부한다. 경로와 장치는 변경 가능하지만 장치 간 완전한 수치 일치까지 보장하지 않는다.

누적 step이 20에 도달했다면 재개해도 추가 업데이트하지 않는다. 각 CLI 작업의 시간 제한도 별도로 적용된다. 완료 후 저장 모델을 재구성해 작은 입력 1개에서 저장 직전 출력과 비교하며 추가 학습 step을 수행하지 않는다. 실제 모델의 재개 실행은 보류했고 작은 CPU 모델의 단위 테스트로 기능을 확인했다.

## 실제 명령과 결과

모델 환경에 PyAV 16.1.0·PyYAML 6.0.3을 기존 캐시에서 오프라인 추가했다. 기존 torch/torchvision과 다른 가상환경은 유지했고 네트워크·전역 설치는 없었다.

```bash
UV_CACHE_DIR=/private/tmp/boram3-s0-uv-cache uv pip install --offline --python .venv-model-check/bin/python -r scripts/requirements-training.txt
.venv-model-check/bin/python scripts/train_r3d18.py --check-only --output artifacts/training-code-check/preflight-02
.venv-model-check/bin/python -m unittest discover -s tests -v
```

실제 자료 결과 일부:

```json
{
  "status": "held",
  "selected_segments": 3,
  "approved_segment_labels": 0,
  "train_segments": 0,
  "training_run": false,
  "weights_downloaded": false,
  "mode": "check_only"
}
```

종료 코드 2는 준비 부족을 알리는 보류다. 자세한 사유는 `artifacts/training-code-check/preflight-02/summary.json`. 실제 학습 모델·checkpoint는 생성하지 않았다.

추가로 `--check-only`를 뺀 일반 실행 명령도 `--output artifacts/training-code-check/guard-01`로 확인했다. 역시 종료 2 / held / training_run=false였고 inputs·loop 폴더가 생성되지 않아 디코딩·모델 실행 전에 멈췄음을 확인했다.

최종 테스트는 **80개 통과, 실패·건너뜀 0, 9.073초**. 로그는 `artifacts/training-code-check/tests-03.log`. 테스트 입력·가중치는 임시 폴더에서 생성하고 정리했다. 검증 범위는 [테스트 설명](tests.md)에 있다.

## 직접 확인·이후 실행 형태

프로젝트 루트에서 새 출력 이름으로 확인한다. 현재 자료라면 held가 예상 결과다.

```bash
.venv-model-check/bin/python scripts/train_r3d18.py \
  --config configs/train_r3d18.local.json --check-only \
  --output artifacts/training-code-check/my-preflight-01
```

다음은 검수 자료와 승인 후 받은 가중치를 설정했을 때 사용할 형태다. 현재 학습을 시작하라는 뜻이 아니며 이 명령도 준비 검사를 통과해야 한다.

```bash
PYTORCH_ENABLE_MPS_FALLBACK=0 .venv-model-check/bin/python scripts/train_r3d18.py \
  --config configs/train_r3d18.local.json --output artifacts/training/first-01

PYTORCH_ENABLE_MPS_FALLBACK=0 .venv-model-check/bin/python scripts/train_r3d18.py \
  --config configs/train_r3d18.local.json --resume artifacts/training/first-01/loop/checkpoint.pt \
  --output artifacts/training/resume-01
```

Colab에서도 같은 저장소·코드를 사용하고 경로·device만 설정할 수 있다. `python scripts/train_r3d18.py ...`로 실행한다. 이번 Colab 준비·업로드·실행은 없었다.

## 본격 학습에 필요한 자료와 예상 시간

검수된 사고/정상 구간, 현재 test와 분리된 train 사고 자료, 원본·사건 그룹과 분할 근거, 모델 입력 미리보기 검수가 필요하다. 사고 1구간·정상 1구간은 로딩 점검용 최소 예시일 뿐 일반화 평가에 충분한 수량이 아니다.

**현재 본격 학습 시간을 신뢰할 숫자로 예상할 근거가 부족하다.** C-1의 5.909초/step은 112픽셀 난수·전체 층·추가 검사 포함 실측이다. 이번 224픽셀 실제 구간·분류층 학습·저장 비용과 달라 그 값을 곱해 시간을 제시하지 않는다. 단위 테스트 9.073초도 모델 속도가 아니다.

자료 준비 후 제한 실행에서 준비·초기화·step·저장 시간을 각각 측정하고 `준비 + 초기화 + ceil(train 구간 수/batch) × epoch 수 × 대표 step 시간 + 저장 비용`으로 다음 규모를 추정한다. 현재 step/시간 제한은 실행 상한이며 완료 시간 예측이 아니다.

다음 완료 기준은 **검수한 구간과 올바른 정답·원본 ID가 함께 읽히는 것**이다. 사고 예측값이나 일반화 성능을 이번 완료 조건으로 삼지 않는다.

<a id="colab-evaluation"></a>
## 2026-09-22 — Mac 부하를 줄이기 위한 Colab 평가 준비

**현재 목적은 기존 저장 모델을 새 독립 원본으로 평가하는 것**이다. 개발·사람 검수는 로컬에서 이어가고, 평가·이후 승인된 학습은 Colab GPU도 선택한다. 로컬 실패는 필수 조건이 아니다. CCTV-only·R3D-18·원본 보존·IID 기준 분리와 새 설치/다운로드/외부 전송/추가 학습의 승인 규칙은 유지한다. 지금은 코드·노트북·전달 ZIP 준비와 로컬 단위 시험만 완료했으며 **Colab 실행·실제 검증·업로드·재학습은 미실행**이다.

### 필요한 실제 자료와 예약 상태

| 용도 | 근거/실제 존재 | 이번 전달 묶음 | 아직 필요한 것 |
|---|---|---|---|
| 저장 모델 | `artifacts/training/limited-real-iid-20260921-01/loop/model.pt`, 132,747,787 bytes | 포함, 원본 복사만 | Colab 실제 로딩/추론 확인 |
| 모델 설정·클래스·학습 버전 | 같은 실행의 `config.json`, `class_mapping.json`, `versions.json` | 포함 | 역사적 `device=mps`는 보존하고 평가 장치는 런타임에서 별도 선택 |
| 학습 원본 배제 근거 | 같은 실행의 `prepared_inputs.json`: 사고008·009/정상QMUL3 | 기록만 포함, 학습 프레임/영상은 제외 | 새 원본이 해당 관리 그룹과 독립인지 사람 근거 |
| 사고 후보1 | CSV15행 `real_videos/F0NHENn-HS4_00.mp4`, IID train / geo test | 영상·정답 없음. CSV 근거와 예약만 포함 | 실제 파일, 출처/이용 조건, 사건 관계, 2초 구간 정답·224 품질 검수 |
| 사고 후보2 | CSV23행 `real_videos/XaqA-uzqeNc_00.mp4`, IID train / geo test | 영상·정답 없음. CSV 근거와 예약만 포함 | 위와 같음. 파일명이 달라도 독립 사건이라고 확정하지 않음 |
| 정상 후보1·2 | QMUL 및 서로와 다른 두 촬영 원본 | 파일명도 미확정. 정상 정답 생성 없음 | 실제 파일·출처/분할·이용 조건·원본 관계와 2초 구간 검수 |
| 실제 검증 입력 | 승인 구간 0 | `segments=[]`, 예약 4개는 별도 | 승인된 배열·시각·미리보기·정답/품질/분리/사용 기록 |

앞선 자료 미확보 기록을 재사용했다. 이번 요청에 새 파일 경로가 없어 전체 폴더를 다시 검색하거나 영상을 재검사하지 않았다. 예약의 `[1.533,3.533)`, `[1.250,3.250)`는 이전 주석 주변 **검수 위치 제안**이며 승인된 평가 구간·사고 시작/종료 정답이 아니다. 공식 IID test 영상은 열지 않으며 검증용으로 쓰지 않는다. 공식 사고 시점·위치·유형 벤치마크와 우리의 정상/사고 구간 평가는 다르다.

### 전달 묶음과 크기 — 로컬 생성, 외부 전송 없음

[ZIP](../artifacts/colab/evaluation-20260922-01/colab-evaluation.zip): **133,353,096 bytes = 약133.35 MB / 127.18 MiB**, 파일13개와 목록 영수증1개. 실제 생성0.325초. [파일별 크기·SHA-256·미확보 목록](../artifacts/colab/evaluation-20260922-01/transfer_manifest.json), [생성 결과](../artifacts/colab/evaluation-20260922-01/bundle_result.json).

포함 범위는 위 모델/학습 근거5개, 현재 평가 명세1개, 원본 CSV1개, Python 모듈5개, 노트북1개다. 전체 데이터셋·원본 영상·학습 입력 배열·재개 체크포인트·공식 초기 가중치·`.venv`·캐시·토큰·비밀번호는 포함하지 않았다. 학습 검수 기록의 검수자 별칭은 포함된다. 상대경로와 해시만 사용하며 ZIP의 텍스트에서 Mac 절대경로가 없음을 확인했다. 전달 허용 여부를 사용자가 확인한 뒤 직접 올린다.

새 영상을 받으면 **로컬에서 기존 전처리/검수 방식을 적용한 뒤** 승인된 `frames.npy`, `frames.json`, `preview.png`와 검수 근거만 다음 묶음에 추가한다. 원본 영상은 로컬에 보관한다. 16×224×224×RGB uint8 배열은 구간당 2,408,448 bytes + 작은 NPY 헤더다. 4구간이면 배열 약9.63MB, 최대10구간이면 약24.09MB가 추가되고 미리보기 PNG/JSON 용량은 그때 실제 측정한다. 이는 미래 예상이며 지금 ZIP에 들어 있지 않다. 이 방법은 Mac에서 소량 입력을 검수하고 GPU 평가만 Colab으로 옮기는 선택이다. 전체 동영상을 Drive로 복사해 다시 디코딩하는 방법은 이번에 쓰지 않는다.

재생성 명령(기존 출력 보호를 위해 새로운 출력 폴더 사용):

```bash
.venv-model-check/bin/python scripts/prepare_colab_bundle.py \
  --manifest configs/evaluation.colab.pending.json \
  --output artifacts/colab/evaluation-20260922-02
```

현재 pending 명세로 다시 만들면 여전히 승인 구간0이다. 반복 생성만으로 평가 준비가 끝나지 않는다.

### 공통 코드 흐름과 차단 기준

[노트북](../notebooks/evaluate_r3d18_colab.ipynb) → [evaluation.py](../src/boram3_cctv/evaluation.py)의 `preflight` → 저장 모델 로딩 → 기존 `training.SegmentDataset`/`tensor_from_array` → `predict` → 예측 JSONL 저장/닫기 → `compare` → HTML/JSON 보고서 → Drive 복사 순서다. 노트북은 실행 순서·경로·복사만 담당하며 학습/평가 알고리즘을 복제하지 않는다.

- 명세는 2초·16장·224·전체 화면 letterbox·KINETICS400 채널 정규화·float32·동일 클래스 매핑을 고정한다. 학습 당시 정상6초 기록은 보존한다. 현재 평가에6초 입력을 넣지 않으며, 2초 전처리를 바꾼 실험도 아니다.
- `preflight`는 기존 `approved_target`로 사람의 정확한 구간/원본 해시/정답/정책을 확인한다. 입력 배열/시각/미리보기의 해시가 품질 검수와 맞아야 한다. 새 평가 승인, 클라우드 이용 근거, 학습 관리 그룹과의 독립성 확인, 공식 분할 근거도 필요하다. 사고 후보는 실제 CSV의 path·행·IID train과 대조한다. 사람의 근거 없이 파일명만으로 사건 독립성을 증명하지 않는다.
- 예약은 정답이 아니므로 현재 실제 실행에서 `approved_validation_segments_required; reservations_are_not_labels`로 차단됐다. 사람이 JSON의 `approved`만 바꿔 우회하는 절차를 안내하지 않는다. 자료를 주면 기존 검수 기록을 연결해 새 명세를 만든다.
- 모델은 `weights=None`으로 구조를 만든 뒤 **기존 최종 state_dict**를 strict 로딩한다. 임시 가중치로 평가하거나 초기 가중치를 내려받지 않는다. eval·inference_mode, gradient 비활성, 상태 비교를 사용하며 optimizer가 없다. BN/가중치 유지 확인도 결과에 기록한다.
- 한 번에1구간, 최대10개다. 추론 루프는 최대300초이고 부모가 50ms 간격으로 감시해 멈춘 자식도 종료한다. 모델 준비에는 별도120초 상한이 있다. 장치 실패 시 CPU로 자동 전환하지 않는다. 프로세스가 강제 종료되면 상태 보존 확인은 미확인으로 남기고, 저장된 행만 비교하며 남은 행은 미실행으로 모두 표시한다.
- 정답은 모델 입력에 넣지 않는다. `predictions.jsonl`에는 ID/예측/logits/점수/상태만 기록하고, 닫힌 뒤 정답을 결합한다. 클래스1 softmax가 사고 점수이고 argmax(동점 정상0)를 유지한다. 보정된 사고 확률이 아니다.
- 결과의 클래스별 구간/파일/관리 그룹 수와 오탐·미탐, 혼동행렬, Accuracy·사고Precision/Recall/F1, 평균 loss는 **성공한 구간 범위**와 실패/미실행 수를 함께 표시한다. 분모가0이면 해당 지표는 null이다. 손실은 기존 학습과 같은 가중치 없는 CrossEntropyLoss이며 고정 최종 모델의 성공 구간 평균이다. 학습 중 평균이나 사건 단위 지표가 아니다.

### 사용자가 누를 순서

1. 먼저 사고 후보 두 파일과 **QMUL과 다른 정상 원본 두 개**의 실제 경로·출처를 알려준다. 사고 후보는 기존 `data/local/accident/real_videos/`에 두어도 된다. 정상은 보관 위치를 알려주면 되고, 없는 파일명을 임의로 만들 필요 없다. 구간 정답·입력 품질·원본 관계·이용 허용을 확인해 점수 확인 전 목록을 고정한다.
2. 위 ZIP과 `transfer_manifest.json`을 확인한다. 지금 ZIP은 준비용이다. 승인 입력이 추가된 최종 묶음의 업로드 범위를 확인한 뒤, Drive의 **제안 위치** `내 드라이브/boram3-cctv-evaluation/incoming/colab-evaluation.zip`에 직접 업로드한다. 이 폴더가 이미 있다고 가정하지 않는다.
3. Colab에서 파일 → 노트북 업로드로 `notebooks/evaluate_r3d18_colab.ipynb`를 연다. GPU 런타임을 선택하고 **1→2→3** 셀을 순서대로 실행한다. 환경과 기존 패키지를 확인한다. Colab에 torch/torchvision/NumPy/Pillow가 없거나 불일치하면 설치하지 않고 중단한다. Mac용 고정 requirements나 프로젝트 전체 `pip install`은 사용하지 않는다.
4. 전송 내용과 Drive 권한을 확인한 뒤 3번 셀의 `CONFIRM_TRANSFER=True`를 설정하고 **4번 셀**을 실행한다. 명시 목록 파일만 런타임으로 복사하고 해시/승인을 대조한다. **현재 ZIP은 여기서 승인 구간 부족으로 멈춘다.** 이는 GPU 오류가 아니다.
5. 승인된 새 묶음으로4번이 통과하면 **5→6→7**을 실행한다. 5번은 저장파일 CPU 로딩/설정 표시,6번은 제한된 자식 프로세스 안에서 실제 CUDA 모델 로딩·평가,7번은 결과 저장이다. 추가 학습 셀은 없다.
6. Drive의 `boram3-cctv-evaluation/results/<실제 run_id>/`에서 결과 폴더 전체를 보관한다. HTML과 `previews/`를 함께 받아 Mac에서 `report.html`을 열면 된다. Drive/Colab에서 직접 저장·다운로드한 이력은 현재 없다.

Colab의 GPU와 런타임은 가용성에 따라 달라지고 종료 시 로컬 런타임 파일을 잃을 수 있으므로 결과를 Drive에 보관하도록 했다. [공식 Colab FAQ](https://research.google.com/colaboratory/faq.html). 모델·입력·분할을 같게 유지해도 CUDA와 MPS 또는 라이브러리 버전이 다르면 수치가 완전히 같다고 가정하지 않는다.

### 결과 파일과 시간 범위

- `fixed_manifest.json`: 점수 확인 전에 고정한 구간/라벨/출처/분리/전처리/모델 정보.
- `predictions.jsonl`: 정답을 결합하기 전 예측. 실패·미실행 행도 포함한다.
- `comparison.json`, `report.html`, `previews/`: 정답과 모든 예측 비교, 샘플별 loss·원본 시각·미리보기·지표·한계. 사용자의 직접 확인은 pending.
- `device.json`, `worker_result.json`, `worker.log`: 실제 GPU/라이브러리·추론 루프 시간·가중치/BN 상태 확인·CUDA 할당/예약 메모리 최고값. 할당과 예약 값을 합해 총 메모리로 표시하지 않는다.
- `timing.json`: 런타임 복사, 모델 파일 사전 확인, 평가 함수 시작→준비·모델 로딩·추론·비교·HTML 저장 종료까지를 구분한다.
- `drive_export_timing.json`: 위 활성 작업 시간 + 실제 Drive 결과 복사 시간. 패키지 준비·Drive 인증·사용자 셀 사이 대기·마지막 시간 영수증 쓰기는 제외한다. 기존15.10초와 측정 범위가 다르며 이를 전체 시간으로 재사용하지 않는다.

### 실제 확인과 아직 미실행인 것

- 로컬 macOS/arm64 기존 환경에서 새18개 단위 시험 통과(1.435초). 마지막 노트북 미리보기 표시 변경 후 해당1개 시험 재실행 통과(0.089초). 생성 입력·작은 CPU 테스트 모델만 사용했고 실제 R3D-18 성능 수치가 아니다.
- 파일 허용 목록·ZIP CRC·크기·Mac 절대경로 미포함, pending 실제 차단, 원본 모델 해시 보존을 [로컬 준비 결과](../artifacts/colab/evaluation-20260922-01/local_preparation_check.json)에 기록했다.
- 새 모델 학습/실제 영상 추론/Colab CUDA 실행/Drive 인증·업로드·보고서 저장은0회다. 클라우드 패키지 호환성·속도·메모리·정확도는 아직 미검증이다. 노트북 셀 문법/빈 출력은 검사했고 Colab UI와 HTML 시각 렌더링은 미검증이다.

### 이후 자료 확대의 분할 방침

현재 예약한 네 후보는 **개발용 검증 전용**으로 유지한다. 기존 소수 학습·예약 자료를 비율에 맞춰 다시 나누지 않는다. 추후 충분한 자료를 확보하면 공식 IID test를 보존하고, 학습 사용이 허용된 풀 안에서 원본/사건/재편집 관리 그룹 기준 **약80:20 학습:개발용 검증**을 출발점으로 제안한다. 같은 그룹은 한 분할에만 두고, 파일/구간 개수가 아닌 그룹 수와 클래스 구성을 확인한다. 정상 원본의 비공개 최종 평가용 묶음도 학습·개발 검증과 별도로 확보해야 한다. 예약 네 후보나 이미 개발 중 본 자료를 새 최종 평가로 바꾸지 않는다. 다음 행동은 새 독립 원본의 확보·로컬 검수 하나이며, 재학습은 별도 승인한다.


## 2026-09-23 — 확대 실험·발표 보고 기준 (설계, 지표 루프 구현 대기)

### 목적과 모델 선정 설명
목표는미학습CCTV의충돌·전복발생구간을찾아원본시각/근거와함께보여주는것이다. R3D-18은3D합성곱으로영상의공간·시간변화를함께처리하는잔차네트워크기준선이다. 기존training.py의모델생성코드에서torchvision공식KINETICS400_V1를로컬가중치로읽고fc를2출력으로교체한다. 초기화/이전모델이어학습을구분한다. 선정이유는기존공식가중치·공통Python코드재사용과Mac/Colab제한실행검증경험이며,현재자료에서다른모델보다우수하다고비교한근거는없다. 특징부/BatchNorm을고정하고fc만학습한범위를모델전체튜닝으로과장하지않는다. 확장모델은후속비교후보이지지금추가하지않는다.

### 파이프라인과 재현성
원본/메타정보→라벨근거(사람검수/CSV파생/사용자정상가정)→원본그룹분할→선택시간의16장→224letterbox→THWC uint8에서CTHW float32/정규화→R3D-18→정상/사고출력→손실/업데이트→epoch고정모델평가→JSON/CSV/HTML/Streamlit. 기존33구간은정상6초·사고2초였으며새2초통일은별도변경으로기록한다. 실험ID/코드SHA/자료목록SHA/분할SHA/가중치SHA/seed/장치/라이브러리/학습률/최적화기/학습층/전처리/판정규칙/실패사유/시간메모리를연결한다. 데이터확대와시간통일을동시에하면각변경의단독기여는확정할수없다. 같은검증목록으로현재모델도평가해야전후비교가가능하며이미학습한원본을새검증으로이동하지않는다.

### 요청한 epoch 표의 계산 계약
`Epoch | Train Loss(online) | Train F1 | Validation Loss | Validation F1 | Validation Recall | Validation PR-AUC(AP) | FP/FN | epoch초 | 평가초 | 핵심 해석`을목표표로한다. Accuracy·Precision·클래스별loss·표본/원본수·측정메모리는별도표또는추가열로보인다. 모든F1/Recall/Precision은사고1을positive로명시하며macro와혼용하지않는다. TrainF1은epoch종료후고정가중치eval/inference모드로학습목록을평가한값;변하는가중치의학습중배치예측합산값과혼동하지않는다. 일부학습자료만평가하면TrainSubsetF1과실제수를쓴다. Validation은고정된미학습개발목록이며group독립성미확인시참고평가로표시한다.

PR-AUC는표에서`AP`를명시하는AveragePrecision정의로고정할계획이며PR곡선사다리꼴면적과혼용하지않는다. 사고점수전체를사용하고단일임계값Recall/F1과구분한다. 양클래스가없거나분모가0인경우null와사유를표시한다. 기존argmax/동점정상규칙을유지하고최종test로임계값/모델을고르지않는다. 예측/정답/점수를샘플별로남긴뒤지표계산하며평가실패행을정상으로바꾸거나조용히제외하지않는다. 학습cross_entropy는현재클래스가중치없는구현이고online loss는업데이트중평균;ValidationLoss는고정epoch모델의평균으로구분한다. 재개step/epoch중복을제거하고부분epoch이면부분값임을표시한다.

### 시각화·발표·테스트 관점
발표는문제/완료기준→데이터와분할→모델선정이유/구조→파이프라인→실험변경표→epoch곡선/PR곡선/혼동행렬→오탐미탐근거→속도/메모리→한계/다음실험순서로구성한다. 높은TrainF1·낮은ValidationF1은과적합/분포차이의징후이며원인확정은아니다. 사고ACCIDENT/정상DETRAC이라는출처차이를배우는위험도함께기록한다. 정상판정가정정답의한계를명시한다. 테스트수/입력연결성공은모델성능과분리하고분할누수·시간순서·라벨출처·저장재개·BN보존·실패처리·지표의정의/단일클래스/동점/재개중복등을검증근거로제시한다. 구간F1과연속사건평가(미탐사건/오탐빈도/탐지지연)는다른결과다.

기존33구간실제loss표는scale-200-20260923-01/report.html에재사용했다. epoch별TrainF1/Validation지표와PR곡선루프/Streamlit새실험등록은아직구현·실행대기다. 과거값을상상하거나재학습하여원래기록처럼쓰지않는다. 새200후보는추가사고파일후보수이며독립사건200개/학습완료200개를뜻하지않는다. 정상수량과촬영그룹분할·전처리상한·epoch평가시간은최종실행명세에서정한다.


## 2026-09-23 — 500개 확대 요청의 가용 범위

현재원본분할/기존학습/예약/family/시각제외조건을적용한새사고학습후보는421개(요청500보다79개부족)다. 기존사고30개를재사용하면451사고구간규모를검토할수있다. 정상비율/촬영그룹/실행상한은미확정이다. 이전200개와개발검수20목록을보존했다. 메타정보계획상한만500으로변경하고실제학습기능의상한을늘리거나500학습을실행하지않았다. 관련29시험통과. 다음확대실험은자료수/라벨근거/분할/코드·모델버전과실제epoch성능/시간을기존보고기준에연결한다.


## 2026-09-23 — 승인된400구간 실제 입력 생성(학습 아님)

최신 `notebooks/prepare_detrac_inputs_colab.ipynb`를 Colab에 다시 열고 **10. 확대400구간 실제 입력 생성** 아래 셀만 실행한다. GPU 불필요. `expansion-inputs-code.zip`(30,918bytes)을 선택하며 이전 `expansion-preparation-code.zip`과 혼동하지 않는다. 경로는 성공한9단계의VIDEOS/DETRAC와 같아야 한다.

노트북은 패키지 확인→Drive 연결→작은 코드 ZIP 선택→기존 Python 모듈로400구간 전처리→새 Drive 폴더 저장을 수행한다. 1~9단계 반복 불필요. 진행 중 `17/400 expansion_0016: prepared_not_split` 형태의 상태가 보인다(형식 예시이며 실제400개 실행 결과가 아님). 개별 실패/시간초과는 manifest에 남는다. 전처리600초 상한, Drive저장별도120초. 입력배열약919MiB+이미지별도; 원본ZIP을복사/전체해제하지 않는다.

완료 출력의 새폴더에서 **manifest.json / execution.json**을 공유한다. report.html과미리보기는 원본시간→프레임순서→정답근거 순서로 확인한다. 사용자가 열어봤다는 답변 전에는 사용자 검수완료로 기록하지 않는다. 잘못된 셀/해시 오류에서는 파일명을 바꾸거나 안전검사를 지우지 않는다.

공통 실행 진입점(실제 경로를 지정할 때만 실행): `python scripts/prepare_expansion_inputs.py --request <request.json> --receipt <file_connection.json> --videos <영상폴더> --archive <DETRAC-Images.zip> --output <새결과폴더>`. Colab셀은 이 공통코드를 호출하며 별도학습코드를복제하지 않는다. 입력 생성은끝나도 split미확정은유지한다. 정확도/F1은 아직 계산하지 않는다.


### 2026-09-23 10단계 수정판 — expected_two_seconds 해결

실제2초구간의소수계산오차를엄격비교하여중단한코드오류를수정했다. 새 `artifacts/colab/expansion-inputs-20260923-02/colab-cell.py` 전체를기존10단계셀에교체하고같은폴더 `expansion-inputs-code.zip`(30,944bytes)을선택한다. 새셀/ZIP해시는한쌍이며이전ZIP은보존만한다. real_videos기본경로반영. 기존세션에서는설치와1~9단계반복불필요. 실제400행검사/관련56시험통과,Colab재실행대기. 원본구간시각·정답·모델은변경없음.


### 2026-09-23 정상183구간 재개(11단계)

`artifacts/colab/expansion-resume-20260923-01/colab-cell.py` 전체를Colab새셀에붙여넣고실행한다. `expansion-resume-code.zip`을선택한다. 기존1~10단계반복금지. 성공폴더경로는사용자제공값으로고정했으며이전manifest/배열해시검사를삭제하지않는다. 기존199입력참조/사고18오류보존/정상183만시도한다. 600초상한·Drive저장별도120초. 재개폴더의manifest.json과execution.json을공유한다. 원래199입력폴더도보존한다. 새manifest의input_origin/input_base를읽어야하며그냥새폴더에모든배열이있다고가정하지않는다. 이번은전처리재개로학습아니다.


## 2026-09-23 — 382입력 학습 연결 제안

artifacts/colab/expansion-training-20260923-01/training-input-proposal.json에사고182·정상200의두Drive기준경로/해시/정답근거/보수관리그룹을연결했다. 18실패보존. 공식test/검증예약제외,독립검증미준비로모두train후보. 기존SegmentDataset필드와맞췄지만원격파일검증/확대실행목적게이트/학습셀은남았다. 현재상태는실행가능학습설정이아니므로기존학습명령으로바로실행하지않는다. 제안은head/5epoch또는30분/최대1910step,3step60초probe별도. 공식가중치기존보유여부확인필요,추가다운로드자동실행없음. 실제학습승인·실행미완료.


## 2026-09-23 — 382구간 Colab 학습 노트북(실행 승인 대기)

[노트북](../notebooks/train_expansion_r3d18_colab.ipynb), [코드 ZIP](../artifacts/colab/expansion-training-20260923-02/expansion-training-code.zip)(86,232bytes). 새노트북의1~4단계만실행해환경/코드/설정을확인한다. 입력전처리노트북의단계번호와다르다. GPU런타임선택은사용자가하며코드는설치/다운로드를하지않는다. 패키지누락은메시지를공유한다.

3단계WEIGHTS_FILE은공식r3d_18-b3b3357e.pth의실제Drive경로가있을때만채운다. 없으면빈값으로4설정점검까지가능하다. 이파일은프로젝트model.pt와다르며새사고분류층초기화실험이다. TRAINING_APPROVED=False유지. 실제학습승인후만3단계설정반영/4확인/5실행/6저장한다. 실제실행상한제안은5epoch1910update또는30분,초기3step60초포함. 입력캐시5분/Drive저장3분은별도상한이며추정처리시간아니다.

기존CLI명령형식: `python scripts/train_r3d18.py --colab-expansion --config <runtime-config.json> --output <새artifacts폴더> --check-only`. `--check-only`는설정만확인하고원격파일/가중치/GPU연산확인은하지않는다. 승인없는실제실행은모델시작전에거부한다. 노트북은같은CLI를호출하고배열/시간JSON만런타임으로옮긴다. 기존33구간용config를확대수치로직접고치지않는다.

학습후생성예정: `result.json`, `summary.json`, `epoch-results.json`, `loop/model.pt`, `loop/checkpoint.pt`, 설정/버전/클래스매핑/로그. epoch의online_mean_loss는학습중각업데이트에서계산한손실평균이고고정모델평가손실과다르다. 미기록epoch/학습전점수는만들지않는다. TrainF1/ValidationF1/Recall/AP는아직미측정이며null. 독립검증없음. 실제실행결과를받은후Streamlit등록은다음별도연결이다.


### 2026-09-23 — 입력캐시164개 보존 복구

학습전input_staging_time_limit는가중치업데이트가아니다. 같은세션의새코드셀에 `artifacts/colab/expansion-training-20260923-03/recovery-cell.py` 전체를붙이고실행,같은폴더 `expansion-training-code.zip`(87,153bytes)을선택한다. 새셀은사용자제공기존캐시/설정을읽고해시가맞는164개를재사용한다. 경로/기존설정없으면멈추며설치/전처리/Drive업로드반복불필요. 누락복사5분상한내모두준비되면기존학습범위로시작한다. 중단시에도staging-progress/summary를확인할수있다. 복구종료후기존6번셀로새OUTPUT결과저장. 이전5번셀은누르지않는다. 런타임초기화시캐시는사라질수있다.


## 2026-09-23 고정30구간 참고 평가 묶음
현재1412step체크포인트유지. 추가학습없음. 기존10예측재사용,신규20예약구간만준비·예측한다. 평가노트북맨아래고정30구간셀과 artifacts/colab/reference-benchmark-20260923-01/reference-benchmark-code.zip 사용. 원본주석·정상가정기준이며독립검증아님. 기존10의FP0/FN0/P·R·F1=1.0,고정모델평균CE0.03553576. 새30전체지표는미실행이다. 최신실행상한/테스트/파일은current_status참조.


## 누적5epoch 이어학습 실행 (2026-09-23)
기존 학습 노트북 마지막 **누적 5 epoch 이어학습 — 진행 게이지** 아래 코드셀만 실행한다. 기존 학습/복구셀은 실행하지 않는다. 새 Colab 노트북을 쓰려면 artifacts/colab/epoch-continuation-20260923-01/colab-cell.py의 전체내용을 새 코드셀에 붙여넣어도 같다. GPU 런타임에서 epoch-continuation-code.zip(약89KB)을 선택한다. 자동설치/다운로드없음. 경로는 기존 Drive 결과와 원래 입력제안으로 고정된다.
입력준비 게이지는 학습률이 아니다. 학습 게이지는 이번498회 중 완료율이며 누적5epoch까지 진행한다. loss는 최근 batch 손실이고 정확도가 아니다. ETA는추정. 저장 후 Drive results/epoch5-continuation-<UTC>/의 result.json, summary.json, epoch-results.json, execution.json을 공유한다. result가없으면launch.log/staging-progress.json을공유하고전체학습을반복하지않는다. loop/checkpoint.pt와model.pt는새결과에저장한다. 이전모델은보존한다.
4번째epoch의새116업데이트평균은부분평균이고5번째382개가모두기록되어야완전epoch평균이다. 기존4번째loss에임의합산하지않는다. 목표미도달/저장실패는성공으로표시하지않는다. 실제Colab실행미검증상태이며로컬모델연산은하지않았다.

## 2026-09-23 — Colab A100 누적5epoch 완료 결과 수신

사용자 첨부 result/summary/epoch-results/execution 네 JSON을 artifacts/colab/epoch-continuation-20260923-01/received에 원문보존하고 상호대조했다. 실제 Colab 실행 결과:1412→1910 추가498업데이트,completed_epochs5/cursor0/step_limit/returncode0/timeoutfalse. 382×5=1910 및 이번116+382=498 일치. NVIDIA A100-SXM4-40GB 사용. 마지막분류층변경true/특징부·BN유지true/state_dict저장재로딩true. reload_inference_run=false/validation_run=false:새모델추론과정확도확인은아직미실행. 모델파일은이번첨부에없어로컬에서새모델해시나Drive실물을독립확인하지않았다. 사용자로그의Drive저장완료보고와worker저장검증을구분한다.

입력준비279.266초/382구간,캐시재사용0·764파일복사(구간마다배열+시간JSON). training_worker_launched=false는staging시점의상태이며최종result의training_run=true와충돌하지않는다. 이번학습루프누적시간차194.672초(1980.861835135−1786.189495426),worker198.065초. compute6.022초/input10.591초/checkpoint177.118초. 현재코드매업데이트체크포인트저장이주요측정시간이지만이번에저장주기자동변경없음. 전체명령487.039초/부모파일준비포함저장전498.562초(약8분19초),Drive최종복사시간미수신/마운트·업로드·사용자대기제외. 이전T4와장치가달라630초추정과직접동일조건속도비교금지.

4epoch는이번나머지116회만의online CE평균0.0233211581로부분기록이며전체4epoch미완료라는뜻이아니다. 5epoch382회전체평균0.0314762643. 서로다른범위라loss증가/과적합으로단정하지않는다. trainF1/validation지표null은미계산이고0점아님. 기존참고23구간95.65%는이전1412step모델수치로새모델에복사하지않는다.

사용자Drive위치 results/epoch5-continuation-20260923T060447010904Z,새모델loop/model.pt·재개loop/checkpoint.pt. 이번턴은수신파일읽기/산술대조/원문보존/기록만수행,추가학습·추론·코드변경없음. 다음하나:고정30후보중기존성공23입력을재사용해1910step모델을추론하고기존1412step예측과비교(7실패별도유지,참고평가/독립검증아님). 다음실행은자동시작하지않음.

## 2026-09-23 — 누적5epoch 모델의 동일23구간 비교 Colab 셀 연결
사용자 이어진행 승인으로 기존evaluation.py에 fixed_comparison_candidates/stage_epoch_comparison/epoch_comparison_worker를 추가했다. 기존예약30/이전23예측으로선택고정,정상10·사고13과미탐20포함,실패7재처리없음. 기존23입력/원본시각/letterbox를재사용하며모델1910step/5epoch 및부모1412모델SHA/학습목록·설정대조. reserved_prediction_records의기존10개검사를배치로나눠재사용하고하나의모델로23개predict호출,eval/inference_mode/BN상태검사유지. 이전예측재실행0/새예측실패를옛성공으로대체금지. annotation_reference_metrics로동일CSV/정상가정참고정답비교,30행/실패7/새실패/모델SHA/시간/미리보기·프레임시각보존. 새실패시분모차이경고. 기존독립검증gate변경없음.

notebooks/evaluate_r3d18_colab.ipynb 마지막 ‘누적5epoch 모델 · 기존23구간 참고 비교’ 셀만실행. 새모델Drive epoch5-continuation-20260923T060447010904Z/loop/model.pt와기존reserved-recovered 및reference-benchmark batch1/2에서복사. 모델/23입력준비300초·worker로딩/전체추론/보고300초·Drive저장120초. 원본추출/학습배열382복사/학습/다운로드없음. parent processgroup상한/실패로그도Drive저장. results/epoch5-comparison-UTC에 comparison.json,reference-metrics.json,execution.json,worker_result.json,report.html,23PNG,input-times.json. Streamlit표는결과수신전미측정유지.

묶음 artifacts/colab/epoch5-comparison-20260923-01/epoch5-comparison-code.zip272312bytes SHA224be81f4e73a0b962b176844929face21bef1a0bc72238ba9d5cdd615a05a49. 모델/영상/환경미포함. 동일폴더colab-cell.py와receipt. 테스트 PYTHONPATH=src .venv-model-check/bin/python -m unittest discover -s tests -p test_expansion_colab_training.py -q →23개0.339초통과. 신규고정23선택·중복/부모변조거부·23저장배열재사용/기존출력보존확인. 노트북/묶음구문·원문일치·SHA연결검사통과. 실제Colab추론은사용자실행대기,로컬모델연산0. 다음은비교결과수신·Streamlit미측정표연결하나이며자동재학습없음.

## 2026-09-23 — 5epoch 비교결과 수신·Streamlit 연결
첨부3JSON을 artifacts/colab/epoch5-comparison-20260923-01/received에원문보존. executioncompleted/단계returncode0·timeoutfalse/학습false. 동일23성공/7기존실패유지. 이전과새모델23판정모두동일:TN10/FP0/FN1/TP12,Accuracy95.65217%/Precision100%/Recall92.30769%/F1 96%. 고정모델meanCE는0.0931447480→0.1156689963상승. 미탐reserved_accident_20의사고점수0.294939071→0.221865222,여전히정상판정. epoch추가로참고판정개선확인안됨;이것만으로과적합원인확정금지. 원본주석/정상가정/사건·카메라독립성미확인·사고7입력실패·출처편향한계유지.
모델SHA새fbe7558c5dbef10ff23b4788549a65c14973b25c884376124f5915198904e7e6/이전b3dc759b… 연결대조. 이번준비32.004초/worker15.006초/저장전전체47.011초. worker내로딩포함8.127초와구분. Drive저장시간미수신. 실제모델파일/BN상태worker_result는이번첨부미포함이며재검사없음.
기존results_viewer.midterm에서수신3파일상호대조/23ID분모/모델부모/점수연결을확인해미측정칸을실제수치로갱신. 이전·새30행/미탐/7실패·고정모델loss·평가시간표추가. 자료가없으면기존pending화면유지. 원문/모델수정없음,사용자확인대기. 테스트 test_results_viewer.py30개0.354초 / test_results_viewer_app.py2개1.498초통과. 모델연산/학습/설치/업로드없음. 다음하나는같은설정을유지하고기존공식train중미사용자료의확대가능목록검토(제안만);추가epoch자동실행없음.

## 2026-09-23 — 반복정책 신규7추론/기존23재사용 셀 구현
사용자 다음단계진행 승인으로 evaluation.py에 low_fps_seven_records/check_strict_reuse_metadata/stage_complete_thirty/complete_thirty_worker 추가. 기존strict gate 기본보존,별도repeat_policy에서만비감소PTS허용(정책ID필수). 신규7정확한ID/시간/원본/targetnull·반복수/배열해시/프레임JSON/학습중복확인. 기존23의준비manifest기록증가시각/16장/변환/구간일치를확인하고opt-in옵션이중복거부만바꾸므로성공strict입력의선택동일성근거기록. 기존23배열재추출/재예측없음. 재사용점수의modelSHA와현재5epoch모델SHA동일강제,1910step확인.
고정30후보에서새7predict만수행하고기존23점수와참고정답비교. 학습/독립평가false. 30행/실패유지/정책변경명시,과거23지표와학습효과로단순비교금지. 기존BN유지predict코드재사용. 새7원본시각과미리보기저장. 전처리검수완료를사용자확인발언만으로승격하지않음.
노트북마지막 ‘반복 프레임 정책 · 신규7추론 + 기존23재사용’. 입력/모델준비300초,로딩·7추론·보고300초,저장120초. 준비위치는수신low-fps-review-20260923T080716409317Z,모델epoch5-continuation-20260923T060447010904Z. lowmanifest내용fingerprint를수신내용과대조해공백차이허용/내용변조거부. 출력results/complete-thirty-UTC. 새로운영상/학습배열복사없음.
묶음 artifacts/colab/complete-thirty-20260923-01/complete-thirty-code.zip274462bytes SHAee64bb15cdfe90eb37f216817c9a3eb01e5cf782d99291b310af6ba5d9dac703. 단위시험 test_expansion_colab_training.py25개0.349초통과(반복optin/기존strict거부/역행거부/7정책기록변조추가). 소스ZIP일치·노트북구문·SHA연결검사통과. 실제Colab추론미실행,로컬모델0. 다음은사용자 reference-metrics/execution/worker_result 수신 후30구간해석·기존Streamlit연결이며추가학습자동실행없음.

## 2026-09-23 — 신규242 Colab 입력 준비 연결 (클라우드 실행 대기)
역할 목록의 training_candidate 242개만 기존 CSV에 재대조하여 request.json 생성. 보호/사용 이력 자료 제외, 2초·16장·224·기존 strict 샘플링 유지. 기존 정상200배열은 재처리하지 않는다. 기존 inspect_colab_expansion은 사고+정상 합계400 상한을 유지하며 사고 단독 요청에 DETRAC ZIP을 요구하지 않도록 최소 수정. expansion_request_from_ledger가 변경된 CSV시점/공식test/과거학습/개발예약 충돌을 거부한다.
산출물: artifacts/colab/expansion-242-20260923-01/{request.json,expansion-242-code.zip,colab-cell.py}; 기존 notebooks/prepare_training_inputs_colab.ipynb 마지막 독립 셀 추가. ZIP49,954bytes, SHA256 50f2fc70e24e2d2e7bfeb754d4f509beb4fd40270d282074b0e4ff2d70e8fff3. 원본/모델/비밀정보 포함 없음.
제안 실행: 사용자가 Colab에서 마지막 셀 실행, 기존 Drive accident/real_videos와 상위 metadata-real.csv 경로 확인. 최대242구간·입력준비600초(작업 내부530초), 초과 시 프로세스 그룹 중단; Drive 저장은 별도 측정. 원본 전체 추출 없음. 배열 본문 합계 약583MB(추정), PNG/미리보기 등 추가 공간 필요. 설치/클라우드 실행/업로드는 에이전트가 수행하지 않았다.
실제 로컬 확인: 후보242 연결·원본CSV SHA 일치·Python 셀/드라이버 구문검사. `.venv-inspection/bin/python -m unittest discover -s tests -p test_train_candidates.py`:43개 통과0.164초. 동일 명령 -p test_expansion_inputs.py:20개 통과0.570초. 합계63은 코드 시험이며 실제 영상 성능이 아니다. 실제 Colab 파일 확보/디코딩/완료수/소요시간은 미실행·미확인, 사용자 확인도 대기.
전부 성공할 때 현재382+242=624구간(사고424·정상200)이 가능한 상한일 뿐 실제 학습 목록 확정 아님. 다음 하나: Colab file-receipt.json·prepared/manifest.json·execution.json을 받아 성공/실패와 클래스 구성을 반영한 확대 학습 목록·실행 범위를 확정한다. 추가 학습은 이번에 실행하지 않는다.

## 2026-09-23 — 603구간 목록 연결·학습 설정 제안
사용자 이어진행 요청에 따라 기존 connect_expansion_inputs를 재사용해 신규성공221만 연결하고 merge_expansion_proposals를 추가했다. 기존382 기록은 내용 그대로 유지, 신규221의 sample_id만 new242_ 접두사로 구분(원격경로/원본파일명 변경없음). 총603 고유ID, 사고403/정상200, 실패21 보류. 보호/공식분할검사는 기존연결함수 재사용. 원격배열 해시검사는 아직 미실행.
산출물 artifacts/colab/expansion-242-20260923-01/merged-603/training-input-proposal.json 및 training-config.proposed.json. 설정은 공식사전학습+새head, 기존학습률0.001/momentum0.9/batch1/16장224 유지, 최대5epoch(3015업데이트) 또는1800초 제안. 실제실행승인false. 원본데이터 구성 변경효과를 비교하기 위해 초기화방법도 기존 확대학습과 동일하게 제안하며 기존5epoch모델 덮어쓰지 않음.
주의: 기존382 고정 실행검사는 그대로 보존했다. 설정의 draft_not_executable_under_existing_382_guard에 명시했으며 지금 기존학습셀에 넣으면 실행되지 않는다. 실행 가능한 Colab 묶음/603범위 전용 승인검사는 다음 작업이다. 학습/모델/다운로드/클라우드 실행 없음.
검사: .venv-inspection/bin/python -m unittest discover -s tests -p test_expansion_colab_training.py →28개0.371초 통과. 신규ID충돌해결/기존입력불변/원본중복차단/평가목록혼입차단 포함. 로컬 목록 생성603건·구분403/200·기존382불변 assertion 통과. 사용자 검수/이해는 대기.
다음 하나: 이603목록을 사용하는 실행범위 검증·진행표시·결과보존 Colab 셀 연결. 실제학습은 위시간/epoch범위 승인 후 진행. 입력복사시간은 별도이며 기존A100/T4 시간만으로603실행시간 단정하지 않음.

## 2026-09-23 — 603구간 Colab 학습 실행 연결
사용자 ‘그래시작할까’에 따라 제안된603구간/최대5epoch·30분 학습 셀 연결. 실제클라우드 실행은 사용자가 수행. training_data.validate_config/validate_expansion_execution에 명시적 fixed_603_v1 분기 추가: 고정proposal SHA·603ID·403/200·3015step·1800초·기존head/학습률/전처리 강제. 기존382/1910step 검사는 기본그대로 유지. 공식가중치에서 새head 초기화이며 기존5epoch모델 이어학습/덮어쓰기 아님.
기존 run_colab_expansion/stage_expansion_inputs/학습worker 재사용. 입력준비300초·3stepprobe60초·학습worker1800초·부모2130초·Drive저장120초. 이전 캐시는 해시일치시에만 재사용. 입력603은 약1.45GB 배열본문(추정)이며 GPU메모리가 아니다. 체크포인트 매업데이트 저장 정책 유지. 저장시간 비용은 알려진한계이나 이번에 동시에변경하지 않음.
산출물 artifacts/colab/expansion-603-20260923-01/expansion-603-code.zip 116658bytes SHA ad6fab6b5e5d99226d34544863cb261cbea485b96723ee5795d8c654dcaec20b, colab-cell.py, training-config.json, training-input-proposal.json. 기존 notebooks/train_expansion_r3d18_colab.ipynb 마지막 ‘603구간 확대 학습’ 독립 셀. 가중치 실제Drive경로를 입력받고 자동다운로드/설치 없음. 입력/학습 게이지를 분리, 실제 batchloss/step/epoch위치 표시. 실패/시간초과 결과도 저장, 입력배열 중복저장 제외. 새 결과폴더 사용.
검증: test_expansion_colab_training.py31개0.343초 통과. 초기에 일반 max_steps1910 제한으로 신규603검사가 실패하여 해당명시scope에만3015허용 수정후통과. 기존382안전검사 테스트유지. 코드ZIP내용일치/셀구문통과. --check-only: ready, training_worker_launched=false, remote_input_files_checked=false, weights_checked=false. 로컬첫check는상대proposal경로때문에FileNotFoundError; 실제Colab처럼절대경로 runtime config로 대조후성공. 원격해시/실제GPU실행/메모리·시간·성능은 이번에 미실행. 사용자실행결과수신대기.
다음 하나: 사용자실행 result/summary/epoch-results/execution을 받아 실제완료량과저장상태확인. 이후같은30구간참고평가비교는별도단계. 독립검증이라고표시하지않는다.

## 2026-09-24 — 새런타임603 이어학습 셀 준비
기존 epoch continuation 경로에 fixed603 scope 추가. 체크포인트SHA469662e9…/step1678/cursor472/603순서·proposal검증, 남은1337→3015/누적5epoch. 기존382 continuation 기본보존. run_loop checkpoint_every 기본1 유지, fixed603에서만50+epoch끝+정상종료 저장. 강제중단 최대49update 재계산가능. optimizer/RNG/가중치 복원, 파라미터/전처리변경없음.
입력준비900초/추가loop900초/worker960초/부모1980초/Drive저장120초로제안셀에표시. 새런타임Drive parent checkpoint/prepared_inputs만먼저읽고기존승인proposal의원래Drive배열경로로복사. 새결과폴더사용. GPU모델다운로드/공식가중치경로입력없음. 로컬모델연산/실제Colab실행없음.
notebooks/train_expansion_r3d18_colab.ipynb 마지막603이어학습셀, artifacts/colab/continuation-603-20260924-01/continuation-603-code.zip116259bytes SHA731a472240978d60cdbe1c57d670b1fecbc0f30cfffdd5602b6f128334c46081. colab-cell.py도제공. 기존새학습셀/이전복구셀실행불필요.
검증 test_expansion_colab_training.py32개0.354초통과,새603진행률/잘못된step차단/기존382회귀검사포함. 시간값동등검사부동소수점차이는AlmostEqual로수정. 셀/소스구문확인. 실제GPU재개·새저장주기동작·실행시간은미검증,결과수신후확인. 다음하나 result/summary/epoch-results/execution확인후고정30새모델참고평가연결. 자동평가없음.

## 2026-09-24 — 603 누적5epoch 완료 보고 수신
지정 Downloads/drive-download-20260924T013056Z-1-001의4JSON파일은현환경에없어직접열람실패. 접근가능한첨부Pasted text.txt의콘솔원문및result/summary/epoch-results블록을 continuation-603-20260924-01/received에 user-provided 이름으로보존. execution.json내용미수신. 원본JSON파일수신으로표시하지않음.
보고상TeslaT4/torch2.11.0+cu130/torchvision0.26.0+cu130,부모SHA469662e9…일치,추가1337/누적3015=603*5/완료5epoch/cursor0. result completed_limited_loop/step_limit,featureBN유지/head변경/state재로딩true,재로딩추론false. summary worker_completed/checkpoint_available/result_available=true. 실제새모델파일해시/열람미확인.
입력준비360.296716초/603구간/1206파일새복사. worker185.598488초,누적루프1968.656094초에서부모1788.973874초를빼면이번179.682220초. 입력39.039974/계산125.922500/체크포인트12.048436초. 전체명령시간및Drive저장시간미수신. 저장비용이줄었으나이전과update수/환경cu128→cu130등이다르므로통제된성능비교로단정하지않음.
새로그epoch3남은131평균0.0394101321(전체epoch아님),epoch4전체603평균0.0361719031,epoch5전체603평균0.0286690487. 기존실파일epoch3앞472와가중평균한전체epoch3는0.048695953336(새로기록된원본평균이아닌계산값). 학습loss이며정확도/F1미측정. 중첩staging.training_worker_launched=false는준비시점상태이며result.training_run=true와구분.
사용자Drive보고위치 results/continuation-603-20260924T013004490691Z. 다음하나는같은고정30구간의새모델참고평가연결. 새loop/model.pt또는해시확인필요. 추가epoch/학습자동실행없음. 이번은저장콘솔해석/산술·부모대조만,신규단위시험/모델연산없음.

## 2026-09-24 — 603 새모델 고정30 참고평가 셀 연결
Downloads/model .pt를weights_only/cpu로저장정보확인(모델forward없음). SHA bb3e939ab28ed9c53f788b316cc071d08cbca5fa57d0fa182a9cee3d29d9595e,step3015/5epoch/normal0accident1. 기존입력strict23+repeat7 그대로재사용하며새모델30예측전부계산. 이전모델30점수는비교표만사용. 원본재디코딩/학습없음.
evaluation.py stage_603_comparison/check_603_comparison/comparison_603_worker 추가,기존검증·로더·predict·주석참고지표 재사용.고정모델SHA/학습목록·순서/입력hash/학습중복검사유지. 예측파일저장후정답비교. 입력300초/worker전체300초/저장120초. report.html 전30행·미리보기·comparison.json·input-times.json·reference-metrics·worker_result. 독립성미확인유지.
notebooks/evaluate_r3d18_colab.ipynb 마지막603모델비교셀. artifacts/colab/compare-603-20260924-01/compare-603-code.zip307728bytes SHAc2e8f1f6d5d3f69fc8b84d826db1816c406d791196e1b762f6dd69f337a8730c. 기존Drive continuation-603-20260924T013004490691Z 모델경로. 모델/배열은ZIP미포함,코드·metadata·기존결과목록포함.
검증 test_expansion_colab_training.py33개0.365초통과,5epoch및30ID게이트테스트추가. ZIP소스일치/셀구문확인. 실제Colab모델추론·지표비교·사용자보고서확인은미실행대기. 다음하나는사용자comparison/reference-metrics/execution/worker_result수신후오탐미탐분석이며자동추가학습없음.

## 2026-09-24 — 603평가 묶음 모델ID 연결오류 수정02
사용자Colab로그 fixed_23_comparison_scope_changed는예측전stage_epoch_comparison의선택검사에서발생. 실제묶음대조: benchmark-plan모델SHA b3dc…(이전중간모델), previous-metrics23및baseline30 SHA fbe755…(382/5epoch)로불일치. 개발자가묶음을연결하며과거선택ID를갱신하지않은오류. 사용자경로/GPU문제아님. 모델추론미시작,comparison/reference-metrics/worker_result/report는이번실패에서미생성. 실행로그상결과폴더 compare-603-20260924T014718040847Z에는execution/staging.log가예상되며원격직접목록확인미실행.
수정판 compare-603-20260924-02에서plan modelSHA를실제old23/baseline30공통모델ID로연결하고원선택모델ID historical_selection_model_sha256보존. 후보30records완전동일 assertion,안전검사함수변경/해제없음.기존결과/ZIP보존.
ZIP307890bytes SHA1d98e6adc53d55ca16989ea2afa2045926607e94aa0073eaeb0010d3543e6dfc. 기존evaluate노트북마지막셀해시수정,새colab-cell.py제공. 신규실제묶음회귀검사포함 test_expansion_colab_training.py34개0.369초통과. 수정후Colab실행은미확인,추가학습/모델추론없음. 다음은수정마지막셀한개/수정ZIP으로같은30참고평가실행. 노트북전체순차재실행금지,과거셀이력유지.

## 2026-09-24 — 603모델 같은30 참고평가 실제결과 수신
지정 Downloads/drive-download-20260924T015912Z-1-001의6파일실제읽음. compare-603-20260924-02/received에원문+SHA/audit보존. 고정30ID·이전metrics원문·새metrics일치,혼동행렬·평균loss재집계통과. 새모델bb3e939…/기존fbe755… 식별자일치.30성공0실패,사고20정상10,TP20/TN10/FP0/FN0. Accuracy/P/R/F1=1.0,AP1.0000000000000002는부동소수점오차,meanCE0.0350992006. 이전30 Accuracy96.6667/Recall95/F1 97.4359/CE0.099281467. 클래스판정변경은미탐20하나:0→1,score0.22186522→0.83939338. 나머지29판정동일.
NVIDIA L4는사용자콘솔보고,worker실파일cuda/modelBNunchangedtrue. stage94.013331초/worker프로세스18.007102초/저장전active112.021953초. worker내로딩포함9.389352초,추론loop2.556715초는중첩범위. peakallocated417134592bytes/reserved448790528bytes. mount/upload/userwaiting/finalDrivesave제외. 다른 GPU의 이전 측정과 속도 배율 비교하지 않음.
독립검증false/ACCIDENT사고대DETRAC정상출처편향/원본사건독립성미확인유지. 자동표human_reviewed=false는원본주석참고정답경로이며기존미탐20사용자확인취소아님.30/30은다른CCTV전체100%보장아님. report.html수신,PNG미첨부로로컬미리보기표시미검증. 신규모델계산/추가학습없음. 사용자보고서직접확인대기.
다음제안하나: 미사용긴CCTV1본을사고시점정보없이기존연속추론으로분석해정상통행오경보와사고주변확인. 대상/범위/상한승인후진행,이번자동실행없음.

## 2026-09-24 — 연속원본10초603모델 Colab셀 준비
사용자 다음작업승인. 평가미탐20원본 xSMybsal-zs_00.mp4 0~10초를2초창/1초간격으로분석하는기존 analyze_video 재사용. 사고주석/정답시각을추론입력으로읽지않음. 학습미사용이나개발평가에서이미본원본임을명시,새독립검증아님. 앞/중간/끝예정창1/5/9미리보기선택은점수보기전고정. 신규정답null유지·오탐미탐수치자동생성없음.
최소코드수정: CUDA장치선택/메모리관찰8GiB상한, source_scope와preview_schedule 옵션추가. 기존strict16프레임정책유지/실패미분류기록. 학습·원본수정없음.
notebooks/evaluate_r3d18_colab.ipynb 마지막603연속0~10초셀. artifacts/colab/continuous-603-20260924-01/continuous-603-code.zip36220bytes SHA82574657c27dbe37347f5b7a582843a7ba98abdeb290496e028acc6cca6210d8. 원본/모델ZIP미포함. Drive→런타임복사180초/분석worker300초/부모330초/저장120초. 결과report/result/predictions/execution 새폴더. 전체영상파일한개복사하지만분석은최대10초,전체프레임저장없음.
기존test_video_inference.py21개5.004초통과(.venv-model-check). inspection환경에서는torch누락관련실패후설치하지않고기존모델환경에서실행. 실제CCTV/R3D모델연산없음,가상영상/코드시험. ColabCUDA실행·실제결과는미실행대기. 다음은사용자연속예측결과/앞중간끝장면수신해사고판정범위및과다경보검토. 추가학습자동실행없음.

## 2026-09-24 — 603연속10초 결과 실파일 확인
새사용자경로 drive-download-20260924T021240Z-1-001/result.json,predictions.jsonl 읽기성공, continuous-603-20260924-01/received에원문보존. 앞경로에서파일없음과구분,사용자오전송으로단정하지않음. 고정창9개(0~2,…8~10), 예측8개모두사고, 0~2는insufficient_frames_or_time_gap 미분류. score순서1~3 .92292565,2~4 .81698608,3~5 .79415578,4~6 .88584358,5~7 .88586861,6~8 .93466759,7~9 .96163207,8~10 .94649839. truth/loss/correct null유지.8예측각16프레임·구간내PTS엄격증가 assertion통과,모델SHA bb3e…/가중치버퍼불변true. 학습0.
completed_with_unclassified_windows는모든창분류완료아님. 평균FPS7.8072/영상10.25초header,분석0~10초.0~2실패의정확한프레임선택원인은JSON만으로확정불가. 앞중간끝슬롯중앞창실패로preview2개만(4~6 prediction_004.png,8~10 prediction_008.png). PNG는이번미수신. 기존3.5~5.5사용자사고확인은보존하되1~3정상/8~10사후정차를자동라벨링하지않음.
추론합2.758049초/loop4.358844초/worker6.270724초는중첩범위. 이전콘솔명령21.962초/준비7.069초/전체29.032초와측정범위구분. RSSpeak1519771648bytes/CUDAreserved490733568bytes.참고30/30과연속사고구간탐지성능은별개이며이번8모두사고는발생시점구별미검증을보여줌. 원인(출처편향/학습구성/사후장면민감도)은가설.
다음하나는기존원본1~3초및8~10초의충돌·전복/사고후상태/정상통행/불명확을사람이확인하는것. 이미검수3.5~5.5재질문없음. 재추론/재학습/임계값변경없음.


## 2026-09-24 — 검수 정상3구간 Colab 입력 준비 연결
사용자 승인 정상0–2초 3구간을 기존 prepare_expansion_inputs.prepare_one → preprocessing.sample_frames/save_frames로 처리하는 제한 셀을 준비했다. 정답 user_segment_review 보존, 새 입력 품질 확인은 대기다. 원본/모델/603 학습목록/30 평가목록 변경 없음. 새 학습·모델 추론 없음.

변경: scripts/prepare_expansion_inputs.py의 사람 검수 라벨 미리보기 설명만 구분(INPUT QUALITY PENDING). 기존 NOT HUMAN REVIEW는 다른 원본주석/가정 라벨에 유지. tests/test_expansion_inputs.py에 해당 표시, 누락 파일 정답 보존/입력불가, 검수 JSON 변조 차단 검사 추가. notebooks/prepare_training_inputs_colab.ipynb 마지막 **검수 완료 정상 3구간 입력 준비** 셀 추가.

실제 명령 `.venv-inspection/bin/python -m unittest discover -s tests -p test_expansion_inputs.py`: 22개 통과, 0.641초. 이는 코드/가상자료 테스트이며 실제3영상 성공이나 성능 측정이 아니다.

묶음 `artifacts/colab/normal-three-20260924-01/normal-three-code.zip` 18,886 bytes, SHA256 `994ac21eb6499b01e47b1bb6f3d5691012e79ad567f6f4b1a9a8a36aa183ddc4`. 코드/검수 JSON만 포함. 같은 폴더 colab-cell.py는 노트북 마지막 셀과 동일. 입력 Drive `boram3-cctv-evaluation/accident/real_videos`; 출력 `training-preparation/normal-three-<실행UTC>/manifest.json, execution.json, report.html` 및 각ID의 frames.npy/frames.json/preview.png. 처리 부모90초/각자식27초/저장별도120초. 실행 결과는 Colab 미실행으로 대기.

전처리: 2초에서16장, 기존 nearest timestamp·strict distinct 순서 유지, 비율 유지224 letterbox, uint8 RGB [16,224,224,3]. 여기서 float32/채널정규화나 모델 연산은 하지 않으며 기존 학습로더 단계에서 수행한다. 짧음/프레임부족은 오류로 남기고 범위 확대·반복 프레임 자동 적용 없음. 실패해도 사용자 target0은 보존하고 input_usable=false. 성공해도 새 미리보기 사용자 품질 확인 완료로 기록하지 않는다.

다음 하나: 사용자가 이 셀을 실행한 manifest/execution과 미리보기 품질을 확인한다. 정상 여부 재검수는 요구하지 않는다. 성공3개를603에 연결하면606 제안 가능하나 지금 학습목록을 변경하거나 추가 학습을 승인한 것은 아니다.



## 2026-09-24 — 정상3구간 실제 결과 확인: 성공1·실패2
수신 manifest/execution/report를 artifacts/colab/normal-three-20260924-01/received에 원문·SHA와 함께 보존했다. 검수 JSON SHA 및 3개 모든 원검수 필드 일치 assertion 통과. 실행 명령 종료0/8.739706초/시간초과false는 개별3개 성공을 뜻하지 않는다. 학습·모델 실행false.

01 AEzHgZ0xjlk_00,02 H6FXwjTkWYI_00: insufficient_distinct_frames, input_usable=false. 사용자 정상0 정답은 보존. 정확한 원본 FPS/가용 장수는 실패 manifest에 없으므로 단정하지 않음. 반복 프레임 허용/구간 확대/라벨 변경 없음.
03 mlv_VGdtang_00: prepared_not_split. manifest의16장 시각0.000~1.900초, 모두[0,2)내·엄격증가, 최대간격0.133333초, [16,224,224,3] uint8/정상0 확인. 1920×1080→224×126+상하여백49, crop=null 기록. 원본2.0333초까지 디코딩은 선택을 위한 lookahead이며 저장 입력은2초 밖을 포함하지 않음. 영상 전체 추출false.

실제 명령 `.venv-inspection/bin/python /tmp/audit_normal_received.py`의 근거 대조 assertion 통과. 신규 코드 수정/단위 테스트/영상 디코딩/모델실행 없음. 수신3파일만 있고 report의 train_normal_review_03/preview.png 및 frames.npy는 미첨부: 실제 픽셀·배열해시·장면잘림/중요움직임누락은 미확인. crop 없음은 공간자르기없음의 기록일 뿐 시간샘플링상 중요한장면없음 보장은 아님. 사용자 입력품질검수 완료로 처리하지 않는다.
다음 하나: 같은 Drive normal-three-20260924T023649379635Z의 train_normal_review_03/preview.png(가능하면 source_reference.png 포함)를 받아 기존 생성이미지를 확인. 재실행 불필요. 01/02 입력정책 변경 및 추가학습은 이번 실행하지 않음. 기존603목록·30평가·모델 보존.



## 2026-09-24 — 정상01·02 반복 프레임 미리보기 준비
사용자 승인으로 실패01/02만 기존 low_fps_preview_one/sample_frames(allow_repeated_frames=True)를 재사용하는 Colab 묶음 작성. 0–2초·user_segment_review 정상0 유지. 기존 strict16 학습 로더 변경없음. reviewed_normal 명시옵션은 정상검수/구간/학습승인false 검사를 통과해야 하며 기본 사고검토 경로 보존. 16칸의 서로다른프레임수/반복칸수 기록, 시간역전/구간밖프레임 차단. target0은 검수 기록에 보존하되 저장배열 input.target=null/정책검토용·training_input_usable=false 유지. 학습목록에 자동 연결하지 않는다.

변경파일 scripts/prepare_expansion_inputs.py, tests/test_expansion_inputs.py, notebooks/prepare_training_inputs_colab.ipynb 마지막 정상01·02 반복 프레임 미리보기 셀. 기존03/실패기록/모델/원본 보존. 코드시험 `.venv-inspection/bin/python -m unittest discover -s tests -p test_expansion_inputs.py`: 24개 0.682초 통과. 합성8장→16칸, 사용자정답유지·학습차단·구간확장/미검수차단 확인. Colab실제영상은 미실행, 성공/장면누락판정 미확인.

artifacts/colab/normal-repeat-20260924-01/normal-repeat-code.zip 19,271bytes SHA256 059ce6d37b4e5a34ce2a8e4b45f3bd3bc0fa17b0ad91808c5a3b086ee3263bd6. 코드와 기존검수2행만 포함. 단독 colab-cell.py와 노트북 셀 동일. 처리90초·각자식27초·Drive저장별도120초. Drive accident/real_videos 입력, training-preparation/normal-repeat-<UTC> 출력. manifest/execution/report와각구간 preview/source_reference/frames.npy/frames.json/policy-result. 학습·모델0, 설치/다운로드없음.
다음 하나: 사용자가 셀 실행 후 manifest/execution 및미리보기를 공유하면 반복 장수와장면식별을 확인. 정책변경이 학습에 적절한지는 별도 검토하며 현재603/30고정목록 유지.



## 2026-09-24 — 정상3구간 포함606 후보 목록·로더 연결
사용자 코드연결 승인으로 training_data.connect_reviewed_normal_proposal/validate_reviewed_normal_details 및 training.SegmentDataset의 명시적 user_segment_review 분기를 추가. 기존 strict 경로와382/603 실행상한검사 보존. 기존603 records 완전동일 assertion, 새3개는기존train원본그룹을상속. 사고403/정상203 총606 후보이며새독립원본추가아님. 01/02 nearest-bin-repeat-v1,03 strict-distinct-v1 표시. 원검수정상0 유지, frames.json 원본targetnull을덮어쓰지않고별도검수근거에연결. 자동학습승인/품질승인없음.
목록 artifacts/colab/normal-606-20260924-01/training-input-proposal.json. 목적 reviewed_normal_606_proposal_not_authorized,실행false. 실제Drive array/frames해시는다음실행에서확인필요;frames해시는수신details와기존JSON저장규칙으로계산한예상값이며원격실파일검증아님. 품질필드는 assistant_preview_checked_user_confirmation_pending. 새로더는approved가아니면차단. 기존실행validator는606실행을허용하지않으며새학습셀/ZIP생성없음.
실제 명령 .venv-inspection/bin/python /tmp/connect606.py 통과. .venv-model-check/bin/python -m unittest discover -s tests -p test_reviewed_normal_connection.py:5개0.010초통과. 같은환경 test_training.py:39개6.911초통과. 실제CCTV모델계산아닌기존합성입력/작은시험모델검사. 원본/모델/603/평가30 보존.
남은한계: 실제3배열미수신·Drive해시/로더읽기미실행,새미리보기사용자품질확인미기록,실제학습범위미승인. 다음하나제안: Colab에서추가3입력만해시대조·로더읽기(모델없음,90초)를확인하고그결과로학습실험범위제시. 전체603입력재복사/동일검사반복불필요.



## 2026-09-24 — Colab 추가3입력 읽기 확인 셀 연결
입력점검 승인에 따라 training.inspect_reviewed_normal_input을추가했다. 실제array/frames파일 존재·SHA 확인→validate_reviewed_normal_details(inspection_only=True)→기존tensor_from_array(CTHW float32/정규화)→유한값확인. 점검모드는품질승인을만들거나record를변경하지않으며일반SegmentDataset은기존approved요구를유지한다. 시간역전/구간밖/정답근거불일치차단은유지. 모델생성/가중치로드/추론/학습/GPU 없음.
추가3행만묶음포함,기존603배열읽기/복사없음. artifact artifacts/colab/normal-input-check-20260924-01/normal-input-check-code.zip 32,131bytes SHA256 6cb08c80d227e21bb40cfacc9d43813612cc90f98146162fc20bf5612e503e61. notebooks/prepare_training_inputs_colab.ipynb 마지막 추가 정상3입력 읽기 확인 셀 또는같은artifact colab-cell.py 사용. 기존Drive 경로에서 npy/frames JSON 총6파일읽음. worker90초/저장120초별도,새설치/다운로드없음. CPU는입력tensor변환에만사용;모델CPU전환아님.
실제실행명령 .venv-model-check/bin/python -m unittest discover -s tests -p test_reviewed_normal_connection.py:12회검사0.681초통과(상속된기존5항목재실행포함). test_training.py:39개6.847초통과. 합성배열읽기/입력정규화/해시변조차단·품질대기학습차단확인. 코드/노트북셀문법확인완료. 실제Colab3배열읽기는아직미실행.
출력Drive training-preparation/normal-input-check-<UTC>/result.json,execution.json,check.log. 각행성공/오류기록으로전체명령종료0과3개성공을구분. 다음하나: 사용자결과수신후추가3입력연결성공여부판정,학습실험의초기화·학습량·시간상한제안. 실제추가학습범위자동승인없음. 사용자품질승인미기록보존.



## 2026-09-24 — 추가3입력 점검 성공 수신·606 학습 실험 범위 제안
사용자 붙여넣기 결과3행 input_verified_not_training 수신. 01/02/03 서로다른12/15/16장,CTHW [3,16,224,224]float32·정상0·시각이기존수신목록과일치.점검내부0.281640초/명령11.112141초/timeoutfalse. 모델·학습없음. 사용자보고필드를기존목록과대조해 input-check.user-reported.json에재구성저장(원격실행을에이전트가직접재실행한것아님). 입력연결의동일점검반복불필요.
606 실험제안 artifacts/colab/normal-606-20260924-01/experiment-proposal.json 생성. 이전603와같은공식사전학습가중치+새fc/seed20260918에서시작,head고정범위·lr.001·batch1·16장224·5epoch 유지. 기존603완료모델이어학습으로학습량효과를추가혼합하지않음. 변화는정상3구간추가(01/02반복정책포함),기존603입력불변. 반복정책영향과정상예시추가효과를별개로분리한실험은아님. 3구간소수추가효과보장없음.
제안상한: 준비15분,3step시험60초,학습최대5epoch/3030회 또는30분,저장2분. 상한합최대48분이며예상소요확정아님. 이전T4 603처리는처음30분에미완료였고저장간격50재개는짧게끝났으므로정확한속도비교불가;현재GPU짧은시험후ETA기록. checkpoint50회/epoch끝/종료저장안. 기존모델덮어쓰기/자동다운로드/유료GPU사용승인없음.
config는명시적proposal-only/authorizationfalse로기존CLI에투입할수없다. 실제실행adapter/Colab학습셀접속은범위와품질확인후구현. 지금실제모델학습미실행. 다음평가는고정30참고지표와기존정상1~3초오탐을분리해비교하는제안이며독립검증이아니다.이번자동평가승인아님.
실제확인명령 .venv-inspection/bin/python /tmp/prepare606_experiment.py:기존603완전동일/606구성/5epoch3030상한/실행false assertion통과. 신규로더수정/단위시험추가없음. 남은사용자확인:3미리보기식별가능/추가장면불필요여부와구체학습범위승인. 정상정답은재질문하지않는다.



## 2026-09-24 — 사용자606/5epoch 완료학습 승인·진행률Colab셀
사용자 “추가 장면이 필요 없는거같아” 수신: 기존3구간정상판정과에이전트이미지검사에연결하여새실행용목록의input_quality_review=approved 기록. “30분 상한안해도 되고…마무리까지…진행…파악”으로606/5epoch완료학습승인,시간30분중단제거. 구pending목록/실험제안은원문보존,새 train-606-20260924-01에승인목록/설정별도저장. 기존epoch수/모델/head범위/lr/seed/입력설정은유지. 유료자원/추가epoch자동승인아님.
변경 training.loop_time_expired: None은승인fixed_606_epoch_bounded_v1/5epoch/3030범위에서만허용,기존scope시간제한보존. 기존 run_limited_real_worker 재사용:공식초기화/3step시험/이어학습/고정특징BN/재로딩추론확인. 606본학습checkpoint50업데이트+epoch경계+종료저장,시험3회는3030안에포함. 기존메모리8GiB/개별step30초보호·probe60초유지. None시간상한을이전실행에전파하지않음.
입력 준비 stage_expansion_inputs 재사용15분상한/원격해시대조/캐시재사용. 학습worker총시간상한없음·3030회로종료,원본/이전모델보존. 화면입력606게이지/학습3030게이지·epoch·경과·최근loss·최근step기반ETA표시. ETA는추정이고저장step에변동. 런타임checkpoint약50회마다갱신,Drive recovery는epoch경계확인시복사(복사별120초). 마지막 run폴더별도저장120초. Colab세션강제종료시마지막Drive저장시점이후업데이트는유실될수있으며자동재개/완료보장아님. 새실행기능은새초기화이므로중단시이셀무작정재실행하지말고checkpoint로별도재개연결필요.
묶음 artifacts/colab/train-606-20260924-01/train-606-code.zip121523bytes SHA4425798a1890c4c6725a141908614b61e0a79e084eab995313d86a0b6bfcc222. 코드/606목록/설정만,영상/모델없음. 기존 weights/r3d_18-b3b3357e.pth 필요,없으면중단(다운로드0). notebooks/train_expansion_r3d18_colab.ipynb 마지막 승인606구간5epoch완료학습 셀만사용. Drive results/training-606-<UTC>/run 아래result/summary/epoch-results/execution/loop모델,중간recovery 별도. 새평가/임계값변경없음.
실제테스트 test_reviewed_normal_connection.py13회0.678초(상속기존검사포함),test_training.py39개6.842초,test_expansion_colab_training.py34개0.361초통과. 묶음코드문법/목록해시/606행/승인/3030/상한None assertion통과. 합성자료검사이며실제Colab모델학습미실행. 지금학습예상시간정확값은미측정;초기3step및진행중ETA로확인. 이전T4기록에서603첫30분미완료는저장빈도영향포함이라동일시간예측으로사용하지않음.
다음하나:사용자실행결과수신후완료5epoch/3030·저장/재로딩·loss·시간확인. 그후기존30참고평가및정상오탐사례비교를제안하며자동평가없음.



## 2026-09-24 — 606구간5epoch 완료결과 수신·입력준비 병목
사용자Downloads drive-download-20260924T042420Z-1-001의 result/summary/execution/epoch-results 실파일읽기·원문보존(received 폴더)·해시기록. 실제Colab TeslaT4/공식초기화+새head,606=사고403/정상203,5epoch3030업데이트 완료. 각epoch606행 완결 대조통과. 특징부/BN해시불변,headweight최대변화.3625356853/bias.0503792576,3step시험후재개/최종모델재로딩추론확인true. 모델파일자체는미수신으로로컬해시미확인. 평가미실행.
온라인평균CE(epoch별): .2700116882,.0915782000,.0656997072,.0535827500,.0455625534. 학습중바뀌는모델의배치1평균이며고정모델평가loss/F1/정확도로환산불가. 정상보완효과/오탐감소아직미측정.
입력준비652.853092초(10분52.85초),캐시재사용0/복사1212파일(606개의array+frames JSON). 학습worker준비/저장/재로딩포함394.798535초(6분34.80초). 누적loop훈련시간387.304309초(중첩범위),전체command1061.866561초(17분41.87초,Drive mount/upload/userwaiting/finalsave제외). 입력준비가command약61.5%. 전송/파일별프로세스기동/해시별시간분리는미측정이므로단일원인확정없음. 파일별subprocess copy 구조는코드근거확인.
Drive 결과 사용자콘솔: results/training-606-20260924T040144049888Z/run/loop/model.pt 및checkpoint.pt. 무작정학습재실행불필요. staging.training_worker_launched=false는준비단계필드로실제학습없음을뜻하지않음(상위training_run=true와3030확인).
입력속도개선제안:같은런타임 /tmp/cctv-606-input-cache 재사용(해시검사유지);새런타임은승인목록파일을몇개묶음으로전송해작은파일반복접근줄이기(패키징/크기/압축효과는미측정);구현/업로드이번없음. GPU업그레이드는이준비단계해결로간주하지않음. 초기3step ETA3084초는매step저장시험을본학습50step저장에적용해과대추정가능,실제worker394.8초와구분.
실행 .venv-inspection/bin/python /tmp/receive606.py 수신대조assertion통과. 신규학습/단위시험/모델실행없음. 다음하나:606모델을고정30참고입력과기존연속정상1~3초오탐사례에비교하는Colab평가연결제안. 30참고평가만으로독립성능/오탐개선확정금지.



## 2026-09-24 — 606모델 고정30+정상1~3초 Colab 비교 연결
사용자준비요청으로기존 evaluation.stage_603_comparison/comparison_603_worker 재사용. reviewed606 명시분기에서606학습목록·3030/5epoch·모델내proposal/record_order/설정검사,603기준모델SHA확인. 기존603분기와23/30선택검사보존. 고정30은동일저장배열재사용,정상통행xSMybsal-zs_00 1~3초는기존배열미저장으로기존sample_frames로재구성하고이전16actual시각완전일치요구. 학습원본과중복이면차단. 당시픽셀배열해시없어픽셀완전동일은미확인,재구성입력표시.
606학습Drive results/training-606-20260924T040144049888Z/run의모델/작은학습목록/결과만연결,606학습배열복사없음. 준비worker300초(기존30복사와정상2초디코딩포함),추론worker전체300초(31구간/로딩/비교표포함),저장120초. 예측31행저장후30주석참고지표와정상사례target0비교를분리. 모델eval/inference전용/BN불변검사기존predict재사용. 독립검증false/임계값argmax유지/추가학습없음. 정상사례는이전예측후검수이므로독립검증/전체정확도에합산하지않음.
notebooks/evaluate_r3d18_colab.ipynb 마지막606모델비교평가셀, artifacts/colab/compare-606-20260924-01/compare-606-code.zip310982bytes SHA76fc4c1cff72cac1d9c96e003774fc87e69f54c74e44ca8eb1ecb8fe4aaac8e6. 셀GPU와av존재사전확인(자동설치없음). 새학습/다운로드/클라우드실행은에이전트가하지않음. 사용자GPU실행대기.
관련검사 test_reviewed_normal_connection.py14회0.733초(기존상속항목포함), test_expansion_colab_training.py34개.385초통과. test_evaluation.py 최초38중1실패:노트북에설치명령문구가있으면금지하는검사에안내문구가걸림. 설치자동실행은없었으며문구수정후38개4.476초통과. 이전epoch비교함수에신규분기가섞이지않게수정. 묶음문법/606목록이실제학습묶음과바이트동일/603baseline모델과30ID연결대조통과. 실제모델결과아직미실행.
출력Drive results/compare-606-<UTC>/comparison.json,reference-metrics.json,normal-case-comparison.json,execution.json,worker_result.json,report.html/미리보기. 첫두파일은30참고비교,normal-case는과거오탐과이번예측별도기록. 다음하나:사용자실행결과수신후사고검출유지와정상통행오탐변화확인. 입력준비묶음전송전체최적화/새학습파생없음.



## 2026-09-24 — 609구간5epoch Colab 실행 연결
사용자 “이제 다시 시작해볼까?”를 직전 제안한609/동일5epoch 실험 진행 요청으로 연결. 에이전트 클라우드 실행/업로드 없음. 기존606 승인후속의 완료epoch기준/진행률 유지. 새 실행 공식사전학습+새head 초기화,606이어학습 아님. 사고403정상206,5epoch3045업데이트(초기3시험 포함),lr.001/seed20260918/batch1/frozenfeaturesBN/판정기준 유지. 추가학습자료3 외 실험변수 확대 없음.
training.loop_time_expired에명시적 fixed_609_epoch_bounded_v1/3045/5/authorized 조합만추가. 기존606/다른시간상한 보호유지. scripts/train_r3d18.py에서609도50step체크포인트 적용. run609는기존실행기재사용,정확609/403/고유ID/목록해시/공식가중치해시확인,신규3상대경로만런타임절대경로연결. 준비900초/시험60초/메모리8GiB·step30초보호/본학습5epoch/저장120초. 학습30분자동중단없음. 실제Colab미실행.
새ZIP artifacts/colab/train-609-20260924-01/train-609-code.zip3416353bytes SHA f03c8687d5d3bd1a6d0254cebca51e6b51381ba5b7c93450acc19b3503c76151. 코드/목록/설정/새3입력6파일 포함15개. 원본영상·모델·토큰없음. 기존606 Drive경로유지,같은런타임 /tmp/cctv-606-input-cache 해시재사용. 노트북 notebooks/train_expansion_r3d18_colab.ipynb 마지막609셀 단독실행;전체셀실행금지안내. 기존모델/결과보존,새Drive results/training-609-UTC/run 저장. 진행률/최근loss/ETA/중간체크포인트Drive저장유지.
검사: test_reviewed_normal_connection.py16개0.705초(신규609잘못된상한/승인거부·ZIP3입력해시/품질검사 포함),test_training.py39개6.504초,test_expansion_colab_training.py34개0.344초 통과. 총89시험이며일부기존상속검사포함,실제사고성능시험아님. ZIP/셀문법확인통과. 실험자원예상은이전606 T4 준비10.9분+worker6.6분을참고하되속도보장없음.
다음하나:사용자가Colab실행후result/summary/epoch-results/execution수신,완료5epoch3045및저장결과확인. 자동평가·추가epoch없음.


## 2026-09-24 — 609 실제 학습 결과 수신·5epoch 완료
사용자 지정 실제 파일 5개 원문 보존·SHA 기록.
609구간(사고403/정상206),TeslaT4,5epoch3045업데이트 완료. result/summary/epoch기록 대조통과. 각epoch609회,loss .281795→.155818→.123834→.107473→.095587. 온라인무가중batch1 CE이며정확도로환산금지. 기존606마지막loss .045563보다높지만자료/순서가달라그것만으로성능저하확정불가. 독립검증/참고평가 이번미실행.
입력준비5.056596초,기존1212파일재사용/신규6복사(3배열+3시각기록). worker400.570258초,전체명령416.828108초(약6분57초),Drive mount/upload/사용자대기/최종저장제외. 606의652.853초준비대비이번캐시재사용조건으로입력준비감소;순수GPU속도개선아님. 특징부/BN불변,head변경,시험3step후재개/최종재로딩출력1x2 확인로그true. 모델파일자체미수신으로로컬가중치해시확인없음.
수신 artifacts/colab/train-609-20260924-01/received 에 result/summary/epoch-results/execution/parent.log·receipt-audit 보존. 실제학습·모델검사재실행없음. Drive콘솔최종저장경로 results/training-609-20260924T051725737854Z/run/loop/model.pt. 다음하나:새609모델을기존고정30구간+정상1~3초오탐사례에비교(자동실행없음). 오탐감소미측정,모델승격보류.


## 2026-09-24 — 609모델 비교 Colab 연결
사용자loss질문/다음작업요청으로기존 evaluation.comparison_603_worker에 reviewed609 명시경로추가. 새 check_609_comparison이5epoch3045/609목록/고정30/이전606모델SHA를검사. 기존603/606가드유지. 원본동일성·학습목록대조·eval/inference모드/BN불변/predict 기존경로재사용. 임계값·전처리·모델변경없음.
이전606 수신comparison.received.json의각30예측/점수/지표를baseline으로연결. 새609를기존30+별도사용자정상1~3초=31예측,정상사례606점수.9177208543와비교. 30지표에정상사례합산하지않음. 독립검증false/기존약한정답한계유지. 입력은기존30배열재사용,정상사례만기존sample_frames와16시각일치검사로재구성. 원본영상전체재추출없음.
notebooks/evaluate_r3d18_colab.ipynb 마지막609비교셀; artifacts/colab/compare-609-20260924-01/compare-609-code.zip312506bytes SHA3c7519686ab5d98a8a67a62cf0f289e245ca6fa130f2189f424d3b11d490f613. 실제609 Drive results/training-609-20260924T051725737854Z/run 모델연결. 준비300초/추론worker300초/최종저장기존경로. Drive results/compare-609-UTC에새저장. 모델/원본/이전결과보존. 로컬모델연산/클라우드실행/다운로드/업로드/추가학습없음.
검사 test_reviewed_normal_connection.py17개0.750초(609잘못된목록/기준모델/고정30거부포함),test_evaluation.py38개4.162초 통과,총55개. ZIP/셀문법확인. 실제GPU결과미실행;성능수치새생성없음.
다음하나:사용자실행후comparison/reference-metrics/normal-case-comparison/execution/worker_result수신해606대비오탐변화확인. 추가학습/epoch증가자동진행금지.


## 2026-09-24 — 609 비교 실파일 수신: 두 오탐 유지·점수 악화
사용자 재첨부 Downloads/drive-download-20260924T084955Z-1-001 comparison/normal-case-comparison 실제읽기성공. artifacts/colab/compare-609-20260924-01/received 에원문/SHA/receipt-audit 보존. 이전첨부접근불가상태를이번2파일에한해해소. execution/worker_result 실파일은여전히미수신,시간은기존콘솔근거.
고정30 ID고유성/성공30/행별혼동행렬재집계확인:TP20 TN9 FP1 FN0. 606→609 판정변경0. 같은오탐 reserved_normal_MVI_63544 정상가정0: .5432788134→.7753885984,예측1유지. 별도사용자정상1~3초: .9177208543→.9591450691,예측1유지. 정답기준두오탐의사고점수가더높아졌음. 고정30meanCE .0583114→.0950866,AP약1→.997619. 정상3추가609실험은오탐감소목표미달,609개선모델승격없음. 30참고평가/예측후검수개발사례이며독립검증아님.
수신모델식별자609 SHA436b0e5bf84c09471fa0ae0d74eecf6c40be0f3ce80de16b6742aaf8ddfc9eac. 로컬모델파일해시검사아님. 추가학습/추론/테스트재실행0,JSON일관성assert검사만수행.
다음하나 제안: 기존609목록에서출처×클래스및원본그룹분포를대조하여출처차이학습가설점검. 사고403ACCIDENT/정상200DETRAC+6ACCIDENT라는구성의편향가능성은가설이지원인확정아님. 같은목적의3개씩추가학습반복/epoch증가/임계값튜닝자동진행없음. 평가자료학습편입금지유지.


## 2026-09-24 — 631구간 입력·Colab 연결 완료, 실제 학습 미실행
사용자 요청으로 기존609구간과 에이전트 관찰 기반 임시 정상22를 연결했다. 사고403·정상228=631. 보류05·09·12·17·24·26·27·30은 제외하며 답변을 기다리느라 이번 연결을 막지 않는다. 추가 답변은 선택사항이다. 기존609행은 동일하게 유지했다. 임시22는 assistant_observation_provisional_normal/human_reviewed=false이며 CSV 공식 정상 정답이나 사용자 직접 장면 검수로 표시하지 않는다.
기존 수신 PNG16장씩을 프레임 순서 그대로 uint8 배열로 묶음: 새 디코딩·리샘플링·모델 연산 없음. 기존 정규화/2초16장224 입력 유지. 별도 임시라벨 어댑터를 추가했고 기존 사람검수/엄격입력 검사 경로는 유지한다. 보류/승인누락/시각범위오류/거짓 사람검수 표시를 거부한다.
산출물: artifacts/colab/train-631-20260924-01/{training-input-proposal.json,training-config.json,run631.py,colab-cell.py,train-631-code.zip,bundle-receipt.json}. ZIP 27,394,737bytes(약27.4MB), 코드+목록+신규22와기존3의입력배열25개. 원본영상/가중치/환경/토큰 없음. 기존Drive입력606개와 공식가중치가 필요하며 새 런타임에서는 기존입력 캐시를 다시 준비할 수 있다. 새631목록은 별도 보존, 이전 제안/모델/결과 보존.
notebooks/train_expansion_r3d18_colab.ipynb 마지막 ‘631구간 연결’ 셀만 실행. 새ZIP 해시/허용목록 확인 후 사용자 ‘시작’ 입력으로 실제학습 승인 기록. 기본 config execution=false. 실행 범위: 공식가중치+새head 초기화, lr.001/seed20260918/batch1/head-only/특징부BN고정,5epoch3155업데이트,기존허용대로학습30분종료제한없음. 준비15분/저장2분/3step시험·메모리·step상한 유지. 진행률·최근loss·ETA·epoch체크포인트와 결과Drive저장 재사용. 기존609모델 이어학습 아님. 모델 품질 개선 보장 아님.
로컬 검사: reviewed_normal_connection22 + training39 + expansion_colab_training34 =95개 통과. 신규22 실제 입력을 Dataset으로 읽어 shape/target/해시 확인(모델 학습·추론 없음). 셀 문법·노트북 동일성 확인. Colab 실행·GPU 학습·성능측정은 미실행,사용자실행/결과확인 대기. 다음하나: 사용자가 마지막셀에서 이범위 학습을 실행한 후 result/summary/epoch-results/execution을 확인. 자동평가·추가학습 없음.


## 2026-09-24 — 631구간 Colab 학습 완료 결과 수신
사용자 제공 result/summary/epoch-results/execution JSON4개와Colab출력 수신, artifacts/colab/train-631-20260924-01/received 에 원본바이트 보존. 코드ZIP SHA가배포묶음과일치,631×5=3155업데이트/5epoch완료,클래스403사고228정상,정답출처403주석파생/200정상가정/6사람검수/22임시관찰 일치. 보류8은미사용. 공식사전학습+새head이며609이어학습아님.
학습 online unweighted batch1 cross entropy epoch평균:1=.46748744,2=.31852995,3=.29631720,4=.27695723,5=.25765625. 학습중변하는모델의손실이며정확도나최종모델평가손실아님. 609의.095587보다높으나학습자료/정상라벨구성이달라성능악화또는개선을판정할수없다. 임시22라벨불확실성유지.
TeslaT4,준비673.743초(새캐시1262파일복사/재사용0),루프누적449.467초,모델준비/저장/재로딩포함worker458.645초,명령전체1146.433초. Drive연결/업로드/사용자대기/최종Drive저장 제외. 분류층weight변경최대.3771187,특징부/BN전후해시동일,저장재로딩출력[1,2]/3step재개검사성공은수신로그근거. summary.staging.training_worker_launched=false는복사단계상태이며학습미실행을뜻하지않음;result완료가최종근거다.
Drive최종저장성공은사용자Colab출력에기록: /content/drive/MyDrive/boram3-cctv-evaluation/results/training-631-20260924T125751055830Z/run 。모델예상파일loop/model.pt,모델본체이번미수신/Drive직접확인미실행. JSON 일관성/해시확인만실행,기존코드테스트/학습/추론반복없음. 결과수신확인과사용자직접결과검수는구분.
현재631모델Accuracy/Precision/Recall/F1/오탐미탐은미측정. 다음하나:동일고정30구간+별도정상1–3초사례로609대631예측비교연결(제안). 독립검증/최종test아님,자동추가학습없음.


## 2026-09-24 — 609 대 631 참고비교 Colab 연결 완료
목적: 임시 정상22 추가 후 기존고정30(사고20/정상10) 및 별도 정상1–3초오탐이 개선됐는지 같은판정기준으로 비교. 학습/임계값변경/새독립검증아님. 비교목록에점수보고선택한자료를새로추가하지않는다.
631 model.pt weights_only CPU역직렬화로메타만검사:학습목록SHA가config/provenance에일치,5epoch3155step,class_mapping normal0/accident1확인. 로컬추론없음. 모델SHA7dbd352a3992e2c788ba517522698f5dd1353f5df6c46f4247a74820c59b891c 고정. 이전609 SHA436b0e5bf84c09471fa0ae0d74eecf6c40be0f3ce80de16b6742aaf8ddfc9eac 및실제수신comparison.json의새예측을baseline30으로재사용. 별도정상사례이전점수.9591450691223145,사고예측1.
기존evaluation.comparison_603_worker에명시적631분기및check_631_comparison추가. 기존603/606/609경로유지. 준비stage_606_comparison재사용:기존30입력재사용/정상1–3초만재구성하며기존PTS와같아야진행. 기존학습중복검사·해시/클래스·eval/inference/BN불변검사유지. 잘못된모델/epoch/목록수/이전모델/30행누락거부.
산출물 artifacts/colab/compare-631-20260924-01/{compare-631-code.zip,colab-cell.py,bundle-receipt.json};ZIP332055bytes,모델/원본영상제외. notebooks/evaluate_r3d18_colab.ipynb 마지막 ‘609 → 631’ 셀. Drive training-631-20260924T125751055830Z/run 모델·목록참조. 준비5분/31입력예측작업5분/저장2분,기존av필요·자동설치없음. 새모델예측31행진행표시.30참고지표와별도정상사례를합산하지않음. comparison/reference-metrics/normal-case-comparison/execution/worker_result/report.html/미리보기저장.
검사명령: .venv-model-check/bin/python -m unittest discover -s tests -p test_reviewed_normal_connection.py (24통과),동일명령 -p test_evaluation.py (38통과). 합62통과. ZIP해시/내장코드/학습목록/노트북일치확인. 실제Colab/추론/학습없음. 보고서에비교완료나정확도개선으로적지않는다. 다음하나:사용자가새평가셀실행후비교결과수신·해석. 자동재학습없음.


## 2026-09-24 — 609→631 같은 참고자료 비교 결과 수신
실제 수신5JSON은 artifacts/colab/compare-631-20260924-01/received 에 원본바이트·SHA보존. 배포ZIP/모델SHA/30행고유ID/혼동행렬/실행성공일관성확인. 코드/모델재실행없음.
고정30구간(사고20/정상10)전부성공,실패0:609→631 Accuracy96.67→100%,Precision95.24→100%,Recall100→100%,F197.56→100%,FP1→0/FN0→0,고정모델평균CE .09508660→.03905874. AP .997619→약1(원본1.0000000000000002부동소수점오차,사다리꼴PR-AUC와구분). 변경1행reserved_normal_MVI_63544 사고점수.7753885984→.4295442700/사고→정상. 나머지29판정동일. 점수는보정된실제사고확률아님.
별도 정상통행1–3초사례(30지표미포함)는오탐지속,사고점수.9591450691→.9936825037로상승. 따라서기존30개오탐개선은관찰했지만목표인ACCIDENT출처정상사례오탐개선은달성못함. 임시정상22추가가전체정상구분/새CCTV일반화개선을입증했다고쓰지않는다. 30참고라벨원본주석/정상가정·독립원본수미확인·출처편향가능성유지. 이전603도같은30에서100%였으므로새모델최종선정근거부족.
준비109.021초/예측작업19.021초/내부추론루프4.440초/준비포함저장전128.044초. Drive마운트/업로드/사용자대기/최종저장제외. 모델/BN불변true, 학습false. 사용자로그Drive결과 results/compare-631-20260924T133811302437Z. 사용자시각검수/독립검증은미확인. 다음하나:학습원본·사건과분리된정상·사고 개발검증목록을고정해출처차이와일반화점검,추가학습/임계값튜닝은자동진행안함.


## 2026-09-24 — 같은 출처 정상·사고 5쌍 Colab 참고평가 연결
고정5파일10구간/normal5 accident5,pre-point-minus4-assumed-normal-v1로실행코드연결. artifacts/colab/paired-dev-631-20260924-01 에 plan/run_paired.py/colab-cell.py/paired-dev-code.zip/bundle-receipt.json 저장. ZIP329613bytes SHA587063d178997368920c87df04c7c58152893324e6fe795c9114dbdfd8233922. notebooks/evaluate_r3d18_colab.ipynb 마지막5쌍셀추가.
공통pp.sample_frames/save_frames,ev.load_model/ReservedPredictionDataset/predict/annotation_reference_metrics재사용. 공통지표함수에정상가정명시분기만추가:원본CSV행·IIDtrain·0–2초·t-4여유확인,다른정상정책유지. 고정목록SHA/10ID/5원본/쌍클래스/학습파일명그룹중복검사. 모델SHA631고정,저장클래스/전처리/학습provenance기존검사유지. 이전학습·test·기존평가자료로목록자동대체없음.
준비전체5분/각구간25초와내부275초,추론작업5분/저장2분. 사용자Colab에서만실행;이번로컬모델연산없음. 2초16장224/nearest-bin-repeat-v1,원본PNG미리보기/원본PTS저장. 정답은예측후공통지표계산에만사용. 실패10행모두지표행에포함하며실패를정상으로안바꿈. eval/inference모드/모델BN불변검사유지. 독립검증false,정상사용자가정/사고주석파생,독립원본수미확인.
검사: .venv-model-check/bin/python -m unittest discover -s tests -p test_evaluation.py —40개통과. 신규테스트는여유경계/공식test거부/실패미판정/고정목록변조/학습중복/ZIP·노트북일치검사. 새모델예측미실행,Colab실제패키지/영상경로는사용자실행에서확인. 다음하나:마지막셀로실행후 reference-metrics/execution/worker_result/manifest수신,실패시로그. 자동추가학습없음.


## 2026-09-24 — paired-dev 진행 manifest 덮어쓰기 오류 수정
사용자 Colab 실행에서2번째구간후 manifest.json 저장시FileExistsError. 원인:pp.write_json은open(x)로원본보존용인데driver가매구간동일파일로호출. 모델예측전준비단계6.004초실패,지표파일미생성. 기존결과Drive results/paired-dev-20260924T140638372749Z에execution/staging.log,manifest는기존셀구조상runtime에만있음. 사용자잘못/영상라벨문제아님.
run_paired.py의진행목록만임시JSON작성후replace로원자갱신하도록최소수정,아직시도안한행포함10행항상보존. pp.write_json원본보존함수변경없음. Colab최종저장에준비manifest복사추가(실패해도가능할때저장). 기존ZIP/셀/driver/receipt는before-manifest-fix에보존. 새ZIP329681bytes SHAadfe1d070970cdc1e6876a68333250e96aaaa9991ac75aba1b7ef02b04241b11. 기존마지막노트북셀의해시동기화. 데이터/모델/규칙/상한불변.
관련test_evaluation41개통과. 회귀시험은실제stage반복10회/가짜디코드9성공1실패로JSON갱신/전체행/실패보존을확인(실제영상디코드·모델아님). Colab재실행미확인. 사용자수정노트북마지막셀+수정ZIP로같은10구간만재시도. 이전실패결과/원본/모델보존,학습없음.


## 2026-09-24 — 같은 출처 5쌍 참고평가 완료: 전부 사고 예측
paired-dev-631/received에manifest/report/reference-metrics/worker_result 원본4파일수신보존·SHA기록. execution.json은미첨부,시간은사용자Colab출력근거. 고정목록/모델SHA/10ID/준비10성공/예측10성공/혼동행렬/평균loss일관성확인. 새디코딩/학습/추론없음.
같은5파일의정상가정5+사고주석5 전부사고예측. TN0 FP5 FN0 TP5,Accuracy50%,Precision50%,Recall100%,F166.67%,AP.5342857,평균CE2.40083464. Recall100%는모든입력을사고라고한결과이므로정상구분성공아님. normal점수는.983749/.999314/.917382/.995185/.990536,paired accident는.916996/.998921/.987312/.994607/.991660. 5쌍중3쌍에서정상점수가사고보다높다. 단순임계값상향으로모든쌍해결불가. 기존30의100%와다른시험목록/정상라벨근거이므로모델성능이100→50으로시간상악화했다고표현하지않는다. 동일631모델에서평가자료구성변경으로새약점드러남.
모든입력반복프레임12~15고유장,규칙v1정상가정은육안정답확정아님. 출처/배경shortcut또는움직임분별부족등은가설,인과확정아님. 알려진다른파일명분리만확인,사건/카메라독립미확인. 사고정상시간구분목표에불충분,배포준비완료아님.
사용자출력:준비26.012초/예측작업7.006초/저장전33.019초(Drive마운트업로드대기최종저장제외). 실제worker루프1.262초/modelBN불변true. Drive results/paired-dev-20260924T141354243337Z. 보고서이미지는별도PNG가필요;이미지미수신으로이번육안검수실행안함.
다음하나(제안):이5쌍은개발평가전용유지하고학습원본쪽의동일출처정상/사고구성개선실험설계. epoch/임계값자동변경없음. 정상가정불확실성은유지하며새자료를점수보고재라벨링하지않음. 반복소량학습보다원인에맞춘한개변경실험을먼저설계.


## 2026-09-24 — 기존 학습 원본의 정상가정 확대 목록80 확정
사용자 질문에규칙이모델예측을만든것아님/10개모두사고예측과가정정답비교로50%산출됨을설명. 학습선택은모델이맞힌것만선별하지않음. 기존631의ACC사고403파일에서CSV/분할/예약기준만으로정상0–2초후보선정. 287개는t<6으로4초여유불충족,28개는기존정상중복,8개는이전육안보류를유지. 나머지80개파일존재/공식IIDtrain/0–2초<=t-4/평가예약파일명그룹비중복assert통과. score사용없음.
artifacts/training/normal-631-20260924-01/normal-expansion-candidates.json에전체80행과제외323행/이유기록. 신규라벨user_assumed_pre_accident_normal,human_reviewedfalse,입력준비미실행. 기존631을변경하지않음. 모두성공시711구간(사고403/정상308),현재는711학습완료아님. 새독립원본추가0:기존학습사고파일의새시간구간임.
10평가구간/기존30/별도정상사례는학습에넣지않음. 다음하나:80개기존전처리입력준비Colab묶음;입력성공수를확인후동일모델/lr/5epoch의데이터구성한개변경실험범위를제시. 새학습/다운로드/클라우드실행없음. 목록검사만수행하고기존모델테스트반복없음.


## 2026-09-24 — 정상가정80 Colab 입력준비 연결
사용자 ‘그럼해보자’에따라기존정상80목록의입력준비작업완료. artifacts/colab/normal-80-20260924-01에prepare_inputs/candidates/colab-cell/normal-80-code.zip/bundle-receipt저장. ZIP17840bytes SHA49200dcda3a6a8b5e3d9cf8c288f89b4b64adea91201a1ea3353b9dce6aba26f. 기존normal-batch드라이버/공통preprocessing재사용,신규모델코드없음. 마지막notebooks/prepare_training_inputs_colab.ipynb 셀연결.
고정80해시/정상0/2초/주석t>=6/공식IIDtrain/가정출처/human_reviewedfalse/훈련승인false검사. 기존2초16장224 letterbox/nearest-bin-repeat-v1. save_unlabeled_input=True로PNG/frames.npy/frames.json/미리보기저장,입력에정답주입없음;manifest에사용자가정정상라벨0분리. 원본size/mtime변경시거부. 시작부터80행manifest저장하고임시파일원자갱신,실패/미실행행보존. 입력배열/시간기록SHA와원본PTS기록.
부모5분/내부280초/구간15초/Drive저장2분,진행 i/80표시. GPU불필요,패키지없으면자동설치안함. Colab실행/실제영상전처리/학습/추론미실행. 기존원본/631모델/평가목록보존. 입력모두성공해야잠재711이며아직학습완료아님.
검사 .venv-inspection/bin/python -m unittest discover -s tests -p test_normal_batch_review.py :7통과. 고정규칙/공식test거부/80회진행JSON갱신/80실패보존/ZIP노트북문법해시검사(가짜디코드,실제영상아님). 다음하나:사용자마지막전처리셀실행후manifest/execution수신,성공수량확정후동일설정학습연결. 자동학습없음.


## 2026-09-24 — 정상80 실제 manifest 수신·80개 전부 준비 성공
Downloads/manifest.json 실제파일JSON완전파싱,후보80ID/원본/CSV행/라벨/정책시각일치.80개모두prepared_assumed_normal,실패0. 각[16,224,224,3]/16시각0~2초/시간비감소/SHA필드확인. artifacts/colab/normal-80-20260924-01/received/manifest.json 보존,receipt-audit갱신. 기존잘린원문보존. 배열본체미수신이므로실제배열해시재계산검사가아닌manifest검사임을명시.
이전사용자실행기록221.880초,manifest221.760초,학습모델false. 잠재총711(403사고308정상)입력준비수량충족;새모델학습미실행. manifest경로는Colab임시/tmp/cctv-normal-80-1xfiambj/results라Drive영구폴더경로추정하지않음. 다음하나:사용자가출력된Drive결과전체경로전달하면영구입력경로로학습목록연결. 재전처리불필요,원본모델/평가분할보존.


## 2026-09-24 — 정상80 Drive 위치 연결·711 학습 셀 준비
사용자전달 /boram3-cctv-evaluation/training-preparation/normal-80-20260924T143922369468Z 를기존MyDrive기준전체경로로해석. 원격존재확인완료라고표시하지않고런타임manifest SHA=fadf4fa4b836169980818e484857737a09a413f86ddeec2c31144ebfca1cc7a1 일치검사후시작확인. 다르면학습차단. 신규80 배열/시간SHA를기존stage_expansion_inputs가복사시확인,영상재디코딩없음.
기존631행그대로+신규80=711(사고403정상308),정상가정어댑터별도추가로공식IIDtrain/0–2초/t>=6/사용자가정출처/사람검수false/원본시간기록동일성확인. 원본주석정답/사람검수로승격없음. 기존엄격/사람검수/임시22가드유지. 평가자료/보류8제외유지.
artifacts/colab/train-711-20260924-01/{training-input-proposal.json,training-config.json,run711.py,colab-cell.py,train-711-code.zip,bundle-receipt.json}. ZIP27415505bytes SHA78393e6462dffb03baa46ce84e32f8ab08d2f219f210a59d10eb97d79409be49. 이전번들25입력포함/신규80은Drive참조. 새가중치다운로드없음. notebooks/train_expansion_r3d18_colab.ipynb 마지막711셀만실행.
공식사전학습+새head초기화,기존모델이어학습아님. 동일lr.001/seed20260918/batch1/headonly/특징부BN고정/5epoch3555업데이트/저장50step. 기존승인대로학습30분종료제한없음,준비15분/저장2분/3step측정/메모리guard유지. 셀범위표시후사용자가시작입력,config기본실행false. 진행률/ETA/최근loss표시. 결과Drive새training-711-UTC/run 보존.
로컬검사 reviewed_normal_connection26+training39=65통과. 과거compare631번들테스트가현재소스최신파일/마지막셀과동일성을요구하던stale검사를과거노트북셀존재+고정ZIP해시검사로수정(과거묶음보존). 새711묶음은현재코드/설정/노트북동일성검사. Colab실행/배열원격해시/실제학습미실행. 다음하나:사용자711마지막셀실행후result/summary/epoch-results/execution확인. 자동평가없음.


## 2026-09-25 — 711 학습 완료 수신·같은5쌍 비교 연결
result(rootDownloads)/epoch/summary/execution(JSON3개지정폴더)와Colab출력읽기,train-711/received에바이트보존/해시감사. 5epoch3555업데이트/711구간(403사고308정상),출처사고403/DETRAC가정200/사람6/임시22/사고전가정80. 특징부BN유지/분류층변경/저장재로딩·재개성공수신. 모델본체미수신/원격직접검증미실행. Drive training-711-20260924T145537511407Z/run 최종저장성공은사용자로그근거.
실제epoch online평균loss .87623964/.76697188/.70176908/.66445669/.63284660. 마지막step.00938과epoch평균구분. 기존631과다른학습집합이므로loss절대값으로성능악화단정금지. epoch검증지표없음. 입력789.147초/루프472.648초/worker480.823초/명령1283.793초(마운트업로드대기최종저장제외). received/report.html에기록표·native meter 막대및해석저장. 새의존성/재학습없음.
다음우선비교같은5원본10구간연결:paired-dev-711-20260925-01/paired-711-code.zip356013bytes SHA f2f683b233eff5657af0cc25286c7c3ed10d1df3187937ef3097ba72e0c14246. 모델SHA는미수신이므로사용자711result전체SHA/학습목록provenance/모델step3555epoch5/클래스전처리검증으로다른모델거부하고실제SHA기록. 원본10재구성후기존631평가배열SHA/프레임상세동일성확인,다르면중단. 평가정답변경없음. 기존predict/metrics재사용. 이전631점수저장비교표추가. 준비5분/예측5분/저장2분. 노트북evaluate마지막711셀,추가학습없음.
관련test_evaluation42통과. 711Colab예측미실행. 기존30+정상사례까지의711평가도미실행;현재도착점(오탐개선결론)은아직대기. 보유약2400은사용자파일수보고,CSV2027행IIDtrain507/test1520은확인자료. 잔여파일을정상/학습으로자동할당안함. test보호/개발예약유지. 이번10결과부터확인후다음개선결정.


## 2026-09-25 — 711 고정 10구간 평가 수신: 오탐 감소·미탐 증가

사용자가 전달한 reference-metrics/comparison/execution/worker_result JSON을 artifacts/colab/paired-dev-711-20260925-01/received에 원문 보존하고 SHA256을 기록했다. 10행에서 혼동행렬을 재계산해 일치 확인했다. Colab 실행 기록은 성공, 준비 38.015초·worker 16.011초·저장 전 54.028초, 모델/BN 불변이다. 로컬 모델 연산·추가 학습은 하지 않았다. 모델 본체·원격 Drive 직접 검사는 미실행이다.

같은 5원본 10구간에서 631→711: TN 0→5, FP 5→0, FN 0→4, TP 5→1. Accuracy 50→60%, Precision 50→100%, Recall 100→20%, F1 66.67→33.33%, 참고 평균 loss 2.40083→1.06228. 새 모델 SHA d81a8a12c7b0ccb2235f655a1e96abf919e8a309927e52d9ae522e3d78fd1941. 사고 01·03·04·05를 놓쳤고 02만 탐지했다. Precision 100%의 분모는 경보 1개뿐이다. 정상80 추가 실험에서 오탐은 감소했으나 사고 누락이 증가했으므로 전체 개선·운영 적합·최종 모델 승격으로 판정하지 않는다. 정상 가정 라벨과 사건/카메라 독립성 미확인, independent_validation=false를 유지한다. 기존 30구간에 대한 711 평가는 아직 미실행이다.

다음 작업 하나(제안): 새 CCTV 시험을 위한 개발 검증 목록과 정답·측정 기준을 확정한다. 기존 보유 자료와 새 출처를 사용할 수 있으나 학습 원본/사건/재편집과 분리하고, 사고 시점뿐 아니라 정상 연속 영상도 포함한다. 최종 test는 튜닝에 사용하지 않는다. 영상 수나 epoch만으로 운영 가능 여부를 정하지 않고, 사고 사건 Recall·시간당 오탐·탐지 지연을 실제 사용 조건에서 확인한다. 추가 수집/다운로드/학습은 이번에 실행하지 않았다.


## 2026-09-25 — 711 고정30 참고평가 Colab 연결
사용자 승인 범위 CCTV-011/016/017: 기존 고정30입력과631점수재사용,711모델만예측. evaluation.comparison_603_worker에reviewed711분기/check_711_comparison추가;3555step/5epoch/711목록/수신모델SHA/631기준모델SHA/30ID확인. 기존입력해시·학습중복·클래스전처리검사유지. 정상별도사례1개는이번범위제외,원본재디코딩없음.
artifacts/colab/compare-711-20260925-01/{compare-711-code.zip,colab-cell.py,bundle-receipt.json};ZIP353736bytes SHA24bec3517518f4f5591b5a14af1ae0d93db771f38602dd747148ba70749c4550. notebooks/evaluate_r3d18_colab.ipynb 마지막711고정30셀연결. Drive training-711-20260924T145537511407Z/run 사용. 입력준비5분/예측5분/저장2분,진행0/30,새학습없음. 결과compare-711-UTC폴더.
검사 .venv-model-check/bin/python -m unittest discover -s tests -p test_evaluation.py. 관련44개 테스트 통과(4.197초, 가짜 입력·모델 시험이며 실제 CCTV 추론 아님). 기존paired711셀이항상마지막이어야한다는검사를노트북내보존확인으로변경;모델/개수/기준선거부·ZIP해시/코드동일성·셀문법검사추가. 실제Colab모델추론미실행,미탐/Recall개선미측정. 다음하나:사용자가새마지막셀실행후comparison/reference-metrics/execution/worker_result전달. 기존30은개발참고자료이며독립최종평가아님.


## 2026-09-25 — 711 고정30 평가 수신
711 고정30 Colab평가 실제결과4JSON을 compare-711-20260925-01/received에원문보존/SHA기록. 30행에서혼동행렬재계산일치,실패0. 631→711: TP20→6,FN0→14,TN10→10,FP0→0. Accuracy100→53.33%,Precision100→100%,Recall100→30%,F1100→46.15%,AP약1→.997619,참고CE.039059→1.030014. 실행성공이나미탐우선목표상711채택근거없음. 631비교기준보존,두모델모두새CCTV운영성능확정아님.
준비86.017초/worker12.021초/저장전98.039초,추론loop3.953초,model/BN불변. 기존코드ZIP SHA일치. 입력23재사용로그는엄격23+저FPS7=총30처리의일부이며23개만평가한것아님. 실제모델연산은사용자Colab;로컬은JSON산술검사만수행.
AP가높고Recall이낮은것은점수순위와현재판정경계에서의분류가다르기때문. 완전분별불능으로단정하지않으며정상80추가/새학습조건에따른점수변화원인미확정. 다음하나제안:저장된30및짝10점수를함께사용한판정기준민감도분석(추가추론불필요). 선택한임계값의효과는개발참고이며독립자료재검증필요;실서비스임계값자동변경없음. 추가학습없음.


## 2026-09-25 — 같은631 출처별 추출비중 비교 실험 준비
사용자다음작업요청에따라기존631목록바이트그대로보존,source-balanced-631-v1 추출정책만추가. source_balanced_order는회차별독립seed와그룹순환추출로사고315/ACC정상158/DETRAC정상158=631draws,5회3155업데이트. 정상28개회차당5~6회노출,전체자료한번씩보는epoch와다름. lr.001/momentum.9/batch1/headonly/공식가중치freshhead/seed20260918기준유지. 기존가중치이어학습아님. 정상22임시/DETRAC가정/사람6출처유지,평가8/제외원본학습불포함검사.
training.py에정책검증/그룹수403·28·200/제외원본거부/결정적회차순서및재개적용,기존기본순차루프보존. scripts/train_r3d18.py 새실험checkpoint50적용. run_balanced.py 실제추출ID목록sampling-schedule.json저장. notebooks/train_expansion_r3d18_colab.ipynb 마지막실험셀연결.
artifacts/colab/balanced-631-20260925-01 코드/설정/proposal/셀/ZIP보존. ZIP27395955bytes SHA191da2efc7bb01140c62220448e7eae25adb3333e133d287320c85f50826bfee. 기존허용묶음25입력배열포함,그외Drive기존입력재사용. 새다운로드/설치없음. 실제학습승인config false기본,Colab범위표시후시작입력. 준비15분/저장2분/5추출회차3155상한,기존사용자요청대로학습30분종료제한없음. 진행률/ETA/최근loss표시,새Drive balanced-631-UTC결과폴더사용. 자동평가없음.
관련tests/test_source_balanced_sampling.py5개(정확그룹수/반복횟수/결정성/잘못된범위거부/ZIP/실제로작은합성모델4step와2+2재개의sample_ids및가중치완전일치)와test_training.py39개통과. 실제CCTV모델연산/Colab학습은미실행. 도구준비완료와모델성능개선을구분. 소수정상28반복과적합위험,출처비중최적성미확정. 다음하나:사용자가실험범위확인후Colab마지막셀실행,result/summary/epoch-results/execution전달.


## 2026-09-25 — balanced-631 실제 Colab 학습 완료 수신
result/summary/Colab 출력 원문과 SHA를 artifacts/colab/balanced-631-20260925-01/received에 보존. 출력의 epoch-results를 추출 저장하고 완료 5회차·3155업데이트·입력631·고정특징부/BN 불변·저장 재로딩 확인을 대조했다. 전체 입력을 매번 한 번씩 보는 epoch가 아닌 출처별 균형 추출 5회차다. 실제 sampling-schedule.json은 미수신이므로 추출 ID 전체 감사는 미완료.
입력 준비 673.773초(11분14초), 학습 worker 444.194초(7분24초), 명령 전체 1135.988초(18분56초). mount/upload/user waiting/final Drive save 제외. loss 0.782901→0.547271→0.509455→0.356904→0.308111. 학습 중 평균 손실이며 기존 다른 추출분포 실험 loss와 직접 우열 비교하지 않는다. validation 미실행/F1 null은 오류가 아니라 미측정. summary.staging.training_worker_launched=false는 준비 단계의 스냅샷이고 상위 training_run=true 및 result 업데이트가 실제 학습 완료 근거다.
Drive balanced-631-20260925T001525003206Z/run 최종 저장 성공은 사용자 출력으로 확인; Drive 모델 직접 해시 확인은 미실행. Mac 모델 연산/추가 학습/평가 없음. 다음 하나: 새 모델과 기존631을 같은 판정 기준으로 고정30 및 검수 후8구간에서 비교하여 미탐 유지·오탐 감소를 확인. 평가8은 학습에 넣지 않는다. 이후 실행은 정상 진행 중 고정 시간 강제종료 대신 진행 감시/재개 방식으로 보완하라는 사용자 요청을 기록하되 이번 완료 ZIP은 변경하지 않았다.


## 2026-09-25 — 균형추출631 고정30·검수8 비교 셀 및 그래프 준비
사용자 비교/그래프 요청. artifacts/colab/compare-balanced-631-20260925-01의 run_compare.py, colab-cell.py, 코드ZIP 준비; 평가노트북 마지막 셀 추가. ZIP SHA188a8e0aa945a27366d49d38027765f733709268e93e9daf105357e491e7d61b,377980bytes. 새 학습 없음. 사용자 Colab balanced-631-20260925T001525003206Z/run 모델/result/학습목록 사용, result SHA 및 3155step/5회차/source-balanced 정책/모델 전처리·목록 출처 검증. 기존631 SHA 기준30 점수/짝8 점수 별도 비교.
기존30 입력 재사용, 검수8 원본의 같은 구간만 재구성 후 이전 배열SHA와 input 상세 일치 요구. 제외 Z 원본과 학습 파일명가족 중복 거부. 사고4 사용자검수·정상4 가정 구분. 모델변경/BN불변 검사는 기존 predict 재사용. 고정0.5/동률정상,38행 성공 요구. report.html에 묶음별 미탐·오탐 및 Recall/Precision/F1 4개 SVG 그래프와 각 구간 점수 전후 표 제공. 집계38 단일 성능표로 합치지 않음. 독립검증은 아님.
정상 진행 중 총시간 종료 없음; 준비 크기/로그/예측수 변화를 감시하며 15분 무변화일 때 중단. 모델/입력/로그 준비와 저장 상황에 따라 Colab 자체 연결제한은 여전히 적용. 별도 av 설치 자동실행 없음. 실제 Colab 평가 미실행. unittest test_evaluation.py44 통과(4.442초), 별도 30/8구성·기준모델SHA·제외원본·4SVG·ZIP해시/코드일치·셀문법 확인 통과. 로컬 모델 연산 없음.
다음 하나: 사용자 새 평가 셀 실행 후 comparison.json/reference-metrics.json/execution.json/worker_result.json 전달. 미탐 유지·오탐 감소를 보고 채택 여부 판단; 추가 학습 자동 실행 없음.


## 2026-09-25 — 균형추출631 비교 결과 수신·노트북 기록 보존
사용자 전달 4JSON 원문과 SHA/대조영수증을 compare-balanced-631-20260925-01/received에 보존. 30/8 ID 일치·38행 성공·0.5판정 재현·혼동행렬 재계산·reference-metrics 일치 확인. 제공 run_compare.report로 실제 수신값 HTML/SVG 재생성(모델 재실행 아님). comparison-record.ipynb에 제공 코드와 수신 JSON을 명시적으로 구분해 보존; 사용자 Colab 실제 수정 셀/전체 출력의 완전 복제는 아님. 향후 ipynb/Drive 저장 중심 사용 선호 기록.
새 모델 SHA91ede60697c6970f1f8834868beb0a5fc9a61fc5dbee1d660804fc95280c6e37. 고정30: 기존 TP20TN10FP0FN0→새 TP14TN10FP0FN6, Recall100→70%, Precision100→100%, F1100→82.35%, Accuracy100→80%. 검수8: 기존 TP4TN0FP4FN0→새 TP4TN1FP3FN0, Recall100%유지, Precision50→57.14%, F166.67→72.73%. 오탐1 감소 대신 다른 고정30에서 미탐6 추가. 미탐 우선 사용자 목표상 새 모델 채택 근거 부족, 기존631 기준 유지. 정상 가정/작은 반복 개발셋/독립성 미확인 유지; 전체CCTV 성능 확정 불가.
저장전150.098초, 순수추론4.399초, CUDA/모델BN불변, 추가학습false. 출력 widget 메시지는 completed 및38개실제예측으로 실행 실패가 아님을 확인. 다음 하나 제안: 두 묶음의 새/기존 저장점수로 미탐6의 점수 및 판정경계 변화 분석(모델 재실행·새학습 없이), 실제 기준 자동변경 없음.


## 2026-09-25 — 기존631·711·균형추출631 동일 평가 비교
사용자711추가비교요청. artifacts/analysis/three-models-20260925-01/compare.py 실행,comparison.json과4개SVG포함report.html 생성. 저장점수만 사용/새학습·추론없음. 수신파일SHA,711모델동일성,각30/8행ID·정답동일성,0.5판정,제외01원본 검증. 711과거10은같은8ID로필터후재계산했으며기존10원문보존.
고정30 기존631/711/균형631: FN0/14/6,FP0/0/0,Recall100/30/70%,F1100/46.15/82.35%. 동일8:FN0/3/0,FP4/0/3,Recall100/25/100%,Precision50/100/57.14%,F166.67/40/72.73%. 711동일8 Recall25%는과거10 Recall20%와분모가달라개선아님. 미탐우선목표에서기존631비교기준유지. 검수8에서711과균형631 Accuracy62.5%동일하지만미탐/오탐구성다름. 독립검증미확인/정상가정/소표본제약유지. 다음하나:저장점수의미탐변화분석,자동재학습/임계값변경없음.


## 2026-09-25 — 분류층 유지·개발용 검증100 후보 목록 대조
사용자 결정: 현 R3D-18 head-only 유지, 특징 추출층 미세조정/모델교체 자동진행 없음. 기존631 기준으로 개발용 검증 최대100 목표를 먼저 추진. development-100-20260925-01/candidate-plan.json 및 report.html 생성. 기존 post711 14파일을 세 실험 학습proposal와 고정30/검수8의 파일명가족 대조, 제외Z 거부, 같은가족4보류. 남은10파일에서 사고10+정상가정1=11구간 후보. 목표50+50에 사고40/정상49 부족. 후보이지독립검증확정자료아님;기존준비노출/전체과거예측감사미완료/사건카메라독립성미확인 유지. T9지정경로의후보파일존재만재확인;영상디코딩/CSV신규전체감사/모델실행없음. DETRAC미배정공식train시퀀스0(기존원장기준),공식test보호.
다음 필요사항:추가사고·정상원본출처확보후같은제외규칙/정답근거검사. 수량을맞추려고평가자료학습전환/기존자료세분화로독립원본수를부풀리지않음. 평가100이확보됐다고표시하지않음.


## 2026-09-25 — 개발11 동일 입력 세 모델 Colab 평가 완료
사용자의 연속 진행 승인으로 Chrome Colab의 기존 노트북에 별도 셀을 추가하여 기존631·711·균형631을 고정11구간에 각각 예측했다. 입력 재추출/학습/판정 기준 변경 없이 기존 입력을 재사용. 사고 주석10·정상 사용자 가정1/10파일명묶음이며 독립 검증이 아니다.

| 모델 | TP/TN/FP/FN | Recall | Precision | F1 |
|---|---|---|---|---|
| 기존631 | 10/0/1/0 | 100% | 90.91% | 95.24% |
| 711 | 3/1/0/7 | 30% | 100% | 46.15% |
| 균형631 | 6/1/0/4 | 60% | 100% | 75% |

33예측 모두 성공, returncode0, 저장 전100.012초. 세 모델 CUDA 추론/모델·BN 불변 확인. 입력/모델/학습목록 SHA와 학습 파일명묶음 비중복 검사. 수신 후 ID·정답·argmax 판정·혼동행렬·Accuracy/Precision/Recall/F1 재계산 일치. Drive comparison SHA 일치도 Colab 셀[4]에서 확인. 실제 코드와 ZIP/원문/실행기록 ipynb/감사 SHA/HTML 그래프 보존; 실행기록은 전체 Colab 노트북의 복제가 아니다.

미탐 우선으로 기존631 기준 유지. 단, 631은 11개 전부 사고 판정이므로 정상 분별력이 입증된 것은 아니다. 정상1개의 결과로 일반 CCTV 오탐 성능을 추정하지 않는다. 사고 시점 중심 입력·정상 가정·사건/카메라 독립성 미확인 유지. 기존30/검수8과 분모를 합쳐 성능을 부풀리지 않음. 다음 하나는 학습과 분리된 정상 개발 원본 추가 확보를 위한 출처/이용조건 검토. 최대100 계획은 아직 진행 중이며 추가학습/임계값 변경은 하지 않았다.

근거: `artifacts/colab/compare-development-11-20260925-01/received/report.html`, `verification.json`, `execution-record.ipynb`, `verify_received.py`. Drive: `results/compare-development11-20260925T033901338734Z`.
