"""Code-only release packaging and inherited-file verification; no Docker/model."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_runpod_gpu_release as builder


class GPUReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cctv-cuda-release-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.blocked = patch.dict(sys.modules, {name: None for name in
            ('torch', 'torchvision', 'pytorchvideo', 'ultralytics', 'av', 'cv2', 'numpy', 'boto3')})
        self.blocked.start()
        self.addCleanup(self.blocked.stop)
        self.network = patch('socket.socket', side_effect=AssertionError('No network in packaging test'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def prepare(self, name='release'):
        directory = self.root / name
        record = builder.build(directory)
        return directory, directory / 'context', record

    def old_manifest(self):
        return builder.runtime.read_json(builder.FROZEN / 'image-manifest.json')

    def test_deterministic_small_archive_and_existing_output_preservation(self):
        directory, context, record = self.prepare()
        other, _, second = self.prepare('second')
        self.assertEqual(record, second)
        self.assertEqual((directory / builder.ARCHIVE).read_bytes(), (other / builder.ARCHIVE).read_bytes())
        self.assertFalse(record['includes_model_weights'])
        self.assertFalse(record['includes_inputs_results_or_secrets'])
        self.assertFalse(record['docker_build_performed'])
        self.assertEqual((record['model_calls'], record['remote_changes'], record['registry_uploads']), (0, 0, 0))
        self.assertEqual(record['frozen_image_files_verified'], 53)
        self.assertEqual(record['inherited_files_unchanged'], 50)
        names = {p.relative_to(context).as_posix() for p in context.rglob('*') if p.is_file()}
        expected = {'Dockerfile', '.dockerignore', 'image-manifest.json', *builder.RUNTIME_SOURCES,
                    *('service/' + name for name in (*builder.verifier.PORTABLE_FILES, 'service-manifest.json'))}
        self.assertEqual(names, expected)
        self.assertEqual(len(names), record['files'])
        self.assertFalse(any(Path(name).suffix in {'.pt', '.mp4', '.jpg', '.pem', '.key'} for name in names))
        self.assertLess(sum(p.stat().st_size for p in context.rglob('*') if p.is_file()), 500_000)
        with zipfile.ZipFile(directory / builder.ARCHIVE) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {builder.ARCHIVE_ROOT + name for name in names})
            for name in names:
                self.assertEqual(archive.read(builder.ARCHIVE_ROOT + name), (context / name).read_bytes())
        original = (directory / builder.ARCHIVE).read_bytes()
        with self.assertRaisesRegex(ValueError, 'existing files are preserved'):
            builder.build(directory)
        self.assertEqual((directory / builder.ARCHIVE).read_bytes(), original)

    def test_full_manifest_changes_only_three_runtime_files_and_assembles(self):
        _, context, record = self.prepare()
        old = self.old_manifest()
        new = builder.runtime.read_json(context / 'image-manifest.json')
        self.assertEqual(set(new['files']), set(old['files']))
        changed = {name for name in old['files'] if new['files'][name] != old['files'][name]}
        self.assertEqual(changed, set(builder.RUNTIME_SOURCES))
        for name in ('packages', 'python_version', 'source_archives', 'supported_versions'):
            self.assertEqual(new[name], old[name])
        self.assertEqual(new['schema_version'], 'cctv-runpod-image-v3')
        self.assertEqual(new['gpu'], builder.verifier.PORTABLE_GPU)
        self.assertEqual(new['base_image'], builder.verifier.PORTABLE_BASE_IMAGE)
        self.assertEqual(new['parent_image_manifest_sha256'], builder.verifier.IMAGE_MANIFEST_SHA256)
        assembled = self.root / 'assembled-image'
        assembled.mkdir()
        for name in old['files']:
            target = assembled / name
            target.parent.mkdir(parents=True, exist_ok=True)
            # Only read shared inodes; never modify these links or inherited data.
            source = context / name if name in changed else builder.FROZEN / name
            os.link(source, target)
        (assembled / 'image-manifest.json').write_bytes((context / 'image-manifest.json').read_bytes())
        verified, digest = builder.runtime.inspect_image(assembled)
        self.assertEqual(verified, new)
        self.assertEqual(digest, record['image_manifest_sha256'])
        for name, expected in old['files'].items():
            builder.runtime.check_file(builder.FROZEN / name, expected)
        self.assertEqual(builder.runtime.file_hash(builder.FROZEN / 'image-manifest.json'),
                         builder.verifier.IMAGE_MANIFEST_SHA256)

    def test_service_catalog_runtime_copies_and_actual_recipe_are_bound(self):
        _, context, record = self.prepare()
        service = context / 'service'
        catalog = builder.runtime.read_json(service / builder.verifier.PORTABLE_CATALOG)
        self.assertEqual(catalog['image_manifest_sha256'], record['image_manifest_sha256'])
        self.assertEqual(catalog['execution_adapter_sha256'],
                         builder.runtime.file_hash(context / 'runtime/job.py'))
        self.assertEqual((service / 'Dockerfile').read_bytes(), (context / 'Dockerfile').read_bytes())
        self.assertEqual((service / '.dockerignore').read_bytes(), (context / '.dockerignore').read_bytes())
        for path, source in (('apps/model_compare/runner.py', 'runtime/runner.py'),
                             ('apps/runpod_image/image_runtime.py', 'runtime/image_runtime.py')):
            self.assertEqual((service / path).read_bytes(), (context / source).read_bytes())
        self.assertEqual((service / 'apps/runpod_image/service_job.py').read_bytes(),
                         builder.read_source('apps/runpod_image/service_job.py'))
        actual = builder.verifier.verify(service)
        self.assertEqual(actual['files_verified'], len(builder.verifier.PORTABLE_FILES))
        self.assertEqual(actual['manifest_sha256'], record['service_manifest_sha256'])
        command = [sys.executable, '-I', '-B', str(service / 'apps/runpod_image/verify_service_bundle.py')]
        process = subprocess.run(command, capture_output=True, text=True, timeout=5, check=True, cwd=self.root)
        self.assertEqual(json.loads(process.stdout), actual)

    def test_recipe_preserves_entrypoint_models_and_environment_without_install(self):
        _, context, _ = self.prepare()
        lines = [line for line in (context / 'Dockerfile').read_text().splitlines()
                 if line and not line.startswith(('#', ' '))]
        self.assertEqual(lines[0], 'FROM ' + builder.verifier.PORTABLE_BASE_IMAGE)
        self.assertEqual([line for line in lines if line.startswith('COPY ')], [
            'COPY runtime/ /opt/cctv-image/runtime/',
            'COPY image-manifest.json /opt/cctv-image/image-manifest.json',
            'COPY service/ /opt/cctv-service/'])
        self.assertIn('ENV CCTV_ANALYSIS_DEPLOYMENT=' + builder.verifier.PORTABLE_DEPLOYMENT, lines)
        self.assertIn('ENV CCTV_EXPECTED_GPU=auto', lines)
        self.assertFalse(any(line.startswith(('ENTRYPOINT ', 'CMD ')) for line in lines))
        self.assertEqual([line for line in lines if line.startswith('RUN ')], [
            'RUN /opt/cctv-image/.venv/bin/python -B /opt/cctv-image/runtime/image_runtime.py inspect',
            'RUN /opt/cctv-image/.venv/bin/python -B /opt/cctv-image/runtime/image_runtime.py check-environment',
            'RUN /opt/cctv-image/.venv/bin/python -B apps/runpod_image/verify_service_bundle.py'])

    def test_wrong_catalog_blocks_before_creating_output(self):
        read = builder.read_source
        for change in ({'image_manifest_sha256': 'ab' * 32}, {'gpu': 'NVIDIA RTX A4500'},
                       {'deployment': '../other'}, {'extra': True}):
            def altered(name):
                data = read(name)
                return (builder.json_bytes(dict(json.loads(data), **change))
                        if name == builder.verifier.PORTABLE_CATALOG else data)
            target = self.root / 'blocked'
            with self.subTest(change=change), patch.object(builder, 'read_source', side_effect=altered):
                with self.assertRaises(ValueError):
                    builder.build(target)
            self.assertFalse(target.exists())

    def test_mutated_inherited_source_refused_without_changing_originals(self):
        frozen = self.root / 'frozen'
        frozen.mkdir()
        old = self.old_manifest()
        for name in ('image-manifest.json', *old['files']):
            target = frozen / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(builder.FROZEN / name, target)
        path = frozen / 'sources/cctv-runpod-a4000/worker.py'
        path.unlink()  # Replace only this temporary link, preserving original bytes.
        path.write_text('# mutated inherited source\n')
        with patch.object(builder, 'FROZEN', frozen), self.assertRaisesRegex(ValueError, 'identity mismatch'):
            builder.build(self.root / 'blocked')
        self.assertFalse((self.root / 'blocked').exists())
        builder.runtime.check_file(builder.FROZEN / path.relative_to(frozen), old['files'][path.relative_to(frozen).as_posix()])

    def test_service_v3_rejects_wrong_contract_unknown_files_and_catalog_tamper(self):
        _, context, _ = self.prepare()
        service = context / 'service'
        path = service / 'service-manifest.json'
        original = builder.runtime.read_json(path)
        for changed in ({'base_image': builder.verifier.BASE_IMAGE}, {'deployment': 'other'},
                        {'image_manifest_sha256': 'ab' * 32}, {'automatic_inference': True},
                        {'platform': 'linux/arm64'}, {'schema_version': 'unknown'}):
            path.write_bytes(builder.json_bytes(dict(original, **changed)))
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                builder.verifier.verify(service)
        path.write_bytes(builder.json_bytes(original))
        extra = service / '.env'
        extra.write_text('SYNTHETIC=unregistered')
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            builder.verifier.verify(service)
        extra.unlink()
        catalog_path = service / builder.verifier.PORTABLE_CATALOG
        catalog_path.write_bytes(catalog_path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            builder.verifier.verify(service)

    def test_symlink_output_or_source_cannot_enter_release(self):
        link = self.root / 'linked'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            builder.build(link / 'release')
        source_root = self.root / 'source'
        source_root.mkdir()
        target = source_root / 'linked.py'
        target.symlink_to(builder.ROOT / 'apps/model_compare/job.py')
        with patch.object(builder, 'ROOT', source_root), self.assertRaisesRegex(ValueError, 'Symlink'):
            builder.read_source('linked.py')


if __name__ == '__main__':
    unittest.main()
