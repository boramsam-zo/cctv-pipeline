"""Generated review/CSV fixtures; no real video, training, weights or network."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
from boram3_cctv import preprocessing as pp, training_data as td
from scripts import train_r3d18 as cli, record_segment_reviews as reviews


class ReviewedReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = json.loads((ROOT/'configs/train_r3d18.local.json').read_text())
        self.config.update(data_root='data', metadata_path='data/metadata-real.csv')
        self.metadata = {}
        self.batches = []
        for kind, target in [('accident', 1), ('qmul', 0)]:
            records = []
            for i in range(3):
                sid = f'{kind}_{i}'
                source_hash = f'{i+1:064x}' if target else '4'*64
                start, end = (float(i*2), float(i*2+2)) if target else (float(i*10), float(i*10+6))
                label = {'status':'approved', 'scope':'segment', 'label':pp.CLASS_ORDER[target],
                         'basis':'generated fixture only', 'reviewer':'fixture', 'reviewed_at':'2026-09-21',
                         'policy_version':'test-policy', 'source_sha256':source_hash,
                         'start_seconds':start, 'end_seconds':end}
                sample = {'sample_id':sid, 'source_sha256':source_hash, 'start_seconds':start,
                          'end_seconds':end, 'label_review':label, 'event_id':None,
                          'source_group_id':None if target else 'one-local-file'}
                if target:
                    sample.update(video_path=f'real_videos/{i}.mp4', official_splits={
                        'split_in_distribution':'test' if i==0 else 'train', 'split_geo_aware':'test'})
                    self.metadata[sample['video_path']] = [{'csv_row':i+2, 'values':{
                        'path':sample['video_path'], 'type':'single', 'accident_time':'1.0',
                        'weather':'normal', **sample['official_splits']}}]
                else:
                    sample['official_split'] = None
                records.append({'sample_id':sid, 'original_segment_record':sample,
                                'approved_target':target, 'quality_status':'confirmed',
                                'quality_submission':{'reviewer':'fixture','reviewed_at':'2026-09-21'}})
            self.batches.append({'kind':kind, 'policy_version':'test-policy', 'quality':{'samples':records}})

    def summarize(self):
        return td.reviewed_input_readiness(self.config, self.batches, self.metadata)

    def test_labels_quality_counts_do_not_become_training_assignments(self):
        before = deepcopy(self.batches)
        report = self.summarize()
        self.assertEqual(report['approved_label_counts'], {'accident':3,'normal':3})
        self.assertEqual(report['quality_counts'], {'confirmed':6})
        self.assertEqual(report['assigned_train_segments'],0)
        self.assertEqual(report['assigned_validation_segments'],0)
        self.assertEqual(report['status'],'held')
        self.assertFalse(report['training_gate_replaced']);self.assertFalse(report['training_run'])
        self.assertEqual(before,self.batches)

    def test_protocols_are_alternatives_and_test_is_never_assigned(self):
        r=self.summarize();c=r['protocol_comparison']
        self.assertEqual(c['split_in_distribution']['official_train_only_not_authorized'],['accident_1','accident_2'])
        self.assertEqual(c['split_geo_aware']['official_train_only_not_authorized'],[])
        self.assertIsNone(r['chosen_evaluation_protocol'])
        self.assertTrue(all(s['assigned_split'] is None for s in r['samples']))

    def test_three_normal_segments_are_one_source_not_three_events(self):
        r=self.summarize()
        self.assertEqual(r['distinct_recorded_file_hashes'],4)
        self.assertEqual(r['normal_distinct_recorded_file_hashes'],1)
        self.assertIsNone(r['independent_event_count'])
        c=next(x for x in r['keep_together_constraints'] if x['evidence_key']=='source_sha256')
        self.assertEqual(c['sample_ids'],['qmul_0','qmul_1','qmul_2'])
        self.assertIn('independent_normal_sources_insufficient_for_train_validation',r['issues'])

    def test_unknown_ids_stay_unknown_and_external_normal_needs_no_fake_event(self):
        r=self.summarize()
        self.assertTrue(all(s['event_id'] is None for s in r['samples']))
        self.assertTrue(all(s['upstream_original_id'] is None for s in r['samples']))
        self.assertTrue(all(s['recorded_official_splits'] is None and s['csv_row'] is None for s in r['samples'][3:]))
        self.assertTrue(all(s['target']==0 for s in r['samples'][3:]))

    def test_quality_problem_does_not_relabel_accident(self):
        self.batches[0]['quality']['samples'][0]['quality_status']='reported_issue'
        r=self.summarize();self.assertEqual(r['samples'][0]['target'],1)
        self.assertIn('accident_0:input_quality_not_confirmed',r['issues'])

    def test_missing_label_cannot_be_manufactured_from_annotations(self):
        self.batches[0]['quality']['samples'][0]['original_segment_record']['label_review'].update(status='unreviewed',label=None)
        with self.assertRaisesRegex(td.TrainingError,'approved_review_target_mismatch'):self.summarize()

    def test_changed_csv_retains_both_versions_and_reports_conflict(self):
        self.metadata['real_videos/0.mp4'][0]['values']['split_in_distribution']='train'
        r=self.summarize();row=r['samples'][0]
        self.assertEqual(row['recorded_official_splits']['split_in_distribution'],'test')
        self.assertEqual(row['csv_official_splits']['split_in_distribution'],'train')
        self.assertIn('official_split_record_csv_mismatch',row['issues'])
        self.assertIsNone(row['assigned_split'])

    def test_missing_duplicate_unknown_csv_is_not_normal_or_train(self):
        for state in ('missing','duplicate','unknown'):
            original=deepcopy(self.metadata)
            if state=='missing':del self.metadata['real_videos/0.mp4']
            elif state=='duplicate':self.metadata['real_videos/0.mp4']*=2
            else:self.metadata['real_videos/0.mp4'][0]['values']['split_geo_aware']='unknown'
            with self.subTest(state=state):
                r=self.summarize();self.assertTrue(r['samples'][0]['issues'])
                self.assertEqual(r['samples'][0]['target'],1);self.assertIsNone(r['samples'][0]['assigned_split'])
            self.metadata=original

    def test_different_durations_reported_not_automatically_changed(self):
        r=self.summarize();self.assertEqual(r['segment_durations_seconds'],[2.0,6.0])
        self.assertIn('different_segment_durations_need_temporal_policy',r['issues'])

    def test_duplicate_ids_windows_and_oversized_batches_rejected(self):
        original=deepcopy(self.batches)
        for state in ('id','window','size','kind'):
            self.batches=deepcopy(original);items=self.batches[0]['quality']['samples']
            if state=='id':items[1]['original_segment_record']['sample_id']=items[0]['sample_id']
            elif state=='window':items[1]['original_segment_record']=deepcopy(items[0]['original_segment_record']);items[1]['original_segment_record']['sample_id']='different_id'
            elif state=='size':items.append(deepcopy(items[0]))
            else:self.batches.append(deepcopy(self.batches[0]))
            with self.subTest(state=state), self.assertRaises(td.TrainingError):self.summarize()

    def setup_files(self):
        (self.root/'data/real_videos').mkdir(parents=True)
        fields=list(next(iter(self.metadata.values()))[0]['values'])
        with (self.root/'data/metadata-real.csv').open('w') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
            writer.writerows(v[0]['values'] for v in self.metadata.values())
        self.config_path=self.root/'config.json';pp.write_json(self.config_path,self.config)
        batch=self.batches[0];quality=deepcopy(batch['quality'])
        quality.update(status='ready_to_record', purpose='input_quality_only', counts={'confirmed':3},
                       parent_plan_sha256='p',parent_report_sha256='r',answers_sha256='a',labels_changed=False,training_run=False)
        self.fresh=quality
        stored={**quality,'status':'recorded'}
        for name,value in [('plan',{'policy_version':'test-policy'}),('link',{}),('answers',{}),('quality',stored)]:
            pp.write_json(self.root/(name+'.json'),value)
        self.request={'schema_version':1,'purpose':'reviewed_input_readiness_only','batches':[{
            'kind':'accident','plan':'plan.json','link_report':'link.json','answers':'answers.json','quality_report':'quality.json'}]}
        self.request_path=self.root/'request.json';pp.write_json(self.request_path,self.request)

    def test_file_audit_reuses_quality_binding_and_does_not_decode(self):
        self.setup_files()
        with patch.object(reviews,'run_quality',return_value=self.fresh) as binding, \
             patch.object(td,'preflight',side_effect=AssertionError('not training preflight')), \
             patch.object(pp,'sample_frames',side_effect=AssertionError('no raw decode')):
            r=cli.inspect_reviewed_inputs(self.root,self.config_path,self.request_path)
        self.assertTrue(binding.call_args.kwargs['check_only'])
        self.assertEqual(r['quality_counts'],{'confirmed':3});self.assertTrue(r['inputs_unchanged'])

    def test_stale_saved_quality_report_is_rejected(self):
        self.setup_files();path=self.root/'quality.json';stored=json.loads(path.read_text())
        stored['samples'][0]['approved_target']=0;path.write_text(json.dumps(stored))
        with patch.object(reviews,'run_quality',return_value=self.fresh), self.assertRaisesRegex(td.TrainingError,'saved_quality_record_mismatch'):
            cli.inspect_reviewed_inputs(self.root,self.config_path,self.request_path)

    def test_missing_changed_or_oversized_records_fail(self):
        self.setup_files()
        with patch.object(reviews,'run_quality',side_effect=pp.PreprocessingError('checked_input_changed')), self.assertRaisesRegex(pp.PreprocessingError,'checked_input_changed'):
            cli.inspect_reviewed_inputs(self.root,self.config_path,self.request_path)
        (self.root/'answers.json').unlink()
        with self.assertRaises((OSError,pp.PreprocessingError)):cli.inspect_reviewed_inputs(self.root,self.config_path,self.request_path)
        (self.root/'answers.json').write_bytes(b' '* (reviews.MAX_JSON_BYTES+1))
        with self.assertRaisesRegex(pp.PreprocessingError,'json_size_limit'):cli.inspect_reviewed_inputs(self.root,self.config_path,self.request_path)

    def test_paths_must_stay_inside_project(self):
        for value in ('../outside.json','/private/tmp/outside.json',''):
            with self.subTest(value=value),self.assertRaises(td.TrainingError):cli.review_input_path(self.root,value)

    def test_output_protection_and_timeout_cannot_launch_training(self):
        out=self.root/'artifacts/out'
        with patch.object(cli,'ROOT',self.root), patch.object(cli.subprocess,'run',side_effect=subprocess.TimeoutExpired('fixture',55)), \
             patch.object(cli,'run',side_effect=AssertionError('training not permitted')):
            r=cli.run_review_readiness(self.root/'config.json',self.root/'request.json',out)
            self.assertEqual(r['reason'],'review_check_timeout');self.assertFalse(r['training_run'])
            with self.assertRaisesRegex(td.TrainingError,'output_exists'):cli.run_review_readiness(self.root/'config.json',self.root/'request.json',out)
            with self.assertRaisesRegex(td.TrainingError,'output_must_be'):cli.run_review_readiness(self.root/'config.json',self.root/'request.json',self.root/'data/out')

    def test_cli_routes_modes_separately_and_rejects_resume(self):
        base=['train_r3d18.py','--output','artifacts/fixture']
        with patch.object(cli,'run',return_value={'status':'held'}) as old, patch.object(cli,'run_review_readiness',return_value={'status':'held'}) as new:
            with patch.object(sys,'argv',base+['--check-only']):self.assertEqual(cli.main(),2)
            old.assert_called_once();new.assert_not_called()
            with patch.object(sys,'argv',base+['--review-readiness','request.json']):self.assertEqual(cli.main(),2)
            new.assert_called_once()
            for forbidden in (['--resume','model.pt'],['--check-only']):
                with patch.object(sys,'argv',base+['--review-readiness','request.json']+forbidden):self.assertEqual(cli.main(),2)
            self.assertEqual(new.call_count,1)

    def iid_selection(self):
        return {'column':'split_in_distribution','source':'user_message',
                'task':'binary_segment_classification','raw_response':'explicit fixture choice',
                'training_execution_authorized':False,
                'smoke_sample_ids':['accident_1','accident_2','qmul_0']}

    def selected(self, selection=None):
        return td.reviewed_input_readiness(self.config,self.batches,self.metadata,
                                          protocol_selection=selection or self.iid_selection())

    def test_iid_choice_keeps_five_candidates_and_protects_test(self):
        r=self.selected()
        self.assertEqual(r['chosen_evaluation_protocol'],'split_in_distribution')
        self.assertNotIn('official_protocol_not_selected',r['issues'])
        self.assertEqual(r['A_short_training_check']['candidate_sample_ids'],['accident_1','accident_2','qmul_0','qmul_1','qmul_2'])
        self.assertEqual(r['samples'][0]['proposed_use'],'official_test_protected')
        self.assertTrue(all(s['assigned_split'] is None and not s['training_authorized'] for s in r['samples']))

    def test_B_shortage_does_not_erase_A_inputs_or_approve_execution(self):
        r=self.selected();a=r['A_short_training_check'];b=r['B_independent_evaluation']
        self.assertEqual(a['data_status'],'reviewed_candidates_available')
        self.assertFalse(a['blocked_by_B_data_shortage'])
        self.assertEqual(b['status'],'not_ready')
        self.assertTrue(a['existing_training_gate_unchanged'])
        self.assertIn('explicit_training_execution_approval',a['remaining_conditions'])
        self.assertEqual(a['proposed_limits']['max_steps'],3)
        self.assertFalse(r['training_run'])

    def test_unknown_accident_relation_kept_together_not_declared_independent(self):
        r=self.selected();group=r['unresolved_accident_keep_together']
        self.assertEqual(group['sample_ids'],['accident_1','accident_2'])
        self.assertIsNone(group['confirmed_same_event']);self.assertIsNone(r['independent_event_count'])

    def test_test_unknown_duplicate_or_too_many_smoke_ids_rejected(self):
        for ids in (['accident_0','qmul_0'],['missing'],['qmul_0']*2, ['accident_1','accident_2','qmul_0','qmul_1']):
            selection=self.iid_selection();selection['smoke_sample_ids']=ids
            with self.subTest(ids=ids),self.assertRaises(td.TrainingError):self.selected(selection)

    def test_geo_test_cannot_be_used_by_reusing_iid_choice(self):
        selection=self.iid_selection();selection['column']='split_geo_aware'
        with self.assertRaisesRegex(td.TrainingError,'smoke_candidate_not_eligible'):self.selected(selection)

    def test_protocol_selection_is_not_training_approval(self):
        for key,value in [('training_execution_authorized',True),('source','model_prediction'),('raw_response',''),('column','guessed_column')]:
            selection=self.iid_selection();selection[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(td.TrainingError,'invalid_protocol_selection'):self.selected(selection)

    def test_unconfirmed_quality_or_changed_official_row_cannot_enter_A(self):
        original=deepcopy(self.batches)
        self.batches[0]['quality']['samples'][1]['quality_status']='awaiting_review'
        with self.assertRaisesRegex(td.TrainingError,'smoke_candidate_not_eligible'):self.selected()
        self.batches=original;self.metadata['real_videos/1.mp4'][0]['values']['split_in_distribution']='test'
        with self.assertRaisesRegex(td.TrainingError,'smoke_candidate_not_eligible'):self.selected()

    def historical(self):
        rows=[]
        for i in range(3):
            path=f'real_videos/old_{i}.mp4';splits={'split_in_distribution':'train','split_geo_aware':'train'}
            self.metadata[path]=[{'csv_row':i+10,'values':{'path':path,**splits}}]
            rows.append({'path':path,'csv_row':i+10,'official_splits':splits})
        return {'purpose':'acquisition_candidates_only','metadata_sha256':'a'*64,'candidates':rows}

    def test_old_and_new_file_keys_same_metadata_explains_split_difference(self):
        h=self.historical();r=self.summarize()
        c=td.compare_candidate_history(h,self.metadata,'a'*64,r['samples'],'a'*64)
        self.assertEqual(c['conclusion'],'different_file_keys_same_metadata_bytes')
        self.assertEqual(c['overlapping_file_keys'],[]);self.assertEqual(len(c['rows']),6)
        self.assertTrue(all(row['sample_id'] is None for row in c['rows'][:3]))
        self.assertFalse(c['upstream_or_event_identity_deduced'])

    def test_changed_metadata_or_same_file_key_is_not_assumed_new_video(self):
        h=self.historical();r=self.summarize()
        for old_hash,received_hash in [('b'*64,'a'*64),('a'*64,'b'*64)]:
            h['metadata_sha256']=old_hash
            c=td.compare_candidate_history(h,self.metadata,'a'*64,r['samples'],received_hash)
            self.assertEqual(c['conclusion'],'requires_row_and_version_review')
        h['metadata_sha256']='a'*64;row=r['samples'][0]
        h['candidates'][0]={'path':row['video_path'],'csv_row':row['csv_row'],'official_splits':row['csv_official_splits']}
        c=td.compare_candidate_history(h,self.metadata,'a'*64,r['samples'],'a'*64)
        self.assertEqual(c['overlapping_file_keys'],['real_videos/0.mp4'])
        self.assertEqual(c['conclusion'],'requires_row_and_version_review')

    def test_historical_row_mismatch_is_retained_not_corrected(self):
        h=self.historical();h['candidates'][0]['csv_row']=999
        c=td.compare_candidate_history(h,self.metadata,'a'*64,self.summarize()['samples'],'a'*64)
        self.assertFalse(c['recorded_rows_match_current'])
        self.assertEqual(c['rows'][0]['recorded_csv_row'],999)
        self.assertEqual(c['rows'][0]['current_csv_row'],10)

    def test_iid_mode_reuses_prepared_files_and_checks_actual_source_hashes(self):
        self.setup_files();(self.root/'prepared').mkdir()
        items=[]
        for item in self.fresh['samples']:
            s=item['original_segment_record'];sid=s['sample_id']
            source=self.root/'data'/s['video_path'];source.write_bytes(sid.encode())
            s['source_sha256']=pp.sha256(source);s['label_review']['source_sha256']=s['source_sha256']
            (self.root/'prepared'/f'{sid}.npy').write_bytes(b'checked array fixture')
            (self.root/'prepared'/f'{sid}.json').write_text('{}')
            items.append({'sample_id':sid,'input_path':f'{sid}.npy','frames_metadata_path':f'{sid}.json'})
        (self.root/'plan.json').write_text(json.dumps({'policy_version':'test-policy','samples':[s['original_segment_record'] for s in self.fresh['samples']]}))
        (self.root/'quality.json').write_text(json.dumps({**self.fresh,'status':'recorded'}))
        pp.write_json(self.root/'prepared/manifest.json',{'samples':items})
        selection=self.iid_selection();selection['smoke_sample_ids']=['accident_1']
        self.request['protocol_selection']=selection;self.request_path.write_text(json.dumps(self.request))
        with patch.object(reviews,'run_quality',return_value=self.fresh):
            r=cli.inspect_reviewed_inputs(self.root,self.config_path,self.request_path)
            self.assertEqual(r['A_short_training_check']['proposed_inputs'][0]['sample_id'],'accident_1')
            self.assertEqual(r['samples'][1]['prepared_input']['original_file_status'],'present_hash_verified')
            (self.root/'data/real_videos/1.mp4').write_bytes(b'changed')
            with self.assertRaisesRegex(td.TrainingError,'selected_original_hash_changed'):
                cli.inspect_reviewed_inputs(self.root,self.config_path,self.request_path)


class TrainingPreparationTransferTests(unittest.TestCase):
    """Tiny saved-input fixtures; no video decoding or model execution."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = json.loads((ROOT/'configs/train_inputs.colab.json').read_text())
        self.manifest['sample_ids'] = ['qmul_001', 'sample_010']
        self.normal = {'sample_id':'qmul_001', 'source_kind':'qmul', 'video_path':None,
            'source_sha256':'a'*64, 'start_seconds':30., 'end_seconds':36., 'target':0,
            'input_quality_status':'confirmed', 'management_group':'qmul_one_source',
            'split':'train', 'official_splits':None,
            'label_review':{'status':'approved', 'label':'normal', 'scope':'segment',
                'policy_version':'draft-0.1', 'source_sha256':'a'*64,
                'start_seconds':30., 'end_seconds':36., 'reviewer':'fixture',
                'reviewed_at':'2026-09-20', 'basis':'generated test'}}
        self.source = {'sample_id':'sample_010', 'video_path':'real_videos/a.mp4',
            'source_sha256':'b'*64, 'start_seconds':6., 'end_seconds':8., 'target':1,
            'management_group':'unresolved', 'split':'train',
            'official_splits':{'split_in_distribution':'train'},
            'label_review':{'status':'unreviewed', 'label':None},
            'label_provenance':{'kind':'source_annotation_derived', 'human_reviewed':False,
                'policy_version':'accident-point-window-v1', 'csv_row':2, 'annotation_seconds':7.}}
        for row in (self.normal, self.source):
            folder = self.root/row['sample_id']; folder.mkdir()
            for kind, name in [('array','frames.npy'), ('frames','frames.json'), ('preview','preview.png')]:
                p=folder/name;p.write_bytes(b'fixture '+name.encode())
                row[kind+'_path']=p.relative_to(self.root).as_posix()
                row[kind+'_sha256']=pp.sha256(p)
        (self.root/'metadata.csv').write_text('path,accident_time,split_in_distribution\nreal_videos/a.mp4,7,train\n')
        self.policy={'authorization':{'use_source_annotations':True}, 'training_execution_authorized':False}
        self.reserved={'segments':[{'source_path':'real_videos/reserved.mp4', 'source_sha256':'c'*64}]}
        self.sync()

    def sync(self):
        refs={}
        def put(key,name,value):
            p=self.root/name;p.write_text(json.dumps(value))
            refs[key]={'path':name,'sha256':pp.sha256(p)}
        refs['metadata']={'path':'metadata.csv','sha256':pp.sha256(self.root/'metadata.csv')}
        put('annotation_policy','policy.json',self.policy)
        self.source['label_provenance'].update(
            metadata_sha256=refs['metadata']['sha256'],
            policy_authorization_sha256=refs['annotation_policy']['sha256'])
        put('previous_inputs','old.json',[self.normal])
        put('human_receipt','human.json',{'samples':[self.normal]})
        put('annotation_report','annotations.json',{'status':'source_annotation_inputs_prepared',
            'training_execution_authorized':False,'samples':[self.source]})
        put('reserved_manifest','reserved.json',self.reserved)
        self.manifest['references']=refs
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))

    def result(self):
        return td.connect_training_preparation(self.root,self.manifest)

    def test_origins_temporal_rules_and_execution_hold_preserved(self):
        before=deepcopy(self.manifest);result=self.result()
        self.assertEqual(result['class_counts'],{'normal':1,'accident':1})
        self.assertEqual(result['temporal_seconds_by_class'],{'normal':[6.],'accident':[2.]})
        self.assertEqual(result['label_origin_counts'],{'human_review':1,'source_annotation_derived':1})
        self.assertEqual(result['normal_source_files'],1)
        self.assertIsNone(result['independent_event_count'])
        self.assertFalse(result['training_execution_authorized'])
        self.assertEqual(result['records'][1]['label_review']['status'],'unreviewed')
        self.assertEqual(before,self.manifest)

    def test_official_test_and_reserved_sources_rejected(self):
        (self.root/'metadata.csv').write_text('path,accident_time,split_in_distribution\nreal_videos/a.mp4,7,test\n')
        self.sync()
        with self.assertRaisesRegex(td.TrainingError,'official_IID_test'):self.result()
        (self.root/'metadata.csv').write_text('path,accident_time,split_in_distribution\nreal_videos/a.mp4,7,train\n')
        self.reserved['segments'][0]['source_sha256']='b'*64;self.sync()
        with self.assertRaisesRegex(td.TrainingError,'reserved_source'):self.result()

    def test_missing_and_corrupted_arrays_rejected(self):
        p=self.root/self.normal['array_path'];p.write_bytes(b'changed')
        with self.assertRaisesRegex(td.TrainingError,'reference_changed'):self.result()
        p.unlink()
        with self.assertRaisesRegex(td.TrainingError,'reference_missing'):self.result()

    def test_missing_human_quality_and_fabricated_source_review_rejected(self):
        self.normal['input_quality_status']='pending';self.sync()
        with self.assertRaisesRegex(td.TrainingError,'human_evidence'):self.result()
        self.normal['input_quality_status']='confirmed'
        self.source['label_provenance']['human_reviewed']=True;self.sync()
        with self.assertRaisesRegex(td.TrainingError,'source_label'):self.result()

    def test_scope_and_preprocessing_cannot_silently_change(self):
        self.manifest['training_execution_authorized']=True
        with self.assertRaisesRegex(td.TrainingError,'only_scope'):self.result()
        self.manifest['training_execution_authorized']=False
        self.manifest['preprocessing']['image_size']=112
        with self.assertRaisesRegex(td.TrainingError,'settings_changed'):self.result()

    def test_colab_json_must_match_saved_input_not_just_count(self):
        prepared=self.result();r=prepared['records'][1];p=r['label_provenance']
        row={k:deepcopy(r[k]) for k in ('sample_id','source_path','start_seconds','end_seconds','target','label_provenance')}
        row.update(csv_row=2,metadata_sha256=p['metadata_sha256'],official_iid_split='train')
        report={'purpose':'source_annotation_window_list_only','class_mapping':td.CLASS_MAPPING,
                'errors':[],'training_run':False,'training_ready':False,'segments':[row]}
        self.assertEqual(td.match_colab_source_windows(prepared,report)['matched_samples'],['sample_010'])
        report['segments'][0]['end_seconds']=8.5
        with self.assertRaisesRegex(td.TrainingError,'differs_from_saved'):td.match_colab_source_windows(prepared,report)
        report['segments']=[]
        with self.assertRaisesRegex(td.TrainingError,'selection_mismatch'):td.match_colab_source_windows(prepared,report)
        report['errors']=[{'reason':'missing'}]
        with self.assertRaisesRegex(td.TrainingError,'incomplete'):td.match_colab_source_windows(prepared,report)

    def test_transfer_allowlist_excludes_raw_models_and_does_not_overwrite(self):
        import zipfile
        from scripts import prepare_colab_bundle as bundle
        # Only code bytes are fixtures; the builder need not import the shipped files.
        for rel in set(bundle.CODE_FILES)-{'src/boram3_cctv/evaluation.py'}:
            p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# fixture\n')
        out=self.root/'artifacts'/'out'
        result=bundle.build_training_preparation(self.root,'manifest.json',out)
        with zipfile.ZipFile(out/result['archive']) as z:
            self.assertFalse(any(n.endswith(('.mp4','.avi','.pt')) for n in z.namelist()))
            receipt=json.loads(z.read('transfer_manifest.json'))
            self.assertEqual(set(z.namelist()),{'transfer_manifest.json',*(f['path'] for f in receipt['files'])})
            for f in receipt['files']:
                self.assertEqual(__import__('hashlib').sha256(z.read(f['path'])).hexdigest(),f['sha256'])
        self.assertFalse(result['training_run'])
        with self.assertRaisesRegex(ValueError,'new_output_directory'):
            bundle.build_training_preparation(self.root,'manifest.json',out)


