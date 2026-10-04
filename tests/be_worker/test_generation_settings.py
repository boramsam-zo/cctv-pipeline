"""Synthetic generation settings checks: no DB, network, or model calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker import generation_settings as settings
from apps.be_worker.common import WorkerError
from apps.be_worker import report_jobs
from apps.scene_description import scene
from apps.rag_index import index


class SettingsTests(unittest.TestCase):
    def test_current_defaults_match_fixed_legacy_and_are_json_serializable(self):
        snapshot = settings.from_environment({})
        self.assertEqual(snapshot, settings.legacy_settings())
        self.assertEqual(json.loads(json.dumps(snapshot)), snapshot)

    def test_model_defaults_are_independent(self):
        for variable, field in ((settings.SCENE_MODEL_ENV, 'scene_model'),
                                (settings.REPORT_MODEL_ENV, 'report_model')):
            with self.subTest(variable=variable):
                snapshot = settings.from_environment({variable: 'gemini-example-preview'})
                expected = settings.legacy_settings()
                expected[field] = 'gemini-example-preview'
                self.assertEqual(snapshot, expected)

    def test_actual_environment_read_happens_at_each_admission(self):
        with patch.dict('os.environ', {settings.SCENE_MODEL_ENV: 'gemini-first'}, clear=True):
            first = settings.from_environment()
        with patch.dict('os.environ', {settings.SCENE_MODEL_ENV: 'gemini-second'}, clear=True):
            second = settings.from_environment()
            self.assertEqual(settings.validate(first), first)
        self.assertEqual(first['scene_model'], 'gemini-first')
        self.assertEqual(second['scene_model'], 'gemini-second')

    def test_blank_invalid_and_embedding_names_never_echo_values(self):
        invalid = ['', ' ', '\n', ' gemini-example', 'gemini-example ', 'gemini-',
                   'gemini-embedding-001', 'gemini-EMBEDDING-001', 'gemini-test/secret',
                   'sensitive-token-value', 'gemini-' + 'a' * 122, None, True, 3]
        for value in invalid:
            for variable in (settings.SCENE_MODEL_ENV, settings.REPORT_MODEL_ENV):
                with self.subTest(variable=variable, kind=type(value).__name__):
                    with self.assertRaises(WorkerError) as caught:
                        settings.from_environment({variable: value})
                    self.assertEqual(str(caught.exception), 'generation_model_invalid')

    def test_legacy_is_literal_and_returns_independent_copy(self):
        original = settings.legacy_settings()
        with patch.dict('os.environ', {settings.SCENE_MODEL_ENV: 'gemini-new'}, clear=True), \
             patch.object(scene, 'PROMPT_VERSION', 'future-scene'), \
             patch.object(report_jobs, 'VERSION', 'future-report'), \
             patch.object(settings, 'DEFAULT_MODEL', 'gemini-other'):
            self.assertEqual(settings.legacy_settings(), original)
        changed = settings.legacy_settings()
        changed['scene_model'] = 'gemini-changed'
        self.assertEqual(settings.legacy_settings(), original)

    def test_exact_schema_and_types(self):
        original = settings.from_environment({})
        variants = [None, [], {}, dict(original, unexpected='value'),
                    {key: value for key, value in original.items() if key != 'query_sha256'},
                    dict(original, scene_fps=True), dict(original, scene_fps=4.0),
                    dict(original, scene_model=None)]
        for value in variants:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(WorkerError, '^generation_settings_invalid$'):
                    settings.validate(value)
        for key in original.keys() - {'scene_model', 'report_model'}:
            value = copy.deepcopy(original)
            value[key] = 5 if key == 'scene_fps' else 'changed'
            with self.subTest(field=key):
                with self.assertRaisesRegex(WorkerError, '^generation_contract_changed$'):
                    settings.validate(value)

    def test_scene_prompt_schema_and_constructor_drift_rejected(self):
        snapshot = settings.from_environment({})
        schema = copy.deepcopy(scene.RESPONSE_SCHEMA)
        schema['properties']['summary']['description'] = 'Changed schema instruction'
        for name, changed in [('SYSTEM', scene.SYSTEM + '\nchanged'),
                              ('RESPONSE_SCHEMA', schema),
                              ('PROMPT_VERSION', 'changed-version')]:
            with self.subTest(constant=name), patch.object(scene, name, changed):
                with self.assertRaisesRegex(WorkerError, '^generation_contract_changed$'):
                    settings.validate(snapshot)
        with patch.object(scene, 'build_request', return_value=b'changed-constructor'):
            with self.assertRaisesRegex(WorkerError, '^generation_contract_changed$'):
                settings.validate(snapshot)

    def test_report_prompt_query_and_version_drift_rejected(self):
        snapshot = settings.from_environment({})
        cases = [('report_request', {'changed': 'instruction'}),
                 ('query_from_description', 'changed query')]
        for name, changed in cases:
            with self.subTest(function=name), patch.object(report_jobs, name, return_value=changed):
                with self.assertRaisesRegex(WorkerError, '^generation_contract_changed$'):
                    settings.validate(snapshot)
        with patch.object(report_jobs, 'VERSION', 'new-version'):
            with self.assertRaisesRegex(WorkerError, '^generation_contract_changed$'):
                settings.validate(snapshot)

    def test_embedding_profile_model_and_dimensions_remain_fixed(self):
        snapshot = settings.from_environment({})
        for name, changed in [('PROFILE', 'new-profile'), ('MODEL', 'gemini-embedding-other'),
                              ('DIMENSIONS', 3072)]:
            with self.subTest(constant=name), patch.object(index, name, changed):
                with self.assertRaisesRegex(WorkerError, '^generation_contract_changed$'):
                    settings.validate(snapshot)
                with self.assertRaisesRegex(WorkerError, '^generation_contract_changed$'):
                    settings.from_environment({})

    def test_validate_returns_copy_without_secrets(self):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'synthetic-secret'}, clear=True), \
             patch.object(settings, 'read_generation_key', side_effect=AssertionError('must not read')):
            snapshot = settings.from_environment()
            validated = settings.validate(snapshot)
        validated['scene_model'] = 'gemini-other'
        self.assertNotEqual(validated, snapshot)
        self.assertNotIn('synthetic-secret', json.dumps(snapshot))
        self.assertNotIn('/run/secrets', json.dumps(snapshot))
        self.assertFalse(any('key' in key or 'secret' in key for key in snapshot))


class SecretTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'synthetic-secret'
        patcher = patch.object(settings, 'KEY_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_unavailable(self):
        with self.assertRaises(WorkerError) as caught:
            settings.read_generation_key()
        self.assertEqual(str(caught.exception), 'generation_secret_unavailable')
        self.assertIsNone(caught.exception.__cause__)

    def test_missing_unreadable_and_invalid_utf8_never_fall_back_to_env(self):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'synthetic-env-value'}, clear=True):
            self.assert_unavailable()
            with patch.object(Path, 'read_text', side_effect=PermissionError('synthetic-secret-detail')):
                self.assert_unavailable()
            self.path.write_bytes(b'\xff')
            self.assert_unavailable()

    def test_blank_internal_whitespace_and_control_characters_rejected(self):
        for value in ['', '  \n\t', 'fake key', 'fake\nkey', 'fake\tkey', 'fake\x00key']:
            self.path.write_text(value)
            self.assert_unavailable()

    def test_every_read_observes_replaced_secret_file(self):
        self.path.write_text('synthetic-first-key\n')
        self.assertEqual(settings.read_generation_key(), 'synthetic-first-key')
        replacement = self.path.with_name('replacement')
        replacement.write_text('synthetic-second-key\n')
        replacement.replace(self.path)
        self.assertEqual(settings.read_generation_key(), 'synthetic-second-key')


if __name__ == '__main__':
    unittest.main()
