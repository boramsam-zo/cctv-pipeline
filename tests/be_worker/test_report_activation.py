"""Deterministic read-only plans and explicit grants, using fake IO only."""
from contextlib import nullcontext
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import Mock, patch

from apps.be_worker import hour_session as session, report_continuation as policy, analysis_profiles, team_settings
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


    def save_approval(self, marker):
        task_id = activation.selection_jobs.identifier(dict(gpu_key=marker['source']['gpu_key'],
            purpose='full_report',generation_settings=marker['generation_settings'],reference_date=None))
        plan = dict(schema='cctv-report-continuation-plan-v1', marker=deepcopy(marker), task_id=task_id,
            scene_request_sha256='c'*64, max_new_gpu_calls=0, max_new_scene_calls=1,
            max_cpu_retrievals=1, max_external_embedding_or_report_calls=0,
            admission_window_seconds=900, hard_wall_clock_timeout=False, automatic_retry=False)
        plan['plan_sha256'] = policy.fingerprint(plan)
        path = self.root / ('.report-continuation-' + marker['name'] + '.json')
        activation.save_exclusive(path, {'status':'approved_apply_intent','plan':plan})
        activation.save_exclusive(path.with_suffix('.ready.json'), {
            'status':'report_continuation_admitted', 'plan_sha256':plan['plan_sha256'],
            'selection':{'jobs':[{'worker_job_id':marker['source']['worker_job_id'],
                                 'task_id':task_id,'already_present':True}]}})

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
        self.save_approval(previous)
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
                         (self.directory / 'input.mp4', None, self.version, 'full_report'))
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


