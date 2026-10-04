"""Preparation contract tests; no live database or model calls."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from apps.be_worker.prepare_stage1 import prepare, sql


class Stage1Tests(unittest.TestCase):
    def test_one_transaction_with_corpus_preservation_and_role_check(self):
        value = sql()
        self.assertEqual(value.splitlines().count('BEGIN;'), 1)
        self.assertEqual(value.splitlines().count('COMMIT;'), 1)
        self.assertLess(value.index('stage1_rag_before'), value.index('CREATE SCHEMA'))
        self.assertIn('b.original IS DISTINCT FROM to_jsonb(c)', value)
        self.assertIn('SET LOCAL ROLE cctv_app;', value)
        self.assertIn("'existing_rag_corpus_empty'", value)

    def test_commit_receipt_only_after_process_success(self):
        def runner(command, **kwargs):
            self.assertIn('ON_ERROR_STOP=1', command)
            self.assertEqual(command[command.index('-f')+1], '/known/compose.yaml')
            self.assertEqual(kwargs['input'], sql())
            return SimpleNamespace(returncode=0, stdout='{"stage":1,"status":"passed","rag_unchanged":true}')
        self.assertTrue(prepare(Path('/known/compose.yaml'), runner)['transaction_committed'])

    def test_failed_commit_cannot_report_success_or_leak_stderr(self):
        def runner(*args, **kwargs):
            return SimpleNamespace(returncode=1, stdout='{"stage":1,"status":"passed","rag_unchanged":true}',
                                   stderr='private_connection_details')
        with self.assertRaises(RuntimeError) as caught:
            prepare(Path('/known/compose.yaml'), runner)
        self.assertNotIn('private_connection_details', str(caught.exception))

    def test_invalid_receipt_is_not_success(self):
        def runner(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"stage":1,"status":"failed"}')
        with self.assertRaises(ValueError):
            prepare(Path('/known/compose.yaml'), runner)


if __name__ == '__main__':
    unittest.main()
