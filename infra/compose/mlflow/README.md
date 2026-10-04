# 기존 EC2의 선택적 MLflow 추적 서비스

이 폴더는 **배포 예제이며 아직 실행하지 않았다.** 새 서버를 만들거나 기존 DB를
변경하지 않는다. MLflow 3.16.1의 별도 SQLite DB와 작은 실행 JSON은 전용 영속
volume에 보관한다. 영상·가중치는 이 volume에 복제하지 않는다. R2는 기존 비공개
S3의 고정 해시 경로, 기존 X3D는 이미지 digest와 경로를 등록 참조로 사용한다.
`baseimage:`는 실행 이미지의 출처 표현이며 MLflow의 범용 모델 로더 주소가 아니다.
실제 가중치 로딩은 기존 Runpod의 모델별 어댑터가 담당한다.

기존 EC2의 마지막 RAM 기록은 약 1.9GiB이고 기존 worker의 메모리 여유가 작았다.
따라서 현재 가용량·기존 서비스 peak를 읽고 후보 상한 384MiB에서 tracker 기동과
실제 metadata 호출을 측정한 뒤 적용해야 한다. 이 상한은 측정된 최소 요구량이 아니다.
여유가 부족하면 기존 서비스를 축소하거나 서버를 증설하지 말고 배포를 보류한다.

`compose.example.yaml`은 host 127.0.0.1:5000에만 포트를 게시한다. 동일 EC2의 BE는
`be-override.example.yaml`의 전용 내부 네트워크로 접근하고, MLflow UI는 기존 SSH
터널을 통해 조회한다. 기존 Compose 네트워크·서비스 제한을 유지하는지 최종 병합
설정을 확인한다. 인터넷 포트 개방, 자동 인증 우회, 별도 유료 자원은 포함하지 않는다.

BE 이미지에는 별도 `apps/be_worker/mlflow_requirements.txt`의 skinny client를
포함해야 한다. 등록은 `register_model_reference`로 명시적으로 수행하고 반환된
실제 숫자 version을 비공개 모델 바인딩 설정에 보관한다. 새 접수 전
`verify_registered_model`이 source·모델 ID·업무 버전·가중치 SHA·계약 SHA를 대조한다.
등록은 가중치를 업로드하거나 정확도·호환성을 검증하는 작업이 아니다.

분석/보고가 끝난 뒤 `execution_record` → `record_execution`을 호출한다. 설정과
결과/보고 식별자는 JSON artifact에, 모델·입력·설정 해시는 params에, 처리 시간만
metrics에 보낸다. 정확도/F1은 이 API에서 허용하지 않으며 `not_evaluated`로 남는다.
기존 개발 검증 지표를 독립 최종 평가로 옮기지 않는다.

등록 CLI는 `scripts/register_mlops_models.py`다. `--tracking-uri`, `--bindings-output`,
`--receipt-dir`, `--s3-bucket`, `--r2-key`, `--r2-checkpoint`를 명시한다. R2 key는
`models/r2plus1d_18/<가중치 SHA256>/candidate-epoch-7.pt`로 제한한다. 선택적
`--r2-version-id`는 기존 객체 버전 확인용이다. 이 명령은 실행 시 두 모델 참조를
실제로 등록하며, 실제 등록 번호를 포함한 별도 바인딩 JSON을 0600으로 생성한다.
기존 바인딩 파일은 내용이 같을 때만 재사용하고 덮어쓰지 않는다.

CLI는 로컬 R2 바이트를 다시 스트리밍 검증하고 기존 bucket의 public-access-block과
S3 크기·SHA metadata·AES256을 확인한다. 객체가 없고 `--upload-r2`까지 명시한 경우에만
`IfNoneMatch=*`인 조건부 PutObject를 한 번 요청한다. ACL/정책 변경·공개 업로드·기존
객체 덮어쓰기·버킷 생성은 하지 않는다. HEAD 확인은 원격 파일 전체를 내려받아 해시를
다시 계산한 검사와 구분한다. 불확실 업로드는 HEAD로만 확인하고 자동 재업로드하지 않는다.

기록 실패는 spool의 `mlflow-outbox/<execution_id>/receipt.json`에 `pending`으로
보관하고 분석/보고 상태를 변경하지 않는다. 별도 idle 단계 또는 운영 명령에서
`retry_pending(spool_root, limit=2)`로 **저장 metadata만** 다시 전송한다. GPU 제출,
VLM/RAG 생성, report 재생성은 이 모듈에 없다. 원격 create 응답이 불확실하면 태그로
기존 run을 찾으며, 찾을 수 없으면 새 run을 만들지 않고 확인 필요로 남긴다.

volume 백업은 SQLite 일관성이 보장되는 백업 방식 또는 tracker를 정지한 상태에서
수행해야 한다. `docker compose down -v`는 실행 기록을 삭제하므로 운영 절차에 넣지 않는다.

공식 근거(2026-10-04 확인):
- [v3.16.1 릴리스](https://github.com/mlflow/mlflow/releases/tag/v3.16.1)
- [Tracking server·SQLite·artifact 분리](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/)
- [MlflowClient 등록·조회·run·artifact API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html)
