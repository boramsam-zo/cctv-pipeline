"""Small synthetic checkpoint/S3/MLflow doubles; no models or external calls."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from apps.be_worker.common import WorkerError
from apps.be_worker.model_catalog import contract_for_version
from scripts import register_mlops_models as registration
from tests.be_worker.test_mlflow_tracking import FakeClient


class S3Error(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}
        super().__init__('unsafe credential-like error must not be printed')


class FakeS3:
    def __init__(self, contract, *, exists=True):
        self.expected = contract
        self.head = self.metadata() if exists else None
        self.head_calls = []
        self.put_calls = []
        self.public = False
        self.fail_put_before = self.fail_put_after = False

    def metadata(self):
        return {'ContentLength': self.expected['weights_bytes'],
                'Metadata': {'sha256': self.expected['weights_sha256']},
                'ChecksumSHA256': base64.b64encode(bytes.fromhex(self.expected['weights_sha256'])).decode(),
                'ServerSideEncryption': 'AES256', 'VersionId': 'version-1'}

    def get_public_access_block(self, **kwargs):
        return {'PublicAccessBlockConfiguration': {
            name: not self.public for name in registration.PUBLIC_BLOCK_FIELDS}}

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        if self.head is None:
            raise S3Error('404')
        return deepcopy(self.head)

    def put_object(self, **kwargs):
        self.put_calls.append({key: value for key, value in kwargs.items() if key != 'Body'})
        if self.fail_put_before:
            raise S3Error('AccessDenied')
        self.payload = kwargs['Body'].read()
        assert hashlib.sha256(self.payload).hexdigest() == self.expected['weights_sha256']
        self.head = self.metadata()
        if self.fail_put_after:
            raise TimeoutError('synthetic unknown response with token=hidden')
        return {'VersionId': 'version-1'}


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b'small checkpoint byte fixture; never deserialized'
        self.checkpoint = self.root / 'candidate-epoch-7.pt'
        self.checkpoint.write_bytes(self.data)
        self.r2 = contract_for_version('R2')
        self.r2.update(weights_sha256=hashlib.sha256(self.data).hexdigest(), weights_bytes=len(self.data))
        real_contract = contract_for_version
        patcher = patch.object(registration, 'contract_for_version',
            side_effect=lambda version: deepcopy(self.r2) if version == 'R2' else real_contract(version))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = FakeClient()
        self.client.versions = []
        self.client.registered = set()
        self.s3 = FakeS3(self.r2)
        self.args = SimpleNamespace(tracking_uri='http://localhost:5000',
            bindings_output=self.root / 'private' / 'bindings.json', receipt_dir=self.root / 'receipts',
            s3_bucket='private-models', r2_key='models/r2plus1d_18/' + self.r2['weights_sha256'] + '/candidate-epoch-7.pt',
            r2_version_id=None, r2_checkpoint=self.checkpoint, upload_r2=False)

    def run_registration(self):
        return registration.register_models(self.args, s3_client=self.s3, mlflow_client=self.client)

    def test_existing_verified_object_registers_both_actual_versions(self):
        stat_before = self.checkpoint.stat()
        result = self.run_registration()
        self.assertEqual(result['status'], 'mlops_model_references_registered')
        self.assertEqual(result['models']['B']['registered_model_version'], '1')
        self.assertEqual(result['models']['R2']['registered_model_version'], '2')
        self.assertEqual(self.s3.put_calls, [])
        saved = json.loads(self.args.bindings_output.read_text())
        self.assertEqual(saved['B']['source_uri'], registration.X3D_SOURCE_URI)
        self.assertTrue(saved['R2']['source_uri'].startswith('s3://'))
        self.assertFalse(result['model_loaded'])
        self.assertFalse(result['remote_bytes_downloaded'])
        self.assertEqual(stat_before.st_mtime_ns, self.checkpoint.stat().st_mtime_ns)
        self.assertEqual(self.args.bindings_output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_explicit_upload_is_conditional_encrypted_with_sha_and_no_acl(self):
        self.s3.head = None
        self.args.upload_r2 = True
        result = self.run_registration()
        self.assertTrue(result['r2_upload_attempted'])
        self.assertEqual(len(self.s3.put_calls), 1)
        sent = self.s3.put_calls[0]
        self.assertEqual(sent['IfNoneMatch'], '*')
        self.assertEqual(sent['ServerSideEncryption'], 'AES256')
        self.assertEqual(sent['Metadata']['sha256'], self.r2['weights_sha256'])
        self.assertNotIn('ACL', sent)
        self.assertEqual(self.s3.payload, self.data)

    def test_missing_object_does_not_upload_without_flag(self):
        self.s3.head = None
        with self.assertRaisesRegex(WorkerError, 'upload_not_requested'):
            self.run_registration()
        self.assertEqual(self.s3.put_calls, [])
        self.assertEqual(self.client.created_versions, 0)

    def test_existing_mismatched_object_is_never_overwritten(self):
        self.args.upload_r2 = True
        self.s3.head['Metadata']['sha256'] = 'f' * 64
        with self.assertRaisesRegex(WorkerError, 'metadata_mismatch'):
            self.run_registration()
        self.assertEqual(self.s3.put_calls, [])
        self.assertEqual(self.client.created_versions, 0)

    def test_local_hash_mismatch_stops_before_s3_and_registry(self):
        self.checkpoint.write_bytes(b'x' * len(self.data))
        with self.assertRaisesRegex(WorkerError, 'checkpoint_sha256_mismatch'):
            self.run_registration()
        self.assertEqual(self.s3.head_calls, [])
        self.assertEqual(self.client.created_versions, 0)

    def test_uncertain_put_is_reconciled_by_head_without_second_upload(self):
        self.args.upload_r2 = True
        self.s3.head = None
        self.s3.fail_put_after = True
        result = self.run_registration()
        self.assertEqual(result['status'], 'mlops_model_references_registered')
        self.run_registration()
        self.assertEqual(len(self.s3.put_calls), 1)

    def test_unresolved_put_is_not_repeated(self):
        self.args.upload_r2 = True
        self.s3.head = None
        self.s3.fail_put_before = True
        with self.assertRaisesRegex(WorkerError, 'upload_outcome_unknown'):
            self.run_registration()
        self.s3.fail_put_before = False
        with self.assertRaisesRegex(WorkerError, 'upload_outcome_unknown'):
            self.run_registration()
        self.assertEqual(len(self.s3.put_calls), 1)

    def test_specified_version_id_is_verified_and_never_created(self):
        self.args.r2_version_id = 'version-1'
        self.run_registration()
        self.assertEqual(self.s3.head_calls[0]['VersionId'], 'version-1')
        self.s3.head = None
        self.args.upload_r2 = True
        with self.assertRaisesRegex(WorkerError, 'requested_version_missing'):
            self.run_registration()
        self.assertEqual(self.s3.put_calls, [])

    def test_private_bucket_and_content_addressed_key_are_required(self):
        self.s3.public = True
        with self.assertRaisesRegex(WorkerError, 'private_bucket_unverified'):
            self.run_registration()
        self.assertEqual(self.client.created_versions, 0)
        self.s3.public = False
        self.args.r2_key = 'results/overwrite.pt'
        with self.assertRaisesRegex(WorkerError, 'not_content_addressed'):
            self.run_registration()

    def test_identical_bindings_reused_and_changed_bindings_preserved(self):
        self.run_registration()
        before = self.args.bindings_output.read_bytes()
        mtime = self.args.bindings_output.stat().st_mtime_ns
        result = self.run_registration()
        self.assertFalse(result['bindings_written'])
        self.assertEqual(self.client.created_versions, 2)
        self.assertEqual(self.args.bindings_output.stat().st_mtime_ns, mtime)
        modified = json.loads(before)
        modified['B']['weights_sha256'] = 'f' * 64
        self.args.bindings_output.write_text(json.dumps(modified))
        changed = self.args.bindings_output.read_bytes()
        with self.assertRaisesRegex(WorkerError, 'existing_bindings_preserved'):
            self.run_registration()
        self.assertEqual(self.args.bindings_output.read_bytes(), changed)

    def test_metadata_head_is_not_claimed_as_independent_remote_byte_verification(self):
        self.s3.head.pop('ChecksumSHA256')
        result = self.run_registration()
        self.assertFalse(result['remote_bytes_downloaded'])
        receipt = json.loads(next(self.args.receipt_dir.glob('s3-*.json')).read_text())
        self.assertTrue(receipt['sha256_metadata_matches'])
        self.assertFalse(receipt['s3_checksum_matches'])


if __name__ == '__main__':
    unittest.main()
