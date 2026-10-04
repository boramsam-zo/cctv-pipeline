import unittest,json,copy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from boram3_cctv.training_data import validate_reviewed_normal_details,TrainingError
class ReviewConnectionTests(unittest.TestCase):
 def row(self):
  p=Path(__file__).resolve().parents[1]/'artifacts/colab/normal-606-20260924-01/training-input-proposal.json'
  return json.loads(p.read_text())['records'][-3]
 def test_pending_blocks(self):
  r=self.row()
  with self.assertRaises(TrainingError):validate_reviewed_normal_details(r,r['reviewed_input_details'])
 def test_explicit_approved_repeat(self):
  r=self.row();r['input_quality_review']='approved';validate_reviewed_normal_details(r,r['reviewed_input_details'])
 def test_reverse_and_outside_rejected(self):
  for t in [-1,3]:
   r=self.row();r['input_quality_review']='approved';r['reviewed_input_details']['frames'][5]['actual_seconds']=t
   with self.assertRaises(TrainingError):validate_reviewed_normal_details(r,r['reviewed_input_details'])
 def test_unbound_target_rejected(self):
  r=self.row();r['input_quality_review']='approved';d=copy.deepcopy(r['reviewed_input_details']);d['target']=0
  with self.assertRaises(TrainingError):validate_reviewed_normal_details(r,d)
 def test_strict_rejects_repeats(self):
  r=self.row();r['input_quality_review']='approved';r['sampling_policy']='strict-distinct-v1';r['reviewed_input_details']['target']=0
  with self.assertRaises(TrainingError):validate_reviewed_normal_details(r,r['reviewed_input_details'])

class ReadOnlyInputTests(ReviewConnectionTests):
 def test_actual_array_transform_and_default_gate(self):
  import tempfile,numpy as np
  from boram3_cctv import preprocessing as pp
  from boram3_cctv.training import inspect_reviewed_normal_input,SegmentDataset
  with tempfile.TemporaryDirectory() as t:
   r=self.row();p=Path(t);np.save(p/'frames.npy',np.zeros((16,224,224,3),dtype=np.uint8))
   pp.write_json(p/'frames.json',r['reviewed_input_details'])
   for k,n in [('array','frames.npy'),('frames','frames.json')]:r[k+'_path']=str(p/n);r[k+'_sha256']=pp.sha256(p/n)
   before=copy.deepcopy(r);settings={'frame_count':16,'image_size':224}
   result=inspect_reviewed_normal_input(r,settings)
   self.assertEqual(result['shape'],[3,16,224,224]);self.assertEqual(r,before)
   with self.assertRaises(TrainingError):SegmentDataset([r],settings)[0]
   (p/'frames.npy').write_bytes(b'changed')
   with self.assertRaises(TrainingError):inspect_reviewed_normal_input(r,settings)
 def test_inspection_still_blocks_reverse(self):
  r=self.row();r['reviewed_input_details']['frames'][4]['actual_seconds']=-1
  with self.assertRaises(TrainingError):validate_reviewed_normal_details(r,r['reviewed_input_details'],inspection_only=True)

class EpochBoundedTests(unittest.TestCase):
 def test_only_explicit_606_scope_has_no_clock_cap(self):
  from boram3_cctv.training import loop_time_expired
  c=dict(max_seconds=None,execution_scope='fixed_606_epoch_bounded_v1',training_execution_authorized=True,max_steps=3030,max_epochs=5)
  self.assertFalse(loop_time_expired(c,999999))
  self.assertTrue(loop_time_expired(dict(max_seconds=1800),1801))
  for k,v in [('max_steps',3031),('training_execution_authorized',False),('execution_scope','fixed_603_v1')]:
   with self.assertRaises(TrainingError):loop_time_expired(dict(c,**{k:v}),99999)