class SourceTrainingExecutionTests(unittest.TestCase):
    sync = TrainingPreparationTransferTests.sync

    def setUp(self):
        TrainingPreparationTransferTests.setUp(self)
        self.config = json.loads((ROOT/'configs/train_r3d18.limited-real.json').read_text())
        self.config.update(purpose=td.SOURCE_REAL, initialization='official_pretrained_fresh_head',
                           train_manifest='execution.json', max_steps=10, max_seconds=300, probe_steps=3)
        self.execution = {'schema_version':1, 'purpose':td.SOURCE_REAL,
            'official_split_column':'split_in_distribution', 'validation_segments':[], 'test_segments':[],
            'preparation_manifest':'manifest.json', 'sample_ids':self.manifest['sample_ids'],
            'authorization':{'source':'user_message', 'training_authorized':True,
                'raw_response':'Generated fixture authorization only', 'conservative_groups_authorized':True}}
        self.save_execution()

    def save_execution(self):
        self.execution['preparation_manifest_sha256']=pp.sha256(self.root/'manifest.json')
        self.execution['authorization'].update(config_sha256=td.fingerprint(self.config),
            **{k:self.config[k] for k in ('max_epochs','max_steps','max_seconds','probe_steps','probe_max_seconds')})
        (self.root/'execution.json').write_text(json.dumps(self.execution))

    def test_explicit_execution_reuses_origins_and_keeps_evaluation_separate(self):
        r=td.source_real_preflight(self.config,self.root)
        self.assertEqual(r['class_counts'],{'normal':1,'accident':1})
        self.assertEqual((r['approved_segment_labels'],r['source_annotation_labels']),(1,1))
        self.assertFalse(r['generalization_evaluation'])
        self.assertEqual(r['validation_segments'],0)
        self.assertEqual(r['prepared'][1]['label_review']['status'],'unreviewed')
        self.assertTrue(r['prepared'][1]['training_execution_authorized'])
        self.assertFalse(r['prepared'][1]['evaluation_authorized'])
        self.assertEqual(r['prepared'][1]['management_group'],'accident_training_unresolved')
        self.assertEqual(r['temporal_seconds_by_class'],{'normal':[6.],'accident':[2.]})
        self.assertFalse(self.manifest['training_execution_authorized'])

    def test_changed_config_or_missing_authorization_cannot_execute(self):
        with self.assertRaisesRegex(td.TrainingError,'authorization'):
            td.source_real_preflight({**self.config,'learning_rate':.002},self.root)
        self.execution['authorization']['training_authorized']=False; self.save_execution()
        with self.assertRaisesRegex(td.TrainingError,'authorization'):
            td.source_real_preflight(self.config,self.root)

    def test_new_limits_do_not_expand_old_purpose(self):
        for key,value in [('max_steps',41),('max_seconds',301),('max_epochs',6),('probe_steps',4),
                          ('probe_max_seconds',61),('train_scope','all'),('batch_size',2),
                          ('initialization','previous_checkpoint')]:
            with self.subTest(key=key), self.assertRaises(td.TrainingError):
                td.validate_config({**self.config,key:value})
        old=json.loads((ROOT/'configs/train_r3d18.limited-real.json').read_text())
        with self.assertRaisesRegex(td.TrainingError,'max_steps'):
            td.validate_config({**old,'max_steps':40})

    def test_selection_epoch_budget_and_preparation_fingerprint_bound(self):
        self.config['max_steps']=11; self.save_execution()
        with self.assertRaisesRegex(td.TrainingError,'selection_or_limits'):
            td.source_real_preflight(self.config,self.root)
        self.config['max_steps']=10; self.execution['sample_ids']=['sample_010']; self.save_execution()
        with self.assertRaisesRegex(td.TrainingError,'selection_or_limits'):
            td.source_real_preflight(self.config,self.root)
        (self.root/'manifest.json').write_text('{}')
        with self.assertRaisesRegex(td.TrainingError,'reference_changed'):
            td.source_real_preflight(self.config,self.root)

    def test_source_execution_still_rejects_reserved_and_official_test(self):
        self.reserved['segments'][0]['source_sha256']=self.source['source_sha256']
        self.sync(); self.save_execution()
        with self.assertRaisesRegex(td.TrainingError,'reserved'):
            td.source_real_preflight(self.config,self.root)
        self.reserved['segments'][0]['source_sha256']='c'*64
        (self.root/'metadata.csv').write_text('path,accident_time,split_in_distribution\nreal_videos/a.mp4,7,test\n')
        self.sync(); self.save_execution()
        with self.assertRaisesRegex(td.TrainingError,'IID_test'):
            td.source_real_preflight(self.config,self.root)

    def test_held_preparation_is_not_an_execution_manifest(self):
        self.config['train_manifest']='manifest.json'
        with self.assertRaisesRegex(td.TrainingError,'train_only_IID'):
            td.source_real_preflight(self.config,self.root)

    def test_no_validation_split_and_no_changed_preprocessing(self):
        self.execution['validation_segments']=['sample_010']; self.save_execution()
        with self.assertRaisesRegex(td.TrainingError,'train_only_IID'):
            td.source_real_preflight(self.config,self.root)
        self.execution['validation_segments']=[]
        self.config['preprocessing']['image_size']=112; self.save_execution()
        with self.assertRaises(td.TrainingError):
            td.source_real_preflight(self.config,self.root)


