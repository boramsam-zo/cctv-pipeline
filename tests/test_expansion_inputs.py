import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image

spec = importlib.util.spec_from_file_location('expansion', Path(__file__).resolve().parents[1] / 'scripts/prepare_expansion_inputs.py')
e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)

class ExpansionInputTests(unittest.TestCase):
    def candidate(self):
        return dict(sequence_id='MVI_20011', start_seconds=2, end_seconds=4,
                    target=0, label_origin='user_assumed_normal', human_reviewed=False)

    def request(self, c):
        return dict(purpose='colab_expansion_file_check_only', settings=dict(seconds=2,frames=16,image_size=224,fps_detrac=25),
                    accident_candidates=[], normal_candidates=[c]), dict(records=[dict(c,status='file_connected_not_decoded')])

    def test_real_zip_decode_and_original_time(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t); z=p/'source.zip'; image=io.BytesIO();Image.new('RGB',(80,40),'red').save(image,format='JPEG')
            with zipfile.ZipFile(z,'w') as archive:
                for n in range(51,101):archive.writestr(f'DETRAC-Images/MVI_20011/img{n:05d}.jpg',image.getvalue())
            digest=e.pp.sha256(z)
            result=e.prepare_one(self.candidate(),p,z,p/'output')
            self.assertEqual(result['input']['array_shape'],[16,224,224,3])
            self.assertEqual(result['input']['frames'][0]['actual_seconds'],2.08)
            self.assertEqual(result['input']['frames'][-1]['actual_seconds'],3.92)
            self.assertEqual(e.pp.sha256(z),digest)
            self.assertTrue((p/'output/preview.png').is_file())

    def test_missing_video(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)
            with self.assertRaises(e.pp.PreprocessingError):
                e.prepare_one(dict(source_path='real_videos/missing.mp4'),p,p/'x.zip',p/'out')

    def test_invalid_time_and_changed_receipt(self):
        a,b=self.request(self.candidate()); b['records'][0]['target']=1
        with self.assertRaises(e.pp.PreprocessingError):e.validate(a,b)
        c=self.candidate();c.update(start_seconds=4,end_seconds=2);a,b=self.request(c)
        with self.assertRaises(e.pp.PreprocessingError):e.validate(a,b)

    def test_short_video_preserves_failure(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'x.mp4').write_bytes(b'fixture')
            c=dict(source_path='real_videos/x.mp4',start_seconds=2,end_seconds=4)
            with patch.object(e.pp,'sample_frames',side_effect=e.pp.PreprocessingError('window_outside_duration')):
                with self.assertRaisesRegex(e.pp.PreprocessingError,'window_outside_duration'):
                    e.prepare_one(c,p,p/'x.zip',p/'out')

    def test_timeout_retained_not_normal(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);a,b=self.request(self.candidate())
            with patch.object(e.subprocess,'run',side_effect=subprocess.TimeoutExpired('test',1)):
                result=e.run(a,b,p/'videos',p/'images.zip',p/'out',1)
            self.assertIsNone(result['records'][0]['target'])
            self.assertEqual(result['records'][0]['declared_target'],0)
            self.assertEqual(result['records'][0]['reason'],'worker_timeout')
            self.assertTrue((p/'out/manifest.json').is_file())

    def test_original_output_overlap_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);a,b=self.request(self.candidate())
            with self.assertRaises(e.pp.PreprocessingError):e.run(a,b,p,p/'images.zip',p/'out')

    def test_missing_normal_frame(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)
            with zipfile.ZipFile(p/'x.zip','w'):pass
            with self.assertRaises(KeyError):e.prepare_one(self.candidate(),p,p/'x.zip',p/'out')

    def test_worker_pipeline_saves_real_normal_input(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);z=p/'images.zip'; data=io.BytesIO();Image.new('RGB',(32,32),'blue').save(data,format='JPEG')
            with zipfile.ZipFile(z,'w') as archive:
                for n in range(51,101):archive.writestr(f'DETRAC-Images/MVI_20011/img{n:05d}.jpg',data.getvalue())
            a,b=self.request(self.candidate())
            result=e.run(a,b,p/'videos',z,p/'out',10)
            self.assertEqual(result['counts'],{'prepared_not_split':1})
            self.assertEqual(result['records'][0]['target'],0)
            self.assertFalse(result['records'][0]['training_authorized'])
            self.assertTrue((p/'out/report.html').is_file())

    def test_reserved_normal_and_duplicates_rejected(self):
        a,b=self.request(self.candidate());a['official_normal_test']=['MVI_20011']
        with self.assertRaises(e.pp.PreprocessingError):e.validate(a,b)
        a,b=self.request(self.candidate());a['normal_candidates']*=2;b['records']*=2
        with self.assertRaises(e.pp.PreprocessingError):e.validate(a,b)

    def test_float_roundoff_preserves_original_times(self):
        for start,end in [(6.2,8.2),(14.1,16.1),(2.437,4.436999999999999)]:
            c=self.candidate();c.update(start_seconds=start,end_seconds=end)
            a,b=self.request(c)
            self.assertEqual(e.validate(a,b)[0],c)

    def test_genuinely_wrong_duration_rejected(self):
        for end in (3.99999,4.00001,5):
            c=self.candidate();c['end_seconds']=end;a,b=self.request(c)
            with self.assertRaisesRegex(e.pp.PreprocessingError,'expected_two_seconds'):
                e.validate(a,b)

    def resume_fixture(self, root):
        accident=dict(source_path='real_videos/a.mp4',start_seconds=1,end_seconds=3,
                      target=1,label_origin='source_annotation_derived')
        normal=[dict(self.candidate(),start_seconds=s,end_seconds=s+2) for s in (0,2,4)]
        request,receipt=self.request(normal[0]);request.update(accident_candidates=[accident],normal_candidates=normal)
        records=[accident]+normal
        receipt['records']=[dict(c,status='file_connected_not_decoded') for c in records]
        previous=root/'old';previous.mkdir()
        rows=[]
        for i,c in enumerate(records):
            row=dict(c,sample_id=f'expansion_{i:04d}',declared_target=c['target'],target=None,
                     status='error',reason='insufficient_distinct_frames' if i==0 else 'worker_timeout')
            if i==1:
                folder=previous/row['sample_id'];folder.mkdir()
                (folder/'frames.npy').write_bytes(b'preserved input fixture')
                (folder/'preview.png').write_bytes(b'preview fixture')
                row.update(status='prepared_not_split',reason=None,target=0,
                           input_path=row['sample_id']+'/frames.npy',preview_path=row['sample_id']+'/preview.png',
                           frames_sha256=e.pp.sha256(folder/'frames.npy'))
            if i==3:row.update(status='not_run',reason='total_time_limit')
            rows.append(row)
        e.pp.write_json(previous/'manifest.json',{'records':rows})
        return request,receipt,previous,e.pp.sha256(previous/'manifest.json')

    def test_resume_only_failed_and_unattempted_normal(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);a,b,previous,digest=self.resume_fixture(root)
            fake=subprocess.CompletedProcess([],0,json.dumps({'status':'error','reason':'fixture'}),'')
            with patch.object(e.subprocess,'run',return_value=fake) as worker:
                result=e.run(a,b,root/'videos',root/'archive.zip',root/'new',10,
                             previous_output=previous,previous_sha256=digest)
            self.assertEqual(worker.call_count,2)
            self.assertEqual(result['reused_success_count'],1)
            self.assertEqual(result['preserved_accident_error_count'],1)
            self.assertEqual(result['records'][1]['input_origin'],'previous')
            self.assertEqual(result['records'][1]['sample_id'],'expansion_0001')
            self.assertEqual(e.pp.sha256(previous/'manifest.json'),digest)
            for call in worker.call_args_list:
                payload=json.loads(call.kwargs['input'])
                self.assertNotIn('source_path',payload['candidate'])

    def test_resume_changed_array_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);a,b,previous,digest=self.resume_fixture(root)
            (previous/'expansion_0001/frames.npy').write_bytes(b'changed')
            with self.assertRaisesRegex(e.pp.PreprocessingError,'previous_array_hash_mismatch'):
                e.run(a,b,root/'videos',root/'archive.zip',root/'new',10,previous_output=previous,previous_sha256=digest)

    def test_resume_wrong_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);a,b,previous,digest=self.resume_fixture(root)
            with self.assertRaisesRegex(e.pp.PreprocessingError,'previous_manifest_hash_mismatch'):
                e.run(a,b,root/'videos',root/'archive.zip',root/'new',10,previous_output=previous,previous_sha256='wrong')

