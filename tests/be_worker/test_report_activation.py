"""Deterministic read-only plans and explicit grants, using fake IO only."""
from contextlib import nullcontext
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import hour_session as session, report_continuation as policy
from apps.be_worker.common import WorkerError
from scripts import activate_report_continuation as activation
from tests.be_worker import test_report_continuation as fixtures


class ReportActivationTests(unittest.TestCase):
    def setUp(self):
        fixtures.ReportContinuationTests.setUp(self)
        activation.APPLY_INTENT = False
        self.root.chmod(0o700)
        session.marker_path().write_text(json.dumps(session.marker_from_state(self.old)))
        self.store = Mock(connection=self.conn)
        self.store.rows.side_effect = lambda: [deepcopy(self.row)]
        self.store.lock.side_effect = lambda _: nullcontext()
        source = self.marker['source']
        self.parameters = dict(name=self.marker['name'], source_session=self.old['name'],
            worker_job_id=self.wid, event_id=self.event, input_sha256=source['input_sha256'],
            gpu_key=source['gpu_key'], manifest_sha256=source['manifest_sha256'],
            result_sha256=source['result_sha256'])
        for context in (patch.dict(os.environ, RAG_MODEL_PATH=str(self.root)),
                        patch.object(activation.scene, 'load_bundle', return_value=(
                            self.config, {}, {}, {}, source['manifest_sha256'])),
                        patch.object(activation.team_scene, 'prepare', return_value=(
                            b'synthetic', {'request_sha256': 'c' * 64})),
                        patch.object(activation.team_model, 'preflight', return_value=True),
                        patch.object(activation.mlops_jobs, 'binding_for', return_value={})):
            context.start(); self.addCleanup(context.stop)

    def plan(self):
        return activation.inspect_source(self.conn, self.store, **self.parameters)[0]

    def snapshot(self):
        return (deepcopy(self.db.rows), {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()})

    def test_default_plan_is_read_only_and_hash_excludes_time(self):
        before = self.snapshot()
        first = self.plan()
        self.db.time += 19
        second = self.plan()
        self.assertEqual(first, second)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(first['max_new_gpu_calls'], 0)
        self.assertEqual(first['max_new_scene_calls'], 1)
        self.assertFalse(first['hard_wall_clock_timeout'])

    def test_wrong_review_hash_or_changed_source_never_grants(self):
        plan = self.plan(); before = self.snapshot()
        with self.assertRaisesRegex(WorkerError, 'reviewed_plan_changed'):
            activation.apply(self.conn, self.store, self.parameters, 'f' * 64)
        self.assertEqual(self.snapshot(), before)
        (self.bundle / 'clip.mp4').write_bytes(b'changed-after-review')
        with self.assertRaisesRegex(WorkerError, 'reviewed_plan_changed'):
            activation.apply(self.conn, self.store, self.parameters, plan['plan_sha256'])
        self.assertNotIn(self.marker['name'], self.db.rows)
        self.assertFalse(list(self.root.glob('.report-continuation-*')))

    def test_active_work_existing_generation_or_second_approval_rejected(self):
        for attribute in ('busy', 'generated'):
            setattr(self.conn, attribute, True)
            with self.subTest(attribute=attribute), self.assertRaises(Exception):
                self.plan()
            setattr(self.conn, attribute, False)
        previous = dict(deepcopy(self.marker), name='mlops-session-20261005-03')
        self.db.rows[previous['name']] = dict(previous, started_at=-1000., deadline_at=-100.,
            stopped=False, bindings={self.wid: policy.binding(previous)}, claims=[])
        with self.assertRaisesRegex(WorkerError, 'already_approved_no_new_window'):
            self.plan()

    def test_failure_result_or_spool_state_never_becomes_completed(self):
        self.conn.tasks[0]['status'] = 'paused'
        with self.assertRaisesRegex(WorkerError, 'comparison_not_completed'):
            self.plan()
        self.conn.tasks[0]['status'] = 'comparison_completed'
        (self.directory / 'state.json').write_text('{}')
        with self.assertRaisesRegex(WorkerError, 'spool_state_changed'):
            self.plan()

    def admit(self, store, video, day, version, purpose, **kwargs):
        self.assertEqual((video, day, version, purpose),
                         (self.directory / 'input.mp4', None, 'B', 'full_report'))
        with self.conn.transaction():
            session.bind_job(self.conn, self.row, self.settings, None)
        return {'selection_id': 'e' * 32, 'jobs': [{'worker_job_id': self.wid,
                'task_id': self.reviewed['task_id'], 'already_present': True}]}

    def test_apply_preserves_history_and_grants_only_reviewed_task(self):
        self.reviewed = self.plan()
        old = deepcopy(self.db.rows)
        with patch.object(activation.selection_jobs, 'admit', side_effect=self.admit) as admit:
            result = activation.apply(self.conn, self.store, self.parameters, self.reviewed['plan_sha256'])
        self.assertEqual(admit.call_count, 1)
        self.assertEqual({k: self.db.rows[k] for k in old}, old)
        self.assertEqual(session.get(self.conn)['claims'], [])
        self.assertEqual(result['worker_job_id'], self.wid)
        self.assertFalse(result['actual_report_completed'])
        self.assertEqual(len(list(self.root.glob('.report-continuation-*'))), 2)
        with self.assertRaises(Exception):
            activation.apply(self.conn, self.store, self.parameters, self.reviewed['plan_sha256'])

    def test_uncertain_admission_keeps_grant_and_originals_for_inspection(self):
        plan = self.plan(); old = deepcopy(self.db.rows)
        with patch.object(activation.selection_jobs, 'admit', side_effect=RuntimeError('uncertain commit')):
            with self.assertRaisesRegex(RuntimeError, 'uncertain commit'):
                activation.apply(self.conn, self.store, self.parameters, plan['plan_sha256'])
        self.assertEqual({k: self.db.rows[k] for k in old}, old)
        self.assertEqual(session.current_marker(), plan['marker'])
        self.assertEqual(session.get(self.conn)['claims'], [])
        self.assertTrue(activation.APPLY_INTENT)
        self.assertEqual(len(list(self.root.glob('.report-continuation-*'))), 1)


if __name__ == '__main__':
    unittest.main()
