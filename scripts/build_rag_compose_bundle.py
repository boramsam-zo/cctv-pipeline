"""Small allowlisted EC2 app bundle. No secrets, Terraform state or model weights."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/infra/cpu-rag-compose-20260927-01/cctv-rag-compose.zip'
README='''# EC2 RAG Compose 도구 묶음

먼저 원본 저장소의 infra/terraform/cpu-rag 구성으로 EC2를 준비합니다.
Terraform이 /opt/cctv-stack/compose.yaml과 DB를 설치해야 아래 명령을 사용할 수 있습니다.
이 ZIP은 DB·AWS를 만들지 않으며 아직 원격에 업로드하지 않았습니다.

EC2 /tmp/cctv-rag-compose.zip에 이 파일을 전달한 뒤:

```bash
sudo mkdir /opt/cctv-rag-compose
sudo python3 -m zipfile -e /tmp/cctv-rag-compose.zip /opt/cctv-rag-compose
cd /opt/cctv-rag-compose
sudo bash rag.sh build
sudo bash rag.sh schema
sudo bash rag.sh check --input /input/chunks.jsonl
```

기존 폴더가 있다면 덮어쓰지 말고 새 경로를 사용합니다. build는 Python 이미지와
psycopg 드라이버를 받습니다. check는 API를 호출하지 않습니다.

Gemini 계정의 001 모델 가용성·요금/한도 확인 후 키를 숨김 입력하고 실행:

```bash
sudo python3 /opt/cctv-infra/prepare-host.py --gemini
sudo bash rag.sh embed --input /input/chunks.jsonl --output /output/embedded-001-768-v1.jsonl
sudo bash rag.sh load --input /output/embedded-001-768-v1.jsonl
sudo bash rag.sh search --query '교통사고 발생 후 조치나 신고를 방해한 경우 관련 조문' --date 2026-09-27
sudo systemctl start cctv-db-backup.service
sudo journalctl -u cctv-db-backup.service --no-pager
```

embed는 문서2회, search는 질의1회 API를 호출합니다. 출력이 있으면 v2 등 새 이름을
사용합니다. 출력은 EC2 /srv/cctv/rag-output, DB는 /srv/cctv/postgresql입니다.
S3는 원본·근거·백업용입니다. BE·worker·화면과 실제 Gemini 검증은 별도 단계입니다.

bundle-manifest.json은 묶음의 소스 SHA256입니다. Gemini 키·DB 비밀번호·state는
포함하지 않았습니다. app/README.md의 저장소 상대 링크는 원본 저장소에서 확인합니다.
'''
WRAPPER='''#!/bin/bash
set -euo pipefail
bundle_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export CCTV_COMPOSE_FILE=/opt/cctv-stack/compose.yaml
export CCTV_INPUT_DIR="$bundle_dir/input"
exec bash "$bundle_dir/app/compose.sh" "$@"
'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--embedded-input', type=Path)
    args=parser.parse_args()
    output=args.output
    files={}
    for name in ('Dockerfile','.dockerignore','requirements.txt','index.py','container_entrypoint.py',
                 'schema.sql','grants.sql','compose.tools.yaml','compose.sh','README.md'):
        files['app/'+name]=(ROOT/'apps/rag_index'/name).read_bytes()
    files['input/chunks.jsonl']=(ROOT/'artifacts/rag/law-153-20260927-01/chunks.jsonl').read_bytes()
    files['README.md']=README.encode()
    if args.embedded_input:
        source=args.embedded_input
        raw=source.read_bytes()
        receipt_path=Path(str(source)+'.complete.json')
        receipt=json.loads(receipt_path.read_text())
        spec=importlib.util.spec_from_file_location('rag_bundle_index',ROOT/'apps/rag_index/index.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows=module.read_chunks(source,embedded=True)
        if (receipt['output_sha256'] != hashlib.sha256(raw).hexdigest()
                or receipt['source_sha256'] != hashlib.sha256(files['input/chunks.jsonl']).hexdigest()
                or receipt['chunks'] != len(rows) or receipt['profile'] != module.PROFILE):
            raise ValueError('Embedding receipt does not match the reviewed source/output/profile')
        files['input/embedded.jsonl']=raw
        files['input/embedded.jsonl.complete.json']=receipt_path.read_bytes()
        files['README.md']='''# EC2 RAG Compose — 기존 임베딩 재사용

Terraform으로 Docker·DB를 준비한 EC2에서 이 묶음을 새 폴더에 풉니다.
기존 폴더나 DB는 덮어쓰지 않습니다. 묶음에는 법령2청크와 이미 생성한
Gemini 001/768차원 벡터·완료영수증이 들어 있습니다. 인증키는 없습니다.

```bash
sudo bash rag.sh build
sudo bash rag.sh schema
sudo bash rag.sh check --input /input/chunks.jsonl
sudo bash rag.sh load --input /input/embedded.jsonl
```

load는 기존 벡터를 DB에 적재하며 Gemini를 호출하지 않습니다.
verified_rows=2, committed=true를 확인합니다. 새 질문의 검색은 별도로
서버에 Gemini 키를 비공개 설정한 뒤 rag.sh search로 실행합니다.
BE·VLM·화면 통합은 별도입니다. bundle-manifest.json으로 파일 해시를 확인합니다.
'''.encode()
    files['rag.sh']=WRAPPER.encode()
    manifest={'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()},
              'schema':'cctv-rag-compose-bundle-v1','actual_gemini_embeddings_included':bool(args.embedded_input)}
    files['bundle-manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():
            info=zipfile.ZipInfo(name)
            info.external_attr=0o100644 << 16
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for name,sha in manifest['files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest()==sha
    print(json.dumps({'files':len(files),'bytes':output.stat().st_size,
                      'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'file':str(output)}))

if __name__=='__main__':main()
