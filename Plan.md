# CCTV 실시간 객체 탐지 + 사고/충돌 탐지 프로젝트 실행 계획

> **2026-10-01 추가 요청 반영:** Streamlit의 A/B 선택·동일 영상 비교를 현재 TODO의 P4/P6에 포함했다. 검증된 두 버전을 이미지에서 선택 가능하게 구성하고 기본 사용 버전은 비교 결과로 정한다. GPU 한 슬롯에서 순차 처리하며 P1 환경 확인→P2 실행 조건→P3 비교 순서는 유지한다. 구현 완료가 아닌 계획 반영이다.

> **2026-10-01 현재 작업 안내:** 실행 기준은 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) 상단 P0~P6 TODO다. 최신 사용자 요청에 따라 기존 X3D-S+YOLO11s 묶음 A와 deployment_handoff_1001(v11) 묶음 B 두 버전만 비교한다. X3D 가중치는 같고 YOLO/후처리 차이가 있으며, 다른 후보·교차조합은 추후 요청 전 보류한다. 비교 후 선택 버전을 Runpod 이미지로 재현한다. 각 단계 실제 검증 후 다음으로 진행하며 추가 학습·범위 확대는 하지 않는다. 아래 본문과 9/19의 '현재' 설명은 과거 이력으로만 보존한다. 최신 사실은 [작업 재개 요약](docs/current_status.md)을 따른다.

> **과거 참고안 — 2026-09-19 현재 실행 범위에 적용하지 않음.** 아래 본문은 사용자 자료로 보존한다. YOLO·ByteTrack·R(2+1)D 통합, 3일 MVP 및 설치·학습 예시는 현재 승인 작업이 아니다. 현재는 M1 Max/64GB 로컬 우선·필요 시 Colab·R3D-18 단독 기준선이며, 현황 확인과 문서 정합성 수정까지만 진행한다. [현재 현황](docs/current_status.md)을 따른다.

> 목표: **먼저 한 번의 End-to-End 흐름으로 결과를 만든다.**  
> 그 다음에 **더 빠르게**, 마지막으로 **더 정확하게** 만든다.  
> 기간 기준: 3일 MVP → 이후 실험/고도화 가능

---

## 0. 최종 결정

### 데이터셋
**ACCIDENT (A Benchmark Dataset for Vehicle Accident Detection from Traffic Surveillance Videos)**

선정 이유:

- **고정형 교통 CCTV 관점**의 차량 사고 탐지 benchmark
- 실제 CCTV 약 **2,027개**와 CARLA 기반 합성 CCTV 약 **2,211개**로 구성
- 사고의 **발생 시점(when), 위치(where), 충돌 유형(what)** 정보를 제공
- 합성 데이터에는 **Bounding Box, Segmentation Mask, Tracklet** 등 더 풍부한 annotation 제공
- 실제 CCTV 영상은 저화질·압축·원거리·가림 등 실제 운영 환경의 어려움을 포함
- 객체 탐지/추적과 사고 탐지를 한 데이터셋 안에서 연결해 실험하기 좋음
- 공식 repository에 heuristic baseline과 데이터 처리 예제가 있어 3일 MVP에 유리

공식 프로젝트:

- https://accidentbench.github.io/

공식 저장소:

- https://github.com/accidentbench/ACCIDENT

논문:

- *ACCIDENT: A Benchmark Dataset for Vehicle Accident Detection from Traffic Surveillance Videos*
- CVPR 2026 Workshop

---

## 1. 사용할 모델과 역할

### 1-1. 객체 탐지: YOLO26n

사용 모델:

```text
YOLO26n
```

역할:

```text
Frame
 ↓
사람 / 자동차 / 버스 / 트럭 / 오토바이 등 탐지
 ↓
Bounding Box + Class + Confidence
```

YOLO를 새로 학습하지 않는다.

YOLO26n은 COCO 사전학습 모델을 그대로 사용한다.

우선 사용할 클래스:

```text
person
bicycle
car
motorcycle
bus
truck
```

COCO class index 기준:

```python
TARGET_CLASSES = [0, 1, 2, 3, 5, 7]
```

선정 이유:

- 첫 결과물을 매우 빠르게 만들 수 있음
- `n` 모델은 가장 작은 계열이라 실시간 처리 실험에 적합
- Ultralytics에서 Detection / Tracking API를 바로 사용할 수 있음
- 현재 목표는 "객체 탐지 모델 자체 연구"가 아니라 사고 탐지 파이프라인 구축

공식 문서:

- https://docs.ultralytics.com/models/yolo26/
- https://docs.ultralytics.com/tasks/detect/

---

### 1-2. 객체 추적: ByteTrack

ByteTrack은 사고 판별 모델이 아니라 **Tracking 알고리즘**이다.

역할:

```text
Frame 1: Car → ID 12
Frame 2: Car → ID 12
Frame 3: Car → ID 12
Frame 4: Car → ID 12
```

즉 프레임마다 새롭게 탐지되는 차량을 같은 차량으로 연결한다.

사용 이유:

- 차량 이동 궤적을 얻을 수 있음
- 속도 변화, 이동 방향 변화, 차량 간 접근 등을 계산할 수 있음
- 이후 사고 모델을 모든 순간 실행하지 않고 "의심 상황"에만 실행하는 최적화가 가능
- Ultralytics Tracking API에서 바로 사용 가능

초기 설정:

```python
tracker="bytetrack.yaml"
```

공식 문서:

- https://docs.ultralytics.com/modes/track

---

### 1-3. 사고/충돌 이벤트 탐지: R(2+1)D-18

사용 모델:

```text
torchvision.models.video.r2plus1d_18
```

사전학습:

```text
Kinetics-400
```