class ReservedPreparationTests(unittest.TestCase):
    def request(self):
        return {'status':'reserved_candidates_connected_not_evaluation_ready','records':[
            dict(sample_id='review_a',source_path='real_videos/missing.mp4',requested_class='accident',
                 start_seconds=1.,end_seconds=3.,target=None,official_iid_split='train',
                 label_origin='source_annotation_window_candidate_not_human_review',split='validation_reserved',
                 training_authorized=False,evaluation_authorized=False)]}

    def test_missing_video_is_error_not_normal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);videos=root/'videos';videos.mkdir()
            r=e.run(self.request(),None,videos,root/'unused.zip',root/'out',max_seconds=10,validation_reservations=True)
            self.assertEqual(r['records'][0]['status'],'error')
            self.assertIsNone(r['records'][0]['target'])
            self.assertFalse(r['model_run'])

    def test_approval_test_split_bad_window_and_limit_rejected(self):
        import copy
        for changes in ({'target':1},{'official_iid_split':'test'},{'end_seconds':1.},{'evaluation_authorized':True}):
            with self.subTest(changes=changes):
                r=copy.deepcopy(self.request());r['records'][0].update(changes)
                with self.assertRaises(e.pp.PreprocessingError):e.validate_reserved(r)
        r=self.request();r['records']*=11
        with self.assertRaises(e.pp.PreprocessingError):e.validate_reserved(r)

    def test_accident_success_keeps_unknown_target(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);videos=root/'videos';videos.mkdir()
            child=Mock(returncode=0,stdout=json.dumps({'status':'prepared_not_split','preview_path':'review_a/preview.png'}))
            with patch.object(e.subprocess,'run',return_value=child):
                r=e.run(self.request(),None,videos,root/'unused.zip',root/'out',max_seconds=10,validation_reservations=True)
            self.assertIsNone(r['records'][0]['target'])
            self.assertEqual(r['records'][0]['sample_id'],'review_a')
            self.assertEqual(r['records'][0]['split'],'validation_reserved')

