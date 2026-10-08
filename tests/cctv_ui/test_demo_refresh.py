"""Polling must not repaint idle video/report pages; saved changes still do."""
from copy import deepcopy
import unittest

from apps.cctv_ui.control_data import demo_refresh_signature


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.status = {'active': True, 'remaining_jobs': 4, 'server_time_epoch': 100}
        self.jobs = [{'worker_job_id': 'a', 'stage': 'waiting', 'queue_updated_at': 'now',
                      'flow': {'status': 'waiting', 'elapsed_seconds': 3}, 'reports': []},
                     {'worker_job_id': 'b', 'stage': 'completed'}]
        self.groups = [{'selection_id': 's', 'jobs': deepcopy(self.jobs)}]
        self.worker = {'age_seconds': 2, 'payload': {'stage': 'idle'}}

    def signature(self):
        return demo_refresh_signature(self.status, self.jobs, self.groups, self.worker)

    def test_poll_clocks_and_response_order_do_not_repaint(self):
        before = self.signature()
        self.status['server_time_epoch'] += 5
        self.worker['age_seconds'] += 5
        for job in (self.jobs[0], self.groups[0]['jobs'][0]):
            job['queue_updated_at'] = 'later'
            job['flow']['elapsed_seconds'] += 5
        self.jobs.reverse()
        self.groups[0]['jobs'].reverse()
        self.assertEqual(before, self.signature())

    def test_report_storage_stage_error_and_limits_trigger_refresh(self):
        mutations = [lambda: self.jobs[0].update(stage='reporting'),
                     lambda: self.jobs[0].update(error_code='provider_failed'),
                     lambda: self.jobs[0]['reports'].append({'report_id': 'r', 'state': {'status': 'report_stored', 'result': 'saved report'}}),
                     lambda: self.status.update(remaining_jobs=3),
                     lambda: self.status.update(active=False),
                     lambda: self.worker.update(age_seconds=181)]
        for mutate in mutations:
            self.setUp()
            before = self.signature()
            mutate()
            self.assertNotEqual(before, self.signature())

    def test_fingerprint_does_not_mutate_stored_results(self):
        before = deepcopy((self.status, self.jobs, self.groups, self.worker))
        self.signature()
        self.assertEqual(before, (self.status, self.jobs, self.groups, self.worker))


if __name__ == '__main__':
    unittest.main()
