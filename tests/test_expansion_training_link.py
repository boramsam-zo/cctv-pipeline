import copy
import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from boram3_cctv import training_data as td
from boram3_cctv import preprocessing as pp

class ExpansionTrainingLinkTests(unittest.TestCase):
    def fixture(self):
        c=dict(source_path='real_videos/a_00.mp4',start_seconds=1.,end_seconds=3.,target=1,
               label_origin='source_annotation_derived',csv_row=2,annotation_seconds=2.)
        normal=dict(sequence_id='MVI_1',start_seconds=0,end_seconds=2,target=0,label_origin='user_assumed_normal')
        rows=[]
        for i,x in enumerate((c,normal)):
            rows.append(dict(x,sample_id=f'expansion_{i:04d}',declared_target=x['target'],
                status='prepared_not_split',input_origin='previous' if i==0 else 'current',input_base='/old',
                input_path=f'expansion_{i:04d}/frames.npy',frames_sha256='fixture',
                input={'array_shape':[16,224,224,3],'target':x['target'],
                       'frames':[{'actual_seconds':x['start_seconds']+(k+.5)/8} for k in range(16)]}))
        manifest=dict(purpose='expanded_inputs_not_training',records=rows)
        request=dict(accident_candidates=[c],normal_candidates=[normal],reserved_accident_paths=[],reserved_normal_sequences=[])
        metadata={c['source_path']:[dict(csv_row=2,values={'split_in_distribution':'train','accident_time':'2'})]}
        return manifest,request,metadata,dict(train=['MVI_1'],test=['MVI_2'])

    def connect(self,args):return td.connect_expansion_inputs(*args,previous_root='/old',current_root='/new')

    def test_two_origins_conservative_train_only_not_authorized(self):
        r=self.connect(self.fixture())
        self.assertEqual(r['records'][0]['array_path'],'/old/expansion_0000/frames.npy')
        self.assertEqual(r['records'][1]['array_path'],'/new/expansion_0001/frames.npy')
        self.assertFalse(r['training_execution_authorized']);self.assertEqual(r['validation_segments'],[])

    def test_official_test_rejected(self):
        args=self.fixture();next(iter(args[2].values()))[0]['values']['split_in_distribution']='test'
        with self.assertRaises(td.TrainingError):self.connect(args)

    def test_reserved_normal_rejected(self):
        args=self.fixture();args[1]['reserved_normal_sequences']=['MVI_1']
        with self.assertRaises(td.TrainingError):self.connect(args)

    def test_error_remains_held(self):
        args=self.fixture();args[0]['records'][0].update(status='error',target=None,reason='decode_error')
        r=self.connect(args);self.assertEqual(len(r['records']),1);self.assertEqual(len(r['held']),1)

    def test_origin_and_path_changes_rejected(self):
        for updates in ({'input_base':'/wrong'},{'input_path':'../frames.npy'}):
            args=self.fixture();args[0]['records'][0].update(updates)
            with self.assertRaises(td.TrainingError):self.connect(args)

    def test_file_verification_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);a=p/'frames.npy';a.write_bytes(b'fixture');f=p/'frames.json';pp.write_json(f,{'target':0})
            r={'array_path':str(a),'array_sha256':pp.sha256(a),'frames_path':str(f),'frames_sha256':pp.sha256(f)}
            proposal={'purpose':'expanded_training_input_proposal_not_authorized','records':[r]}
            self.assertEqual(td.verify_expansion_input_files(proposal),[r])
            a.write_bytes(b'changed')
            with self.assertRaises(td.TrainingError):td.verify_expansion_input_files(proposal)
