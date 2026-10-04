import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from boram3_cctv import training_data as td,preprocessing as pp

class ExpansionColabTrainingTests(unittest.TestCase):
    def fixture(self):
        config=json.loads(Path('configs/train_r3d18.expansion-colab.json').read_text())
        rows=[]
        for i in range(382):
            target=int(i<182)
            rows.append(dict(sample_id=f'fixture_{i}',target=target,
                             label_origin='source_annotation_derived' if target else 'user_assumed_normal',
                             source_group_id='accident-expansion-unresolved' if target else 'detrac-camera-unresolved',proposed_split='train'))
        proposal=dict(purpose='expanded_training_input_proposal_not_authorized',records=rows,
                      validation_segments=[],test_segments=[],independent_validation=False)
        return config,proposal

    def test_no_approval_blocks(self):
        c,p=self.fixture()
        with self.assertRaisesRegex(td.TrainingError,'explicit_approval'):
            td.validate_expansion_execution(c,p)
        td.validate_expansion_execution(c,p,require_authorization=False)

    def test_fixed_scope_and_no_validation(self):
        for updates in ({'device':'cpu'},{'max_steps':1911},{'train_scope':'all'},{'max_seconds':1801}):
            c,p=self.fixture();c.update(updates)
            with self.assertRaises(td.TrainingError):td.validate_expansion_execution(c,p,require_authorization=False)
        c,p=self.fixture();p['validation_segments']=[{}]
        with self.assertRaises(td.TrainingError):td.validate_expansion_execution(c,p,require_authorization=False)

    def test_normal_provenance_not_human_review(self):
        c,p=self.fixture();p['records'][-1]['label_origin']='human_review'
        with self.assertRaises(td.TrainingError):td.validate_expansion_execution(c,p,require_authorization=False)

    def test_old_source_limit_remains(self):
        c=json.loads(Path('configs/train_r3d18.t9-expansion.json').read_text());c['max_steps']=1910
        with self.assertRaises(td.TrainingError):td.validate_config(c)

    def test_cli_dry_run_and_denied_run_no_model(self):
        c,p=self.fixture()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);proposal=root/'proposal.json';pp.write_json(proposal,p)
            c.update(input_proposal=str(proposal),input_proposal_sha256=pp.sha256(proposal))
            config=root/'config.json';pp.write_json(config,c)
            cmd=[sys.executable,'scripts/train_r3d18.py','--colab-expansion','--config',str(config),'--output',str(root/'output')]
            dry=subprocess.run(cmd+['--check-only'],capture_output=True,text=True,timeout=10)
            self.assertEqual(dry.returncode,0,dry.stdout+dry.stderr)
            self.assertFalse(json.loads(dry.stdout)['training_worker_launched'])
            denied=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
            self.assertIn('explicit_approval',denied.stdout)
            self.assertNotEqual(denied.returncode,0)
            self.assertFalse((root/'output').exists())

    def test_epoch_loss_uses_real_unique_steps_only(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('training_cli',Path('scripts/train_r3d18.py'))
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'probe').mkdir();(p/'loop').mkdir()
            (p/'probe/train.jsonl').write_text(json.dumps({'step':1,'loss':.8})+'\n')
            (p/'loop/train.jsonl').write_text(json.dumps({'step':2,'loss':.4})+'\n'+json.dumps({'step':3,'loss':.2})+'\n')
            rows=cli.summarize_expansion_logs(p,2)
            self.assertAlmostEqual(rows[0]['online_mean_loss'],.6)
            self.assertTrue(rows[0]['epoch_complete']);self.assertFalse(rows[1]['epoch_complete'])
            self.assertIsNone(rows[0]['validation_f1'])
            (p/'loop/train.jsonl').write_text(json.dumps({'step':1,'loss':.4})+'\n')
            with self.assertRaises(td.TrainingError):cli.summarize_expansion_logs(p,2)

    def staging_cli(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('staging_cli',Path('scripts/train_r3d18.py'))
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        return cli

    def test_staging_reuses_valid_files_without_copy(self):
        from unittest.mock import patch
        cli=self.staging_cli()
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);cache=p/'cache';item=cache/'sample';item.mkdir(parents=True);out=p/'out';out.mkdir()
            a=item/'frames.npy';a.write_bytes(b'array');f=item/'frames.json';f.write_text('{}')
            row={'sample_id':'sample','array_path':'/missing-source-array','array_sha256':pp.sha256(a),
                 'frames_path':'/missing-source-json','frames_sha256':pp.sha256(f)}
            with patch.object(cli.subprocess,'run') as copier:
                result=cli.stage_expansion_inputs([row],cache,out)
            copier.assert_not_called();self.assertEqual(result['status'],'ready')
            self.assertEqual(result['reused_files'],2)

    def test_staging_partial_then_resume(self):
        cli=self.staging_cli()
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);source=p/'source';source.write_bytes(b'fixture');cache=p/'cache';out=p/'out';out.mkdir()
            rows=[{'sample_id':f's{i}','array_path':str(source),'frames_path':str(source),
                   'array_sha256':pp.sha256(source),'frames_sha256':pp.sha256(source)} for i in range(2)]
            (cache/'s0').mkdir(parents=True);(cache/'s0/frames.npy').write_bytes(b'fixture')
            # Resume a half-copied segment, preserving its already verified array.
            result=cli.stage_expansion_inputs(rows,cache,out)
            self.assertEqual(result['verified_segments'],2);self.assertEqual(result['reused_files'],1)
            self.assertEqual(result['copied_files'],3)

    def test_staging_corrupt_cache_is_preserved_and_blocked(self):
        cli=self.staging_cli()
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);cache=p/'cache';(cache/'s').mkdir(parents=True);out=p/'out';out.mkdir()
            path=cache/'s/frames.npy';path.write_bytes(b'changed')
            row={'sample_id':'s','array_path':'/missing','array_sha256':'wrong','frames_path':'/missing','frames_sha256':'wrong'}
            result=cli.stage_expansion_inputs([row],cache,out)
            self.assertEqual(result['reason'],'cached_input_hash_changed')
            self.assertFalse(result['training_worker_launched']);self.assertEqual(path.read_bytes(),b'changed')

    def test_staging_copy_timeout_retains_progress_no_partial_final(self):
        from unittest.mock import patch
        cli=self.staging_cli()
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);out=p/'out';out.mkdir();cache=p/'cache'
            row={'sample_id':'s','array_path':'/missing','array_sha256':'x','frames_path':'/missing','frames_sha256':'x'}
            with patch.object(cli.subprocess,'run',side_effect=subprocess.TimeoutExpired('copy',1)):
                result=cli.stage_expansion_inputs([row],cache,out)
            self.assertEqual(result['status'],'input_staging_incomplete')
            self.assertTrue((out/'staging-progress.json').is_file())
            self.assertFalse((cache/'s/frames.npy').exists())

