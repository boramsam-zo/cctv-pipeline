# 서울 S3 버킷 — Terraform

Mac에서 실행한다. Runpod/GPU는 필요 없다. 서울(ap-northeast-2)에 비공개 버킷 1개와 공개차단/ACL비활성/SSE-S3 암호화 설정을 만든다. 버킷 이름은 `boram3-cctv-evidence-` 뒤에 Terraform이 고유 접미사를 붙인다. IAM 계정·액세스 키는 만들지 않으며, Runpod 업로드용 정책 JSON만 출력한다.

## 1. AWS 인증

이미 받은 계정의 AWS CLI 프로필이 있다면 그 프로필을 사용한다. 콘솔 로그인 비밀번호와 API 자격 증명은 다르다. 키 기반 IAM 사용자라면 아래 첫 명령의 프롬프트에 직접 입력하고, 인증 값은 채팅/코드에 쓰지 않는다. 팀이 SSO/임시 인증을 제공했다면 그 방식으로 로그인한다.

```bash
aws configure --profile cctv
export AWS_PROFILE=cctv
aws sts get-caller-identity
```

리전 질문에는 `ap-northeast-2`를 입력한다. 기존 정상 프로필이 있으면 `aws configure`는 생략하고 그 이름을 export한다. STS 출력은 본인이 계정 확인용으로 보고 채팅에 공유할 필요 없다.

## 2. 계획 확인 후 생성

저장소 루트에서 다음 폴더로 이동한다(현재 터미널이 Runpod이면 Mac 터미널로 전환).

```bash
cd infra/terraform/evidence-s3
terraform init
terraform fmt -check
terraform validate
terraform plan -out=evidence.tfplan
```

plan에는 S3 버킷과 관련 설정 총4개 생성이 예상된다. 계정과 서울 리전이 맞는지 확인한 뒤 해당 계획을 적용한다.

```bash
terraform apply evidence.tfplan
terraform output runpod_environment
```

현재 사용자의 버킷 생성 권한 외에 PublicAccessBlock/OwnershipControls/Encryption/Tagging 설정과 provider 조회 권한도 필요하다. AccessDenied가 나면 실패한 API 권한을 담당자에게 요청한다. 버킷 생성만 허용된 권한으로 모든 설정이 된다고 보장할 수 없다.

Terraform state는 이 폴더에 로컬 저장된다. 이 폴더에서 계속 관리하고 state를 보존한다. Git에서는 state/plan을 제외하고 `.terraform.lock.hcl`은 버전관리 대상으로 둔다. 버킷 삭제 보호를 설정했고 자동 파일 삭제/lifecycle 만료는 추가하지 않았다.

## 3. Runpod 업로드 권한

```bash
terraform output -raw runpod_upload_policy_json
```

이 JSON은 해당 버킷의 `cctv-evidence/*`에 PutObject와 GetObject를 허용한다. 기존 업로더가 HEAD 검증을 하므로 GetObject도 필요하다. 사용할 Runpod 인증 주체에 이 권한이 이미 있으면 추가할 필요 없다. IAM 정책 수정 권한이 없다면 계정을 제공한 담당자에게 전달한다. 버킷 생성 권한과 IAM 변경 권한은 별개다.

## 4. Runpod 연결

`runpod_environment` 출력의4개 설정과 업로드용 AWS 자격 증명을 Runpod 실행 환경에 적용한다. 자격 증명을 Terraform 변수로 전달하거나 Terraform으로 키를 생성하지 않는다. Pod 재시작으로 임시 파일이 사라질 수 있으므로 현재 원본/생성근거는 먼저 보관한다.

이미 CPU 환경에서 클립을 만들었다면 Runpod에서 boto3만 추가하고 기존 파일을 업로드한다:

```bash
cd /workspace/cctv-evidence-release/cctv-runpod-a4000
.venv-cpu/bin/python -m pip install boto3==1.43.103
.venv-cpu/bin/python storage.py \
  --output /workspace/evidence-fd20cca0-v1 \
  --job fd20cca0be53483696e6704e7c985b84
```

실행 전에 `EVIDENCE_STORAGE=s3`, 버킷/리전/prefix와 AWS 인증이 해당 터미널에 적용되어 있어야 한다. 이 명령은 GPU 재추론 없이 업로드하고 `s3_receipt.json`을 만든다. 그 manifest_key가 BE의 회수 시작점이다. 자세한 계약은 [S3_SETUP.md](../../../apps/runpod_worker/S3_SETUP.md) 참조.

공식 참고: [AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs), [S3 버킷](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket), [공개 차단](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_public_access_block).
