"""Demo MP4 limits and identity reuse; only local bytes and fake metadata."""
from copy import deepcopy
import json
from pathlib import Path
from fractions import Fraction
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import demo_access
from apps.be_worker.common import WorkerError


class DemoAccessTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.video=Path(temp.name)/'new-name.mp4'
        self.video.write_bytes(b'\x00\x00\x00\x18ftypisom'+b'x'*20)
        self.policy={'max_upload_bytes':1024,'max_video_seconds':60}
    def probe(self,seconds=5,streams=None):
        return Mock(return_value=NS(stdout=json.dumps({'format':{'duration':seconds},
            'streams':streams if streams is not None else [{'codec_type':'video'}]})))
    def test_header_and_bytes_checked_before_probe(self):
        runner=self.probe()
        with self.assertRaises(WorkerError):
            demo_access.validate_video(self.video,dict(self.policy,max_upload_bytes=3),run=runner)
        self.video.write_bytes(b'not-a-video-file')
        with self.assertRaises(WorkerError):demo_access.validate_video(self.video,self.policy,run=runner)
        runner.assert_not_called()
    def test_duration_audio_only_nan_and_missing_metadata_rejected(self):
        for seconds,streams in ((61,None),(0,None),('NaN',None),('Infinity',None),(5,[{'codec_type':'audio'}])):
            with self.subTest(seconds=seconds),self.assertRaises(WorkerError):
                demo_access.validate_video(self.video,self.policy,run=self.probe(seconds,streams))
        runner=Mock(return_value=NS(stdout='{}'))
        with self.assertRaises(WorkerError):demo_access.validate_video(self.video,self.policy,run=runner)
    def test_valid_video_metadata_uses_local_protocols_timeout_and_no_mutation(self):
        before=self.video.read_bytes();runner=self.probe(5.36)
        self.assertEqual(demo_access.validate_video(self.video,self.policy,run=runner),5.36)
        argv=runner.call_args.args[0]
        self.assertEqual(argv[:3],[sys.executable,'-I','-c'])
        self.assertEqual(argv[3],demo_access._PYAV_METADATA)
        self.assertEqual(argv[4],str(self.video))
        self.assertEqual(runner.call_args.kwargs['timeout'],15)
        self.assertEqual(self.video.read_bytes(),before)
    def test_pyav_probe_uses_stream_duration_fallback_without_decode(self):
        container=Mock()
        container.duration=None
        container.streams=[NS(type='video',duration=150,time_base=Fraction(1,25))]
        context=Mock()
        context.__enter__=Mock(return_value=container)
        context.__exit__=Mock(return_value=False)
        av=NS(open=Mock(return_value=context),time_base=1000000)
        with patch.dict(sys.modules,{'av':av}),patch.object(sys,'argv',['-c',str(self.video)]),\
             patch('builtins.print') as output:
            exec(compile(demo_access._PYAV_METADATA,'<test-metadata>','exec'),{})
        av.open.assert_called_once_with(str(self.video),mode='r',format='mov',
            options={'protocol_whitelist':'file,pipe','enable_drefs':'0'})
        container.decode.assert_not_called()
        self.assertEqual(json.loads(output.call_args.args[0]),
            {'format':{'duration':6.0},'streams':[{'codec_type':'video'}]})
    def test_pyav_probe_uses_longest_declared_stream_not_short_container_value(self):
        container=Mock()
        container.duration=5_000_000
        container.streams=[NS(type='video',duration=2000,time_base=Fraction(1,25))]
        context=Mock();context.__enter__=Mock(return_value=container);context.__exit__=Mock(return_value=False)
        with patch.dict(sys.modules,{'av':NS(open=Mock(return_value=context),time_base=1000000)}),\
             patch.object(sys,'argv',['-c',str(self.video)]),patch('builtins.print') as output:
            exec(compile(demo_access._PYAV_METADATA,'<test-metadata>','exec'),{})
        self.assertEqual(json.loads(output.call_args.args[0])['format']['duration'],80.0)
        container.decode.assert_not_called()
    def test_probe_timeout_fails_closed_without_mutation(self):
        before=self.video.read_bytes()
        runner=Mock(side_effect=subprocess.TimeoutExpired(['python'],15))
        with self.assertRaisesRegex(WorkerError,'demo_video_metadata_invalid'):
            demo_access.validate_video(self.video,self.policy,run=runner)
        self.assertEqual(self.video.read_bytes(),before)
    def test_same_sha_model_and_gpu_contract_returns_view_only_reuse(self):
        sha='d'*64;key='e'*64
        item={'selection_id':'a'*32,'input_sha256':sha,'selection':'B','purpose':'full_report',
            'jobs':[{'gpu_key':key}], 'children':[{'worker_job_id':'b'*32,'task_id':'c'*32}]}
        before=deepcopy(item)
        with patch('apps.be_worker.job_queue.fingerprint',return_value=(sha,32)),\
             patch('apps.be_worker.analysis_profiles.profile',return_value={}),\
             patch('apps.be_worker.analysis_profiles.gpu_identity',return_value={'gpu_key':key}),\
             patch('apps.be_worker.flow_service.selection_views',return_value=[item]):
            result=demo_access.reuse_selection(object(),self.video,'B')
        self.assertTrue(result['result_reused']);self.assertEqual(result['new_gpu_calls'],0)
        self.assertEqual(item,before)
    def test_same_video_other_model_or_contract_is_not_claimed_as_reused(self):
        sha='d'*64;key='e'*64
        for model,saved_key in (('R2',key),('B','f'*64)):
            item={'selection_id':'a'*32,'input_sha256':sha,'selection':model,'purpose':'full_report',
                'jobs':[{'gpu_key':saved_key}],'children':[]}
            with self.subTest(model=model),patch('apps.be_worker.job_queue.fingerprint',return_value=(sha,32)),\
                 patch('apps.be_worker.analysis_profiles.profile',return_value={}),\
                 patch('apps.be_worker.analysis_profiles.gpu_identity',return_value={'gpu_key':key}),\
                 patch('apps.be_worker.flow_service.selection_views',return_value=[item]):
                self.assertIsNone(demo_access.reuse_selection(object(),self.video,'B'))
