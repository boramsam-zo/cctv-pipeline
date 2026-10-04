import unittest,json,collections,sys,copy,zipfile,hashlib,ast
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from boram3_cctv.training import source_balanced_order,loop_time_expired
from boram3_cctv.training_data import TrainingError

class SamplingTests(unittest.TestCase):
 def setUp(self):
  self.root=Path('artifacts/colab/balanced-631-20260925-01')
  self.rows=json.loads((self.root/'training-input-proposal.json').read_text())['records']
  self.c=json.loads((self.root/'training-config.json').read_text())
 def test_counts_and_each_small_pool_cycle(self):
  for round_index in range(5):
   order=source_balanced_order(self.rows,self.c,round_index)
   self.assertEqual(len(order),631)
   counts=collections.Counter('accident' if self.rows[i]['target'] else 'accnormal' if self.rows[i].get('source_path') else 'detrac' for i in order)
   self.assertEqual(counts,dict(accident=315,accnormal=158,detrac=158))
   normal=collections.Counter(i for i in order if self.rows[i].get('source_path') and self.rows[i]['target']==0)
   self.assertEqual(len(normal),28);self.assertEqual(set(normal.values()),{5,6})
 def test_replay_and_changed_order(self):
  first=source_balanced_order(self.rows,self.c,2)
  self.assertEqual(first,source_balanced_order(self.rows,self.c,2))
  self.assertNotEqual(first,source_balanced_order(self.rows,self.c,3))
  # Resume uses same round and cursor; no stochastic sampler state to restore.
  self.assertEqual(first[:266]+source_balanced_order(self.rows,self.c,2)[266:],first)
 def test_invalid_scope_counts_excluded(self):
  for key,value in [('sampling_policy','bad'),('batch_size',2),('execution_scope','fixed_631_epoch_bounded_v1')]:
   with self.assertRaises(TrainingError):source_balanced_order(self.rows,dict(self.c,**{key:value}),0)
  rows=copy.deepcopy(self.rows);rows[0]['source_path']='real_videos/Z_GJQ1KrRJw_00.mp4'
  with self.assertRaises(TrainingError):source_balanced_order(rows,self.c,0)
  with self.assertRaises(TrainingError):source_balanced_order(self.rows[:-1],self.c,0)
  with self.assertRaises(TrainingError):loop_time_expired(self.c,100)
  self.assertFalse(loop_time_expired(dict(self.c,training_execution_authorized=True),4000))
 def test_preserved_data_and_bundle(self):
  self.assertEqual((self.root/'training-input-proposal.json').read_bytes(),Path('artifacts/colab/train-631-20260924-01/training-input-proposal.json').read_bytes())
  receipt=json.loads((self.root/'bundle-receipt.json').read_text());raw=(self.root/'balanced-631-code.zip').read_bytes()
  self.assertEqual(hashlib.sha256(raw).hexdigest(),receipt['zip_sha256'])
  with zipfile.ZipFile(self.root/'balanced-631-code.zip') as z:
   for name in ['src/boram3_cctv/training.py','scripts/train_r3d18.py']:self.assertEqual(z.read(name),Path(name).read_bytes())
   ast.parse(z.read('run_balanced.py').decode())
  cell=(self.root/'colab-cell.py').read_text();ast.parse(cell);self.assertIn(receipt['zip_sha256'],cell);self.assertIn("!= '시작'",cell)
 def test_actual_loop_resume_matches_sample_ids_and_weights(self):
  import torch,tempfile
  from boram3_cctv import training as tr
  class Data:
   def __init__(self,rows):self.records=rows
   def __len__(self):return len(self.records)
   def __getitem__(self,i):return torch.full((3,2,4,4),i/631),self.records[i]['target'],self.records[i]['sample_id']
  class Model(torch.nn.Module):
   def __init__(self):
    super().__init__();self.features=torch.nn.Sequential(torch.nn.AdaptiveAvgPool3d(1),torch.nn.Conv3d(3,2,1),torch.nn.BatchNorm3d(2));self.fc=torch.nn.Linear(2,2)
   def forward(self,x):return self.fc(self.features(x).flatten(1))
  torch.set_num_threads(2);torch.manual_seed(1);model=Model();initial=copy.deepcopy(model.state_dict())
  c=dict(self.c,training_execution_authorized=True,device='cpu');device=torch.device('cpu');ds=Data(self.rows)
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);tr.run_loop(model,ds,c,{},p/'full',device,stop_after=4)
   first=Model();first.load_state_dict(initial);tr.run_loop(first,ds,c,{},p/'first',device,stop_after=2)
   resumed=Model();tr.run_loop(resumed,ds,c,{},p/'resumed',device,resume=p/'first/checkpoint.pt',stop_after=2)
   for key in model.state_dict():torch.testing.assert_close(model.state_dict()[key],resumed.state_dict()[key],rtol=0,atol=0)
   logs=lambda n:[json.loads(x)['sample_ids'] for x in (p/n/'train.jsonl').read_text().splitlines()]
   self.assertEqual(logs('full'),logs('first')+logs('resumed'))
