"""Read-only schema cache isolation; no provider, model, or network calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
from threading import Barrier, get_ident
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from apps.scene_description import scene, team_scene
from apps.be_worker import ui_validation


FIXTURES = Path(__file__).parent / 'fixtures' / 'rag_v2'


class ContractCacheTests(unittest.TestCase):
    def setUp(self):
        ui_validation._check_schema_definition.cache_clear()
        self.addCleanup(ui_validation._check_schema_definition.cache_clear)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.original = {}
        for name in team_scene.CONTRACT_HASHES:
            raw = (team_scene.CONTRACT_DIRECTORY / name).read_bytes()
            self.original[name] = raw
            (self.root / name).write_bytes(raw)
        self.value = scene.parse((FIXTURES / 'synthetic-event.json').read_bytes())
        self.value['event_id'] = 'b' * 32 + '_event_000'

    def contract(self):
        with ui_validation.readonly_validation():
            return team_scene.contract(self.root)

    def validate_event(self, value):
        with ui_validation.readonly_validation():
            return team_scene.validate_event(value, self.value['event_id'],
                                             contract_directory=self.root)

    def test_repeated_reads_check_definition_once_but_read_both_files_each_time(self):
        with patch.object(Draft202012Validator, 'check_schema',
                          wraps=Draft202012Validator.check_schema) as check, \
                patch.object(team_scene, '_read', wraps=team_scene._read) as read:
            for _ in range(4):
                schema, prompt, validator = self.contract()
                self.assertEqual(schema, scene.parse(self.original['event.schema.json']))
                self.assertEqual(prompt, self.original['VLM_PROMPT.md'].decode())
                validator.validate(self.value)
            self.assertEqual(check.call_count, 1)
            self.assertEqual(read.call_count, 8)

    def test_returned_schema_and_validator_mutations_never_pollute_next_call(self):
        schema, _, validator = self.contract()
        schema.clear()
        validator.schema['type'] = 'integer'
        second, _, fresh = self.contract()
        self.assertIsNot(schema, second)
        self.assertIsNot(validator, fresh)
        self.assertEqual(second, scene.parse(self.original['event.schema.json']))
        fresh.validate(self.value)
        with self.assertRaises(ValidationError):
            fresh.validate(dict(self.value, description=''))

    def test_warm_cache_still_rejects_changed_schema_or_prompt(self):
        self.contract()
        for name in team_scene.CONTRACT_HASHES:
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(self.original[name] + b'\n')
                with self.assertRaisesRegex(ValueError, 'team_scene_contract_changed'):
                    self.contract()
                path.write_bytes(self.original[name])
        self.assertEqual(ui_validation._check_schema_definition.cache_info().hits, 0)

    def test_warm_cache_still_rejects_symlinks_and_oversized_files(self):
        self.contract()
        for name in team_scene.CONTRACT_HASHES:
            with self.subTest(name=name):
                path = self.root / name
                target = self.root / ('saved-' + name)
                path.rename(target)
                path.symlink_to(target)
                with self.assertRaisesRegex(ValueError, 'team_scene_file_invalid'):
                    self.contract()
                path.unlink()
                target.rename(path)
                path.write_bytes(b' ' * 65537)
                with self.assertRaisesRegex(ValueError, 'size limit'):
                    self.contract()
                path.write_bytes(self.original[name])

    def test_each_new_pinned_schema_version_is_checked_and_cache_is_bounded(self):
        original = scene.parse(self.original['event.schema.json'])
        with patch.object(Draft202012Validator, 'check_schema',
                          wraps=Draft202012Validator.check_schema) as check:
            for version in range(10):
                schema = dict(original, description=f'Synthetic contract {version}')
                raw = scene.encoded(schema)
                (self.root / 'event.schema.json').write_bytes(raw)
                with patch.dict(team_scene.CONTRACT_HASHES,
                                {'event.schema.json': scene.sha(raw)}):
                    self.contract()
                    self.contract()
            self.assertEqual(check.call_count, 10)
        info = ui_validation._check_schema_definition.cache_info()
        self.assertEqual(info.maxsize, 8)
        self.assertEqual(info.currsize, 8)

    def test_invalid_definition_is_not_cached_even_with_matching_file_hash(self):
        self.contract()
        raw = scene.encoded({'type': 'not-a-json-schema-type'})
        (self.root / 'event.schema.json').write_bytes(raw)
        with patch.dict(team_scene.CONTRACT_HASHES, {'event.schema.json': scene.sha(raw)}), \
                patch.object(Draft202012Validator, 'check_schema',
                             wraps=Draft202012Validator.check_schema) as check:
            for _ in range(2):
                with self.assertRaises(SchemaError):
                    self.contract()
            self.assertEqual(check.call_count, 2)
        self.assertEqual(ui_validation._check_schema_definition.cache_info().currsize, 1)

    def test_every_event_keeps_schema_and_semantic_validation_when_cache_is_warm(self):
        with patch.object(Draft202012Validator, 'check_schema',
                          wraps=Draft202012Validator.check_schema) as check:
            for _ in range(2):
                self.validate_event(self.value)
                with self.assertRaises(ValidationError):
                    self.validate_event(dict(self.value, description=''))
                wrong_location = deepcopy(self.value)
                wrong_location['location']['road_type'] = 'urban'
                with self.assertRaisesRegex(ValueError, 'unprovided_location_or_time'):
                    self.validate_event(wrong_location)
            self.assertEqual(check.call_count, 1)

    def test_legacy_and_structured_request_bytes_match_before_cache_change(self):
        bundle = ({'job_id': 'b' * 32, 'event_id': 'event_000'}, {},
                  {'frame_role': 'unannotated_frame'},
                  {'clip': b'synthetic clip', 'frame': b'synthetic frame',
                   'roi_clip': b'synthetic roi'}, None)
        expected = {
            None: '936f41571240ad5af14b71468183ad1f4907d98020c64835c3be15e849f80b25',
            team_scene.STRUCTURED_RESPONSE_VERSION:
                'df6053dab6d486415e63d1da0c35164f785d3b87ecb42920bdad7e09e93c39a9',
        }
        for version, digest in expected.items():
            with self.subTest(version=version):
                for _ in range(2):
                    data = team_scene.build_request(bundle, 'gemini-3.5-flash-lite',
                        contract_directory=self.root, response_format_version=version)
                    self.assertEqual(scene.sha(data), digest)

    def test_execution_source_hash_and_outside_scope_definition_checks_are_preserved(self):
        # Read optimization never rewrites the installed execution source;
        # an intentional new provider format is a separately deployed version.
        source_before = Path(team_scene.__file__).read_bytes()
        self.contract()
        before = ui_validation._check_schema_definition.cache_info()
        with patch.object(Draft202012Validator, 'check_schema',
                          wraps=Draft202012Validator.check_schema) as check:
            team_scene.contract(self.root)
            team_scene.contract(self.root)
            self.assertEqual(check.call_count, 2)
        self.assertEqual(ui_validation._check_schema_definition.cache_info(), before)
        self.assertEqual(Path(team_scene.__file__).read_bytes(), source_before)

    def test_nested_scope_resets_after_errors_and_installs_only_once(self):
        self.contract()
        wrapper = team_scene.contract
        self.assertFalse(ui_validation._read_only.get())
        with ui_validation.readonly_validation():
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                with ui_validation.readonly_validation():
                    self.assertTrue(ui_validation._read_only.get())
                    raise RuntimeError('synthetic failure')
            self.assertTrue(ui_validation._read_only.get())
        self.assertFalse(ui_validation._read_only.get())
        self.assertIs(team_scene.contract, wrapper)

    def test_simultaneous_read_thread_does_not_change_worker_validation(self):
        self.contract()  # Warm only the read-side definition cache.
        barrier = Barrier(2)
        checked_threads = []
        original_check = Draft202012Validator.check_schema

        def check(schema):
            checked_threads.append(get_ident())
            return original_check(schema)

        def read_view():
            with ui_validation.readonly_validation():
                barrier.wait(timeout=5)
                for _ in range(2):
                    team_scene.contract(self.root)
                barrier.wait(timeout=5)
            self.assertFalse(ui_validation._read_only.get())
            return get_ident()

        def worker():
            barrier.wait(timeout=5)
            self.assertFalse(ui_validation._read_only.get())
            for _ in range(2):
                team_scene.contract(self.root)
            barrier.wait(timeout=5)
            return get_ident()

        with patch.object(Draft202012Validator, 'check_schema', side_effect=check), \
                ThreadPoolExecutor(max_workers=2) as pool:
            read_future, worker_future = pool.submit(read_view), pool.submit(worker)
            read_id, worker_id = read_future.result(), worker_future.result()
        self.assertNotEqual(read_id, worker_id)
        self.assertEqual(checked_threads, [worker_id, worker_id])


if __name__ == '__main__':
    unittest.main()
