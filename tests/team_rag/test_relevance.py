from copy import deepcopy
import builtins
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from apps.team_rag import control_rag as rag
from apps.team_rag.relevance import source_relevance
from apps.team_rag.review import review_saved_report

FIXTURES = Path(__file__).with_name("fixtures")
EVENT = json.loads((FIXTURES / "saved-vlm-event-v2.json").read_text())
REPORT = json.loads((FIXTURES / "saved-team-report.json").read_text())
CORPUS = [json.loads(line) for line in (rag.ROOT / "data/chunks.jsonl").read_text().splitlines()]
PEDESTRIAN = next(d for d in CORPUS if d["chunk_id"] == "scenario-traffic-15-3e48b1298a69")
SCHOOL = next(d for d in CORPUS if d["chunk_id"] == "scenario-traffic-16-3e48b1298a69")


def stored_source(doc):
    return {**{k: deepcopy(doc.get(k)) for k in
               ("chunk_id", "title", "content", "source_url", "corpus_kind", "section")},
            **{k: deepcopy(doc["metadata"].get(k)) for k in
               ("actors", "application_conditions", "content_role")}}


class ReportRelevance(unittest.TestCase):
    def with_pedestrian_hit(self):
        report = deepcopy(REPORT)
        report["sources"].append(stored_source(PEDESTRIAN))
        report["agency_notifications"][0]["legal_basis"].append(PEDESTRIAN["chunk_id"])
        return report

    def test_unobserved_and_unknown_have_distinct_reasons(self):
        for state, text in (("not_observed", "미관찰"), ("unknown", "판단 불가")):
            with self.subTest(state=state):
                event = deepcopy(EVENT)
                event["people"]["person_on_road"]["state"] = state
                result = source_relevance(PEDESTRIAN, event, "police")
                self.assertFalse(result["accepted"])
                self.assertEqual(result["observed_states"]["people.person_on_road"], state)
                self.assertIn(text, ";".join(result["reasons"]))

    def test_person_presence_cannot_establish_crosswalk_or_driver_obligations(self):
        event = deepcopy(EVENT)
        event["people"]["person_on_road"] = {"state": "observed", "evidence_ids": ["e1"]}
        event["participants"].append({"id": "p3", "type": "Pedestrian", "count": 1, "evidence_ids": ["e1"]})
        result = source_relevance(PEDESTRIAN, event, "police")
        self.assertFalse(result["accepted"])
        reasons = ";".join(result["reasons"])
        self.assertIn("확인 필드가 없는", reasons)
        self.assertIn("운전자 의무", reasons)

    def test_school_zone_does_not_infer_designation_from_people(self):
        result = source_relevance(SCHOOL, EVENT, "police")
        self.assertFalse(result["accepted"])
        self.assertIn("법정 보호구역 지정", ";".join(result["reasons"]))
        self.assertNotIn("people.person_on_road", result["observed_states"])

    def test_generic_pedestrian_response_can_use_supported_observation(self):
        event = deepcopy(EVENT)
        event["people"]["person_on_road"] = {"state": "observed", "evidence_ids": ["e1"]}
        event["participants"].append({"id": "p3", "type": "Pedestrian", "count": 1, "evidence_ids": ["e1"]})
        doc = {"title": "보행자 현장 안전 확인", "content": "경찰은 관찰 위치를 확인한다.",
               "metadata": {"actors": ["경찰"], "application_conditions": ["보행자가 관찰된 경우"]}}
        self.assertTrue(source_relevance(doc, event, "police")["accepted"])
        event["participants"][-1]["evidence_ids"] = []
        self.assertFalse(source_relevance(doc, event, "police")["accepted"])

    def test_saved_review_keeps_raw_evidence_and_excludes_only_unsupported_hits(self):
        report = self.with_pedestrian_hit()
        old = deepcopy(report)
        result = review_saved_report(report, corpus=CORPUS)
        out = result["report_json"]
        self.assertEqual(report, old)
        self.assertEqual(out["input"], old["input"])
        self.assertEqual(out["retrieval"]["trace"], old["retrieval"]["trace"])
        self.assertEqual(out["quality_review"]["original_sources"], old["sources"])
        self.assertEqual({s["chunk_id"] for s in out["sources"]}, {s["chunk_id"] for s in REPORT["sources"]})
        self.assertEqual({s["chunk_id"] for s in out["sources"]}, {s["chunk_id"] for s in out["references"]})
        self.assertNotIn(PEDESTRIAN["chunk_id"], out["agency_notifications"][0]["legal_basis"])
        self.assertEqual(out["excluded_references"][0]["chunk_id"], PEDESTRIAN["chunk_id"])
        self.assertNotIn(PEDESTRIAN["content"], result["report_markdown"])
        self.assertIn("대응 근거 제외", result["report_markdown"])
        from apps.be_worker.team_jobs import validate_report
        validate_report(result, EVENT)

    def test_definition_and_broad_source_incidental_mentions_are_not_filtered(self):
        result = review_saved_report(REPORT, corpus=CORPUS)
        self.assertEqual(result["report_json"]["excluded_references"], [])
        ref = next(r for r in result["report_json"]["references"] if "사고유형의 결정" in r["title"])
        self.assertEqual(ref["report_use"], "accident_type_definition")
        broad = {"title": "교통사고 초동 조사", "content": "사고를 기록한다. 보행자가 있으면 상태도 확인한다.",
                 "metadata": {"actors": ["경찰"], "scenario_tags": ["교통사고"]}}
        self.assertTrue(source_relevance(broad, EVENT, "police")["accepted"])

    def test_full_extract_preserves_intro_numbered_items_exception_and_citations(self):
        text = "이 절차는 기관 확인 후 적용한다.\n\n1. 안전을 확보한다.\n\n2. 자료를 보존한다.\n\n다만, 위험한 경우 진입하지 않는다. [근거1]"
        self.assertEqual(rag.summarize_source(text), text)

    def test_existing_stored_markdown_stays_byte_identical(self):
        self.assertEqual(rag.markdown(REPORT), (FIXTURES / "saved-team-report.md").read_text())

    def test_retrieval_check_rejects_before_ranking_even_with_common_traffic_scope(self):
        plan = rag.agency_plans(EVENT)[0]
        ok, reason = rag.check_source(PEDESTRIAN, plan, {"traffic"}, event=EVENT)
        self.assertFalse(ok)
        self.assertIn("미관찰", reason)

    def test_high_similarity_does_not_restore_excluded_source(self):
        import numpy as np
        from tests.team_rag.test_relevance_synthetic import report_fixture
        report = report_fixture()
        docs = []
        for source in report["sources"]:
            docs.append({**source, "metadata": {**source, "agencies": ["경찰"]}})
        class Encoder:
            def __getitem__(self, index):
                return type('Member', (), {'auto_model': type('Auto', (), {'config': type('Config', (), {'_commit_hash': None})()})()})()
            def encode(self, *args, **kwargs):
                return np.ones((1, 1), dtype=np.float32)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'index.sqlite'
            with sqlite3.connect(path) as con:
                con.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE chunks(chunk_id TEXT,payload TEXT); CREATE TABLE vectors(chunk_id TEXT,vector BLOB);')
                con.executemany('INSERT INTO metadata VALUES(?,?)', [(k, json.dumps(v)) for k, v in {'model': 'synthetic', 'revision': None, 'dimensions': 1}.items()])
                for doc in docs:
                    con.execute('INSERT INTO chunks VALUES(?,?)', (doc['chunk_id'], json.dumps(doc)))
                    con.execute('INSERT INTO vectors VALUES(?,?)', (doc['chunk_id'], np.ones(1, dtype=np.float32).tobytes()))
            with patch.object(rag, 'encoder', return_value=Encoder()):
                result = rag.retrieve(path, report['input'], {})
        self.assertEqual([s['chunk_id'] for s in result['sources']], ['synthetic-common'])
        check = result['retrieval']['trace'][0]['condition_actor_checks']['synthetic-pedestrian']
        self.assertFalse(check['accepted'])
        self.assertNotIn('synthetic-pedestrian', str(result['retrieval']['trace'][0]['topics'][0]['embedding_top20']))

    def test_invalid_source_and_missing_references_rejected(self):
        report = self.with_pedestrian_hit()
        report["sources"][-1]["content"] += "tampered"
        with self.assertRaisesRegex(ValueError, "corpus_mismatch"):
            review_saved_report(report, corpus=CORPUS)
        report = deepcopy(REPORT)
        report["agency_notifications"][0]["legal_basis"].append("missing")
        with self.assertRaisesRegex(ValueError, "unknown_source"):
            review_saved_report(report)

    def test_review_has_no_model_import_or_external_execution(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split('.')[0] in {'torch', 'sentence_transformers', 'transformers', 'numpy', 'boto3'}:
                raise AssertionError("unexpected model import " + name)
            return original(name, *args, **kwargs)
        with patch.object(builtins, '__import__', side_effect=guarded), patch.object(rag, 'encoder') as encoder:
            result = review_saved_report(self.with_pedestrian_hit(), corpus=CORPUS)
        encoder.assert_not_called()
        self.assertFalse(result['execution']['retrieval_executed'])
        self.assertTrue(all(result['execution'][key] == 0 for key in
                            ('vlm_calls', 'generation_calls', 'query_embedding_calls', 'gpu_calls', 'network_requests')))


if __name__ == '__main__':
    unittest.main()
