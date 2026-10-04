"""Persistent local Compose database for the existing embeddings. No AWS actions.

Only `search` calls Gemini, once for the query; it reads the key with getpass.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'artifacts/rag/local-db-v1'
INPUT = ROOT / 'artifacts/rag/law-153-20260927-01'
EMBEDDED = 'embedded-001-768-v1.jsonl'
PROJECT = 'cctv-rag-local'


def prepare(runtime=RUNTIME):
    """Create local secret files once. Never regenerate credentials for a populated DB."""
    marker = runtime / 'local-project.json'
    if runtime.is_symlink():
        raise ValueError('Local runtime must not be a symlink')
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not marker.exists() and any(runtime.iterdir()):
        raise ValueError('Nonempty local directory has no project marker; review before use')
    if marker.exists() and json.loads(marker.read_text()) != {'project': PROJECT, 'version': 1}:
        raise ValueError('Unexpected local project marker')
    if not marker.exists():
        marker.write_text(json.dumps({'project': PROJECT, 'version': 1})+'\n')
    runtime.chmod(0o700)
    for directory in ('data', 'data/postgresql', 'data/rag-output', 'secrets', 'receipts'):
        target = runtime / directory
        if target.is_symlink():
            raise ValueError('Local runtime subdirectory must not be a symlink')
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = runtime / 'secrets'
    private.chmod(0o700)
    populated = (runtime / 'data/postgresql/PG_VERSION').exists()
    for name in ('db_admin_password', 'db_app_password'):
        path = private / name
        if path.is_symlink():
            raise ValueError('Secret symlinks are not accepted')
        if path.exists():
            if not re.fullmatch('[a-f0-9]{64}', path.read_text().strip()):
                raise ValueError('Invalid stored DB secret; preserve it for review')
        elif populated:
            raise ValueError('Populated DB has a missing secret; restore it without rotating passwords')
        else:
            with path.open('x') as stream:
                stream.write(secrets.token_hex(32)+'\n')
        # Host directory is private; the explicitly assigned container can read the bind mount.
        path.chmod(0o444)
    placeholder = private / 'gemini_api_key'
    if placeholder.is_symlink():
        raise ValueError('Secret symlinks are not accepted')
    if not placeholder.exists():
        placeholder.touch(mode=0o444)


def compose(args, *, input=None, timeout=180):
    env = dict(os.environ, CCTV_DATA_DIR=str(RUNTIME/'data'),
               CCTV_SECRETS_DIR=str(RUNTIME/'secrets'), CCTV_INPUT_DIR=str(INPUT),
               RAG_APP_DIR=str(ROOT/'apps/rag_index'))
    env.pop('GEMINI_API_KEY', None)
    command = ['docker', 'compose', '-p', PROJECT,
               '-f', str(ROOT/'infra/compose/rag/compose.yaml'),
               '-f', str(ROOT/'apps/rag_index/compose.tools.yaml'), '--profile', 'tools', *args]
    return subprocess.run(command, env=env, input=input, capture_output=True, check=True,
                          timeout=timeout).stdout.decode()


def check_input():
    spec = importlib.util.spec_from_file_location('rag_index', ROOT/'apps/rag_index/index.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = INPUT/EMBEDDED
    rows = module.read_chunks(path, embedded=True)
    receipt = json.loads(Path(str(path)+'.complete.json').read_text())
    assert receipt['chunks'] == len(rows)
    assert receipt['output_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt['source_sha256'] == hashlib.sha256((INPUT/'chunks.jsonl').read_bytes()).hexdigest()
    return len(rows)


def record(kind, result):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    result = dict(result, operation=kind, recorded_at=stamp, compose_project=PROJECT,
                  input_sha256=hashlib.sha256((INPUT/EMBEDDED).read_bytes()).hexdigest(),
                  driver_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    path = RUNTIME/'receipts'/f'{stamp}-{kind}.json'
    with path.open('x') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('Receipt: '+str(path.relative_to(ROOT)))


VERIFY = '''
import json, math
from pathlib import Path
from container_entrypoint import configure
from index import connect, read_chunks, PROFILE
configure()
rows=read_chunks('/input/embedded-001-768-v1.jsonl',embedded=True)
checked=[]
with connect() as conn:
    total=conn.execute('SELECT count(*) FROM cctv_rag.chunks').fetchone()[0]
    for row in rows:
        key=(row['chunk_id'],PROFILE,row['embedding_input_sha256'])
        found=conn.execute("""SELECT content, metadata, vector_dims(embedding), embedding::text
            FROM cctv_rag.chunks WHERE chunk_id=%s AND embedding_profile=%s AND input_sha256=%s""",key).fetchone()
        assert found and found[0]==row['content'] and found[2]==768
        expected_metadata={k:v for k,v in row.items() if k not in ('embedding','database_loaded')}
        assert found[1]==expected_metadata
        stored=json.loads(found[3])
        max_error=max(abs(a-b) for a,b in zip(stored,row['embedding']))
        assert max_error<1e-6
        nearest=conn.execute("""SELECT chunk_id,input_sha256,embedding <=> %s::vector AS distance
            FROM cctv_rag.chunks WHERE embedding_profile=%s ORDER BY distance LIMIT 1""",
            (json.dumps(row['embedding']),PROFILE)).fetchone()
        assert nearest[0]==row['chunk_id'] and nearest[1]==row['embedding_input_sha256']
        assert abs(nearest[2])<1e-5
        checked.append({'chunk_id':row['chunk_id'],'dimensions':found[2],
            'max_vector_rounding_error':max_error,'source_url':found[1]['source_url'],
            'content_preview':row['content'][:90],'stored_vector_self_match':True})
print(json.dumps({'verified_rows':len(checked),'total_db_rows':total,'rows':checked,
    'method':'stored document vectors; no new question embedding',
    'gemini_api_calls':0,'semantic_query_quality_evaluated':False},ensure_ascii=False))
'''

SEARCH = '''
import json, os, sys
from container_entrypoint import configure
from index import search
configure()
os.environ['GEMINI_API_KEY']=sys.stdin.readline().strip()
result=search(sys.argv[1],sys.argv[2])
print(json.dumps(result,ensure_ascii=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['up','load','verify','status','stop','search'])
    parser.add_argument('--query')
    parser.add_argument('--date', default='2026-09-27', help='Date of applicability; set for each real incident')
    args = parser.parse_args()
    if args.command == 'search' and not args.query:
        parser.error('search requires --query')
    os.umask(0o077)
    if args.command == 'up':
        prepare()
        compose(['config','--quiet'])
        compose(['up','-d','--wait','--wait-timeout','120','db'])
        print('Local PostgreSQL ready; no API called. Files persist in artifacts/rag/local-db-v1/data.')
        return
    if not (RUNTIME/'local-project.json').exists():
        raise ValueError('Run python3 scripts/rag_local.py up first')
    if args.command == 'status':
        print(compose(['ps','db'])); return
    if args.command == 'stop':
        compose(['stop','db']); print('DB stopped; saved rows and secret files retained.'); return
    if args.command == 'load':
        check_input()
        # Validate the existing image against current sources using Docker's build cache.
        compose(['build','rag'], timeout=300)
        for name in ('schema.sql','grants.sql'):
            compose(['exec','-T','--user','postgres','db','psql','-X','-v','ON_ERROR_STOP=1','-d','cctv'],
                    input=(ROOT/'apps/rag_index'/name).read_bytes())
        result=json.loads(compose(['run','--rm','-T','--no-deps','rag','load','--input','/input/'+EMBEDDED]))
        record('load',dict(result,gemini_api_calls=0))
    elif args.command == 'verify':
        check_input()
        result=json.loads(compose(['run','--rm','-T','--no-deps','--entrypoint','python','rag','-c',VERIFY]))
        record('verify',result)
    elif args.command == 'search':
        print('This sends the query to Gemini once. Account quota/pricing applies. The key is not saved.')
        key = getpass.getpass('Gemini API key (hidden): ').strip()
        if not key or any(c.isspace() for c in key):
            raise ValueError('A non-empty API key is required')
        result=json.loads(compose(['run','--rm','-T','--no-deps','--entrypoint','python','rag','-c',
                                  SEARCH,args.query,args.date],input=(key+'\n').encode(),timeout=90))
        record('search',{'query':args.query,'date':args.date,'results':result,'gemini_query_api_calls':1})


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as exc:
        # Commands contain no passwords or API keys. Do not print environment or stdin.
        print(exc.stderr.decode()[-2500:], file=sys.stderr)
        raise SystemExit(exc.returncode)
