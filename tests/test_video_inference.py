"""Tiny generated fixtures only; never classify or relabel a real CCTV file."""
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import av
import numpy as np
from boram3_cctv.video_inference import InferenceError, WindowBuffer, sequential_windows, summarize_logged_losses, fixed_saved_losses
from boram3_cctv import preprocessing as pp


def frame(t):
    return {'actual_seconds':t,'pixels':np.zeros((4,4,3),dtype=np.uint8),'pts':round(t*24),'pts_time_base':'1/24'}


class WindowTests(unittest.TestCase):
    def test_future_frame_not_added_to_decision(self):
        b=WindowBuffer(2,1)
        for i in range(48):
            self.assertEqual(list(b.advance(i/24)),[])
            b.push(frame(i/24))
        window=list(b.advance(2))[0]
        self.assertEqual(window['status'],'ready')
        self.assertEqual(len(window['frames']),16)
        self.assertTrue(all(0<=f['actual_seconds']<2 for f in window['frames']))
        self.assertEqual(window['decision_source_time_sec'],2)
        self.assertEqual([f['actual_seconds'] for f in window['frames']],sorted({f['actual_seconds'] for f in window['frames']}))
        b.push(frame(2))

    def test_overlapping_windows_reuse_buffered_pixels(self):
        b=WindowBuffer(2,1)
        outputs=[]
        for i in range(73):
            outputs.extend(b.advance(i/24))
            b.push(frame(i/24))
        self.assertEqual([(w['start_seconds'],w['end_seconds']) for w in outputs],[(0,2),(1,3)])
        self.assertLessEqual(b.peak_frames,49)
        self.assertTrue(all(w['status']=='ready' for w in outputs))

    def test_new_window_has_new_pixels_and_keeps_previous_array(self):
        # Time-coded pixels reveal accidental reuse even when timestamps look right.
        b=WindowBuffer(2,1)
        outputs=[]
        snapshots=[]
        for i in range(73):
            for window in b.advance(i/24):
                outputs.append(window)
                snapshots.append(np.stack([r['pixels'] for r in window['frames']]))
            record=frame(i/24)
            record['pixels'].fill(i)
            b.push(record)
        self.assertEqual(len(outputs),2)
        self.assertFalse(np.array_equal(snapshots[0],snapshots[1]))
        for window,snapshot in zip(outputs,snapshots):
            np.testing.assert_array_equal(snapshot,np.stack([r['pixels'] for r in window['frames']]))
            for record,pixels in zip(window['frames'],snapshot):
                self.assertTrue(np.all(pixels==record['pts']))
        first={r['pts']:r['pixels'] for r in outputs[0]['frames']}
        shared=[r for r in outputs[1]['frames'] if r['pts'] in first]
        self.assertEqual(len(shared),8)
        for r in shared:
            np.testing.assert_array_equal(first[r['pts']],r['pixels'])

    def test_short_video_is_warmup_without_normal_prediction(self):
        b=WindowBuffer(2,1)
        b.push(frame(0))
        self.assertEqual(list(b.advance(1)),[])

    def test_gap_and_duplicate_selection_not_normal(self):
        b=WindowBuffer(2,1)
        b.push(frame(0))
        w=list(b.advance(2))[0]
        self.assertEqual(w['status'],'insufficient_frames_or_time_gap')
        self.assertEqual(w['frames'],[])

    def test_invalid_or_backward_timestamp(self):
        for t in [float('nan'),-1]:
            with self.assertRaises(InferenceError):WindowBuffer(2,1).push(frame(t) if t==-1 else {'actual_seconds':t})
        b=WindowBuffer(2,1);b.push(frame(.5))
        with self.assertRaises(InferenceError):b.push(frame(.5))

    def test_future_push_and_buffer_limit(self):
        with self.assertRaises(InferenceError):WindowBuffer(2,1).push(frame(2))
        b=WindowBuffer(2,1,count=2,max_frames=2)
        b.push(frame(0));b.push(frame(.1))
        with self.assertRaisesRegex(InferenceError,'capacity'):b.push(frame(.2))


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=self.root/'generated_fixture.mp4'
        with av.open(str(self.path),'w') as c:
            stream=c.add_stream('mpeg4',rate=24);stream.width=32;stream.height=32;stream.pix_fmt='yuv420p'
            for i in range(60):
                f=av.VideoFrame.from_ndarray(np.full((32,32,3),i,dtype=np.uint8),format='rgb24')
                f.pts=i;f.time_base=Fraction(1,24)
                for packet in stream.encode(f):c.mux(packet)
            for packet in stream.encode():c.mux(packet)

    def test_decode_once_and_known_time_mapping(self):
        stats={}
        windows=list(sequential_windows(self.path,2,1,2.5,stats,time.perf_counter()+10))
        self.assertEqual(len(windows),1)
        self.assertEqual(stats['stream_open_count'],1)
        self.assertEqual(stats['preprocessed_frames'],60)
        self.assertEqual(stats['decoded_frames'],60)
        self.assertLessEqual(stats['buffer_peak_frames'],49)
        self.assertAlmostEqual(stats['observed_coverage_end_seconds'],2.5)
        for f in windows[0]['frames']:
            self.assertAlmostEqual(f['actual_seconds'],f['pts']*float(Fraction(f['pts_time_base'])))

    def test_missing_video(self):
        with self.assertRaisesRegex(InferenceError,'video_file_missing'):
            list(sequential_windows(self.root/'missing.mp4',2,1,2,{},time.perf_counter()+10))

    def test_nearest_pts_matches_existing_preprocessor(self):
        old,_=pp.sample_frames(self.path,0,2,16)
        new=list(sequential_windows(self.path,2,1,2.5,{},time.perf_counter()+10))[0]['frames']
        self.assertEqual([r['actual_seconds'] for r in old],[r['actual_seconds'] for r in new])

    def test_existing_output_survives_direct_worker_retry(self):
        out=self.root/'old';out.mkdir();receipt=out/'result.json';receipt.write_text('{"preserved":true}\n')
        run=subprocess.run([sys.executable,str(ROOT/'scripts/analyze_video.py'),'--video',str(self.path),
                            '--model',str(self.root/'missing.pt'),'--output',str(out),'--window-seconds','2',
                            '--device','cpu','--worker'],capture_output=True,text=True,timeout=15)
        self.assertNotEqual(run.returncode,0)
        self.assertEqual(receipt.read_text(),'{"preserved":true}\n')

    def test_interruption_and_range_cap(self):
        with self.assertRaises(TimeoutError):list(sequential_windows(self.path,2,1,2,{},0))
        with self.assertRaisesRegex(InferenceError,'60_seconds'):
            list(sequential_windows(self.path,2,1,61,{},time.perf_counter()+10))

    def test_missing_model_records_failed_without_predictions(self):
        out=self.root/'output'
        run=subprocess.run([sys.executable,str(ROOT/'scripts/analyze_video.py'),'--video',str(self.path),
                            '--model',str(self.root/'missing.pt'),'--output',str(out),
                            '--window-seconds','2','--device','cpu'],capture_output=True,text=True,timeout=15)
        self.assertEqual(run.returncode,2)
        result=json.loads((out/'result.json').read_text())
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['prediction_count'],0)
        self.assertEqual(result['error'],'model_file_missing')