역할:

```text
한 장의 이미지가 아니라

Frame t-15
Frame t-14
...
Frame t

16개 프레임
 ↓
R(2+1)D-18
 ↓
Normal / Accident
```

즉 **시간 변화까지 보는 Video Classification 모델**이다.

첫 MVP에서는 출력 클래스를 두 개로 제한한다.

```text
0 = Normal
1 = Accident / Collision Event
```

선정 이유:

- 이미지 CNN보다 시간 정보를 직접 사용
- Transformer 기반 VideoMAE / TimeSformer보다 MVP가 단순함
- torchvision에 공식 사전학습 weight가 있음
- 16-frame 입력을 기본적으로 다루기 좋음
- Kinetics-400 사전학습 weight를 가져와 마지막 classifier만 교체해서 Fine-tuning 가능
- 3일 프로젝트에서 "직접 영상 모델을 학습했다"는 경험까지 확보 가능

공식 문서:

- https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.video.r2plus1d_18.html

---

# 2. 객체 탐지 / 충돌 탐지 / 사고 탐지의 관계

이 세 가지를 같은 문제로 생각하면 안 된다.

## 객체 탐지

질문:

> 화면에 무엇이 어디 있는가?

출력:

```text
Car      [x1, y1, x2, y2]
Person   [x1, y1, x2, y2]
Truck    [x1, y1, x2, y2]
```

담당:

```text
YOLO26n
```

---

## 충돌 탐지

질문:

> 객체들의 시간적 움직임에서 충돌로 보이는 사건이 발생했는가?

필요 정보 예시:

```text
차량 간 거리 감소
속도 급변
방향 급변
BBox 접근/겹침
충돌 직후 정지
```

이것은 단일 이미지보다 **여러 프레임의 변화**가 중요하다.

---

## 사고 탐지

사고는 충돌보다 더 넓은 개념이다.

예:

```text
차량 ↔ 차량 충돌
차량 ↔ 보행자 충돌
단독 충돌
전복
도로 이탈
기타 비정상 상황
```

### 이번 MVP의 범위

처음부터 이걸 모두 분리하지 않는다.

```text
Normal
vs
Accident Event
```

만 먼저 학습한다.

즉 MVP에서는:

```text
Accident Detection ≒ Collision/Accident Event Detection
```

으로 단순화한다.

End-to-End 성공 이후 필요하면:

```text
Normal
Vehicle-Vehicle Collision
Vehicle-Pedestrian Collision
Single Vehicle Accident
...
```

처럼 확장한다.

---

# 3. 최종 시스템 구조

## MVP 구조

```text
                CCTV / ACCIDENT Video Stream
                            │
                            ▼
                     ┌────────────┐
                     │ YOLO26n    │
                     │ Detection  │
                     └─────┬──────┘
                           │
              bbox / class / confidence
                           │
                           ▼
                     ┌────────────┐
                     │ ByteTrack  │
                     │ Tracking   │
                     └─────┬──────┘
                           │
                 track_id / trajectory
                           │
                           │
Frame Buffer ──────────────┼──────────────┐
16 frames                  │              │
                           │              ▼
                           │       ┌──────────────┐
                           │       │ R(2+1)D-18   │
                           │       │ Accident     │
                           │       │ Classifier   │
                           │       └──────┬───────┘
                           │              │
                           │        accident_score
                           │              │
                           └──────┬───────┘
                                  ▼
                           Event Processor
                                  │
                     ┌────────────┴────────────┐
                     ▼                         ▼
               output_demo.mp4             events.json
```

중요:

### 첫 버전에서는 YOLO와 R(2+1)D를 억지로 연결하지 않는다.

둘은 같은 영상 스트림을 사용하되 역할을 분리한다.

```text
YOLO + ByteTrack
→ "무슨 객체가 움직이고 있는가"

R(2+1)D
→ "최근 영상 흐름이 사고인가"
```

이 구조가 먼저 동작하게 만든다.

그 다음 Tracking 정보를 사용해서 사고 모델을 효율화한다.

---

# 4. 프로젝트의 우선순위

## P0 — 무조건 먼저 성공

목표:

> 영상 하나를 입력하면 객체가 탐지되고, ID가 붙고, 사고 확률이 표시되고, 이벤트가 저장된다.

정확도가 낮아도 된다.

완성 결과:

```text
ACCIDENT CCTV video / input.mp4

↓

화면:
Car #12
Car #7
Person #3

Accident Score: 0.83
⚠ ACCIDENT DETECTED

↓

output_demo.mp4

events.json
```

예:

```json
{
  "frame": 382,
  "timestamp": 38.2,
  "accident_score": 0.83,
  "detected_objects": 7,
  "event": "ACCIDENT"
}
```

이 결과가 나오기 전까지 모델 변경, 최적화, 복잡한 실험을 하지 않는다.

---

## P1 — 속도 개선

P0가 성공한 다음 질문:

> 같은 결과를 더 적은 연산으로 만들 수 있는가?

측정:

```text
FPS
평균 inference latency
P95 latency
R(2+1)D 호출 횟수
```

---

## P2 — 정확도 개선

그 다음 질문:

> 사고를 놓치지 않으면서 False Positive를 줄일 수 있는가?

측정:

```text
Precision
Recall
F1
PR-AUC
False Alarms / minute
Detection Delay
```

---

# 5. 폴더 구조

