# Runpod 근거 생성·S3 연결 — 2026-09-27

## 2026-09-29: 새 Pod 복원·시작 — 운영자용 최신 절차

새 Pod에 파일이 없는 경우 이 절차를 사용한다. 기존 설치가 남아 있다면 먼저 확인하고, 실행 중인 API 위에 ZIP이나 토큰을 덮어쓰지 않는다. GPU 운영은 사용자가 맡고 동료는 BE에서 API를 호출한다. 아래 비공개 경로·토큰은 팀 공개 저장소에 포함하지 않는다.

### 1. Mac에서 보관할 파일

프로젝트 루트를 기준으로 다음 파일이 있다.

| 파일 | 역할 |
|---|---|
| `artifacts/runpod/a4000-evidence-20260927-02/cctv-runpod-a4000.zip` | 코드·X3D/YOLO 가중치·설치 스크립트, 50,893,136bytes |
| `artifacts/be-worker/queue-local-v1/secrets-pod-3whxkh444xl3qc-v1/runpod_worker_token` | 이전에 Mac으로 받아 둔 분석 API 토큰. 새 Pod의 `.api-token`으로 복원 |

2026-09-29 확인: ZIP CRC와 manifest의 25개 파일 크기/SHA256이 일치하고, 현재 실행 소스와도 일치한다. 바뀐 것은 이 운영 안내다. 토큰은 44bytes/0600이며 값은 출력하지 않았다. 폴더 이름의 이전 Pod ID는 보관 위치의 이름일 뿐, 토큰을 그 Pod에 묶는 기능은 없다. 이후 별도로 토큰을 교체했다면 그 최신 토큰이 기준이다.

`fd20cca0-recovery.zip`은 과거 결과 JSON/로그이며 설치용이 아니다. 팀 공유용 `cctv-pipeline-source-v5.zip`에는 가중치가 없어 이번 설치에는 위 Runpod 전용 ZIP을 쓴다. 원본 ZIP을 그대로 보존하며, ZIP 안 문서를 수동으로 교체하면 무결성 검사가 실패할 수 있다.

### 2. 새 GPU Pod에 설치 — Runpod의 Jupyter Terminal

현재 설치 코드는 CUDA GPU와 PyTorch 2.8.0 / torchvision 0.23.0을 요구한다. 이전에 사용한 이미지 식별자는 `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`다. 새 GPU의 메모리·드라이버·실제 추론 가능 여부는 새 환경에서 확인한다. `a4000` 파일명 때문에 A4000만 가능한 것은 아니며, RTX 2000 Ada에서도 이전 실행 이력이 있다.

새 Pod의 Connect → Jupyter Notebook에서 위 **ZIP과 토큰 파일 두 개**를 `/workspace`에 업로드한다. 토큰 파일명은 `runpod_worker_token`으로 유지한다. 이 방법은 SSH IP를 명령에 넣지 않는다. Jupyter에서 File → New → Terminal을 열고 실행한다.

```bash
cd /workspace &&
test ! -e /workspace/cctv-runpod-a4000 &&
python3 -m zipfile -e cctv-runpod-a4000.zip /workspace &&
cd /workspace/cctv-runpod-a4000 &&
python3 verify_bundle.py &&
bash setup.sh
```

설치 폴더가 이미 있으면 위 명령은 해제를 하지 않고 멈춘다. 기존 폴더를 삭제하지 말고 설치 완료 여부부터 확인한다. `Bundle verified: 25 files; models not loaded`와 `Setup finished`를 확인한다. setup은 필요한 패키지를 설치하며 새 영상 분석은 하지 않는다. ZIP에는 설치된 `.venv`가 없으므로 빈 Pod에서는 설치를 다시 한다.

#### `.venv/bin/python: No such file or directory`가 나온 경우

