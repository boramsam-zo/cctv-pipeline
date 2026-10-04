"""Session-only replay boundaries: timing, identity, no record mutation."""
from copy import deepcopy
import unittest

from apps.cctv_ui import control_demo as demo
from apps.cctv_ui.control_data import load_saved_snapshot


class DemoStateTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = load_saved_snapshot()
        self.before = deepcopy(self.snapshot)
        self.targets = demo.candidates(self.snapshot)
        self.target = self.targets[0]

    def tearDown(self):
        self.assertEqual(self.snapshot, self.before)

    def test_two_second_boundary_and_double_click_do_not_reschedule(self):
        state = demo.schedule(None, self.target, 'saved', now=100)
        self.assertEqual(demo.advance(state, now=101.999)['status'], 'pending')
        self.assertEqual(demo.schedule(state, self.targets[1], 'saved', now=101), state)
        arrived = demo.advance(state, now=102)
        self.assertEqual(arrived['status'], 'unread')
        self.assertEqual(arrived['arrived_at'], 102)
        self.assertEqual(arrived['target'], self.target)
        self.assertEqual(demo.advance(arrived, now=900), arrived)

    def test_read_and_replay_only_replace_one_session_alert(self):
        pending = demo.schedule(None, self.target, 'saved', now=100)
        self.assertEqual(demo.acknowledge(pending, now=101), pending)
        arrived = demo.advance(pending, now=102)
        read = demo.acknowledge(arrived, now=103)
        self.assertEqual(read['status'], 'read')
        self.assertEqual(demo.acknowledge(read, now=104), read)
        replay = demo.schedule(read, self.targets[1], 'saved', now=105)
        self.assertEqual(replay['status'], 'pending')
        self.assertEqual(replay['due_at'], 107)
        self.assertIsNone(replay['arrived_at'])
        self.assertIsNone(replay['read_at'])

    def test_only_saved_identity_checked_report_targets_are_offered(self):
        self.assertEqual(len(self.targets), 5)
        self.assertNotIn('36545d0d2927430d9231c0c880ba4ef8', [t['job_id'] for t in self.targets])
        crossed = deepcopy(self.snapshot)
        job = next(j for j in crossed['jobs'] if j['worker_job_id'] == self.target['job_id'])
        report = next(r for r in job['reports'] if r['report_id'] == self.target['report_id'])
        report['state']['result']['worker_job_id'] = 'different-job'
        self.assertFalse(demo.target_available(crossed, self.target))

    def test_alert_marker_never_crosses_source_or_job(self):
        state = demo.advance(demo.schedule(None, self.target, 'saved', now=100), now=102)
        self.assertEqual(demo.card_notice(state, self.target['job_id'], 'saved'), '시연 알림 · 새 알림')
        self.assertIsNone(demo.card_notice(state, self.targets[1]['job_id'], 'saved'))
        self.assertIsNone(demo.card_notice(state, self.target['job_id'], 'api:other'))
        self.assertEqual(demo.card_notice(demo.acknowledge(state), self.target['job_id'], 'saved'), '시연 알림 · 읽음')

    def test_removed_report_cannot_be_opened_as_another_result(self):
        changed = deepcopy(self.snapshot)
        job = next(j for j in changed['jobs'] if j['worker_job_id'] == self.target['job_id'])
        job['reports'] = []
        self.assertFalse(demo.target_available(changed, self.target))
        self.assertIsNone(demo.advance(None, now=999))


if __name__ == '__main__':
    unittest.main()
