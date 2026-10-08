"""Publication copies exclude operational files and reject unsafe inputs offline."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts import prepare_git_publication as publication


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='publication-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'source'
        self.root.mkdir()
        (self.root / 'README.md').write_text('Original project\n')

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def prepare(self):
        return publication.prepare(self.base / 'publication', self.root)

    def test_source_copy_excludes_private_runtime_data_and_preserves_originals(self):
        self.put('apps/demo.py', 'print("hello")\n')
        for name in ('apps/data/video.mp4', 'apps/outputs/result.json', 'apps/private/runtime.env',
                     'apps/.venv/lib.py', 'apps/secret.pt', 'infra/private.tfvars', '.aws/credentials'):
            self.put(name, 'do not include')
        original = (self.root / 'README.md').read_bytes()
        result = self.prepare()
        tree = self.base / 'publication/tree'
        self.assertEqual({p.relative_to(tree).as_posix() for p in tree.rglob('*') if p.is_file()},
                         {'README.md', 'apps/demo.py'})
        self.assertEqual((self.root / 'README.md').read_bytes(), original)
        self.assertEqual((tree / 'README.md').read_bytes(), original)
        self.assertEqual(result['remote_operations'], 0)
        self.assertEqual(result['secret_scan_findings'], 0)
        self.assertFalse(result['source_files_modified'])
        with self.assertRaisesRegex(ValueError, 'existing_output_preserved'):
            self.prepare()

    def test_personal_paths_change_only_the_copy(self):
        user = '/' + 'Users/some-person'
        volume = '/' + 'Volumes/T9'
        original = str(self.root) + '/apps/demo.py ' + user + '/notes ' + volume + '/video.mp4'
        file = self.put('README.md', '# Project overview\n' + original)
        self.prepare()
        copied = (self.base / 'publication/tree/README.md').read_text()
        self.assertEqual(file.read_text(), '# Project overview\n' + original)
        self.assertEqual(copied, '# Project overview\n./apps/demo.py /path/to/user/notes /path/to/data/video.mp4')
        self.assertIn('./apps/demo.py', copied)
        self.assertIn('/path/to/user/notes', copied)
        self.assertIn('/path/to/data/video.mp4', copied)
        self.assertIsNone(publication.PRIVATE_PATH.search(copied))

    def test_secret_rejected_without_returning_its_value(self):
        value = 'ghp_' + 'aBcD1234' * 5
        self.put('apps/sample.py', 'TOKEN = ' + repr(value))
        with self.assertRaises(ValueError) as raised:
            self.prepare()
        self.assertIn('github_token', str(raised.exception))
        self.assertNotIn(value, str(raised.exception))
        self.assertFalse((self.base / 'publication').exists())

    def test_operational_destinations_are_removed_only_from_document_copy(self):
        original = ('i-1234567890abcdef0 https://abcdefghijklm-8000.proxy.runpod.net '
                    'Pod abcdefghijklm ssh 8.8.4.4 http://127.0.0.1:8501 10.0.0.1')
        source = self.put('docs/operations.md', original)
        code = self.put('apps/example.py', '# ' + original)
        self.prepare()
        tree = self.base / 'publication/tree'
        text = (tree / 'docs/operations.md').read_text()
        self.assertEqual(source.read_text(), original)
        self.assertEqual((tree / 'apps/example.py').read_bytes(), code.read_bytes())
        for value in ('i-1234567890abcdef0', 'abcdefghijklm', '8.8.4.4'):
            self.assertNotIn(value, text)
        self.assertIn('YOUR_EC2_INSTANCE_ID', text)
        self.assertIn('YOUR_RUNPOD_ID-8000.proxy.runpod.net', text)
        self.assertIn('203.0.113.10', text)
        self.assertIn('http://127.0.0.1:8501', text)
        self.assertIn('10.0.0.1', text)

    def test_symlink_in_allowlisted_source_is_not_followed(self):
        target = self.put('docs/guide.md', 'original')
        (self.root / 'apps').mkdir()
        (self.root / 'apps/link.py').symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.prepare()

    def test_catalog_code_is_not_anonymized_silently(self):
        user = '/' + 'Users/some-person'
        self.put('apps/demo.py', 'PATH = ' + repr(user))
        self.put('apps/be_worker/mlops-20261004-v1.json',
                 json.dumps({'code_files': {'service/apps/demo.py': 'a' * 64}}))
        with self.assertRaisesRegex(ValueError, 'catalog_source_requires_review'):
            self.prepare()

    def test_negative_url_test_exception_does_not_allow_real_host_or_production_file(self):
        example = 'https://' + 'user:secret@example.com/'
        real = 'https://' + 'user:secret@internal.service/'
        self.assertEqual(publication.secrets(example, 'tests/test_url.py'), [])
        self.assertIn('embedded_basic_auth', publication.secrets(example, 'apps/config.py'))
        self.assertIn('embedded_basic_auth', publication.secrets(real, 'tests/test_url.py'))


if __name__ == '__main__':
    unittest.main()
