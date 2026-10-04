"""P5 integration checks on a new isolated PostgreSQL, using cached images only.

The host entry point creates its own Compose project, internal network and
temporary data/secret directories. It publishes no ports and never runs a
production Compose file or cleanup command. The inner check uses real psycopg
transactions and the application role; external provider entry points fail shut.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
TEST_DB = 'cctv_p5_test'
WORKER_IMAGE = 'cctv-be-queue:pg-v1'
TABLES = ('cctv_jobs.queue', 'cctv_jobs.flows', 'cctv_scene.descriptions',
          'cctv_scene.report_runs', 'cctv_rag.chunks')


def inside():
    """Only callable in the dedicated no-egress validation container."""
    if os.environ.get('CCTV_P5_ISOLATED_POSTGRES') != '1':
        raise RuntimeError('isolated_container_marker_required')
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from unittest.mock import patch
    from types import SimpleNamespace
    from apps.be_worker import flow_service as flow, generation_settings as generation
    from apps.be_worker import job_queue, prepare_generation_settings
    from apps.be_worker.common import read_json
    from apps.be_worker.queue_store import Store
    from apps.scene_description import scene
    from apps.rag_index import index

    report = {'status': 'running', 'database': TEST_DB, 'checks': [],
              'model_calls': 0, 'external_api_calls': 0, 'synthetic_media_only': True}
    stage = 'connect_isolated_database'

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)

    def connect(user, secret_name):
        return psycopg.connect(host='db', dbname=TEST_DB, user=user,
            password=Path('/run/secrets', secret_name).read_text().strip(),
            autocommit=True, row_factory=dict_row, connect_timeout=5,
            options='-c statement_timeout=10000 -c lock_timeout=3000')

    def snapshot(conn):
        answer = {}
        for table in TABLES:
            payload = "to_jsonb(t)-'generation_settings'" if table == 'cctv_jobs.flows' else 'to_jsonb(t)'
            answer[table] = [row['value'] for row in conn.execute(
                f'SELECT ({payload})::text AS value FROM {table} t ORDER BY ({payload})::text')]
        return answer

    try:
        with ExitStack() as stack:
            admin = stack.enter_context(connect('postgres', 'db_admin_password'))
            check(admin.execute('SELECT current_database() AS name').fetchone()['name'] == TEST_DB,
                  'database_name_is_isolated')
            stage = 'create_legacy_schema'
            for name in ('apps/be_worker/queue_schema.sql', 'apps/scene_description/schema.sql',
                         'apps/be_worker/report_schema.sql', 'apps/rag_index/schema.sql',
                         'apps/rag_index/grants.sql'):
                admin.execute((ROOT/name).read_text())
            flow_sql = (ROOT/'apps/be_worker/flow_schema.sql').read_text()
            addition = 'ALTER TABLE cctv_jobs.flows ADD COLUMN IF NOT EXISTS generation_settings jsonb;'
            check(flow_sql.count(addition) == 1, 'legacy_fixture_removes_only_settings_column')
            admin.execute(flow_sql.replace(addition, ''))
            check(admin.execute("""SELECT count(*) AS n FROM information_schema.columns
                WHERE table_schema='cctv_jobs' AND table_name='flows'
                AND column_name='generation_settings'""").fetchone()['n'] == 0,
                'legacy_flow_has_no_settings_column')

            app = stack.enter_context(connect('cctv_app', 'db_app_password'))
            check(app.execute('SELECT current_user AS role').fetchone()['role'] == 'cctv_app',
                  'application_role_connection_verified')
            work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='p5-check-')))
            spool = work/'spool'
            config = {'runpod_url': 'https://test.invalid', 's3_bucket': 'cctv-test-bucket',
                      's3_region': 'ap-northeast-2'}
            external_guards = [stack.enter_context(patch.object(target, name,
                side_effect=AssertionError('external_call_forbidden')))
                for target, name in [(flow, 'api_for'), (flow, 'InstanceRoleReader'),
                                     (flow, 'key'), (scene, 'call_gemini'), (index, 'embed')]]
            external_guards.append(stack.enter_context(patch('urllib.request.urlopen',
                side_effect=AssertionError('external_call_forbidden'))))
            stack.enter_context(patch.object(flow, 'spool', return_value=spool))
            stack.enter_context(patch.object(flow, 'config', return_value=config))

            def video(name):
                path = work/(name+'.mp4')
                path.write_bytes(b'\0\0\0\x18ftypisom-P5-SYNTHETIC-' + name.encode())
                return path

            stage = 'seed_legacy_rows'
            store = Store(app)
            legacy = job_queue.enqueue(store, spool, video('legacy'), config, 'legacy-p5-seed')
            legacy_id = legacy['worker_job_id']
            app.execute('INSERT INTO cctv_jobs.flows(worker_job_id,reference_date) VALUES (%s,%s)',
                        (legacy_id, '2026-10-01'))
            analysis_id = '00000000-0000-0000-0000-000000000005'
            payload = {'schema_version': 'cctv-scene-v1', 'status': 'completed',
                       'analysis_id': analysis_id, 'provider_job_id': 'a'*32, 'event_id': 'event_000',
                       'description': {'assessment': 'uncertain'}, 'synthetic_preserved': True}
            admin.execute('''INSERT INTO cctv_scene.descriptions
                (analysis_id,provider_job_id,event_id,model,assessment,result_sha256,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''', (analysis_id, 'a'*32, 'event_000',
                    'synthetic-legacy', 'uncertain', 'b'*64, Jsonb(payload)))
            admin.execute('''INSERT INTO cctv_scene.report_runs
                (report_id,analysis_id,worker_job_id,settings,state) VALUES (%s,%s,%s,%s,%s)''',
                ('00000000-0000-0000-0000-000000000006', analysis_id, legacy_id,
                 Jsonb({'model': 'synthetic-legacy'}), Jsonb({'status': 'report_stored', 'preserved': True})))
            admin.execute('''INSERT INTO cctv_rag.chunks
                (chunk_id,embedding_profile,input_sha256,document_id,content,metadata,embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s::vector)''',
                ('synthetic-p5-chunk', index.PROFILE, 'c'*64, 'synthetic-p5-document',
                 '보존 확인용 합성 문서', Jsonb({'source_url': 'https://example.invalid/synthetic'}),
                 json.dumps([1.0]+[0.0]*767)))
            baseline = snapshot(admin)
            check(all(len(rows) == 1 for rows in baseline.values()), 'five_legacy_tables_seeded')

            stage = 'apply_migration_twice'
            admin.execute(prepare_generation_settings.sql())
            check(snapshot(admin) == baseline, 'migration_preserves_all_old_row_values')
            admin.execute(prepare_generation_settings.sql())
            check(snapshot(admin) == baseline, 'migration_is_idempotent_and_preserves_values')
            check(app.execute('SELECT generation_settings FROM cctv_jobs.flows').fetchone()[
                'generation_settings'] is None, 'legacy_settings_remain_null_until_resume')
            privileges = app.execute("""SELECT
                has_column_privilege(current_user,'cctv_jobs.flows','generation_settings','SELECT') AS readable,
                has_column_privilege(current_user,'cctv_jobs.flows','generation_settings','INSERT') AS insertable,
                has_column_privilege(current_user,'cctv_jobs.flows','generation_settings','UPDATE') AS updateable,
                has_table_privilege(current_user,'cctv_jobs.flows','DELETE') AS deletable""").fetchone()
            check(privileges == {'readable': True, 'insertable': True, 'updateable': True, 'deletable': False},
                  'application_settings_privileges_are_limited')
            try:
                with app.transaction():
                    app.execute('DELETE FROM cctv_jobs.flows WHERE worker_job_id=%s', (legacy_id,))
            except psycopg.errors.InsufficientPrivilege:
                report['checks'].append('application_delete_denied_by_postgres')
            else:
                raise AssertionError('application_delete_was_not_denied')

            stage = 'legacy_resume'
            legacy_row = next(row for row in store.rows() if row['worker_job_id'] == legacy_id)
            legacy_flow = app.execute('SELECT * FROM cctv_jobs.flows WHERE worker_job_id=%s',
                                      (legacy_id,)).fetchone()
            directory_provider = SimpleNamespace(directory=lambda row: spool/row['directory_id'])
            with patch.dict(os.environ, {generation.SCENE_MODEL_ENV: 'gemini-new-default',
                                         generation.REPORT_MODEL_ENV: 'gemini-new-report'}):
                restored = flow.prepare_settings(app, directory_provider, legacy_row, legacy_flow)
            check(restored == generation.legacy_settings(), 'legacy_resume_ignores_new_defaults')
            check(app.execute('SELECT generation_settings FROM cctv_jobs.flows WHERE worker_job_id=%s',
                (legacy_id,)).fetchone()['generation_settings'] == restored,
                'legacy_settings_persist_as_jsonb')
            check(snapshot(admin) == baseline, 'legacy_resume_preserves_all_old_values')

            stage = 'atomic_admission_rollback'
            crashed_video = video('crash')
            before_dirs = set(spool.iterdir())
            original_receipt = flow.write_settings_receipt

            def crash_after_enqueue(directory, reference_date, settings):
                # This query is inside the real transaction and proves enqueue
                # already inserted a row before this simulated process failure.
                check(len(Store(app).rows()) == 2, 'queue_insert_visible_inside_transaction')
                original_receipt(directory, reference_date, settings)
                raise RuntimeError('synthetic_interruption_before_flow_insert')

            with patch.object(flow, 'write_settings_receipt', side_effect=crash_after_enqueue):
                try:
                    flow.admit(store, crashed_video, '2026-10-01')
                except RuntimeError as exc:
                    if str(exc) != 'synthetic_interruption_before_flow_insert':
                        raise
                else:
                    raise AssertionError('expected_interruption_did_not_occur')
            check(snapshot(admin) == baseline, 'transaction_rolls_back_queue_and_flow_together')
            orphans = set(spool.iterdir()) - before_dirs
            check(len(orphans) == 1, 'rollback_leaves_one_private_spool_orphan')
            orphan = orphans.pop()
            orphan_state = read_json(orphan/'state.json')
            check(orphan_state['status'] == 'prepared' and orphan_state['post_attempts'] == 0
                  and orphan_state['provider_job_id'] is None, 'orphan_has_no_submission')
            check(orphan_state['worker_job_id'] not in {r['worker_job_id'] for r in store.rows()},
                  'orphan_is_absent_from_dispatch_queue')

            observer = stack.enter_context(connect('cctv_app', 'db_app_password'))

            def check_connection_and_locks(label):
                check(app.execute('SELECT 1 AS n').fetchone()['n'] == 1,
                      label+'_connection_reusable')
                for lock_key, lock_name in ((1, 'dispatch'), (4, 'admission')):
                    acquired = observer.execute('SELECT pg_try_advisory_lock(734821,%s) AS ok',
                                                (lock_key,)).fetchone()['ok']
                    try:
                        check(acquired, label+'_'+lock_name+'_lock_released')
                    finally:
                        if acquired:
                            observer.execute('SELECT pg_advisory_unlock(734821,%s)', (lock_key,))

            check_connection_and_locks('python_interruption')

            class QueueInsertFailureStore(Store):
                def insert(self, request_key, directory_id, state):
                    # A real SQL failure marks the enclosing transaction INERROR.
                    # This is deliberately different from a Python-only mock.
                    self.connection.execute('SELECT 1/0')

            class FlowInsertFailureConnection:
                def execute(self, sql, params=None):
                    if sql.lstrip().startswith('INSERT INTO cctv_jobs.flows'):
                        return app.execute('SELECT 1/0')
                    return app.execute(sql, params)

                def transaction(self):
                    return app.transaction()

                @property
                def closed(self):
                    return app.closed

            for label, failing_store in (
                    ('queue_sql_failure', QueueInsertFailureStore(app)),
                    ('flow_sql_failure', Store(FlowInsertFailureConnection()))):
                stage = label
                previous_dirs = set(spool.iterdir())
                try:
                    flow.admit(failing_store, video(label), '2026-10-01')
                except psycopg.errors.DivisionByZero:
                    report['checks'].append(label+'_preserves_original_database_exception')
                else:
                    raise AssertionError('expected_sql_failure_did_not_occur')
                check(snapshot(admin) == baseline, label+'_transaction_rolled_back')
                check_connection_and_locks(label)
                new_orphans = set(spool.iterdir()) - previous_dirs
                check(len(new_orphans) == 1, label+'_one_spool_orphan')
                state = read_json(new_orphans.pop()/'state.json')
                check(state['status'] == 'prepared' and state['post_attempts'] == 0
                      and state['provider_job_id'] is None, label+'_orphan_not_submitted')
                check(state['worker_job_id'] not in {r['worker_job_id'] for r in store.rows()},
                      label+'_orphan_absent_from_queue')

            stage = 'admission_restart_and_duplicates'
            first_video = video('first')
            first_env = {generation.SCENE_MODEL_ENV: 'gemini-p5-scene-first',
                         generation.REPORT_MODEL_ENV: 'gemini-p5-report-first'}
            with patch.dict(os.environ, first_env):
                first = flow.admit(store, first_video, '2026-10-01')
                expected_first = generation.from_environment()
            first_id = first['worker_job_id']
            first_flow = app.execute('SELECT * FROM cctv_jobs.flows WHERE worker_job_id=%s',
                                     (first_id,)).fetchone()
            check(first_flow['generation_settings'] == expected_first and not first['already_present'],
                  'admission_pins_environment_in_jsonb')
            first_row = next(r for r in store.rows() if r['worker_job_id'] == first_id)
            check(read_json(spool/first_row['directory_id']/'flow-settings.json') == {
                'reference_date': '2026-10-01', 'generation_settings': expected_first},
                'admission_spool_receipt_matches_database')
            app.close()
            app = stack.enter_context(connect('cctv_app', 'db_app_password'))
            store = Store(app)
            second_env = {generation.SCENE_MODEL_ENV: 'gemini-p5-scene-second',
                          generation.REPORT_MODEL_ENV: 'gemini-p5-report-second'}
            with patch.dict(os.environ, second_env):
                duplicate = flow.admit(store, first_video, '2026-10-02')
                second = flow.admit(store, video('second'), '2026-10-02')
                expected_second = generation.from_environment()
            check(duplicate == {'worker_job_id': first_id, 'already_present': True},
                  'restart_duplicate_reuses_original_job')
            check(app.execute('SELECT * FROM cctv_jobs.flows WHERE worker_job_id=%s',
                (first_id,)).fetchone() == first_flow, 'restart_duplicate_preserves_full_flow_row')
            second_flow = app.execute('SELECT * FROM cctv_jobs.flows WHERE worker_job_id=%s',
                (second['worker_job_id'],)).fetchone()
            check(second_flow['generation_settings'] == expected_second
                  and expected_first != expected_second, 'new_input_uses_new_defaults_only')
            with patch.dict(os.environ, {generation.SCENE_MODEL_ENV: ''}):
                invalid_default_duplicate = flow.admit(store, first_video, '2026-10-03')
            check(invalid_default_duplicate == duplicate, 'duplicate_ignores_invalid_current_defaults')
            all_rows = store.rows()
            check(len(all_rows) == 3 and app.execute('SELECT count(*) AS n FROM cctv_jobs.flows').fetchone()['n'] == 3,
                  'only_legacy_and_two_new_jobs_exist')
            check(all(row['submit_attempts'] == 0 and row['state']['post_attempts'] == 0
                      and row['state']['gemini_api_calls'] == 0 for row in all_rows),
                  'all_jobs_have_zero_model_and_submission_attempts')
            check(all(guard.call_count == 0 for guard in external_guards), 'external_entry_points_not_called')
            report.update(status='passed', legacy_tables_preserved=5, migration_applications=2,
                          queue_rows=3, flow_rows=3, orphan_spools=3,
                          real_psycopg_transactions=True, real_application_role=True)
    except Exception as exc:
        report.update(status='failed', failed_stage=stage, error_type=type(exc).__name__)
        if isinstance(exc, AssertionError) and re.fullmatch(r'[a-z_]+', str(exc)):
            report['assertion'] = str(exc)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


def main(report_path):
    if report_path.exists():
        raise FileExistsError('Choose a fresh report path')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    project = 'cctv-p5-check-' + uuid.uuid4().hex[:12]
    base = Path(tempfile.mkdtemp(prefix='cctv-p5-', dir='/private/tmp'))
    report = {'status': 'running', 'project': project, 'database': TEST_DB,
              'application_database_modified': False, 'image_downloads': 0,
              'model_calls': 0, 'external_api_calls': 0, 'published_ports': 0,
              'limits': {'database_memory_mib': 512, 'validator_memory_mib': 256,
                         'database_cpus': 1, 'validator_cpus': 1,
                         'database_ready_seconds': 45, 'validator_seconds': 90}}
    launched = False
    command = None
    stage = 'read_cached_image_identifiers'

    def run(args, timeout=20):
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError('isolated_command_failed')
        return result.stdout

    def inventory():
        return {'containers': set(run(['docker', 'ps', '-aq']).split()),
                'volumes': set(run(['docker', 'volume', 'ls', '-q']).split())}

    try:
        compose_source = (ROOT/'infra/compose/rag/compose.yaml').read_text()
        images = re.findall(r'^\s+image:\s+(pgvector/pgvector:\S+@sha256:[a-f0-9]{64})\s*$',
                            compose_source, flags=re.MULTILINE)
        if len(images) != 1:
            raise RuntimeError('pinned_database_image_required')
        db_image = images[0]
        report['images'] = [db_image, WORKER_IMAGE]
        report['docker_version'] = run(['docker', 'info', '--format', '{{.ServerVersion}}'], 15).strip()
        run(['docker', 'image', 'inspect', '--format', '{{.Id}}', db_image, WORKER_IMAGE])
        previous = inventory()
        data, private = base/'data', base/'secrets'
        data.mkdir()
        private.mkdir(mode=0o700)
        for name in ('db_admin_password', 'db_app_password'):
            (private/name).write_text(secrets.token_hex(32)+'\n')
            (private/name).chmod(0o444)
        mounts = [f'{ROOT/name}:/code/{name}:ro' for name in (
            'apps/be_worker', 'apps/scene_description', 'apps/runpod_worker', 'apps/rag_index')]
        mounts.append(f'{Path(__file__).resolve()}:/code/tests/be_worker/check_generation_database.py:ro')
        composition = {
            'services': {
                'db': {'image': db_image, 'pull_policy': 'never', 'restart': 'no',
                    'mem_limit': '512m', 'cpus': 1,
                    'environment': {'POSTGRES_DB': 'cctv', 'POSTGRES_USER': 'postgres',
                        'POSTGRES_PASSWORD_FILE': '/run/secrets/db_admin_password',
                        'POSTGRES_INITDB_ARGS': '--auth-host=scram-sha-256 --auth-local=trust'},
                    'secrets': ['db_admin_password', 'db_app_password'], 'networks': ['database'],
                    'volumes': [f'{data}:/var/lib/postgresql/data',
                        f'{ROOT}/infra/compose/rag/init-db.sql:/docker-entrypoint-initdb.d/10-cctv.sql:ro'],
                    'healthcheck': {'test': ['CMD-SHELL', "pg_isready -U postgres -d cctv && psql -U postgres -d cctv -tAc \"SELECT EXISTS (SELECT FROM pg_roles WHERE rolname='cctv_app');\" | grep -qx t"],
                        'interval': '2s', 'timeout': '3s', 'retries': 20, 'start_period': '3s'}},
                'generation-check': {'image': WORKER_IMAGE, 'pull_policy': 'never',
                    'entrypoint': ['python'], 'working_dir': '/code', 'user': '0:0',
                    'mem_limit': '256m', 'cpus': 1, 'read_only': True, 'tmpfs': ['/tmp:size=64m'],
                    'environment': {'CCTV_P5_ISOLATED_POSTGRES': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                                    'PYTHONPATH': '/code'},
                    'networks': ['database'], 'secrets': ['db_admin_password', 'db_app_password'],
                    'volumes': mounts}},
            'networks': {'database': {'internal': True}},
            'secrets': {name: {'file': str(private/name)}
                        for name in ('db_admin_password', 'db_app_password')},
        }
        compose_file = base/'compose.json'
        compose_file.write_text(json.dumps(composition))
        command = ['docker', 'compose', '-p', project, '-f', str(compose_file)]
        stage = 'validate_isolated_compose'
        run(command+['config', '--quiet'])
        if run(command+['ps', '-aq']).strip():
            raise RuntimeError('isolated_project_name_already_used')
        launched = True
        stage = 'start_isolated_database'
        run(command+['up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '45', 'db'], 55)
        run(command+['exec', '-T', '--user', 'postgres', 'db', 'createdb', TEST_DB], 15)
        stage = 'run_real_database_checks'
        result = subprocess.run(command+['run', '--rm', '-T', '--no-deps', 'generation-check',
            '-B', '/code/tests/be_worker/check_generation_database.py', '--inside'],
            capture_output=True, text=True, timeout=90)
        report['database_checks'] = json.loads(result.stdout)
        if result.returncode or report['database_checks']['status'] != 'passed':
            raise RuntimeError('isolated_database_checks_failed')
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', failed_stage=stage, error_type=type(exc).__name__)
    finally:
        removed = not launched
        if launched and command:
            try:
                run(command+['down', '--remove-orphans', '--timeout', '5'], 30)
                removed = not run(command+['ps', '-aq']).strip()
            except Exception:
                removed = False
        report['test_containers_removed'] = removed
        if removed:
            shutil.rmtree(base)
            report['test_temporary_files_removed'] = True
        else:
            report['test_temporary_files_removed'] = False
            report['status'] = 'failed'
        if 'previous' in locals():
            try:
                after = inventory()
                report['preexisting_containers_preserved'] = previous['containers'] <= after['containers']
                report['preexisting_volumes_preserved'] = previous['volumes'] <= after['volumes']
                if not all(report[key] for key in ('preexisting_containers_preserved', 'preexisting_volumes_preserved')):
                    report['status'] = 'failed'
            except Exception:
                report['preexisting_inventory_verification'] = 'unavailable'
                report['status'] = 'failed'
        report['elapsed_seconds'] = round(time.monotonic()-started, 3)
        with report_path.open('x') as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--report', type=Path)
    group.add_argument('--inside', action='store_true')
    args = parser.parse_args()
    raise SystemExit(inside() if args.inside else main(args.report))