class EvaluationProvenanceTests(unittest.TestCase):
    def test_expansion_binding_rejects_changed_labels_order_and_proposal(self):
        from boram3_cctv import evaluation as ev
        import copy
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            rows=[{'sample_id':str(i),'target':i,'array_path':'drive/a','frames_path':'drive/f'} for i in (0,1)]
            pp.write_json(root/'proposal.json',{'records':rows})
            sha=pp.sha256(root/'proposal.json')
            state={'format_version':1,'model_name':'r3d_18','class_mapping':td.CLASS_MAPPING,
                   'config':{'purpose':td.EXPANDED_REAL,'preprocessing':ev.SETTINGS,'input_proposal_sha256':sha},
                   'provenance':{'input_proposal_sha256':sha,'record_order':['1','0']}}
            trained=[dict(r,array_path='/tmp/a',frames_path='/tmp/f') for r in reversed(rows)]
            pp.write_json(root/'trained.json',trained)
            manifest={'training_inputs':{'path':'trained.json','sha256':pp.sha256(root/'trained.json')},
                      'training_proposal':{'path':'proposal.json','sha256':sha}}
            ev.validate_saved_configuration(root,manifest,state)
            for mode in ('label','order','proposal'):
                with self.subTest(mode=mode):
                    changed=copy.deepcopy(trained); s=copy.deepcopy(state)
                    if mode=='label':changed[0]['target']=0
                    elif mode=='order':changed.reverse()
                    else:s['provenance']['input_proposal_sha256']='0'*64
                    (root/'trained.json').write_text(json.dumps(changed))
                    manifest['training_inputs']['sha256']=pp.sha256(root/'trained.json')
                    with self.assertRaises(td.TrainingError):ev.validate_saved_configuration(root,manifest,s)

