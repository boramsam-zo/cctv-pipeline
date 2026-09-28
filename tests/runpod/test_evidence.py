"""Evidence timing, encoding and storage tests; synthetic frames only, no models."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'apps/runpod_worker'))
import evidence
import storage


def fixture(root):
    files = {}
    for name in ['result.json','vlm_handoffs.json','runtime.json','evidence.json','evidence/event_000/clip.mp4']:
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{}' if name.endswith('json') else b'synthetic-media')
        files[name] = evidence.file_record(path)
    (root/'manifest.json').write_text(json.dumps({'files':files,'input_sha256':'0'*64}))


class FakeS3:
    def __init__(self, fail_at=None, corrupt=False):
        self.objects, self.calls = {}, []
        self.fail_at, self.corrupt = fail_at, corrupt
    def put_object(self, **args):
        if len(self.calls) == self.fail_at:
            raise RuntimeError('simulated upload interruption')
        self.calls.append(args['Key'])
        self.objects[args['Key']] = args
    def head_object(self, **args):
        record = self.objects[args['Key']]
        return {'ContentLength':len(record['Body']) + int(self.corrupt), 'Metadata':record['Metadata']}


class EvidenceTests(unittest.TestCase):
    def test_edge_context_and_rounding(self):
        records = evidence.plan([dict(event_id='event_000',candidate_time_s=3,yolo_objects=[])],25,215)
        self.assertEqual((records[0]['source_start_s'],records[0]['source_end_s']), (0,6))
        self.assertEqual(records[0]['representative_frame'],75)
        late = evidence.plan([dict(event_id='event_000',candidate_time_s=8,yolo_objects=[])],25,215)[0]
        self.assertTrue(late['post_context_truncated'])
        self.assertEqual(late['end_frame_exclusive'],215)
    def test_invalid_and_duplicate_events_rejected(self):
        for event in [dict(event_id='../x',candidate_time_s=1,yolo_objects=[]),
                      dict(event_id='event_000',candidate_time_s=float('nan'),yolo_objects=[])]:
            with self.assertRaises(ValueError): evidence.plan([event],25,215)
        event = dict(event_id='event_000',candidate_time_s=1,yolo_objects=[])
        with self.assertRaises(ValueError): evidence.plan([event,event],25,215)
    def test_cfr_origin_allowed_vfr_rejected(self):
        frames = [{'best_effort_timestamp_time':10+i/25} for i in range(10)]
        self.assertEqual(evidence.verify_timeline(frames,25,10),10)
        frames[5]['best_effort_timestamp_time'] += .02
        with self.assertRaises(ValueError): evidence.verify_timeline(frames,25,10)
    def test_missing_pts_rejected(self):
        with self.assertRaises((KeyError,TypeError,ValueError)):
            evidence.verify_timeline([{},{}],25,2)
    def test_empty_candidates(self):
        self.assertEqual(evidence.plan([],25,215),[])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        fixture(self.root)
        self.config = dict(bucket='test-bucket',region='ap-northeast-2',prefix='cctv-evidence')
    def tearDown(self): self.temp.cleanup()
    def test_manifest_last_and_private_checksum_upload(self):
        client = FakeS3()
        receipt = storage.publish(self.root,'a'*32,self.config,client)
        self.assertTrue(receipt['s3_uploaded'])
        self.assertTrue(client.calls[-1].endswith('/manifest.json'))
        for args in client.objects.values():
            self.assertNotIn('ACL',args)
            self.assertEqual(args['ServerSideEncryption'],'AES256')
            self.assertIn('ChecksumSHA256',args)
        remote = json.loads(client.objects[client.calls[-1]]['Body'])
        self.assertTrue(remote['files']['evidence.json']['key'].endswith('/evidence.json'))
    def test_failure_no_completion_marker_and_retry_no_models(self):
        client = FakeS3(fail_at=2)
        with self.assertRaises(RuntimeError): storage.publish(self.root,'a'*32,self.config,client)
        self.assertFalse(any(k.endswith('/manifest.json') for k in client.calls))
        self.assertFalse((self.root/'s3_receipt.json').exists())
        client.fail_at = None
        storage.publish(self.root,'a'*32,self.config,client)
        self.assertTrue(client.calls[-1].endswith('/manifest.json'))
    def test_corrupt_remote_size_rejected(self):
        with self.assertRaises(ValueError): storage.publish(self.root,'a'*32,self.config,FakeS3(corrupt=True))
        self.assertFalse((self.root/'s3_receipt.json').exists())
    def test_corrupt_local_and_traversal_rejected(self):
        (self.root/'result.json').write_text('changed')
        with self.assertRaises(ValueError): storage.verified_files(self.root)
        fixture(self.root)
        m=json.loads((self.root/'manifest.json').read_text()); m['files']['../outside']={}
        (self.root/'manifest.json').write_text(json.dumps(m))
        with self.assertRaises(ValueError): storage.verified_files(self.root)
    def test_s3_config_never_silently_falls_back(self):
        with patch.dict(os.environ,{'EVIDENCE_STORAGE':'s3'},clear=True):
            with self.assertRaises(ValueError): storage.settings()


@unittest.skipUnless(importlib.util.find_spec('av'), 'PyAV not available in this test interpreter')
class EncodingTests(unittest.TestCase):
    def test_actual_clip_frame_count_and_representative_pixels(self):
        import av
        import numpy as np
        from fractions import Fraction
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); video=root/'input.mp4'; output=root/'out'; output.mkdir()
            with av.open(str(video),'w') as container:
                stream=container.add_stream('libx264',rate=25)
                stream.width,stream.height,stream.pix_fmt=64,48,'yuv420p'
                for i in range(50):
                    frame=av.VideoFrame.from_ndarray(np.full((48,64,3),i*4,dtype=np.uint8),format='rgb24')
                    frame.pts,frame.time_base=i,Fraction(1,25)
                    for packet in stream.encode(frame): container.mux(packet)
                for packet in stream.encode(): container.mux(packet)
            (output/'vlm_handoffs.json').write_text(json.dumps([
                dict(event_id='event_000',candidate_time_s=1,yolo_objects=[])]))
            result=evidence.create(video,output,{'fps':25,'decoded_frames':50},.4,.4)
            event=result['events'][0]
            self.assertEqual(event['clip_frame_count'],20)
            self.assertAlmostEqual(event['source_start_s'],.6)
            with av.open(str(output/event['frame'])) as image:
                pixels=next(image.decode(video=0)).to_ndarray(format='rgb24')
                self.assertLess(abs(float(pixels.mean())-100),5)
            with av.open(str(output/event['clip'])) as clip:
                frames=list(clip.decode(video=0))
                self.assertEqual(len(frames),20)
                self.assertLess(abs(float(frames[0].to_ndarray(format='rgb24').mean())-60),7)
            (output/'result.json').write_text('{}')
            (output/'runtime.json').write_text(json.dumps({'video_check':{'fps':25,'decoded_frames':50}}))
            evidence.write_manifest(output, evidence.sha256(video))
            regenerated=root/'regenerated'
            subprocess.run([sys.executable,str(Path(evidence.__file__)), '--video',str(video),
                '--input-results',str(output),'--output',str(regenerated)],check=True,capture_output=True)
            storage.verified_files(regenerated)
            self.assertTrue(json.loads((regenerated/'runtime.json').read_text())['evidence_media_created'])
            # No-candidate output is an explicit empty evidence list, not an encode failure.
            (output/'vlm_handoffs.json').write_text('[]')
            self.assertEqual(evidence.create(video,output,{'fps':25,'decoded_frames':50})['events'],[])

if __name__ == '__main__': unittest.main()
