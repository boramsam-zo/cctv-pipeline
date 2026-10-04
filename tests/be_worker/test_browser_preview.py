"""CPU display derivatives only. Two stored B assets; no inference/network."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from apps.be_worker import browser_preview as preview

ROOT=Path(__file__).resolve().parents[2]
PYTHON=ROOT/'.venv-model-check/bin/python'
SOURCE=ROOT/'artifacts/runpod/image-p4-20261001-01/image-validation-20261001-01/input_001-B/output/raw/event_000'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


class BrowserPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PYTHON.is_file():raise unittest.SkipTest('Existing .venv-model-check PyAV required; no install')
        cls.temp=TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name)
        cls.sources={role:SOURCE/f'event_000_{role}.mp4' for role in ('full','roi')}
        cls.original={role:sha(path) for role,path in cls.sources.items()}
        cls.receipts={}
        for role,path in cls.sources.items():
            cls.receipts[role]=preview.prepare(path,cls.root/role,source_sha256=cls.original[role],
                expected_frames=150,fps=25.,timeout_seconds=45,python_executable=PYTHON)

    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)

    def call(self,role='full',destination=None,**overrides):
        args={'source_sha256':self.original[role],'expected_frames':150,'fps':25.,
              'python_executable':PYTHON}
        args.update(overrides)
        return preview.prepare(self.sources[role],destination or self.directory/'new',**args)

    def copy_preview(self,role='full'):
        target=self.directory/role;shutil.copytree(self.root/role,target);return target

    def test_real_full_roi_are_verified_h264_with_original_resolution_pts_frames(self):
        for role,receipt in self.receipts.items():
            with self.subTest(role=role):
                self.assertEqual(receipt['source_metadata']['codec'],'mpeg4')
                self.assertEqual(receipt['output_metadata']['codec'],'h264')
                self.assertEqual(receipt['output_metadata']['decoded_frames'],150)
                self.assertEqual(receipt['output_metadata']['fps'],25.)
                self.assertEqual(receipt['output_metadata']['duration_s'],6.)
                self.assertTrue(receipt['output_metadata']['pts_verified'])
                for key in ('width','height'):self.assertEqual(receipt['source_metadata'][key],receipt['output_metadata'][key])
                self.assertFalse(receipt['evidence_replaced'])
                self.assertEqual(receipt['model_calls'],0)
                self.assertLess(receipt['elapsed_seconds'],45)
                self.assertEqual(sha(self.sources[role]),self.original[role])

    def test_cached_prepare_verifies_without_subprocess_or_decode(self):
        with patch.object(preview.subprocess,'run',side_effect=AssertionError('cache must not execute')):
            result=self.call(destination=self.root/'full')
        self.assertEqual(result,self.receipts['full'])

    def test_same_recipe_produces_identical_roi_bytes(self):
        repeated=self.call('roi')
        self.assertEqual(repeated['output_sha256'],self.receipts['roi']['output_sha256'])

    def test_source_hash_frames_and_fps_mismatch_rejected_without_overwrite(self):
        target=self.copy_preview();before=sha(target/'preview.mp4')
        for changes in ({'source_sha256':'0'*64},{'expected_frames':149},{'fps':30.}):
            with self.subTest(changes=changes),patch.object(preview.subprocess,'run',side_effect=AssertionError('noexec')):
                with self.assertRaises(ValueError):self.call(destination=target,**changes)
        self.assertEqual(sha(target/'preview.mp4'),before)

    def test_altered_preview_and_receipt_rejected_without_regeneration(self):
        for mode in ('bytes','recipe','fps','frame_count','codec','resolution','files'):
            target=self.directory/mode;shutil.copytree(self.root/'full',target)
            if mode=='bytes':
                path=target/'preview.mp4';path.write_bytes(path.read_bytes()+b'changed')
            elif mode=='files':(target/'unlisted.txt').write_text('extra')
            else:
                receipt=json.loads((target/'receipt.json').read_text())
                if mode=='recipe':receipt['recipe']['crf']='27'
                else:
                    key={'fps':'fps','frame_count':'decoded_frames','codec':'codec','resolution':'width'}[mode]
                    receipt['output_metadata'][key]='changed' if mode=='codec' else 1
                (target/'receipt.json').write_text(json.dumps(receipt))
            with self.subTest(mode=mode),patch.object(preview.subprocess,'run',side_effect=AssertionError('noexec')):
                with self.assertRaises(ValueError):self.call(destination=target)

    def test_timeout_keeps_partial_directory_and_never_retries_it(self):
        target=self.directory/'timeout'
        with patch.object(preview.subprocess,'run',side_effect=subprocess.TimeoutExpired('synthetic',1)) as run:
            with self.assertRaisesRegex(ValueError,'timeout_partial_preserved'):
                self.call(destination=target,timeout_seconds=1)
            self.assertEqual(run.call_count,1)
            self.assertTrue(target.exists())
            with self.assertRaises(ValueError):self.call(destination=target)
            self.assertEqual(run.call_count,1)

    def test_subprocess_failure_keeps_source_and_no_completion_receipt(self):
        failed=subprocess.CompletedProcess([],1,'','synthetic-error')
        with patch.object(preview.subprocess,'run',return_value=failed):
            with self.assertRaisesRegex(ValueError,'encoding_failed_partial_preserved'):self.call()
        self.assertFalse((self.directory/'new/receipt.json').exists())
        self.assertEqual(sha(self.sources['full']),self.original['full'])

    def test_invalid_limits_source_symlink_and_output_symlink_rejected(self):
        for changes in ({'timeout_seconds':0},{'timeout_seconds':121},{'expected_frames':True},
                        {'fps':float('nan')},{'fps':0},{'source_sha256':'bad'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.call(**changes)
        link=self.directory/'source.mp4';link.symlink_to(self.sources['full'])
        with self.assertRaises(ValueError):preview.prepare(link,self.directory/'new',source_sha256=self.original['full'],expected_frames=150,fps=25.)
        link=self.directory/'output';link.symlink_to(self.root/'full',target_is_directory=True)
        with self.assertRaises(ValueError):self.call(destination=link)

    def test_duplicate_receipt_keys_and_missing_receipt_rejected(self):
        target=self.copy_preview();path=target/'receipt.json'
        original=path.read_text();path.write_text(original[:-1]+',"schema_version":"cctv-browser-preview-v1"}')
        with self.assertRaises(ValueError):self.call(destination=target)
        path.unlink()
        with self.assertRaises(ValueError):self.call(destination=target)


if __name__=='__main__':unittest.main()
