"""Small generated JPEG fixtures; never evidence of real normal traffic."""
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from boram3_cctv import preprocessing as pp


class DetracPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'data'
        self.folder = self.root / 'MVI_1'
        self.folder.mkdir(parents=True)
        for i in range(1, 51):
            Image.new('RGB', (48, 24), (i, 0, 0)).save(self.folder / f'img{i:05d}.jpg')
        (self.folder / '._img00001.jpg').write_text('sidecar')

    def test_order_timing_and_bounded_sampling(self):
        frames, info = pp.sample_detrac_images(self.root, 'MVI_1')
        self.assertEqual(len(frames), 16)
        numbers = [f['source_frame_number'] for f in frames]
        self.assertEqual(numbers, sorted(set(numbers)))
        self.assertEqual(numbers[0], 3)
        self.assertEqual(numbers[-1], 49)
        self.assertEqual(frames[-1]['actual_seconds'], 1.92)
        self.assertIn('not_measured', info['fps_basis'])

    def test_short_or_missing_image(self):
        (self.folder / 'img00050.jpg').unlink()
        with self.assertRaisesRegex(pp.PreprocessingError, 'incomplete'):
            pp.sample_detrac_images(self.root, 'MVI_1')

    def test_missing_sequence(self):
        with self.assertRaisesRegex(pp.PreprocessingError, 'sequence_missing'):
            pp.sample_detrac_images(self.root, 'MVI_2')

    def test_bad_selected_image_is_failure_not_normal(self):
        (self.folder / 'img00003.jpg').write_text('broken')
        r = pp.prepare_detrac_preview(self.root, ['MVI_1'], self.base / 'out')
        self.assertEqual(r['status'], 'needs_attention')
        self.assertIsNone(r['samples'][0]['target'])
        self.assertEqual(r['samples'][0]['reason'], 'image_decode_failed')

    def test_user_assumption_not_human_review_and_readonly(self):
        before = {p.name: pp.sha256(p) for p in self.folder.iterdir()}
        r = pp.prepare_detrac_preview(self.root, ['MVI_1'], self.base / 'out')
        row = r['samples'][0]
        self.assertEqual(row['target'], 0)
        self.assertEqual(row['label_origin'], 'user_assumed_normal')
        self.assertFalse(row['human_reviewed'])
        self.assertFalse(row['training_authorized'])
        self.assertIsNone(row['official_split'])
        self.assertEqual(row['input']['array_shape'], [16,224,224,3])
        self.assertEqual(before, {p.name: pp.sha256(p) for p in self.folder.iterdir()})
        self.assertTrue((self.base / 'out/report.html').exists())

    def test_reject_traversal_duplicates_and_excess(self):
        for names in [['../MVI_1'], ['MVI_1'] * 2, ['MVI_1','MVI_2','MVI_3','MVI_4'], []]:
            with self.assertRaises(pp.PreprocessingError):
                pp.prepare_detrac_preview(self.root, names, self.base / 'out')

    def test_protect_existing_output_and_originals(self):
        for out in [self.folder, self.root / 'output', self.base]:
            with self.assertRaises(pp.PreprocessingError):
                pp.prepare_detrac_preview(self.root, ['MVI_1'], out)

    def test_symlink_source_rejected(self):
        path = self.folder / 'img00003.jpg'
        path.unlink()
        path.symlink_to(self.folder / 'img00004.jpg')
        with self.assertRaisesRegex(pp.PreprocessingError, 'incomplete'):
            pp.sample_detrac_images(self.root, 'MVI_1')

    def test_inconsistent_resolution(self):
        Image.new('RGB', (50, 24)).save(self.folder / 'img00006.jpg')
        with self.assertRaisesRegex(pp.PreprocessingError, 'resolution'):
            pp.sample_detrac_images(self.root, 'MVI_1')


