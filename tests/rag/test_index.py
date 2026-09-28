import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('rag_index',ROOT/'apps/rag_index/index.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SOURCE=ROOT/'examples/chunks.jsonl'

class IndexTests(unittest.TestCase):
    def test_existing_source(self):
        rows=m.read_chunks(SOURCE)
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['embedding'] is None for r in rows))
    def test_vectors(self):
        self.assertAlmostEqual(sum(x*x for x in m.validate_vector([2.0]*768)),1)
        for bad in ([0]*768,[1]*767,[float('nan')]*768,[True]*768):
            with self.assertRaises(ValueError):m.validate_vector(bad)
    def test_mock_embedding_preserves_original_and_completion(self):
        original=SOURCE.read_bytes()
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'vectors.jsonl'
            with patch.object(m,'embed',return_value=[1.0]*768) as call:
                self.assertEqual(m.embed_file(SOURCE,target),2)
                self.assertEqual(call.call_count,2)
                self.assertTrue(all(c.args[1]=='RETRIEVAL_DOCUMENT' for c in call.call_args_list))
            self.assertEqual(len(m.read_chunks(target,embedded=True)),2)
            self.assertTrue(Path(str(target)+'.complete.json').exists())
            with self.assertRaises(FileExistsError):m.embed_file(SOURCE,target)
        self.assertEqual(original,SOURCE.read_bytes())
    def test_failed_call_no_completion(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'vectors.jsonl'
            with patch.object(m,'embed',side_effect=[[1.0]*768,RuntimeError('test failure')]):
                with self.assertRaises(RuntimeError):m.embed_file(SOURCE,p)
            self.assertFalse(Path(str(p)+'.complete.json').exists())
            with self.assertRaises(FileNotFoundError):m.load(p)
    def test_changed_text_rejected(self):
        row=m.read_chunks(SOURCE)[0];row['content']='changed'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'invalid.jsonl';p.write_text(json.dumps(row))
            with self.assertRaises(ValueError):m.read_chunks(p)
if __name__=='__main__':unittest.main()