```text
cctv-accident-detection/
│
├── data/
│   ├── raw/
│   │   └── accident/
│   │       ├── real/
│   │       ├── synthetic/
│   │       ├── annotations/
│   │       └── metadata/
│   │
│   └── processed/
│       ├── metadata.csv
│       ├── train.csv
│       ├── val.csv
│       └── test.csv
│
├── models/
│   ├── accident/
│   │   └── best.pt
│   └── yolo/
│
├── outputs/
│   ├── detection_demo.mp4
│   ├── tracking_demo.mp4
│   ├── final_demo.mp4
│   ├── events.json
│   └── metrics.json
│
├── src/
│   ├── data/
│   │   ├── inspect_accident.py
│   │   ├── build_index.py
│   │   └── build_clips.py
│   │
│   ├── detection/
│   │   └── yolo_detector.py
│   │
│   ├── tracking/
│   │   └── tracker.py
│   │
│   ├── accident/
│   │   ├── dataset.py
│   │   ├── model.py
│   │   ├── train.py
│   │   └── evaluate.py
│   │
│   └── pipeline/
│       └── run.py
│
├── experiments/
│   └── results.csv
│
├── requirements.txt
└── README.md
```

---

# 6. 환경 구축

Mac 기준:

```bash
mkdir cctv-accident-detection
cd cctv-accident-detection

uv venv --python 3.12
source .venv/bin/activate
```

필요 패키지:

```bash
uv pip install \
  torch \
  torchvision \
  ultralytics \
  opencv-python \
  pandas \
  openpyxl \
  numpy \
  scikit-learn \
  tqdm \
  matplotlib
```

PyTorch Device 확인:

```python
import torch

if torch.cuda.is_available():
    device = "cuda"
elif torch.backends.mps.is_available():
    device = "mps"
else:
    device = "cpu"

print(device)
```

### 환경 사용 원칙

```text
M1 Pro
→ 데이터 확인
→ YOLO 추론
→ Tracking
→ End-to-End 실행

Colab GPU
→ R(2+1)D Fine-tuning이 오래 걸릴 때 사용
```

처음부터 Colab 환경에 모든 코드를 종속시키지 않는다.

---

# 7. STEP 1 — ACCIDENT EDA

## 목표

모델 학습 전에 데이터를 빠르게 확인해서 아래 질문에 답한다.

```text
1. 영상은 어떤 형태인가?
2. 사고 annotation은 어떤 기준인가?
3. Real / Synthetic 차이가 얼마나 큰가?
4. YOLO가 객체를 탐지할 수 있을 정도로 차량이 보이는가?
5. Train / Validation / Test를 어떻게 나눠야 하는가?
```

EDA 자체가 목적은 아니다.

**이 데이터를 어떻게 전처리하고 어떤 방식으로 학습할지 결정하기 위한 최소 EDA만 수행한다.**

3일 프로젝트이므로 EDA는 오래 끌지 않는다.

---

## 7-1. 데이터 구조 확인

먼저 ACCIDENT 데이터의 실제 폴더와 annotation 구조를 확인한다.

확인할 항목:

```text
video file 위치
annotation file 위치
Real / Synthetic 구분 방법
fps 정보
accident time 표현 방식
collision type
collision location
bbox / tracklet 제공 여부
```

중요:

인터넷 예제의 field명을 그대로 믿지 말고
다운로드한 실제 annotation 파일을 직접 출력해서 확인한다.

확인 후 프로젝트 내부에서는 다음과 같은 공통 schema로 정리한다.

```json
{
  "video_id": "sample_001",
  "source_type": "synthetic",
  "fps": 10,
  "width": 1280,
  "height": 720,
  "duration_sec": 12.4,
  "accident_time": 8.7,
  "collision_type": "vehicle_vehicle"
}
```

---

## 7-2. 기본 통계 확인

최소한 다음 통계를 계산한다.

```text
전체 영상 개수
Real 영상 개수
Synthetic 영상 개수

FPS 분포
영상 길이 분포
해상도 분포

사고 유형별 개수
```

가능하면 다음도 확인한다.

```text
주간 / 야간
저화질 / 고화질
차량이 크게 보이는 영상 / 작게 보이는 영상
```

---

## 7-3. Real / Synthetic 차이 확인

ACCIDENT의 중요한 특징은
Synthetic과 Real CCTV가 같이 있다는 점이다.

따라서 두 domain을 반드시 따로 본다.

확인 예:

```text
Synthetic

해상도
차량 크기
화질
카메라 각도
배경 복잡도

vs

Real CCTV

해상도
차량 크기
압축
motion blur
야간
가림
```

이 단계의 목적:

```text
Synthetic에서 학습한 모델이
Real CCTV에서 성능이 떨어질 가능성이 있는가?
```

를 미리 파악하는 것이다.

---

## 7-4. 사고 annotation 직접 확인

최소 5~10개 사고 영상을 직접 확인한다.

각 영상에서:

```text
사고 전
사고 발생 시점
사고 직후
```

프레임을 추출한다.

예:

```text
outputs/eda/
├── sample_01_before.jpg
├── sample_01_accident.jpg
├── sample_01_after.jpg
├── sample_02_before.jpg
├── sample_02_accident.jpg
└── sample_02_after.jpg
```

확인할 것:

```text
annotation의 accident time이 실제 영상과 맞는가?
frame index인가 timestamp인가?
사고 시작만 있는가?
사고 구간 start/end가 있는가?
```

---

## 7-5. 객체 크기와 화질 확인

현재 파이프라인은:

```text
YOLO
 ↓
ByteTrack
 ↓
사고 판단
```

이므로 객체 탐지가 가능한 영상인지 확인해야 한다.

Real CCTV 샘플을 최소 다음 기준으로 직접 저장한다.

```text
good_quality.jpg
poor_quality.jpg
night.jpg
small_vehicle.jpg
occlusion.jpg
```

그리고 눈으로 확인한다.

질문:

```text
차량이 너무 작아서 식별이 어려운가?
사람은 탐지 가능한 크기인가?
여러 차량이 겹쳐 있는가?
카메라가 너무 멀리 있는가?
```

---

## 7-6. YOLO 간단 사전 테스트

본격적인 STEP 2 전에
EDA의 일부로 몇 개 영상만 YOLO에 넣어본다.

목적은 성능 측정이 아니라:

```text
이 데이터에서 객체 탐지가 기본적으로 가능한가?
```

를 확인하는 것이다.

샘플:

```text
Real 5개
Synthetic 5개
```

정도면 충분하다.

확인할 것:

```text
Car detection 여부
Person detection 여부
작은 차량 누락 여부
False Positive 여부
저화질 영상에서 탐지가 무너지는지
```

여기서 YOLO가 거의 아무것도 잡지 못한다면
뒤 단계로 바로 넘어가지 않고 원인을 먼저 확인한다.

---

## 7-7. 데이터 분할 위험 확인

영상 데이터에서는 frame 단위 Random Split을 사용하면 안 된다.

잘못된 예:

```text
같은 영상

Frame 100~115 → Train
Frame 105~120 → Test
```

거의 동일한 영상이 Train/Test에 들어가기 때문에
성능이 과대평가된다.

따라서:

```text
원본 video_id 기준 분할
```

을 사용한다.

가능하면 ACCIDENT 공식 split을 우선 사용한다.

---

## STEP 1 결과물

EDA가 끝나면 아래 결과만 있으면 충분하다.

```text
outputs/eda/
├── resolution_distribution.png
├── duration_distribution.png
├── collision_type_distribution.png
│
├── real/
│   ├── good_quality.jpg
│   ├── poor_quality.jpg
│   ├── night.jpg
│   └── small_vehicle.jpg
│
└── synthetic/
    ├── sample_01.jpg
    └── sample_02.jpg
```

추가로 간단한 요약 파일을 만든다.

```text
outputs/eda/summary.md
```

예:

```text
전체 영상: ...
Real: ...
Synthetic: ...

주요 해상도: ...
평균 영상 길이: ...

Real CCTV에서 작은 차량이 많음
일부 영상에서 compression artifact 확인
Synthetic과 Real 화질 차이가 큼

YOLO pretrained 모델은
대부분 차량을 탐지했지만
저화질/원거리 영상에서 누락 발생
```

---

## STEP 1 완료 조건

다음 질문에 답할 수 있으면 끝낸다.

```text
ACCIDENT 데이터 구조를 이해했는가?
사고 annotation 의미를 이해했는가?
Real / Synthetic 차이를 확인했는가?
YOLO가 차량/사람을 탐지할 수 있는가?
Train/Test leakage 위험을 이해했는가?
```

이 다섯 가지에 답할 수 있으면
EDA를 더 길게 하지 않고 STEP 2로 넘어간다.

---

## EDA 시간 제한

3일 MVP 기준:

```text
1~3시간
```

이상을 쓰지 않는다.

목표는 완벽한 데이터 분석이 아니라

```text
모델 설계에 필요한 정보 확보
```

이다.

---

# 8. STEP 2 — YOLO26n 객체 탐지

## 목표

ACCIDENT CCTV frame 하나에서 다음을 탐지한다.

```text
Person
Car
Bus
Truck
Motorcycle
Bicycle
```

기본 코드:

```python
from ultralytics import YOLO

model = YOLO("yolo26n.pt")

result = model(
    frame,
    classes=[0, 1, 2, 3, 5, 7],
    conf=0.25,
)
```

---

## 처음에는 학습하지 않는다

잘못된 접근:

```text
ACCIDENT
 ↓
YOLO 새로 학습
```

ACCIDENT 합성 데이터에는 bbox/tracklet annotation이 있지만, 첫 MVP에서는 객체 탐지 모델 자체를 새로 학습하지 않는다.

따라서:

```text
COCO pretrained YOLO26n
 ↓
바로 inference
```

한다.

---

## STEP 2 결과물

```text
outputs/detection_demo.mp4
```

화면 예:

```text
[Car 0.87]
[Bus 0.72]
[Person 0.61]
```

추가 기록:

```json
{
  "processed_frames": 1000,
  "elapsed_seconds": 84.2,
  "fps": 11.87
}
```

### 완료 조건

- 사람/차량 Bounding Box가 영상에 표시됨
- 영상 전체를 순차 처리할 수 있음
- 현재 Mac에서의 FPS를 기록함

정확도가 조금 낮아도 다음 단계로 간다.

---

# 9. STEP 3 — ByteTrack Tracking

Detection 결과를 프레임 단위로 끝내지 않는다.

```python
results = model.track(
    frame,
    persist=True,
    tracker="bytetrack.yaml",
    classes=[0, 1, 2, 3, 5, 7],
)
```

출력:

```text
Car #4
Car #7
Person #11
```

프레임이 진행되어도 같은 ID를 유지하는지 확인한다.

---

## 저장할 정보

각 frame마다:

```json
{
  "frame": 120,
  "objects": [
    {
      "track_id": 4,
      "class": "car",
      "bbox": [120, 201, 180, 250],
      "confidence": 0.91
    }
  ]
}
```

이를 통해 trajectory를 만들 수 있다.

```text
Car #4

Frame 100 → (100, 200)
Frame 101 → (105, 201)
Frame 102 → (111, 203)
Frame 103 → (120, 205)
```

---

## STEP 3 결과물

```text
outputs/tracking_demo.mp4
outputs/tracks.json
```

### 완료 조건

- 동일 차량에 ID가 지속적으로 유지되는 장면이 확인됨
- 차량별 중심 좌표 history를 저장할 수 있음

---

# 10. STEP 4 — 사고 모델용 데이터 만들기

