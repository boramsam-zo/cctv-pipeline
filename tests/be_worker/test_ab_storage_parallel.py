"""Bounded publication with synthetic bytes; no artifact/model/network dependency.

The v2 identity-validator boundary is mocked as already successful. Real identity
and media-contract validation have separate ab_evidence/ab_storage tests. These
tests exercise the publisher's unchanged per-byte S3 checks and completion rules.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from apps.runpod_worker import storage


CONFIG = dict(bucket='synthetic-evidence', region='ap-northeast-2', prefix='cctv-evidence')
JOB = 'a' * 32


class S3Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {'Error': {'Code': code}}


class MemoryS3:
    def __init__(self):
        self.objects, self.puts, self.reads, self.heads = {}, [], [], []

    def put_object(self, **args):
        key = args['Key']
        if args.get('IfNoneMatch') == '*' and key in self.objects:
            raise S3Error('PreconditionFailed')
        self.puts.append(key)
        self.objects[key] = deepcopy(args)

    def head_object(self, **args):
        key = args['Key']
        self.heads.append(key)
        if key not in self.objects:
            raise S3Error('NoSuchKey')
        item = self.objects[key]
        return {'ContentLength': len(item['Body']), 'Metadata': deepcopy(item['Metadata'])}


def synthetic_bundle(root, *, no_candidates=False):
    """Not inference output: only tiny data to test the post-validation publisher."""
    root.mkdir()
    status = 'no_candidates' if no_candidates else 'completed'
    events = [] if no_candidates else [{'event_id': 'event_000'}]
    values = {
        'result.json': json.dumps({'status': status, 'event_count': len(events)}).encode(),
        'evidence.json': json.dumps({'status': status, 'events': events}).encode(),
        'runtime.json': b'{"synthetic":true}',
        'raw/manifest.json': b'{"synthetic":true}',
    }
    if not no_candidates:
        values.update({'evidence/event_000/clip.mp4': b'synthetic-clip-bytes',
                       'evidence/event_000/roi.mp4': b'synthetic-roi-bytes',
                       'evidence/event_000/frame.jpg': b'synthetic-frame-bytes'})
    files = {}
    for name, data in values.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    manifest = {'schema_version': 'runpod-evidence-v2', 'files': files,
                'analysis_profile': {'version': 'synthetic'},
                'gpu_key': 'gpu-v1:' + 'b' * 64, 'input_sha256': 'c' * 64,
                'normalized_result_sha256': files['result.json']['sha256']}
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


class LockedMemoryS3(MemoryS3):
    """Only short in-memory operations take the lock; hooks can overlap."""
    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()
        self.before_put = lambda key: None
        self.before_read = lambda key: None
        self.after_read = lambda key: None
        self.corrupt_key = None
        self.request_threads = set()

    def put_object(self, **args):
        self.before_put(args['Key'])
        with self.lock:
            self.request_threads.add(threading.get_ident())
            return super().put_object(**args)

    def head_object(self, **args):
        with self.lock:
            self.request_threads.add(threading.get_ident())
            return super().head_object(**args)

    def get_object(self, **args):
        key = args['Key']
        with self.lock:
            self.request_threads.add(threading.get_ident())
            self.reads.append(key)
            data = self.objects[key]['Body']
            if key == self.corrupt_key:
                data = bytes([data[0] ^ 1]) + data[1:]
        owner = self

        class ObservedBody(io.BytesIO):
            def read(self, size=-1):
                owner.before_read(key)
                value = super().read(size)
                owner.after_read(key)
                return value

        return {'Body': ObservedBody(data)}


class ParallelPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'output'
        self.manifest = synthetic_bundle(self.root)
        validator = patch('apps.be_worker.ab_evidence.validate_local',
                          side_effect=lambda directory: json.loads((Path(directory) / 'manifest.json').read_text()))
        validator.start()
        self.addCleanup(validator.stop)
        self.client = LockedMemoryS3()
        self.prefix = f"{CONFIG['prefix']}/{JOB}/"
        self.marker = self.prefix + 'manifest.json'
        self.names = list(self.manifest['files'])

    def publish(self, **kwargs):
        return storage.publish(self.root, JOB, CONFIG, self.client, **kwargs)

    def assert_incomplete(self):
        self.assertNotIn(self.marker, self.client.objects)
        self.assertFalse((self.root / 's3_receipt.json').exists())

    def test_default_two_file_operations_overlap_but_never_exceed_two(self):
        real_put = storage._put_v2
        first_two = threading.Barrier(2, timeout=5)
        lock = threading.Lock()
        active, peak, started = 0, 0, 0

        def observed(*args, **kwargs):
            nonlocal active, peak, started
            if args[2] == self.marker:
                with lock:
                    self.assertEqual(active, 0)
                return real_put(*args, **kwargs)
            with lock:
                active += 1
                started += 1
                ordinal = started
                peak = max(peak, active)
            try:
                if ordinal <= 2:
                    first_two.wait()
                return real_put(*args, **kwargs)
            finally:
                with lock:
                    active -= 1

        with patch.object(storage, '_put_v2', side_effect=observed):
            self.publish()
        self.assertEqual(peak, 2)
        self.assertEqual(started, len(self.names))
        self.assertEqual(self.client.puts[-1], self.marker)

    def test_all_get_bytes_finish_before_manifest_and_receipt_appear(self):
        held_key = self.prefix + self.names[-1]
        entered, release = threading.Event(), threading.Event()
        verified = set()

        def hold_read(key):
            if key == held_key:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('test did not release final byte verification')

        def observe_read(key):
            with self.client.lock:
                verified.add(key)

        def observe_put(key):
            if key == self.marker:
                self.assertEqual(verified, {self.prefix + name for name in self.names})

        self.client.before_read = hold_read
        self.client.after_read = observe_read
        self.client.before_put = observe_put
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.publish)
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(future.done())
                self.assert_incomplete()
            finally:
                release.set()
            self.assertTrue(future.result(timeout=5)['s3_uploaded'])
        self.assertTrue((self.root / 's3_receipt.json').is_file())

    def test_failure_drains_active_file_then_explicit_resume_preserves_success(self):
        good_key, fail_key = (self.prefix + name for name in self.names[:2])
        reading, release, failure_seen = (threading.Event() for _ in range(3))

        def hold_good(key):
            if key == good_key:
                reading.set()
                if not release.wait(5):
                    raise TimeoutError('test did not release in-flight verification')

        def fail_second(key):
            if key == fail_key:
                if not reading.wait(5):
                    raise TimeoutError('other file never reached byte verification')
                failure_seen.set()
                raise RuntimeError('synthetic failed asset')

        self.client.before_read = hold_good
        self.client.before_put = fail_second
        diagnostics = {}
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.publish, diagnostics=diagnostics)
            try:
                self.assertTrue(failure_seen.wait(5))
                self.assertFalse(future.done(), 'publish must drain running file work')
                self.assert_incomplete()
                self.assertEqual(set(self.client.objects), {good_key})
            finally:
                release.set()
            with self.assertRaisesRegex(RuntimeError, 'synthetic failed asset'):
                future.result(timeout=5)
        self.assert_incomplete()
        self.assertEqual(diagnostics['status'], 'failed')
        self.assertEqual(diagnostics['files'][self.names[0]]['status'], 'succeeded')
        self.assertEqual(diagnostics['files'][self.names[1]]['status'], 'failed')
        self.assertNotIn('running', [item['status'] for item in diagnostics['files'].values()])
        original_good = deepcopy(self.client.objects[good_key])
        self.client.before_put = lambda key: None
        self.client.before_read = lambda key: None
        self.publish()
        self.assertEqual(self.client.objects[good_key], original_good)
        self.assertEqual(self.client.puts.count(good_key), 1)
        self.assertEqual(self.client.puts[-1], self.marker)

    def test_corrupt_remote_bytes_prevent_completion_and_resume_never_overwrites(self):
        self.client.corrupt_key = self.prefix + self.names[1]
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            self.publish()
        self.assert_incomplete()
        original_objects = deepcopy(self.client.objects)
        self.client.corrupt_key = None
        self.publish()
        for key, value in original_objects.items():
            self.assertEqual(self.client.objects[key], value)
            self.assertEqual(self.client.puts.count(key), 1)

    def test_sequential_and_parallel_publications_have_identical_bytes_and_receipts(self):
        serial_diagnostics, parallel_diagnostics = {}, {}
        sequential = self.publish(max_workers=1, diagnostics=serial_diagnostics)
        objects = deepcopy(self.client.objects)
        serial_threads = set(self.client.request_threads)
        self.assertEqual(serial_threads, {threading.get_ident()})
        (self.root / 's3_receipt.json').unlink()
        self.client = LockedMemoryS3()
        parallel = self.publish(max_workers=2, diagnostics=parallel_diagnostics)
        self.assertEqual(sequential, parallel)
        self.assertEqual(objects, self.client.objects)
        for expected_workers, diagnostics in ((1, serial_diagnostics), (2, parallel_diagnostics)):
            self.assertEqual(diagnostics['max_workers'], expected_workers)
            self.assertEqual(diagnostics['status'], 'succeeded')
            self.assertEqual(set(diagnostics['files']), set(self.names))
            self.assertGreaterEqual(diagnostics['total_seconds'], diagnostics['assets_seconds'])
            for name, item in diagnostics['files'].items():
                self.assertEqual(item['bytes'], self.manifest['files'][name]['bytes'])
                self.assertEqual(item['status'], 'succeeded')
                self.assertGreaterEqual(item['elapsed_seconds'], 0)

    def test_legacy_v1_keeps_sequential_upload_with_requested_parallelism(self):
        legacy = Path(self.temp.name) / 'legacy'
        legacy.mkdir()
        files = {}
        for name in ('result.json', 'vlm_handoffs.json', 'runtime.json', 'evidence.json'):
            data = b'{"events":[]}' if name == 'evidence.json' else b'{}'
            (legacy / name).write_bytes(data)
            files[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        (legacy / 'manifest.json').write_text(json.dumps({
            'schema_version': 'runpod-evidence-v1', 'files': files}))
        receipt = storage.publish(legacy, JOB, CONFIG, self.client, max_workers=4)
        self.assertTrue(receipt['s3_uploaded'])
        self.assertEqual(self.client.request_threads, {threading.get_ident()})
        self.assertEqual(self.client.puts, [self.prefix + name for name in files] + [self.marker])
        self.assertEqual(self.client.reads, [])

    def test_explicit_no_candidates_stays_empty_after_parallel_publication(self):
        target = Path(self.temp.name) / 'no-candidates-output'
        synthetic_bundle(target, no_candidates=True)
        storage.publish(target, JOB, CONFIG, self.client)
        saved_index = json.loads(self.client.objects[self.prefix + 'evidence.json']['Body'])
        self.assertEqual(saved_index['status'], 'no_candidates')
        self.assertEqual(saved_index['events'], [])
        self.assertFalse(any(key.endswith(('.mp4', '.png', '.jpg')) for key in self.client.objects))
        self.assertEqual(self.client.puts[-1], self.marker)

    def test_invalid_worker_limits_do_not_touch_s3(self):
        for value in (0, -1, 5, True, 1.5, '2', None):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                self.publish(max_workers=value)
        self.assertEqual(self.client.heads, [])
        self.assertEqual(self.client.puts, [])


if __name__ == '__main__':
    unittest.main()
