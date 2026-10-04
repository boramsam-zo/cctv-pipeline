"""Build a local, documents-only handoff snapshot with explicitly fictional fixtures."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/team-handoff/20260926-01"
CONTRACT = ROOT / "docs/contracts/demo-analysis-cases-v0.1.json"
VERSION = "handoff-proposal-v0.1"
HISTORY = "---\n\n<details>\n<summary>2026-09-19~20"


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def fixture(name, status, predicted=0, unknown=0):
    return {
        "schema_version": VERSION,
        "is_demo": True,
        "job_id": f"demo-{name}",
        "run_id": None if status == "awaiting_external_run" else f"demo-run-{name}",
        "status": status,
        "execution_mode": "external_notebook",
        "video": {"source_video_id": "demo-video-only", "duration_seconds": 30.0,
                  "camera_id": None, "recorded_at": None, "asset_id": None},
        "coverage": {"scheduled_windows": 29, "predicted_windows": predicted,
                     "unclassified_windows": unknown,
                     "pending_windows": 29 - predicted - unknown},
        "models": {
            "objects": {"family": "YOLO", "version": None, "weights_sha256": None},
            "accident": {"family": None, "version": None, "weights_sha256": None},
            "preprocessing_version": "demo-only",
            "decision_rule_version": "demo-only",
            "candidate_policy_version": "demo-only",
        },
        "stages": dict.fromkeys(["objects", "accident", "vlm", "rag", "report"], "not_started"),
        "candidates": [],
        "errors": [],
    }


def examples():
    waiting = fixture("waiting", "awaiting_external_run")
    empty = fixture("no-candidates", "completed", 29)
    partial = fixture("unclassified", "partial", 25, 4)
    for response in [empty, partial]:
        response["stages"].update(objects="completed", accident="completed",
                                  vlm="skipped", rag="skipped", report="skipped")
    pending = fixture("explanation-pending", "running", 29)
    pending["stages"].update(objects="completed", accident="completed")
    pending["candidates"] = [{
        "event_id": "demo-event-001", "start_seconds": 12.0, "end_seconds": 16.0,
        "label": "suspected_accident", "score": 0.8,
        "score_type": "demo_uncalibrated_max_window_score",
        "evidence": {"clip_asset_id": None, "source_offset_seconds": 12.0},
        "object_observations": [{"source_time_seconds": 13.0, "class_name": "car",
                                 "bbox_format": "xyxy_normalized_original",
                                 "bbox": [0.15, 0.40, 0.35, 0.65], "score": 0.9}],
        "vlm": {"status": "not_started", "summary": None, "evidence_times_seconds": []},
        "retrieval": {"status": "not_started", "corpus_kind": "guidance", "citations": []},
        "report": {"status": "not_started", "text": None},
        "human_review": {"status": "unreviewed", "note": None},
    }]
    ready = deepcopy(pending)
    ready.update(job_id="demo-with-draft", run_id="demo-run-with-draft", status="completed")
    ready["stages"] = dict.fromkeys(ready["stages"], "completed")
    event = ready["candidates"][0]
    event["event_id"] = "demo-event-002"
    event["vlm"] = {"status": "completed", "summary": "가상 설명: 두 차량의 움직임을 검토할 필요가 있습니다.",
                    "evidence_times_seconds": [13.0], "limitations": ["화면 상태 시연용 문장입니다."]}
    event["retrieval"].update(status="insufficient_evidence")
    event["report"] = {"status": "completed", "text": "가상 보고 초안: 원본 장면 확인 필요. 적용할 문서 근거는 확보되지 않았습니다."}
    failed = fixture("failed", "failed")
    failed["stages"]["accident"] = "failed"
    failed["errors"] = [{"stage": "accident", "code": "DEMO_MODEL_LOAD_FAILED",
                         "message": "모델 로드 실패 화면용 가상 오류", "retryable": True}]
    return {"schema_version": VERSION, "is_demo": True,
            "description": "6개 화면 상태용 가상 API 응답. 실제 영상·모델·예측·정답·근거 파일은 포함하지 않습니다. cases는 예시 모음이며 실제 API 목록 응답 형식이 아닙니다.",
            "cases": [waiting, empty, partial, pending, ready, failed]}


def main():
    if OUTPUT.exists():
        raise SystemExit("Refusing to overwrite an existing handoff snapshot.")
    payload = examples()
    assert len(payload["cases"]) == 6
    for case in payload["cases"]:
        assert case["is_demo"] and case["schema_version"] == VERSION
        coverage = case["coverage"]
        assert coverage["scheduled_windows"] == sum(coverage[k] for k in
            ["predicted_windows", "unclassified_windows", "pending_windows"])
        for event in case["candidates"]:
            assert 0 <= event["start_seconds"] < event["end_seconds"] <= case["video"]["duration_seconds"]
            assert all(0 <= value <= 1 for obj in event["object_observations"] for value in obj["bbox"])
    raw = json_bytes(payload)
    if CONTRACT.exists() and CONTRACT.read_bytes() != raw:
        raise SystemExit("Refusing to change a different existing contract fixture.")
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    if not CONTRACT.exists():
        CONTRACT.write_bytes(raw)

    handoff = (ROOT / "docs/team_handoff.md").read_text().split(HISTORY)[0].rstrip() + "\n"
    prompt = (ROOT / "docs/prd_authoring_prompt.md").read_text()
    readme = """# CCTV 팀 공유안 v0.1