이 단계가 영상 학습에서 가장 중요하다.

## 데이터 사용 원칙

ACCIDENT는 실제 CCTV와 합성 CCTV를 모두 가진다.

첫 3일 MVP에서는 다음 순서로 사용한다.

```text
1. Synthetic
   → annotation이 풍부하므로 학습/파이프라인 검증에 우선 사용

2. Real CCTV
   → 실제 저화질 CCTV에서 동작하는지 검증/데모에 사용
```

주의:

실제 benchmark의 real CCTV test label은 공개 범위가 제한될 수 있으므로,
**실제 다운로드본에서 사용할 수 있는 annotation 범위를 먼저 확인한다.**

---

## 기본 설정

MVP 내부 처리 FPS:

```text
10 FPS
```

Video classifier 입력:

```text
16 frames
```

즉 모델은 대략 최근:

```text
1.6초
```

의 흐름을 본다.

---

# 11. 미래 프레임 누수 금지

실시간 시스템에서 현재 시점이 `t`라면 사용할 수 있는 것은:

```text
t-15 ... t
```

뿐이다.

사용하면 안 되는 것:

```text
t+1
t+2
...
```

따라서 accident time을 알고 있어도
실시간 추론에서는 미래 frame을 사용하지 않는다.

학습 데이터도 이를 지켜야 한다.

---

# 12. Positive / Negative Clip 생성

예:

```text
accident_start = 200
```

### Negative 예

사고와 충분히 떨어진 이전 구간:

```text
frames 120 ~ 135
frames 140 ~ 155
frames 160 ~ 175
```

Label:

```text
0
```

### Positive 예

최근 16 frame 안에 사고 시작이 포함되도록 구성:

```text
frames 185 ~ 200
frames 189 ~ 204
frames 193 ~ 208
```

Label:

```text
1
```

accident end가 제공되는 경우에는
사고 구간을 Positive로 확장할 수 있다.

---

## 애매한 영역은 첫 학습에서 제외

예:

```text
사고 시작 직전 0.5~1초
```

에는 이미 위험 행동이 있을 수 있다.

MVP에서는 이런 sample을 굳이 Negative라고 강제로 학습하지 않는다.

처음에는:

```text
명확한 Normal
vs
명확한 Accident
```

부터 학습한다.

---

## Synthetic / Real 분리 실험

첫 baseline:

```text
Train
→ Synthetic

Validation
→ Synthetic

Demo / Generalization Check
→ Real CCTV
```

그 다음:

```text
Synthetic에서 학습한 모델이
저화질 Real CCTV에서 얼마나 성능이 떨어지는가?
```

를 별도 실험으로 기록한다.

이 차이는 프로젝트의 좋은 분석 주제가 된다.

---

# 13. Train / Validation / Test Leakage 방지

가장 중요한 원칙:

```text
frame 단위 random split 금지
```

예를 들어 같은 사고 영상에서:

```text
Frame 100~115 → Train
Frame 104~119 → Test
```

가 되면 사실상 거의 같은 영상이 Train/Test에 들어간다.

성능이 부풀려진다.

### 우선순위

1. ACCIDENT 공식 repository / benchmark split 확인
2. 공식 split이 있다면 우선 사용
3. 같은 원본 영상에서 만든 clip이 Train/Test에 동시에 들어가지 않게 함
4. synthetic / real source를 기록해 domain별 성능을 따로 볼 수 있게 함

---

## STEP 4 결과물

```text
data/processed/train.csv
data/processed/val.csv
data/processed/test.csv
```

예:

```csv
clip_id,source_video,start_frame,end_frame,label
000001,video01,120,135,0
000002,video01,185,200,1
```

추가 출력:

```text
Train
Normal:   ...
Accident: ...

Validation
Normal:   ...
Accident: ...

Test
Normal:   ...
Accident: ...
```

### 완료 조건

- 16-frame clip dataset을 Dataset/DataLoader로 읽을 수 있음
- Train/Test leakage가 없는지 설명할 수 있음

---

# 14. STEP 5 — R(2+1)D-18 Fine-tuning

모델:

```python
from torchvision.models.video import (
    r2plus1d_18,
    R2Plus1D_18_Weights
)

weights = R2Plus1D_18_Weights.DEFAULT

model = r2plus1d_18(weights=weights)
```

마지막 classifier를:

```text
Kinetics 400 classes
```

에서:

```text
Normal
Accident
```

두 개로 바꾼다.

개념:

```python
import torch.nn as nn

in_features = model.fc.in_features
model.fc = nn.Linear(in_features, 2)
```

---

## 첫 학습

처음에는 Backbone 대부분을 freeze한다.

```text
Pretrained Video Feature Extractor
                ↓
        새로운 FC Layer
                ↓
         Normal / Accident
```

목적:

> 좋은 성능이 아니라 학습 파이프라인이 정상 동작하는지 확인

첫 번째 실행에서 확인할 것:

```text
Loss가 감소하는가?
Validation F1이 계산되는가?
best.pt가 저장되는가?
```

---

## 그 다음 Fine-tuning

Baseline이 성공하면 마지막 block 일부를 풀고 다시 학습한다.

예:

```text
Run A
Classifier only

Run B
Last block + Classifier
```

비교:

```text
Validation F1
Recall
Training Time
```

---

## 시작 Hyperparameter

절대값이 아니라 **첫 실험 시작값**이다.

```text
clip length = 16
batch size = 4~8
optimizer = AdamW

classifier only:
learning rate = 1e-3

partial fine-tuning:
learning rate = 1e-4
```

GPU memory에 따라 batch size를 줄인다.

---

# 15. 사고 모델 평가