class ReservedPredictionGateTests(unittest.TestCase):
    def fixture(self,root):
        from boram3_cctv import evaluation as ev
        details={'target':None,'array_shape':[16,224,224,3],
                 'transform':{'method':'letterbox','crop':None},
                 'frames':[{'actual_seconds':1+(i+.5)/8,'requested_seconds':1+(i+.5)/8} for i in range(16)]}
        (root/'s').mkdir();(root/'s/frames.npy').write_bytes(b'gate fixture only')
        pp.write_json(root/'s/frames.json',details)
        r=dict(sample_id='s',source_path='real_videos/held_00.mp4',target=None,label_origin='candidate',
               requested_class='accident',official_iid_split='train',start_seconds=1.,end_seconds=3.,
               training_authorized=False,status='prepared_not_split',input_path='s/frames.npy',
               frames_sha256=pp.sha256(root/'s/frames.npy'),input=details)
        return {'purpose':'reserved_validation_input_preparation','records':[r]}, {'records':[copy.deepcopy(r)]}

    def test_unknown_label_accepted_without_inventing_target(self):
        from boram3_cctv import evaluation as ev
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p,r=self.fixture(root)
            rows=ev.reserved_prediction_records(root,p,r,[])
            self.assertIsNone(rows[0]['target'])

    def test_label_and_overlap_and_array_mutation_blocked(self):
        from boram3_cctv import evaluation as ev
        for kind in ['label','overlap','array']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as t:
                root=Path(t);p,r=self.fixture(root);trained=[]
                if kind=='label':p['records'][0]['target']=1
                elif kind=='overlap':trained=[{'source_path':'real_videos/held_00.mp4'}]
                else:(root/'s/frames.npy').write_bytes(b'changed')
                with self.assertRaises(td.TrainingError):ev.reserved_prediction_records(root,p,r,trained)

class AnnotationReferenceMetricTests(unittest.TestCase):
    def fixture(self):
        a=dict(sample_id='a',requested_class='accident',source_path='real_videos/a.mp4',csv_row=2,start_seconds=1.,end_seconds=3.,target=None)
        n=dict(sample_id='n',requested_class='normal',sequence_id='MVI_1',label_origin='user_assumed_normal',start_seconds=0.,end_seconds=2.)
        md={a['source_path']:[dict(csv_row=2,values={'split_in_distribution':'train','accident_time':'2'})]}
        return [a,n],md
    def test_correct_and_wrong_and_missing_are_distinguished(self):
        from boram3_cctv.evaluation import annotation_reference_metrics as metric
        rows,md=self.fixture()
        good=[dict(sample_id='a',status='predicted',prediction=1,accident_score=.9),dict(sample_id='n',status='predicted',prediction=0,accident_score=.1)]
        r=metric(rows,good,md)
        self.assertEqual(r['metrics']['accident_f1'],1.);self.assertEqual(r['metrics']['average_precision'],1.)
        self.assertFalse(r['independent_validation']);self.assertIsNone(rows[0]['target'])
        good[1].update(prediction=1,accident_score=.8)
        self.assertEqual(metric(rows,good,md)['confusion_matrix']['fp'],1)
        r=metric(rows,good[:1],md);self.assertEqual(r['failed_or_missing'],1)
        self.assertIsNone(r['rows'][1]['correct'])
    def test_official_test_and_duplicate_predictions_rejected(self):
        from boram3_cctv.evaluation import annotation_reference_metrics as metric
        rows,md=self.fixture();p=dict(sample_id='a',status='predicted',prediction=1,accident_score=.9)
        with self.assertRaises(td.TrainingError):metric(rows,[p,p],md)
        md[rows[0]['source_path']][0]['values']['split_in_distribution']='test'
        with self.assertRaises(td.TrainingError):metric(rows,[p],md)

