"""Re-render one saved teammate report without retrieval or model execution."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .control_rag import ROOT, agency_plans, markdown, read, report_view, validate
from .runtime import PIPELINE_ID


def replay_saved_report(saved_report, event, *, corpus=None):
    """Reuse exact input, selected documents and search trace; apply three fixes.

    This is report formatting replay, not proof of a new E5 search or model
    response. It cannot convert an invalid VLM response into a valid event.
    """
    validate(event)
    if saved_report.get("input") != event or saved_report.get("event_id") != event["event_id"]:
        raise ValueError("team_replay_input_mismatch")
    report = deepcopy(saved_report)
    sources = report.get("sources")
    if not isinstance(sources, list):
        raise ValueError("team_replay_sources_missing")
    by_id = {s["chunk_id"]: s for s in sources}
    if len(by_id) != len(sources):
        raise ValueError("team_replay_duplicate_source")
    if corpus is not None:
        corpus_by_id = {row["chunk_id"]: row for row in corpus}
        for source in sources:
            original = corpus_by_id.get(source["chunk_id"])
            if original is None or any(source.get(k) != original.get(k)
                                       for k in ("content", "source_url", "title")):
                raise ValueError("team_replay_corpus_mismatch")
    plans = {p["agency_key"]: p for p in agency_plans(event)}
    if {p["agency_key"] for p in report["agency_notifications"]} != set(plans):
        raise ValueError("team_replay_agency_mismatch")
    for saved_plan in report["agency_notifications"]:
        original_plan = plans[saved_plan["agency_key"]]
        if (saved_plan["agency"] != original_plan["agency"]
                or saved_plan["notification"] != original_plan["notification"]
                or any(cid not in by_id for cid in saved_plan["legal_basis"])):
            raise ValueError("team_replay_plan_mismatch")
        # Retrieve used to overwrite precisely this field. Rebuild its original
        # agency-specific text from the same unmodified event; keep all hits.
        saved_plan["reason"] = original_plan["reason"]
    report["retrieval"]["date_filter_supported"] = False
    report["retrieval"]["reference_date"] = None
    report.update(report_view(report))
    return {"pipeline_id": PIPELINE_ID, "report_json": report,
            "report_markdown": markdown(report),
            "execution": {"mode": "saved_report_replay", "retrieval_executed": False,
                          "vlm_calls": 0, "generation_calls": 0,
                          "query_embedding_calls": 0, "gpu_calls": 0,
                          "network_requests": 0}}


def _write_new(path, raw):
    # Original files and earlier replay attempts are immutable inputs.
    with path.open("xb") as stream:
        stream.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--saved-report", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/chunks.jsonl")
    parser.add_argument("--output", type=Path, required=True,
                        help="A new directory; existing outputs are preserved")
    args = parser.parse_args(argv)
    corpus = [json.loads(line) for line in args.corpus.read_text(encoding="utf-8-sig").splitlines()
              if line.strip()]
    result = replay_saved_report(read(args.saved_report), read(args.event), corpus=corpus)
    args.output.mkdir(parents=True, exist_ok=False)
    report_bytes = (json.dumps(result["report_json"], ensure_ascii=False, indent=2) + "\n").encode()
    markdown_bytes = result["report_markdown"].encode()
    _write_new(args.output / "report.json", report_bytes)
    _write_new(args.output / "report.md", markdown_bytes)
    receipt = {"status": "team_saved_report_replayed", "event_id": result["report_json"]["event_id"],
               **result["execution"],
               "files": {"report.json": {"bytes": len(report_bytes), "sha256": hashlib.sha256(report_bytes).hexdigest()},
                         "report.md": {"bytes": len(markdown_bytes), "sha256": hashlib.sha256(markdown_bytes).hexdigest()}},
               "input_files": {p.name: {"bytes": p.stat().st_size,
                                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                               for p in (args.event, args.saved_report, args.corpus)}}
    _write_new(args.output / "replay-receipt.json", (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
