"""M3 functional tests with labelled synthetic fixtures, never real predictions."""
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

from boram3_cctv.events import EventError, EventProcessor, MAX_RECORDS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/events.synthetic.json'
UTC = '2026-09-22T00:00:00+00:00'
spec = importlib.util.spec_from_file_location('build_events', ROOT / 'scripts/build_events.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text())
        self.context, self.policy = self.data['context'], self.data['policy']
        self.p = EventProcessor(self.context, self.policy)

    def row(self, number, label=1, status='predicted'):
        row = deepcopy(self.data['rows'][0])
        row.update(window_id=f'window_{number:03d}', prediction_id=f'prediction_{number:03d}',
                   start_seconds=float(number-1), end_seconds=float(number+1),
                   decision_source_time_sec=float(number+1), status=status)
        if status == 'predicted':
            row.update(prediction=label, logits=[-1.0,1.0] if label else [1.0,-1.0],
                       accident_score=1/(1+math.exp(-2 if label else 2)))
        else:
            for key in ('prediction_id','prediction','logits','accident_score'):
                row.pop(key)
            row['frame_count'] = 0
        return row

    def add(self, row, processor=None):
        return (processor or self.p).consume(row, emitted_at_utc=UTC,
                           elapsed_wall_sec=row['decision_source_time_sec'])

    def finish(self, reason='source_ended'):
        return self.p.finish(reason=reason, emitted_at_utc=UTC, elapsed_wall_sec=100)

    def assert_unchanged_failure(self, row, reason):
        before = self.p.snapshot()
        with self.assertRaisesRegex(EventError, reason):
            self.add(row)
        self.assertEqual(self.p.snapshot(), before)

    def test_duplicate_does_not_open_or_increment(self):
        self.add(self.row(1))
        before = self.p.snapshot()
        for _ in range(3):
            self.assertEqual(self.add(self.row(1))['action'], 'duplicate_ignored')
        self.assertEqual(self.p.snapshot(), before)
        self.assertIsNone(self.p.events[0]['alert_decision_source_time_sec'])
        self.add(self.row(2))
        event = self.p.events[0]
        self.assertEqual((event['evidence_start_seconds'],event['first_suspicion_source_time_sec'],
                          event['alert_decision_source_time_sec']), (0,2,3))
        self.assertEqual(event['alert_emitted_at_utc'], UTC)
        self.assertEqual(event['alert_elapsed_wall_sec'], 3)

    def test_old_duplicate_after_new_rows_is_noop(self):
        for i in (1,2,3): self.add(self.row(i))
        before = self.p.snapshot()
        self.assertEqual(self.add(self.row(1))['action'], 'duplicate_ignored')
        self.assertEqual(self.p.snapshot(), before)

    def test_duplicate_changed_content_rejected_atomically(self):
        self.add(self.row(1))
        self.assert_unchanged_failure(self.row(1,0), 'duplicate_identity_content_conflict')

    def test_same_window_new_prediction_id_rejected(self):
        self.add(self.row(1))
        row=self.row(1);row['prediction_id']='another_id'
        self.assert_unchanged_failure(row, 'window_identity_content_conflict')

    def test_unique_time_reversal_or_equal_time_rejected(self):
        self.add(self.row(3))
        self.assert_unchanged_failure(self.row(2), 'source_time_not_increasing')
        row=self.row(4);row.update(start_seconds=2,end_seconds=4,decision_source_time_sec=4)
        self.assert_unchanged_failure(row, 'source_time_not_increasing')

    def test_saved_argmax_tie_and_near_boundary(self):
        tie=self.row(1,0);tie.update(logits=[0.,0.],accident_score=.5)
        self.add(tie)
        self.assertEqual(self.p.events, [])
        pos=self.row(2);pos.update(logits=[0.,.00001],accident_score=1/(1+math.exp(-.00001)))
        self.add(pos)
        self.assertEqual(self.p.events[0]['positive_count'], 1)

    def test_bad_class_score_and_nonfinite_inputs(self):
        for field,value,reason in [('prediction',0,'saved_class_disagrees'),
                                   ('accident_score',.1,'saved_score_disagrees'),
                                   ('accident_score',float('nan'),'invalid_saved_score'),
                                   ('logits',[0,float('inf')],'invalid_saved_logits'),
                                   ('prediction',True,'invalid_class')]:
            with self.subTest(field=field,value=value):
                row=self.row(1);row[field]=value
                self.assert_unchanged_failure(row,reason)

    def test_invalid_times_and_unclassified_prediction(self):
        for change in ({'start_seconds':-1},{'end_seconds':0},{'decision_source_time_sec':float('nan')},
                       {'end_seconds':True},{'decision_source_time_sec':3}):
            with self.subTest(change=change):
                row=self.row(1);row.update(change)
                self.assert_unchanged_failure(row,'invalid_source_time')
        row=self.row(1,status='analysis_failed');row['prediction']=0
        self.assert_unchanged_failure(row,'unclassified_cannot_have_prediction')

    def test_candidate_cancelled_then_confirmation_starts_again(self):
        for i,label in enumerate([1,0,1,1],1):self.add(self.row(i,label))
        self.assertEqual([e['status'] for e in self.p.events],['cancelled','open'])
        self.assertEqual(self.p.events[1]['positive_prediction_ids'],['prediction_003','prediction_004'])

    def test_release_streak_resets_without_duplicate_increase(self):
        for i,label in enumerate([1,1,0,1,0],1):self.add(self.row(i,label))
        self.add(self.row(5,0))
        self.assertEqual((self.p.events[0]['status'],self.p.events[0]['release_streak']),('open',1))
        self.add(self.row(6,0))
        self.assertEqual(self.p.events[0]['status'],'closed')
        self.assertEqual(self.p.events[0]['positive_count'],3)

    def test_close_and_immediately_rearm_two_nearby_events(self):
        for i,label in enumerate([1,1,0,0,1,1],1):self.add(self.row(i,label))
        self.assertEqual([e['status'] for e in self.p.events],['closed','open'])
        self.assertNotEqual(self.p.events[0]['event_id'],self.p.events[1]['event_id'])
        self.assertEqual(self.p.events[1]['alert_decision_source_time_sec'],7)

    def test_gap_and_failed_window_are_never_normal(self):
        for status in ('analysis_failed','insufficient_frames_or_time_gap','buffer_restarted','incomplete_window'):
            with self.subTest(status=status):
                p=EventProcessor(self.context,self.policy)
                self.add(self.row(1),p);self.add(self.row(2),p)
                self.add(self.row(3,status=status),p)
                self.assertEqual(p.events[0]['status'],'incomplete')
                self.assertEqual(p.unclassified[0]['reason'],status)
                self.assertIsNone(p.unclassified[0]['truth'])
                self.assertEqual(p.events[0]['release_prediction_ids'],[])
                before=p.snapshot();self.add(self.row(3,status=status),p)
                self.assertEqual(p.snapshot(),before)

    def test_implicit_time_gap_interrupts_then_new_candidate(self):
        self.add(self.row(1));self.add(self.row(2));self.add(self.row(6))
        self.assertEqual([e['status'] for e in self.p.events],['incomplete','candidate'])
        self.assertEqual(self.p.unclassified[0]['reason'],'decision_time_gap')
        self.assertEqual(self.p.events[1]['positive_count'],1)

    def test_eof_and_failure_preserve_incomplete_event(self):
        self.add(self.row(1));self.add(self.row(2))
        result=self.finish()
        self.assertEqual(result['events'][0]['status'],'incomplete')
        self.assertEqual(result['events'][0]['last_transition_reason'],'source_ended_before_release')
        self.assertEqual(result['events'][0]['alert_decision_source_time_sec'],3)
        self.assertEqual(result,self.finish())
        self.assert_unchanged_failure(self.row(3),'processor_finished')
        empty=EventProcessor(self.context,self.policy).finish(reason='analysis_failed',emitted_at_utc=UTC,elapsed_wall_sec=0)
        self.assertEqual(empty['finish_reason'],'analysis_failed')
        self.assertIsNone(empty['event_metrics'])

    def test_incomplete_predicted_window_rejected(self):
        row=self.row(1);row['frame_count']=15
        self.assert_unchanged_failure(row,'incomplete_predicted_window')

    def test_policy_requires_explicit_test_values(self):
        for change in ({'purpose':'real'},{'confirm_count':0},{'release_count':True},
                       {'cooldown_seconds':10},{'max_gap_seconds':float('nan')},
                       {'decision_rule':'new_threshold'},{'merge_rule':'merge_everything'}):
            with self.subTest(change=change),self.assertRaises(EventError):
                EventProcessor(self.context,{**self.policy,**change})
        missing=deepcopy(self.policy);del missing['confirm_count']
        with self.assertRaisesRegex(EventError,'explicit_policy_fields'):
            EventProcessor(self.context,missing)

    def test_run_source_isolation_and_real_input_blocked(self):
        self.add(self.row(1))
        other=EventProcessor({**self.context,'run_id':'synthetic_other'},self.policy)
        self.add(self.row(1),other)
        self.assertNotEqual(self.p.events[0]['event_id'],other.events[0]['event_id'])
        row=self.row(2);row['source_video_id']='synthetic_other_source'
        self.assert_unchanged_failure(row,'source_mismatch')
        row=self.row(2);row['model_sha256']='a'*64
        self.assert_unchanged_failure(row,'real_model_predictions_not_enabled')

    def test_wall_and_source_time_are_validated_separately(self):
        before=self.p.snapshot()
        with self.assertRaisesRegex(EventError,'explicit_utc_required'):
            self.p.consume(self.row(1),emitted_at_utc='2026-09-22T00:00:00',elapsed_wall_sec=0)
        self.assertEqual(before,self.p.snapshot())
        self.add(self.row(1))
        with self.assertRaisesRegex(EventError,'invalid_elapsed_time'):
            self.p.consume(self.row(2),emitted_at_utc=UTC,elapsed_wall_sec=1)

    def test_memory_limit_and_snapshot_not_mutable(self):
        for i in range(1,MAX_RECORDS+1):self.add(self.row(i,0))
        self.assert_unchanged_failure(self.row(MAX_RECORDS+1),'unique_window_limit')
        value=self.p.snapshot();value['policy']['confirm_count']=99
        self.assertEqual(self.p.policy['confirm_count'],2)

    def test_replay_reconstructs_logical_events_and_ignores_truth(self):
        def process(rows):
            p=EventProcessor(self.context,self.policy)
            for r in rows:self.add(r,p)
            p.finish(reason='source_ended',emitted_at_utc=UTC,elapsed_wall_sec=100)
            return p.events,p.transitions,p.unclassified
        original=self.data['rows']
        unique=list({r['window_id']:r for r in original}.values())
        self.assertEqual(process(original),process(unique))
        self.assertEqual(process(original),process([{**r,'truth':0} for r in original]))


class EventCliTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'artifacts').mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='.test-events-',dir=ROOT/'artifacts')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_demo_run_and_output_protection(self):
        raw=FIXTURE.read_bytes()
        info=cli.run(FIXTURE,self.root/'demo')
        out=json.loads((self.root/'demo/events.json').read_text())
        self.assertEqual(info['status'],'completed_synthetic_test')
        self.assertEqual(out['unique_windows'],10)
        self.assertEqual(out['unique_predictions'],9)
        self.assertEqual([e['status'] for e in out['events']],['closed','incomplete','incomplete'])
        self.assertEqual(sum(d['action']=='duplicate_ignored' for d in out['delivery_receipts']),2)
        self.assertEqual(sum(e['alert_decision_source_time_sec'] is not None for e in out['events']),2)
        self.assertEqual(info['model_calls'],0)
        self.assertEqual(FIXTURE.read_bytes(),raw)
        self.assertIn('synthetic_test',(self.root/'demo/report.html').read_text())
        before=(self.root/'demo/events.json').read_bytes()
        with self.assertRaisesRegex(EventError,'existing_output_protected'):
            cli.run(FIXTURE,self.root/'demo')
        self.assertEqual((self.root/'demo/events.json').read_bytes(),before)

    def test_failure_after_some_rows_is_preserved(self):
        data=json.loads(FIXTURE.read_text())
        data['rows'][3]['prediction_id']=data['rows'][2]['prediction_id']
        path=self.root/'invalid.json';path.write_text(json.dumps(data))
        info=cli.run(path,self.root/'partial')
        result=json.loads((self.root/'partial/events.json').read_text())
        self.assertEqual(info['status'],'failed')
        self.assertEqual(result['error']['delivery_index'],3)
        self.assertEqual(result['finish_reason'],'analysis_failed')
        self.assertEqual(result['events'][0]['status'],'incomplete')
        self.assertEqual(result['error']['remaining_deliveries'],9)
        report=(self.root/'partial/report.html').read_text()
        self.assertIn('duplicate_identity_content_conflict',report)
        self.assertEqual(report.count('<td>failed_input</td>'),1)
        self.assertEqual(report.count('<td>not_processed_after_failure</td>'),8)

    def test_nonobject_failure_can_still_write_report(self):
        data=json.loads(FIXTURE.read_text());data['rows']=[None,{}]
        path=self.root/'nonobject.json';path.write_text(json.dumps(data))
        info=cli.run(path,self.root/'nonobject')
        self.assertEqual(info['status'],'failed')
        self.assertIn('prediction_object_required',(self.root/'nonobject/report.html').read_text())

    def test_report_uses_explicit_policy_and_shows_prediction_identity(self):
        data=json.loads(FIXTURE.read_text());data['policy'].update(confirm_count=1,release_count=3)
        path=self.root/'policy.json';path.write_text(json.dumps(data))
        cli.run(path,self.root/'policy')
        report=(self.root/'policy/report.html').read_text()
        self.assertIn('사고 예측1회',report)
        self.assertIn('정상 예측3회',report)
        self.assertIn('<td>prediction_001</td>',report)

    def test_missing_bad_and_oversized_inputs_do_not_create_outputs(self):
        for name,value in [('bad.json','{'),('big.json',' '* (128*1024+1))]:
            path=self.root/name;path.write_text(value)
            with self.assertRaises(EventError):cli.run(path,self.root/'out')
            self.assertFalse((self.root/'out').exists())
        with self.assertRaises(EventError):cli.run(self.root/'missing.json',self.root/'out')

    def test_real_purpose_and_too_many_deliveries_blocked(self):
        for change in ({'purpose':'real_saved_predictions'}, {'rows':[{}]*30}):
            data=json.loads(FIXTURE.read_text());data.update(change)
            path=self.root/'blocked.json';path.write_text(json.dumps(data))
            with self.assertRaises(EventError):cli.run(path,self.root/'out')
            self.assertFalse((self.root/'out').exists())


if __name__ == '__main__':
    unittest.main()
