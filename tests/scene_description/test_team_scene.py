"""Schema 2.0 observation identity/guard/reuse checks; no inference or network."""
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from apps.scene_description import scene, rag_trial, team_scene, team_db
from tests.be_worker import test_ab_scene

CONTRACT = Path(__file__).parent / 'fixtures/rag_v2'


class TeamSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_ab_scene.ABSceneTests.setUpClass()
        cls.storage = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.storage.name).resolve() / 'input'
        config = test_ab_scene.ABSceneTests.configs['B']
        scene.fetch(config, cls.bundle, test_ab_scene.MemoryReader(
            test_ab_scene.ABSceneTests.prepared['B'], config))
        cls.event_id = config['job_id'] + '_' + config['event_id']

    @classmethod
    def tearDownClass(cls):
        cls.storage.cleanup()
        test_ab_scene.ABSceneTests.tearDownClass()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / 'description'
        self.value = scene.parse((CONTRACT / 'synthetic-event.json').read_bytes())
        self.value['event_id'] = self.event_id

    def raw(self, value=None):
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': scene.encoded(self.value if value is None else value).decode()}]}}],
                'usageMetadata': {'promptTokenCount': 1}, 'modelVersion': 'mock'}

    def invoke(self, caller, **kwargs):
        return team_scene.describe(self.bundle, self.output, 'gemini-3.5-flash-lite',
                                   key='mock-key', execute=True, caller=caller, **kwargs)

    def success(self):
        return self.invoke(Mock(return_value=self.raw()))

    def test_default_preflight_never_calls_or_writes(self):
        caller = Mock(side_effect=AssertionError('network forbidden'))
        result = team_scene.describe(self.bundle, self.output, 'gemini-3.5-flash-lite', caller=caller)
        self.assertEqual(result['generation_calls'], 0)
        self.assertFalse(self.output.exists())
        caller.assert_not_called()

    def test_pre_call_guard_has_durable_intent_and_blocks_generation(self):
        caller = Mock()
        def guard(receipt):
            saved = scene.parse((self.output / 'request.json').read_bytes())
            self.assertEqual(saved, receipt)
            raise ValueError('mock_limit_expired')
        with self.assertRaisesRegex(ValueError, 'mock_limit_expired'):
            self.invoke(caller, pre_call=guard)
        caller.assert_not_called()
        failure = scene.parse((self.output / 'failure.json').read_bytes())
        self.assertEqual(failure['request_attempts'], 0)
        self.assertEqual(failure['provider_completion'], 'not_called')

    def test_request_uses_new_contract_and_same_media_roles(self):
        caller = Mock(return_value=self.raw())
        result = self.invoke(caller)
        caller.assert_called_once()
        request = scene.parse(caller.call_args.args[1])
        self.assertNotIn('responseJsonSchema', request['generationConfig'])
        self.assertNotIn('accident_suspicion', request['systemInstruction']['parts'][0]['text'])
        self.assertIn('must be null', request['systemInstruction']['parts'][0]['text'])
        self.assertIn('road_type must be unknown', request['systemInstruction']['parts'][0]['text'])
        parts = request['contents'][0]['parts']
        self.assertEqual(sum('inlineData' in p for p in parts), 3)
        self.assertNotIn('physical_contact_observed', parts[0]['text'])
        self.assertEqual(result['team_event'], self.value)
        self.assertEqual(result['schema_version'], team_scene.SCHEMA_VERSION)
        self.assertEqual(result['source']['assets']['frame']['asset_id'],
                         'b'*32 + '/event_000/frame')
        self.assertEqual(result['gemini_api_calls'], 1)
        self.assertEqual(set(result['source']['assets']), {'clip','frame','roi_clip','context'})
        self.assertEqual(result['human_review'], 'unreviewed')
        self.assertNotIn('mock-key', ''.join(p.read_text() for p in self.output.iterdir()))
        self.assertEqual(team_scene.validate_result(result), scene.sha(scene.encoded(result)))

    def test_repeat_preserves_first_call(self):
        caller = Mock(return_value=self.raw())
        self.invoke(caller)
        with self.assertRaises(FileExistsError):
            self.invoke(caller)
        caller.assert_called_once()

    def test_response_failure_preserves_raw_without_retry(self):
        raw = self.raw()
        raw['candidates'][0]['finishReason'] = 'MAX_TOKENS'
        caller = Mock(return_value=raw)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.invoke(caller)
        self.assertTrue((self.output / 'response.raw.json').is_file())
        self.assertFalse((self.output / 'result.json').exists())
        self.assertFalse(scene.parse((self.output / 'failure.json').read_bytes())['automatic_retry'])
        with self.assertRaises(FileExistsError):
            self.invoke(caller)
        caller.assert_called_once()

    def test_transport_failure_marks_unconfirmed(self):
        caller = Mock(side_effect=TimeoutError('mock timeout'))
        with self.assertRaises(TimeoutError):
            self.invoke(caller)
        failure = scene.parse((self.output / 'failure.json').read_bytes())
        self.assertEqual(failure['request_attempts'], 1)
        self.assertEqual(failure['provider_completion'], 'unconfirmed')
        self.assertFalse((self.output / 'response.raw.json').exists())

    def test_duplicate_json_and_nonfinite_response_are_rejected(self):
        text = scene.encoded(self.value).decode()
        examples = ['{"event_id":"wrong",' + text[1:],
                    text.replace('"candidate_time_s": null', '"candidate_time_s": NaN')]
        for value in examples:
            raw = self.raw()
            raw['candidates'][0]['content']['parts'][0]['text'] = value
            with self.subTest(value=value[:45]), self.assertRaises(ValueError):
                team_scene.parse_response(raw, self.event_id)

    def test_unknown_policy_and_reference_errors_are_not_normalized(self):
        edits = [lambda v: v.update(candidate_time_s=2),
                 lambda v: v['location'].update(camera_id='invented'),
                 lambda v: v['location'].update(road_type='highway'),
                 lambda v: v['hazards']['fire'].update(evidence_ids=[]),
                 lambda v: v['hazards']['fire'].update(evidence_ids=['missing']),
                 lambda v: v['evidence'].append(deepcopy(v['evidence'][0])),
                 lambda v: v['participants'].append({'id':'v1','type':'Van','count':1,'evidence_ids':[]})]
        for i, edit in enumerate(edits):
            value = deepcopy(self.value)
            edit(value)
            before = deepcopy(value)
            with self.subTest(edit=i), self.assertRaises(Exception):
                team_scene.validate_event(value, self.event_id)
            self.assertEqual(value, before)

    def test_result_identity_and_facts_tamper_rejected(self):
        result = self.success()
        edits = [lambda r: r.update(schema_version='cctv-scene-v2'),
                 lambda r: r.update(provider_job_id='c'*32),
                 lambda r: r.update(input_sha256='0'*64),
                 lambda r: r['source']['assets']['clip'].update(asset_id='wrong'),
                 lambda r: r['source'].update(manifest_key='other/manifest.json'),
                 lambda r: r['source']['bundle_hashes'].update({'manifest.json':'0'*64}),
                 lambda r: r['team_event'].update(description='changed observation'),
                 lambda r: r.update(gemini_api_calls=2)]
        for i, edit in enumerate(edits):
            changed = deepcopy(result)
            edit(changed)
            with self.subTest(edit=i), self.assertRaises(Exception):
                team_scene.validate_result(changed)

    def test_changed_bundle_stops_before_output_or_call(self):
        path = self.bundle / 'evidence/event_000/clip.mp4'
        original = path.read_bytes()
        caller = Mock()
        try:
            path.write_bytes(original[:-1])
            with self.assertRaises(ValueError):
                self.invoke(caller)
        finally:
            path.write_bytes(original)
        caller.assert_not_called()
        self.assertFalse(self.output.exists())

    def saved_trial(self):
        saved = self.root / 'saved'
        rag_trial.run(self.bundle, CONTRACT, saved, 'gemini-3.5-flash-lite',
                      execute=True, key='mock-key', caller=Mock(return_value=self.raw()))
        hashes = {name: scene.sha((saved / name).read_bytes()) for name in (
            'request.json','response.raw.json','event-v2.json','result.json')}
        return saved, hashes

    def test_saved_trial_preserves_facts_and_all_original_files(self):
        saved, hashes = self.saved_trial()
        result = team_scene.import_saved_trial(self.bundle, saved, self.output,
                                              expected_file_hashes=hashes)
        self.assertEqual(result['team_event'], self.value)
        self.assertEqual(result['generation_origin'], 'existing_independent_vlm')
        self.assertEqual(result['provenance']['new_generation_calls'], 0)
        self.assertEqual({name:scene.sha((saved/name).read_bytes()) for name in hashes}, hashes)
        self.assertEqual(team_scene.validate_result(result), scene.sha(scene.encoded(result)))
        self.assertEqual(scene.parse((self.output/'event-v2.json').read_bytes()), self.value)

    def test_saved_trial_missing_or_wrong_original_hash_blocks(self):
        saved, hashes = self.saved_trial()
        for bad in ({}, {**hashes,'response.raw.json':'0'*64}):
            with self.assertRaises(ValueError):
                team_scene.import_saved_trial(self.bundle, saved, self.output,
                                              expected_file_hashes=bad)
        self.assertFalse(self.output.exists())

    def test_saved_trial_false_source_even_with_matching_new_hash_blocks(self):
        saved, hashes = self.saved_trial()
        request = scene.parse((saved/'request.json').read_bytes())
        prior = scene.parse((saved/'result.json').read_bytes())
        request['manifest_key'] = 'other/manifest.json'
        prior['manifest_key'] = request['manifest_key']
        for name,value in (('request.json',request),('result.json',prior)):
            (saved/name).write_bytes(scene.encoded(value)+b'\n')
            hashes[name] = scene.sha((saved/name).read_bytes())
        with self.assertRaisesRegex(ValueError, 'source_identity'):
            team_scene.import_saved_trial(self.bundle,saved,self.output,expected_file_hashes=hashes)
        self.assertFalse(self.output.exists())

    def test_saved_trial_is_validation_only_by_default(self):
        saved, hashes = self.saved_trial()
        result = team_scene.import_saved_trial(self.bundle,saved,expected_file_hashes=hashes)
        self.assertEqual(result['team_event'], self.value)
        self.assertFalse(self.output.exists())
        again = team_scene.import_saved_trial(self.bundle,saved,expected_file_hashes=hashes)
        self.assertEqual(scene.encoded(result), scene.encoded(again))

    def test_legacy_scene_has_no_implicit_conversion(self):
        legacy = {'schema_version':'cctv-scene-v2','description':{'observations':[]}}
        with self.assertRaises(ValueError):
            team_scene.validate_result(legacy)

    def test_saved_operating_result_is_rechecked_against_request_raw_and_bundle(self):
        result=self.success()
        before={path:path.read_bytes() for path in self.output.iterdir()}
        again=team_scene.validate_saved_output(self.bundle,self.output)
        self.assertEqual(again,result)
        self.assertEqual(before,{path:path.read_bytes() for path in before})

    def test_saved_fact_changes_cannot_be_hidden_by_rehashing_event(self):
        result=self.success()
        result['team_event']['description']='modified observed fact'
        result['team_event_sha256']=scene.sha(scene.encoded(result['team_event']))
        (self.output/'result.json').write_bytes(scene.encoded(result)+b'\n')
        (self.output/'event-v2.json').write_bytes(scene.encoded(result['team_event'])+b'\n')
        with self.assertRaisesRegex(ValueError,'differs_from_raw'):
            team_scene.validate_saved_output(self.bundle,self.output)

    def test_saved_bundle_receipt_hash_change_is_rejected(self):
        self.success()
        path=self.bundle/'fetch.complete.json'
        original=path.read_bytes()
        try:
            path.write_bytes(original+b' ')
            with self.assertRaisesRegex(ValueError,'request_identity'):
                team_scene.validate_saved_output(self.bundle,self.output)
        finally:
            path.write_bytes(original)

    def test_saved_raw_or_request_hash_change_is_rejected(self):
        self.success()
        for name in ('request.json','response.raw.json'):
            path=self.output/name;before=path.read_bytes();value=scene.parse(before)
            if name=='request.json':value['request_sha256']='0'*64
            else:value['usageMetadata']['promptTokenCount']+=1
            try:
                path.write_bytes(scene.encoded(value)+b'\n')
                with self.subTest(name=name),self.assertRaises(ValueError):
                    team_scene.validate_saved_output(self.bundle,self.output)
            finally:path.write_bytes(before)

    def db_modules(self):
        return patch.dict(sys.modules, {
            'psycopg': SimpleNamespace(),
            'psycopg.types': SimpleNamespace(),
            'psycopg.types.json': SimpleNamespace(Jsonb=lambda value: deepcopy(value)),
            'psycopg.rows': SimpleNamespace(tuple_row=object())})

    def test_team_database_stores_once_and_reads_same_envelope(self):
        result = self.success()
        connection = MemoryConnection()
        with self.db_modules():
            first = team_db.store(connection, result)
            second = team_db.store(connection, result)
            loaded = team_db.show(connection, result['analysis_id'])
        self.assertTrue(first['inserted'])
        self.assertTrue(second['already_present'])
        self.assertEqual(loaded, result)
        self.assertEqual(len(connection.rows), 1)
        self.assertEqual(first['gemini_api_calls'], 0)
        self.assertTrue(all('cctv_scene.team_descriptions' in sql for sql in connection.sql))

    def test_team_database_conflicting_content_preserves_original(self):
        result = self.success()
        changed = deepcopy(result)
        changed['elapsed_seconds'] += 1
        connection = MemoryConnection()
        with self.db_modules():
            team_db.store(connection, result)
            with self.assertRaisesRegex(ValueError, 'conflict'):
                team_db.store(connection, changed)
            self.assertEqual(team_db.show(connection, result['analysis_id']), result)

    def test_team_database_validates_before_any_statement(self):
        connection = MemoryConnection()
        with self.db_modules(), self.assertRaises(ValueError):
            team_db.store(connection, {'schema_version':'cctv-scene-v2'})
        self.assertEqual(connection.sql, [])

    def test_team_database_readback_tamper_is_rejected(self):
        result = self.success()
        connection = MemoryConnection()
        with self.db_modules():
            team_db.store(connection, result)
            digest, saved = connection.rows[result['analysis_id']]
            saved['elapsed_seconds'] += 1
            with self.assertRaisesRegex(ValueError, 'stored_description_changed'):
                team_db.show(connection, result['analysis_id'])


class MemoryConnection:
    """Statement recorder for append/read/conflict behavior, not a PG substitute."""
    def __init__(self):
        self.rows = {}
        self.sql = []

    @contextmanager
    def transaction(self):
        yield

    @contextmanager
    def cursor(self, **_):
        yield MemoryCursor(self)


class MemoryCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 0
        self.row = None

    def execute(self, sql, args):
        self.connection.sql.append(sql)
        if sql.lstrip().startswith('INSERT'):
            analysis_id, _, _, _, digest, value = args
            self.rowcount = int(analysis_id not in self.connection.rows)
            if self.rowcount:
                self.connection.rows[analysis_id] = (digest, value)
        else:
            self.row = self.connection.rows.get(args[0])

    def fetchone(self):
        return self.row


if __name__ == '__main__':
    unittest.main()
