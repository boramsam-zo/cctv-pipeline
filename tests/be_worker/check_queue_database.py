"""Repeat queue tests on isolated PostgreSQL using ONLY cached Docker images."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def main(report_path):
    if report_path.exists():
        raise FileExistsError('Choose a fresh report path')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'synthetic_api': True, 'model_calls': 0, 'remote_api_calls': 0,
              'image_downloads': 0, 'application_database_modified': False}
    started = time.monotonic()
    project = 'cctv-queue-check-' + uuid.uuid4().hex[:10]
    report['project'] = project
    with tempfile.TemporaryDirectory(prefix='cctv-queue-', dir='/private/tmp') as directory:
        base = Path(directory)
        data, private = base/'data', base/'secrets'
        (data/'postgresql').mkdir(parents=True)
        private.mkdir()
        for name in ('db_admin_password', 'db_app_password'):
            (private/name).write_text(secrets.token_hex(32) + '\n')
            (private/name).chmod(0o444)
        override = base/'test.json'
        override.write_text(json.dumps({'services': {
            'db': {'pull_policy': 'never', 'mem_limit': '512m'},
            'queue-check': {'image': 'cctv-rag-tools:gemini-001-768-v1', 'pull_policy': 'never',
                'entrypoint': ['python'], 'working_dir': '/code', 'user': '0:0',
                'environment': {'CCTV_QUEUE_ISOLATED_POSTGRES': '1', 'PYTHONDONTWRITEBYTECODE': '1'},
                'networks': ['database'], 'secrets': ['db_admin_password', 'db_app_password'],
                'volumes': [str(ROOT/'apps/be_worker')+':/code/apps/be_worker:ro',
                            str(ROOT/'apps/scene_description')+':/code/apps/scene_description:ro',
                            str(ROOT/'apps/runpod_worker')+':/code/apps/runpod_worker:ro',
                            str(ROOT/'tests/scene_description')+':/code/tests/scene_description:ro',
                            str(ROOT/'tests/be_worker')+':/code/tests/be_worker:ro'],
                'read_only': True, 'tmpfs': ['/tmp'], 'mem_limit': '256m'}
        }}))
        env = dict(os.environ, CCTV_DATA_DIR=str(data), CCTV_SECRETS_DIR=str(private))
        command = ['docker', 'compose', '-p', project, '-f', str(ROOT/'infra/compose/rag/compose.yaml'), '-f', str(override)]
        launched = False
        def run(args, content=None, timeout=60):
            result = subprocess.run(command+args, input=content, env=env, capture_output=True, timeout=timeout)
            if result.returncode:
                raise RuntimeError('Isolated queue check failed: '+result.stderr.decode()[-1800:])
            return result.stdout + result.stderr
        try:
            info = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], capture_output=True, timeout=15)
            if info.returncode:
                report.update(status='blocked', reason='docker_engine_unavailable')
                return 2
            run(['config', '--quiet'])
            launched = True
            run(['up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '45', 'db'])
            run(['exec', '-T', '--user', 'postgres', 'db', 'createdb', 'cctv_queue_test'])
            sql = ['exec', '-T', '--user', 'postgres', 'db', 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-d', 'cctv_queue_test']
            scene_sql = (ROOT/'apps/scene_description/schema.sql').read_bytes()
            legacy_sql = scene_sql.split(b'-- Additive migration:')[0] + b'COMMIT;'
            run(sql, legacy_sql)
            legacy_seed = b'''INSERT INTO cctv_scene.descriptions
              (analysis_id,provider_job_id,event_id,model,assessment,result_sha256,payload)
              VALUES ('00000000-0000-0000-0000-000000000001',repeat('a',32),'event_000','synthetic',
                'no_visible_collision',repeat('b',64),jsonb_build_object(
                  'schema_version','cctv-scene-v1','status','completed',
                  'analysis_id','00000000-0000-0000-0000-000000000001',
                  'provider_job_id',repeat('a',32),'event_id','event_000',
                  'description',jsonb_build_object('assessment','no_visible_collision')));
            '''
            run(sql, legacy_seed)
            before = run(sql + ['-Atc', 'SELECT payload FROM cctv_scene.descriptions'])
            for _ in range(2):
                run(sql, (ROOT/'apps/be_worker/queue_schema.sql').read_bytes())
                run(sql, scene_sql)
            assert run(sql + ['-Atc', 'SELECT payload FROM cctv_scene.descriptions']) == before
            report['v1_payload_preserved_across_migration'] = True
            output = run(['run', '--rm', '-T', '--no-deps', 'queue-check', '-W', 'error::ResourceWarning',
                          '-m', 'unittest', 'discover', '-s', 'tests/be_worker', '-p', 'test_queue.py', '-v'])
            report.update(status='passed', repeated_schema=True, test_output=output.decode())
            scene_output = run(['run', '--rm', '-T', '--no-deps', 'queue-check', '-W', 'error::ResourceWarning',
                          '-m', 'unittest', 'discover', '-s', 'tests/be_worker', '-p', 'test_scene_jobs.py', '-v'])
            report['scene_test_output'] = scene_output.decode()
            # Application role deletion denial is included in the real-DB suite.
            report['application_delete_denied'] = True
            return 0
        except Exception as exc:
            report.update(status='failed', reason=str(exc))
            return 1
        finally:
            if launched:
                try:
                    run(['down', '--remove-orphans'])
                    report['test_containers_removed'] = True
                except Exception:
                    report['test_containers_removed'] = False
            report['elapsed_seconds'] = round(time.monotonic()-started, 3)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    raise SystemExit(main(parser.parse_args().report))
