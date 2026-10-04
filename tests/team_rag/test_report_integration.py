from __future__ import annotations

import builtins
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from apps.team_rag import control_rag as rag
from apps.team_rag.replay import main, replay_saved_report
from apps.team_rag.runtime import build_result
from apps.team_rag import runtime

FIXTURES = Path(__file__).with_name("fixtures")
EVENT = json.loads((FIXTURES / "saved-vlm-event-v2.json").read_text())
ORIGINAL_REPORT = json.loads((FIXTURES / "saved-team-report.json").read_text())
CORPUS = [json.loads(line) for line in (rag.ROOT / "data/chunks.jsonl").read_text().splitlines()]
DEFINITION_ID = "kr-traffic-accident-investigation-rule-1213-effective-20260724-article-20-3-paragraph-0"
SIX_SECTIONS = ["사고 개요", "영상 관찰 및 판단 불가 항목", "관제의 전달·요청 사항",
                "현장 대응 참고", "확인 필요 사항", "관련 근거"]


class SavedReportIntegration(unittest.TestCase):
    def replay(self):
        return replay_saved_report(ORIGINAL_REPORT, EVENT, corpus=CORPUS)

    def test_actual_saved_input_and_source_hashes(self):
        self.assertEqual(hashlib.sha256((FIXTURES / "saved-vlm-event-v2.json").read_bytes()).hexdigest(),
                         "f36de3c37dcd88da2c03df7c0d1737a5cc5138f8e2ff4967acb12c22e5724c99")
        self.assertEqual(hashlib.sha256((FIXTURES / "saved-team-report.json").read_bytes()).hexdigest(),
                         "d987285f9ca93aa6b3d4125bed026f3dbdc6c1f6e0ad65c87daecc73d5a1ff70")
        self.assertEqual(hashlib.sha256((FIXTURES / "saved-team-report.md").read_bytes()).hexdigest(),
                         "acacb3896d688dff7525dd50a21b9ed37e5e1dcee424cda29c00534a463d3a50")

    def test_facts_original_inputs_and_search_are_unchanged(self):
        old_event, old_report = deepcopy(EVENT), deepcopy(ORIGINAL_REPORT)
        out = self.replay()
        self.assertEqual(out["report_json"]["input"], EVENT)
        self.assertEqual(EVENT, old_event)
        self.assertEqual(ORIGINAL_REPORT, old_report)
        self.assertEqual(out["report_json"]["sources"], ORIGINAL_REPORT["sources"])
        for key in ("trace", "model", "fusion", "weights", "rrf_k", "experimental_cosine_floor"):
            self.assertEqual(out["report_json"]["retrieval"][key], ORIGINAL_REPORT["retrieval"][key])
        self.assertFalse(out["execution"]["retrieval_executed"])
        self.assertTrue(all(out["execution"][k] == 0 for k in
                            ("vlm_calls", "generation_calls", "query_embedding_calls", "gpu_calls", "network_requests")))

    def test_null_and_unknown_values_are_listed_for_confirmation(self):
        report = self.replay()["report_json"]
        needed = "\n".join(report["verification_needed"])
        for field in ("영상 후보 시각", "카메라 ID", "등록 위치", "도로 종류", "차체 끼임"):
            self.assertIn(field, needed)
        self.assertEqual(report["uncertainties"], [])
        self.assertNotIn("불꽃 판단 불가", needed)  # not_observed stays distinct.

    def test_original_uncertainties_are_preserved_without_duplicates(self):
        event = deepcopy(EVENT)
        event["uncertainties"] = ["영상만으로 부상 여부를 확인할 수 없음"] * 2
        needed = rag.verification_needed(event)
        self.assertEqual(needed.count(event["uncertainties"][0]), 1)
        self.assertEqual(event["uncertainties"], [event["uncertainties"][0]] * 2)

    def test_each_agency_keeps_its_original_specific_reason(self):
        report = self.replay()["report_json"]
        reasons = {p["agency_key"]: p["reason"] for p in report["agency_notifications"]}
        expected = {p["agency_key"]: p["reason"] for p in rag.agency_plans(EVENT)}
        self.assertEqual(reasons, expected)
        self.assertEqual(len(set(reasons.values())), 3)
        rescue = next(p for p in report["agency_responses"] if p["agency"] == "소방·구급")
        self.assertIn("탑승자 상태", rescue["reason"])
        self.assertEqual(rescue["notification"], "현장 확인 후 조건부")
        self.assertEqual(rescue["actions"], [])

    def test_definition_stays_a_reference_and_leaves_action_section(self):
        out = self.replay()
        report = out["report_json"]
        definition = next(s for s in report["references"] if s["chunk_id"] == DEFINITION_ID)
        self.assertEqual(definition["reference_id"], "R3")
        self.assertEqual(definition["report_use"], "accident_type_definition")
        self.assertEqual(definition["content"], next(s["content"] for s in ORIGINAL_REPORT["sources"] if s["chunk_id"] == DEFINITION_ID))
        for plan in report["agency_responses"]:
            self.assertTrue(all("R3" not in a["reference_ids"] for a in plan["actions"]))
        action_section = out["report_markdown"].split("## 현장 대응 참고", 1)[1].split("## 확인 필요 사항", 1)[0]
        self.assertNotIn("사고유형의 결정", action_section)
        self.assertIn("### [R3] 교통사고 조사규칙 제20조의3(사고유형의 결정)", out["report_markdown"])

    def test_six_sections_and_all_original_references_remain(self):
        out = self.replay()
        sections = [line[3:] for line in out["report_markdown"].splitlines() if line.startswith("## ")]
        self.assertEqual(sections, SIX_SECTIONS)
        old_refs = [(s["reference_id"], s["chunk_id"]) for s in ORIGINAL_REPORT["references"]]
        self.assertEqual([(s["reference_id"], s["chunk_id"]) for s in out["report_json"]["references"]], old_refs)
        available = {r["reference_id"] for r in out["report_json"]["references"]}
        for agency in out["report_json"]["agency_responses"]:
            for action in agency["actions"]:
                self.assertTrue(set(action["reference_ids"]) <= available)

    def test_new_path_explicitly_has_no_date_filter(self):
        result = self.replay()["report_json"]
        self.assertIs(result["retrieval"]["date_filter_supported"], False)
        self.assertIsNone(result["retrieval"]["reference_date"])

    def test_input_mismatch_and_invalid_evidence_are_rejected(self):
        event = deepcopy(EVENT)
        event["description"] += "변경"
        with self.assertRaisesRegex(ValueError, "input_mismatch"):
            replay_saved_report(ORIGINAL_REPORT, event)
        event = deepcopy(EVENT)
        event["hazards"]["lane_blocked"]["evidence_ids"] = ["not-a-real-id"]
        with self.assertRaisesRegex(ValueError, "존재하지 않는"):
            replay_saved_report(ORIGINAL_REPORT, event)

    def test_changed_reference_or_unknown_agency_is_rejected(self):
        report = deepcopy(ORIGINAL_REPORT)
        report["sources"][0]["content"] += "changed"
        with self.assertRaisesRegex(ValueError, "corpus_mismatch"):
            replay_saved_report(report, EVENT, corpus=CORPUS)
        report = deepcopy(ORIGINAL_REPORT)
        report["agency_notifications"][0]["agency_key"] = "unexpected"
        with self.assertRaisesRegex(ValueError, "agency_mismatch"):
            replay_saved_report(report, EVENT, corpus=CORPUS)

    def test_build_requires_local_model_before_retrieval(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(rag, "encoder") as model:
            with self.assertRaisesRegex(ValueError, "local_model_not_prepared"):
                build_result(EVENT)
            model.assert_not_called()

    def test_expired_budget_stops_before_any_model_setup(self):
        with patch.object(runtime.time, 'time', return_value=1000), patch.object(runtime, 'retrieve') as search:
            with self.assertRaisesRegex(ValueError, 'session_time_budget_exhausted'):
                build_result(EVENT, deadline_at=999)
            search.assert_not_called()

    def test_budget_callback_stops_between_cpu_queries(self):
        import numpy as np
        class Encoder:
            def __init__(self): self.calls = 0
            def __getitem__(self, index):
                return type('Member', (), {'auto_model': type('Auto', (), {'config': type('Config', (), {'_commit_hash': None})()})()})()
            def encode(self, *args, **kwargs):
                self.calls += 1
                return np.zeros((1,384), dtype=np.float32)
        model = Encoder()
        def budget():
            if model.calls:
                raise ValueError('session_expired_after_first_query')
        with patch.object(rag, 'encoder', return_value=model):
            with self.assertRaisesRegex(ValueError, 'session_expired_after_first_query'):
                rag.retrieve(rag.ROOT / 'data/index.sqlite', EVENT, {}, budget_check=budget)
        self.assertEqual(model.calls, 1)

    def test_import_and_saved_replay_load_no_model_modules(self):
        command = """
import builtins, json
from pathlib import Path
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'torch','sentence_transformers','transformers','numpy','boto3'}:
        raise AssertionError('Forbidden heavy/model import: ' + name)
    return original(name,*args,**kwargs)
builtins.__import__ = guarded
from apps.team_rag.replay import replay_saved_report
fixture = Path('tests/team_rag/fixtures')
replay_saved_report(json.loads((fixture/'saved-team-report.json').read_text()),
                    json.loads((fixture/'saved-vlm-event-v2.json').read_text()))
"""
        proc = subprocess.run([sys.executable, "-B", "-c", command], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_cli_preserves_existing_output_directory(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "saved"
            out.mkdir()
            marker = out / "original.txt"
            marker.write_text("original")
            with self.assertRaises(FileExistsError):
                main(["--event", str(FIXTURES / "saved-vlm-event-v2.json"),
                      "--saved-report", str(FIXTURES / "saved-team-report.json"), "--output", str(out)])
            self.assertEqual(list(out.iterdir()), [marker])
            self.assertEqual(marker.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
