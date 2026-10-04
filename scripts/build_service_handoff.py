"""Build and statically verify the v0.2 documents-only service handoff."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/team-handoff/20260926-02"
VERSION = "service-draft-v0.2"
MARKER = "<!-- SERVICE_PRD_V02_HISTORY -->"
FIXTURE = "docs/contracts/demo-analysis-cases-v0.2.json"
STAGES = ["objects", "accident", "evidence", "vlm", "rag", "report"]


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def stage(status="pending", reason=None):
    return {"status": status, "reason_code": reason}


def base(name, status, predicted=0, unknown=0):
    return {
        "schema_version": VERSION, "is_demo": True,
        "job_id": f"demo-job-{name}", "run_id": f"demo-run-{name}",
        "source_video_id": "demo-source-no-real-video",
        "status": status, "detection_outcome": "pending",
        "video": {"duration_seconds": 30.0, "camera_id": None,
                  "recorded_at": None, "original_asset_id": None},
        "coverage": {"requested_start_seconds": 0.0, "requested_end_seconds": 30.0,
                     "scheduled_windows": 29, "predicted_windows": predicted,
                     "unclassified_windows": unknown,
                     "pending_windows": 29 - predicted - unknown,
                     "unknown_ranges": []},
        "models": {
            "objects": {"family": "YOLO11s", "weights_sha256": None,
                        "preprocessing_version": "demo-only", "class_map_version": None},
            "accident": {"family": "X3D-S", "weights_sha256": None,
                         "preprocessing_version": "demo-only", "class_map_version": "demo-only"},
            "candidate_policy_version": "demo-only", "decision_rule_version": "demo-only"},
        "stages": {key: stage() for key in STAGES}, "candidates": [], "errors": []}


def event():
    return {
        "event_id": "demo-event-001", "start_seconds": 12.0, "end_seconds": 16.0,
        "label": "suspected_accident", "score": 0.8,
        "score_type": "demo_uncalibrated_max_window_score", "prediction_ids": ["demo-p-001"],
        "evidence": {"clip_asset_id": None, "clip_start_seconds": 10.0,
                     "clip_end_seconds": 18.0, "frames": []},
        "object_observations": [],
        "vlm": {"status": "pending", "summary": None, "observations": [],
                "uncertainties": [], "provider_model": None, "prompt_version": "demo-only"},
        "retrieval": {"status": "pending", "query": None, "corpus_version": None,
                      "retrieval_version": "demo-only", "citations": []},
        "report": {"report_id": None, "revision": None, "status": "pending", "text": None,
                   "source_event_id": "demo-event-001", "evidence_asset_ids": [],
                   "citation_document_ids": [], "limitations": [], "generated_at": None},
        "human_review": {"status": "unreviewed", "review_id": None,
                         "review_revision": 0, "report_revision": None, "note": None}}


def add_report(case, text):
    case["stages"]["report"] = stage("completed")
    case["candidates"][0]["report"].update(
        report_id="demo-report-001", revision=1, status="completed", text=text,
        limitations=["화면용 가상 데이터; 실제 영상·문서 없음"],
        generated_at="2026-09-26T00:00:00Z")


def make_cases():
    queued = base("queued", "queued")
    running = base("running", "running", 10)
    running["stages"]["accident"] = stage("running")
    empty = base("empty", "completed", 29)
    empty["detection_outcome"] = "no_candidates"
    for key in ["accident", "evidence"]:
        empty["stages"][key] = stage("completed")
    for key in ["objects", "vlm", "rag", "report"]:
        empty["stages"][key] = stage("skipped", "no_candidates")
    partial = deepcopy(empty)
    partial.update(job_id="demo-job-partial", run_id="demo-run-partial",
                   status="partial", detection_outcome="unknown")
    partial["coverage"].update(predicted_windows=25, unclassified_windows=4,
                               unknown_ranges=[{"start_seconds": 24.0, "end_seconds": 30.0,
                                                "reason_code": "demo_frame_quality"}])
    for key in ["objects", "vlm", "rag", "report"]:
        partial["stages"][key] = stage("skipped", "no_usable_candidate")
    pending = base("enriching", "enriching", 29)
    pending["detection_outcome"] = "candidates_found"
    for key in ["objects", "accident", "evidence"]:
        pending["stages"][key] = stage("completed")
    pending["stages"]["vlm"] = stage("running")
    pending["candidates"] = [event()]
    pending["candidates"][0]["vlm"]["status"] = "running"
    no_docs = deepcopy(pending)
    no_docs.update(job_id="demo-job-no-docs", run_id="demo-run-no-docs", status="completed")
    no_docs["stages"]["vlm"] = stage("completed")
    no_docs["stages"]["rag"] = stage("insufficient_evidence", "no_relevant_document")
    no_docs["candidates"][0]["vlm"].update(
        status="completed", summary="가상 설명: 해당 구간의 차량 움직임을 확인하세요.",
        provider_model="demo-only", uncertainties=["실제 장면을 분석한 문장이 아닙니다."])
    no_docs["candidates"][0]["retrieval"].update(
        status="insufficient_evidence", query="가상 질의", corpus_version="demo-empty")
    add_report(no_docs, "가상 보고 초안: 검토 후보가 있으며 관련 문서 근거는 부족합니다.")
    vlm_failed = deepcopy(pending)
    vlm_failed.update(job_id="demo-job-vlm-failed", run_id="demo-run-vlm-failed", status="partial")
    vlm_failed["stages"]["vlm"] = stage("failed", "demo_timeout")
    vlm_failed["stages"]["rag"] = stage("skipped", "vlm_unavailable")
    vlm_failed["candidates"][0]["vlm"]["status"] = "failed"
    vlm_failed["candidates"][0]["retrieval"].update(status="skipped", reason_code="vlm_unavailable")
    vlm_failed["errors"] = [{"stage": "vlm", "code": "DEMO_TIMEOUT",
                              "message": "가상 VLM 시간 초과", "retryable": True}]
    add_report(vlm_failed, "가상 부분 보고: 장면 설명 실패. 후보와 원본을 먼저 검토하세요.")
    failed = base("failed", "failed")
    failed["detection_outcome"] = "unknown"
    failed["stages"]["accident"] = stage("failed", "demo_model_load_failed")
    for key in ["objects", "evidence", "vlm", "rag", "report"]:
        failed["stages"][key] = stage("skipped", "no_usable_candidate")
    failed["errors"] = [{"stage": "accident", "code": "DEMO_MODEL_LOAD_FAILED",
                          "message": "가상 모델 로드 실패", "retryable": True}]
    return {"schema_version": VERSION, "is_demo": True,
            "description": "화면 상태 검토용 8개 가상 응답. 실제 가중치·영상·예측·문서가 없으며 null asset을 실제 API 성공 사례로 사용하지 않는다. cases는 API 목록 응답 형식이 아니다.",
            "cases": [queued, running, empty, partial, pending, no_docs, vlm_failed, failed]}


def validate_cases(payload):
    assert payload["is_demo"] and len(payload["cases"]) == 8
    ids = set()
    for case in payload["cases"]:
        assert case["job_id"] not in ids
        ids.add(case["job_id"])
        assert case["is_demo"] and case["schema_version"] == VERSION
        c = case["coverage"]
        assert c["scheduled_windows"] == sum(c[k] for k in
            ["predicted_windows", "unclassified_windows", "pending_windows"])
        assert set(case["stages"]) == set(STAGES)
        for key, value in case["stages"].items():
            assert value["status"] in {"pending", "running", "completed", "skipped", "failed", "insufficient_evidence"}
            if value["status"] == "skipped":
                assert value["reason_code"]
            if value["status"] == "insufficient_evidence":
                assert key == "rag"
        if case["detection_outcome"] == "no_candidates":
            assert not case["candidates"] and c["scheduled_windows"] > 0
            assert c["unclassified_windows"] == c["pending_windows"] == 0
        if case["status"] == "completed":
            assert c["unclassified_windows"] == c["pending_windows"] == 0
            assert all(s["status"] in {"completed", "skipped", "insufficient_evidence"} for s in case["stages"].values())
        if case["detection_outcome"] == "candidates_found":
            assert case["candidates"]
        for item in case["candidates"]:
            assert 0 <= item["start_seconds"] < item["end_seconds"] <= case["video"]["duration_seconds"]
            assert 0 <= item["score"] <= 1
            assert item["human_review"]["status"] == "unreviewed"
            assert item["report"]["source_event_id"] == item["event_id"]
            assert not item["object_observations"]  # No actual object inference in UI fixtures.


def current(path):
    return (ROOT / path).read_text().split(MARKER)[0].rstrip() + "\n"


def main():
    archive = OUT / "cctv-service-prd-v0.2.zip"
    if archive.exists():
        raise SystemExit("Refusing to overwrite the v0.2 handoff snapshot.")
    fixture = make_cases()
    validate_cases(fixture)
    data = encode(fixture)
    fixture_path = ROOT / FIXTURE
    if fixture_path.exists():
        assert fixture_path.read_bytes() == data
    else:
        fixture_path.write_bytes(data)
    paths = ["PROJECT_BRIEF.md", "docs/requirements.md", "docs/team_handoff.md",
             "docs/prd_authoring_prompt.md", "docs/contracts/service_contract.md"]
    paths += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "docs/roles").glob("*.md"))]
    members = {p: current(p).encode() for p in paths}
    members[FIXTURE] = data
    metadata = "docs/contracts/received-model-metadata-v0.2.json"
    members[metadata] = (ROOT / metadata).read_bytes()
    members["IMPLEMENTATION_PLAN.md"] = (ROOT / "IMPLEMENTATION_PLAN.md").read_text().split("<!-- SERVICE_PRD_V02_PLAN_END -->")[0].encode()
    rows = [line for line in (ROOT / "docs/current_status.md").read_text().splitlines()
            if line.startswith(("| 현재 작업 범위 |", "| 서비스 PRD v0.2 |", "| 모델 역할 최신 답변 |", "| 바로 다음 한 작업 |"))]
    members["docs/current_status.md"] = ("# 서비스 문서 현재 상태 — 2026-09-26 스냅샷\n\n전체 실험 기록은 원본 저장소에서 확인한다.\n\n| 항목 | 상태 |\n|---|---|\n" + "\n".join(rows) + "\n").encode()
    members["README.md"] = ("# CCTV 팀 서비스 PRD v0.2\n\n"
        "[전체 PRD](PROJECT_BRIEF.md) → [공통 계약](docs/contracts/service_contract.md) → "
        "[담당 역할 문서](docs/roles/README.md) 순서로 읽으세요.\n\n"
        "- [팀 공유·편집 방법](docs/team_handoff.md)\n"
        "- [요구사항](docs/requirements.md) · [통합 확인](docs/roles/validation.md)\n"
        "- [착수 순서](IMPLEMENTATION_PLAN.md) · [현재 상태](docs/current_status.md)\n\n"
        "문서와 가상 응답만 포함합니다. 실제 모델·영상·코드·비밀은 없습니다. "
        "문서에 나오는 저장소 코드 경로는 참고이며 이 ZIP에는 포함하지 않았습니다. "
        "Mermaid 블록을 지원하는 Markdown 뷰어에서 구조도를 볼 수 있습니다. "
        "공동 편집은 저장소의 원본 파일에서 하고 ZIP은 읽기용 스냅샷으로 사용하세요.\n").encode()
    link_count = 0
    diagram_count = 0
    for name, content in members.items():
        text = content.decode()
        assert "/Users/" not in text, name
        if not name.endswith(".md"):
            continue
        assert len(re.findall(r"^```", text, flags=re.M)) % 2 == 0, name
        assert "```javascript" not in text and "```rust" not in text, name
        diagram_count += text.count("```mermaid")
        for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "#")):
                continue
            target = target.split("#", 1)[0]
            dest = posixpath.normpath(str(PurePosixPath(name).parent / target))
            assert dest in members, (name, target, dest)
            link_count += 1
    req = members["docs/requirements.md"].decode()
    req_ids = re.findall(r"^\| (CCTV-\d{3}) \|", req, flags=re.M)
    assert req_ids == [f"CCTV-{i:03}" for i in range(1, 29)]
    qa = members["docs/roles/validation.md"].decode()
    assert len(re.findall(r"^\| QA-\d{2} \|", qa, flags=re.M)) == 18
    prd = members["PROJECT_BRIEF.md"].decode()
    assert "candidate --> yolo" in prd and "x3d --> candidate" in prd
    assert "yolo --> x3d" not in prd
    assert diagram_count == 1
    manifest = {"version": VERSION, "scope": "documents_and_fictional_fixtures_only",
                "sources": [{"path": name, "sha256": sha256(content).hexdigest(), "bytes": len(content)}
                            for name, content in sorted(members.items())]}
    members["manifest.json"] = encode(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive, "w", ZIP_DEFLATED) as z:
        for name, content in sorted(members.items()):
            z.writestr(name, content)
    with ZipFile(archive) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == set(members)
        assert all(z.read(name) == content for name, content in members.items())
    verification = {
        "version": VERSION, "archive": archive.name, "archive_bytes": archive.stat().st_size,
        "archive_sha256": sha256(archive.read_bytes()).hexdigest(), "archive_entries": len(members),
        "fictional_cases": 8, "requirements": 28, "planned_qa_scenarios": 18,
        "relative_links_checked": link_count, "mermaid_blocks_static_checked": diagram_count,
        "checks": "JSON consistency, candidate/unknown distinction, ordered requirement IDs, relative links, fences, ZIP CRC and bytes",
        "not_run": ["Mermaid visual rendering", "service integration", "model inference", "training", "cloud deployment", "paid API", "external upload"],
    }
    (OUT / "manifest.json").write_bytes(encode(manifest))
    (OUT / "verification.json").write_bytes(encode(verification))
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