class LossAggregationTests(unittest.TestCase):
    def test_fixed_loss_and_accuracy_are_not_complements(self):
        rows=[{'truth':1,'logits':[0.,1.]},{'truth':0,'logits':[0.,1.]}]
        result=fixed_saved_losses(rows)
        self.assertAlmostEqual(result['mean_loss'],.81326166,places=6)
        self.assertNotAlmostEqual(result['mean_loss'],.5)
        self.assertEqual(result['denominator'],2)
        self.assertGreater(result['rows'][1]['loss'],result['rows'][0]['loss'])

    def test_unlabelled_window_cannot_get_loss(self):
        with self.assertRaisesRegex(InferenceError,'truth'):
            fixed_saved_losses([{'truth':None,'logits':[0.,1.]}])

    def test_resume_global_steps_without_duplicates(self):
        rows=[{'step':i+1,'loss':float(i+1),'sample_ids':[s]} for i,s in enumerate(['a','b','a','b'])]
        result=summarize_logged_losses([('probe',rows[:1]),('resume',rows[1:])],['a','b'],1)
        self.assertEqual([e['epoch_mean'] for e in result['epochs']],[1.5,3.5])
        self.assertEqual(result['observed_sample_visits'],4)
        with self.assertRaisesRegex(InferenceError,'duplicate'):
            summarize_logged_losses([('probe',rows[:1]),('resume',rows)],['a','b'],1)

    def test_missing_step_not_complete_epoch(self):
        r=summarize_logged_losses([('probe',[{'step':1,'loss':.2,'sample_ids':['a']}])],['a','b'],1)
        self.assertIsNone(r['epochs'][0]['epoch_mean'])
        self.assertFalse(r['epochs'][0]['complete'])

    def test_uneven_batch_means_are_sample_weighted(self):
        rows=[{'step':1,'loss':1.0,'sample_ids':['a','b']},{'step':2,'loss':4.0,'sample_ids':['c']}]
        r=summarize_logged_losses([('one',rows)],['a','b','c'],2)
        self.assertEqual(r['epochs'][0]['epoch_mean'],2.0)
        self.assertEqual(r['epochs'][0]['denominator'],3)


@unittest.skipUnless((ROOT/'artifacts/training-review/limited-real-iid-20260921-01/build_report.py').is_file(),
                     'Local report artifact is not distributed with the repository')
class SavedReportAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import runpy
        cls.audit=staticmethod(runpy.run_path(str(ROOT/'artifacts/training-review/limited-real-iid-20260921-01/build_report.py'))['audit_saved_predictions'])

    def row(self):
        return {'start_seconds':0,'end_seconds':2,'logits':[0.,0.],'accident_score':.5,
                'prediction':0,'frames':[{'actual_seconds':(i+.5)/8} for i in range(16)]}

    def test_tied_logits_use_normal_without_running_model(self):
        result=self.audit([self.row()],{'normal':0,'accident':1})
        self.assertTrue(result['argmax_matches'])

    def test_rejects_reversed_mapping_or_stale_score(self):
        with self.assertRaisesRegex(ValueError,'classes'):
            self.audit([self.row()],{'normal':1,'accident':0})
        row=self.row();row['accident_score']=.9
        with self.assertRaisesRegex(ValueError,'score_or_decision'):
            self.audit([row],{'normal':0,'accident':1})
        row=self.row();row['prediction']=1
        with self.assertRaisesRegex(ValueError,'score_or_decision'):
            self.audit([row],{'normal':0,'accident':1})

    def test_rejects_out_of_order_or_future_saved_frames(self):
        for bad in [0.,2.]:
            row=self.row();row['frames'][-1]['actual_seconds']=bad
            with self.assertRaisesRegex(ValueError,'frame_times'):
                self.audit([row],{'normal':0,'accident':1})


if __name__=='__main__':unittest.main()
