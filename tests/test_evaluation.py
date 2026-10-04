"""Tiny CPU fixtures, not ACCIDENT samples or measured model accuracy."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from boram3_cctv import evaluation as ev, preprocessing as pp
from boram3_cctv.training import SegmentDataset
from boram3_cctv.training_data import TrainingError, fingerprint

torch.set_num_threads(2)


class Tiny(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.features = torch.nn.Sequential(torch.nn.AdaptiveAvgPool3d(1), torch.nn.Conv3d(3, 2, 1), torch.nn.BatchNorm3d(2))
        self.fc = torch.nn.Linear(2, 2)

    def forward(self, inputs):
        return self.fc(self.features(inputs).flatten(1))


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.signed = {'status':'approved','reviewer':'unit-test-only','reviewed_at':'2026-09-22','basis':'generated fixture'}
        trained = [{'source_sha256':'a'*64,'management_group':'train-source','upstream_original_id':'original-train','event_id':None,
                    'array_path':'old-host/input.npy','frames_path':'old-host/frames.json'}]
        pp.write_json(self.root/'trained.json',trained)
        self.manifest = {'schema_version':1,'purpose':'development_validation','policy_version':'draft-0.1',
            'class_mapping':{'normal':0,'accident':1},'preprocessing':ev.SETTINGS,'window_seconds':2,
            'decision_rule':'argmax_logits_tie_normal','max_segments':10,'max_inference_seconds':300,
            'training_inputs':{'path':'trained.json','sha256':pp.sha256(self.root/'trained.json')},
            'evaluation_approval':dict(self.signed),'segments':[]}
        state={'format_version':1,'model_name':'r3d_18','class_mapping':{'normal':0,'accident':1},
            'config':{'preprocessing':ev.SETTINGS},'model_state':Tiny().state_dict(),
            'provenance':{'prepared_inputs_sha256':fingerprint([
                {k:v for k,v in r.items() if k not in ('array_path','frames_path')} for r in trained])}}
        torch.save(state,self.root/'model.pt')
        self.manifest['model']={'path':'model.pt','sha256':pp.sha256(self.root/'model.pt')}
        for i,target in enumerate((0,1)):
            sid=f'fixture_{i}'; directory=self.root/sid;directory.mkdir()
            frames=[{'image':Image.new('RGB',(32,32),(i*100,0,0)), 'requested_seconds':(j+.5)/8,
                    'actual_seconds':(j+.5)/8,'pts':j,'pts_time_base':'1/8'} for j in range(16)]
            pp.save_frames(directory, frames,224,target,sid)
            sha=str(i+1)*64
            record={'sample_id':sid,'source_path':sid+'.mp4','source_reference':'synthetic unit fixture only',
                'source_sha256':sha,'management_group':f'independent-{i}', 'split':'validation',
                'training_authorized':False,'final_test':False,'official_iid_split':'not_applicable','dataset':'unit_fixture',
                'start_seconds':0.,'end_seconds':2.,'target':target,
                'label_review':dict(self.signed,scope='segment',label=('normal','accident')[target],
                       source_sha256=sha,start_seconds=0.,end_seconds=2.,policy_version='draft-0.1'),
                'independence_review':dict(self.signed,source_sha256=sha,independent_from_training=True,compared_training_groups=['train-source']),
                'usage_review':dict(self.signed,cloud_evaluation_allowed=True,local_evaluation_allowed=True),
                'split_review':dict(self.signed)}
            for kind,filename in [('array','frames.npy'),('frames','frames.json'),('preview','preview.png')]:
                record[kind+'_path']=f'{sid}/{filename}'
                record[kind+'_sha256']=pp.sha256(directory/filename)
            record['input_quality_review']=dict(self.signed,no_crop_or_missing_scene=True,
                **{k:record[k] for k in ('array_sha256','frames_sha256','preview_sha256')})
            self.manifest['segments'].append(record)

    def tearDown(self):
        self.temp.cleanup()

    def assert_blocked(self, value, pattern):
        with self.assertRaisesRegex((TrainingError,pp.PreprocessingError),pattern):
            ev.preflight(self.root,value)

    def reference_manifest(self):
        m=copy.deepcopy(self.manifest)
        m.update(purpose=ev.REFERENCE_PURPOSE,max_segments=3)
        for s in m['segments']:
            s['independence_review']={'status':'pending','independent_from_training':None}
        m['reference_comparison_approval']=dict(self.signed,purpose=ev.REFERENCE_PURPOSE,
            independence_unconfirmed_accepted=True,not_independent_validation=True,
            scope_sha256=ev.reference_scope_sha256(m))
        return m

    def test_reference_accepts_acknowledged_unknown_but_strict_still_blocks(self):
        m=self.reference_manifest()
        self.assertEqual(len(ev.preflight(self.root,m)),2)
        self.assertIsNone(m['segments'][0]['independence_review']['independent_from_training'])
        m['purpose']='development_validation'
        self.assert_blocked(m,'human_review_required')

    def test_reference_requires_explicit_scope_and_cannot_expand_approval(self):
        m=self.reference_manifest();m.pop('reference_comparison_approval')
        self.assert_blocked(m,'human_review_required')
        for key,value in [('purpose','development_validation'),('scope_sha256','0'*64),
                          ('not_independent_validation',False),('independence_unconfirmed_accepted',False)]:
            m=self.reference_manifest();m['reference_comparison_approval'][key]=value
            self.assert_blocked(m,'reference_scope_approval_required')
        for key,value in [('start_seconds',.25),('target',1),('array_sha256','0'*64)]:
            m=self.reference_manifest();m['segments'][0][key]=value
            self.assert_blocked(m,'reference_scope_approval_required')
        m=self.reference_manifest();m['max_segments']=4
        self.assert_blocked(m,'reference_comparison_limit')
        m=self.reference_manifest();m['max_inference_seconds']=301
        self.assert_blocked(m,'inference_limit')

    def test_reference_does_not_allow_known_training_overlap_or_test(self):
        for key,value,reason in [('source_sha256','a'*64,'training_source_or_event_overlap'),
            ('management_group','train-source','training_source_or_event_overlap'),
            ('event_id','original-train','training_source_or_event_overlap'),
            ('official_iid_split','test','official_test_or_unknown'),('final_test',True,'no_final_test')]:
            m=self.reference_manifest();m['segments'][0][key]=value
            m['reference_comparison_approval']['scope_sha256']=ev.reference_scope_sha256(m)
            self.assert_blocked(m,reason)

    def test_reference_keeps_labels_quality_usage_and_integrity_gates(self):
        for field in ('label_review','input_quality_review','usage_review'):
            m=self.reference_manifest();m['segments'][0][field]['status']='pending'
            self.assert_blocked(m,'review|label')
        m=self.reference_manifest();m['segments'][0]['usage_review']['cloud_evaluation_allowed']=False
        self.assert_blocked(m,'cloud_use_approval_required')
        m=self.reference_manifest();m['segments'][0]['input_quality_review']['array_sha256']='0'*64
        self.assert_blocked(m,'quality_review_not_bound')
        m=self.reference_manifest();(self.root/m['segments'][0]['array_path']).write_bytes(b'bad')
        self.assert_blocked(m,'file_hash_mismatch')

    def test_reference_report_keeps_errors_and_does_not_claim_independent_validation(self):
        m=self.reference_manifest();records=ev.preflight(self.root,m)
        pred=[{'sample_id':records[0]['sample_id'],'status':'predicted','prediction':1,
               'accident_score':.8,'logits':[0.,1.]}]
        result=ev.compare(records,pred,purpose=m['purpose'])
        self.assertFalse(result['independent_validation'])
        self.assertEqual(result['independence_status'],'unconfirmed')
        self.assertEqual(result['false_positives'],1)
        self.assertEqual(result['coverage'],{'requested':2,'predicted':1,'failed_or_not_run':1})
        self.assertIsNone(result['rows'][1]['correct'])
        out=self.root/'reference-report';out.mkdir();ev.write_report(out,result,records)
        html=(out/'report.html').read_text()
        self.assertIn('독립성 미확인',html)
        self.assertIn('독립 검증 성능이 아님',html)
        self.assertNotIn('새 독립 원본의 예비 검증',html)

    def test_reference_parent_propagates_purpose_into_saved_result(self):
        m=self.reference_manifest();pp.write_json(self.root/'reference.json',m)
        with patch.object(ev.subprocess,'Popen') as spawn:
            spawn.return_value.poll.return_value=0;spawn.return_value.returncode=0
            result=ev.run_evaluation(self.root,'reference.json',self.root/'reference-run','cpu',execution_location='local')
        saved=pp.read_json(self.root/'reference-run/comparison.json')
        self.assertEqual(saved['purpose'],ev.REFERENCE_PURPOSE)
        self.assertFalse(saved['independent_validation'])
        self.assertEqual(result['coverage']['predicted'],0)

    def test_reference_bundle_ready_does_not_approve_independence(self):
        module,_=self.bundle_review_fixture()
        m=self.reference_manifest();pp.write_json(self.root/'reference.json',m)
        receipt=module.build(self.root,'reference.json',self.root/'reference-bundle')
        self.assertTrue(receipt['evaluation_ready'])
        self.assertEqual(receipt['pending_reviews'],[])
        self.assertFalse(receipt['independent_validation'])
        self.assertEqual(receipt['independence_status'],'unconfirmed')
        self.assertEqual(m['segments'][0]['independence_review']['status'],'pending')

    def test_pending_is_not_evaluation_data(self):
        self.manifest['segments']=[]
        self.assert_blocked(self.manifest,'approved_validation_segments_required')

    def test_unapproved_label_and_quality(self):
        for key in ('label_review','input_quality_review','independence_review','usage_review'):
            with self.subTest(key=key):
                m=copy.deepcopy(self.manifest); m['segments'][0][key]['status']='unreviewed'
                self.assert_blocked(m,'review|label')

    def test_train_overlap_by_hash_group_and_original(self):
        for key,value in [('source_sha256','a'*64),('management_group','train-source'),('upstream_original_id','original-train')]:
            m=copy.deepcopy(self.manifest);m['segments'][0][key]=value
            self.assert_blocked(m,'training_source_or_event_overlap')

    def test_test_and_unknown_split_blocked(self):
        for value in ('test',None):
            m=copy.deepcopy(self.manifest);m['segments'][0]['official_iid_split']=value
            self.assert_blocked(m,'official_test_or_unknown')
        self.manifest['segments'][0]['final_test']=True
        self.assert_blocked(self.manifest,'no_final_test')

    def test_accident_row_verified_against_csv(self):
        csv=self.root/'metadata.csv'; csv.write_text('path,split_in_distribution\nfixture_0.mp4,test\n')
        s=self.manifest['segments'][0];s.update(dataset='ACCIDENT',official_iid_split='train',
            metadata={'path':'metadata.csv','sha256':pp.sha256(csv),'csv_row':2})
        self.assert_blocked(self.manifest,'official_iid_train_row_required')

    def test_changed_inputs_or_quality_binding(self):
        m=copy.deepcopy(self.manifest);m['segments'][0]['input_quality_review']['array_sha256']='f'*64
        self.assert_blocked(m,'quality_review_not_bound')
        (self.root/'fixture_0/frames.npy').write_bytes(b'changed')
        self.assert_blocked(self.manifest,'file_hash_mismatch')

    def test_paths_missing_and_escape(self):
        for value in ('../outside.npy','/tmp/outside.npy','absent.npy'):
            with self.subTest(value=value):
                m=copy.deepcopy(self.manifest);m['segments'][0]['array_path']=value
                self.assert_blocked(m,'outside_bundle|missing_file')

    def test_changed_duration_and_limits_blocked(self):
        m=copy.deepcopy(self.manifest);m['segments'][0]['end_seconds']=6
        self.assert_blocked(m,'two_second')
        for key,value in [('max_segments',11),('max_inference_seconds',301)]:
            m=copy.deepcopy(self.manifest);m[key]=value
            self.assert_blocked(m,'limit')

    def test_loader_and_predict_preserve_weights_and_bn(self):
        records=ev.preflight(self.root,self.manifest)
        model,device=ev.load_model(self.root,self.manifest,'cpu',factory=Tiny)
        before={k:v.clone() for k,v in model.state_dict().items()}
        out=self.root/'result';out.mkdir()
        ev.predict(model,SegmentDataset(records,ev.SETTINGS),device,out,300)
        predictions=[json.loads(line) for line in (out/'predictions.jsonl').read_text().splitlines()]
        self.assertEqual(len(predictions),2)
        self.assertTrue(all('target' not in p and p['status']=='predicted' for p in predictions))
        self.assertTrue(all(torch.equal(v,model.state_dict()[k]) for k,v in before.items()))
        self.assertTrue(pp.read_json(out/'worker_result.json')['model_and_bn_unchanged'])
        result=ev.compare(records,predictions)
        ev.write_report(out,result,records)
        self.assertTrue((out/'report.html').is_file())
        self.assertEqual(result['coverage']['predicted'],2)
        self.assertEqual(len(result['rows'][0]['actual_seconds']),16)

    def test_mapping_mismatch_fails_before_forward(self):
        state=torch.load(self.root/'model.pt',weights_only=True);state['class_mapping']={'normal':1,'accident':0}
        torch.save(state,self.root/'model.pt');self.manifest['model']['sha256']=pp.sha256(self.root/'model.pt')
        with self.assertRaisesRegex(TrainingError,'saved_model_configuration_mismatch'):
            ev.load_model(self.root,self.manifest,'cpu',factory=Tiny)

    def test_training_fingerprint_ignores_paths_but_file_checksum_is_enforced(self):
        path=self.root/'trained.json'
        trained=pp.read_json(path)
        state=torch.load(self.root/'model.pt',weights_only=True)
        self.assertNotEqual(state['provenance']['prepared_inputs_sha256'],self.manifest['training_inputs']['sha256'])
        trained[0]['array_path']='new-host/input.npy'
        trained[0]['frames_path']='new-host/frames.json'
        path.write_text(json.dumps(trained,indent=4)+'\n')
        with self.assertRaisesRegex(TrainingError,'file_hash_mismatch'):
            ev.load_model(self.root,self.manifest,'cpu',factory=Tiny)
        self.manifest['training_inputs']['sha256']=pp.sha256(path)
        with patch.object(Tiny,'forward',side_effect=AssertionError('no inference needed')):
            model,_=ev.load_model(self.root,self.manifest,'cpu',factory=Tiny)
        self.assertFalse(model.training)

    def test_changed_training_content_rejected_even_with_new_file_checksum(self):
        path=self.root/'trained.json'; trained=pp.read_json(path)
        trained[0]['management_group']='changed'
        path.write_text(json.dumps(trained))
        self.manifest['training_inputs']['sha256']=pp.sha256(path)
        with self.assertRaisesRegex(TrainingError,'saved_model_training_content_mismatch'):
            ev.load_model(self.root,self.manifest,'cpu',factory=Tiny)

    def test_saved_fingerprint_rejects_the_old_file_hash_definition(self):
        state=torch.load(self.root/'model.pt',weights_only=True)
        state['provenance']['prepared_inputs_sha256']=self.manifest['training_inputs']['sha256']
        with self.assertRaisesRegex(TrainingError,'saved_model_training_content_mismatch'):
            ev.validate_saved_configuration(self.root,self.manifest,state)

    def test_local_cloud_permissions_are_explicit_and_independent(self):
        for s in self.manifest['segments']:
            s['usage_review']['cloud_evaluation_allowed']=False
        self.assertEqual(len(ev.preflight(self.root,self.manifest,execution_location='local')),2)
        self.assert_blocked(self.manifest,'cloud_use_approval_required')
        m=copy.deepcopy(self.manifest);m['segments'][0]['usage_review']['local_evaluation_allowed']=False
        with self.assertRaisesRegex(TrainingError,'local_use_approval_required'):
            ev.preflight(self.root,m,execution_location='local')
        with self.assertRaisesRegex(TrainingError,'explicit_execution_location_required'):
            ev.preflight(self.root,m,execution_location='cpu')

    def test_local_still_requires_labels_independence_and_safe_split(self):
        cases=[('label_review','status','unreviewed','approved_segment_label'),
               ('independence_review','independent_from_training',False,'independence_unconfirmed'),
               (None,'official_iid_split','test','official_test_or_unknown')]
        for parent,key,value,reason in cases:
            with self.subTest(key=key):
                m=copy.deepcopy(self.manifest)
                if parent:
                    m['segments'][0][parent][key]=value
                    if parent=='label_review':m['segments'][0][parent]['label']=None
                else:m['segments'][0][key]=value
                with self.assertRaisesRegex((TrainingError,pp.PreprocessingError),reason):
                    ev.preflight(self.root,m,execution_location='local')

    def test_parent_passes_location_to_worker_and_records_it(self):
        for s in self.manifest['segments']:s['usage_review']['cloud_evaluation_allowed']=False
        pp.write_json(self.root/'local.json',self.manifest)
        with patch.object(ev.subprocess,'Popen') as spawn:
            spawn.return_value.poll.return_value=0
            spawn.return_value.returncode=0
            result=ev.run_evaluation(self.root,'local.json',self.root/'local-run','cpu',execution_location='local')
        self.assertEqual(spawn.call_args.args[0][-2:],['cpu','local'])
        self.assertEqual(result['execution_location'],'local')
        self.assertEqual(result['coverage']['predicted'],0)
        self.assertEqual(pp.read_json(self.root/'local-run/run_environment.json')['execution_location'],'local')

    def test_worker_rechecks_explicit_location_before_model_loading(self):
        pp.write_json(self.root/'local.json',self.manifest)
        with patch.object(ev,'preflight',side_effect=TrainingError('test stops before model')) as check:
            with self.assertRaisesRegex(TrainingError,'test stops'):
                ev._worker(str(self.root),str(self.root/'local.json'),str(self.root/'out'),'cpu','local')
        self.assertEqual(check.call_args.kwargs,{'execution_location':'local'})

    def test_actual_saved_versions_match_historical_content_definition(self):
        # Read small existing JSON only, never load a real model or video in unit tests.
        root=Path(__file__).resolve().parents[1]
        directory=root/'artifacts/training/limited-real-iid-20260921-01'
        if not (directory/'versions.json').is_file():self.skipTest('optional local historical JSON absent')
        trained=pp.read_json(directory/'prepared_inputs.json')
        saved=pp.read_json(directory/'versions.json')
        manifest={'training_inputs':{'path':str((directory/'prepared_inputs.json').relative_to(root)),
                                    'sha256':pp.sha256(directory/'prepared_inputs.json')}}
        state={'format_version':1,'model_name':'r3d_18','class_mapping':ev.CLASS_MAPPING,
               'config':{'preprocessing':ev.SETTINGS},'provenance':saved}
        hashes=ev.validate_saved_configuration(root,manifest,state)
        self.assertEqual(hashes['prepared_inputs_content_sha256'],saved['prepared_inputs_sha256'])
        self.assertNotEqual(hashes['training_inputs_file_sha256'],hashes['prepared_inputs_content_sha256'])

    def test_failure_not_normal_or_silently_removed(self):
        records=ev.preflight(self.root,self.manifest)
        out=self.root/'fail';out.mkdir()
        model,device=ev.load_model(self.root,self.manifest,'cpu',factory=Tiny)
        with patch.object(SegmentDataset,'__getitem__',side_effect=OSError('fixture read failure')):
            ev.predict(model,SegmentDataset(records,ev.SETTINGS),device,out,300)
        predictions=[json.loads(line) for line in (out/'predictions.jsonl').read_text().splitlines()]
        result=ev.compare(records,predictions)
        self.assertEqual(result['coverage']['failed_or_not_run'],2)
        self.assertIsNone(result['accuracy'])
        self.assertIsNone(result['mean_loss'])
        self.assertTrue(all(r.get('prediction') is None for r in result['rows']))

    def test_soft_deadline_no_new_forward(self):
        records=ev.preflight(self.root,self.manifest)
        model,device=ev.load_model(self.root,self.manifest,'cpu',factory=Tiny)
        out=self.root/'timeout';out.mkdir()
        ticks=iter([0,0,301,302,303])
        with patch.object(model,'forward',side_effect=AssertionError('must not run')):
            ev.predict(model,SegmentDataset(records,ev.SETTINGS),device,out,300,clock=lambda:next(ticks))
        result=ev.compare(records,[json.loads(line) for line in (out/'predictions.jsonl').read_text().splitlines()])
        self.assertEqual(result['coverage']['predicted'],0)

    def test_metrics_false_positive_false_negative_and_loss(self):
        records=ev.preflight(self.root,self.manifest)
        pred=[{'sample_id':r['sample_id'],'status':'predicted','prediction':1-r['target'],
               'accident_score':.8 if r['target']==0 else .2,'logits':[0.,1.] if r['target']==0 else [1.,0.]} for r in records]
        result=ev.compare(records,pred)
        self.assertEqual(result['confusion_matrix_true_rows_normal_accident'],[[0,1],[1,0]])
        self.assertEqual(result['accident_f1'],0.)
        self.assertAlmostEqual(result['mean_loss'],1.3132616,places=6)
        self.assertEqual(result['counts']['normal']['source_files'],1)

    def test_hard_timeout_kills_worker_without_cpu_retry(self):
        class Child:
            killed = False
            def poll(self): return None
            def kill(self): self.killed = True
            def wait(self): return -9
        out=self.root/'hard-timeout';out.mkdir()
        pp.write_json(out/'loop_started.json', {'monotonic': 10.})
        child=Child()
        reason=ev.supervise(child,out,0,300,clock=lambda:310.,pause=lambda _:None)
        self.assertEqual(reason,'inference_hard_timeout')
        self.assertTrue(child.killed)

    def test_startup_timeout_is_separate(self):
        class Child:
            killed = False
            def poll(self): return None
            def kill(self): self.killed = True
            def wait(self): return -9
        child=Child()
        self.assertEqual(ev.supervise(child,self.root,0,300,clock=lambda:120.,pause=lambda _:None),'startup_timeout')
        self.assertTrue(child.killed)

    def test_export_preserves_previous_result_and_records_total(self):
        out=self.root/'export-source';out.mkdir()
        pp.write_json(out/'timing.json',{'active_total_before_drive_export_seconds':2.,
            'excludes':['package setup','Drive mount','interactive pauses']})
        dest=self.root/'drive-fixture'
        ev.export_results(out,dest)
        self.assertGreaterEqual(pp.read_json(dest/'drive_export_timing.json')['active_total_through_drive_copy_seconds'],2.)
        with self.assertRaises(FileExistsError): ev.export_results(out,dest)

    def test_transfer_allowlist_excludes_secrets_and_training_frames(self):
        import importlib.util, zipfile
        repo=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('bundle_script',repo/'scripts/prepare_colab_bundle.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for rel in module.CODE_FILES + ['notebooks/evaluate_r3d18_colab.ipynb']:
            p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
        (self.root/'.env').write_text('UNIT_TEST_SECRET=not-a-real-secret')
        m=copy.deepcopy(self.manifest);m['segments']=[];m['support_files']=[]
        pp.write_json(self.root/'pending.json',m)
        receipt=module.build(self.root,'pending.json',self.root/'transfer')
        self.assertFalse(receipt['evaluation_ready'])
        with zipfile.ZipFile(self.root/'transfer/colab-evaluation.zip') as z:
            self.assertNotIn('.env',z.namelist())
            self.assertNotIn('fixture_0/frames.npy',z.namelist())
            self.assertIn('model.pt',z.namelist())

    def test_notebook_calls_module_and_has_no_saved_outputs(self):
        repo=Path(__file__).resolve().parents[1]
        nb=pp.read_json(repo/'notebooks/evaluate_r3d18_colab.ipynb')
        source=''
        for cell in nb['cells']:
            if cell['cell_type']=='code':
                code=''.join(cell['source']);compile(code,'notebook-cell','exec')
                self.assertEqual(cell['outputs'],[])
                self.assertIsNone(cell['execution_count'])
                source+=code
        self.assertIn('ev.run_evaluation(',source)
        for prohibited in ('loss.backward(', 'optimizer.step(', 'pip install', '/Users/', 'DEVICE = "mps"'):
            self.assertNotIn(prohibited,source)

    def bundle_review_fixture(self):
        """Saved PNGs with different pixels at each time; never real CCTV labels."""
        import importlib.util
        repo = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('bundle_review_script', repo/'scripts/prepare_colab_bundle.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for rel in module.CODE_FILES + ['notebooks/evaluate_r3d18_colab.ipynb']:
            path = self.root/rel; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}')
        csv = self.root/'metadata.csv'
        csv.write_text('path,split_in_distribution\nreal_videos/fixture_0.mp4,train\nreal_videos/fixture_1.mp4,train\n')
        plan = {'schema_version':1, 'policy_version':'draft-0.1', 'frame_count':16,
                'image_size':224, 'time_basis':'local_clip_seconds', 'samples':[]}
        rows = []
        for i, target in enumerate((0,1)):
            sid = ('devval_normal_01','devval_accident_01')[i]
            sha = str(i+1)*64
            plan['samples'].append({'sample_id':sid, 'video_path':f'real_videos/fixture_{i}.mp4',
                'source_sha256':sha, 'start_seconds':0., 'end_seconds':2.,
                'label_review':{'status':'unreviewed','label':None}})
            frames = [{'image':Image.new('RGB',(32,32),(j*10,i*50,0)),
                       'requested_seconds':(j+.5)/8, 'actual_seconds':(j+.5)/8,
                       'pts':j, 'pts_time_base':'1/8'} for j in range(16)]
            details = pp.save_frames(self.root/'reviews'/sid,frames,224,None,sid)
            rows.append({'sample_id':sid,'source_path':f'real_videos/fixture_{i}.mp4',
                'source_sha256':sha,'start_seconds':0.,'end_seconds':2.,'status':'unconfirmed',
                'original_unchanged':True,'processed_frames':details,
                'frames_metadata_path':f'{sid}/frames.json','preview_path':f'{sid}/preview.png',
                'official_splits':{'split_in_distribution':'train'}})
        pp.write_json(self.root/'plan.json',plan)
        parent = {'plan_sha256':pp.sha256(self.root/'plan.json'), 'metadata_sha256':pp.sha256(csv),
            'inputs_unchanged':True,'settings':{k:plan[k] for k in ('policy_version','frame_count','image_size','time_basis')},
            'samples':rows}
        pp.write_json(self.root/'reviews/manifest.json',parent)
        m=copy.deepcopy(self.manifest); m['segments']=[]; m['reservations']=[]
        m['evaluation_approval']={'status':'pending'}
        for i,row in enumerate(rows):
            old=self.manifest['segments'][i]
            m['reservations'].append({'reservation_id':row['sample_id'],'target':i,
                'source_sha256':row['source_sha256'],'source_group_id':old['management_group'],
                'approved_label_seconds':[0.,2.],'training_authorized':False,
                'metadata_path_value':row['source_path'],'official_iid':'train',
                'source_url':'synthetic-test-fixture','label_review':old['label_review'],
                'prepared_review':{'plan_path':'plan.json','plan_sha256':pp.sha256(self.root/'plan.json'),
                    'manifest_path':'reviews/manifest.json','manifest_sha256':pp.sha256(self.root/'reviews/manifest.json'),
                    'frames_sha256':pp.sha256(self.root/'reviews'/row['frames_metadata_path']),
                    'preview_sha256':pp.sha256(self.root/'reviews'/row['preview_path']),
                    'metadata':{'path':'metadata.csv','sha256':pp.sha256(csv),'csv_row':i+2}}})
        return module,m

    def test_connect_saved_pngs_preserves_pixels_times_and_pending_gate(self):
        module,m=self.bundle_review_fixture()
        pp.write_json(self.root/'pending.json',m)
        with patch.object(pp,'sample_frames',side_effect=AssertionError('no video decoding')):
            receipt=module.build(self.root,'pending.json',self.root/'connected',connect_previews=True)
        self.assertFalse(receipt['evaluation_ready'])
        self.assertEqual(receipt['class_counts'],{'normal':1,'accident':1})
        connected=pp.read_json(self.root/receipt['evaluation_manifest'])
        for s in connected['segments']:
            array=np.load(self.root/s['array_path'],allow_pickle=False)
            original=pp.read_json(self.root/'reviews'/s['sample_id']/'frames.json')
            details=pp.read_json(self.root/s['frames_path'])
            self.assertEqual(details['frames'],original['frames'])
            self.assertEqual(details['target'],s['target'])
            for j in range(16):
                with Image.open(self.root/'reviews'/s['sample_id']/'frames'/f'{j:03d}.png') as im:
                    self.assertTrue(np.array_equal(array[j],np.asarray(im)))
        self.assert_blocked(connected,'human_review_required')
        self.assertEqual(m['segments'],[])

    def test_review_connection_rejects_reordered_or_missing_pixels(self):
        module,m=self.bundle_review_fixture()
        directory=self.root/'reviews/devval_normal_01/frames'
        a,b=directory/'000.png',directory/'001.png'
        a_bytes,b_bytes=a.read_bytes(),b.read_bytes();a.write_bytes(b_bytes);b.write_bytes(a_bytes)
        with self.assertRaisesRegex(ValueError,'pixels_or_order_changed'):
            module.connect_reviewed_previews(self.root,m,self.root/'bad')
        a.unlink()
        with self.assertRaisesRegex(TrainingError,'missing_file'):
            module.connect_reviewed_previews(self.root,m,self.root/'missing')

    def test_review_connection_rejects_changed_label_and_interval(self):
        module,m=self.bundle_review_fixture()
        bad=copy.deepcopy(m);bad['reservations'][0]['target']=1
        with self.assertRaisesRegex(ValueError,'approved_label_required'):
            module.connect_reviewed_previews(self.root,bad,self.root/'bad-label')
        bad=copy.deepcopy(m);bad['reservations'][0]['approved_label_seconds']=[0.,1.]
        with self.assertRaisesRegex(ValueError,'window'):
            module.connect_reviewed_previews(self.root,bad,self.root/'bad-time')

    def test_review_connection_rejects_training_source_and_iid_test(self):
        module,m=self.bundle_review_fixture()
        bad=copy.deepcopy(m);bad['reservations'][0]['source_group_id']='train-source'
        with self.assertRaisesRegex(ValueError,'training_overlap'):
            module.connect_reviewed_previews(self.root,bad,self.root/'overlap')
        bad=copy.deepcopy(m);bad['reservations'][0]['official_iid']='test'
        with self.assertRaisesRegex(ValueError,'iid_train'):
            module.connect_reviewed_previews(self.root,bad,self.root/'test-split')

    def test_normal_bundle_path_still_refuses_pending_connected_list(self):
        module,m=self.bundle_review_fixture()
        connected=module.connect_reviewed_previews(self.root,m,self.root/'arrays')
        pp.write_json(self.root/'connected.json',connected)
        with self.assertRaisesRegex(TrainingError,'human_review_required'):
            module.build(self.root,'connected.json',self.root/'must-not-exist')
        self.assertFalse((self.root/'must-not-exist').exists())


if __name__=='__main__': unittest.main()

class PairedAssumptionTests(unittest.TestCase):
    def test_normal_margin_and_official_split(self):
        from boram3_cctv.evaluation import annotation_reference_metrics
        c={'sample_id':'n','requested_class':'normal','label_origin':'user_assumed_pre_accident_normal','source_path':'real_videos/a.mp4','csv_row':2,'start_seconds':0,'end_seconds':2}
        m={c['source_path']:[{'csv_row':2,'values':{'split_in_distribution':'train','accident_time':'6'}}]}
        r=annotation_reference_metrics([c],[],m)
        self.assertEqual(r['failed_or_missing'],1);self.assertIsNone(r['metrics']['accuracy'])
        for point,split in [('5.99','train'),('6','test')]:
            m[c['source_path']][0]['values'].update(accident_time=point,split_in_distribution=split)
            with self.assertRaises(Exception):annotation_reference_metrics([c],[],m)
    def test_pair_bundle_and_training_overlap(self):
        import tempfile,zipfile,importlib.util,hashlib,ast,json
        from pathlib import Path
        b=Path('artifacts/colab/paired-dev-631-20260924-01')
        receipt=json.loads((b/'bundle-receipt.json').read_text())
        self.assertEqual(hashlib.sha256((b/'paired-dev-code.zip').read_bytes()).hexdigest(),receipt['sha256'])
        with tempfile.TemporaryDirectory() as t:
            with zipfile.ZipFile(b/'paired-dev-code.zip') as z:z.extractall(t)
            spec=importlib.util.spec_from_file_location('paired_driver',Path(t)/'run_paired.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            p=json.loads((Path(t)/'plan.json').read_text());trained=json.loads((Path(t)/'training-input-proposal.json').read_text())['records']
            self.assertEqual(len(module.validate(p,trained)),10)
            with self.assertRaises(Exception):module.validate(p,trained+[{'source_path':p['records'][0]['source_path']}])
            p['records'][0]['end_seconds']=3
            with self.assertRaises(Exception):module.validate(p,trained)
        cell=(b/'colab-cell.py').read_text();ast.parse(cell)
        nb=json.loads(Path('notebooks/evaluate_r3d18_colab.ipynb').read_text());self.assertTrue(any(''.join(c.get('source',[]))==cell for c in nb['cells'] if c['cell_type']=='code'))

class PairedProgressRegressionTests(unittest.TestCase):
    def test_stage_updates_manifest_ten_times_preserving_failure(self):
        import tempfile,zipfile,importlib.util,json,subprocess
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with zipfile.ZipFile('artifacts/colab/paired-dev-631-20260924-01/paired-dev-code.zip') as z:z.extractall(root)
            spec=importlib.util.spec_from_file_location('paired_regression',root/'run_paired.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
            model=root/'saved';(model/'loop').mkdir(parents=True);(model/'loop/model.pt').write_bytes(b'fixture-not-a-model')
            proposal=json.loads((root/'training-input-proposal.json').read_text());(model/'prepared_inputs.json').write_text(json.dumps(proposal['records']))
            out=root/'prepared';original_sha=m.pp.sha256;seen=[]
            def fake_run(args,**kwargs):
                if (out/'manifest.json').exists():
                    previous=json.loads((out/'manifest.json').read_text());self.assertEqual(len(previous['records']),10);seen.append(previous)
                r=json.loads(args[-1])
                if r['sample_id']=='paired_dev_01_accident':return subprocess.CompletedProcess(args,1,stderr='fixture decode failure')
                path=out/r['sample_id'];path.mkdir();(path/'prepared.json').write_text(json.dumps(dict(r,status='prepared')))
                return subprocess.CompletedProcess(args,0,stderr='')
            with patch.object(m.pp,'sha256',side_effect=lambda p:m.MODEL_SHA if Path(p)==model/'loop/model.pt' else original_sha(p)),patch.object(m.subprocess,'run',side_effect=fake_run):
                m.stage(root/'videos',model,out)
            rows=json.loads((out/'manifest.json').read_text())['records']
            self.assertEqual(len(seen),9);self.assertEqual(len(rows),10)
            self.assertEqual(sum(r['status']=='prepared' for r in rows),9)
            self.assertEqual(rows[1]['status'],'error');self.assertFalse((out/'manifest.partial.json').exists())

class Paired711BundleTests(unittest.TestCase):
    def test_same_inputs_and_new_training_binding(self):
        import ast,zipfile,hashlib,json,tempfile,importlib.util
        from pathlib import Path
        b=Path('artifacts/colab/paired-dev-711-20260925-01');receipt=json.loads((b/'bundle-receipt.json').read_text())
        self.assertEqual(hashlib.sha256((b/'paired-711-code.zip').read_bytes()).hexdigest(),receipt['sha256'])
        with tempfile.TemporaryDirectory() as t:
            with zipfile.ZipFile(b/'paired-711-code.zip') as z:
                z.extractall(t);ast.parse(z.read('run_paired.py').decode())
                self.assertEqual(z.read('baseline-metrics.json'),Path('artifacts/colab/paired-dev-631-20260924-01/received/reference-metrics.json').read_bytes())
                self.assertEqual(z.read('training-input-proposal.json'),Path('artifacts/colab/train-711-20260924-01/training-input-proposal.json').read_bytes())
            spec=importlib.util.spec_from_file_location('paired711',Path(t)/'run_paired.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
            self.assertEqual(m.RESULT_SHA,hashlib.sha256(Path('artifacts/colab/train-711-20260924-01/received/result.json').read_bytes()).hexdigest())
            plan=json.loads((Path(t)/'plan.json').read_text());trained=json.loads((Path(t)/'training-input-proposal.json').read_text())['records'];self.assertEqual(len(m.validate(plan,trained)),10)
        cell=(b/'colab-cell.py').read_text();ast.parse(cell)
        self.assertIn(cell,[''.join(c['source']) for c in json.loads(Path('notebooks/evaluate_r3d18_colab.ipynb').read_text())['cells'] if c['cell_type']=='code'])

class Comparison711Tests(unittest.TestCase):
    def test_fixed_model_and_thirty_guard(self):
        import zipfile
        with zipfile.ZipFile('artifacts/colab/compare-711-20260925-01/compare-711-code.zip') as z:
            plan=json.loads(z.read('benchmark-plan.json'))
            baseline=json.loads(z.read('baseline-thirty.json'))
        info={'training_progress':{'step':3555,'completed_epochs':5}}
        sha='d81a8a12c7b0ccb2235f655a1e96abf919e8a309927e52d9ae522e3d78fd1941'
        ev.check_711_comparison(info,baseline,plan,[{}]*711,sha)
        for bad_sha,count in [('wrong',711),(sha,631)]:
            with self.assertRaises(TrainingError):ev.check_711_comparison(info,baseline,plan,[{}]*count,bad_sha)
        wrong=copy.deepcopy(baseline);wrong['rows']=wrong['rows'][:-1]
        with self.assertRaises(TrainingError):ev.check_711_comparison(info,wrong,plan,[{}]*711,sha)
        wrong=copy.deepcopy(baseline);wrong['model_sha256']='wrong'
        with self.assertRaises(TrainingError):ev.check_711_comparison(info,wrong,plan,[{}]*711,sha)

    def test_bundle_cell_scope(self):
        import zipfile,hashlib,ast
        b=Path('artifacts/colab/compare-711-20260925-01')
        receipt=json.loads((b/'bundle-receipt.json').read_text());raw=(b/'compare-711-code.zip').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),receipt['zip_sha256'])
        cell=(b/'colab-cell.py').read_text();ast.parse(cell)
        self.assertIn(receipt['zip_sha256'],cell)
        self.assertIn('reviewed711=True',cell)
        self.assertNotIn('str(VIDEO)',cell)
        with zipfile.ZipFile(b/'compare-711-code.zip') as z:
            self.assertEqual(z.read('src/boram3_cctv/evaluation.py'),Path('src/boram3_cctv/evaluation.py').read_bytes())
            self.assertEqual(len(json.loads(z.read('training-input-proposal.json'))['records']),711)
            self.assertEqual(len(json.loads(z.read('baseline-thirty.json'))['rows']),30)
