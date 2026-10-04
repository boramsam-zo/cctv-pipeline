# 정상 개발자료 후보 조사 — 2026-09-25

목적: 기존631의 정상 오탐을 학습에 쓰지 않은 출처에서 확인한다. 자료 선정 조사이며 다운로드·영상검수·라벨확정·모델실행은 아직 없다. 기존 개발11은 완료, 최대100 확보는 미완료다.

## 우선 후보: Bellevue Traffic Video Dataset
공식: https://github.com/City-of-Bellevue/TrafficVideoDataset
공식 설명은 교차로5곳, 약101시간,1280×720,30Hz,2017년9월 실제 pole-mounted traffic cameras,연구용 제공이다. 이번 개발 실험에 맞는 영상 형태로 판단했다. 상업 서비스 이용 허가까지 확인한 것은 아니다. 사고/비사고 시간 주석이나 전체 무사고 보증은 공식 페이지에서 확인되지 않았다. 정상 후보로만 취급하고 사용 구간을 검수한다.

서로 다른 카메라 후보(공식 페이지 연결):
- Bellevue_150th_Eastgate: https://drive.google.com/drive/folders/1cR1VwoAvEjFLRaUzeYph-bxx4LoM6pOH
- Bellevue_150th_Newport: https://drive.google.com/drive/folders/1irB6XKu2iM3BSJ2AEYH4kJl9nfG9j-yy
- Bellevue_150th_SE38th: https://drive.google.com/drive/folders/1IN6kwywddO3B3uHyC5S18vqf0KEWToJ_

웹 도구에서 폴더 연결은 확인했으나 내부 파일명/용량/다운로드 가능 여부는 미확인이다. 전체101시간을 다운로드하지 않는다. 다음은 이3폴더의 파일명·용량·길이를 확인하고 카메라당 원본1개 후보를 정하는 것. 정상 구간은 예측 점수가 아니라 장면 확인으로 정한다. 3원본에서 여러 구간을 추출해도 독립 원본3개 이상이라고 표현하지 않는다. 기존 자료와 원본/재편집 중복 확인 후 개발 전용으로 고정한다.

사용자 다운로드가 필요할 때: 위 공식 페이지 Download에서 선택된 파일만 받아 T9의 새 external/bellevue 폴더에 원본 이름을 유지하고, Drive boram3-cctv-evaluation/external/bellevue에도 같은 카메라별 폴더로 둔다. 경로는 제안이며 아직 폴더 생성/업로드된 사실이 아니다. 파일 목록·용량 확인 전 대용량 전체 다운로드를 안내하지 않는다.

## 보류 후보
- GRAM-RTM: https://gram.web.uah.es/data/datasets/rtm/index.html — 이번 공식 URL 조회403. 논문 링크는 존재하지만 현재 다운로드·이용조건 확인 실패. 자료 자체 부재로 단정하지 않는다.
- MAVD-traffic: https://zenodo.org/records/3338727 — 네 위치의 오디오/동기 영상,30fps 촬영 설명. 주석은 교통 소리용으로 사고 정답이 아니다. 표시된 v0.1 video_validate.zip38.1MB이나 newer version 안내가 있어 최신 버전·이용조건·CCTV시점 적합성 확인 전 선정하지 않음.
- QMUL: https://personal.ie.cuhk.edu.hk/~ccloy/downloads_qmul_junction.html — 연구용/비상업, 기존 학습 원본이므로 새 독립 정상 자료로 재활용하지 않는다.

## 다음 실행 순서
1. Bellevue 공식 연결 폴더의 파일명/용량 확인.
2. 서로 다른 카메라의 제한된 원본을 확보하고 SHA·출처·이용근거·기존중복을 기록.
3. 정상 구간 확인 → 개발 전용 목록 고정 → 기존 전처리 → 세 모델 동일 평가.
4. 기존 사고 평가 결과와 별도 분모로 보고. 정상만의 평가에서 사고 Recall/F1 개선을 주장하지 않음. 운영 성능은 긴 영상 오탐 빈도와 사고 탐지 지연도 별도로 필요.


## 2026-09-25 실제 Drive 목록 확인
공개 폴더를 브라우저로 열어 파일명과 표시용량을 확인했다. 다음은 같은 날짜18시대 후보이며 전체목록의 최솟값을 찾거나 낮/밤을 육안 확정한 것은 아니다.

| 카메라 | 파일명 | Drive 표시용량 |
|---|---|---|
| Eastgate | Bellevue_150th_Eastgate__2017-09-10_18-08-24.mp4 | 353.5MB |
| Newport | Bellevue_150th_Newport__2017-09-10_18-08-24.mp4 | 692MB |
| SE38th | Bellevue_150th_SE38th__2017-09-10_18-08-24.mp4 | 477.6MB |

합계 표시용량1,523.1MB(약1.52GB). 길이/FPS/실제바이트/SHA/무사고 여부는 파일검사 전 미확인. 3카메라 후보이지 독립사건/정상정답 확정3개가 아니다. 다운로드·업로드·디코딩·추론은 하지 않았다. 최초 도입은 Eastgate 원본1개로 접근/길이/입력 적합성을 확인하고 적합하면 다른2카메라로 넓히는 범위를 제안한다. 이는 학습량을 조금씩 늘리는 실험이 아니라 데이터 연결 확인이다. 사용자 이전 외부 정상자료 사용 의사는 유지한다.

## 2026-09-25 — 평가 원본·정답 접수: Bellevue Eastgate 1원본
기존 계획의 접수 TODO 실행. 공개 Bellevue Eastgate 원본1개 370,717,286bytes 확보, SHA256 fc683bda631807c42e801f51ad10519a30477e37bf708c070dfbafc1d9579974. 약3599.985초/1280×720/H264/평균29.9818FPS. 디코딩 전 0–2/30–32/60–62초를 개발 전용으로 정하고 3입력(각16장224) 준비. 3개 학습proposal의 파일명/출처 일치0; 전체 시각적 중복·사건/카메라 독립성 증명은 아님. 원본전체 디코딩 미실행.
접수 결과: 사용가능 후보3구간/6초/1원본/1카메라, 정답 확정0. 제공 사고정답 없음. 에이전트 contact sheet 관찰에서 뚜렷한 충돌·전복이 보이지 않으나 사람검수 정상으로 승격하지 않음. 검수용 원본해상도 짧은 영상3개(61/60/60프레임), 원본PTS 기록과 후보목록 SHA 보존. 사용자에게 세 구간의 정상/충돌·전복/판단 어려움 확인만 요청. 전체1시간 정상 가정 없음. 다른2카메라 추가 다운로드 없음.
근거: artifacts/data-review/bellevue-intake-20260925-01/{intake-plan.json,manifest.json,evaluation-roster.json,evaluation-roster.sha256,review-clips.json,report.html,inspect_source.py,finalize_intake.py}. 기존631/판정기준 유지, 학습·추론·Drive업로드 없음. 다음 하나:3구간 정답 확인 후 개발 평가용 정답 버전 확정. 기존 후보목록은 덮어쓰지 않고 정답 확인 이력을 별도 연결한다.
