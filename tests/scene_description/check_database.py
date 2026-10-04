"""Isolated local Compose check. Existing images only; synthetic media/API replies."""
import argparse
import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from test_scene import fixture, response
import scene


def main(report_path):
    if report_path.exists():
        raise FileExistsError('Use a fresh test report path')
    started = time.monotonic()
    project = 'cctv-scene-check-' + uuid.uuid4().hex[:10]
    report = {'project': project, 'synthetic': True, 'gemini_api_calls': 0,
              'aws_calls': 0, 'image_downloads': 0, 'status': 'running'}
    with tempfile.TemporaryDirectory(prefix='cctv-scene-', dir='/private/tmp') as directory:
        base = Path(directory)
        data, secrets_dir = base/'data', base/'secrets'
        (data/'postgresql').mkdir(parents=True, mode=0o700)
        secrets_dir.mkdir(mode=0o700)
        for name in ['db_admin_password', 'db_app_password']:
            (secrets_dir/name).write_text(secrets.token_hex(32)+'\n')
            (secrets_dir/name).chmod(0o444)
        original = base/'original'; original.mkdir()
        config, _, reader = fixture(original)
        scene.fetch(config, base/'bundle', reader)
        result = scene.describe(base/'bundle',base/'description','gemini-synthetic',4,'synthetic-key',lambda *args:response())
        result['test_fixture'] = True
        result['description']['summary'] = "SYNTHETIC '; DROP TABLE cctv_scene.descriptions; --"
        result_path = base/'result.json'
        result_path.write_bytes(scene.encoded(result)); result_path.chmod(0o644)
        override = base/'limits.json'
        override.write_text(json.dumps({'services': {'db': {'pull_policy': 'never', 'mem_limit': '512m'}}}))
        env = dict(os.environ, CCTV_DATA_DIR=str(data), CCTV_SECRETS_DIR=str(secrets_dir),
                   SCENE_APP_DIR=str(ROOT/'apps/scene_description'), SCENE_RESULT_FILE=str(result_path))
        command = ['docker','compose','-p',project,'-f',str(ROOT/'infra/compose/rag/compose.yaml'),
                   '-f',str(ROOT/'apps/scene_description/compose.tools.yaml'),'-f',str(override),'--profile','tools']
        def run(args, data=None, expected=0, timeout=60):
            proc = subprocess.run(command+args,env=env,input=data,capture_output=True,timeout=timeout)
            if proc.returncode != expected:
                raise RuntimeError('Isolated Compose check failed: '+proc.stderr.decode()[-1500:])
            return proc.stdout
        try:
            run(['config','--quiet'])
            run(['up','-d','--pull','never','--wait','--wait-timeout','45','db'])
            run(['exec','-T','--user','postgres','db','psql','-X','-v','ON_ERROR_STOP=1','-d','cctv'],
                (ROOT/'apps/scene_description/schema.sql').read_bytes())
            tool = ['run','--rm','-T','--no-deps','scene-db']
            first = json.loads(run(tool+['load']))
            second = json.loads(run(tool+['load']))
            assert first['inserted'] and second['already_present']
            found = json.loads(run(tool+['show']))
            assert found['result'] == result and found['database_loaded']
            changed = copy.deepcopy(result); changed['description']['summary'] = 'changed attempt'
            result_path.write_bytes(scene.encoded(changed))
            run(tool+['load'],expected=1)
            result_path.write_bytes(scene.encoded(result))
            assert json.loads(run(tool+['show']))['result'] == result
            sql = 'SELECT count(*) FROM cctv_scene.descriptions;'
            count = run(['exec','-T','--user','postgres','db','psql','-X','-d','cctv','-Atc',sql]).decode().strip()
            assert count == '1'
            denied_code = '''import sys; sys.path.insert(0,'/scene')
import db,psycopg
try:
    with db.connect() as conn: conn.execute('DELETE FROM cctv_scene.descriptions')
except psycopg.errors.InsufficientPrivilege: print('delete_denied')
else: raise AssertionError('unexpected delete permission')
'''
            denied = run(['run','--rm','-T','--no-deps','--entrypoint','python','scene-db','-c',denied_code])
            assert denied.decode().strip() == 'delete_denied'
            report.update(status='passed', rows=1, inserted=True, duplicate_noop=True,
                conflicting_id_rejected=True, original_preserved=True, application_delete_denied=True,
                sql_text_stored_as_data=True, show_matches_result=True)
        except Exception as exc:
            report.update(status='failed',error=str(exc))
            raise
        finally:
            try:
                run(['down','--remove-orphans'])
                report['test_containers_removed'] = True
            finally:
                report['elapsed_seconds'] = round(time.monotonic()-started,3)
                report_path.parent.mkdir(parents=True,exist_ok=True)
                report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--report',type=Path,required=True)
    main(parser.parse_args().report)