`start.sh`는 설치된 가상환경의 Python을 사용한다. 이 오류는 그 실행 파일 또는 심볼릭 링크 대상이 없어서 API 시작 전에 멈췄다는 뜻이다. ZIP 해제만으로 `.venv`가 생기지는 않는다. 특히 위 명령의 `test ! -e`는 폴더가 이미 있으면 뒤의 `bash setup.sh`도 실행하지 않는다. 설치 중 오류/재시작에 따른 유실 가능성도 있어 원인은 설치 출력으로 확인한다.

이미 코드 폴더가 있으므로 ZIP을 다시 풀거나 폴더를 지우지 말고, **현재 Runpod Jupyter Terminal**에서 설치 단계를 직접 실행한다.

```bash
cd /workspace/cctv-runpod-a4000 && bash setup.sh
```

`Setup finished. Start the API with: bash start.sh`가 나온 뒤 같은 터미널에서 `bash start.sh`를 실행한다. setup은 `.api-token`과 기존 작업 결과를 삭제하지 않는다. CUDA/PyTorch 버전 또는 다른 설치 오류로 멈추면 start를 반복하지 말고 그 첫 오류를 확인한다. 새 터미널이라면 서버 시작 전에 아래 S3/AWS 환경변수도 다시 설정한다. 이 절차는 사용자 실행 안내이며 실제 새 Pod 설치 성공은 아직 확인하지 않았다.

#### `AssertionError: CUDA GPU is unavailable`가 나온 경우

사용자 후속 설치 출력에서 bundle25파일 검증 뒤 이 오류가 발생했다. 현행 setup.sh 기준 PyTorch/torchvision 버전 검사는 통과했지만 `torch.cuda.is_available()`이 false다. 가상환경 생성보다 앞에서 중단되므로 이 실행으로 `.venv`가 생성되지 않은 것이 설명된다. CPU 실행/실제 GPU 미할당, CPU용 PyTorch 빌드, 드라이버·컨테이너 GPU 노출 문제는 아직 구분되지 않았다.

먼저 **같은 Pod의 Runpod Jupyter Terminal**에서 다음을 실행한다. Python 명령은 모델을 로드하거나 영상을 추론하지 않는다.

```bash
nvidia-smi --query-gpu=name,driver_version --format=csv
python3 - <<'PY'
import torch
print("PyTorch:", torch.__version__)
print("PyTorch CUDA:", torch.version.cuda)
print("CUDA 사용 가능:", torch.cuda.is_available())
print("인식된 GPU 개수:", torch.cuda.device_count())
PY
```

동시에 같은 Pod의 Details에서 GPU 모델과 개수를 확인한다. `nvidia-smi` 명령이 없다는 사실만으로 CPU Pod라고 확정하지 않는다. `torch.version.cuda`가 None이면 CPU용 PyTorch 빌드다. GPU가 표시되지만 torch가 인식하지 못하면 드라이버/컨테이너/설정 진단이 이어져야 한다. 현재 CUDA 필수 검사를 제거하거나 CPU 추론으로 우회하지 않는다. Runpod의 CPU 시작 선택은 파일 접근을 위한 GPU 없는 실행일 수 있다: https://docs.runpod.io/pods/troubleshooting/zero-gpus

### 3. 기존 토큰 복원 — 같은 Runpod Terminal, API 시작 전

```bash
cd /workspace/cctv-runpod-a4000 &&
test -s /workspace/runpod_worker_token &&
test ! -e .api-token &&
install -m 600 /workspace/runpod_worker_token .api-token &&
rm -- /workspace/runpod_worker_token
```

위 `rm`은 복원이 성공한 후 Jupyter 업로드용 사본 하나만 정리한다. Mac 원본은 유지된다. `.api-token`이 이미 있으면 중단하므로 기존 서버/토큰 상태를 먼저 확인한다. `start.sh`는 기존 `.api-token`이 있으면 그대로 쓰고, 없을 때만 새 토큰을 만든다. 이 토큰은 Runpod 계정 관리 API 키와 별개다.

### 4. S3 설정·AWS 인증·서버 시작 — 같은 Runpod Terminal

