#!/usr/bin/env python3
"""Mac: run P6 PostgreSQL checks with cached images and disposable tmpfs only.

Creates exactly one uniquely named PostgreSQL container, an ephemeral BE test
container, and a new local receipt/log. No image pulls, installs, host ports,
production volumes, external network, cloud/model calls, or service mutations.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
PG='pgvector/pgvector@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b'
BE='cctv-be-queue:pg-v1'
LABEL='cctv.p6.isolated-check'


def docker(*args,timeout=60,check=True):
    result=subprocess.run(['docker',*args],text=True,capture_output=True,timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Docker test command failed: '+result.stderr.strip()[:1000])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path,default=ROOT/'artifacts/be-worker/selection-p6-20261002-01/postgres-check-01.json')
    args=parser.parse_args();receipt=args.receipt.absolute();log=receipt.with_suffix('.log')
    if receipt.exists() or log.exists():raise SystemExit('Existing test receipt/log preserved; choose a new --receipt')
    receipt.parent.mkdir(parents=True,exist_ok=True)
    # Inspect only named cached images. All subsequent docker run calls forbid pulling.
    image_ids={name:docker('image','inspect',name,'--format','{{.Id}}').stdout.strip() for name in (PG,BE)}
    before=set(docker('ps','-aq').stdout.split())
    run_id=uuid.uuid4().hex[:12];name='cctv-p6-isolated-'+run_id
    started=time.monotonic();created=False;outcome={'status':'failed_before_tests'};test_output=''
    try:
        docker('run','--detach','--pull','never','--name',name,'--label',LABEL+'='+run_id,
               '--network','none','--memory','512m','--cpus','2',
               '--tmpfs','/var/lib/postgresql/data:rw,size=256m','--tmpfs','/tmp:rw,size=32m',
               '--env','POSTGRES_HOST_AUTH_METHOD=trust','--env','POSTGRES_DB=cctv_selection_test',PG)
        created=True
        for _ in range(60):
            process=docker('exec',name,'cat','/proc/1/comm',check=False)
            ready=docker('exec',name,'psql','-X','-U','postgres','-d','cctv_selection_test',
                         '-Atqc','SELECT 1',check=False)
            if process.stdout.strip()=='postgres' and ready.returncode==0 and ready.stdout.strip()=='1':break
            time.sleep(.2)
        else:raise RuntimeError('Isolated PostgreSQL did not become ready')
        with tempfile.TemporaryDirectory(prefix='cctv-p6-receipts-') as temp:
            result=docker('run','--rm','--pull','never','--network','container:'+name,
                '--memory','512m','--cpus','2','--read-only','--tmpfs','/tmp:rw,size=128m',
                '--mount','type=bind,source='+str(ROOT)+',target=/code,readonly',
                '--mount','type=bind,source='+temp+',target=/receipts',
                '--workdir','/code','--env','PYTHONDONTWRITEBYTECODE=1',
                '--env','CCTV_P6_ISOLATED_POSTGRES=1','--env','CCTV_P6_TEST_RECEIPT=/receipts/result.json',
                '--entrypoint','python',BE,'-B','-m','tests.be_worker.test_selection_postgres',timeout=90,check=False)
            test_output=result.stdout+'\n'+result.stderr
            output=Path(temp)/'result.json'
            outcome=json.loads(output.read_text()) if output.exists() else {'status':'test_process_failed_before_receipt'}
            outcome['test_process_exit_code']=result.returncode
    except Exception as exc:
        outcome.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        if created:
            inspected=docker('inspect',name,'--format','{{ index .Config.Labels "'+LABEL+'" }}',check=False)
            if inspected.returncode==0 and inspected.stdout.strip()==run_id:
                docker('rm','--force',name)
            else:
                outcome.update(status='cleanup_requires_inspection',cleanup_label_verified=False)
        after=set(docker('ps','-aq').stdout.split())
        outcome.update(schema_version='cctv-p6-postgres-check-v1',cached_image_ids=image_ids,
            isolated_tmpfs_database=True,network='none; BE shares isolated PG namespace',
            image_pulls=0,installations=0,production_data_access=0,
            prior_containers_preserved=before<=after,test_containers_removed=not bool(after-before),
            elapsed_including_setup_seconds=round(time.monotonic()-started,6),
            source_sha256={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (ROOT/'apps/be_worker/selection_jobs.py',ROOT/'apps/be_worker/selection_schema.sql',
                    ROOT/'apps/be_worker/prepare_selections.py',ROOT/'apps/be_worker/flow_service.py',
                    ROOT/'apps/be_worker/job_queue.py',ROOT/'apps/be_worker/worker.py',
                    ROOT/'apps/be_worker/runpod_api.py',ROOT/'apps/be_worker/generation_settings.py',
                    ROOT/'tests/be_worker/test_selection_postgres.py',Path(__file__))})
        if not outcome['prior_containers_preserved'] or not outcome['test_containers_removed']:
            outcome['status']='container_inventory_changed_review_required'
        with log.open('x') as stream:stream.write(test_output)
        with receipt.open('x') as stream:json.dump(outcome,stream,indent=2)
    print(json.dumps(outcome,indent=2))
    raise SystemExit(0 if outcome.get('status')=='passed' and outcome.get('test_process_exit_code')==0 else 1)


if __name__=='__main__':main()