class R2ReportActivationTests(ReportActivationTests):
    VERSION = 'R2'

    def previous_b(self):
        profile = analysis_profiles.profile('B')
        sha = self.marker['source']['input_sha256']
        gpu_key = analysis_profiles.gpu_identity(sha, profile)['gpu_key']
        worker = 'd'*32
        self.old['bindings'][worker] = dict(input_sha256=sha,
            input_bytes=self.row['state']['input_bytes'], gpu_key=gpu_key,version='B',
            settings=None,reference_date=None,event_ids=[self.event])
        self.db.rows[self.old['name']] = deepcopy(self.old)
        self.marker['source']['session_sha256'] = policy.fingerprint(self.old)
        previous = deepcopy(self.marker)
        previous['source'].update(worker_job_id=worker, provider_job_id='e'*32,
            analysis_profile=profile,gpu_key=gpu_key,
            comparison_task_id=activation.selection_jobs.identifier(dict(gpu_key=gpu_key,
                purpose='gpu_comparison',generation_settings=None,reference_date=None)))
        previous['generation_settings'] = team_settings.from_environment(os.environ,profile)
        previous['generation_settings'].update(report_code_sha256='1'*64,report_runtime_sha256='2'*64)
        self.save_approval(previous)
        state = dict(previous, started_at=0.,deadline_at=900.,stopped=False,
            bindings={worker:policy.binding(previous)}, claims=[
                dict(worker_job_id=worker,event_id=self.event,kind=kind,request_sha256='c'*64,
                     model=session.model_for(kind),claimed_at=600.+index)
                for index,kind in enumerate(('scene','cpu_retrieval'))])
        self.db.rows[previous['name']] = state
        task = dict(worker_job_id=worker, purpose='full_report', version='B', gpu_key=gpu_key,
            generation_settings=deepcopy(previous['generation_settings']), reference_date=None,
            status='completed', finished_at='synthetic-completed', paused=False, error_code=None)
        task['task_id'] = activation.selection_jobs.identifier({key:task[key] for key in
            ('gpu_key','purpose','generation_settings','reference_date')})
        self.conn.tasks.append(task)
        session.marker_path().write_text(json.dumps(previous))
        self.parameters['name'] = 'mlops-session-20261005-03'
        return previous

    def test_r2_after_old_b_report_preserves_history_and_pins_selected_marker(self):
        previous = self.previous_b()
        before = self.snapshot()
        self.reviewed = self.plan()
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.reviewed['marker']['source']['analysis_profile']['version'],'R2')
        self.assertEqual(policy.binding(self.reviewed['marker'])['version'],'R2')
        with self.assertRaisesRegex(WorkerError,'contract_changed'):
            policy.validate_marker(previous)
        with patch.object(activation.selection_jobs,'admit',side_effect=self.admit):
            result = activation.apply(self.conn,self.store,self.parameters,self.reviewed['plan_sha256'])
        self.assertEqual(result['session'],'mlops-session-20261005-03')
        self.assertEqual({key:self.db.rows[key] for key in before[0]},before[0])
        for name,raw in before[1].items():
            if name != str(session.marker_path().relative_to(self.root)):
                self.assertEqual((self.root/name).read_bytes(),raw)

    def test_saved_handoff_uses_validated_source_policy_not_selected_old_report(self):
        self.previous_b()
        before = self.snapshot()
        with patch.object(session, 'handoff', wraps=session.handoff) as handoff:
            plan = self.plan()
        self.assertEqual(handoff.call_args_list[0].kwargs['policy'], self.old)
        self.assertEqual(handoff.call_args_list[-1].kwargs['policy'], plan['marker'])
        self.assertTrue(all('policy' in call.kwargs for call in handoff.call_args_list))
        self.assertEqual(self.snapshot(), before)

    def test_invalid_source_ledger_is_rejected_before_saved_handoff(self):
        self.previous_b()
        self.db.rows[self.old['name']]['bindings'][self.wid]['gpu_key'] = 'invalid'
        before = self.snapshot()
        with patch.object(session, 'handoff', wraps=session.handoff) as handoff:
            with self.assertRaisesRegex(WorkerError, 'gpu_identity_changed'):
                self.plan()
        handoff.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_history_missing_or_changed_approval_never_grants(self):
        previous = self.previous_b()
        path = self.root/('.report-continuation-'+previous['name']+'.ready.json')
        old = path.read_bytes()
        path.write_text('{}')
        with self.assertRaisesRegex(WorkerError,'history_approval_mismatch'):
            self.plan()
        path.write_bytes(old)
        state = self.db.rows[previous['name']]
        state['claims'][0]['request_sha256'] = 'invalid'
        with self.assertRaisesRegex(WorkerError,'claim_invalid'):
            self.plan()
        self.assertNotIn(self.parameters['name'],self.db.rows)

    def test_selected_marker_change_invalidates_reviewed_plan_without_grant(self):
        self.previous_b()
        plan = self.plan()
        session.marker_path().write_text(json.dumps(session.marker_from_state(self.old)))
        with self.assertRaisesRegex(WorkerError,'reviewed_plan_changed'):
            activation.apply(self.conn,self.store,self.parameters,plan['plan_sha256'])
        self.assertNotIn(self.parameters['name'],self.db.rows)

    def test_active_previous_b_window_is_never_replaced(self):
        previous = self.previous_b()
        state = self.db.rows[previous['name']]
        state['started_at'],state['deadline_at'] = 700.,1600.
        for claim in state['claims']:
            claim['claimed_at'] += 700
        with self.assertRaisesRegex(WorkerError,'active_session_preserved'):
            self.plan()
        self.assertNotIn(self.parameters['name'],self.db.rows)

    def test_selected_previous_report_requires_confirmed_completed_task(self):
        self.previous_b()
        task = self.conn.tasks[-1]
        before = self.snapshot()
        for field, value in (('status','report_started'), ('finished_at',None), ('paused',True),
                             ('error_code','synthetic_failure'), ('gpu_key','changed')):
            original = task[field]
            task[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                    WorkerError,'selected_report_not_completed'):
                self.plan()
            task[field] = original
        self.conn.tasks.remove(task)
        with self.assertRaisesRegex(WorkerError,'selected_report_not_completed'):
            self.plan()
        self.conn.tasks.append(task)
        self.assertEqual(self.plan()['marker']['source']['analysis_profile']['version'],'R2')
        self.assertEqual(self.snapshot(),before)
        self.assertNotIn(self.parameters['name'],self.db.rows)

    def test_old_p6_team_history_requires_original_task_receipt_and_preserves_strict_execution(self):
        profile = analysis_profiles.profile('B')
        worker = 'f'*32
        binding = deepcopy(self.old['bindings'][self.wid])
        binding.pop('version')
        binding['gpu_key'] = analysis_profiles.gpu_identity(binding['input_sha256'],profile)['gpu_key']
        binding['settings'] = team_settings.from_environment(os.environ,profile)
        binding['settings'].update(report_code_sha256='1'*64,report_runtime_sha256='2'*64)
        name = 'p6-hour-session-20261004-01'
        history = dict(schema=session.MARKER['schema'],name=name,max_jobs=1,
            started_at=-5000.,deadline_at=-1400.,stopped=True,bindings={worker:binding},claims=[])
        self.db.rows[name] = history
        row = dict(worker_job_id=worker,directory_id=worker,state=dict(worker_job_id=worker,
            analysis_profile=profile,**{key:binding[key] for key in ('input_sha256','input_bytes','gpu_key')}))
        self.conn.queue_rows[worker] = row
        task = dict(worker_job_id=worker,purpose='full_report',version='B',gpu_key=binding['gpu_key'],
            generation_settings=deepcopy(binding['settings']),reference_date=None,status='completed',
            finished_at='synthetic-completed',paused=False,error_code=None)
        task['task_id'] = activation.selection_jobs.identifier({key:task[key] for key in
            ('gpu_key','purpose','generation_settings','reference_date')})
        self.conn.tasks.append(task)
        directory = self.root/worker
        directory.mkdir(mode=0o700)
        receipt = directory/('task-'+task['task_id']+'.json')
        receipt.write_text(json.dumps(activation.selection_jobs.snapshot(task)))
        before = self.snapshot()
        with self.assertRaisesRegex(WorkerError,'contract_changed'):
            session.validate(history,expected_marker=session.marker_from_state(history))
        plan = self.plan()
        self.assertEqual(plan['preserved_history_sha256'][name],policy.fingerprint(history))
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(plan['marker']['generation_settings'],self.settings)
        raw = receipt.read_bytes()
        receipt.write_text('{}')
        with self.assertRaisesRegex(WorkerError,'receipt_mismatch'):
            self.plan()
        receipt.write_bytes(raw)
        self.conn.tasks.remove(task)
        with self.assertRaisesRegex(WorkerError,'history_task_identity_changed'):
            self.plan()
        self.conn.tasks.append(task)
        self.assertEqual(self.snapshot(),before)
        self.assertNotIn(self.parameters['name'],self.db.rows)


if __name__ == '__main__':
    unittest.main()
