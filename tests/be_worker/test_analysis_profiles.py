"""Pure P6 identity tests; small recorded manifests only, no checkpoint loading."""
from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from apps.be_worker import analysis_profiles as profiles

ROOT = Path(__file__).resolve().parents[2]
INPUT = '6c8233026b1393dd1a7287fb73127fa8799b11337985b444b31f72091295e459'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def reverse_objects(value):
    return json.loads(json.dumps(value), object_pairs_hook=lambda items: dict(reversed(items)))


class FrozenEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'artifacts/runpod/ab-runner-20261001-02/cctv-ab-runner-20261001-01.zip'
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != '168c1e7192a978a2c8806f4f61ab8de239df48a23360792c431b1890746e30fb':
            raise AssertionError('Frozen P3 runner changed')
        with zipfile.ZipFile(path) as bundle:
            cls.contract = json.loads(bundle.read('cctv-ab-runner-20261001-01/contract.json'))
            cls.sources = json.loads(bundle.read('cctv-ab-runner-20261001-01/source-files.json'))
        cls.image_bytes = (ROOT / 'artifacts/runpod/image-p4-20261001-01/context/image-manifest.json').read_bytes()
        cls.image = json.loads(cls.image_bytes)
        cls.runtime = json.loads((ROOT / 'artifacts/runpod/image-p4-20261001-01/image-validation-20261001-01/input_001-A/output/raw/runtime.json').read_text())

    def test_profiles_match_frozen_p3_contract_and_every_source_hash(self):
        for version in ('A', 'B'):
            with self.subTest(version=version):
                actual = profiles.profile(version)
                contract = self.contract['versions'][version]
                self.assertEqual(actual['source_archive_sha256'], contract['bundle_sha256'])
                self.assertEqual(actual['comparison_version_sha256'], fingerprint(contract))
                self.assertEqual(actual['source_files_sha256'], fingerprint(self.sources[version]))
                self.assertEqual(actual['model_files'], contract['model_files'])
                for path, model in actual['model_files'].items():
                    self.assertEqual(model, self.sources[version][path])

    def test_p4_manifest_binds_same_sources_models_adapter_and_environment(self):
        for version, root in [('A', 'cctv-runpod-a4000'), ('B', 'deployment_handoff_1001')]:
            with self.subTest(version=version):
                actual = profiles.profile(version)
                self.assertEqual(actual['image_manifest_sha256'], hashlib.sha256(self.image_bytes).hexdigest())
                self.assertEqual(actual['source_archive_sha256'], self.image['source_archives'][version])
                self.assertEqual(actual['execution_adapter_sha256'], self.image['files']['runtime/job.py']['sha256'])
                for name, expected in self.sources[version].items():
                    self.assertEqual(expected, self.image['files'][f'sources/{root}/{name}'])
                for name, digest in actual['code_files'].items():
                    self.assertEqual(digest, self.image['files'][name]['sha256'])
                self.assertEqual(actual['runtime']['python'], self.runtime['python'])
                self.assertEqual(actual['runtime']['cuda_runtime'], self.runtime['cuda_runtime'])
                self.assertEqual(actual['runtime']['gpu'], self.runtime['gpu'])
                for name, value in actual['runtime']['packages'].items():
                    self.assertEqual(value, self.image['packages'][name])
                self.assertEqual(actual['execution_settings']['batch_size'], self.runtime['batch_size'])

    def test_execution_settings_match_frozen_adapter_arguments_and_b_media_constants(self):
        context = ROOT / 'artifacts/runpod/image-p4-20261001-01/context'
        adapter = (context / 'runtime/job.py').read_bytes()
        self.assertEqual(hashlib.sha256(adapter).hexdigest(), self.image['files']['runtime/job.py']['sha256'])
        calls = [node for node in ast.walk(ast.parse(adapter)) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)]
        analyses = [node for node in calls if node.func.attr == 'analyze_video']
        self.assertEqual(len(analyses), 2)
        batches = [ast.literal_eval(next(k.value for k in node.keywords if k.arg == 'batch_size'))
                   for node in analyses]
        self.assertEqual(batches, [8, 8])
        model = next(node for node in calls if node.func.attr == 'ModelBundle')
        device = ast.literal_eval(next(k.value for k in model.keywords if k.arg == 'device'))
        evidence = next(node for node in calls if node.func.attr == 'create_evidence')
        a_before, a_after = map(ast.literal_eval, evidence.args[-2:])
        context_source = (context / 'sources/deployment_handoff_1001/incident_context.py').read_bytes()
        self.assertEqual(hashlib.sha256(context_source).hexdigest(),
                         self.image['files']['sources/deployment_handoff_1001/incident_context.py']['sha256'])
        constants = {target.id: ast.literal_eval(node.value)
                     for node in ast.walk(ast.parse(context_source)) if isinstance(node, ast.Assign)
                     for target in node.targets if isinstance(target, ast.Name)
                     and target.id in ('VLM_BEFORE_S', 'VLM_AFTER_S')}
        for version, before, after in [('A', a_before, a_after),
                                      ('B', constants['VLM_BEFORE_S'], constants['VLM_AFTER_S'])]:
            self.assertEqual(profiles.profile(version)['execution_settings'],
                             dict(device=device, batch_size=batches[0],
                                  evidence_before_s=before, evidence_after_s=after))