class DetracArchiveTests(unittest.TestCase):
    def setUp(self):
        import zipfile
        self.zipfile = zipfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.archive = self.base / 'images.zip'

    def make_zip(self, missing=False, extra=None):
        with self.zipfile.ZipFile(self.archive, 'w') as z:
            for i in range(1, 50 if missing else 51):
                z.writestr(f'DETRAC-images/MVI_1/img{i:05d}.jpg', b'fixture')
            z.writestr('MVI_2/img00001.jpg', b'not selected')
            z.writestr('__MACOSX/MVI_1/img00001.jpg', b'not data')
            if extra:
                z.writestr(extra, b'extra')

    def test_only_explicit_selection_extracted(self):
        self.make_zip()
        digest = pp.sha256(self.archive)
        r = pp.extract_detrac_selection(self.archive, ['MVI_1'], self.base/'out')
        self.assertEqual(r['file_count'], 50)
        self.assertFalse((self.base/'out/MVI_2').exists())
        self.assertEqual(pp.sha256(self.archive), digest)

    def test_missing_blocks_before_writing(self):
        self.make_zip(missing=True)
        with self.assertRaisesRegex(pp.PreprocessingError, 'missing'):
            pp.extract_detrac_selection(self.archive, ['MVI_1'], self.base/'out')
        self.assertFalse((self.base/'out').exists())

    def test_ambiguous_prefix(self):
        self.make_zip(extra='other/MVI_1/img00001.jpg')
        with self.assertRaisesRegex(pp.PreprocessingError, 'ambiguous'):
            pp.extract_detrac_selection(self.archive, ['MVI_1'], self.base/'out')

    def test_path_traversal(self):
        self.make_zip(extra='../MVI_1/img00001.jpg')
        with self.assertRaisesRegex(pp.PreprocessingError, 'unsafe'):
            pp.extract_detrac_selection(self.archive, ['MVI_1'], self.base/'out')

    def test_existing_output_protected(self):
        self.make_zip()
        with self.assertRaisesRegex(pp.PreprocessingError, 'already_exists'):
            pp.extract_detrac_selection(self.archive, ['MVI_1'], self.base)


class DetracNotebookTests(unittest.TestCase):
    def test_notebook_worker_with_generated_zip(self):
        import json, shutil, sys, zipfile
        notebook=json.loads(Path('notebooks/prepare_detrac_inputs_colab.ipynb').read_text())
        code=[c['source'] for c in notebook['cells'] if c['cell_type']=='code']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            shutil.copyfile(pp.__file__, root/'preprocessing.py')
            archive=root/'fixture.zip'
            fixture=root/'image.jpg'
            Image.new('RGB',(48,24)).save(fixture)
            with zipfile.ZipFile(archive,'w') as z:
                for i in range(1,51):
                    z.writestr(f'DETRAC-images/MVI_1/img{i:05d}.jpg',fixture.read_bytes())
            scope={'ROOT':root,'ARCHIVE':archive,'OUTPUT':root/'result','RUN_ID':'selected',
                   'SEQUENCES':['MVI_1'],'sys':sys,'json':json}
            import time
            scope['time']=time
            exec(compile(code[3],'<notebook-input-preparation>','exec'),scope)
            self.assertEqual(scope['execution']['status'],'completed')
            r=json.loads((root/'result/manifest.json').read_text())
            self.assertFalse(r['model_run'])
            self.assertEqual(r['samples'][0]['label_origin'],'user_assumed_normal')
            self.assertEqual(len(r['samples'][0]['input']['frames']),16)


class DetracSplitCellTests(unittest.TestCase):
    def run_fixture(self, names):
        import ast, json, subprocess, sys, zipfile
        n=json.loads(Path('notebooks/prepare_detrac_inputs_colab.ipynb').read_text())
        source=next(''.join(c['source']) for c in n['cells']
                    if c['cell_type']=='code' and 'official_split_inventory_not_training' in ''.join(c['source']))
        tree=ast.parse(source)
        worker=next(ast.literal_eval(x.value) for x in tree.body if isinstance(x,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=='worker' for t in x.targets))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'external/ua-detrac';folder.mkdir(parents=True)
            for split, ids in names.items():
                with zipfile.ZipFile(folder/f'DETRAC-{split}-Annotations-XML.zip','w') as z:
                    for i,name in enumerate(ids):z.writestr(f'folder{i}/{name}.xml','fixture not parsed')
                    z.writestr('__MACOSX/._MVI_99.xml','ignored')
            return subprocess.run([sys.executable,'-c',worker,str(root)],text=True,
                                  capture_output=True,timeout=5)

    def test_lists_and_membership_only(self):
        import json
        p=self.run_fixture({'Train':['MVI_20011','MVI_20012'],'Test':['MVI_20032']})
        self.assertEqual(p.returncode,0,p.stderr)
        r=json.loads(p.stdout)
        self.assertEqual(r['prepared_sequence_membership']['MVI_20032'],'test')
        self.assertFalse(r['training_run'])
        self.assertFalse(r['archives']['train']['xml_contents_read'])

    def test_train_test_overlap_rejected(self):
        self.assertNotEqual(self.run_fixture({'Train':['MVI_1'],'Test':['MVI_1']}).returncode,0)

    def test_duplicate_sequence_rejected(self):
        self.assertNotEqual(self.run_fixture({'Train':['MVI_1','MVI_1'],'Test':['MVI_2']}).returncode,0)

    def test_missing_zip_rejected(self):
        self.assertNotEqual(self.run_fixture({'Train':['MVI_1']}).returncode,0)
