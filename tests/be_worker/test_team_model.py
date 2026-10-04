"""Model file admission checks with tiny files; no weight/model execution."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from apps.be_worker import team_model
from apps.be_worker.common import WorkerError


class ModelFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.model=self.root/'model';self.model.mkdir()
        self.raw=b'provided-weight-fixture';(self.model/'weights').write_bytes(self.raw)
        self.manifest=self.root/'manifest.json'
        self.body=json.dumps({'files':{'weights':{'bytes':len(self.raw),
                         'sha256':hashlib.sha256(self.raw).hexdigest()}}}).encode()
        self.manifest.write_bytes(self.body);(self.model/'model-inventory.json').write_bytes(self.body)
        self.patcher=patch.object(team_model,'MANIFEST',self.manifest);self.patcher.start();self.addCleanup(self.patcher.stop)
        team_model._verify.cache_clear();self.addCleanup(team_model._verify.cache_clear)

    def test_empty_and_relative_path_rejected_before_read(self):
        for path in ('',None,'model'):
            with self.assertRaisesRegex(WorkerError,'not_prepared'):team_model.preflight(path)

    def test_verified_files_reuse_hash_receipt_only_when_stats_match(self):
        self.assertTrue(team_model.preflight(str(self.model)))
        self.assertTrue(team_model.preflight(str(self.model)))
        self.assertEqual(team_model._verify.cache_info().hits,1)

    def test_changed_weights_same_length_rejected_after_cached_pass(self):
        team_model.preflight(str(self.model))
        (self.model/'weights').write_bytes(b'x'*len(self.raw))
        with self.assertRaisesRegex(WorkerError,'identity_changed'):team_model.preflight(str(self.model))

    def test_different_manifest_copy_rejected(self):
        (self.model/'model-inventory.json').write_bytes(b'{}')
        with self.assertRaisesRegex(WorkerError,'inventory_changed'):team_model.preflight(str(self.model))

    def test_symlink_weight_rejected(self):
        (self.model/'weights').unlink();(self.root/'outside').write_bytes(self.raw)
        (self.model/'weights').symlink_to(self.root/'outside')
        with self.assertRaisesRegex(WorkerError,'symlink_invalid'):team_model.preflight(str(self.model))


if __name__=='__main__':unittest.main()
