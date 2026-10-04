"""Public CI cases: entirely invented inputs/text, no corpus or model files."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from apps.team_rag import control_rag as rag
from apps.team_rag.review import review_saved_report


def event_fixture():
    def signal(state="not_observed"):
        return {"state": state, "evidence_ids": ["e1"] if state == "observed" else []}
    return {
        "schema_version": "2.0", "event_id": "synthetic-ci", "candidate_time_s": 1,
        "description": "합성 검사 전용 차량 사건", "location": {
            "camera_id": None, "registered_location": None, "road_type": "unknown",
            **{k: signal() for k in ("tunnel", "mountain_road", "near_forest")}},
        "scene": {"day_time": "day", "weather": "clear"},
        "participants": [{"id": "v1", "type": "Car", "count": 2, "evidence_ids": ["e1"]}],
        "accident": {"forms": ["side_collision"], "vehicle_count": 2, "collision_count": 1},
        "hazards": {k: signal() for k in ("fire", "smoke", "debris", "cargo_spill", "lane_blocked", "forest_fire_spread", "secondary_collision", "dangerous_goods_mark")},
        "people": {k: signal() for k in ("person_on_road", "rider_separated", "visible_entrapment")},
        "evidence": [{"id": "e1", "observation": "합성 입력이며 실제 영상이 아님"}],
        "uncertainties": [],
    }


def report_fixture():
    event = event_fixture()
    # Deliberately not actual law text: this checks conditions and preservation.
    common = {"chunk_id": "synthetic-common", "title": "합성 현장 확인 지침",
              "content": "이 문서는 시험 전용이다.\n\n1. 안전 정보를 확인한다.\n\n다만, 조건을 먼저 확인한다.",
              "source_url": "https://example.invalid/test", "corpus_kind": "synthetic",
              "actors": ["경찰"], "application_conditions": ["교통사고 확인"],
              "content_role": "synthetic_test_text"}
    special = {**common, "chunk_id": "synthetic-pedestrian", "title": "합성 보행자 확인 지침",
               "content": "이 문서는 시험 전용 보행자 조건이다.",
               "application_conditions": ["보행자와 횡단보도 통행 의도 확인"]}
    plans = rag.agency_plans(event)
    for plan in plans:
        plan["legal_basis"] = ["synthetic-common", "synthetic-pedestrian"] if plan["agency_key"] == "police" else []
        plan["contact"] = {"channel": "시험", "address": None}
    return {"event_id": event["event_id"], "input": event,
            "accident_summary": event["description"], "agency_notifications": plans,
            "sources": [common, special], "uncertainties": [],
            "retrieval": {"trace": [{"selected": ["synthetic-common", "synthetic-pedestrian"]}],
                          "date_filter_supported": False, "reference_date": None}}


class SyntheticRelevance(unittest.TestCase):
    def test_review_filters_unsupported_condition_without_rerun_or_input_mutation(self):
        report = report_fixture()
        old = deepcopy(report)
        with patch.object(rag, "encoder", side_effect=AssertionError("no model")):
            result = review_saved_report(report)
        reviewed = result["report_json"]
        self.assertEqual(report, old)
        self.assertEqual(reviewed["input"], report["input"])
        self.assertEqual(reviewed["retrieval"]["trace"], report["retrieval"]["trace"])
        self.assertEqual([s["chunk_id"] for s in reviewed["sources"]], ["synthetic-common"])
        self.assertEqual([s["chunk_id"] for s in reviewed["references"]], ["synthetic-common"])
        self.assertEqual(reviewed["quality_review"]["excluded_sources"], [report["sources"][1]])
        self.assertFalse(result["execution"]["retrieval_executed"])
        self.assertEqual(result["execution"]["gpu_calls"], 0)
        self.assertEqual(result["execution"]["vlm_calls"], 0)
        self.assertIn(report["sources"][0]["content"], reviewed["agency_responses"][0]["actions"][0]["text"])
        self.assertEqual(len([line for line in result["report_markdown"].splitlines() if line.startswith("## ")]), 6)

    def test_unknown_does_not_become_observed_and_prior_markdown_does_not_change(self):
        report = report_fixture()
        report["input"]["people"]["person_on_road"]["state"] = "unknown"
        result = review_saved_report(report)
        self.assertIn("판단 불가", result["report_markdown"])
        self.assertEqual(result["report_json"]["input"]["people"]["person_on_road"]["state"], "unknown")
        saved = deepcopy(result["report_json"])
        self.assertEqual(rag.markdown(saved), result["report_markdown"])

    def test_actor_role_guard_is_independent_of_document_identity(self):
        report = report_fixture()
        special = report["sources"][1]
        special.update(title="합성 일반 운전자 의무", actors=["운전자 / 경찰 조사"], application_conditions=[])
        result = review_saved_report(report)
        self.assertIn("운전자 의무", result["report_markdown"])
        self.assertEqual(len(result["report_json"]["sources"]), 1)

    def test_corpus_tampering_and_unknown_reference_fail_closed(self):
        report = report_fixture()
        corpus = deepcopy(report["sources"])
        report["sources"][0]["content"] += "변조"
        with self.assertRaisesRegex(ValueError, "corpus_mismatch"):
            review_saved_report(report, corpus=corpus)
        report = report_fixture()
        report["agency_notifications"][0]["legal_basis"].append("missing")
        with self.assertRaisesRegex(ValueError, "unknown_source"):
            review_saved_report(report)


if __name__ == '__main__':
    unittest.main()
