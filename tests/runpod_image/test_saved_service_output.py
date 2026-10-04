"""Stored P4 media -> service result verification, with a synthetic new ledger.

The source outputs and inputs are existing A/B results. Only the new service
plan/ledger is synthetic, in a resolved temporary directory. Its `approved` field
tests the saved contract; this file never executes that plan or any model.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker.ab_evidence import validate_local
from apps.be_worker.analysis_profiles import gpu_identity, profile
from apps.be_worker.common import read_json, save
from apps.runpod_image import image_runtime
from apps.runpod_image.service_job import Runtime


ROOT = Path(__file__).resolve().parents[2]
P4 = ROOT / 'artifacts/runpod/image-p4-20261001-01'
SAVED = P4 / 'image-validation-20261001-01'


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(data)
    return value.hexdigest()


def inventory(root):
    return {p.relative_to(root).as_posix(): (p.stat().st_size, sha(p))
            for p in root.rglob('*') if p.is_file()}


class SavedServiceOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.originals = {version: inventory(SAVED / f'input_001-{version}') for version in ('A', 'B')}

    @classmethod
    def tearDownClass(cls):
        for version, expected in cls.originals.items():
            if inventory(SAVED / f'input_001-{version}') != expected:
                raise AssertionError('Original P4 inputs/results changed during test')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # macOS /var is an alias: runtime plain_path intentionally rejects links.
        self.root = Path(temporary.name).resolve()
        self.runtime = Runtime((P4 / 'context').resolve(), self.root / 'image-state')
        # Any accidental execution, SDK call or decoder/model import fails the test.
        self.block_modules = patch.dict('sys.modules', {
            'torch': None, 'ultralytics': None, 'pytorchvideo': None,
            'av': None, 'cv2': None, 'boto3': None, 'botocore': None})
        self.block_modules.start()
        self.addCleanup(self.block_modules.stop)
        self.process = patch('subprocess.Popen', side_effect=AssertionError('No process execution in stored-result test'))
        self.process.start()
        self.addCleanup(self.process.stop)
        self.network = patch('socket.socket', side_effect=AssertionError('No network in stored-result test'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def fixture(self, version):
        original = SAVED / f'input_001-{version}'
        result = read_json(original / 'output/result.json')
        identifier = ('a' if version == 'A' else 'b') * 32
        directory = self.root / 'service' / identifier
        directory.mkdir(parents=True)
        shutil.copyfile(original / 'input.mp4', directory / 'input.mp4')
        identity = gpu_identity(result['input_sha256'], profile(version))
        state = dict(job_id=identifier, input_bytes=(directory / 'input.mp4').stat().st_size,
                     input_sha256=result['input_sha256'], analysis_profile=identity['profile'],
                     gpu_key=identity['gpu_key'])
        self.assertEqual(sha(directory / 'input.mp4'), state['input_sha256'])
        self.runtime.command(directory, state, 30.)  # Build the plan only; never run it.
        plan = read_json(directory / 'execution-plan.json')
        folder = self.runtime.plan_directory(state)
        copied = folder / ('input-' + version)
        shutil.copytree(original, copied)
        ledger = image_runtime.new_state(plan, {
            'plan_sha256': sha(directory / 'execution-plan.json'),
            'image_manifest_sha256': state['analysis_profile']['image_manifest_sha256']}, 1000.)
        ledger.update(status='completed_plan', last_observed_at=1001.)
        ledger['jobs'][0].update(status=result['status'], attempts=1, started_at=1000.,
                                 finished_at=1001., result_sha256=sha(copied / 'output/result.json'))
        save(folder / 'plan.json', plan)
        save(folder / 'state.json', ledger)
        return directory, state, folder, copied

    def test_saved_a_and_b_complete_prepare_and_reuse_match_original_bytes(self):
        for version in ('A', 'B'):
            with self.subTest(version=version):
                directory, state, folder, copied = self.fixture(version)
                output, digest, result = self.runtime.completed_output(directory, state)
                self.assertEqual(output, copied / 'output')
                self.assertEqual(digest, sha(SAVED / f'input_001-{version}/output/result.json'))
                self.assertEqual((result['version'], result['input_sha256']), (version, state['input_sha256']))
                outcome = self.runtime.prepare_output(directory, state)
                self.assertEqual(outcome, {'outcome': 'completed', 'event_count': 1})
                manifest = validate_local(directory / 'output')
                self.assertEqual(manifest['analysis_profile'], profile(version))
                self.assertEqual(manifest['gpu_key'], state['gpu_key'])
                self.assertEqual(manifest['input_sha256'], state['input_sha256'])
                self.assertEqual(manifest['normalized_result_sha256'], digest)
                self.assertEqual((directory / 'output/normalized-result.json').read_bytes(),
                                 (copied / 'output/result.json').read_bytes())
                for record in result['artifacts']:
                    self.assertEqual((directory / 'output' / record['path']).read_bytes(),
                                     (copied / 'output' / record['path']).read_bytes())
                before = inventory(directory / 'output')
                with patch('apps.be_worker.ab_evidence.prepare', side_effect=AssertionError('Completed package must be reused')):
                    self.assertEqual(self.runtime.prepare_output(directory, state), outcome)
                self.assertEqual(inventory(directory / 'output'), before)
                self.assertEqual(read_json(folder / 'state.json')['jobs'][0]['attempts'], 1)

    def test_request_input_profile_and_gpu_key_mismatch_are_blocked(self):
        directory, original, _, _ = self.fixture('A')
        variants = [dict(original, input_sha256='f' * 64),
                    dict(original, input_bytes=original['input_bytes'] + 1),
                    dict(original, gpu_key='gpu-v1:' + 'f' * 64)]
        other = gpu_identity(original['input_sha256'], profile('B'))
        variants.append(dict(original, analysis_profile=other['profile'], gpu_key=other['gpu_key']))
        for changed in variants:
            with self.subTest(changed=next(k for k in changed if changed[k] != original[k])):
                with self.assertRaises(ValueError):
                    self.runtime.completed_output(directory, changed)
        self.assertFalse((directory / 'output').exists())

    def test_ledger_identity_status_attempts_and_saved_plan_tampering_are_blocked(self):
        directory, state, folder, _ = self.fixture('B')
        original = read_json(folder / 'state.json')
        changes = [lambda x: x['identity'].update(plan_sha256='f' * 64),
                   lambda x: x['identity'].update(image_manifest_sha256='f' * 64),
                   lambda x: x.update(status='running'),
                   lambda x: x['jobs'][0].update(attempts=0),
                   lambda x: x['jobs'][0].update(version='A'),
                   lambda x: x.update(deadline_at=x['deadline_at'] + 1)]
        for change in changes:
            ledger = deepcopy(original)
            change(ledger)
            save(folder / 'state.json', ledger)
            with self.assertRaises(ValueError):
                self.runtime.completed_output(directory, state)
        save(folder / 'state.json', original)
        plan = read_json(folder / 'plan.json')
        plan['jobs'][0]['sha256'] = 'f' * 64
        save(folder / 'plan.json', plan)
        with self.assertRaises(ValueError):
            self.runtime.completed_output(directory, state)
        self.assertFalse((directory / 'output').exists())

    def test_copied_input_bytes_and_symlink_are_blocked(self):
        directory, state, _, copied = self.fixture('A')
        path = copied / 'input.mp4'
        original = path.read_bytes()
        path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, 'File identity mismatch'):
            self.runtime.completed_output(directory, state)
        path.unlink()
        path.symlink_to(directory / 'input.mp4')
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            self.runtime.completed_output(directory, state)

    def test_normalized_result_and_original_artifact_tampering_are_blocked(self):
        directory, state, folder, copied = self.fixture('B')
        result_path = copied / 'output/result.json'
        original = result_path.read_bytes()
        result_path.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'completed_result_receipt_mismatch'):
            self.runtime.completed_output(directory, state)
        result_path.write_bytes(original)
        result = read_json(result_path)
        artifact = copied / 'output' / result['media'][0]['path']
        with artifact.open('ab') as out:
            out.write(b'synthetic-tamper')
        with self.assertRaisesRegex(ValueError, 'File identity mismatch'):
            self.runtime.prepare_output(directory, state)
        self.assertFalse((directory / 'output').exists())

    def test_prepared_package_tampering_is_blocked_on_evidence_only_resume(self):
        directory, state, _, _ = self.fixture('B')
        self.runtime.prepare_output(directory, state)
        target = directory / 'output/evidence.json'
        original = target.read_bytes()
        target.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'artifact_integrity'):
            self.runtime.prepare_output(directory, state)
        self.assertEqual(target.read_bytes(), original + b'\n')


if __name__ == '__main__':
    unittest.main()
