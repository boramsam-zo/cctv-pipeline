"""Code-only service packaging: temporary files, no Docker, SDKs, or inference."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_runpod_service_bundle as builder


class ServiceBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cctv-service-bundle-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def prepare(self, name='release'):
        output = self.root / name
        record = builder.build(output)
        return output, output / 'context', record

    def test_allowlist_archive_hashes_and_standalone_verifier(self):
        output, context, record = self.prepare()
        self.assertEqual(record['files'], len(builder.verifier.FILES) + 1)
        self.assertEqual(record['status'], 'service_overlay_prepared_not_built')
        self.assertFalse(record['includes_model_weights'])
        self.assertFalse(record['includes_inputs_results_or_secrets'])
        self.assertEqual((record['model_calls'], record['remote_changes'], record['registry_uploads']), (0, 0, 0))
        self.assertFalse(record['docker_build_performed'])
        archive = output / builder.ARCHIVE
        self.assertEqual(record['sha256'], builder.verifier.fingerprint(archive.read_bytes())['sha256'])
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(set(bundle.namelist()), {'cctv-runpod-service/' + name
                for name in (*builder.verifier.FILES, 'service-manifest.json')})
            for name in builder.verifier.FILES:
                self.assertEqual(bundle.read('cctv-runpod-service/' + name), (context / name).read_bytes())
        process = subprocess.run([sys.executable, '-I', '-B',
            str(context / 'apps/runpod_image/verify_service_bundle.py')],
            capture_output=True, text=True, check=True, cwd=self.root)
        verified = json.loads(process.stdout)
        self.assertEqual((verified['status'], verified['model_calls']), ('service_bundle_verified', 0))

    def test_separate_contexts_are_deterministic_and_existing_output_is_preserved(self):
        first, _, one = self.prepare('one')
        second, _, two = self.prepare('two')
        self.assertEqual(one, two)
        before = (first / builder.ARCHIVE).read_bytes()
        with self.assertRaisesRegex(ValueError, 'existing files are preserved'):
            builder.build(first)
        self.assertEqual((first / builder.ARCHIVE).read_bytes(), before)
        self.assertEqual((second / builder.ARCHIVE).read_bytes(), before)

    def test_verifier_rejects_missing_extra_changed_and_symlink_files(self):
        _, context, _ = self.prepare()
        target = context / 'apps/be_worker/common.py'
        original = target.read_bytes()
        target.unlink()
        with self.assertRaisesRegex(ValueError, 'missing or unregistered'):
            builder.verifier.verify(context)
        target.write_bytes(original + b'\n# changed\n')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            builder.verifier.verify(context)
        target.write_bytes(original)
        extra = context / '.env'
        extra.write_text('SYNTHETIC_SECRET=must-not-ship')
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            builder.verifier.verify(context)
        extra.unlink()
        target.unlink()
        target.symlink_to(builder.ROOT / 'apps/be_worker/common.py')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            builder.verifier.verify(context)

    def test_manifest_schema_base_and_allowlist_are_pinned(self):
        _, context, _ = self.prepare()
        target = context / 'service-manifest.json'
        original = json.loads(target.read_text())
        for key, value in [('base_image', 'unapproved:latest'), ('automatic_inference', True),
                           ('image_manifest_sha256', '0' * 64), ('default_command', 'start-service')]:
            with self.subTest(key=key):
                target.write_text(json.dumps(dict(original, **{key: value})))
                with self.assertRaisesRegex(ValueError, 'identity or allowlist'):
                    builder.verifier.verify(context)
        target.write_text(json.dumps(original)[:-1] + ',"default_command":"pod"}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            builder.verifier.verify(context)

    def test_missing_import_and_changed_frozen_executor_fail_before_writing(self):
        payload = {name: (builder.ROOT / name).read_bytes() for name in builder.verifier.FILES if name.endswith('.py')}
        builder.verify_local_imports(payload)
        for missing in ['apps/model_compare/runner.py', 'apps/runpod_image/image_runtime.py',
                        'apps/be_worker/analysis_profiles.py', 'apps/runpod_worker/verify_bundle.py']:
            with self.subTest(missing=missing):
                reduced = {name: data for name, data in payload.items() if name != missing}
                with self.assertRaisesRegex(ValueError, 'Missing local import'):
                    builder.verify_local_imports(reduced)
        frozen = self.root / 'frozen'
        (frozen / 'runtime').mkdir(parents=True)
        for name in ['image-manifest.json', 'runtime/runner.py', 'runtime/image_runtime.py']:
            (frozen / name).write_bytes((builder.FROZEN / name).read_bytes())
        with (frozen / 'runtime/runner.py').open('ab') as target:
            target.write(b'\n# changed\n')
        with patch.object(builder, 'FROZEN', frozen):
            with self.assertRaisesRegex(ValueError, 'Frozen image helper changed'):
                builder.build(self.root / 'blocked')
        self.assertFalse((self.root / 'blocked').exists())

    def test_container_layers_preserve_default_and_frozen_paths_without_install(self):
        _, context, _ = self.prepare()
        lines = [(line.split()[0].upper(), line) for line in (context / 'Dockerfile').read_text().splitlines()
                 if line and not line.startswith('#') and not line.startswith(' ')]
        self.assertEqual(lines[0][1], 'FROM ' + builder.verifier.BASE_IMAGE)
        self.assertNotIn('ENTRYPOINT', [verb for verb, _ in lines])
        self.assertNotIn('CMD', [verb for verb, _ in lines])
        self.assertIn(('WORKDIR', 'WORKDIR /opt/cctv-service'), lines)
        self.assertEqual([line for verb, line in lines if verb == 'RUN'],
            ['RUN /opt/cctv-image/.venv/bin/python -B apps/runpod_image/verify_service_bundle.py'])
        script = (context / 'apps/runpod_image/start-service.sh').read_text()
        self.assertLess(script.index('verify_service_bundle.py'), script.index('exec /opt/cctv-image/.venv/bin/python'))
        self.assertNotIn('CCTV_GPU_MAX_JOBS=', script)
        self.assertNotIn('pip install', (context / 'Dockerfile').read_text())
        # Every local packaged module remains stdlib-only except service and the
        # deferred S3 publisher. No model import is introduced by this layer.
        forbidden = {'torch', 'torchvision', 'pytorchvideo', 'ultralytics', 'cv2', 'av', 'numpy'}
        for path in context.rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_bytes())):
                imports = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
                self.assertFalse(forbidden & {name.split('.')[0] for name in imports}, str(path))

    def test_output_parent_symlink_is_rejected(self):
        link = self.root / 'linked'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'real parent'):
            builder.build(link / 'release')
        self.assertFalse((self.root / 'release').exists())


if __name__ == '__main__':
    unittest.main()