class EpochContinuationTests(unittest.TestCase):
    def fixture(self):
        import importlib.util, random
        spec=importlib.util.spec_from_file_location('epoch_cli',Path('scripts/train_r3d18.py'))
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        path=Path('artifacts/colab/expansion-training-20260923-01/training-input-proposal.json')
        proposal=json.loads(path.read_text());records=copy.deepcopy(proposal['records'])
        c=json.loads(Path('configs/train_r3d18.expansion-colab.json').read_text())
        c.update(training_execution_authorized=True,input_proposal_sha256=pp.sha256(path))
        random.Random(c['seed']).shuffle(records)
        state={'config':c,'class_mapping':td.CLASS_MAPPING,
               'progress':{'step':1412,'cursor':266,'completed_epochs':3,'training_seconds':1786.189},
               'provenance':{'input_proposal_sha256':pp.sha256(path),'record_order':[r['sample_id'] for r in records]}}
        return cli,state,records,proposal,pp.sha256(path)

    def test_budget_and_parent_preserved(self):
        cli,s,r,p,h=self.fixture();before=copy.deepcopy(s)
        c=cli.continuation_config(s,r,p,h)
        self.assertEqual(c['max_steps']-s['progress']['step'],498)
        self.assertAlmostEqual(c['max_seconds']-s['progress']['training_seconds'],900)
        self.assertEqual(s,before)
        for k in ('learning_rate','preprocessing','train_scope'):self.assertEqual(c[k],s['config'][k])

    def test_tampered_labels_order_or_parent_rejected(self):
        for change in ('label','order','cursor','hash','approval'):
            cli,s,r,p,h=self.fixture()
            if change=='label':r[0]['target']=1-r[0]['target']
            if change=='order':r[0],r[1]=r[1],r[0]
            if change=='cursor':s['progress']['cursor']=0
            if change=='hash':h='changed'
            if change=='approval':s['config']['training_execution_authorized']=False
            with self.assertRaises(td.TrainingError):cli.continuation_config(s,r,p,h)

    def test_runtime_paths_can_change_not_input_hashes(self):
        cli,s,r,p,h=self.fixture();r[0]['array_path']='/tmp/new/frames.npy'
        cli.continuation_config(s,r,p,h)
        r[0]['array_sha256']='changed'
        with self.assertRaises(td.TrainingError):cli.continuation_config(s,r,p,h)

    def test_progress_reports_new_work_not_fake_accuracy(self):
        cli,*_=self.fixture()
        v=cli.continuation_progress({'step':1661,'loss':.05},10,110)
        self.assertEqual(v['percent'],50);self.assertEqual(v['eta_seconds_estimate'],100)
        self.assertNotIn('accuracy',v)
        self.assertEqual(cli.continuation_progress({'step':1910,'loss':.01},10,210)['percent'],100)

    def test_resume_epoch_log_does_not_invent_previous_losses(self):
        cli,*_=self.fixture()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'loop').mkdir()
            (root/'loop/train.jsonl').write_text('\n'.join(json.dumps({'step':s,'loss':.1}) for s in range(1413,1911)))
            rows=cli.summarize_expansion_logs(root,382)
            self.assertEqual([r['recorded_updates'] for r in rows],[116,382])
            self.assertEqual([r['epoch_complete'] for r in rows],[False,True])

class EpochComparisonTests(unittest.TestCase):
    def fixture(self):
        from boram3_cctv import evaluation as ev
        base=Path('artifacts/colab/reference-benchmark-20260923-01')
        return ev,json.loads((base/'benchmark-plan.json').read_text()),json.loads((base/'received/reference-metrics.json').read_text())

    def test_exact_old_success_selection(self):
        ev,plan,old=self.fixture();selected=ev.fixed_comparison_candidates(plan,old)
        self.assertEqual(len(selected),23)
        self.assertNotIn('reserved_accident_11',{r['sample_id'] for r in selected})
        self.assertIn('reserved_accident_20',{r['sample_id'] for r in selected})
        before=copy.deepcopy(old);ev.fixed_comparison_candidates(plan,old);self.assertEqual(old,before)

    def test_changed_parent_or_duplicate_rejected(self):
        for variant in ('parent','duplicate','count'):
            ev,p,o=self.fixture()
            if variant=='parent':o['model_sha256']='wrong'
            if variant=='duplicate':o['rows'][0]=o['rows'][1]
            if variant=='count':o['scored_segments']=30
            with self.assertRaises(td.TrainingError):ev.fixed_comparison_candidates(p,o)

    def test_staging_saved_arrays_only(self):
        ev,p,o=self.fixture()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);src=root/'source';src.mkdir();code=root/'code';code.mkdir();model=root/'model';(model/'loop').mkdir(parents=True)
            for n in ('benchmark-plan.json','previous-metrics.json','training-input-proposal.json','metadata-real.csv'):
                (code/n).write_text(json.dumps(p if n=='benchmark-plan.json' else o if n=='previous-metrics.json' else {}))
            for n in ('loop/model.pt','prepared_inputs.json','result.json'):(model/n).write_bytes(b'fixture')
            rows=[]
            for c in ev.fixed_comparison_candidates(p,o):
                sid=c['sample_id'];folder=src/sid;folder.mkdir()
                for n in ('frames.npy','frames.json','preview.png'):(folder/n).write_bytes(b'preserved')
                rows.append(dict(c,status='prepared_not_split',input_path=sid+'/frames.npy',preview_path=sid+'/preview.png'))
            (src/'manifest.json').write_text(json.dumps({'records':rows}))
            ev.stage_epoch_comparison(code,root/'runtime',model,[src])
            self.assertEqual(len(list((root/'runtime').glob('*/frames.npy'))),23)
            self.assertEqual((root/'runtime'/rows[0]['input_path']).read_bytes(),b'preserved')
            with self.assertRaises(FileExistsError):ev.stage_epoch_comparison(code,root/'runtime',model,[src])