class UnlabeledRecoveryTests(unittest.TestCase):
    def frames(self):
        return [dict(image=Image.new('RGB',(224,224),(i,0,0)),actual_seconds=1+(i+.5)/8,
                     requested_seconds=1+(i+.5)/8,pts=i,pts_time_base='1/8') for i in range(16)]
    def row(self):
        return dict(sample_id='review_a',source_path='real_videos/a.mp4',requested_class='accident',
                    start_seconds=1.,end_seconds=3.,target=None,official_iid_split='train',
                    label_origin='source_annotation_window_candidate_not_human_review',split='validation_reserved',
                    training_authorized=False,evaluation_authorized=False,status='error',reason='FileNotFoundError')
    def test_real_save_of_unlabeled_candidate_writes_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'a.mp4').write_bytes(b'fixture')
            with patch.object(e.pp,'sample_frames',return_value=(self.frames(),{})):
                result=e.prepare_one(self.row(),root,root/'unused',root/'out')
            self.assertTrue((root/'out/frames.npy').is_file())
            self.assertIsNone(result['input']['target'])
    def test_recovery_reuses_pngs_preserves_old_and_null_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';old.mkdir()
            e.pp.save_frames(old/'review_a',self.frames(),224,None,'review_a')
            e.pp.write_json(old/'manifest.json',dict(purpose='reserved_validation_input_preparation',records=[self.row()]))
            sha=e.pp.sha256(old/'manifest.json')
            r=e.recover_reserved_previews(old,root/'new',sha)
            self.assertEqual(r['counts'],{'prepared_not_split':1})
            self.assertIsNone(r['records'][0]['target'])
            self.assertFalse((old/'review_a/frames.npy').exists())
            self.assertTrue((root/'new/review_a/frames.npy').is_file())
            with self.assertRaises(e.pp.PreprocessingError):e.recover_reserved_previews(old,root/'bad','0'*64)

class ReferenceBenchmarkReuseTests(unittest.TestCase):
    def test_failed_new_inputs_preserve_ten_old_predictions_and_coverage(self):
        import shutil
        root_repo=Path(__file__).resolve().parents[1]
        source=root_repo/'artifacts/colab/reference-benchmark-20260923-01'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('benchmark-plan.json','existing-predictions.json'):
                shutil.copyfile(source/name,root/name)
            shutil.copyfile(root_repo/'data/local/accident/metadata-real.csv',root/'metadata-real.csv')
            (root/'checkpoint.pt').write_bytes(b'not a model; no inference')
            plan=e.pp.read_json(root/'benchmark-plan.json');plan['model_sha256']=e.pp.sha256(root/'checkpoint.pt')
            (root/'benchmark-plan.json').write_text(json.dumps(plan))
            def failed(request,*args,**kwargs):
                return {'records':[dict(r,status='error',reason='fixture_missing') for r in request['records']]}
            with patch.object(e,'run',side_effect=failed) as prepare,patch.object(e.subprocess,'run') as infer:
                result=e.run_reference_benchmark(root,root/'videos',root/'archive',root/'out')
            self.assertEqual(prepare.call_count,2);infer.assert_not_called()
            self.assertEqual(result['scored_segments'],10)
            self.assertEqual(result['failed_or_missing'],20)
            self.assertEqual(result['reused_predictions'],10)
            self.assertFalse(result['independent_validation'])