```bash
export EVIDENCE_STORAGE=s3
export S3_BUCKET=boram3-cctv-evidence-80bee9db7c3bca316ef395375a
export AWS_DEFAULT_REGION=ap-northeast-2
export S3_PREFIX=cctv-evidence
```

Runpod용 AWS 자격 증명을 아래 질문에 입력한다. 입력값은 표시되지 않는다. 각 질문에 답한 뒤 다음 명령을 진행한다. access key와 secret key는 같은 발급 쌍이어야 하며, 임시 키일 때는 session token도 함께 넣는다. 필요한 권한은 기존 버킷의 `cctv-evidence/*`에 대한 `s3:PutObject`와 `s3:GetObject`다. BE용 읽기 권한만으로는 업로드할 수 없다.

```bash
read -rsp "AWS Access Key ID: " AWS_ACCESS_KEY_ID; echo
read -rsp "AWS Secret Access Key: " AWS_SECRET_ACCESS_KEY; echo
read -rsp "AWS Session Token (없으면 Enter): " AWS_SESSION_TOKEN; echo
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
if [ -n "$AWS_SESSION_TOKEN" ]; then
  export AWS_SESSION_TOKEN
else
  unset AWS_SESSION_TOKEN
fi
```

이어서 같은 터미널에서 API를 한 번만 시작한다.

```bash
cd /workspace/cctv-runpod-a4000 && bash start.sh
```

`Uvicorn running on http://0.0.0.0:8000`이면 서버가 실행 중이다. 이 터미널은 서버 실행용으로 둔다. 별도 터미널의 `export`는 이미 실행 중인 API에 반영되지 않는다. `Only one API process may use this job directory`가 나오면 기존 프로세스가 있는 것이므로 잠금 파일을 지우지 말고 기존 서버부터 확인한다.

### 5. 확인·새 BE 주소 전달

**Runpod의 다른 Jupyter Terminal**에서:

```bash
cd /workspace/cctv-runpod-a4000 &&
.venv/bin/python client.py health
```

`status: ok`, `storage: s3`를 확인한다. 이는 인증된 API 응답과 S3 모드 설정 확인이며, 새 GPU 추론이나 실제 S3 쓰기 성공을 보증하지 않는다.

BE에는 새 Pod의 **Connect → Port 8000 → HTTP Service** 주소를 전달한다. 형태는 `https://새-Pod-ID-8000.proxy.runpod.net`이다. SSH 접속 IP·포트와 분석 API URL은 별개다. 새 Pod ID이면 URL이 바뀌고, 같은 토큰을 복원했다면 BE의 토큰 파일은 유지한다. S3 버킷·리전·권한도 변경하지 않았다면 그대로 쓴다. 공식 접속 안내: https://docs.runpod.io/pods/connect-to-a-pod

기존 프로젝트의 BE 설정을 준비할 때는 **Mac Terminal**에서 다음을 실행하고, 질문에 새 HTTP Service 주소를 붙여넣는다. 아래는 새 설정 파일만 만들며 작업을 제출하지 않는다. 같은 출력 이름이 이미 있으면 새 이름을 선택한다.

```bash
cd . &&
python3 apps/be_worker/worker.py configure \
  --output artifacts/be-worker/queue-local-v1/input/config-new-pod-v1.json \
  --s3-bucket boram3-cctv-evidence-80bee9db7c3bca316ef395375a \
  --s3-region ap-northeast-2
```

새 영상은 이 설정으로 접수한다. 이미 큐에 넣은 작업은 입력 설정 파일만 바꿔도 목적지가 바뀌지 않는다. `retarget`은 제출 0회인 `prepared` 작업에만 적용한다. 현재 S3 인계가 완료된 기존 사건은 새 Pod로 재제출하지 않고 BE의 기존 S3 근거에서 이어간다. API 토큰을 바꾼 경우에만 BE의 비공개 토큰도 맞춘다.

### 6. 테스트 후 중지·저장소 선택

