"""Trial orchestration uses actual scope guard and SQL double; providers are spies."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from apps.be_worker import flow_service as flow, trial_limits as trial
from apps.be_worker.common import WorkerError
from test_trial_limits import Connection, row, settings


class TrialFlowTests(unittest.TestCase):
    def setUp(self):
        self.conn = Connection()
        trial.initialize(self.conn, trial.approved_plan())
        self.current = row()
        with self.conn.transaction():
            trial.bind_job(self.conn, self.current, settings(), '2026-10-02')
        self.queue = Mock()
        self.queue.store.connection = self.conn
        self.queue.store.rows.return_value = [self.current]
        self.scenes, self.reports, self.emit = Mock(), Mock(), Mock()

    def process(self):
        flow.process_events(self.queue, self.current, '2026-10-02', self.emit,
                            self.scenes, self.reports, settings=settings())

    def test_all_candidates_checked_before_first_media_or_generation(self):
        self.current['handoff']['scene_configs'].append('scene-event_001.json')
        with self.assertRaisesRegex(WorkerError, 'trial_candidate_limit_or_identity'):
            self.process()
        self.scenes.fetch.assert_not_called()
        self.scenes.describe.assert_not_called()
        self.reports.run.assert_not_called()
        self.emit.assert_not_called()
        self.assertEqual(trial.get(self.conn)['claims'], [])

    def test_changed_settings_stop_before_first_media_or_generation(self):
        before = deepcopy(trial.get(self.conn))
        changed = settings(); changed['scene_model'] = 'gemini-other'
        with self.assertRaisesRegex(WorkerError, 'trial_generation_model_changed'):
            flow.process_events(self.queue, self.current, '2026-10-02', self.emit,
                                self.scenes, self.reports, settings=changed)
        self.scenes.fetch.assert_not_called()
        self.scenes.describe.assert_not_called()
        self.reports.run.assert_not_called()
        self.assertEqual(trial.get(self.conn), before)

    def test_scene_failure_does_not_invoke_report_or_undo_consumption(self):
        def failed_description(*args, **kwargs):
            trial.claim(self.conn, self.current, 'event_000', 'scene', '1'*64, model=trial.MODEL)
            raise TimeoutError('synthetic lost provider response')
        self.scenes.describe.side_effect = failed_description
        with self.assertRaises(TimeoutError): self.process()
        self.reports.run.assert_not_called()
        self.assertEqual(len(trial.get(self.conn)['claims']), 1)
        self.conn = Connection(self.conn.db)
        self.queue.store.connection = self.conn
        with self.assertRaisesRegex(WorkerError, 'trial_attempt_already_claimed_no_retry'):
            self.process()
        self.reports.run.assert_not_called()
        self.assertEqual(len(trial.get(self.conn)['claims']), 1)


if __name__ == '__main__': unittest.main()
