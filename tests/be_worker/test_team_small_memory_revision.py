"""Revision02 byte representation checks; Docker/SQL/resource calls are doubles."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.be_worker import test_team_small_saved_helpers as previous

DIRECTORY = previous.DIRECTORY.parent / 'small-memory-02'
REVISION = previous.load('test_small_saved_memory_revision', DIRECTORY / 'small-saved-rollout-01.py')


def config(before, *, strings=False):
    with mock.patch.object(previous, 'ROLLOUT', REVISION):
        result = previous.desired_config(before)
    if strings:
        for cfg in result['services'].values():
            for field in ('mem_limit', 'memswap_limit'):
                cfg[field] = str(cfg[field])
    return result


class SmallMemoryRevisionTests(unittest.TestCase):
    def test_integer_and_ascii_decimal_strings_preserve_exact_int64_bytes(self):
        for value in (0, 536870912, 805306368, 1073741824, 1610612736, 2**63 - 1):
            with self.subTest(value=value):
                self.assertEqual(REVISION.canonical_bytes(value), value)
                self.assertEqual(REVISION.canonical_bytes(str(value)), value)

    def test_zero_and_unlimited_swap_sentinel_are_not_conflated(self):
        for value in (0, '0'):
            self.assertEqual(REVISION.canonical_bytes(value, swap=True), 0)
        for value in (-1, '-1'):
            self.assertEqual(REVISION.canonical_bytes(value, swap=True), -1)
            with self.assertRaisesRegex(RuntimeError, 'compose_memory_byte_value_invalid'):
                REVISION.canonical_bytes(value)

    def test_invalid_types_suffixes_non_ascii_and_out_of_range_fail_closed(self):
        values = [None, True, False, 536870912.0, float('nan'), float('inf'), '',
                  '512m', '536870912.0', '1e9', ' 536870912', '536870912\n', '+536870912',
                  '５３６８７０９１２', '536_870_912', '--1', {}, [], 2**63, str(2**63), -2, '-2']
        for value in values:
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(RuntimeError, 'compose_memory_byte_value_invalid'):
                    REVISION.canonical_bytes(value)
                with self.assertRaisesRegex(RuntimeError, 'compose_memory_byte_value_invalid'):
                    REVISION.canonical_bytes(value, swap=True)

    def test_three_service_string_wire_values_match_integer_caps_and_diagnostics(self):
        before = previous.containers()
        values = REVISION.verify_desired(config(before, strings=True), before)
        self.assertEqual(set(values), {'be-api', 'be-worker', 'cctv-ui'})
        for service, record in values.items():
            self.assertEqual(record, {
                'compose_memory_raw_type': 'str',
                'compose_memory_bytes': before[service]['HostConfig']['Memory'],
                'expected_memory_bytes': before[service]['HostConfig']['Memory'],
                'compose_swap_raw_type': 'str',
                'compose_swap_bytes': before[service]['HostConfig']['MemorySwap'],
                'expected_swap_bytes': before[service]['HostConfig']['MemorySwap'],
            })
        integers = REVISION.verify_desired(config(before), before)
        self.assertTrue(all(record['compose_memory_raw_type'] == 'int' for record in integers.values()))

    def test_missing_null_or_non_integer_fields_are_rejected_even_if_expected_zero(self):
        before = previous.containers()
        before['be-api']['HostConfig'].update(Memory=0, MemorySwap=0)
        for field in ('mem_limit', 'memswap_limit'):
            for invalid in (None, False, 0.0, '0m', 'null'):
                with self.subTest(field=field, invalid=invalid):
                    value = config(before)
                    value['services']['be-api'][field] = invalid
                    with self.assertRaisesRegex(RuntimeError, 'compose_memory_byte_value_invalid'):
                        REVISION.verify_desired(value, before)
            value = config(before)
            del value['services']['be-api'][field]
            with self.assertRaisesRegex(RuntimeError, 'compose_memory_byte_value_invalid'):
                REVISION.verify_desired(value, before)

    def test_real_memory_or_swap_byte_changes_are_rejected(self):
        before = previous.containers()
        for field, error in (('mem_limit', 'compose_memory_limit_changed'),
                             ('memswap_limit', 'compose_memory_swap_limit_changed')):
            with self.subTest(field=field):
                value = config(before, strings=True)
                value['services']['be-worker'][field] = str(int(value['services']['be-worker'][field]) + 1)
                with self.assertRaisesRegex(RuntimeError, error):
                    REVISION.verify_desired(value, before)

    def test_zero_memory_and_swap_negative_one_require_matching_runtime_semantics(self):
        before = previous.containers()
        before['be-api']['HostConfig'].update(Memory=0, MemorySwap=-1)
        result = REVISION.verify_desired(config(before, strings=True), before)
        self.assertEqual(result['be-api']['compose_memory_bytes'], 0)
        self.assertEqual(result['be-api']['compose_swap_bytes'], -1)
        wrong = config(before, strings=True)
        wrong['services']['be-api']['memswap_limit'] = '0'
        with self.assertRaisesRegex(RuntimeError, 'compose_memory_swap_limit_changed'):
            REVISION.verify_desired(wrong, before)
        wrong_runtime = deepcopy(before)
        wrong_runtime['be-api']['HostConfig']['MemorySwap'] = False
        with self.assertRaisesRegex(RuntimeError, 'existing_runtime_memory_byte_value_invalid'):
            REVISION.verify_desired(config(before), wrong_runtime)

    def test_existing_mount_image_environment_and_cap_checks_are_unchanged(self):
        with mock.patch.object(previous, 'ROLLOUT', REVISION):
            prior = previous.SmallSavedHelpersTests()
            prior.test_desired_compose_rejects_missing_secret_or_changed_memory()
            prior.test_runtime_keeps_all_four_limits_command_and_legacy_mounts()
        before = previous.containers()
        value = config(before, strings=True)
        value['services']['be-api']['environment']['KEEP'] = 'changed'
        with self.assertRaisesRegex(RuntimeError, 'unexpected_compose_environment_change'):
            REVISION.verify_desired(value, before)
        value = config(before, strings=True)
        value['services']['cctv-ui']['image'] = 'different'
        with self.assertRaisesRegex(RuntimeError, 'ui_image_changed'):
            REVISION.verify_desired(value, before)

    def _string_apply(self, *, oom_failure=False):
        original_config = previous.desired_config
        def strings(before):
            value = original_config(before)
            for cfg in value['services'].values():
                for field in ('mem_limit', 'memswap_limit'):
                    cfg[field] = str(cfg[field])
            return value
        with mock.patch.object(previous, 'ROLLOUT', REVISION), \
             mock.patch.object(previous, 'desired_config', side_effect=strings):
            return previous.SmallSavedHelpersTests()._rollout_apply(oom_failure=oom_failure)

    def test_apply_string_wire_success_stops_before_up_and_preserves_zero_model_scope(self):
        calls, writes = self._string_apply()
        self.assertLess(next(i for i, args in enumerate(calls) if args[0] == 'stop'),
                        next(i for i, args in enumerate(calls) if 'up' in args))
        summary = next(value for name, value, _ in writes if name == 'small-saved-installed-01.json')
        self.assertEqual(summary['model_calls'], 0)
        self.assertFalse(summary['cpu_retrieval_ready'])
        self.assertTrue(all(v['compose_memory_raw_type'] == 'str'
                            for v in summary['selected_resource_byte_comparison'].values()))
        self.assertFalse(any('/connection' in str(args) or 'POST' in str(args) for args in calls))

    def test_apply_string_wire_still_stops_on_oom_and_does_not_mark_installed(self):
        _, writes = self._string_apply(oom_failure=True)
        self.assertNotIn('small-saved-installed-01.json', [name for name, _, _ in writes])

    def test_apply_invalid_or_changed_config_fails_before_stop_or_permanent_attempt(self):
        before = previous.containers()
        baseline = {table: {'count': 2, 'md5': 'old'} for table in REVISION.BASE.TABLES}
        baseline.update({'p6-final-trial': {'historical': True}, REVISION.HOUR_NAME: previous.hour()})
        observer = mock.Mock()
        observer.read_snapshot.return_value = {
            'host': {'memory_available_mib': 700},
            'services': {service: {'id': obj['Id']} for service, obj in before.items()}}
        def inspect(name):
            for service, container in REVISION.BASE.NAMES.items():
                if name == container:
                    return before[service]
            return {'Id': next(obj['Image'] for obj in before.values() if obj['Config']['Image'] == name)}
        for invalid, error in (('536870913', 'compose_memory_limit_changed'),
                               ('512m', 'compose_memory_byte_value_invalid')):
            value = config(before, strings=True)
            value['services']['be-api']['mem_limit'] = invalid
            with self.subTest(value=invalid), tempfile.TemporaryDirectory() as raw, \
                 mock.patch.object(REVISION, 'OUTPUT', Path(raw)), \
                 mock.patch.object(REVISION, 'verify_release', return_value=previous.IMAGE_ID), \
                 mock.patch.object(REVISION, 'check_guard_marker'), \
                 mock.patch.object(REVISION, 'expired_hour', return_value=previous.hour()), \
                 mock.patch.object(REVISION, 'load_file', return_value=observer), \
                 mock.patch.object(REVISION.os, 'geteuid', return_value=0), \
                 mock.patch.object(REVISION.BASE, 'inspect', side_effect=inspect), \
                 mock.patch.object(REVISION.BASE, 'docker', return_value=json.dumps(value)) as docker, \
                 mock.patch.object(REVISION.BASE, 'snapshot', return_value=baseline), \
                 mock.patch.object(REVISION.BASE, 'no_runnable_work'), \
                 mock.patch.object(REVISION.BASE, 'load_compose_environment', return_value={'PATH': '/bin'}), \
                 mock.patch.object(REVISION.BASE, 'save_new') as save, \
                 mock.patch.object(REVISION.BASE, 'hold_worker_locks') as locks, \
                 mock.patch.object(REVISION, 'stop_on_failure') as stop_failure, \
                 mock.patch.object(Path, 'is_file', return_value=True), \
                 mock.patch.object(Path, 'read_bytes', return_value=b'prior-compose'), \
                 mock.patch('builtins.print') as printed:
                with self.assertRaisesRegex(RuntimeError, error):
                    REVISION.main(['--apply'])
                save.assert_not_called()
                locks.assert_not_called()
                stop_failure.assert_not_called()
                if error == 'compose_memory_limit_changed':
                    printed.assert_called_once()
                    diagnostic = json.loads(printed.call_args.args[0])
                    self.assertEqual(diagnostic['status'], 'small_saved_compose_memory_mismatch')
                    comparison = diagnostic['selected_resource_byte_comparison']
                    self.assertEqual(comparison['compose_memory_raw_type'], 'str')
                    self.assertEqual(comparison['compose_memory_bytes'], 536870913)
                    self.assertEqual(comparison['expected_memory_bytes'], 536870912)
                    self.assertEqual(set(diagnostic), {'status', 'service',
                        'selected_resource_byte_comparison', 'services_changed', 'model_calls'})
                else:
                    printed.assert_not_called()
                docker.assert_called_once()
                self.assertIn('config', docker.call_args.args)
                self.assertFalse(any(arg in ('up', 'stop', 'run') for arg in docker.call_args.args))


if __name__ == '__main__':
    unittest.main()