class ReviewedNormalBundleTests(unittest.TestCase):
    def test_reviewed_caption_does_not_claim_unreviewed_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'x.mp4').write_bytes(b'fixture')
            c=dict(source_path='real_videos/x.mp4',start_seconds=0,end_seconds=2,target=0,label_origin='user_segment_review')
            with patch.object(e.pp,'sample_frames',return_value=([],{})), patch.object(e.pp,'save_frames',return_value={'strictly_increasing_timestamps':True}) as save, patch.object(e.pp,'sha256',return_value='test'):
                e.prepare_one(c,p,p/'unused',p/'output')
            caption=save.call_args.kwargs['label_caption']
            self.assertIn('INPUT QUALITY PENDING',caption)
            self.assertNotIn('NOT HUMAN REVIEW',caption)
            self.assertEqual(save.call_args.args[3],0)

    def test_bundle_missing_files_preserve_reviewed_labels(self):
        bundle=Path(__file__).resolve().parents[1]/'artifacts/colab/normal-three-20260924-01/normal-three-code.zip'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            with zipfile.ZipFile(bundle) as z:z.extractall(p/'code')
            result=subprocess.run([__import__('sys').executable,str(p/'code/prepare_review.py'),str(p/'missing'),str(p/'output')],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            manifest=json.loads((p/'output/manifest.json').read_text())
            self.assertEqual(len(manifest['records']),3)
            for r in manifest['records']:
                self.assertEqual(r['target'],0)
                self.assertEqual(r['status'],'error')
                self.assertFalse(r['input_usable'])
                self.assertFalse(r['training_run'])
            # A tampered review must fail before creating any output.
            (p/'code/review.json').write_text('{}')
            result=subprocess.run([__import__('sys').executable,str(p/'code/prepare_review.py'),str(p/'missing'),str(p/'changed')],capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((p/'changed').exists())

class ReviewedNormalRepeatTests(unittest.TestCase):
    def test_repeat_keeps_label_but_not_training_ready(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'real_videos').mkdir();(p/'real_videos/x.mp4').write_bytes(b'fixture')
            c=dict(sample_id='normal_01',source='real_videos/x.mp4',start_seconds=0,end_seconds=2,target=0,label_origin='user_segment_review',training_execution_authorized=False)
            frames=[dict(image=Image.new('RGB',(32,18),'blue'),actual_seconds=(i//2)*.25,requested_seconds=(i+.5)/8,pts=i//2) for i in range(16)]
            with patch.object(e.pp,'sample_frames',return_value=(frames,{})) as sample:
                r=e.low_fps_preview_one(p,c,p/'out',reviewed_normal=True)
            self.assertTrue(sample.call_args.kwargs['allow_repeated_frames'])
            self.assertEqual(r['target'],0)
            self.assertEqual(r['distinct_frames'],8)
            self.assertEqual(r['repeated_frame_slots'],8)
            self.assertFalse(r['training_input_usable'])
            self.assertFalse(r['training_authorized'])
            self.assertIsNone(r['input']['target'])
            self.assertTrue((p/'out/preview.png').exists())
            with self.assertRaises(e.pp.PreprocessingError):e.low_fps_preview_one(p,c,p/'out',reviewed_normal=True)

    def test_unreviewed_or_expanded_scope_blocked(self):
        base=dict(target=0,label_origin='user_segment_review',training_execution_authorized=False,start_seconds=0,end_seconds=2)
        for change in [dict(target=1),dict(label_origin='user_assumed_normal'),dict(end_seconds=3),dict(training_execution_authorized=True)]:
            with self.assertRaisesRegex(e.pp.PreprocessingError,'reviewed_normal_scope_required'):
                e.low_fps_preview_one(Path('/unused'),dict(base,**change),Path('/unused/out'),reviewed_normal=True)
