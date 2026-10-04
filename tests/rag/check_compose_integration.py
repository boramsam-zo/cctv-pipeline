"""Opt-in local Docker test with synthetic vectors. No Gemini, AWS or model calls.

Run directly with --report PATH. Only its unique Compose project is removed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'artifacts/rag/law-153-20260927-01/chunks.jsonl'

CLIENT_CHECK = '''
import json, os, sys
sys.path.insert(0, '/app')
import psycopg
from container_entrypoint import configure
import index
configure()
with index.connect() as conn:
    rows = conn.execute('SELECT count(*), min(vector_dims(embedding)), max(vector_dims(embedding)) FROM cctv_rag.chunks').fetchone()
    assert rows == (2,768,768), rows
index.embed = lambda text, task: [1.0] + [0.0]*767
results = index.search('SYNTHETIC QUERY: no model used', '2026-09-27')
assert len(results)==2 and results[0]['chunk_id']=='synthetic-compose-0', results
assert results[0]['cosine_distance']==0 and results[1]['cosine_distance']==1, results
assert index.search('SYNTHETIC QUERY', '2020-01-01') == []
try:
    with index.connect() as conn:
        conn.execute('DROP TABLE cctv_rag.chunks')
except psycopg.errors.InsufficientPrivilege:
    pass
else:
    raise AssertionError('Application role unexpectedly owns DDL privileges')
try:
    psycopg.connect(host='db', dbname='cctv', user='cctv_app', password='incorrect-test-password', connect_timeout=5)
except psycopg.OperationalError:
    pass
else:
    raise AssertionError('Incorrect password was accepted')
print(json.dumps({'rows':2,'dimensions':768,'cosine_distances':[0,1], 'date_filter':True, 'ddl_denied':True,'wrong_password_denied':True,'synthetic_vectors':True,'api_calls':0}))
'''

def main(report_path):
    started = time.monotonic()
    report = {'test':'isolated-local-compose','api_calls':0,'aws_changes':0,'model_inference':False,
              'synthetic_vectors':True, 'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest()}
    project = 'cctv-rag-check-' + uuid.uuid4().hex[:10]
    report['project'] = project
    with tempfile.TemporaryDirectory(prefix='cctv-compose-', dir='/private/tmp') as directory:
        base = Path(directory)
        data, secret_dir, inputs = (base / n for n in ('data', 'secrets', 'input'))
        for path in (data, secret_dir, inputs, data/'postgresql', data/'rag-output'):
            path.mkdir(mode=0o700)
        for name in ('db_admin_password','db_app_password'):
            (secret_dir/name).write_text(secrets.token_hex(32)+'\n')
            (secret_dir/name).chmod(0o444)
        (secret_dir/'gemini_api_key').touch(mode=0o444)
        shutil.copyfile(SOURCE, inputs/'original-chunks.jsonl')
        (inputs/'client_check.py').write_text(CLIENT_CHECK)
        # No fixture is ever written to the real law corpus.
        rows=[]
        for i in range(2):
            content=f'SYNTHETIC DATABASE TEST {i}; NOT LEGAL TEXT OR A MODEL OUTPUT'
            sha=hashlib.sha256(content.encode()).hexdigest()
            vector=[0.0]*768; vector[i]=1.0
            rows.append(dict(chunk_id=f'synthetic-compose-{i}',document_id='synthetic-compose-fixture',
                content=content, embedding_input=content, content_sha256=sha, embedding_input_sha256=sha,
                source_url='https://example.invalid/synthetic-fixture',effective_from='2026-01-01',
                embedding_model='gemini-embedding-001',embedding_dimensions=768,
                embedding_profile='gemini-001-768-retrieval-v1',embedding=vector,
                test_fixture=True,embedding_status='synthetic_not_generated_by_model'))
        fixture=inputs/'synthetic.jsonl'
        fixture.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        Path(str(fixture)+'.complete.json').write_text(json.dumps({'chunks':2,
            'output_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'test_fixture':True}))
        # Read-only input bind mounts are read by UID 10001 inside the tools container.
        inputs.chmod(0o755)
        for path in inputs.iterdir(): path.chmod(0o644)
        env=dict(os.environ, CCTV_DATA_DIR=str(data),CCTV_SECRETS_DIR=str(secret_dir),
                 CCTV_INPUT_DIR=str(inputs),RAG_APP_DIR=str(ROOT/'apps/rag_index'))
        command=['docker','compose','-p',project,'-f',str(ROOT/'infra/compose/rag/compose.yaml'),
                 '-f',str(ROOT/'apps/rag_index/compose.tools.yaml'),'--profile','tools']
        def run(args, *, input=None, timeout=300):
            result=subprocess.run(command+args,env=env,input=input,capture_output=True,timeout=timeout)
            if result.returncode:
                # CLI never carries password values; keep diagnostic output bounded.
                raise RuntimeError(f'Compose {args[:3]} failed: '+result.stderr.decode()[-3500:])
            return result.stdout
        try:
            run(['config','--quiet'])
            print('Pulling pinned PostgreSQL/pgvector image', flush=True)
            run(['pull','db'],timeout=600)
            print('Starting isolated database',flush=True)
            run(['up','-d','--wait','--wait-timeout','120','db'],timeout=150)
            report['versions']=run(['exec','-T','db','psql','-X','-U','postgres','-d','cctv','-Atc',
                "SELECT version(); SELECT extversion FROM pg_extension WHERE extname='vector';"]).decode().splitlines()
            for name in ('schema.sql','grants.sql'):
                run(['exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d','cctv'],
                    input=(ROOT/'apps/rag_index'/name).read_bytes())
            print('Building CPU-only RAG command image',flush=True)
            run(['build','rag'],timeout=600)
            report['source_check']=json.loads(run(['run','--rm','-T','rag','check','--input','/input/original-chunks.jsonl']))
            report['loads']=[]
            for _ in range(2):
                report['loads'].append(json.loads(run(['run','--rm','-T','rag','load','--input','/input/synthetic.jsonl'])))
            verify=['run','--rm','-T','--entrypoint','python','rag','/input/client_check.py']
            report['before_recreate']=json.loads(run(verify))
            print('Recreating DB container and checking persistent rows',flush=True)
            run(['up','-d','--force-recreate','--wait','--wait-timeout','120','db'],timeout=150)
            report['after_recreate']=json.loads(run(verify))
            print('Dumping and restoring to a separate test database',flush=True)
            dump=run(['exec','-T','db','pg_dump','-U','postgres','-d','cctv','-Fc'])
            report['dump_sha256']=hashlib.sha256(dump).hexdigest()
            report['dump_bytes']=len(dump)
            run(['exec','-T','db','createdb','-U','postgres','cctv_restore_check'])
            run(['exec','-T','db','pg_restore','--exit-on-error','-U','postgres','-d','cctv_restore_check'],input=dump)
            restored=run(['exec','-T','db','psql','-X','-U','postgres','-d','cctv_restore_check','-Atc',
                'SELECT count(*), min(vector_dims(embedding)), max(vector_dims(embedding)) FROM cctv_rag.chunks;']).decode().strip()
            assert restored=='2|768|768', restored
            report['restore_rows_and_dimensions']=restored
            report['status']='passed'
        except Exception as exc:
            report['status']='failed'
            report['error']=str(exc)
            diagnostic=run(['logs','--no-color','--tail','65','db']).decode()
            for name in ('db_admin_password','db_app_password'):
                diagnostic=diagnostic.replace((secret_dir/name).read_text().strip(),'[REDACTED]')
            report['db_diagnostic']=diagnostic
            print(diagnostic,flush=True)
            raise
        finally:
            print('Removing only the isolated test containers and network',flush=True)
            try:
                run(['down','--remove-orphans'],timeout=90)
                report['test_containers_removed']=True
            finally:
                report['duration_seconds']=round(time.monotonic()-started,3)
                report_path.parent.mkdir(parents=True,exist_ok=True)
                report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,required=True)
    main(parser.parse_args().report)
