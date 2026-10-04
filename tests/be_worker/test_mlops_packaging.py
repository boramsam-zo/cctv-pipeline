"""EC2 packaging and package-preservation probes; no build, API, or model imports."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from apps.be_worker import mlops_image_check as probe
from scripts import build_be_mlops_bundle as bundle
from scripts import build_mlops_be_image as builder


class CpuImageBuildGuardTests(unittest.TestCase):
    def setUp(self):
        self.prepared = builder.plan(builder.BASE_TAG, builder.BASE_ID, 'cctv-be-team-rag:test-mlops')
        self.images = {builder.BASE_TAG: builder.BASE_ID}
        self.calls = []
        self.output_id = 'sha256:' + 'a' * 64
        self.metadata = {'status': 'cpu_mlflow_packages_verified',
                         'base_packages_unchanged': True, 'model_calls': 0,
                         'network_requests': 0, 'service_started': False}

    def docker(self, args, **kwargs):
        self.calls.append(args)
        stdout, stderr, code = '', '', 0
        if args[:3] == ['docker', 'image', 'inspect']:
            stdout = self.images.get(args[-1], '')
            if not stdout:
                stderr, code = 'Error: No such image: redacted', 1
        elif args[:3] == ['docker', 'image', 'tag']:
            self.images[args[-1]] = args[-2]
        elif args[:2] == ['docker', 'build']:
            self.images[self.prepared['output_tag']] = self.output_id
        elif args[:2] == ['docker', 'run']:
            stdout = json.dumps(self.metadata)
        else:
            raise AssertionError('Unexpected Docker operation')
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    def test_default_plan_does_not_touch_docker(self):
        with patch.object(builder.subprocess, 'run', side_effect=AssertionError('Docker must not run')):
            planned = builder.plan(builder.BASE_TAG, builder.BASE_ID, 'cctv-be-team-rag:new')
        self.assertFalse(planned['docker_build_performed'])
        self.assertIn('--pull=false', planned['build_command'])
        self.assertEqual(set(planned['context_files']), set(builder.CONTEXT_FILES))

    def test_explicit_build_preserves_base_and_runs_only_isolated_metadata_probe(self):
        result = builder.build(self.prepared, self.docker)
        self.assertEqual(result['output_image_id'], self.output_id)
        self.assertTrue(result['base_identity_preserved'])
        self.assertFalse(result['service_changes'])
        self.assertEqual(result['model_calls'], 0)
        probe_command = next(args for args in self.calls if args[:2] == ['docker', 'run'])
        self.assertEqual(probe_command[-1], self.output_id)
        self.assertEqual(probe_command[probe_command.index('--network') + 1], 'none')
        self.assertIn('--read-only', probe_command)
        self.assertNotIn('--volume', probe_command)
        self.assertNotIn('--gpus', probe_command)

    def test_wrong_base_existing_output_or_conflicting_alias_prevents_build(self):
        for target, error in ((builder.BASE_TAG, 'base_id_mismatch'),
                              (self.prepared['output_tag'], 'existing_output'),
                              (self.prepared['frozen_local_base_tag'], 'base_alias_conflict')):
            with self.subTest(target=target):
                self.images = {builder.BASE_TAG: builder.BASE_ID, target: self.output_id}
                self.calls = []
                with self.assertRaisesRegex(ValueError, error):
                    builder.build(self.prepared, self.docker)
                self.assertFalse(any(args[:2] == ['docker', 'build'] for args in self.calls))

    def test_changed_context_is_rejected_before_build(self):
        with patch.object(builder, 'context_identity', return_value={'changed': True}):
            with self.assertRaisesRegex(ValueError, 'context_changed'):
                builder.build(self.prepared, self.docker)
        self.assertFalse(any(args[:2] == ['docker', 'build'] for args in self.calls))

    def test_metadata_result_requires_no_model_or_service_execution(self):
        for metadata in ([], dict(self.metadata, model_calls=1), dict(self.metadata, service_started=True)):
            with self.subTest(metadata=metadata):
                self.images = {builder.BASE_TAG: builder.BASE_ID}
                self.metadata = metadata
                with self.assertRaisesRegex(ValueError, 'metadata_probe_contract_mismatch'):
                    builder.build(self.prepared, self.docker)

    def test_authentication_or_daemon_errors_are_not_treated_as_missing_images(self):
        def failed(args, **kwargs):
            return SimpleNamespace(returncode=1, stdout='', stderr='Cannot connect to the Docker daemon')
        with self.assertRaisesRegex(ValueError, 'image_inspection_failed'):
            builder.inspect('example:test', failed, missing=True)


class CpuDependencyProbeTests(unittest.TestCase):
    def test_existing_cpu_dependencies_cannot_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = dict(probe.REQUIRED_BASE, psycopg='3.3.6')
            probe.snapshot(root, baseline)
            self.assertTrue(probe.verify(root, dict(baseline, **probe.CLIENT))['base_packages_unchanged'])
            wrong = dict(baseline, **probe.CLIENT)
            wrong['torch'] = '2.9.0'
            with self.assertRaisesRegex(ValueError, 'existing_cpu_runtime_changed'):
                probe.verify(root, wrong)
            with self.assertRaisesRegex(ValueError, 'requirements_missing'):
                probe.verify(root, baseline)

    def test_wrong_base_stops_before_creating_constraints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'existing_cpu_runtime_missing'):
                probe.snapshot(root, {'torch': '2.8.0'})
            self.assertEqual(list(root.iterdir()), [])


class Ec2BundleTests(unittest.TestCase):
    @unittest.skipUnless(all((bundle.ROOT / name).is_file() for name in bundle.RESOURCE_FILES),
                         'Operational RAG resources are delivered separately from Git')
    def test_full_source_resource_archive_readback_and_no_secret_model_weights(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / 'prepared'
            record = bundle.build(target)
            self.assertEqual(record['remote_changes'], 0)
            self.assertEqual(record['docker_builds'], 0)
            with zipfile.ZipFile(target / record['archive']) as archive:
                names = set(archive.namelist())
                required = {'apps/be_worker/mlops_jobs.py', 'apps/be_worker/mlflow_tracking.py',
                            'apps/be_worker/mlflow_requirements.txt', 'apps/be_worker/MLops.Dockerfile',
                            'apps/cctv_ui/control_room.py', 'apps/cctv_ui/control_room.css',
                            'apps/cctv_ui/analysis_app.py', 'apps/team_rag/runtime.py',
                            'apps/scene_description/team_scene.py', *bundle.RESOURCE_FILES}
                self.assertTrue(required <= names)
                self.assertNotIn('apps/cctv_ui/data/saved-control-room.json', names)
                self.assertFalse(any(Path(name).suffix in {'.pt', '.pth', '.mp4', '.env', '.pem'} for name in names))
                manifest = json.loads(archive.read('ec2-mlops-manifest.json'))
                for name in bundle.RESOURCE_FILES:
                    raw = (bundle.ROOT / name).read_bytes()
                    self.assertEqual(archive.read(name), raw)
                    self.assertEqual(manifest['files'][name]['sha256'], hashlib.sha256(raw).hexdigest())
            with self.assertRaisesRegex(ValueError, 'must_be_new'):
                bundle.build(target)

    def test_recipe_requires_cpu_preservation_and_does_not_start_the_worker(self):
        recipe = (bundle.ROOT / 'apps/be_worker/MLops.Dockerfile').read_text()
        self.assertIn('ARG BASE_IMAGE=cctv-be-team-rag:small-cpu-v1', recipe)
        self.assertIn('--constraint /opt/cctv-mlops/base-constraints.txt', recipe)
        self.assertIn('RUN python -m pip check', recipe)
        self.assertIn('CMD ["verify"', recipe)
        self.assertNotIn('uvicorn', recipe)
        self.assertNotIn('apps.be_worker.daemon', recipe)


if __name__ == '__main__':
    unittest.main()
