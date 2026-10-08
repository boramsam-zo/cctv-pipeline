"""Synthetic saved response only; no provider, model, corpus or database calls."""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from apps.be_worker import report_recovery as recovery
from apps.be_worker.common import WorkerError
from apps.scene_description import scene, team_scene


class SavedReportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / 'scenes/event_000/description-team-v1'
        self.output.mkdir(parents=True)
        self.inputs = self.output.parent / 'input'
        self.inputs.mkdir()
        job = 'b' * 32
        self.event = json.loads((Path(__file__).parents[1] / 'scene_description/fixtures/rag_v2/synthetic-event.json').read_text())
        self.event['event_id'] = job + '_event_000'
        payload = b'synthetic prepared media request'
        source = dict(bucket='synthetic-cctv-bucket', region='ap-northeast-2',
            manifest_key=f'cctv-evidence/{job}/manifest.json', manifest_sha256='c'*64,
            source_start_s=0, source_end_s=7,
            bundle_hashes={name: 'c'*64 for name in ('source.json','manifest.json','evidence.json','fetch.complete.json')},
            assets={kind:dict(asset_id=f'{job}/event_000/{kind}',
                key=f'cctv-evidence/{job}/evidence/event_000/{kind}.{ext}',sha256='d'*64,bytes=8)
                for kind,ext in (('clip','mp4'),('frame','png'))})
        self.request = dict(schema_version=team_scene.REQUEST_SCHEMA,analysis_id=str(uuid.uuid4()),
            provider_job_id=job,event_id='event_000',team_event_id=self.event['event_id'],
            model='gemini-3.5-flash-lite',prompt_version=team_scene.PROMPT_VERSION,
            request_sha256=scene.sha(payload),video_sample_fps=4,contract_sha256=dict(team_scene.CONTRACT_HASHES),
            input_sha256='e'*64,source=source,created_at='2026-10-05T00:00:00+00:00',
            maximum_generation_calls=1,automatic_retry=False)
        prepare = patch.object(team_scene,'prepare',return_value=(payload,deepcopy(self.request)))
        prepare.start(); self.addCleanup(prepare.stop)
        self.raw = self.response(dict(self.event,**{'$schema':recovery.SCHEMA_URI}))
        self.failure = dict(analysis_id=self.request['analysis_id'],status='failed',stage='team_vlm',
            error_type='ValueError',finished_at='2026-10-05T00:00:10+00:00',request_attempts=1,
            automatic_retry=False,provider_completion='response_saved')
        for name, value in zip(recovery.ORIGINAL_FILES,(self.request,self.raw,self.failure)):
            (self.output/name).write_bytes(scene.encoded(value))
        self.original = {name:(self.output/name).read_bytes() for name in recovery.ORIGINAL_FILES}
        self.execution = uuid.uuid4().hex

    def response(self,value):
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(value)}]}}],
                'usageMetadata':{'totalTokenCount':99},'modelVersion':'synthetic-model'}

    def normalize(self):
        return recovery.normalize_saved_response(self.inputs,self.output,execution_id=self.execution,
            source_task_id='a'*32,plan_sha256='f'*64)

    def test_exact_annotation_only_and_all_observation_rules_remain_strict(self):
        raw = deepcopy(self.raw)
        self.assertEqual(recovery.normalized_event(raw,self.event['event_id']),self.event)
        self.assertEqual(raw,self.raw)
        for update in ({'$schema':'https://example.invalid/schema'},
                       {'$schema':recovery.SCHEMA_URI,'unexpected':'ignored?'},
                       {'$schema':recovery.SCHEMA_URI,'candidate_time_s':2}):
            with self.subTest(update=update),self.assertRaises(Exception):
                recovery.normalized_event(self.response(dict(self.event,**update)),self.event['event_id'])
        raw['candidates'][0]['finishReason']='MAX_TOKENS'
        with self.assertRaisesRegex(WorkerError,'incomplete'):
            recovery.normalized_event(raw,self.event['event_id'])

    def test_append_only_derivation_rechecks_original_request_and_response(self):
        result = self.normalize()
        self.assertEqual(result['team_event'],self.event)
        self.assertIsNone(result['elapsed_seconds'])
        self.assertEqual(result['provenance']['new_generation_calls'],0)
        self.assertEqual(result,recovery.validate_saved_output(self.inputs,self.output))
        for name,raw in self.original.items(): self.assertEqual((self.output/name).read_bytes(),raw)
        with self.assertRaisesRegex(WorkerError,'existing_derived_output'):
            self.normalize()
        (self.output/'event-v2.json').write_text('{}')
        with self.assertRaisesRegex(WorkerError,'derived_observation_changed'):
            recovery.validate_saved_output(self.inputs,self.output)

    def test_road_derivation_changes_only_explicit_unregistered_road_pointer(self):
        observed = deepcopy(self.event)
        observed['location']['road_type'] = 'highway'
        raw = self.response(observed)
        before = deepcopy(raw)
        actual = recovery.normalized_event(raw, self.event['event_id'],
            normalization=recovery.ROAD_NORMALIZATION)
        self.assertEqual(actual, self.event)
        self.assertEqual(raw, before)
        for field, value in (('road_type', 'urban'), ('road_type', 'unknown'),
                             ('camera_id', 'registered-camera'), ('registered_location', 'Seoul')):
            changed = deepcopy(observed)
            changed['location'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(Exception):
                recovery.normalized_event(self.response(changed), self.event['event_id'],
                    normalization=recovery.ROAD_NORMALIZATION)
        for update in ({'candidate_time_s': 2}, {'unexpected': 'discard?'},
                       {'event_id': 'wrong_event_000'}):
            with self.subTest(update=update), self.assertRaises(Exception):
                recovery.normalized_event(self.response(dict(observed, **update)), self.event['event_id'],
                    normalization=recovery.ROAD_NORMALIZATION)
        with self.assertRaisesRegex(WorkerError, 'exact_annotation_required'):
            recovery.normalized_event(raw, self.event['event_id'])

    def test_road_saved_output_proves_source_preservation_and_rejects_provenance_tampering(self):
        observed = deepcopy(self.event)
        observed['location']['road_type'] = 'highway'
        (self.output / 'response.raw.json').write_bytes(scene.encoded(self.response(observed)))
        originals = {name: (self.output / name).read_bytes() for name in recovery.ORIGINAL_FILES}
        result = recovery.normalize_saved_response(self.inputs, self.output, execution_id=self.execution,
            source_task_id='a'*32, plan_sha256='f'*64, normalization=recovery.ROAD_NORMALIZATION)
        self.assertEqual(result['team_event'], self.event)
        self.assertEqual(result['provenance']['corrections'], [recovery.ROAD_CORRECTION])
        self.assertFalse(result['provenance']['other_observation_values_changed'])
        self.assertEqual(result['provenance']['new_generation_calls'], 0)
        self.assertEqual(result, recovery.validate_saved_output(self.inputs, self.output))
        for name, raw in originals.items():
            self.assertEqual((self.output / name).read_bytes(), raw)
        provenance = json.loads((self.output / 'normalization.json').read_text())
        provenance['corrections'][0]['before'] = 'urban'
        (self.output / 'normalization.json').write_bytes(scene.encoded(provenance))
        with self.assertRaisesRegex(WorkerError, 'normalization_receipt_changed'):
            recovery.validate_saved_output(self.inputs, self.output)

    def test_inspection_reconstructs_original_structured_request_version(self):
        self.request.update(response_format_version=team_scene.STRUCTURED_RESPONSE_VERSION,
                            response_json_schema_sha256='9'*64)
        (self.output / 'request.json').write_bytes(scene.encoded(self.request))
        payload = b'synthetic prepared media request'
        with patch.object(team_scene, 'prepare', return_value=(payload, deepcopy(self.request))) as prepare:
            recovery.inspect_response(self.inputs, self.output)
        prepare.assert_called_once_with(self.inputs, self.request['model'], self.request['video_sample_fps'],
                                        response_format_version=team_scene.STRUCTURED_RESPONSE_VERSION)

    def person_event(self):
        observed = deepcopy(self.event)
        observed['people']['person_on_road'] = {'state': 'observed', 'evidence_ids': []}
        return observed

    def test_person_gap_preserves_claim_with_warning_without_fabricated_evidence(self):
        observed = self.person_event()
        raw = self.response(observed)
        original = deepcopy(raw)
        expected = deepcopy(self.event)
        expected['description'] += '\n' + recovery.PERSON_EVIDENCE_WARNING
        expected['uncertainties'].append(recovery.PERSON_EVIDENCE_WARNING)
        event = recovery.normalized_event(raw, self.event['event_id'],
            normalization=recovery.PERSON_EVIDENCE_NORMALIZATION)
        self.assertEqual(event, expected)
        self.assertEqual(raw, original)
        self.assertEqual(event['people']['person_on_road'], {'state': 'unknown', 'evidence_ids': []})
        self.assertEqual(event['evidence'], observed['evidence'])
        self.assertEqual(event['description'].split('\n')[0], observed['description'])
        with self.assertRaisesRegex(ValueError, 'observed_without_evidence'):
            team_scene.validate_event(observed, self.event['event_id'])

    def test_person_policy_does_not_repair_other_invalid_fields_or_erase_linked_claims(self):
        bad = []
        for signal in ({'state': 'unknown', 'evidence_ids': []},
                       {'state': 'not_observed', 'evidence_ids': []},
                       {'state': 'observed', 'evidence_ids': ['ev-1']},
                       {'state': 'observed', 'evidence_ids': [], 'extra': 'discard?'}):
            changed = self.person_event()
            changed['people']['person_on_road'] = signal
            bad.append(changed)
        for section, name, value in (
                ('hazards', 'fire', {'state': 'observed', 'evidence_ids': []}),
                ('hazards', 'fire', {'state': 'observed', 'evidence_ids': ['missing']}),
                ('location', 'road_type', 'highway')):
            changed = self.person_event()
            changed[section][name] = value
            bad.append(changed)
        bad.extend([dict(self.person_event(), candidate_time_s=2),
                    dict(self.person_event(), event_id='wrong_event_000'),
                    dict(self.person_event(), unexpected='discard?')])
        for index, changed in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(Exception):
                recovery.normalized_event(self.response(changed), self.event['event_id'],
                    normalization=recovery.PERSON_EVIDENCE_NORMALIZATION)

    def test_person_saved_derivation_preserves_all_source_bytes_and_detects_tampering(self):
        (self.output / 'response.raw.json').write_bytes(scene.encoded(self.response(self.person_event())))
        originals = {name: (self.output / name).read_bytes() for name in recovery.ORIGINAL_FILES}
        result = recovery.normalize_saved_response(self.inputs, self.output, execution_id=self.execution,
            source_task_id='a'*32, plan_sha256='f'*64, normalization=recovery.PERSON_EVIDENCE_NORMALIZATION)
        provenance = result['provenance']
        self.assertEqual(provenance['corrections'], [recovery.PERSON_EVIDENCE_CORRECTION])
        self.assertEqual(provenance['warnings'], [recovery.PERSON_EVIDENCE_WARNING])
        self.assertEqual(provenance['appended_warning_fields'], ['description', 'uncertainties'])
        self.assertFalse(provenance['other_observation_values_changed'])
        self.assertEqual(provenance['new_generation_calls'], 0)
        self.assertEqual(result, recovery.validate_saved_output(self.inputs, self.output))
        for name, original in originals.items():
            self.assertEqual((self.output / name).read_bytes(), original)
        original_receipt = json.loads((self.output / 'normalization.json').read_text())
        for field, value in (('warnings', []), ('appended_warning_fields', ['uncertainties']),
                             ('corrections', [dict(recovery.PERSON_EVIDENCE_CORRECTION, after={})])):
            changed = dict(original_receipt, **{field: value})
            (self.output / 'normalization.json').write_bytes(scene.encoded(changed))
            with self.subTest(field=field), self.assertRaisesRegex(WorkerError, 'normalization_receipt_changed'):
                recovery.validate_saved_output(self.inputs, self.output)
        (self.output / 'normalization.json').write_bytes(scene.encoded(original_receipt))
        changed = deepcopy(result['team_event'])
        changed['description'] = self.event['description']
        (self.output / 'event-v2.json').write_bytes(scene.encoded(changed))
        with self.assertRaisesRegex(WorkerError, 'derived_observation_changed'):
            recovery.validate_saved_output(self.inputs, self.output)

    def tracking_source(self):
        from tests.be_worker.test_mlflow_tracking import binding
        (self.root / 'model-binding.json').write_bytes(scene.encoded(binding()))
        receipt = dict(worker_job_id='b'*32, execution_id=self.execution, input_sha256='e'*64,
            source_task_id='a'*32, analysis_id=self.request['analysis_id'], report_id=str(uuid.uuid4()),
            result_sha256='c'*64, pipeline_seconds=10.1, started_at_ms=1000, finished_at_ms=11100)
        task = dict(task_id='a'*32, generation_settings={'report_model':'cpu-template'})
        approved = dict(plan_sha256='f'*64, source_failed_run_id='d'*32, source_trace_id='tr-original')
        result = {'provenance': {'schema':recovery.ROAD_NORMALIZATION,
                                'corrections':[deepcopy(recovery.ROAD_CORRECTION)]}}
        return receipt, task, approved, result

    def test_tracking_metadata_maps_pointer_without_weakening_real_boundary(self):
        from tests.be_worker.test_mlflow_tracking import record
        receipt, task, approved, result = self.tracking_source()
        original = deepcopy(result)
        with self.assertRaisesRegex(WorkerError, 'private_metadata_disallowed'):
            record(config={'corrections':result['provenance']['corrections']})
        tracked = recovery._tracking_record(self.root, receipt, task, approved, result)
        self.assertEqual(tracked['config']['corrections'], [dict(field='location.road_type',
            before='highway', after='unknown', reason='camera_registration_not_provided')])
        self.assertEqual(tracked['execution_id'], self.execution)
        self.assertEqual(tracked['result_refs']['cpu_retrieval_calls'], 1)
        self.assertEqual(tracked['result_refs']['new_model_calls'], 0)
        self.assertEqual(result, original)
        result['provenance']['corrections'][0]['path'] = '/other'
        with self.assertRaisesRegex(WorkerError, 'tracking_correction_changed'):
            recovery._tracking_record(self.root, receipt, task, approved, result)

    def test_metadata_delivery_uses_same_completed_execution_without_recomputation(self):
        from tests.be_worker.test_mlflow_tracking import FakeClient
        receipt, task, approved, result = self.tracking_source()
        tracked = recovery._tracking_record(self.root, receipt, task, approved, result)
        client = FakeClient()
        with patch('apps.team_rag.runtime.build_result', side_effect=AssertionError('CPU forbidden')) as cpu, \
             patch.object(team_scene, 'describe', side_effect=AssertionError('VLM forbidden')) as vlm:
            first = recovery.mlflow_tracking.record_execution(self.root, tracked, client=client)
            second = recovery.mlflow_tracking.record_execution(self.root, tracked, client=client)
        self.assertEqual(first['status'], 'recorded')
        self.assertEqual(first, second)
        self.assertEqual(client.created_runs, 1)
        cpu.assert_not_called()
        vlm.assert_not_called()

    def test_person_tracking_exposes_original_claim_warning_and_no_new_model_calls(self):
        from tests.be_worker.test_mlflow_tracking import record
        receipt, task, approved, result = self.tracking_source()
        result['provenance'] = dict(schema=recovery.PERSON_EVIDENCE_NORMALIZATION,
            corrections=[deepcopy(recovery.PERSON_EVIDENCE_CORRECTION)],
            warnings=[recovery.PERSON_EVIDENCE_WARNING],
            appended_warning_fields=['description', 'uncertainties'])
        original = deepcopy(result)
        with self.assertRaisesRegex(WorkerError, 'private_metadata_disallowed'):
            record(config={'corrections':result['provenance']['corrections']})
        tracked = recovery._tracking_record(self.root, receipt, task, approved, result)
        self.assertEqual(tracked['config']['corrections'], [dict(field='people.person_on_road',
            **{key: recovery.PERSON_EVIDENCE_CORRECTION[key] for key in ('before', 'after', 'reason')})])
        self.assertEqual(tracked['config']['warnings'], [recovery.PERSON_EVIDENCE_WARNING])
        self.assertEqual(tracked['result_refs']['new_model_calls'], 0)
        self.assertEqual(result, original)
        result['provenance']['warnings'] = []
        with self.assertRaisesRegex(WorkerError, 'tracking_correction_changed'):
            recovery._tracking_record(self.root, receipt, task, approved, result)

    def test_b_requires_explicit_person_policy_and_other_versions_remain_rejected(self):
        for normalization in recovery.NORMALIZATIONS:
            self.assertTrue(recovery._version_allowed('R2', normalization))
            self.assertEqual(recovery._version_allowed('B', normalization),
                             normalization == recovery.PERSON_EVIDENCE_NORMALIZATION)
            self.assertFalse(recovery._version_allowed('A', normalization))
        self.assertFalse(recovery._version_allowed('R2', 'unreviewed-policy'))

    def test_cpu_attempt_is_reserved_before_work_and_cannot_retry_failure(self):
        row = dict(worker_job_id='1'*32,state={'analysis_profile':{'classifier':{
            'model_id':'synthetic','model_version':'v1'}}})
        task = {'task_id':'a'*32}
        approved = dict(plan_sha256='f'*64,execution_id=self.execution,event_id='event_000',input_sha256='e'*64)
        context = (row,task,{},self.root,self.inputs,self.output)
        store = Mock(); store.lock.side_effect=lambda kind:nullcontext()
        cpu = Mock(side_effect=RuntimeError('synthetic cpu failure'))
        provider = Mock(side_effect=AssertionError('provider forbidden'))
        with patch.object(recovery,'_source',return_value=(approved,context)), \
             patch('apps.team_rag.runtime.build_result',cpu), \
             patch.object(team_scene,'describe',provider):
            with self.assertRaisesRegex(RuntimeError,'cpu failure'):
                recovery.apply(Mock(),store,{},'f'*64)
            with self.assertRaisesRegex(WorkerError,'existing_receipt_no_retry'):
                recovery.apply(Mock(),store,{},'f'*64)
        cpu.assert_called_once(); provider.assert_not_called()
        for name,raw in self.original.items(): self.assertEqual((self.output/name).read_bytes(),raw)
        self.assertTrue((self.root/'report-recovery-intent.json').is_file())
        self.assertTrue((self.root/'report-recovery-failure.json').is_file())
        trace = json.loads(next(self.root.glob('trace-*.json')).read_text())
        self.assertEqual(trace['status'],'ERROR')
        self.assertFalse(any(span['kind']=='LLM' for span in trace['spans']))

    def test_database_roundtrip_normalizes_tuples_but_still_rejects_changed_values(self):
        result=self.normalize()
        payload={'report_id':str(uuid.uuid4()),'synthetic_rankings':[('chunk-a',0.5)]}
        task={'worker_job_id':'1'*32, 'version':'R2'}
        stored={}
        corrupted=False
        def execute(sql,args=None):
            if sql.lstrip().startswith('INSERT'):
                stored.update(settings=recovery.canonical(args[3]),state=recovery.canonical(args[4]))
            answer=deepcopy(stored)
            if corrupted and answer: answer['state']['result']['synthetic_rankings'][0][1]=0.9
            return SimpleNamespace(fetchone=lambda:answer)
        conn=Mock(); conn.execute.side_effect=execute
        with patch.dict(sys.modules,{'psycopg':SimpleNamespace(),'psycopg.types':SimpleNamespace(),
                'psycopg.types.json':SimpleNamespace(Jsonb=recovery.canonical)}), \
             patch.object(recovery.team_db,'store',return_value={'committed':True}), \
             patch.object(recovery.team_db,'show',return_value=recovery.canonical(result)), \
             patch.object(recovery.team_settings,'contract',return_value={'synthetic_contract':True}):
            recovery._store_report(conn,result,payload,task,self.execution)
            self.assertEqual(stored['state']['result']['synthetic_rankings'],[['chunk-a',0.5]])
            corrupted=True
            with self.assertRaisesRegex(WorkerError,'database_readback_failed'):
                recovery._store_report(conn,result,payload,task,self.execution)

    def test_b_report_stores_b_contract_and_rejects_mismatched_source_version(self):
        result = dict(analysis_id=self.request['analysis_id'], source={'version':'B'},
            provenance={'schema':recovery.PERSON_EVIDENCE_NORMALIZATION})
        payload = {'report_id':str(uuid.uuid4())}
        task = {'worker_job_id':'1'*32, 'version':'B'}
        stored = {}
        def execute(sql, args=None):
            if sql.lstrip().startswith('INSERT'):
                stored.update(settings=recovery.canonical(args[3]), state=recovery.canonical(args[4]))
            return SimpleNamespace(fetchone=lambda:deepcopy(stored))
        conn = Mock(); conn.execute.side_effect = execute
        with patch.dict(sys.modules, {'psycopg':SimpleNamespace(), 'psycopg.types':SimpleNamespace(),
                'psycopg.types.json':SimpleNamespace(Jsonb=recovery.canonical)}), \
             patch.object(recovery.team_db, 'store', return_value={'committed':True}), \
             patch.object(recovery.team_db, 'show', return_value=result), \
             patch.object(recovery.team_scene, 'validate_result', return_value='e'*64), \
             patch.object(recovery.team_settings, 'contract', return_value={'version':'B'}) as contract:
            recovery._store_report(conn, result, payload, task, self.execution)
            contract.assert_called_once_with('B')
            self.assertEqual(stored['settings']['contract'], {'version':'B'})
            result['source']['version'] = 'R2'
            with self.assertRaisesRegex(WorkerError, 'store_model_changed'):
                recovery._store_report(conn, result, payload, task, self.execution)
            result['source']['version'] = 'B'
            result['provenance']['schema'] = recovery.ROAD_NORMALIZATION
            with self.assertRaisesRegex(WorkerError, 'store_model_changed'):
                recovery._store_report(conn, result, payload, task, self.execution)


class DemoRecoverySourceTests(unittest.TestCase):
    """Use the real session validator; no provider, model or persistent DB."""
    version = 'R2'
    normalization = recovery.ROAD_NORMALIZATION
    def setUp(self):
        from tests.be_worker.test_demo_session import DemoSessionTests
        self.fixture = DemoSessionTests('test_eight_hour_policy_roundtrip_new_inputs_b_and_r2')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.start()
        self.row = self.fixture.row(self.version)
        source_bytes = b'preserved synthetic video'
        self.row['state']['input_sha256'] = scene.sha(source_bytes)
        self.row['state']['input_bytes'] = len(source_bytes)
        from apps.be_worker import analysis_profiles
        self.row['state']['gpu_key'] = analysis_profiles.gpu_identity(scene.sha(source_bytes),
            self.row['state']['analysis_profile'])['gpu_key']
        self.fixture.bind(self.row)
        self.settings = self.fixture.settings(self.row)
        recovery.hour_session.check_events(self.fixture.conn, self.row, ['event_000'],
            self.settings, 'full_report', None)
        recovery.hour_session.claim(self.fixture.conn, self.row, 'event_000', 'scene',
            'a'*64, recovery.hour_session.MODEL)
        self.row.update(submit_attempts=1)
        self.row['state'].update(status='handoff_ready', error_code=None, post_attempts=1,
                                 provider_job_id='c'*32)
        self.task = dict(version=self.version, generation_settings=self.settings, reference_date=None)
        self.directory = recovery.hour_session.marker_path().parent.resolve() / 'saved-job'
        self.directory.mkdir()
        files = {'input.mp4': source_bytes, 'handoff.json': scene.encoded(self.row['handoff']),
            'normalized-result.json': scene.encoded(dict(input_sha256=scene.sha(source_bytes),
                version=self.version, status='completed')), 'model-binding.json': b'{}',
            'scene-event_000.json': b'{}', 'scenes/event_000/input/manifest.json': b'{}',
            'scenes/event_000/input/fetch.complete.json': b'{}'}
        for name, content in files.items():
            path = self.directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def source(self, payload):
        return recovery._demo_source(payload, self.fixture.marker, self.row, self.task,
            self.directory, self.row['state']['input_sha256'], normalization=self.normalization)

    def test_expired_stopped_history_is_verified_without_reactivating_or_claiming(self):
        self.fixture.db.time += self.fixture.marker['window_seconds'] + 1
        for stopped in (False, True):
            if stopped:
                recovery.hour_session.stop(self.fixture.conn)
            payload = recovery.hour_session.get(self.fixture.conn)
            before = deepcopy(self.fixture.db.rows)
            with self.subTest(stopped=stopped):
                event_id, files = self.source(payload)
                self.assertEqual(event_id, 'event_000')
                self.assertEqual(len(files), 7)
                self.assertEqual(self.fixture.db.rows, before)
                with self.assertRaisesRegex(WorkerError, 'expired|stopped'):
                    recovery.hour_session.active(self.fixture.conn, payload)

    def test_changed_binding_claim_and_completed_evidence_fail_closed(self):
        payload = recovery.hour_session.get(self.fixture.conn)
        changed = deepcopy(payload)
        changed['bindings'][self.row['worker_job_id']]['event_ids'] = ['event_001']
        with self.assertRaises(WorkerError):
            self.source(changed)
        changed = deepcopy(payload)
        changed['claims'][0]['claimed_at'] = changed['deadline_at']
        with self.assertRaisesRegex(WorkerError, 'claim_invalid'):
            self.source(changed)
        self.row['state']['status'] = 'provider_failed'
        with self.assertRaisesRegex(WorkerError, 'completed_demo_gpu_required'):
            self.source(payload)
        self.row['state']['status'] = 'handoff_ready'
        (self.directory / 'input.mp4').write_bytes(b'changed')
        with self.assertRaisesRegex(WorkerError, 'evidence_changed'):
            self.source(payload)


class PersonDemoRecoverySourceTests(DemoRecoverySourceTests):
    version = 'B'
    normalization = recovery.PERSON_EVIDENCE_NORMALIZATION

    def test_b_cannot_use_prior_policy_or_another_models_gpu_result(self):
        payload = recovery.hour_session.get(self.fixture.conn)
        for old in (recovery.NORMALIZATION, recovery.ROAD_NORMALIZATION):
            with self.subTest(policy=old), self.assertRaisesRegex(WorkerError, 'demo_binding_changed'):
                recovery._demo_source(payload, self.fixture.marker, self.row, self.task,
                    self.directory, self.row['state']['input_sha256'], normalization=old)
        self.task['version'] = 'R2'
        with self.assertRaisesRegex(WorkerError, 'demo_binding_changed'):
            self.source(payload)
        self.task['version'] = 'B'
        path = self.directory / 'normalized-result.json'
        changed = json.loads(path.read_text())
        changed['version'] = 'R2'
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(WorkerError, 'demo_gpu_result_changed'):
            self.source(payload)


if __name__ == '__main__':
    unittest.main()