class Comparison606PackagingTests(unittest.TestCase):
 def test_bundle_uses_approved606_and_603_baseline(self):
  import zipfile,hashlib
  root=Path(__file__).resolve().parents[1]
  with zipfile.ZipFile(root/'artifacts/colab/compare-606-20260924-01/compare-606-code.zip') as z:
   proposal=json.loads(z.read('training-input-proposal.json'))
   self.assertEqual(len(proposal['records']),606)
   self.assertEqual(z.read('training-input-proposal.json'),(root/'artifacts/colab/train-606-20260924-01/training-input-proposal.json').read_bytes())
   baseline=json.loads(z.read('baseline-thirty.json'))
   from boram3_cctv.evaluation import MODEL603_SHA,check_thirty_baseline
   self.assertEqual(baseline['model_sha256'],MODEL603_SHA)
   check_thirty_baseline(baseline,json.loads(z.read('benchmark-plan.json')))
   bad=copy.deepcopy(baseline);bad['rows'].pop()
   with self.assertRaises(TrainingError):check_thirty_baseline(bad,json.loads(z.read('benchmark-plan.json')))
   case=json.loads(z.read('normal-case-baseline.json'))
   self.assertEqual((case['start_seconds'],case['end_seconds']),(1,3))
   self.assertEqual(len(case['frames']),16)
   for n in z.namelist():
    if n.endswith('.py'):compile(z.read(n),n,'exec')

class Followup609ScopeTests(unittest.TestCase):
 def test_scope_has_exact_five_epoch_bound(self):
  from boram3_cctv.training import loop_time_expired
  c=dict(max_seconds=None,execution_scope='fixed_609_epoch_bounded_v1',training_execution_authorized=True,max_steps=3045,max_epochs=5)
  self.assertFalse(loop_time_expired(c,99999))
  for change in [dict(max_steps=3030),dict(max_epochs=6),dict(training_execution_authorized=False),dict(execution_scope='unknown')]:
   with self.assertRaises(TrainingError):loop_time_expired(dict(c,**change),0)
 def test_bundle_has_only_three_new_verified_arrays(self):
  import zipfile,hashlib
  root=Path(__file__).resolve().parents[1]
  folder=root/'artifacts/colab/train-609-20260924-01'
  with zipfile.ZipFile(folder/'train-609-code.zip') as z:
   p=json.loads(z.read('training-input-proposal.json'));c=json.loads(z.read('training-config.json'))
   self.assertEqual(len(p['records']),609)
   self.assertEqual(sum(r['target']==1 for r in p['records']),403)
   self.assertEqual(c['max_steps'],609*5)
   self.assertEqual(hashlib.sha256(z.read('training-input-proposal.json')).hexdigest(),c['input_proposal_sha256'])
   self.assertEqual(len([n for n in z.namelist() if n.endswith('.npy')]),3)
   for r in p['records'][-3:]:
    self.assertEqual(hashlib.sha256(z.read(r['array_path'])).hexdigest(),r['array_sha256'])
    self.assertEqual(hashlib.sha256(z.read(r['frames_path'])).hexdigest(),r['frames_sha256'])
    validate_reviewed_normal_details(r,json.loads(z.read(r['frames_path'])))

class Compare609Tests(unittest.TestCase):
 def test_comparison_scope_and_baseline(self):
  import zipfile
  from boram3_cctv.evaluation import check_609_comparison
  root=Path(__file__).resolve().parents[1]
  with zipfile.ZipFile(root/'artifacts/colab/compare-609-20260924-01/compare-609-code.zip') as z:
   baseline=json.loads(z.read('baseline-thirty.json'));plan=json.loads(z.read('benchmark-plan.json'))
  info={'training_progress':{'step':3045,'completed_epochs':5}}
  check_609_comparison(info,baseline,plan,[{}]*609)
  with self.assertRaises(TrainingError):check_609_comparison(info,baseline,plan,[{}]*606)
  bad=copy.deepcopy(baseline);bad['model_sha256']='wrong'
  with self.assertRaises(TrainingError):check_609_comparison(info,bad,plan,[{}]*609)
  bad=copy.deepcopy(baseline);bad['rows']=bad['rows'][:-1]
  with self.assertRaises(TrainingError):check_609_comparison(info,bad,plan,[{}]*609)

