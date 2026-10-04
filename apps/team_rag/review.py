"""Review a saved report deterministically; never retrieve or run a model."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

try:
    from .control_rag import check_source, markdown, report_view, topics, validate
    from .relevance import REVISION
except ImportError:  # Existing direct-script entry point.
    from control_rag import check_source, markdown, report_view, topics, validate
    from relevance import REVISION


def review_saved_report(saved_report, *, corpus=None):
    """Return a separate review result with immutable source/trace provenance.

    ``saved_report`` is the report_json object, not the BE envelope. The caller
    owns append-only persistence and linkage to its original report/scene IDs.
    Supplying corpus additionally checks title, content and URL against it.
    """
    report = deepcopy(saved_report)
    event = report["input"]
    validate(event)
    if report.get("event_id") != event["event_id"]:
        raise ValueError("team_review_input_mismatch")
    sources = report.get("sources")
    if not isinstance(sources, list):
        raise ValueError("team_review_sources_missing")
    by_id = {s["chunk_id"]: s for s in sources}
    if len(by_id) != len(sources):
        raise ValueError("team_review_duplicate_source")
    if corpus is not None:
        corpus_by_id = {s["chunk_id"]: s for s in corpus}
        for cid, source in by_id.items():
            original = corpus_by_id.get(cid)
            if original is None or any(source.get(k) != original.get(k)
                                       for k in ("content", "title", "source_url")):
                raise ValueError("team_review_corpus_mismatch")
    original_sources = deepcopy(sources)
    original_basis, excluded = [], []
    for plan in report["agency_notifications"]:
        original_basis.append({"agency_key": plan["agency_key"],
                               "legal_basis": list(plan["legal_basis"])})
        accepted = []
        for cid in plan["legal_basis"]:
            if cid not in by_id:
                raise ValueError("team_review_unknown_source")
            source = by_id[cid]
            # Retrieved reports flatten editorial metadata. Reuse the same
            # guards as retrieval without editing their stored original text.
            candidate = dict(source, metadata=source.get("metadata") or source)
            ok, reason = check_source(candidate, plan, topics(event), event=event)
            if ok:
                accepted.append(cid)
            else:
                excluded.append({"chunk_id": cid, "title": source.get("title"),
                                 "agency_key": plan["agency_key"], "reasons": [reason]})
        plan["legal_basis"] = accepted
    report.update(report_view(report))
    excluded.extend(report["excluded_references"])
    referenced = {r["chunk_id"] for r in report["references"]}
    for source in sources:
        if source["chunk_id"] not in referenced and not any(
                row["chunk_id"] == source["chunk_id"] for row in excluded):
            excluded.append({"chunk_id": source["chunk_id"], "title": source.get("title"),
                             "agency_key": None,
                             "reasons": ["즉시 대응 발췌 또는 정의 참고로 연결된 근거 없음"]})
    report["sources"] = [source for source in sources if source["chunk_id"] in referenced]
    for plan in report["agency_notifications"]:
        plan["legal_basis"] = [cid for cid in plan["legal_basis"] if cid in referenced]
        if "retrieved_action_excerpt" in plan:
            plan["retrieved_action_excerpt"] = [row for row in plan["retrieved_action_excerpt"]
                                                if row["chunk_id"] in plan["legal_basis"]]
        plan["evidence_status"] = "retrieved_candidates" if plan["legal_basis"] else "insufficient_evidence"
    canonical = json.dumps(saved_report, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    report["excluded_references"] = excluded
    report["retrieval"]["date_filter_supported"] = False
    report["retrieval"]["reference_date"] = None
    report["quality_review"] = {
        "revision": REVISION, "mode": "saved_sources_only",
        "source_report_canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "original_sources": original_sources, "original_legal_basis": original_basis,
        "excluded_references": deepcopy(excluded),
        "excluded_sources": [deepcopy(source) for source in original_sources
                             if source["chunk_id"] not in referenced],
        "source_trace_preserved": True,
        "scope": "structured conditions, responding actor, complete supplied excerpt",
        "legal_applicability_verified": False,
    }
    return {"pipeline_id": "team-rag-v1", "report_json": report,
            "report_markdown": markdown(report),
            "execution": {"mode": "saved_report_relevance_review", "retrieval_executed": False,
                          "vlm_calls": 0, "generation_calls": 0,
                          "query_embedding_calls": 0, "gpu_calls": 0,
                          "network_requests": 0}}
