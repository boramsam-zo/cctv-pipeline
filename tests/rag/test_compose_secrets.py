"""Secret persistence and container configuration; no Docker/API calls."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result
host=module('compose_host','infra/compose/rag/prepare-host.py')
entry=module('compose_entry','apps/rag_index/container_entrypoint.py')

class SecretTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.data=Path(temp.name)/'data';self.secrets=Path(temp.name)/'secrets'
        self.chown=patch.object(host.os,'chown');self.chown.start();self.addCleanup(self.chown.stop)
    def prepare(self): host.prepare(self.data,self.secrets)
    def test_reprepare_preserves_passwords_for_populated_database(self):
        self.prepare()
        before={n:(self.secrets/n).read_bytes() for n in ('db_app_password','db_admin_password')}
        (self.data/'postgresql/PG_VERSION').write_text('16')
        self.prepare()
        self.assertEqual(before,{n:(self.secrets/n).read_bytes() for n in before})
        self.assertEqual(self.secrets.stat().st_mode & 0o777,0o700)
        self.assertEqual((self.secrets/'db_app_password').stat().st_mode & 0o777,0o444)
    def test_populated_database_missing_secret_is_not_rotated(self):
        self.prepare();(self.data/'postgresql/PG_VERSION').write_text('16')
        (self.secrets/'db_app_password').unlink()
        with self.assertRaisesRegex(RuntimeError,'missing secret'):self.prepare()
        self.assertFalse((self.secrets/'db_app_password').exists())
    def test_foreign_database_is_preserved(self):
        (self.data/'postgresql').mkdir(parents=True)
        version=self.data/'postgresql/PG_VERSION';version.write_text('16')
        with self.assertRaisesRegex(RuntimeError,'another setup'):self.prepare()
        self.assertEqual(version.read_text(),'16');self.assertFalse(self.secrets.exists())
    def test_wrapper_reads_files_and_empty_key_causes_no_api_setup(self):
        self.prepare()
        with patch.dict(os.environ,{},clear=True):
            entry.configure(self.secrets)
            self.assertTrue(os.environ['RAG_DATABASE_URL'].endswith('@db:5432/cctv'))
            self.assertNotIn('GEMINI_API_KEY',os.environ)
    def test_invalid_password_is_rejected(self):
        self.secrets.mkdir();(self.secrets/'db_app_password').write_text('not-a-password')
        with self.assertRaisesRegex(ValueError,'Invalid'):entry.configure(self.secrets)

if __name__=='__main__': unittest.main()