class LowFpsEvaluationGateTests(unittest.TestCase):
    def test_repeat_optin_keeps_strict_default_and_rejects_reverse(self):
        from boram3_cctv import evaluation as ev
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p,r=ReservedPredictionGateTests().fixture(root)
            row=p['records'][0];row['sampling_policy']='nearest-bin-repeat-v1'
            f=row['input']['frames'];f[1]['actual_seconds']=f[0]['actual_seconds']
            (root/'s/frames.json').write_text(json.dumps(row['input']))
            with self.assertRaises(td.TrainingError):ev.reserved_prediction_records(root,p,r,[])
            result=ev.reserved_prediction_records(root,p,r,[],repeat_policy=True)
            self.assertIsNone(result[0]['target'])
            f[1]['actual_seconds']=f[0]['actual_seconds']-.01
            (root/'s/frames.json').write_text(json.dumps(row['input']))
            with self.assertRaises(td.TrainingError):ev.reserved_prediction_records(root,p,r,[],repeat_policy=True)

    def test_seven_receipt_binding_and_no_label_invention(self):
        from boram3_cctv import evaluation as ev
        from unittest.mock import patch
        low=json.loads(Path('artifacts/colab/low-fps-review-20260923-01/received/manifest.user.json').read_text())
        plan=json.loads(Path('artifacts/colab/reference-benchmark-20260923-01/benchmark-plan.json').read_text())
        previous=json.loads(Path('artifacts/colab/epoch5-comparison-20260923-01/received/reference-metrics.json').read_text())
        plan['model_sha256']=previous['model_sha256']
        with patch.object(ev,'reserved_prediction_records',return_value=[]) as gate:
            ev.low_fps_seven_records(Path('.'),low,plan,previous,[])
            self.assertTrue(gate.call_args.kwargs['repeat_policy'])
            self.assertTrue(all(r['target'] is None for r in gate.call_args.args[1]['records']))
        low['records'][0]['media']['unique_frame_count']=16
        with self.assertRaises(td.TrainingError):ev.low_fps_seven_records(Path('.'),low,plan,previous,[])

class MergeExpansionTests(unittest.TestCase):
    def proposal(self,source):
        return dict(purpose='expanded_training_input_proposal_not_authorized',validation_segments=[],test_segments=[],
                    records=[dict(sample_id='expansion_0000',source_path=source,sequence_id=None,
                                  start_seconds=1,end_seconds=3,target=1,array_path='/original/frames.npy')])
    def test_namespace_preserves_paths_and_sources(self):
        a=self.proposal('a');b=self.proposal('b')
        p=td.merge_expansion_proposals(a,b,namespace='new242')
        self.assertEqual(p['records'][1]['sample_id'],'new242_expansion_0000')
        self.assertEqual(p['records'][1]['array_path'],'/original/frames.npy')
        self.assertEqual(b['records'][0]['sample_id'],'expansion_0000')
        self.assertFalse(p['training_execution_authorized'])
    def test_source_overlap_rejected(self):
        with self.assertRaises(td.TrainingError):td.merge_expansion_proposals(self.proposal('a'),self.proposal('a'),namespace='new242')
    def test_evaluation_inclusion_rejected(self):
        a=self.proposal('a');a['validation_segments']=['reserved']
        with self.assertRaises(td.TrainingError):td.merge_expansion_proposals(a,self.proposal('b'),namespace='new242')