class Provisional631Tests(unittest.TestCase):
 def proposal(self):
  return json.loads(Path('artifacts/colab/train-631-20260924-01/training-input-proposal.json').read_text())
 def test_exact_partition_and_existing609_preserved(self):
  p=self.proposal();old=json.loads(Path('artifacts/colab/train-609-20260924-01/training-input-proposal.json').read_text())
  self.assertEqual(p['records'][:609],old['records']);self.assertEqual(len(p['records']),631)
  self.assertEqual(sum(r['target'] for r in p['records']),403)
  expected={f'normal_batch_{i:02}' for i in range(1,31) if i not in {5,9,12,17,24,26,27,30}}
  self.assertEqual({r['sample_id'] for r in p['records'][609:]},expected)
 def test_all22_actual_inputs_and_provenance(self):
  from boram3_cctv.training import SegmentDataset
  from boram3_cctv.training_data import validate_provisional_normal_details
  root=Path('artifacts/colab/train-631-20260924-01')
  for original in self.proposal()['records'][609:]:
   r=copy.deepcopy(original)
   validate_provisional_normal_details(r,r['reviewed_input_details'])
   for k in ['array_path','frames_path']:r[k]=str(root/r[k])
   x,y,sid=SegmentDataset([r],{'frame_count':16,'image_size':224})[0]
   self.assertEqual(tuple(x.shape),(3,16,224,224));self.assertEqual(y,0)
   self.assertFalse(r['human_reviewed']);self.assertIsNone(r['reviewed_input_details']['target'])
 def test_held_unapproved_and_false_human_review_rejected(self):
  from boram3_cctv.training_data import validate_provisional_normal_details
  for key,value in [('sample_id','normal_batch_05'),('label_status','pending'),('human_reviewed',True),('target',1),('proposed_split','test'),('sampling_policy','strict-distinct-v1')]:
   r=copy.deepcopy(self.proposal()['records'][-1]);r[key]=value
   with self.assertRaises(TrainingError):validate_provisional_normal_details(r,r['reviewed_input_details'])
 def test_bad_timing_and_unbound_details_rejected(self):
  from boram3_cctv.training_data import validate_provisional_normal_details
  for seconds in [-1,2]:
   r=copy.deepcopy(self.proposal()['records'][-1]);r['reviewed_input_details']['frames'][4]['actual_seconds']=seconds
   with self.assertRaises(TrainingError):validate_provisional_normal_details(r,r['reviewed_input_details'])
  r=self.proposal()['records'][-1];d=copy.deepcopy(r['reviewed_input_details']);d['target']=0
  with self.assertRaises(TrainingError):validate_provisional_normal_details(r,d)
 def test_bundle_hash_and_execution_gate(self):
  import hashlib,zipfile
  from boram3_cctv.training import loop_time_expired
  root=Path('artifacts/colab/train-631-20260924-01');receipt=json.loads((root/'bundle-receipt.json').read_text())
  self.assertEqual(hashlib.sha256((root/'train-631-code.zip').read_bytes()).hexdigest(),receipt['zip_sha256'])
  with zipfile.ZipFile(root/'train-631-code.zip') as z:
   self.assertEqual(z.namelist(),receipt['members']);c=json.loads(z.read('training-config.json'))
   self.assertEqual(hashlib.sha256(z.read('training-input-proposal.json')).hexdigest(),c['input_proposal_sha256'])
  with self.assertRaises(TrainingError):loop_time_expired(c,1)
  c['training_execution_authorized']=True;self.assertFalse(loop_time_expired(c,10000))
  c['max_steps']=3156
  with self.assertRaises(TrainingError):loop_time_expired(c,1)