Accuracy 하나만 보면 안 된다.

사고 탐지에서 가장 중요한 것은:

```text
실제 사고를 놓치는가?
정상 상황을 사고라고 자주 오탐하는가?
얼마나 빨리 탐지하는가?
```

따라서 최소:

```text
Precision
Recall
F1
```

을 기록한다.

추가:

```text
PR-AUC
```

를 기록한다.

---

# 16. Event-level 평가

실제 시스템에서는 frame 하나의 정답보다 이벤트 탐지가 중요하다.

예:

```text
실제 사고 시작: 20.0초
최초 Alert:      20.4초
```

Detection Delay:

```text
+0.4초
```

또한:

```text
False Alarms / minute
```

를 측정한다.

최종적으로 중요한 지표:

```text
1. Event Recall
2. False Alarms / minute
3. Detection Delay
4. FPS
```

---

# 17. STEP 6 — End-to-End 통합

이제 처음으로 하나로 연결한다.

의사 코드:

```python
frame_buffer = deque(maxlen=16)

for frame in stream:

    # 1. Detection + Tracking
    tracks = yolo_tracker(frame)

    # 2. Video frame buffer
    frame_buffer.append(frame)

    # 3. Accident classifier
    if len(frame_buffer) == 16:
        accident_score = classifier(frame_buffer)

    # 4. Event decision
    if accident_score >= threshold:
        alert = True

    # 5. Draw
    draw_boxes()
    draw_track_ids()
    draw_accident_score()
    draw_alert()

    # 6. Save
    write_video()
    write_event()
```

---

# 18. 사고 모델을 매 frame 실행할 필요는 없다

10 FPS 입력에서:

```text
Frame 1
Frame 2
Frame 3
Frame 4
```

모두 R(2+1)D를 실행하면 연산량이 크다.

첫 baseline에서도:

```text
4 frame마다 한 번
```

정도로 실행해도 된다.

예:

```python
if frame_id % 4 == 0:
    accident_score = classifier(frame_buffer)
```

10 FPS라면 약:

```text
0.4초마다 사고 판단
```

하게 된다.

---

# 19. Alert 안정화

모델 score가:

```text
0.45
0.62
0.48
0.81
0.52
```

처럼 흔들릴 수 있다.

한 번 threshold를 넘었다고 바로 사고라고 하지 않는다.

첫 MVP에서는 간단하게:

```text
2회 연속 threshold 초과
→ Alert
```

를 사용한다.

그리고 사고 Alert 이후 짧은 cooldown을 둔다.

---

# 20. STEP 6 결과물 — 가장 중요한 결과

최종:

```text
outputs/final_demo.mp4
outputs/events.json
outputs/metrics.json
```

영상 화면 예:

```text
┌──────────────────────────────────────┐
│ CCTV                                 │
│                                      │
│  Car #12 ─────→                      │
│                 ←──── Car #7         │
│                                      │
│ Objects: 7                           │
│ Accident score: 0.87                 │
│                                      │
│ ⚠ ACCIDENT EVENT DETECTED            │
└──────────────────────────────────────┘
```

`events.json`:

```json
[
  {
    "timestamp": 18.4,
    "frame": 184,
    "accident_score": 0.87,
    "event": "ACCIDENT"
  }
]
```

---

# 21. 이 순간 MVP는 완성이다

여기까지 오면:

```text
CCTV
 ↓
Object Detection
 ↓
Tracking
 ↓
Temporal Video Model
 ↓
Accident Event Detection
 ↓
Alert
```

라는 전체 파이프라인이 한 번에 동작한다.

### 이 시점에서는 성능이 낮아도 괜찮다.

먼저 해야 할 것은:

```text
Baseline 측정
```

이다.

---

# 22. Baseline에서 반드시 저장할 숫자

```text
Object Detector FPS
Whole Pipeline FPS

R(2+1)D inference latency

Precision
Recall
F1

False Alarms / minute
Detection Delay
```

예:

```text
Experiment: baseline-v1

F1:                 0.xx
Recall:             0.xx
False Alarm/min:    x.xx
Detection Delay:    x.xx sec
Pipeline FPS:       x.xx
```

실제 측정값만 기록한다.

---

# 23. 다음 목표 1 — 더 빠르게

이제부터 한 번에 하나씩 바꾼다.

---

## SPEED-1. Accident model 호출 주기 조정

Before:

```text
2 frame마다 추론
```

After:

```text
4 frame마다 추론
```

측정:

```text
FPS 변화
Recall 변화
Detection Delay 변화
```

---

## SPEED-2. YOLO 입력 크기

Before:

```text
imgsz=640
```

After:

```text
imgsz=512
```

측정:

```text
YOLO latency
전체 FPS
작은 객체 누락 정도
```

---

## SPEED-3. Tracking 기반 Candidate Gate

여기서 처음으로 YOLO/ByteTrack과 사고 모델을 적극적으로 연결한다.

예:

```text
차량이 0~1대
→ 사고 모델 실행 생략

2대 이상 차량 존재
→ 사고 모델 실행 후보

차량 간 접근 또는 급격한 trajectory 변화
→ 사고 모델 실행
```

구조:

```text
YOLO
 ↓
ByteTrack
 ↓
Collision Candidate Gate
 │
 ├─ False → R(2+1)D 실행 안 함
 │
 └─ True
      ↓
   R(2+1)D
```

목표:

```text
Video classifier 호출 횟수 감소
→ 전체 처리속도 증가
```

주의:

이 Gate가 너무 강하면 사고를 놓친다.

따라서 반드시:

```text
속도 증가
vs
Recall 감소
```

를 같이 측정한다.

---

## SPEED-4. M1 Pro 최종 최적화