class Fixed603Tests(unittest.TestCase):
    def fixture(self):
        import hashlib
        root=Path('artifacts/colab/expansion-242-20260923-01/merged-603')
        raw=(root/'training-input-proposal.json').read_bytes()
        p=json.loads(raw);c=json.loads((root/'training-config.proposed.json').read_text())
        c.update(execution_scope='fixed_603_v1',input_proposal_sha256=hashlib.sha256(raw).hexdigest())
        return c,p
    def test_explicit_scope_without_execution(self):
        c,p=self.fixture();td.validate_expansion_execution(c,p,require_authorization=False)
        with self.assertRaises(td.TrainingError):td.validate_expansion_execution(c,p)
    def test_old_scope_rejects_603(self):
        c,p=self.fixture();c.pop('execution_scope')
        with self.assertRaises(td.TrainingError):td.validate_expansion_execution(c,p,require_authorization=False)
    def test_mutated_scope_rejected(self):
        c,p=self.fixture();c['input_proposal_sha256']='0'*64
        with self.assertRaises(td.TrainingError):td.validate_expansion_execution(c,p,require_authorization=False)
        c,p=self.fixture();c['max_seconds']=3600
        with self.assertRaises(td.TrainingError):td.validate_expansion_execution(c,p,require_authorization=False)

class Continuation603Tests(unittest.TestCase):
    def test_resume_scope_and_progress(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('resume603_cli',Path('scripts/train_r3d18.py'))
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        c,p=Fixed603Tests().fixture();c['training_execution_authorized']=True
        records=p['records'];state=dict(config=c,progress=dict(step=1678,cursor=472,completed_epochs=2,training_seconds=1788.97),class_mapping=td.CLASS_MAPPING,
            provenance=dict(input_proposal_sha256=c['input_proposal_sha256'],record_order=[r['sample_id'] for r in records]))
        got=cli.continuation_config(state,records,p,c['input_proposal_sha256'])
        self.assertEqual(got['max_steps'],3015);self.assertAlmostEqual(got['max_seconds'],2688.97)
        row=cli.continuation_progress({'step':3015,'loss':.1},0,10,603)
        self.assertEqual(row['additional_updates'],1337);self.assertEqual(row['percent'],100)
        state['progress']['step']=0
        with self.assertRaises(td.TrainingError):cli.continuation_config(state,records,p,c['input_proposal_sha256'])

class Compare603Tests(unittest.TestCase):
    def test_fixed_thirty_and_epoch_gate(self):
        from boram3_cctv.evaluation import check_603_comparison
        info={'training_progress':{'step':3015,'completed_epochs':5}}
        plan={'records':[{'sample_id':str(i)} for i in range(30)]}
        base={'independent_validation':False,'rows':[{'sample_id':str(i),'status':'predicted'} for i in range(30)]}
        check_603_comparison(info,base,plan)
        info['training_progress']['step']=1678
        with self.assertRaises(td.TrainingError):check_603_comparison(info,base,plan)
        info['training_progress']['step']=3015;base['rows'].pop()
        with self.assertRaises(td.TrainingError):check_603_comparison(info,base,plan)

class Compare603BundleRegressionTests(unittest.TestCase):
    def test_packaged_selection_binding_and_seven_partition(self):
        import zipfile
        from boram3_cctv.evaluation import fixed_comparison_candidates
        with zipfile.ZipFile('artifacts/colab/compare-603-20260924-02/compare-603-code.zip') as z:
            plan=json.loads(z.read('benchmark-plan.json'));old=json.loads(z.read('previous-metrics.json'));baseline=json.loads(z.read('baseline-thirty.json'))
        selected=fixed_comparison_candidates(plan,old)
        self.assertEqual(len(selected),23)
        self.assertEqual(plan['model_sha256'],baseline['model_sha256'])
        original=json.loads(Path('artifacts/colab/compare-603-20260924-01/benchmark-plan.json').read_text())
        self.assertEqual(plan['records'],original['records'])
        self.assertEqual(plan['historical_selection_model_sha256'],original['model_sha256'])
        wrong=copy.deepcopy(plan);wrong['model_sha256']='wrong'
        with self.assertRaises(td.TrainingError):fixed_comparison_candidates(wrong,old)