class IdentityTests(unittest.TestCase):
    def test_repeat_is_stable_and_other_input_or_version_never_collides(self):
        a = profiles.request_identity(INPUT, profiles.profile('A'), 'gpu_comparison')
        self.assertEqual(a, profiles.request_identity(INPUT, profiles.profile('A'), 'gpu_comparison'))
        b = profiles.request_identity(INPUT, profiles.profile('B'), 'gpu_comparison')
        other = profiles.request_identity('f' * 64, profiles.profile('A'), 'gpu_comparison')
        for key in ('gpu_key', 'request_key'):
            self.assertEqual(len({a[key], b[key], other[key]}), 3)

    def test_purpose_separates_requests_but_reuses_gpu_identity(self):
        compare = profiles.request_identity(INPUT, profiles.profile('A'), 'gpu_comparison')
        report = profiles.request_identity(INPUT, profiles.profile('A'), 'full_report')
        self.assertNotEqual(compare['request_key'], report['request_key'])
        self.assertEqual(compare['gpu_key'], report['gpu_key'])
        self.assertEqual(compare['profile_sha256'], report['profile_sha256'])

    def test_reviewed_packaging_provenance_update_does_not_invalidate_gpu_evidence(self):
        original = profiles.profile('A')
        before = profiles.request_identity(INPUT, original, 'gpu_comparison')
        changed = deepcopy(original)
        for name in ('image_manifest_sha256', 'source_archive_sha256',
                     'source_files_sha256', 'comparison_version_sha256'):
            changed[name] = 'f' * 64
        # Simulate a reviewed catalog provenance update, not an accepted client
        # override: untrusted edits are separately required to fail validation.
        with patch.dict(profiles._PROFILES, {'A': changed}):
            after = profiles.request_identity(INPUT, profiles.profile('A'), 'gpu_comparison')
        self.assertNotEqual(before['profile_sha256'], after['profile_sha256'])
        for field in ('pipeline_sha256', 'gpu_key', 'request_key'):
            self.assertEqual(before[field], after[field])

    def test_reviewed_inference_changes_cannot_reuse_old_gpu_keys(self):
        original = profiles.profile('B')
        before = profiles.request_identity(INPUT, original, 'gpu_comparison')
        for field in ('model_files', 'code_files', 'runtime', 'execution_settings', 'execution_adapter_sha256'):
            with self.subTest(field=field):
                changed = deepcopy(original)
                if field == 'model_files':
                    changed[field][next(iter(changed[field]))]['sha256'] = 'f' * 64
                elif field == 'code_files':
                    changed[field][next(iter(changed[field]))] = 'f' * 64
                elif field == 'runtime':
                    changed[field]['packages']['torch'] = 'different'
                elif field == 'execution_settings':
                    changed[field]['batch_size'] = 1
                else:
                    changed[field] = 'f' * 64
                with patch.dict(profiles._PROFILES, {'B': changed}):
                    after = profiles.request_identity(INPUT, profiles.profile('B'), 'gpu_comparison')
                for key in ('pipeline_sha256', 'gpu_key', 'request_key'):
                    self.assertNotEqual(before[key], after[key])

    def test_a_plus_b_has_ordered_children_matching_standalone_requests(self):
        pair = profiles.selection_identity(INPUT, 'A+B', 'gpu_comparison')
        self.assertEqual([c['profile']['version'] for c in pair['children']], ['A', 'B'])
        self.assertEqual(pair, profiles.selection_identity(INPUT, 'A+B', 'gpu_comparison'))
        for version, child in zip(('A', 'B'), pair['children']):
            single = profiles.selection_identity(INPUT, version, 'gpu_comparison')
            self.assertEqual(single['children'], [child])
            self.assertNotEqual(pair['selection_key'], single['selection_key'])
        report = profiles.selection_identity(INPUT, 'A+B', 'full_report')
        self.assertNotEqual(pair['selection_key'], report['selection_key'])

    def test_jsonb_object_key_order_does_not_change_any_identity(self):
        original = profiles.profile('B')
        reordered = reverse_objects(original)
        self.assertNotEqual(list(original), list(reordered))
        self.assertEqual(profiles.validate_profile(reordered), original)
        for purpose in profiles.PURPOSES:
            self.assertEqual(profiles.request_identity(INPUT, original, purpose),
                             profiles.request_identity(INPUT, reordered, purpose))

    def test_profile_and_identity_returns_are_independent_copies(self):
        original = profiles.profile('A')
        changed = profiles.profile('A')
        name = next(iter(changed['model_files']))
        changed['model_files'][name]['sha256'] = 'f' * 64
        self.assertEqual(profiles.profile('A'), original)
        request = profiles.request_identity(INPUT, original, 'gpu_comparison')
        request['profile']['model_files'][name]['bytes'] = 0
        self.assertEqual(original, profiles.profile('A'))
        pair = profiles.selection_identity(INPUT, 'A+B', 'gpu_comparison')
        expected_b = deepcopy(pair['children'][1])
        pair['children'][0]['profile']['model_files'].clear()
        self.assertEqual(pair['children'][1], expected_b)

    def test_unknown_or_incomplete_legacy_profile_never_defaults_to_a(self):
        for value in (None, {}, {'version': 'A'}, {'version': 'B'}, {'version': 'C'},
                      {'input_sha256': INPUT}, 'A'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    profiles.gpu_identity(INPUT, value)
        for value in (None, '', 'a', 'B-v11', 'A+B', 'C'):
            with self.subTest(version=value):
                with self.assertRaises(ValueError): profiles.profile(value)

    def test_mismatched_label_and_tampered_provenance_rejected(self):
        with self.assertRaisesRegex(ValueError, 'version_mismatch'):
            profiles.validate_profile(profiles.profile('A'), expected_version='B')
        a = profiles.profile('A')
        for name in ('source_archive_sha256', 'source_files_sha256', 'comparison_version_sha256',
                     'image_manifest_sha256', 'execution_adapter_sha256'):
            with self.subTest(field=name):
                changed = dict(a, **{name: 'f' * 64})
                with self.assertRaisesRegex(ValueError, 'profile_changed'):
                    profiles.request_identity(INPUT, changed, 'gpu_comparison')
        for version in ('A', 'B'):
            changed = profiles.profile(version)
            name = next(iter(changed['model_files']))
            changed['model_files'][name]['bytes'] = float(changed['model_files'][name]['bytes'])
            with self.assertRaisesRegex(ValueError, 'profile_changed'):
                profiles.validate_profile(changed)

    def test_generation_settings_and_date_are_not_accepted_inside_gpu_profile(self):
        a = profiles.profile('A')
        for extra in ({'reference_date': '2026-10-01'}, {'generation_settings': {'scene_model': 'gemini-other'}}):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(ValueError, 'profile_changed'):
                    profiles.gpu_identity(INPUT, dict(a, **extra))
        self.assertNotIn('reference_date', profiles.gpu_identity(INPUT, a))
        self.assertNotIn('generation_settings', profiles.gpu_identity(INPUT, a))

    def test_invalid_sha_purpose_or_selection_cannot_generate_a_key(self):
        for value in (None, 0, 'a' * 63, 'g' * 64, INPUT.upper(), INPUT + '\n'):
            with self.subTest(input_sha=value):
                with self.assertRaises(ValueError): profiles.gpu_identity(value, profiles.profile('A'))
        for purpose in (None, '', 'normal', 'report', True, ['gpu_comparison']):
            with self.subTest(purpose=purpose):
                with self.assertRaises(ValueError): profiles.selection_identity(INPUT, 'A', purpose)
        for selection in (None, 'B+A', 'A+A', ['A', 'B'], '', 'C'):
            with self.subTest(selection=selection):
                with self.assertRaises(ValueError): profiles.selection_identity(INPUT, selection, 'gpu_comparison')

    def test_identity_creation_does_not_read_files_or_call_a_service(self):
        with patch('builtins.open', side_effect=AssertionError('Identity must not access files')):
            result = profiles.selection_identity(INPUT, 'A+B', 'gpu_comparison')
        self.assertEqual(len(result['children']), 2)
        self.assertNotEqual(result['children'][0]['request_key'], INPUT)


if __name__ == '__main__':
    unittest.main()