Baseline 완료 이후에만 시도:

```text
CoreML export
모델 input size 조정
추론 주기 조정
```

최적화 자체보다 Before / After 측정이 중요하다.

---

# 24. 다음 목표 2 — 더 정확하게

속도와 마찬가지로 한 번에 하나씩 변경한다.

---

## ACC-1. Threshold 최적화

처음:

```text
threshold = 0.5
```

라고 임의 설정한다.

하지만 최종 threshold는 Validation Set에서 정한다.

여러 threshold를 테스트:

```text
0.30
0.35
0.40
...
0.80
```

기록:

```text
Precision
Recall
F1
False Alarm/min
```

목적:

> 원하는 Recall / False Positive trade-off를 찾는다.

---

## ACC-2. Hard Negative Mining

모델이 자주 사고라고 잘못 판단하는 정상 장면을 모은다.

예:

```text
급정거
차량이 서로 스쳐 지나감
교차로 차량 밀집
BBox가 화면상 겹침
차량이 갑자기 가려짐
```

이런 False Positive clip을:

```text
Hard Negative
```

로 다시 Training Set에 추가한다.

그리고 재학습한다.

비교:

```text
Before False Alarm/min
After False Alarm/min
```

---

## ACC-3. Fine-tuning 범위 확대

Baseline:

```text
classifier만 학습
```

다음:

```text
마지막 VideoResNet block + classifier
```

비교:

```text
F1
Recall
Training Time
Overfitting 여부
```

---

## ACC-4. Tracking Motion Feature 활용

차량 ID를 이미 가지고 있으므로:

```text
center_x
center_y
velocity_x
velocity_y
acceleration
distance between vehicles
IoU
```

를 계산할 수 있다.

이 정보는 "충돌 가능성"을 나타내는 추가 evidence가 된다.

하지만 바로 복잡한 Fusion Network를 만들지 않는다.

첫 실험:

```text
R(2+1)D score
+
motion-based candidate condition
```

으로 False Positive 감소가 가능한지 본다.

---

## ACC-5. 더 큰 YOLO 비교

YOLO26n에서 작은 차량/사람 누락이 심각하다는 근거가 생겼을 때만:

```text
YOLO26n
vs
YOLO26s
```

를 비교한다.

기록:

```text
Detection 품질
FPS
Pipeline latency
```

무조건 큰 모델이 좋은 것이 아니다.

실시간 시스템에서는:

```text
Accuracy
vs
Latency
```

trade-off를 봐야 한다.

---

# 25. 실험 기록 방법

`experiments/results.csv`

```csv
exp_id,change,precision,recall,f1,false_alarm_per_min,delay_sec,fps,p95_latency_ms,notes
baseline,none,,,,,,,,
speed_01,classifier_every_4_frames,,,,,,,,
speed_02,yolo_512,,,,,,,,
speed_03,tracking_gate,,,,,,,,
acc_01,threshold_tuning,,,,,,,,
acc_02,hard_negative,,,,,,,,
acc_03,partial_finetune,,,,,,,,
```

원칙:

```text
한 실험에서 한 가지 핵심 변수만 바꾼다.
```

그래야:

> 왜 성능이 변했는가?

를 설명할 수 있다.

---

# 26. 3일 일정

## DAY 1 — 객체 탐지 + Tracking

목표:

```text
ACCIDENT 확인
 ↓
YOLO26n
 ↓
ByteTrack
 ↓
영상 출력
```

완료 파일:

```text
detection_demo.mp4
tracking_demo.mp4
tracks.json
```

오늘의 학습 개념:

```text
Object Detection
Bounding Box
Confidence
IoU
Tracking
Track ID
```

---

## DAY 2 — 사고 모델

목표:

```text
ACCIDENT annotation 분석
 ↓
16-frame clip dataset 생성
 ↓
R(2+1)D-18 Fine-tuning
 ↓
Validation
```

완료 파일:

```text
train.csv
val.csv
test.csv
best.pt
metrics.json
```

오늘의 학습 개념:

```text
Video Classification
Temporal Feature
Transfer Learning
Fine-tuning
Data Leakage
Precision / Recall / F1
```

---

## DAY 3 — End-to-End

목표:

```text
Frame Stream
 ↓
YOLO
 ↓
ByteTrack
 ↓
16-frame Buffer
 ↓
R(2+1)D
 ↓
Alert
```

완료 파일:

```text
final_demo.mp4
events.json
baseline_metrics.json
```

시간이 남으면 딱 두 실험만 한다.

```text
1. 사고 모델 호출 주기 변경
2. Threshold tuning
```

---

# 27. 3일 동안 하지 않을 것

현재 목표에서는 아래를 하지 않는다.

```text
YOLO 처음부터 학습
Video Transformer 새로 구현
Optical Flow 모델 추가
Multi-modal 모델
VLM
Agent
사고 중증도
사고 원인 분석
몇 중 추돌 판별
차량 속도 km/h 정확 추정
Perspective calibration
복잡한 Web Service
Kafka
Redis
실제 CCTV RTSP 연결
```

이것들은 End-to-End 이후의 문제다.

---

# 27-1. ACCIDENT 데이터의 화질에 대한 프로젝트 원칙

ACCIDENT의 실제 CCTV는 화질이 균일하지 않다.

```text
원거리 차량
낮은 해상도
압축 artifact
motion blur
저조도
가림
```

때문에 YOLO / Tracking 성능이 흔들릴 수 있다.

이것을 데이터의 결함으로만 보지 않는다.

오히려 다음 실험 주제로 사용한다.

```text
Synthetic / 비교적 선명한 영상
        ↓
Baseline

Real / 저화질 CCTV
        ↓
성능 저하 측정

        ↓
어디서 깨지는지 분석

YOLO Detection?
Tracking?
Accident Classifier?
```