- 새 작업 접수를 멈추고 모든 대기/실행 작업의 분석·S3 업로드·BE 인계가 끝났는지 확인한 뒤 Pod를 중지한다. API 프로세스만 종료해도 켜진 Pod의 GPU 과금이 멈추는 것은 아니다.
- Mac의 실행 ZIP과 비공개 토큰은 계속 보관한다. S3 업로드 실패 결과·필요 로그가 Pod에만 남았다면 먼저 복구/회수한다. 현재 코드가 원본 영상·설치 환경·토큰을 자동으로 S3에 백업하지는 않는다.
- Mac/EC2의 PostgreSQL·BE spool은 Runpod과 별개다. Pod 재생성만으로 BE DB를 새로 만들지 않는다.
- 간헐적인 시연은 위 ZIP·토큰 복원으로 시작할 수 있다. 설치 시간을 줄이려면 영속 볼륨을 선택할 수 있으며 중지 중에도 저장 비용이 남는다. Volume disk는 Pod 삭제 전까지, Network volume은 Pod와 독립적으로 유지된다. Network volume은 연결 가능한 데이터센터/GPU를 확인하여 새 Pod 생성 시 선택한다.

Container disk는 중지/재시작 시 지워진다. `/workspace`라는 경로 이름만으로 보존을 가정하지 말고 실제로 연결된 저장소를 확인한다. 볼륨이 있어도 다른 이미지/Python/CUDA로 바꿨다면 `.venv` 재사용 가능 여부를 확인해야 한다. 공식 저장소 안내: https://docs.runpod.io/pods/storage/types

아래 2026-09-28 및 번호 1~7은 과거 설치·복구 이력이다. 새 Pod는 위 경로와 절차를 기준으로 한다. 이번에는 로컬 파일/명령만 확인했으며 새 Pod 접속·설치·업로드·GPU/API 실행·볼륨 생성은 하지 않았다.

## 2026-09-28: 빈 새 Pod에 실행 패키지 설치

사용자가 새 Pod `3whxkh444xl3qc`의 Jupyter8888 Ready와 빈 파일 목록을 보고했다. 이전 Pod의 GPU/설치 상태를 새 Pod에 그대로 적용하지 않는다. 아래는 빈 새 Pod에 처음 설치할 때의 경로다.

| Mac의 `artifacts/runpod/a4000-evidence-20260927-02/` 파일 | 용도 |
|---|---|
| `cctv-runpod-a4000.zip` — 50,893,136bytes | 새 Pod에 올릴 실행 패키지. X3D/YOLO 가중치·API·설치·근거 생성/S3 코드 포함 |
| `fd20cca0-recovery.zip` — 3,752bytes | 이전 성공 작업의 JSON4개·로그1개. 실행 코드/모델/영상은 없으며 신규 설치에는 불필요 |

실행 ZIP은 기존 verification.json의 SHA256과 일치했고 내부 manifest25파일의 크기/해시를 확인했다. 원본 ZIP은 수정하지 않았다. Jupyter 파일 브라우저의 `/workspace`에 **cctv-runpod-a4000.zip**을 올리고 **File → New → Terminal**에서 실행한다. 이번 설치 위치는 `/workspace/cctv-runpod-a4000`이다. 이미 이 폴더에 파일이 있다면 해제 전에 기존 설치인지 확인한다.

```bash
cd /workspace &&
python3 -m zipfile -e cctv-runpod-a4000.zip /workspace &&
cd /workspace/cctv-runpod-a4000 &&
python3 verify_bundle.py &&
bash setup.sh
```

`Bundle verified: 25 files; models not loaded`와 `Setup finished` 출력을 확인한다. setup은 GPU/PyTorch 버전 점검과 필요한 패키지 설치를 수행하며 인터넷을 사용한다. 새 GPU 모델 추론은 하지 않는다. 실패 시 설치 완료로 취급하지 말고 오류 출력을 확인한다.

