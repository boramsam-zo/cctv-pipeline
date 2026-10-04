"""Real saved media/hash checks + JSON Schema, mocked Gemini; no model/network."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from apps.scene_description import scene, rag_trial
from tests.be_worker.test_ab_scene import ABSceneTests, MemoryReader

CONTRACT = Path(__file__).parent / 'fixtures/rag_v2'


class RagTrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ABSceneTests.setUpClass()
        cls.storage = tempfile.TemporaryDirectory()
        cls.root = Path(cls.storage.name)
        cls.bundle = cls.root / 'input'
        config = ABSceneTests.configs['B']
        scene.fetch(config, cls.bundle, MemoryReader(ABSceneTests.prepared['B'], config))
        cls.event_id = config['job_id'] + '_' + config['event_id']

    @classmethod
    def tearDownClass(cls):
        cls.storage.cleanup()
        ABSceneTests.tearDownClass()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / 'result'
        self.value = scene.parse((CONTRACT / 'synthetic-event.json').read_bytes())
        self.value['event_id'] = self.event_id

    def raw(self, value=None):
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': json.dumps(self.value if value is None else value)}]}}],
                'usageMetadata': {'promptTokenCount': 1}}

    def invoke(self, caller, **kwargs):
        return rag_trial.run(self.bundle, CONTRACT, self.output, 'gemini-3.5-flash-lite',
                             execute=True, key='synthetic-key', caller=caller, **kwargs)

    def test_preflight_no_call_or_write(self):
        caller = Mock(side_effect=AssertionError('network forbidden'))
        result = rag_trial.run(self.bundle, CONTRACT, self.output, 'gemini-3.5-flash-lite',
                               caller=caller)
        self.assertEqual(result['generation_calls'], 0)
        self.assertFalse(self.output.exists())
        caller.assert_not_called()

    def test_success_uses_media_and_new_contract_once(self):
        caller = Mock(return_value=self.raw())
        result = self.invoke(caller)
        caller.assert_called_once()
        request = scene.parse(caller.call_args.args[1])
        system = request['systemInstruction']['parts'][0]['text']
        self.assertNotIn('accident_suspicion', system)
        self.assertIn('schema_version', system)
        self.assertNotIn('responseJsonSchema', request['generationConfig'])
        parts = request['contents'][0]['parts']
        self.assertNotIn('Use seconds relative to clip', parts[0]['text'])
        self.assertEqual(sum('inlineData' in p for p in parts), 3)
        self.assertEqual(result['status'], 'event_v2_validated')
        event = scene.parse((self.output / 'event-v2.json').read_bytes())
        self.assertEqual(event, self.value)
        receipt = scene.parse((self.output / 'result.json').read_bytes())
        self.assertFalse(receipt['rag_executed'])
        self.assertEqual(receipt['human_review'], 'unreviewed')
        self.assertNotIn('synthetic-key', ''.join(p.read_text() for p in self.output.iterdir()))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)

    def test_repeat_uses_no_new_call(self):
        caller = Mock(return_value=self.raw())
        self.invoke(caller)
        with self.assertRaises(FileExistsError):
            self.invoke(caller)
        self.assertEqual(caller.call_count, 1)

    def test_transport_failure_preserved_without_retry(self):
        caller = Mock(side_effect=TimeoutError('synthetic transport timeout'))
        with self.assertRaises(TimeoutError):
            self.invoke(caller)
        caller.assert_called_once()
        failure = scene.parse((self.output / 'failure.json').read_bytes())
        self.assertFalse(failure['response_saved'])
        self.assertFalse(failure['automatic_retry'])
        with self.assertRaises(FileExistsError):
            self.invoke(caller)
        caller.assert_called_once()

    def test_incomplete_blocked_and_invalid_json_stop(self):
        examples = [self.raw(), self.raw(), self.raw(), self.raw()]
        examples[0]['candidates'][0]['finishReason'] = 'MAX_TOKENS'
        examples[1]['promptFeedback'] = {'blockReason': 'SAFETY'}
        examples[2]['candidates'][0]['content']['parts'][0]['text'] = '```json\n{}\n```'
        examples[3]['candidates'][0]['content']['parts'][0]['text'] = '{"value": NaN}'
        for i, raw in enumerate(examples):
            with self.subTest(case=i):
                self.output = Path(self.tmp.name) / str(i)
                caller = Mock(return_value=raw)
                with self.assertRaises(Exception):
                    self.invoke(caller)
                caller.assert_called_once()
                self.assertTrue((self.output / 'response.raw.json').exists())
                self.assertTrue((self.output / 'failure.json').exists())
                self.assertFalse((self.output / 'event-v2.json').exists())

    def test_invalid_schema_and_evidence(self):
        edits = [
            lambda v: v.pop('hazards'),
            lambda v: v.update(asset_id='not-allowed'),
            lambda v: v.update(event_id='wrong-event'),
            lambda v: v.update(candidate_time_s=2),
            lambda v: v['location'].update(camera_id='invented'),
            lambda v: v['location'].update(road_type='highway'),
            lambda v: v['hazards']['fire'].update(evidence_ids=[]),
            lambda v: v['hazards']['fire'].update(evidence_ids=['missing']),
            lambda v: v['evidence'].append(deepcopy(v['evidence'][0])),
            lambda v: v['participants'].append({'id':'v1','type':'Van','count':1,'evidence_ids':[]}),
        ]
        validator = rag_trial.contract(CONTRACT)[2]
        for edit in edits:
            value = deepcopy(self.value)
            edit(value)
            with self.subTest(value=value), self.assertRaises(Exception):
                rag_trial.validate_event(value, validator, self.event_id)

    def test_changed_contract_stops_before_call(self):
        altered = Path(self.tmp.name) / 'contract'
        altered.mkdir()
        for name in rag_trial.CONTRACT_HASHES:
            (altered / name).write_bytes((CONTRACT / name).read_bytes())
        with (altered / 'VLM_PROMPT.md').open('a') as stream:
            stream.write('changed')
        caller = Mock()
        with self.assertRaises(ValueError):
            rag_trial.run(self.bundle, altered, self.output, 'gemini-3.5-flash-lite',
                          execute=True, key='synthetic', caller=caller)
        caller.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_evidence_stops_before_call(self):
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


if __name__ == '__main__':
    unittest.main()