즉 첫 목표는:

```text
일부 영상에서 End-to-End 성공
```

그 다음 목표는:

```text
저화질 Real CCTV에서도 얼마나 버티는지 측정
```

이다.

---

# 28. 첫 MVP 이후 확장 순서

아래 순서를 지킨다.

```text
Level 1
한 번의 전체 Flow 성공

        ↓

Level 2
Baseline 성능 측정

        ↓

Level 3
속도 개선

        ↓

Level 4
정확도 개선

        ↓

Level 5
Collision-specific reasoning

        ↓

Level 6
사고 유형 분류

        ↓

Level 7
실제 RTSP CCTV Stream

        ↓

Level 8
서비스 / 관제 Dashboard
```

---

# 29. 이후 Collision Detection을 더 명확히 분리하고 싶다면

MVP에서는:

```text
R(2+1)D
→ Accident Event
```

만 판단한다.

다음 단계에서는:

```text
YOLO + ByteTrack
      ↓
Interaction Detection
      ↓
두 객체가 실제 충돌 후보인가?
```

를 추가한다.

예:

```text
Car #12 trajectory ──────┐
                         ├─ Intersection
Car #7 trajectory  ──────┘
                         ↓
                 abrupt velocity change
                         ↓
                collision candidate
```

그리고:

```text
collision candidate
+
R(2+1)D accident score

→ Final Accident Event
```

로 만들 수 있다.

이때부터:

```text
객체 상호작용 기반 충돌 탐지
```

와:

```text
영상 전체 Context 기반 사고 탐지
```

를 분리해서 비교할 수 있다.

이 구조는 좋은 고도화 주제가 된다.

---

# 30. 포트폴리오에서 보여줘야 하는 것

최종 발표에서는 모델 이름을 많이 나열하는 것이 목적이 아니다.

아래 흐름을 보여준다.

## 문제

```text
단일 Frame 객체 탐지만으로는 사고 여부를 판단하기 어렵다.
```

## 분석

```text
사고는 시간에 따른 객체 움직임 변화가 중요하다.
```

## 첫 구현

```text
YOLO26n
+
ByteTrack
+
R(2+1)D-18
```

## Baseline

```text
F1
Recall
False Alarm/min
Detection Delay
FPS
```

## 문제 발견

예:

```text
혼잡한 교차로에서 False Positive가 많았다.
```

## 개선

예:

```text
Hard Negative Mining
Tracking Candidate Gate
Threshold Calibration
```

## 결과

```text
Before
vs
After
```

이 구조가 포트폴리오 핵심이다.

---

# 31. 최종 성공 기준

## 반드시 달성

- [ ] ACCIDENT 데이터를 직접 읽었다.
- [ ] 사고 시점/구간 annotation을 직접 확인했다.
- [ ] YOLO26n으로 사람/차량을 탐지했다.
- [ ] ByteTrack으로 ID를 유지했다.
- [ ] R(2+1)D-18을 Normal/Accident로 Fine-tuning했다.
- [ ] 실시간처럼 frame을 순차 입력했다.
- [ ] 화면에 객체 Box + ID + Accident Score를 표시했다.
- [ ] 사고 Alert가 실제로 발생했다.
- [ ] `final_demo.mp4`를 만들었다.
- [ ] Precision / Recall / F1을 계산했다.
- [ ] 전체 Pipeline FPS를 계산했다.

---

## 그 다음 달성

- [ ] False Alarms / minute 측정
- [ ] Detection Delay 측정
- [ ] Threshold tuning
- [ ] Hard Negative Mining
- [ ] 사고 모델 호출 빈도 최적화
- [ ] Tracking Candidate Gate
- [ ] Before / After 비교

---

# 32. 프로젝트를 진행하면서 스스로 계속 물어볼 질문

### 데이터

```text
이 label은 정확히 무엇을 의미하는가?
Train/Test에 유사 frame이 섞이지 않았는가?
```

### 객체 탐지

```text
왜 YOLO를 직접 학습하지 않았는가?
사전학습 모델로 충분한가?
작은 객체를 얼마나 놓치는가?
```

### Tracking

```text
왜 track_id가 필요한가?
ID Switch가 사고 판단에 어떤 영향을 주는가?
```

### 사고 모델

```text
왜 이미지 모델이 아니라 Video Model인가?
왜 16 frame인가?
미래 frame을 사용하고 있지는 않은가?
```

### 평가

```text
Accuracy가 높은데 Recall은 낮지 않은가?
False Positive가 실제 운영에서는 얼마나 발생하는가?
```

### 실시간

```text
현재 처리 FPS는 몇인가?
입력 FPS 이상으로 처리 가능한가?
병목은 YOLO인가 R(2+1)D인가?
```

### 개선

```text
이번 변경 때문에 실제로 무엇이 좋아졌는가?
그것을 숫자로 증명할 수 있는가?
```

---

# 33. 이 프로젝트에서 가장 중요한 원칙

```text
완벽한 모델
    X

한 번에 끝까지 동작하는 Pipeline
    O
```

첫 목표:

```text
ACCIDENT
 ↓
YOLO
 ↓
ByteTrack
 ↓
R(2+1)D
 ↓
ACCIDENT ALERT
 ↓
VIDEO + JSON
```

이게 동작하면 그 순간부터 연구가 시작된다.

그 다음부터:

```text
왜 느린가?
왜 놓치는가?
왜 오탐하는가?
```

를 하나씩 측정하고 개선한다.

**첫 3일의 성공 기준은 높은 정확도가 아니라, 측정 가능한 End-to-End Baseline을 만드는 것이다.**