이후에는 아래4번의 **기존 S3 버킷·서울 리전·자격 증명 설정**, 새 설치폴더에서 `bash start.sh`, 별도 Terminal에서 같은 폴더의 `.venv/bin/python client.py health` 순서다. BE 연결 전에 health의 `storage: s3`를 확인한다. ZIP에는 기존 분석 API 토큰/AWS 키가 없으므로 새 Pod에 자동 복원되지 않는다. Pod8000주소와 분석 API 토큰을 BE에 맞추고, 이미 접수한 A의 DB/spool 설정에도 이전 주소가 있는지 확인해야 한다. 단순 input/config.json 수정만으로 기존 작업 목적지가 바뀌지 않는다. 기존 DB·A 작업을 삭제하거나 바로 dispatch하지 않는다.

아래1~2번과6번의 `/workspace/cctv-evidence-release/cctv-runpod-a4000`은 이전 설치 이력이다. 이번 새 Pod에서는 위 `/workspace/cctv-runpod-a4000` 경로를 사용한다.

## 완료 범위와 기본값

원래 X3D/YOLO 코드·모델은 그대로 사용한다. 이후 Runpod CPU가 원본에서 후보 대표 시각 앞 3초/뒤 3초를 H.264 MP4로 인코딩하고, YOLO 대표 시각에 해당하는 원본 PNG 한 장을 만든다. 긴 양성 구간 전체가 아니라 **첫 후보 중심의 6초 문맥**이다. 원본이 짧으면 존재하는 부분만 만들고 부족 여부를 표시한다. 오디오·박스는 넣지 않는다. 3초는 초기 설정이며 정확도 검증으로 정한 값이 아니다.

- `EVIDENCE_PRE_SECONDS`, `EVIDENCE_POST_SECONDS`: 각각 0~10초, 합계 양수, 기본 3.
- 원본 해상도·FPS 유지, H.264 CRF23/fast/CPU 스레드2. 화질/비용 실측은 다음 검증 대상.
- 원본 프레임 PTS와 기존 frame_index/FPS가 일치하는 CFR만 허용. VFR/불연속/PTS 없는 영상은 실패로 분리한다. 첫 디코딩 프레임을 상대시간 0으로 한다.
- 현재는 **전체 MP4 분석 후 파일에서 근거를 생성**한다. 실시간 버퍼/1배속 시연 공급기는 아직 미구현이다.
- `EVIDENCE_STORAGE=local` 기본: ZIP에 클립/PNG/JSON 포함, S3 호출 없음.
- `EVIDENCE_STORAGE=s3`: 모든 결과를 업로드·확인한 후 manifest를 마지막에 저장. Gemini는 호출하지 않으며 BE가 후속 처리한다.

## 1. 새 묶음 준비

기존 `/workspace/cctv-runpod-a4000`과 그 결과는 보존한다. 새 ZIP을 `/workspace/cctv-evidence-release.zip`이라는 이름으로 올린 뒤 Runpod 터미널에서:

```bash
mkdir /workspace/cctv-evidence-release
python3 -m zipfile -e /workspace/cctv-evidence-release.zip /workspace/cctv-evidence-release
cd /workspace/cctv-evidence-release/cctv-runpod-a4000
bash setup.sh
```

기존 PyTorch/CUDA 이미지와 PyAV16.1.0을 사용한다. 시스템 ffmpeg CLI/별도 GPU/볼륨은 추가하지 않는다. boto3는 새 묶음에서 설치하며 설치 결과는 `runtime/environment.freeze.txt`에 기록된다. setup의 CUDA 확인은 모델 추론이 아니다.

## 2. 기존 성공 결과로 먼저 클립 확인 — GPU 재추론 없음

성공 작업의 원본 `/workspace/input.mp4`가 그대로 남아 있어야 한다. 아래는 확인된 기존 job ID를 사용한다. 입력 SHA가 다르면 중단한다.

```bash
.venv/bin/python evidence.py \
  --video /workspace/input.mp4 \
  --input-results /workspace/cctv-runpod-a4000/runtime/jobs/fd20cca0be53483696e6704e7c985b84/output \
  --output /workspace/evidence-fd20cca0-v1
```

