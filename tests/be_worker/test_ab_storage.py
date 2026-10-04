"""Immutable A/B evidence publication using in-memory S3, never a live endpoint."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles
from apps.runpod_worker import storage


ROOT = Path(__file__).resolve().parents[2]
P4 = ROOT / 'artifacts/runpod/image-p4-20261001-01/image-validation-20261001-01'
CONFIG = dict(bucket='synthetic-evidence', region='ap-northeast-2', prefix='cctv-evidence')
JOB = 'a' * 32


class S3Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {'Error': {'Code': code}}


class MemoryS3:
    """Models conditional writes, missing objects, and interrupted publication."""
    def __init__(self):
        self.objects, self.puts, self.reads, self.heads = {}, [], [], []
        self.fail_at = None
        self.unknown_after_put = False
        self.race_body = None

    def put_object(self, **args):
        key = args['Key']
        if len(self.puts) == self.fail_at:
            raise RuntimeError('synthetic upload interruption')
        if self.race_body is not None and key not in self.objects:
            data = args['Body'] if self.race_body == b'IDENTICAL' else self.race_body
            self.objects[key] = dict(args, Body=data,
                                     Metadata={'sha256': hashlib.sha256(data).hexdigest()})
            self.race_body = None
        if args.get('IfNoneMatch') == '*' and key in self.objects:
            raise S3Error('PreconditionFailed')
        self.puts.append(key)
        self.objects[key] = deepcopy(args)
        if self.unknown_after_put:
            self.unknown_after_put = False
            raise TimeoutError('synthetic uncertain write outcome')

    def head_object(self, **args):
        key = args['Key']
        self.heads.append(key)
        if key not in self.objects:
            raise S3Error('NoSuchKey')
        row = self.objects[key]
        return {'ContentLength': len(row['Body']), 'Metadata': deepcopy(row['Metadata'])}

    def get_object(self, **args):
        key = args['Key']
        self.reads.append(key)
        if key not in self.objects:
            raise S3Error('NoSuchKey')
        return {'Body': io.BytesIO(self.objects[key]['Body'])}


class ImmutableObjectTests(unittest.TestCase):
    def put(self, client, data=b'original'):
        return storage._put_v2(client, 'test', 'prefix/asset', 'asset', data,
                               hashlib.sha256(data).hexdigest())

    def test_first_write_and_reuse_read_actual_bytes(self):
        client = MemoryS3()
        self.put(client)
        self.put(client)
        self.assertEqual(client.puts, ['prefix/asset'])
        self.assertEqual(client.reads, ['prefix/asset', 'prefix/asset'])
        self.assertEqual(client.objects['prefix/asset']['IfNoneMatch'], '*')

    def test_conflicting_bytes_with_matching_metadata_are_rejected(self):
        client = MemoryS3()
        self.put(client)
        client.objects['prefix/asset']['Body'] = b'corrupt!'
        before = deepcopy(client.objects)
        with self.assertRaises(ValueError):
            self.put(client)
        self.assertEqual(client.objects, before)
        self.assertEqual(len(client.puts), 1)

    def test_conflicting_metadata_is_rejected(self):
        client = MemoryS3()
        self.put(client)
        client.objects['prefix/asset']['Metadata']['sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            self.put(client)
        self.assertEqual(len(client.puts), 1)

    def test_conditional_write_race_requires_exact_bytes(self):
        for body in (b'IDENTICAL', b'different'):
            with self.subTest(body=body):
                client = MemoryS3()
                client.race_body = body
                if body == b'IDENTICAL':
                    self.put(client)
                else:
                    with self.assertRaises(ValueError):
                        self.put(client)
                self.assertEqual(client.puts, [])

    def test_unknown_write_is_not_automatically_retried(self):
        client = MemoryS3()
        client.unknown_after_put = True
        with self.assertRaises(TimeoutError):
            self.put(client)
        self.assertEqual(len(client.puts), 1)
        self.assertEqual(client.reads, [])
        self.put(client)
        self.assertEqual(len(client.puts), 1)
        self.assertEqual(len(client.reads), 1)

    def test_permission_error_is_not_treated_as_missing(self):
        client = MemoryS3()
        with patch.object(client, 'head_object', side_effect=S3Error('AccessDenied')):
            with self.assertRaises(S3Error):
                self.put(client)
        self.assertEqual(client.puts, [])


class VersionedPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from apps.be_worker import ab_evidence
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.prepared = Path(cls.fixtures.name)
        for version in ('A', 'B'):
            source = P4 / f'input_001-{version}/output'
            result = (source / 'result.json').read_bytes()
            ab_evidence.prepare(source, cls.prepared / version,
                                snapshot=analysis_profiles.profile(version),
                                input_sha256=json.loads(result)['input_sha256'],
                                result_sha256=hashlib.sha256(result).hexdigest())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'output'
        shutil.copytree(self.prepared / 'B', self.root)
        self.client = MemoryS3()

    def publish(self, config=None):
        return storage.publish(self.root, JOB, config or CONFIG, self.client)

    def change_manifest(self, change):
        path = self.root / 'manifest.json'
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def test_all_original_assets_and_identity_are_preserved_manifest_last(self):
        local = storage.verified_files(self.root)
        receipt = self.publish()
        marker = f"{CONFIG['prefix']}/{JOB}/manifest.json"
        self.assertEqual(self.client.puts[-1], marker)
        remote = json.loads(self.client.objects[marker]['Body'])
        self.assertEqual(remote['schema_version'], 'runpod-s3-evidence-v2')
        for field in ('analysis_profile', 'gpu_key', 'input_sha256', 'normalized_result_sha256'):
            self.assertEqual(remote[field], local[field])
        self.assertEqual(set(remote['files']), set(local['files']))
        for name, entry in remote['files'].items():
            self.assertEqual(entry['key'], f"{CONFIG['prefix']}/{JOB}/{name}")
            uploaded = self.client.objects[entry['key']]
            self.assertEqual(uploaded['Body'], (self.root / name).read_bytes())
            self.assertEqual(uploaded['IfNoneMatch'], '*')
            self.assertEqual(uploaded['ServerSideEncryption'], 'AES256')
            self.assertNotIn('ACL', uploaded)
        self.assertEqual(receipt['manifest_sha256'], hashlib.sha256(self.client.objects[marker]['Body']).hexdigest())
        self.assertEqual(receipt['gpu_key'], local['gpu_key'])
        self.assertEqual(len(self.client.reads), len(local['files']) + 1)
        self.assertTrue((self.root / 's3_receipt.json').is_file())

    def test_a_version_also_preserves_unannotated_original_frame(self):
        root = Path(self.temp.name) / 'A'
        shutil.copytree(self.prepared / 'A', root)
        receipt = storage.publish(root, 'b' * 32, CONFIG, self.client)
        self.assertEqual(receipt['analysis_profile']['version'], 'A')
        self.assertTrue(any(key.endswith('.png') for key in self.client.objects))

    def test_repeat_reuses_objects_without_writing_or_loading_models(self):
        first = self.publish()
        puts = list(self.client.puts)
        before = deepcopy(self.client.objects)
        with patch.dict('sys.modules', {'boto3': None, 'torch': None, 'ultralytics': None}):
            second = self.publish()
        self.assertEqual(first, second)
        self.assertEqual(self.client.puts, puts)
        self.assertEqual(self.client.objects, before)

    def test_partial_upload_leaves_no_completion_then_reuses_existing_bytes(self):
        self.client.fail_at = 2
        with self.assertRaises(RuntimeError):
            self.publish()
        marker = f"{CONFIG['prefix']}/{JOB}/manifest.json"
        self.assertNotIn(marker, self.client.objects)
        self.assertFalse((self.root / 's3_receipt.json').exists())
        first_keys = list(self.client.puts)
        self.client.fail_at = None
        self.publish()
        self.assertEqual(self.client.puts[-1], marker)
        for key in first_keys:
            self.assertEqual(self.client.puts.count(key), 1)

    def test_existing_completion_manifest_cannot_be_overwritten(self):
        self.publish()
        marker = f"{CONFIG['prefix']}/{JOB}/manifest.json"
        self.client.objects[marker]['Body'] = b'other manifest'
        self.client.objects[marker]['Metadata']['sha256'] = hashlib.sha256(b'other manifest').hexdigest()
        before = deepcopy(self.client.objects)
        puts = list(self.client.puts)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.client.objects, before)
        self.assertEqual(self.client.puts, puts)

    def test_existing_completion_conflict_stops_before_any_asset_is_uploaded(self):
        marker = f"{CONFIG['prefix']}/{JOB}/manifest.json"
        self.client.objects[marker] = {'Body': b'other version',
                                      'Metadata': {'sha256': hashlib.sha256(b'other version').hexdigest()}}
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.client.puts, [])
        self.assertEqual(self.client.heads, [marker])
        self.assertFalse((self.root / 's3_receipt.json').exists())

    def test_existing_receipt_for_other_job_or_destination_is_preserved(self):
        self.publish()
        before = (self.root / 's3_receipt.json').read_bytes()
        puts, heads = list(self.client.puts), list(self.client.heads)
        for job, config in [('b' * 32, CONFIG),
                            (JOB, dict(CONFIG, bucket='other-bucket')),
                            (JOB, dict(CONFIG, region='us-east-1'))]:
            with self.subTest(job=job, config=config):
                with self.assertRaises(ValueError):
                    storage.publish(self.root, job, config, self.client)
                self.assertEqual(self.client.puts, puts)
                self.assertEqual(self.client.heads, heads)
                self.assertEqual((self.root / 's3_receipt.json').read_bytes(), before)

    def test_identity_receipt_tamper_is_rejected_before_s3(self):
        self.publish()
        receipt = json.loads((self.root / 's3_receipt.json').read_text())
        receipt['gpu_key'] = 'f' * 64
        (self.root / 's3_receipt.json').write_text(json.dumps(receipt))
        heads = list(self.client.heads)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.client.heads, heads)

    def test_version_or_input_or_gpu_identity_mismatch_stops_before_s3(self):
        original = (self.root / 'manifest.json').read_bytes()
        changes = [lambda m: m.update(input_sha256='f' * 64),
                   lambda m: m.update(analysis_profile=analysis_profiles.profile('A')),
                   lambda m: m.update(gpu_key='gpu-v1:' + 'f' * 64)]
        for change in changes:
            with self.subTest(change=change):
                (self.root / 'manifest.json').write_bytes(original)
                self.change_manifest(change)
                with self.assertRaises(ValueError):
                    self.publish()
                self.assertEqual(self.client.heads, [])

    def test_missing_or_extra_file_and_wrong_hash_stop_before_s3(self):
        original = (self.root / 'manifest.json').read_bytes()
        changes = [lambda m: m['files'].pop('evidence.json'),
                   lambda m: m['files'].update({'unexpected.json': {'bytes': 2, 'sha256': 'f' * 64}}),
                   lambda m: m['files']['runtime.json'].update(sha256='f' * 64)]
        for change in changes:
            with self.subTest(change=change):
                (self.root / 'manifest.json').write_bytes(original)
                self.change_manifest(change)
                with self.assertRaises(ValueError):
                    self.publish()
                self.assertEqual(self.client.heads, [])

    def test_noncanonical_paths_and_duplicate_entries_stop_before_s3(self):
        original = (self.root / 'manifest.json').read_bytes()
        for name in ('../outside', '/absolute', 'raw//result.json', './result.json', 'raw\\result.json'):
            with self.subTest(name=name):
                (self.root / 'manifest.json').write_bytes(original)
                self.change_manifest(lambda m: m['files'].update({name: m['files']['result.json']}))
                with self.assertRaises(ValueError):
                    self.publish()
                self.assertEqual(self.client.heads, [])
        text = original.decode().rstrip()
        # A duplicate top-level member must not be silently collapsed by JSON.
        (self.root / 'manifest.json').write_text(text[:-1] + ', "input_sha256":"' + 'f' * 64 + '"}')
        with self.assertRaisesRegex(ValueError, 'duplicate_json_key'):
            self.publish()
        self.assertEqual(self.client.heads, [])

    def test_symlink_asset_is_not_followed(self):
        asset = self.root / 'result.json'
        original = Path(self.temp.name) / 'outside.json'
        asset.rename(original)
        asset.symlink_to(original)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.client.heads, [])

    def test_cross_path_s3_prefix_is_rejected(self):
        for prefix in ('../other', '/other', 'same//other', 'same/./other', 'same/../other', 'unsupported-prefix'):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    self.publish(dict(CONFIG, prefix=prefix))
                self.assertEqual(self.client.heads, [])

    def test_versioned_manifest_cannot_be_downgraded_to_legacy(self):
        self.change_manifest(lambda m: m.update(schema_version='runpod-evidence-v1'))
        with self.assertRaisesRegex(ValueError, 'legacy manifest schema'):
            self.publish()
        self.assertEqual(self.client.heads, [])


if __name__ == '__main__':
    unittest.main()