1. docs/team_handoff.md: 확정 범위·제안 구조·API 초안·개발 착수 순서
2. docs/contracts/demo-analysis-cases-v0.1.json: 화면/계약 검토용 가상 응답 6개
3. docs/prd_authoring_prompt.md: 공통 PRD를 작성할 때 사용할 프롬프트

모든 사람이 같은 버전을 기준으로 API/JSON 계약을 검토한 뒤 시작하세요.
이 묶음은 로컬에서 만든 문서 스냅샷입니다. 팀 공유·배포·코드 실행을 하지 않았습니다.
실제 PRD 본문/OpenAPI, 실행 코드, 영상, 가중치, 인증정보는 포함하지 않습니다.
문서의 원본 저장소 코드 경로는 참고이며 이 ZIP에 해당 코드가 들어 있다는 뜻이 아닙니다.
Markdown은 GitHub나 편집기에서 열 수 있습니다. Mermaid 미지원 뷰어에서는 구조도가 코드로 보입니다.
선택한 AWS 서비스/API는 제안이며 가상 응답은 실제 예측 결과가 아닙니다.
"""
    files = {"README.md": readme.encode(), "docs/team_handoff.md": handoff.encode(),
             "docs/prd_authoring_prompt.md": prompt.encode(),
             "docs/contracts/demo-analysis-cases-v0.1.json": raw}
    for name, content in files.items():
        text = content.decode()
        assert "/Users/" not in text, name
        if name.endswith(".md"):
            assert text.count("```") % 2 == 0, name
            for link in re.findall(r"\]\(([^)]+)\)", text):
                if not link.startswith(("https://", "http://", "#")):
                    assert (Path(name).parent / link).as_posix() in files, (name, link)
    manifest = {"version": VERSION, "is_proposal": True, "model_run": False,
                "cloud_resources_created": False, "remote_share_run": False,
                "files": [{"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}
                          for name, raw in files.items()]}
    files["manifest.json"] = json_bytes(manifest)
    OUTPUT.mkdir(parents=True)
    archive = OUTPUT / "cctv-team-handoff-v0.1.zip"
    with ZipFile(archive, "x", ZIP_DEFLATED) as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    with ZipFile(archive) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == set(files)
        assert all(z.read(name) == raw for name, raw in files.items())
    receipt = {**manifest, "zip_name": archive.name, "zip_bytes": archive.stat().st_size,
               "zip_sha256": sha256(archive.read_bytes()).hexdigest(),
               "checks": {"fictional_cases": 6, "relative_links": "passed",
                          "markdown_fences": "passed", "zip_crc_and_exact_contents": "passed"}}
    (OUTPUT / "verification.json").write_bytes(json_bytes(receipt))
    print(json.dumps({"zip": str(archive.relative_to(ROOT)), "bytes": receipt["zip_bytes"],
                      "cases": 6, "verification": "passed"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