기존 output 경로가 다르면 해당 작업의 `manifest.json`이 있는 폴더를 지정한다. 새 output 폴더가 이미 있으면 덮어쓰지 않는다. 이 명령은 **CPU 디코딩·인코딩만** 한다. 기존 총 worker 시간은 이전 추론 기록이며 새 `evidence_seconds`와 합산된 실측이 아니다.

Jupyter 파일 브라우저에서 `/workspace/evidence-fd20cca0-v1/evidence/event_000/clip.mp4`와 `frame.png`를 열거나 다운로드한다. 기존 대표 시각3초라면 기본 클립 범위는0~6초다. 실제 충돌 시각 확정이 아니다.

## 3. AWS 담당자가 준비할 것

1. S3 일반 버킷을 BE의 EC2와 같은 리전에 생성한다. 이름은 전역에서 유일해야 한다.
2. **모든 퍼블릭 액세스 차단 유지**, Object Ownership는 Bucket owner enforced(ACL 비활성), 기본 암호화 SSE-S3 유지.
3. 초기 prefix는 `cctv-evidence/`. 버킷을 Runpod에 마운트하지 않는다.
4. Runpod 전용 자격 증명에는 해당 prefix의 PutObject와 GetObject만 부여한다. GetObject는 업로드 후 HEAD 검증에도 필요하다. BE에는 읽기 권한을 별도로 부여한다.
5. 운영 시 보관 기간은 팀이 정하고 lifecycle로 관리한다. 이번 코드가 파일을 자동 삭제하지는 않는다.

Runpod 전용 IAM 정책 예시(버킷 이름 교체):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject", "s3:GetObject"],
    "Resource": "arn:aws:s3:::YOUR_BUCKET/cctv-evidence/*"
  }]
}
```

현재 코드는 SSE-S3(AES256)를 명시한다. SSE-KMS 필수 버킷은 이 프로필과 다르므로 키/권한과 코드를 함께 조정해야 한다. AWS 계정 루트 키는 사용하지 않는다. 단기 자격 증명을 쓴다면 session token과 만료 시 갱신도 필요하다.

## 4. Runpod 환경변수

Runpod의 환경변수 설정에서 다음을 지정한다. 실제 인증 키를 채팅·코드·결과ZIP에 넣지 않는다.

| 변수 | 값 |
|---|---|
| `EVIDENCE_STORAGE` | `s3` (버킷 준비 전에는 `local`) |
| `S3_BUCKET` | 만든 버킷 이름 |
| `AWS_DEFAULT_REGION` | 버킷 리전, 예: `ap-northeast-2` |
| `S3_PREFIX` | `cctv-evidence` |
| `AWS_ACCESS_KEY_ID` | Runpod용 최소권한 자격 증명 |
| `AWS_SECRET_ACCESS_KEY` | 해당 비밀 키 |
| `AWS_SESSION_TOKEN` | 단기 자격 증명일 때만 |

환경변수는 실행 프로세스 시작 전에 적용한다. UI 변경으로 Pod 재시작이 필요하면 **현재 임시디스크 원본·결과부터 다운로드/백업**한다. 인증 키를 셸 명령에 직접 쓰거나 터미널 출력으로 확인하지 않는다.

## 5. 기존 클립만 S3로 업로드

위 환경변수가 적용된 새 터미널에서:

```bash
cd /workspace/cctv-evidence-release/cctv-runpod-a4000
.venv/bin/python storage.py \
  --output /workspace/evidence-fd20cca0-v1 \
  --job fd20cca0be53483696e6704e7c985b84