class Compare631Tests(unittest.TestCase):
 def test_fixed_model_baseline_and_thirty(self):
  import zipfile
  from boram3_cctv.evaluation import check_631_comparison
  with zipfile.ZipFile('artifacts/colab/compare-631-20260924-01/compare-631-code.zip') as z:
   b=json.loads(z.read('baseline-thirty.json'));p=json.loads(z.read('benchmark-plan.json'))
  info={'training_progress':{'step':3155,'completed_epochs':5}};sha='7dbd352a3992e2c788ba517522698f5dd1353f5df6c46f4247a74820c59b891c'
  check_631_comparison(info,b,p,[{}]*631,sha)
  for count,digest in [(609,sha),(631,'wrong')]:
   with self.assertRaises(TrainingError):check_631_comparison(info,b,p,[{}]*count,digest)
  for key,value in [('model_sha256','wrong'),('rows',b['rows'][:-1])]:
   bad=copy.deepcopy(b);bad[key]=value
   with self.assertRaises(TrainingError):check_631_comparison(info,bad,p,[{}]*631,sha)
 def test_zip_and_notebook_binding(self):
  import hashlib,zipfile,ast
  root=Path('artifacts/colab/compare-631-20260924-01');receipt=json.loads((root/'bundle-receipt.json').read_text())
  self.assertEqual(hashlib.sha256((root/'compare-631-code.zip').read_bytes()).hexdigest(),receipt['zip_sha256'])
  with zipfile.ZipFile(root/'compare-631-code.zip') as z:
   ast.parse(z.read('src/boram3_cctv/evaluation.py').decode());self.assertIn(b'def check_631_comparison(',z.read('src/boram3_cctv/evaluation.py'))
   self.assertEqual(z.read('training-input-proposal.json'),Path('artifacts/colab/train-631-20260924-01/training-input-proposal.json').read_bytes())
  s=(root/'colab-cell.py').read_text();ast.parse(s)
  nb=json.loads(Path('notebooks/evaluate_r3d18_colab.ipynb').read_text());self.assertTrue(any(''.join(c.get('source',[]))==s for c in nb['cells'] if c['cell_type']=='code'))

class AssumedNormal711Tests(unittest.TestCase):
 def test_eighty_bound_inputs_and_preserved631(self):
  from boram3_cctv.training_data import validate_pre_accident_normal_details
  p=json.loads(Path('artifacts/colab/train-711-20260924-01/training-input-proposal.json').read_text())
  old=json.loads(Path('artifacts/colab/train-631-20260924-01/training-input-proposal.json').read_text())
  self.assertEqual(p['records'][:631],old['records']);self.assertEqual(len(p['records']),711)
  for r in p['records'][631:]:validate_pre_accident_normal_details(r,r['reviewed_input_details'])
  for key,v in [('annotation_seconds',5.9),('official_iid_split','test'),('human_reviewed',True),('target',1)]:
   r=copy.deepcopy(p['records'][-1]);r[key]=v
   with self.assertRaises(TrainingError):validate_pre_accident_normal_details(r,r['reviewed_input_details'])
 def test_package_authorization_and_manifest_binding(self):
  import zipfile,hashlib,ast
  from boram3_cctv.training import loop_time_expired
  b=Path('artifacts/colab/train-711-20260924-01');r=json.loads((b/'bundle-receipt.json').read_text());self.assertEqual(hashlib.sha256((b/'train-711-code.zip').read_bytes()).hexdigest(),r['sha256'])
  with zipfile.ZipFile(b/'train-711-code.zip') as z:
   c=json.loads(z.read('training-config.json'));self.assertEqual(hashlib.sha256(z.read('training-input-proposal.json')).hexdigest(),c['input_proposal_sha256'])
   for n in ['src/boram3_cctv/training.py','src/boram3_cctv/training_data.py','scripts/train_r3d18.py']:self.assertEqual(z.read(n),Path(n).read_bytes())
  self.assertEqual(c['normal80_manifest_sha256'],hashlib.sha256(Path('artifacts/colab/normal-80-20260924-01/received/manifest.json').read_bytes()).hexdigest())
  with self.assertRaises(TrainingError):loop_time_expired(c,0)
  c['training_execution_authorized']=True;self.assertFalse(loop_time_expired(c,99999));c['max_steps']=3556
  with self.assertRaises(TrainingError):loop_time_expired(c,0)
  cell=(b/'colab-cell.py').read_text();ast.parse(cell);ast.parse((b/'run711.py').read_text())
  self.assertEqual(cell,''.join(json.loads(Path('notebooks/train_expansion_r3d18_colab.ipynb').read_text())['cells'][-1]['source']))
