"""Local doubles for the small saved-result rollout; no Docker/DB/model calls."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'artifacts/be-worker/teammate-rag-integration-20261003-01/small-memory-01'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROLLOUT = load('test_small_saved_rollout', DIRECTORY / 'small-saved-rollout-01.py')
SCHEMA = load('test_small_saved_schema', DIRECTORY / 'small-saved-schema-01.py')
IMAGE_ID = 'sha256:' + '5' * 64


def hour():
    return {'schema': 'cctv-hour-session-v1', 'name': ROLLOUT.HOUR_NAME,
            'started_at': 1000.0, 'deadline_at': 4600.0, 'stopped': False,
            'bindings': {'saved-job': {'historical': True}}, 'claims': [{'historical': True}]}


def containers():
    result = {}
    for service in ('be-api', 'be-worker', 'cctv-ui', 'db'):
        mem = ROLLOUT.EXPECTED_MEMORY.get(service, 0)
        app = 'cctv_ui' if service == 'cctv-ui' else 'be_worker'
        result[service] = {
            'Id': 'old-' + service, 'Image': 'image-' + service,
            'State': {'Running': True, 'StartedAt': '2026-10-03T01:00:00Z'},
            'HostConfig': {'Memory': mem, 'MemorySwap': mem * 2,
                           'NanoCpus': 0, 'CpuPeriod': 0, 'CpuQuota': 0,
                           'CpuShares': 0, 'CpusetCpus': ''},
            'Config': {'Image': 'old:' + service, 'Env': ['KEEP=yes'],
                       'Entrypoint': None, 'Cmd': ['python', '-m', 'apps.be_worker.web_api'],
                       'User': '1000:1000',
                       'Labels': {'com.docker.compose.project.config_files': '/opt/original.compose.json'}},
            'Mounts': [] if service == 'db' else [
                {'Destination': '/code/apps/' + app, 'Type': 'bind',
                 'Source': '/opt/old-source/' + app, 'RW': False},
                {'Destination': '/spool', 'Type': 'bind', 'Source': '/srv/spool', 'RW': True},
                {'Destination': '/run/secrets/token', 'Type': 'bind', 'Source': '/srv/token', 'RW': False}],
        }
    return result


def desired_config(before):
    services = {}
    for service in ('be-api', 'be-worker', 'cctv-ui'):
        obj = before[service]
        mounts = ROLLOUT.expected_mounts(obj, service)
        services[service] = {
            'image': ROLLOUT.IMAGE if service != 'cctv-ui' else obj['Config']['Image'],
            'environment': dict(ROLLOUT.BASE.environment(obj), **(
                ROLLOUT.ADDITIONS if service != 'cctv-ui' else {})),
            'mem_limit': obj['HostConfig']['Memory'], 'memswap_limit': obj['HostConfig']['MemorySwap'],
            'volumes': [{'type': kind, 'source': source, 'target': target, 'read_only': not rw}
                        for target, (kind, source, rw) in mounts.items()],
        }
    return {'services': services}


def after_containers(before):
    after = deepcopy(before)
    for service in ('be-api', 'be-worker', 'cctv-ui'):
        obj = after[service]
        obj['Id'] = 'new-' + service
        obj['Mounts'] = [{'Destination': target, 'Type': kind, 'Source': source, 'RW': rw}
                         for target, (kind, source, rw) in ROLLOUT.expected_mounts(before[service], service).items()]
        if service != 'cctv-ui':
            obj['Image'] = IMAGE_ID
            obj['Config']['Env'] = [key + '=' + value for key, value in dict(
                ROLLOUT.BASE.environment(before[service]), **ROLLOUT.ADDITIONS).items()]
    return after


def release_files(root):
    files = {}
    for index in range(139):
        path = root / ('file-' + str(index))
        path.write_text(str(index))
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for app in ('be_worker', 'scene_description', 'team_rag', 'cctv_ui'):
        (root / 'source/apps' / app).mkdir(parents=True)
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps({'files': files}))
    image = {'status': 'team_saved_dependency_image_prepared', 'image': ROLLOUT.IMAGE,
             'image_id': IMAGE_ID, 'code_package_imports_verified': True,
             'model_libraries_imported': False, 'cpu_retrieval_ready': False}
    (root / 'saved-image-receipt-01.json').write_text(json.dumps(image))
    pg = {'status': 'team_isolated_postgres_verified', 'tests': 4, 'failures': 0,
          'errors': 0, 'skipped': 0, 'image_id': IMAGE_ID, 'model_libraries_imported': False,
          'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    (root / 'isolated-postgres-01.json').write_text(json.dumps(pg))
    return image, pg


class SmallSavedHelpersTests(unittest.TestCase):
    def test_saved_release_verifies_139_files_and_pg_without_any_model_folder(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            release_files(root)
            with mock.patch.object(ROLLOUT, 'HERE', root), mock.patch.object(ROLLOUT, 'SOURCE', root / 'source'), \
                 mock.patch.object(ROLLOUT.BASE, 'inspect', return_value={'Id': IMAGE_ID}) as inspect:
                result = ROLLOUT.verify_source_image_and_pg()
            self.assertEqual(result[0], IMAGE_ID)
            inspect.assert_called_once_with(ROLLOUT.IMAGE)
            self.assertFalse((root / 'model').exists())

    def test_modified_frozen_source_or_wrong_pg_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            _, pg = release_files(root)
            with mock.patch.object(ROLLOUT, 'HERE', root), mock.patch.object(ROLLOUT, 'SOURCE', root / 'source'), \
                 mock.patch.object(ROLLOUT.BASE, 'inspect', return_value={'Id': IMAGE_ID}):
                pg['image_id'] = 'different-image'
                (root / 'isolated-postgres-01.json').write_text(json.dumps(pg))
                with self.assertRaisesRegex(RuntimeError, 'isolated_postgres_receipt'):
                    ROLLOUT.verify_source_image_and_pg()
                (root / 'file-7').write_text('tampered')
                with self.assertRaisesRegex(RuntimeError, 'release_file_mismatch'):
                    ROLLOUT.verify_source_image_and_pg()

    def test_saved_image_cannot_claim_e5_readiness_or_model_imports(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            image, _ = release_files(root)
            with mock.patch.object(ROLLOUT, 'HERE', root), mock.patch.object(ROLLOUT, 'SOURCE', root / 'source'), \
                 mock.patch.object(ROLLOUT.BASE, 'inspect', return_value={'Id': IMAGE_ID}):
                for key in ('cpu_retrieval_ready', 'model_libraries_imported'):
                    invalid = dict(image, **{key: True})
                    (root / 'saved-image-receipt-01.json').write_text(json.dumps(invalid))
                    with self.assertRaisesRegex(RuntimeError, 'prepared_saved_image'):
                        ROLLOUT.verify_source_image_and_pg()

    def test_hour_requires_persisted_expired_window_using_database_clock(self):
        with mock.patch.object(ROLLOUT.BASE, 'sql', return_value={'payload': hour(), 'observed_at': 5000.0}) as sql:
            self.assertEqual(ROLLOUT.expired_hour(), hour())
            self.assertIn('clock_timestamp()', sql.call_args.args[0])
        for response in ({'payload': None, 'observed_at': 5000.0},
                         {'payload': hour(), 'observed_at': 4599.0},
                         {'payload': hour(), 'observed_at': True}):
            with mock.patch.object(ROLLOUT.BASE, 'sql', return_value=response):
                with self.assertRaises(RuntimeError):
                    ROLLOUT.expired_hour()

    def test_changed_hour_bindings_or_claims_are_preserved_and_rejected(self):
        changed = hour()
        changed['claims'].append({'new': True})
        with mock.patch.object(ROLLOUT.BASE, 'sql', return_value={'payload': changed, 'observed_at': 5000.0}):
            with self.assertRaisesRegex(RuntimeError, 'existing_hour_or_claims_changed'):
                ROLLOUT.expired_hour(hour())

    def test_hour_guard_marker_must_exist_in_the_same_shared_spool(self):
        before = containers()
        with tempfile.TemporaryDirectory() as raw:
            for service in ('be-api', 'be-worker'):
                before[service]['Mounts'][1]['Source'] = raw
            with self.assertRaisesRegex(RuntimeError, 'existing_hour_guard_marker_required'):
                ROLLOUT.check_guard_marker(before)
            (Path(raw) / '.hour-session.json').write_text(json.dumps(
                {'schema': 'cctv-hour-session-v1', 'name': ROLLOUT.HOUR_NAME}))
            ROLLOUT.check_guard_marker(before)
            before['be-api']['Mounts'][1]['Source'] = '/different/spool'
            with self.assertRaisesRegex(RuntimeError, 'spool_mounts_differ'):
                ROLLOUT.check_guard_marker(before)

    def test_override_preserves_small_caps_and_avoids_model_mount(self):
        before = containers()
        ROLLOUT.check_limits(before)
        value = ROLLOUT.override(before)
        for service, cfg in value['services'].items():
            self.assertEqual(cfg['mem_limit'], before[service]['HostConfig']['Memory'])
            self.assertEqual(cfg['memswap_limit'], before[service]['HostConfig']['MemorySwap'])
            self.assertFalse(any(v['target'].startswith('/models/') for v in cfg['volumes']))
            self.assertNotIn('cpus', cfg)
        self.assertEqual(value['services']['be-worker']['environment']['RAG_MODEL_PATH'], '/models/team-e5-small')
        self.assertTrue(all(value['services']['be-worker']['environment'][key] == val
                            for key, val in ROLLOUT.BASE.CPU_ENV.items()))

    def test_desired_compose_rejects_missing_secret_or_changed_memory(self):
        before = containers()
        config = desired_config(before)
        ROLLOUT.verify_desired(config, before)
        config['services']['be-worker']['volumes'] = [v for v in config['services']['be-worker']['volumes']
                                                      if v['target'] != '/run/secrets/token']
        with self.assertRaisesRegex(RuntimeError, 'unexpected_compose_mount_change'):
            ROLLOUT.verify_desired(config, before)
        config = desired_config(before)
        config['services']['be-worker']['mem_limit'] = 1536 * 1024**2
        with self.assertRaisesRegex(RuntimeError, 'compose_memory_limit_changed'):
            ROLLOUT.verify_desired(config, before)

    def test_runtime_keeps_all_four_limits_command_and_legacy_mounts(self):
        before = containers()
        after = after_containers(before)
        def inspect(name):
            return after[next(service for service, value in ROLLOUT.BASE.NAMES.items() if value == name)]
        with mock.patch.object(ROLLOUT.BASE, 'inspect', side_effect=inspect):
            ROLLOUT.check_after(before, IMAGE_ID)
            after['db']['HostConfig']['MemorySwap'] = -1
            with self.assertRaisesRegex(RuntimeError, 'runtime_resource_limit_changed'):
                ROLLOUT.check_after(before, IMAGE_ID)
            after['db']['HostConfig']['MemorySwap'] = before['db']['HostConfig']['MemorySwap']
            after['be-api']['Config']['User'] = 'root'
            with self.assertRaisesRegex(RuntimeError, 'runtime_command_or_user_changed'):
                ROLLOUT.check_after(before, IMAGE_ID)

    def test_atomic_schema_checks_expired_unchanged_hour_after_lock_before_ddl(self):
        baseline = {table: {'count': 2, 'md5': 'old'} for table in SCHEMA.SCHEMA.LEGACY_TABLES}
        baseline.update({'p6-final-trial': {'historical': True}, ROLLOUT.HOUR_NAME: hour()})
        value = SCHEMA.transaction_sql('CREATE TABLE saved_probe(id int);', baseline)
        self.assertLess(value.index('IN SHARE MODE'), value.index('DO $saved_window$'))
        self.assertLess(value.index('saved_case_hour_not_expired_or_changed'), value.index('CREATE TABLE saved_probe'))
        self.assertIn('clock_timestamp()', value)
        self.assertIn('existing_trial_or_hour_changed', value)
        self.assertNotIn('UPDATE cctv_jobs.service_state', value)
        self.assertEqual(value.count('COMMIT;'), 1)

    def _rollout_apply(self, *, oom_failure=False):
        before = containers()
        after = after_containers(before)
        baseline = {table: {'count': 2, 'md5': 'old'} for table in ROLLOUT.BASE.TABLES}
        baseline.update({'p6-final-trial': {'historical': True}, ROLLOUT.HOUR_NAME: hour()})
        live = False
        docker_calls = []
        writes = []
        def inspect(name):
            for service, value in ROLLOUT.BASE.NAMES.items():
                if name == value:
                    return (after if live else before)[service]
            return {'Id': next(obj['Image'] for obj in before.values() if obj['Config']['Image'] == name)}
        def docker(*args, **kwargs):
            nonlocal live
            docker_calls.append(args)
            if 'config' in args:
                return json.dumps(desired_config(before))
            if 'up' in args:
                live = True
            if args[0] == 'exec':
                if '/_stcore/health' in args[-1]:
                    return 'ok'
                return json.dumps({'/report-pipeline': {'pipeline_id': 'team-rag-v1',
                    'document_reference_date_supported': False}, '/jobs': [1, 2], '/selections': [1, 2]})
            return ''
        def resource(objects):
            return {'host': {'memory_available_mib': 700},
                    'services': {service: {'id': obj['Id']} for service, obj in objects.items()}}
        observer = mock.Mock()
        observer.read_snapshot.side_effect = [resource(before), resource(before), resource(after)]
        observer.check_oom_delta.side_effect = ([{'host_oom_delta': 0}, RuntimeError('host_oom_counter_changed')]
                                              if oom_failure else [{'host_oom_delta': 0}, {'host_oom_delta': 0}])
        with tempfile.TemporaryDirectory() as raw, \
             mock.patch.object(ROLLOUT, 'HERE', Path(raw)), mock.patch.object(ROLLOUT, 'OUTPUT', Path(raw)), \
             mock.patch.object(ROLLOUT, 'verify_release', return_value=IMAGE_ID), \
             mock.patch.object(ROLLOUT, 'check_guard_marker'), \
             mock.patch.object(ROLLOUT, 'load_file', return_value=observer), \
             mock.patch.object(ROLLOUT.os, 'geteuid', return_value=0), \
             mock.patch.object(ROLLOUT.BASE, 'inspect', side_effect=inspect), \
             mock.patch.object(ROLLOUT.BASE, 'docker', side_effect=docker), \
             mock.patch.object(ROLLOUT.BASE, 'snapshot', return_value=baseline), \
             mock.patch.object(ROLLOUT.BASE, 'sql', side_effect=lambda query: (
                 {'payload': hour(), 'observed_at': 5000.0} if 'clock_timestamp()));' in query else {'fresh_idle': True})), \
             mock.patch.object(ROLLOUT.BASE, 'no_runnable_work'), \
             mock.patch.object(ROLLOUT.BASE, 'hold_worker_locks', side_effect=nullcontext), \
             mock.patch.object(ROLLOUT.BASE, 'load_compose_environment', return_value={'PATH': '/bin'}), \
             mock.patch.object(ROLLOUT.BASE, 'save_new', side_effect=lambda p, v, **k: writes.append((p.name, v, k))), \
             mock.patch.object(Path, 'is_file', return_value=True), \
             mock.patch.object(Path, 'read_bytes', return_value=b'prior-compose'), \
             mock.patch.object(ROLLOUT.time, 'sleep'), \
             mock.patch.object(ROLLOUT, 'stop_on_failure') as stopped, \
             mock.patch('builtins.print'):
            if oom_failure:
                with self.assertRaisesRegex(RuntimeError, 'host_oom_counter_changed'):
                    ROLLOUT.main(['--apply'])
                stopped.assert_called_once_with('small-saved-verification-failure-01.json')
            else:
                result = ROLLOUT.main(['--apply'])
                self.assertFalse(result['actual_cpu_retrieval_ready'])
                self.assertEqual(result['phase'], 'saved_case_only')
                stopped.assert_not_called()
        self.assertTrue(any(name == 'rollback-state-01.json' and kwargs.get('private') is True
                            for name, _, kwargs in writes))
        return docker_calls, writes

    def test_apply_stops_old_services_before_start_and_reads_only_local_metadata(self):
        calls, writes = self._rollout_apply()
        stop = next(i for i, args in enumerate(calls) if args[0] == 'stop')
        up = next(i for i, args in enumerate(calls) if 'up' in args)
        self.assertLess(stop, up)
        self.assertIn('small-saved-installed-01.json', [name for name, _, _ in writes])
        self.assertNotIn('installed.json', [name for name, _, _ in writes])
        self.assertFalse(any('/connection' in str(args) or 'POST' in str(args) for args in calls))

    def test_apply_oom_delta_failure_stops_api_worker_and_never_marks_installed(self):
        _, writes = self._rollout_apply(oom_failure=True)
        self.assertNotIn('small-saved-installed-01.json', [name for name, _, _ in writes])


if __name__ == '__main__':
    unittest.main()
