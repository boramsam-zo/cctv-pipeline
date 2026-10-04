"""Checkout-only release, tamper and publication boundaries; no Docker/network/model."""
import json
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_runpod_ci as builder
from scripts import runpod_ci_registry as registry
from scripts import runpod_ci_changes as changes


class ImageChangeDecisionTests(unittest.TestCase):
    before = 'a' * 40
    after = 'b' * 40

    def decision(self, names):
        def local_diff(args, **kwargs):
            self.assertEqual(args, ['git', 'diff', '--name-only', '-z', '--no-renames',
                                    self.before, self.after, '--'])
            return subprocess.CompletedProcess(args, 0, '\0'.join(names) + '\0', '')
        return changes.decide('push', self.before, self.after, local_diff)

    def test_be_only_changes_are_checked_without_publishing_an_image(self):
        self.assertFalse(self.decision(['apps/be_worker/mlops_jobs.py',
                                        'apps/be_worker/mlflow_tracking.py',
                                        'apps/be_worker/model_releases/' + 'a' * 64 + '.json',
                                        'tests/be_worker/test_mlops_jobs.py']))

    def test_image_source_or_contract_changes_require_publish(self):
        for name in ('apps/runpod_image/service_job.py', 'apps/model_compare/runner.py',
                     'apps/be_worker/analysis_profiles.py', 'apps/be_worker/mlops-20261004-v1.json',
                     'scripts/prepare_runpod_ci.py'):
            with self.subTest(name=name):
                self.assertTrue(self.decision([name]))

    def test_pull_request_never_publishes_and_manual_run_is_explicit(self):
        def unexpected(*args, **kwargs):
            raise AssertionError('No Git inspection is needed')
        self.assertFalse(changes.decide('pull_request', '', self.after, unexpected))
        self.assertTrue(changes.decide('workflow_dispatch', '', self.after, unexpected))
        self.assertTrue(changes.decide('push', changes.ZERO, self.after, unexpected))

    def test_unavailable_base_and_invalid_commit_fail_closed(self):
        def failure(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, '', 'missing object')
        with self.assertRaisesRegex(ValueError, 'inspection_failed'):
            changes.decide('push', self.before, self.after, failure)
        with self.assertRaisesRegex(ValueError, 'event_or_commit'):
            changes.decide('push', '--help', self.after, failure)

    def test_workflow_checks_be_changes_and_explicit_mlflow_tests(self):
        workflow = (builder.ROOT / '.github/workflows/runpod-image.yml').read_text()
        self.assertEqual(workflow.count("- 'apps/be_worker/**'"), 2)
        self.assertIn('tests.be_worker.test_mlflow_tracking tests.be_worker.test_register_mlops_models', workflow)
        self.assertIn("needs.check.outputs.publish_required == 'true'", workflow)
        self.assertIn('fetch-depth: 0', workflow)


class CheckoutReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cctv-ci-')
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.root = self.parent / 'checkout'
        self.root.mkdir()
        # A fresh checkout fixture has only the explicit source roster, no artifacts,
        # models, local environment, credentials or original 53 frozen files.
        names = {builder.BASE_MANIFEST, 'apps/runpod_image/MLops.Dockerfile',
                 *builder.RUNTIME_SOURCES.values()}
        names.update(builder.SERVICE_OVERRIDES.get(name, name)
                     for name in builder.verifier.MLOPS_FILES
                     if name not in {'Dockerfile', '.dockerignore', builder.verifier.MLOPS_CATALOG})
        for name in names:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(builder.ROOT / name, target)
        self.blocked = patch.dict(sys.modules, {name: None for name in (
            'torch', 'torchvision', 'numpy', 'PIL', 'av', 'cv2', 'boto3', 'botocore', 'ultralytics')})
        self.blocked.start()
        self.addCleanup(self.blocked.stop)
        self.network = patch('socket.socket', side_effect=AssertionError('No network in CI preparation'))
        self.network.start()
        self.addCleanup(self.network.stop)
        builder.write_catalog(self.root)

    def build(self, name='release'):
        return builder.build(self.parent / name, self.root)

    def test_checkout_only_deterministic_context_has_no_model_or_secret_bytes(self):
        first = self.build()
        second = self.build('second')
        self.assertEqual(first, second)
        self.assertFalse((self.root / 'artifacts').exists())
        self.assertFalse(first['includes_model_weights'])
        self.assertFalse(first['includes_secrets'])
        self.assertEqual(first['inherited_files_verification'], 'deferred to Docker build inspect')
        self.assertEqual(first['model_calls'], 0)
        context = self.parent / 'release/context'
        names = {p.relative_to(context).as_posix() for p in context.rglob('*') if p.is_file()}
        self.assertEqual(names, {'Dockerfile', '.dockerignore', 'image-manifest.json',
                                *builder.RUNTIME_SOURCES,
                                *('service/' + name for name in builder.verifier.MLOPS_FILES),
                                'service/service-manifest.json'})
        self.assertTrue(all(Path(name).suffix not in {'.pt', '.pth', '.mp4', '.key', '.pem', '.env'}
                            for name in names))
        image = json.loads((context / 'image-manifest.json').read_text())
        self.assertEqual(image['supported_versions'], ['B', 'R2'])
        self.assertEqual(image['default_version'], 'B')
        self.assertEqual(image['schema_version'], 'cctv-runpod-image-v4')
        self.assertEqual(len(image['files']), 53)
        base = json.loads((self.root / builder.BASE_MANIFEST).read_text())
        for name, spec in base['files'].items():
            if name not in builder.RUNTIME_SOURCES:
                self.assertEqual(image['files'][name], spec)
        self.assertEqual(builder.verifier.verify(context / 'service')['manifest_sha256'],
                         first['service_manifest_sha256'])

    def test_source_change_requires_explicit_catalog_update(self):
        catalog_file = self.root / builder.verifier.MLOPS_CATALOG
        before = catalog_file.read_bytes()
        old = json.loads(before)
        sha = hashlib.sha256(json.dumps(old, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        file = self.root / 'apps/runpod_image/classifier_adapter.py'
        file.write_bytes(file.read_bytes() + b'\n# Synthetic source change.\n')
        with self.assertRaisesRegex(ValueError, 'Source/catalog drift'):
            self.build()
        self.assertFalse((self.parent / 'release').exists())
        builder.write_catalog(self.root)
        archive = catalog_file.parent / 'model_releases' / (sha + '.json')
        self.assertEqual(archive.read_bytes(), before)
        self.assertNotEqual(catalog_file.read_bytes(), before)
        self.assertFalse(builder.write_catalog(self.root)['previous_catalog_archived'])
        self.build()

    def test_conflicting_catalog_archive_blocks_update(self):
        target = self.root / builder.verifier.MLOPS_CATALOG
        before = target.read_bytes()
        previous = json.loads(before)
        sha = hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        archive = target.parent / 'model_releases' / (sha + '.json')
        archive.parent.mkdir()
        archive.write_bytes(b'conflicting preserved record')
        source = self.root / 'apps/runpod_image/classifier_adapter.py'
        source.write_bytes(source.read_bytes() + b'\n# Changed source\n')
        with self.assertRaisesRegex(ValueError, 'archive differs'):
            builder.write_catalog(self.root)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(archive.read_bytes(), b'conflicting preserved record')

    def test_baseline_metadata_tamper_is_rejected(self):
        file = self.root / builder.BASE_MANIFEST
        file.write_bytes(file.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'base manifest mismatch'):
            self.build()

    def test_symlink_source_rejected_and_existing_output_preserved(self):
        self.build()
        before = (self.parent / 'release/preparation.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'existing files are preserved'):
            self.build()
        self.assertEqual((self.parent / 'release/preparation.json').read_bytes(), before)
        file = self.root / 'apps/runpod_image/classifier_adapter.py'
        file.unlink()
        file.symlink_to(builder.ROOT / 'apps/runpod_image/classifier_adapter.py')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.build('linked')

    def test_unknown_local_import_fails_before_output_is_created(self):
        file = self.root / 'apps/runpod_image/classifier_adapter.py'
        file.write_bytes(file.read_bytes() + b'\nfrom apps.not_shipped import surprise\n')
        with self.assertRaisesRegex(ValueError, 'Missing local import'):
            self.build()
        self.assertFalse((self.parent / 'release').exists())

    def test_service_tamper_and_unregistered_files_rejected(self):
        self.build()
        service = self.parent / 'release/context/service'
        source = service / 'apps/runpod_image/classifier_adapter.py'
        original = source.read_bytes()
        source.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'code mismatch'):
            builder.verifier.verify(service)
        source.write_bytes(original)
        (service / 'unexpected.pt').write_bytes(b'not-a-model')
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            builder.verifier.verify(service)


class RegistryGuardTests(unittest.TestCase):
    image = 'sample/cctv'
    commit = 'a' * 40
    digest = 'sha256:' + 'b' * 64

    def response(self, code=0, stdout='', stderr=''):
        return lambda *args, **kwargs: subprocess.CompletedProcess(args, code, stdout, stderr)

    def test_existing_commit_returns_digest_without_build_or_push(self):
        result = registry.inspect(self.image, self.commit, self.response(stdout=json.dumps({'digest': self.digest})))
        self.assertTrue(result['exists'])
        self.assertEqual(result['digest'], self.digest)
        self.assertEqual(result['tag'], self.image + ':sha-' + self.commit)

    def test_only_confirmed_missing_manifest_allows_new_publish(self):
        result = registry.inspect(self.image, self.commit, self.response(1, stderr='manifest unknown'))
        self.assertFalse(result['exists'])
        for error in ('connection timed out', 'unauthorized', '404 not found',
                      'manifest unknown: 429 too many requests', 'denied: manifest unknown'):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, 'no_publish'):
                registry.inspect(self.image, self.commit, self.response(1, stderr=error))

    def test_bad_image_commit_or_digest_cannot_become_shell_or_workflow_output(self):
        for image, commit in (('sample/cctv\nextra=x', self.commit), ('sample/cctv;id', self.commit),
                              (self.image, '../main'), ('https://registry/image', self.commit)):
            with self.subTest(image=image), self.assertRaises(ValueError):
                registry.identity(image, commit)
        with self.assertRaisesRegex(ValueError, 'digest_invalid'):
            registry.inspect(self.image, self.commit, self.response(stdout='{"digest":"bad\\nextra=x"}'))

    def test_summary_contains_digest_pin_and_manual_pod_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'summary'
            registry.summary(self.image, self.commit, self.digest, output)
            value = output.read_text()
        self.assertIn(self.image + '@' + self.digest, value)
        self.assertIn('EC2 activation are manual', value)

    def test_workflow_main_only_publish_sha_pins_and_no_weight_secret_input(self):
        workflow = (builder.ROOT / '.github/workflows/runpod-image.yml').read_text()
        self.assertIn("if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'", workflow)
        self.assertIn('needs: check', workflow)
        self.assertIn("if: steps.registry.outputs.exists == 'false'", workflow)
        uses = re.findall(r'uses: ([^\s]+)', workflow)
        self.assertTrue(uses)
        self.assertTrue(all(re.fullmatch(r'[a-z-]+/[a-z-]+@[0-9a-f]{40}', value) for value in uses))
        self.assertNotIn('pull_request_target', workflow)
        self.assertNotIn('AWS_', workflow)
        self.assertNotIn(':latest', workflow)
        self.assertNotIn('CCTV_R2_MODEL_S3_KEY', workflow)
        self.assertIn('username: ${{ vars.DOCKERHUB_USERNAME }}', workflow)
        self.assertNotIn('secrets.DOCKERHUB_USERNAME', workflow)
        self.assertIn('password: ${{ secrets.DOCKERHUB_TOKEN }}', workflow)
        self.assertIn('platforms: linux/amd64', workflow)
        self.assertIn('persist-credentials: false', workflow)


if __name__ == '__main__':
    unittest.main()