```

이 명령은 GPU를 사용하지 않는다. 결과 폴더의 SHA/크기 검증 후 비공개 업로드하며 AWS 요청에 SHA256 체크섬을 보낸다. HEAD로 크기/해시 메타데이터를 확인하고 마지막으로 원격 manifest를 저장한다. 성공하면 `s3_receipt.json`에 bucket/region/manifest_key가 생긴다. 같은 폴더·job으로 실패한 업로드를 재시도할 수 있다. **같은 job에 다른 원본/새 설정 결과를 덮어쓰는 용도로 쓰지 않는다.**

부분 실패 파일은 남을 수 있지만 완료 manifest 없이는 BE가 처리하지 않는다. 기존 API job의 failed 상태를 CLI가 succeeded로 바꾸지는 않는다. 업로드 재시도 성공의 기준은 새 receipt와 원격 manifest다.

## 6. 이후 신규 요청부터 자동 연결

기존 API 터미널의 서버를 Ctrl+C로 종료한 뒤 새 폴더에서 시작한다. 기존 앱 인증 토큰을 유지하려면 새 폴더에 토큰이 아직 없는 상태에서 기존 `.api-token`을 복사하고 chmod600을 적용한다. 토큰을 열어서 출력할 필요는 없다. S3 환경변수는 start.sh 프로세스에 전달되어 있어야 한다.

```bash
cd /workspace/cctv-evidence-release/cctv-runpod-a4000
bash start.sh
```

새 터미널에서 같은 새 폴더의 `client.py health`, `submit`, `status`, `result` 명령을 쓴다. API 경로/인증/원본 MP4 접수 형식은 동일하다. 새 API는 새 폴더의 job 디렉터리를 쓰며 이전 job은 자동 이관하지 않는다.

- local: 분석→클립→ZIP→succeeded, `s3_uploaded=false`.
- s3: 분석→클립→S3→ZIP→succeeded, status의 `s3.manifest_key` 제공.
- S3 실패: `failed / s3_upload_failed`, 기존 분석·클립은 남는다. 전체 영상을 submit하지 말고 위 storage.py로 업로드만 재시도한다.
- 후보 없음: evidence.events 빈 목록, 클립 없음; 결과 JSON/manifest는 저장. 분석 오류와 구분한다.

## 7. BE 담당자에게 넘길 계약

API status의 `job_id`, `input_sha256`, `s3.bucket`, `s3.region`, `s3.manifest_key`를 전달한다. 원격 manifest의 `files`는 상대 파일명→`key/bytes/sha256` 매핑이다. BE는 `evidence.json`의 events와 `vlm_handoffs.json`을 event_id로 연결한다.

- event ID는 작업 내부 ID다. BE 식별키는 `(provider_job_id,event_id)`로 사용한다.
- `clip`, `frame`은 manifest 안의 상대 파일명이다. 그 항목의 S3 key로 실제 파일을 읽는다.
- `source_start_s/source_end_s`: 원본 기준 범위(끝 제외), `candidate_clip_time_s`: 클립 내부 후보 상대 시각.
- `representative_source_time_s`: PNG 실제 프레임 시각. 모델 midpoint와 최대 반 프레임 차이 가능.
- `pre_context_truncated/post_context_truncated`: 전후 문맥 부족.
- PNG 좌표계는 원본과 같고, YOLO는 객체 정보이지 충돌 확정 판정이 아니다.
- BE가 클립을 다시 자르지 않는다. Gemini에 실제 클립과 필요 프레임/객체 정보를 보내고 설명·근거 확인 후 첫 사고 의심 알림을 만든다. Gemini/알림/DB는 이번 변경 밖이다.

로컬 manifest/runtime의 storage는 로컬 분석 산출물 단계 기록이다. 원격 저장 완료 여부는 API status/`s3_receipt.json` 및 원격 manifest로 확인한다.

## 검증 상태

로컬에서는 합성 영상 CPU 인코딩·디코딩/프레임 시각과 픽셀, S3 대역 객체 검사·실패/재시도·완료 순서, API/ZIP 검사를 실행한다. 실제 Runpod 새 버전/사용자 원본 화질/S3 권한·전송/지연은 실행 전이다. 버킷 미생성으로 클라우드 성공을 주장하지 않는다.