class SourceExpansionTests(unittest.TestCase):
    sync = TrainingPreparationTransferTests.sync
    save_execution = SourceTrainingExecutionTests.save_execution

    def setUp(self):
        SourceTrainingExecutionTests.setUp(self)

    def add_source(self):
        row = deepcopy(self.source)
        row.update(sample_id='sample_013', video_path='real_videos/new_00.mp4', source_sha256='d'*64)
        meta = self.root/'metadata.csv'
        meta.write_text(meta.read_text()+'real_videos/new_00.mp4,7,train\n')
        self.sync()
        policy = {'authorization':{'source':'user_message','raw_response':'fixture only','use_source_annotations':True},
                  'training_execution_authorized':False, 'sample_ids':['sample_013'],
                  'metadata_sha256':self.manifest['references']['metadata']['sha256'],
                  'reserved_manifest_sha256':self.manifest['references']['reserved_manifest']['sha256']}
        p=self.root/'extra-policy.json';p.write_text(json.dumps(policy))
        row['label_provenance'].update(csv_row=3, metadata_sha256=policy['metadata_sha256'],
                                     policy_authorization_sha256=pp.sha256(p))
        q=self.root/'extra-report.json';q.write_text(json.dumps({'status':'source_annotation_inputs_prepared',
            'training_execution_authorized':False,'inputs_unchanged':True,'samples':[row]}))
        self.manifest['additional_annotation_batches']=[{
            'annotation_report':{'path':q.name,'sha256':pp.sha256(q)},
            'annotation_policy':{'path':p.name,'sha256':pp.sha256(p)}}]
        self.manifest['sample_ids'].append('sample_013')
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        self.config.update(source_input_limit=33,max_steps=15)
        self.execution['sample_ids']=self.manifest['sample_ids']
        self.save_execution()
        return row,policy

    def test_expansion_requires_explicit_bound_and_preserves_origins(self):
        self.add_source()
        with self.assertRaisesRegex(td.TrainingError,'expansion_scope'):
            td.connect_training_preparation(self.root,self.manifest)
        r=td.source_real_preflight(self.config,self.root)
        self.assertEqual(r['class_counts'],{'normal':1,'accident':2})
        self.assertFalse(r['prepared'][-1]['label_provenance']['human_reviewed'])
        self.assertEqual(r['prepared'][-1]['management_group'],'accident_training_unresolved')

    def test_expansion_upper_bounds_and_changed_approval_fail(self):
        self.add_source()
        for delta in ({'max_steps':166},{'source_input_limit':34},{'max_seconds':301},{'max_epochs':6}):
            with self.subTest(delta=delta), self.assertRaises(td.TrainingError):
                td.validate_config({**self.config,**delta})
        self.config['source_input_limit']=8
        with self.assertRaisesRegex(td.TrainingError,'authorization'):
            td.source_real_preflight(self.config,self.root)

    def test_changed_extra_report_or_policy_cannot_train(self):
        self.add_source()
        for name in ('extra-report.json','extra-policy.json'):
            path=self.root/name;before=path.read_bytes();path.write_text('{}')
            with self.subTest(name=name), self.assertRaisesRegex(td.TrainingError,'reference_changed'):
                td.source_real_preflight(self.config,self.root)
            path.write_bytes(before)

    def test_extra_reserved_source_blocked_even_with_updated_receipt_hash(self):
        row,policy=self.add_source()
        row['source_sha256']='c'*64
        p=self.root/'extra-report.json';d=json.loads(p.read_text());d['samples']=[row];p.write_text(json.dumps(d))
        self.manifest['additional_annotation_batches'][0]['annotation_report']['sha256']=pp.sha256(p)
        (self.root/'manifest.json').write_text(json.dumps(self.manifest));self.save_execution()
        with self.assertRaisesRegex(td.TrainingError,'reserved'):
            td.source_real_preflight(self.config,self.root)

    def test_extra_policy_never_becomes_human_review(self):
        row,policy=self.add_source()
        row['label_provenance']['human_reviewed']=True
        p=self.root/'extra-report.json';d=json.loads(p.read_text());d['samples']=[row];p.write_text(json.dumps(d))
        self.manifest['additional_annotation_batches'][0]['annotation_report']['sha256']=pp.sha256(p)
        (self.root/'manifest.json').write_text(json.dumps(self.manifest));self.save_execution()
        with self.assertRaisesRegex(td.TrainingError,'source_label'):
            td.source_real_preflight(self.config,self.root)

    def test_25_new_candidate_limit_and_time_errors_do_not_create_labels(self):
        from boram3_cctv.train_candidates import select_training_expansion
        rows={}
        for i in range(30):
            path=f'real_videos/video{i}_00.mp4'
            rows[path]=[{'csv_row':i+2,'values':{'path':path,'split_in_distribution':'train',
                        'accident_time':'7','duration':'10'}}]
        for i,value in enumerate(('nan','0.5','20')):
            rows[f'real_videos/video{i}_00.mp4'][0]['values']['accident_time']=value
        r=select_training_expansion(rows,[],[])
        self.assertEqual(len(r['selected']),25)
        self.assertEqual(len(r['exclusions']),3)
        self.assertTrue(all(s['target'] is None for s in r['selected']))
        with self.assertRaises(pp.PreprocessingError):select_training_expansion(rows,[],[],26)

    def test_selection_excludes_test_reserved_and_related_filenames(self):
        from boram3_cctv.train_candidates import select_training_expansion
        paths=['old_00','old_01','reserve_00','reserve_01','test_00','new_00','new_01','other_00']
        rows={f'real_videos/{p}.mp4':[{'csv_row':i+2,'values':{'path':f'real_videos/{p}.mp4',
            'split_in_distribution':'test' if p=='test_00' else 'train','accident_time':'4','duration':'10'}}]
            for i,p in enumerate(paths)}
        r=select_training_expansion(rows,[{'source_path':'real_videos/old_00.mp4'}],
                                    [{'source_path':'real_videos/reserve_00.mp4'}])
        self.assertEqual([s['source_path'] for s in r['selected']],['real_videos/new_00.mp4','real_videos/other_00.mp4'])
        self.assertIsNone(r['independent_event_count'])


if __name__=='__main__':unittest.main()
