"""Read-only viewer tests; tiny generated records, no model/data dependency."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from boram3_cctv import results_viewer as v

APP = Path(__file__).resolve().parents[1] / 'apps/results_viewer.py'
spec = importlib.util.spec_from_file_location('results_viewer_app', APP)
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class FakeStreamlit:
    """Tests our rendering contract only, not Streamlit/browser implementation."""
    def __init__(self, key, selection=0):
        self.key, self.selection, self.calls = key, selection, []
        self.sidebar = self

    def radio(self, *args, **kwargs): return self.key
    def selectbox(self, *args, **kwargs): return self.selection
    def expander(self, *args, **kwargs): return nullcontext()

    def __getattr__(self, name):
        if name in {'set_page_config','title','caption','subheader','info','warning','error',
                    'image','video','dataframe','line_chart','bar_chart','json'}:
            return lambda *a, **k: self.calls.append((name,a,k))
        raise AttributeError(name)


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.seed()
        ColabPredictionViewTests().fixture(self.root)
        MidtermViewTests().fixture(self.root)

    def put(self, path, value):
        file = self.root/path;file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value))
        return hashlib.sha256(file.read_bytes()).hexdigest()

    def blob(self, path, value=b'synthetic fixture media, not a playable video'):
        file=self.root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(value)
        return hashlib.sha256(value).hexdigest()

    def get(self,path): return json.loads((self.root/path).read_text())

    def seed(self):
        frames={'frames':[{'actual_seconds':i/8+.0625,'requested_seconds':i/8+.0625} for i in range(16)]}
        fp='artifacts/fixture/frames.json';fh=self.put(fp,frames)
        self.blob('artifacts/fixture/preview.png')
        row={'sample_id':'fixture_sample','truth':1,'prediction':0,'correct':False,
             'class_scores':{'accident':.2},'frames_sha256':fh,'loss':1.6}
        pred={'purpose':'review_existing_training_inputs_only','generalization_evaluation':False,
              'class_mapping':{'normal':0,'accident':1},'rows':[row],
              'input_origin':'synthetic_fixture','rule':'argmax','model_sha256':'fixture_model'}
        ph=self.put(v.REVIEW+'/predictions.json',pred)
        self.put(v.TRAIN+'/prepared_inputs.json',[{'sample_id':'fixture_sample','target':1,
            'label_review':{'status':'approved'},'frames_path':fp,'frames_sha256':fh,'start_seconds':0,'end_seconds':2}])
        self.put(v.TRAIN+'/result.json',{'actual_device':'test_cpu','completed_epochs':1,
            'training_updates':1,'class_counts':{'accident':1,'normal':0},'loop':{'progress':{'training_seconds':.1}},
            'timing':{'worker_total_seconds':.2}})
        self.put(v.LOSSES,{'fixed_predictions_sha256':ph,'fixed_model_training_inputs':{'rows':[row],'mean_loss':1.6},
            'training_log_summary':{'rows':[{'step':1,'phase':'probe','loss':2.}],
            'epochs':[{'epoch':1,'complete':False,'steps':[1],'epoch_mean':2.}],'observed_run_mean':2.},
            'function':'synthetic_CE','class_weight':None,'reduction':'mean','batch_size':1,
            'independent_validation':{'status':'not_run'}})
        self.put(v.SEQUENTIAL+'/config.json',{'window_seconds':2,'stride_seconds':1,'decision_rule':'argmax'})
        self.put(v.SEQUENTIAL+'/command.json',{'wall_seconds':15.1})
        self.put(v.SEQUENTIAL+'/result.json',{'independent_validation':False,'actual_device':'test_cpu',
            'video':{'observed_coverage_end_seconds':3},'timing':{'inference_seconds':10,'worker_seconds':13.6},
            'model_sha256':'fixture_model'})
        seq=[{'window_id':'w1','start_seconds':0,'end_seconds':2,'decision_source_time_sec':2,
              'status':'predicted','prediction':1,'accident_score':.8,'truth':None,'loss':None,'correct':None,
              'preview':'w1.png','frames':frames['frames']},
             {'window_id':'w2','start_seconds':1,'end_seconds':3,'decision_source_time_sec':3,
              'status':'insufficient_frames_or_time_gap','truth':None,'loss':None,'correct':None}]
        (self.root/v.SEQUENTIAL/'predictions.jsonl').write_text('\n'.join(map(json.dumps,seq)))
        self.blob(v.SEQUENTIAL+'/w1.png')
        self.put(v.EVENTS+'/events.json',{'purpose':'synthetic_test','model_calls':0,
            'events':[{'event_id':'test_event','status':'incomplete','positive_count':1}],
            'delivery_receipts':[{'action':'duplicate_ignored'}],'unclassified':[{'reason':'gap'}],
            'policy':{'purpose':'synthetic_test'},'user_review_status':'pending'})
        self.put(v.EVENTS+'/run.json',{'event_processing_seconds':.001,'peak_rss_bytes':10})
        self.put('artifacts/events/m3-synthetic-20260922-01/demo-final-execution.json',{'process_start_to_exit_seconds':.01})
        old={'sample_id':'devval_accident_01','source_sha256':'fixture_hash','start_seconds':1.,'end_seconds':3.,
             'preview_path':'unconfirmed/preview.png','frames_metadata_path':'unconfirmed/frames.json'}
        mh=self.put(v.RECEIPT+'/prepared/manifest.json',{'samples':[old]})
        ih=self.blob(v.RECEIPT+'/prepared/unconfirmed/preview.png')
        self.put(v.RECEIPT+'/prepared/unconfirmed/frames.json',frames)
        signed={**old,'video_path':'real_videos/fixture.mp4',
            'label_review':{'status':'approved','label':'accident','reviewer':'fixture_reviewer',
                            'reviewed_at':'2026-09-22','basis':'synthetic fixture'},
            'input_quality_review':{'status':'pending'},
            'human_submission':{'needs_more_context':True,'related_source_note':'모름'}}
        self.put(v.SIGNED+'/recorded/reviewed_plan.json',{'samples':[signed]})
        self.put(v.SIGNED+'/recorded/report.json',{'parent_manifest_sha256':mh,'preview_sha256':{'devval_accident_01':ih}})
        self.put(v.SIGNED+'/context/manifest.json',{'segments':[{'preview_path':'context.mp4',
                 'frame_mapping':[{'source_seconds':.75,'preview_seconds':0}]}]})
        self.blob(v.SIGNED+'/context/context.mp4')
        base=v.LATEST+'/run-mps'
        self.put(base+'/result.json',{'status':'completed_limited_real_training','validation_run':False,
            'training_segments':1,'training_updates':1,'completed_epochs':1,'actual_device':'fixture_cpu',
            'class_counts':{'accident':1,'normal':0},'reload_verified':True})
        self.put(base+'/config.json',{'batch_size':1})
        self.put(base+'/class_mapping.json',{'normal':0,'accident':1})
        self.put(base+'/prepared_inputs.json',[{'sample_id':'fixture_new','source_path':'real_videos/fixture.mp4',
            'target':1,'start_seconds':0.,'end_seconds':2.,'frames_path':fp,'frames_sha256':fh,
            'preview_path':'artifacts/fixture/preview.png','label_provenance':{'kind':'source_annotation_derived'}}])
        self.blob(base+'/probe/train.jsonl',(json.dumps({'step':1,'sample_ids':['fixture_new'],'loss':.5})+'\n').encode())
        self.blob(base+'/loop/train.jsonl',b'')
        self.put(v.LATEST+'/training-execution.json',{'whole_command_seconds':1.})
        self.put(v.LATEST+'/preparation-execution.json',{'whole_command_seconds':.2})
        self.put(v.EXPANSION+'/scale-500-20260923-01/expansion-plan.json', {
            'purpose':'metadata_only_expansion_proposal','training_ready':False,
            'candidates':[{'source_path':'fixture.mp4','proposed_role':'training_candidate',
                           'split':'unassigned','training_authorized':False,'start_seconds':0,'end_seconds':2}],
            'counts':{'training_candidate':1,'development_review_reserved':0},
            'requested':{'training_candidate':500,'development_review_reserved':20},
            'shortfalls':{'training_candidate':499,'development_review_reserved':20}})
        self.put(v.EXPANSION+'/allocation-20260923-01/split_inventory.user-reported.json',
                 {'splits':{'train':['MVI_1'],'test':['MVI_2']}})
        self.put(v.EXPANSION+'/scale-200-20260923-01/epoch-results-existing.json',
                 {'experiment':'t9-expansion-20260922-01','rows':[{'epoch':1,'train_loss_online':.5,
                  'normal_train_loss_online':.7,'train_f1':None,'validation_f1':None,
                  'validation_recall':None,'validation_pr_auc_ap':None,'interpretation':'fixture'}]})

    def test_expansion_is_plan_not_measured_performance(self):
        out=v.load_view(self.root,'expansion')
        self.assertEqual(out['tables'][0][1][0]['선정'],1)
        self.assertEqual(out['tables'][-1][1][0]['Validation F1'],'미측정')
        self.assertEqual(out['viewer_model_calls'],0)

    def test_expansion_wrong_count_rejected(self):
        path=v.EXPANSION+'/scale-500-20260923-01/expansion-plan.json'
        data=self.get(path);data['counts']['training_candidate']=500;self.put(path,data)
        with self.assertRaises(v.ViewerError):v.load_view(self.root,'expansion')

    def test_expansion_invented_historical_metric_rejected(self):
        path=v.EXPANSION+'/scale-200-20260923-01/epoch-results-existing.json'
        data=self.get(path);data['rows'][0]['validation_f1']=.99;self.put(path,data)
        with self.assertRaises(v.ViewerError):v.load_view(self.root,'expansion')

    def fingerprints(self):
        return {str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}

    def test_all_views_and_repeated_render_are_read_only(self):
        before=self.fingerprints()
        for key in v.PAGES:
            for _ in range(2):
                st=FakeStreamlit(key);ui.render(st,self.root)
                self.assertFalse([c for c in st.calls if c[0]=='error'],key)
        self.assertEqual(before,self.fingerprints())

    def test_wrong_training_prediction_remains_visible(self):
        out=v.load_view(self.root,'training');row=out['tables'][0][1][0]
        self.assertEqual((row['정답'],row['모델 예측'],row['맞음/틀림']),('사고','정상','틀림'))
        self.assertEqual(row['사고 점수'],.2)
        self.assertEqual(out['viewer_model_calls'],0)
        self.assertEqual(out['user_review_status'],'pending')

    def test_latest_uses_saved_losses_and_keeps_accuracy_unmeasured(self):
        out=v.load_view(self.root,'latest')
        self.assertEqual(out['tables'][0][1][0]['정답 출처'],'CSV 사고 시점 파생')
        self.assertEqual(out['tables'][1][1][0]['전체 평균 loss'],.5)
        self.assertTrue(all(r['상태']=='미측정' for r in out['tables'][2][1]))
        self.assertEqual(out['viewer_model_calls'],0)

    def test_latest_duplicate_resume_steps_are_rejected(self):
        base=v.LATEST+'/run-mps'
        self.blob(base+'/loop/train.jsonl',(self.root/base/'probe/train.jsonl').read_bytes())
        d=self.get(base+'/result.json');d['training_updates']=2;self.put(base+'/result.json',d)
        with self.assertRaisesRegex(v.ViewerError,'duplicate_or_unordered'):
            v.load_view(self.root,'latest')

    def test_latest_unknown_log_sample_and_missing_record_are_errors(self):
        base=v.LATEST+'/run-mps'
        self.blob(base+'/probe/train.jsonl',(json.dumps({'step':1,'sample_ids':['missing'],'loss':.5})+'\n').encode())
        with self.assertRaisesRegex(v.ViewerError,'unknown_training_log_sample'):
            v.load_view(self.root,'latest')
        (self.root/base/'result.json').unlink()
        with self.assertRaisesRegex(v.ViewerError,'artifact_missing'):
            v.load_view(self.root,'latest')

    def test_incomplete_epoch_is_not_presented_as_epoch_mean(self):
        out=v.load_view(self.root,'training')
        self.assertIsNone(out['tables'][2][1][0]['epoch 평균 손실'])
        self.assertIn('최종 고정 모델의 학습 자료 평균',out['details'][1][1])

    def test_duplicate_global_step_is_rejected(self):
        data=self.get(v.LOSSES);data['training_log_summary']['rows']*=2;self.put(v.LOSSES,data)
        with self.assertRaisesRegex(v.ViewerError,'duplicate_or_unordered_loss_step'):v.load_view(self.root,'training')

    def test_fixed_loss_bound_to_saved_predictions(self):
        data=self.get(v.REVIEW+'/predictions.json');data['model_sha256']='changed'
        self.put(v.REVIEW+'/predictions.json',data)
        with self.assertRaisesRegex(v.ViewerError,'saved_loss_prediction_hash_mismatch'):v.load_view(self.root,'training')

    def test_frame_tampering_is_not_reconstructed_silently(self):
        self.put('artifacts/fixture/frames.json',{'frames':[]})
        with self.assertRaisesRegex(v.ViewerError,'saved_frame_hash_mismatch'):v.load_view(self.root,'training')

    def test_continuous_unlabelled_failure_not_normal(self):
        out=v.load_view(self.root,'continuous');rows=out['tables'][0][1]
        self.assertEqual(rows[0]['정답'],'미확정')
        self.assertEqual(rows[1]['예측'],'미판정')
        self.assertIsNone(rows[1]['사고 점수'])
        self.assertEqual(len(rows),2)
        self.assertEqual(out['details'][0][1]['자식 실행을 감싼 초'],15.1)

    def test_continuous_unexpected_truth_is_not_accepted_as_validation(self):
        p=self.root/v.SEQUENTIAL/'predictions.jsonl'
        rows=[json.loads(l) for l in p.read_text().splitlines()];rows[0]['truth']=0
        p.write_text('\n'.join(map(json.dumps,rows)))
        with self.assertRaisesRegex(v.ViewerError,'unexpected_continuous_truth'):v.load_view(self.root,'continuous')

    def test_latest_accident_approval_independent_of_pending_quality(self):
        row=v.load_view(self.root,'review')['tables'][0][1][0]
        self.assertEqual((row['정답 상태'],row['정답'],row['입력 품질']),('approved','accident','pending'))

    def test_synthetic_events_separate_and_validation_is_null(self):
        event=v.load_view(self.root,'events');self.assertIn('시험용',event['notice'])
        val=v.load_view(self.root,'validation')
        self.assertTrue(all(r['값'] is None and r['상태']=='미실행' for r in val['tables'][0][1]))

    def test_new_validation_schema_requires_explicit_catalogue_update(self):
        value=self.get(v.LOSSES);value['independent_validation']['status']='completed';self.put(v.LOSSES,value)
        with self.assertRaisesRegex(v.ViewerError,'new_validation_result_requires_catalogue_update'):v.load_view(self.root,'validation')

    def test_missing_corrupt_file_is_visible_error_not_empty_success(self):
        p=self.root/v.TRAIN/'result.json';p.unlink()
        st=FakeStreamlit('training');ui.render(st,self.root)
        self.assertTrue(any(c[0]=='error' and 'artifact_missing' in c[1][0] for c in st.calls))
        p.write_text('{broken')
        with self.assertRaisesRegex(v.ViewerError,'invalid_json'):v.load_view(self.root,'training')

    def test_media_missing_keeps_table_and_explains_failure(self):
        (self.root/'artifacts/fixture/preview.png').unlink()
        st=FakeStreamlit('training');ui.render(st,self.root)
        self.assertTrue(any(c[0]=='error' and '미리보기' in c[1][0] for c in st.calls))
        self.assertTrue(any(c[0]=='dataframe' for c in st.calls))

    def test_media_stat_permission_error_is_reported(self):
        reader=v.ArtifactReader(self.root)
        with patch.object(reader,'path',side_effect=PermissionError('fixture')):
            with self.assertRaisesRegex(v.ViewerError,'artifact_read_failed'):
                reader.media('artifacts/fixture/preview.png')

    def test_path_escape_absolute_uri_and_model_are_blocked(self):
        reader=v.ArtifactReader(self.root)
        for name in ('../secret.json','/tmp/file.json','file:///tmp/file.json','data/local/a.json',
                     'artifacts/../secret.json','artifacts/model.pt','artifacts\\secret.json'):
            with self.subTest(name=name),self.assertRaises(v.ViewerError):reader.json(name)

    def test_symlink_escape_and_large_file_are_blocked(self):
        self.put('outside.json',{'secret':'fixture'})
        (self.root/'artifacts/link.json').symlink_to(self.root/'outside.json')
        reader=v.ArtifactReader(self.root)
        with self.assertRaisesRegex(v.ViewerError,'unsafe_artifact'):reader.json('artifacts/link.json')
        self.blob('artifacts/big.json',b' '*(2*1024*1024+1))
        with self.assertRaisesRegex(v.ViewerError,'artifact_too_large'):reader.json('artifacts/big.json')

    def test_nonfinite_json_bad_schema_and_unknown_page(self):
        (self.root/v.LOSSES).write_text('{"x":NaN}')
        with self.assertRaisesRegex(v.ViewerError,'nonfinite'):v.load_view(self.root,'validation')
        self.put(v.LOSSES,[])
        with self.assertRaisesRegex(v.ViewerError,'saved_schema_mismatch'):v.load_view(self.root,'validation')
        with self.assertRaisesRegex(v.ViewerError,'unknown_catalogue'):v.load_view(self.root,'unknown')

    def test_bad_saved_frame_order(self):
        with self.assertRaisesRegex(v.ViewerError,'saved_frame_order_invalid'):
            v.frame_rows([{'actual_seconds':2},{'actual_seconds':1}])

    def test_reference_video_selection_only_uses_existing_bytes(self):
        st=FakeStreamlit('review',selection=1);before=self.fingerprints();ui.render(st,self.root)
        self.assertEqual(sum(c[0]=='video' for c in st.calls),1)
        self.assertEqual(before,self.fingerprints())


if __name__ == '__main__':unittest.main()

class ColabPredictionViewTests(unittest.TestCase):
    def fixture(self,root):
        import shutil
        source=Path(__file__).resolve().parents[1]
        for rel in ['artifacts/colab/reserved-prediction-20260923-01/received/observed-summary.json',
                    'artifacts/colab/reserved-prediction-20260923-01/manifest.user.json']:
            p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/rel,p)
        return root/'artifacts/colab/reserved-prediction-20260923-01/received/observed-summary.json'

    def test_actual_received_predictions_do_not_become_metrics(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);self.fixture(root)
            data=v.load_view(root,'colab_predictions')
            self.assertEqual(len(data['tables'][0][1]),10)
            self.assertEqual(data['viewer_model_calls'],0)
            self.assertEqual(data['user_review_status'],'pending')
            self.assertEqual(len(data['media']),0)
            self.assertTrue(all(r['정답 상태']=='미확정' for r in data['tables'][0][1][:5]))
            st=FakeStreamlit('colab_predictions');ui.render(st,root)
            self.assertFalse(any(c[0]=='error' for c in st.calls))

    def test_changed_score_and_invented_metric_rejected(self):
        for key,value in [('accident_scores',[2]*5),('metrics',{'accuracy':1.})]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as t:
                root=Path(t);p=self.fixture(root);d=json.loads(p.read_text());d[key]=value;p.write_text(json.dumps(d))
                with self.assertRaises(v.ViewerError):v.load_view(root,'colab_predictions')


class MidtermViewTests(unittest.TestCase):
    def fixture(self, root):
        import shutil
        source=Path(__file__).resolve().parents[1]
        names=['artifacts/colab/epoch-continuation-20260923-01/received/'+n for n in
               ('result.json','summary.json','execution.json','epoch-results.json')]
        names+=['artifacts/colab/reference-benchmark-20260923-01/received/reference-metrics.json']
        for n in names:
            p=root/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/n,p)

    def test_pending_metrics_not_zero_and_partial_loss(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.fixture(root);data=v.load_view(root,'midterm')
        rows=dict(data['tables'])['모델 비교 · 참고 지표']
        self.assertTrue(all('미측정' in r['새 1910 step 모델'] for r in rows))
        self.assertIn('부분',data['charts'][0]['rows'][0]['집계 범위'])
        self.assertEqual(data['charts'][0]['kind'],'bar')
        self.assertEqual(data['viewer_model_calls'],0)
        self.assertEqual(data['user_review_status'],'pending')

    def test_wrong_model_binding_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);self.fixture(root)
            p=root/'artifacts/colab/reference-benchmark-20260923-01/received/reference-metrics.json'
            data=json.loads(p.read_text());data['model_sha256']='other';p.write_text(json.dumps(data))
            with self.assertRaises(v.ViewerError):v.load_view(root,'midterm')


class ReceivedEpochComparisonTests(unittest.TestCase):
    def test_real_receipt_shows_new_metrics_and_keeps_limits(self):
        data=v.load_view(Path(__file__).resolve().parents[1],'midterm')
        tables=dict(data['tables']);comparison=tables['모델 비교 · 참고 지표']
        self.assertEqual(comparison[0]['새 1910 step 모델'],'95.65%')
        rows=tables['새·이전 모델 전체30후보 비교 (0 정상 / 1 사고)']
        self.assertEqual(len(rows),30)
        self.assertEqual(sum(r['새 정오'] is False for r in rows),1)
        self.assertEqual(sum(r['새 예측'] is None for r in rows),7)
        self.assertEqual(data['user_review_status'],'pending')
        self.assertEqual(data['viewer_model_calls'],0)
