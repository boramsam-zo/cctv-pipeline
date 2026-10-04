import copy,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
P=ROOT/'artifacts/colab/normal-batch-20260924-01/prepare_review.py'
spec=importlib.util.spec_from_file_location('normal_review_batch',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class NormalBatchTests(unittest.TestCase):
 def setUp(self):self.plan=json.loads((ROOT/'artifacts/data-review/normal-batch-20260924-01/candidates.json').read_text())
 def test_fixed_pending_scope(self):self.assertEqual(len(m.validate(self.plan)),30)
 def test_bad_scope_rejected(self):
  for field,value in [('target',0),('source_path','../bad.mp4'),('end_seconds',3),('training_execution_authorized',True)]:
   p=copy.deepcopy(self.plan);p['records'][0][field]=value
   with self.assertRaises(ValueError):m.validate(p)
  p=copy.deepcopy(self.plan);p['records'][0]['source_annotation']['split_in_distribution']='test'
  with self.assertRaises(ValueError):m.validate(p)
 def test_errors_stay_unlabeled_all_rows_reported(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'candidates.json').write_text(json.dumps(self.plan));out=root/'results'
   with patch.object(m,'ROOT',root),patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr='missing_video')),patch('builtins.print'):
    m.run(root/'missing',out)
   rows=json.loads((out/'manifest.json').read_text())['records']
   self.assertEqual(len(rows),30);self.assertTrue(all(r['status']=='error' and r['target'] is None for r in rows));self.assertTrue((out/'report.html').is_file())
 def test_no_budget_means_not_run_not_normal(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'candidates.json').write_text(json.dumps(self.plan));out=root/'results'
   with patch.object(m,'ROOT',root),patch.object(m.time,'monotonic',side_effect=[0]+[1000]*100),patch.object(m.subprocess,'run') as run,patch('builtins.print'):
    m.run(root/'missing',out)
   run.assert_not_called();rows=json.loads((out/'manifest.json').read_text())['records'];self.assertTrue(all(r['status']=='not_run_time_budget' and r['target'] is None for r in rows))

class Normal80Tests(unittest.TestCase):
 def load(self,root):
  import zipfile,importlib.util
  from pathlib import Path
  with zipfile.ZipFile('artifacts/colab/normal-80-20260924-01/normal-80-code.zip') as z:z.extractall(root)
  spec=importlib.util.spec_from_file_location('normal80',Path(root)/'prepare_inputs.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
 def test_rules_and_no_test(self):
  import tempfile,copy
  with tempfile.TemporaryDirectory() as t:
   m=self.load(t);p=m.pp.read_json(m.ROOT/'candidates.json');self.assertEqual(len(m.validate(p)),80)
   for key,value in [('target',1),('annotation_seconds',5.99),('official_iid_split','test'),('human_reviewed',True)]:
    bad=copy.deepcopy(p);bad['records'][0][key]=value
    with self.assertRaises(ValueError):m.validate(bad)
 def test_eighty_progress_updates_and_errors_retained(self):
  import tempfile,subprocess,json
  from pathlib import Path
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as t:
   m=self.load(t);out=Path(t)/'output';count=[]
   def failed(args,**kwargs):
    previous=json.loads((out/'manifest.json').read_text());self.assertEqual(len(previous['records']),80);count.append(1)
    return subprocess.CompletedProcess(args,1,stderr='fixture decode failure')
   with patch.object(m.subprocess,'run',side_effect=failed):m.run(Path(t),out)
   rows=json.loads((out/'manifest.json').read_text())['records'];self.assertEqual(len(count),80);self.assertTrue(all(r['status']=='error' for r in rows));self.assertTrue((out/'report.html').is_file())
 def test_bundle_notebook_and_array_save_setting(self):
  import json,hashlib,ast,zipfile
  from pathlib import Path
  b=Path('artifacts/colab/normal-80-20260924-01');receipt=json.loads((b/'bundle-receipt.json').read_text());self.assertEqual(hashlib.sha256((b/'normal-80-code.zip').read_bytes()).hexdigest(),receipt['sha256'])
  with zipfile.ZipFile(b/'normal-80-code.zip') as z:
   s=z.read('prepare_inputs.py').decode();ast.parse(s);self.assertIn('save_unlabeled_input=True',s)
  cell=(b/'colab-cell.py').read_text();ast.parse(cell);nb=json.loads(Path('notebooks/prepare_training_inputs_colab.ipynb').read_text());self.assertEqual(''.join(nb['cells'][-1]['source']),cell)
