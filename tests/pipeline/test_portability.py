"""Portable document input, workspace preparation and deploy package contracts."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from apps.rag_index.chunk import chunks, write_chunks
from apps.rag_index.index import read_chunks
from scripts.prepare_pipeline_local import prepare
from scripts.package_runpod import build, record, RUNTIME_FILES

ROOT = Path(__file__).resolve().parents[2]


class PortableTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.doc = json.loads((ROOT / 'examples/documents.jsonl').read_text())

    def test_chunks_keep_source_offsets_and_metadata(self):
        text = ('원문 문장입니다.  공백도 유지합니다.\n' * 20).strip()
        self.doc['sections'] = [{'section': '제1조', 'content': text, 'page': 3}]
        self.doc['effective_to'] = '2027-01-01'
        rows = chunks([self.doc], 100)
        self.assertGreater(len(rows), 1)
        self.assertEqual(''.join(row['content'] for row in rows), text)
        self.assertEqual(rows, chunks([self.doc], 100))
        self.assertEqual(len(rows), len({r['chunk_id'] for r in rows}))
        for row in rows:
            self.assertEqual(row['content'], text[row['char_start']:row['char_end']])
            self.assertLessEqual(len(row['content']), 100)
            self.assertEqual(row['page'], 3)
            self.assertEqual(row['effective_to'], '2027-01-01')

    def test_chunk_file_is_compatible_and_never_overwritten(self):
        output = self.root / 'chunks.jsonl'
        source = ROOT / 'examples/documents.jsonl'
        self.assertEqual(write_chunks(source, output), 2)
        self.assertEqual(len(read_chunks(output)), 2)
        original = output.read_bytes()
        with self.assertRaises(FileExistsError):
            write_chunks(source, output)
        self.assertEqual(output.read_bytes(), original)

    def test_invalid_metadata_rejected(self):
        with self.assertRaises(ValueError):
            chunks([self.doc, self.doc])
        self.doc['effective_from'] = 'unknown'
        with self.assertRaises(ValueError):
            chunks([self.doc])

    def test_workspace_preserves_secrets_and_quotes_paths(self):
        directory = self.root / "space ' directory"
        env_file = prepare(directory, repo=ROOT)
        before = (directory / 'secrets/db_app_password').read_bytes()
        (directory / 'data/postgresql/PG_VERSION').write_text('16')
        prepare(directory, repo=ROOT)
        self.assertEqual(before, (directory / 'secrets/db_app_password').read_bytes())
        self.assertEqual((directory / 'secrets').stat().st_mode & 0o777, 0o700)
        cmd = ['bash', '-c', 'source "$1"; test "$BE_SPOOL_DIR" = "$2"',
               'test', str(env_file), str((directory / 'spool').resolve())]
        subprocess.run(cmd, check=True)
        (directory / 'secrets/db_app_password').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            prepare(directory, repo=ROOT)

    def test_foreign_workspace_untouched(self):
        (self.root / 'important').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'empty'):
            prepare(self.root)
        self.assertEqual((self.root / 'important').read_text(), 'keep')

    def test_configure_uses_selected_bucket_and_preserves_existing_config(self):
        import sys
        path = self.root / 'config.json'
        command = [sys.executable, str(ROOT / 'apps/be_worker/worker.py'), 'configure',
                   '--output', str(path), '--s3-bucket', 'synthetic-team-bucket',
                   '--s3-region', 'ap-northeast-2']
        result = subprocess.run(command, input='https://example.invalid\n', text=True,
                                capture_output=True, check=True)
        self.assertIn('"api_calls": 0', result.stdout)
        self.assertEqual(json.loads(path.read_text())['s3_bucket'], 'synthetic-team-bucket')
        original = path.read_bytes()
        result = subprocess.run(command, input='https://other.invalid\n', text=True,
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), original)

    def fixture_repo(self):
        repo, weights = self.root / 'repo', self.root / 'private-weights'
        weights.mkdir()
        for name in RUNTIME_FILES:
            target = repo / 'apps/runpod_worker' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / 'apps/runpod_worker' / name).read_bytes())
        model = weights / 'synthetic.pt'
        model.write_bytes(b'synthetic file, never loaded')
        (repo / 'models').mkdir()
        (repo / 'models/expected.json').write_text(json.dumps({'files': {'synthetic.pt': record(model)}}))
        return repo, weights

    def test_package_is_self_contained_and_manifest_matches(self):
        repo, weights = self.fixture_repo()
        archive = build(weights, self.root / 'release', repo)
        with zipfile.ZipFile(archive) as zip_file:
            self.assertIsNone(zip_file.testzip())
            manifest = json.loads(zip_file.read('cctv-runpod-a4000/bundle-manifest.json'))
            for name, entry in manifest['files'].items():
                data = zip_file.read('cctv-runpod-a4000/' + name)
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
            self.assertIn('handoff/pipeline.py', manifest['files'])
        subprocess.run(['python3', 'verify_bundle.py'], cwd=archive.parent / 'cctv-runpod-a4000',
                       check=True, capture_output=True)
        with self.assertRaises(FileExistsError):
            build(weights, self.root / 'release', repo)

    def test_bad_weights_do_not_create_output(self):
        repo, weights = self.fixture_repo()
        (weights / 'synthetic.pt').write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            build(weights, self.root / 'release', repo)
        self.assertFalse((self.root / 'release').exists())


if __name__ == '__main__':
    unittest.main()
