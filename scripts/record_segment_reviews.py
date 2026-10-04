"""Bind human reviews or explicit source annotations to saved inputs; no training."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from boram3_cctv import preprocessing as pp

MAX_JSON_BYTES = 1024 * 1024
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
BOOL_FIELDS = ("preview_visible", "crop_or_missing_problem", "order_problem",
               "needs_more_context", "whole_segment_viewed")
TEXT_FIELDS = ("basis", "reviewer", "reviewed_at", "response_source", "related_source_note",
               "raw_response", "reviewed_at_as_reported")
IDENTITY_FIELDS = ("sample_id", "video_path", "source_sha256", "start_seconds", "end_seconds")
ANNOTATION_POLICY = "accident-point-window-v1"


def require(condition, reason):
    if not condition:
        raise pp.PreprocessingError(reason)


def bounded_json(path):
    require(path.is_file(), "input_file_missing")
    require(path.stat().st_size <= MAX_JSON_BYTES, "json_size_limit")
    value = pp.read_json(path)
    require(isinstance(value, dict), "json_object_required")
    return value


def pending_answers(plan, manifest, plan_hash, manifest_hash, preview_hashes):
    """Validate the parent records before emitting a blank, version-bound form."""
    pp.validate_plan(plan)
    require(manifest.get("plan_sha256") == plan_hash, "plan_manifest_hash_mismatch")
    require(manifest.get("inputs_unchanged") is True, "parent_inputs_changed")
    require(manifest.get("status") == "completed_with_unconfirmed", "pending_manifest_required")
    require(manifest.get("settings") == {k: plan[k] for k in
            ("policy_version", "frame_count", "image_size", "time_basis")}, "settings_mismatch")
    rows = manifest.get("samples")
    require(isinstance(rows, list) and len(rows) == len(plan["samples"]), "sample_set_mismatch")
    by_id = {r.get("sample_id"): r for r in rows if isinstance(r, dict)}
    require(len(by_id) == len(rows), "duplicate_manifest_sample")
    answers = []
    for sample in plan["samples"]:
        pp.validate_window(sample)
        require(re.fullmatch(r"[0-9a-f]{64}", str(sample.get("source_sha256", ""))), "invalid_source_hash")
        path = PurePosixPath(sample.get("video_path", ""))
        require(not path.is_absolute() and ".." not in path.parts and len(path.parts) == 2
                and path.parts[0] == "real_videos" and path.suffix == ".mp4"
                and "\\" not in str(path) and ":" not in str(path), "invalid_video_path")
        require(pp.approved_target(sample, plan["policy_version"], sample["source_sha256"]) is None,
                "already_reviewed_sample")
        row = by_id.get(sample["sample_id"], {})
        require(row.get("status") == "unconfirmed" and row.get("target") is None
                and row.get("original_unchanged") is True, "unconfirmed_sample_required")
        for key in IDENTITY_FIELDS:
            manifest_key = "source_path" if key == "video_path" else key
            require(sample.get(key) == row.get(manifest_key), "plan_sample_mismatch")
        require(row.get("training_authorized") is False, "parent_training_scope_invalid")
        splits = row.get("official_splits")
        require(isinstance(splits, dict) and all(splits.get(k) in ("train", "test", "val", "validation")
                for k in ("split_in_distribution", "split_geo_aware")), "official_splits_missing")
        require(sample["sample_id"] in preview_hashes, "preview_hash_missing")
        answers.append({**{k: sample[k] for k in IDENTITY_FIELDS},
                        "preview_sha256": preview_hashes[sample["sample_id"]],
                        "decision": None, **{k: None for k in BOOL_FIELDS + TEXT_FIELDS}})
    return {"schema_version": 1, "parent_plan_sha256": plan_hash,
            "parent_manifest_sha256": manifest_hash, "responses": answers}


def apply_answers(plan, manifest, template, answers):
    """Only explicit complete responses become segment labels; training stays false."""
    require(set(answers) == set(template), "invalid_answer_header")
    require(all(answers[k] == template[k] for k in template if k != "responses"), "stale_answer_version")
    rows = answers.get("responses")
    require(isinstance(rows, list) and len(rows) == len(template["responses"]), "answer_sample_set_mismatch")
    by_id = {r.get("sample_id"): r for r in rows if isinstance(r, dict)}
    expected_ids = {s["sample_id"] for s in plan["samples"]}
    require(len(by_id) == len(rows) and set(by_id) == expected_ids, "answer_sample_set_mismatch")
    result = deepcopy(plan)
    manifest_by_id = {s["sample_id"]: s for s in manifest["samples"]}
    records = []
    for sample, blank in zip(result["samples"], template["responses"]):
        answer = by_id[sample["sample_id"]]
        require(set(answer) == set(blank), "invalid_answer_fields")
        pp.validate_window(answer)
        require(all(answer[k] == blank[k] for k in IDENTITY_FIELDS + ("preview_sha256",)), "answer_identity_mismatch")
        for k in BOOL_FIELDS:
            require(answer[k] is None or type(answer[k]) is bool, "boolean_or_null_required")
        for k in TEXT_FIELDS:
            require(answer[k] is None or (isinstance(answer[k], str) and len(answer[k]) <= 4000), "invalid_answer_text")
        require(answer["decision"] in (None, "accident", "normal", "undetermined"), "invalid_decision")
        if answer["reviewed_at"]:
            try:
                date.fromisoformat(answer["reviewed_at"])
            except ValueError:
                raise pp.PreprocessingError("invalid_review_date") from None
        require(answer["response_source"] in (None, "user_message", "human_completed_file"), "invalid_response_source")
        missing = [k for k in ("decision", "basis", "reviewer", "reviewed_at", "response_source")
                   if answer[k] is None or (isinstance(answer[k], str) and not answer[k].strip())]
        quality_pending = []
        if answer["preview_visible"] is not True:
            missing.append("preview_not_confirmed_visible")
            quality_pending.append("preview_not_confirmed_visible")
        for k in ("crop_or_missing_problem", "order_problem", "needs_more_context"):
            if answer[k] is not False:
                quality_pending.append(k + "_unresolved")
        if answer["decision"] == "normal" and answer["whole_segment_viewed"] is not True:
            missing.append("normal_requires_whole_segment_review")
        decision = answer["decision"]
        status = "undetermined" if decision == "undetermined" else "hold" if decision else "unreviewed"
        if decision in ("normal", "accident") and not missing:
            status = "approved"
        sample["label_review"] = {"status": status, "label": decision if status == "approved" else None,
            "basis": answer["basis"], "reviewer": answer["reviewer"], "reviewed_at": answer["reviewed_at"],
            "scope": "segment", "start_seconds": sample["start_seconds"], "end_seconds": sample["end_seconds"],
            "source_sha256": sample["source_sha256"], "policy_version": plan["policy_version"]}
        sample["human_submission"] = deepcopy(answer)
        sample["input_quality_review"] = {"status": "pending" if quality_pending else "reported_clear",
                                           "unresolved": quality_pending}
        sample["official_splits"] = deepcopy(manifest_by_id[sample["sample_id"]]["official_splits"])
        sample["training_authorized"] = False
        sample["model_input_human_approval"] = False
        sample["independent_evaluation_ready"] = False
        target = pp.approved_target(sample, plan["policy_version"], sample["source_sha256"])
        records.append({"sample_id": sample["sample_id"], "status": status, "target": target,
                        "missing_or_unresolved": missing, "quality_unresolved": quality_pending,
                        "training_authorized": False})
    result.update(review_purpose="human_review_record_only",
                  approval_scope="sample_labels_only_not_training_or_global_policy_approval",
                  parent_plan_sha256=template["parent_plan_sha256"],
                  parent_manifest_sha256=template["parent_manifest_sha256"])
    return result, records


def run(plan_path, manifest_path, output, answers_path=None):
    started = time.monotonic()
    # Store outputs only in new project artifacts directories, never original/config paths.
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    require(output.resolve().is_relative_to(artifacts.resolve()) and output.resolve() != artifacts.resolve(),
            "output_must_be_under_project_artifacts")
    require(not output.exists(), "output_already_exists")
    inputs = [plan_path, manifest_path] + ([answers_path] if answers_path else [])
    hashes = {}
    documents = []
    for p in inputs:
        require(p.is_file() and p.stat().st_size <= MAX_JSON_BYTES, "missing_or_oversized_input")
        hashes[p] = pp.sha256(p)
        documents.append(bounded_json(p))
    plan, manifest = documents[:2]
    preview_hashes = {}
    rows = manifest.get("samples")
    require(isinstance(rows, list) and 1 <= len(rows) <= 3, "select_1_to_3_samples")
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("preview_path"), str), "preview_path_missing")
        rel = PurePosixPath(row["preview_path"])
        require(not rel.is_absolute() and ".." not in rel.parts and "\\" not in str(rel), "unsafe_preview_path")
        preview = manifest_path.parent / rel
        require(preview.resolve().is_relative_to(manifest_path.parent.resolve()), "unsafe_preview_path")
        require(preview.is_file() and preview.stat().st_size <= MAX_PREVIEW_BYTES, "missing_or_oversized_preview")
        hashes[preview] = pp.sha256(preview)
        preview_hashes[row.get("sample_id")] = hashes[preview]
    template = pending_answers(plan, manifest, hashes[plan_path], hashes[manifest_path], preview_hashes)
    answers = documents[2] if answers_path else template
    reviewed, records = apply_answers(plan, manifest, template, answers)
    require(all(pp.sha256(p) == sha for p, sha in hashes.items()), "input_changed_during_recording")
    counts = dict(Counter(r["status"] for r in records))
    report = {"status": "recorded" if all(r["status"] == "approved" for r in records) else "awaiting_review",
              "samples": records, "counts": counts, "inputs_unchanged": True,
              "input_quality_pending_count": sum(bool(r["quality_unresolved"]) for r in records),
              "parent_plan_sha256": hashes[plan_path], "parent_manifest_sha256": hashes[manifest_path],
              "answers_sha256": hashes[answers_path] if answers_path else None,
              "preview_sha256": preview_hashes, "code_sha256": pp.sha256(Path(__file__)),
              "preprocessing_code_sha256": pp.sha256(Path(pp.__file__)),
              "source_video_rechecked": False, "video_frames_decoded": 0, "training_run": False,
              "duration_seconds": time.monotonic() - started}
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / "answers.template.json", template)
    pp.write_json(output / "reviewed_plan.json", reviewed)
    pp.write_json(output / "report.json", report)
    return report


def quality_inputs(plan_path, report_path, kind):
    """Bind quality answers to existing checked 224px inputs, without a model/video."""
    from boram3_cctv.preprocessing_link import contained, indexed
    import numpy as np
    from PIL import Image

    require(kind in ("accident", "qmul"), "unknown_quality_source")
    plan, report = bounded_json(plan_path), bounded_json(report_path)
    hashes = {p: pp.sha256(p) for p in (plan_path, report_path)}
    samples = plan.get("samples")
    require(isinstance(samples, list) and 1 <= len(samples) <= 3, "select_1_to_3_samples")
    require(report.get("status") == "passed" and report.get("training_run") is False, "passed_link_report_required")
    if kind == "accident":
        pp.validate_plan(plan)
        require(report.get("purpose") == "configured_size_input_link_only" and
                report.get("derived_plan_sha256") == hashes[plan_path], "quality_plan_report_mismatch")
        require(report.get("configured_training_input_verified") is True, "input_link_not_verified")
        settings, folder = plan, report_path.parent / "prepared"
        manifest_path = folder / "manifest.json"
        manifest = bounded_json(manifest_path)
        records = indexed(manifest["samples"])
        hashes[manifest_path] = pp.sha256(manifest_path)
        require(manifest.get("plan_sha256") == hashes[plan_path] and manifest.get("inputs_unchanged") is True,
                "quality_plan_report_mismatch")
        checked = indexed(report["samples"])
        checks = ("preview_visible",)
    else:
        require(report.get("purpose") == "external_reviewed_input_label_link_only" and
                report.get("input_sha256", {}).get("review") == hashes[plan_path] and
                report.get("inputs_unchanged") is True, "quality_plan_report_mismatch")
        require(plan.get("time_basis") == "local_file_pts_seconds" and plan.get("class_order") == pp.CLASS_ORDER,
                "invalid_external_review")
        settings, folder = report["preprocessing"], report_path.parent
        records, checked = indexed(report["samples"]), indexed(report["verification"])
        checks = ("preview_visible", "crop_or_missing_problem", "needs_more_context")
    require(settings.get("image_size") == 224 and settings.get("frame_count") == 16, "expected_16_frames_at_224")
    ids = {s["sample_id"] for s in samples}
    require(len(ids) == len(samples) and set(records) == set(checked) == ids, "quality_sample_set_mismatch")
    answers = []
    for sample in samples:
        sid = sample["sample_id"]
        require(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", sid), "invalid_sample_id")
        pp.validate_window(sample)
        require(re.fullmatch(r"[a-f0-9]{64}", str(sample.get("source_sha256", ""))), "invalid_source_hash")
        row, check = records[sid], checked[sid]
        target = pp.approved_target(sample, plan["policy_version"], sample["source_sha256"])
        require(target is not None and type(check.get("target")) is int and check["target"] == target
                and check.get("status") == "passed" and type(row.get("target")) is int and row["target"] == target,
                "approved_label_link_mismatch")
        require(row.get("label_review") == sample["label_review"] and row.get("training_authorized") is False,
                "review_evidence_mismatch")
        for key in ("source_sha256", "start_seconds", "end_seconds", "source_group_id", "event_id"):
            require(row.get(key) == sample.get(key), "quality_provenance_mismatch:" + key)
        split_key = "official_splits" if kind == "accident" else "official_split"
        require(row.get(split_key) == sample.get(split_key), "quality_split_mismatch")
        require(row.get("source_video_id") == "sha256:" + sample["source_sha256"] and
                row.get("time_basis") == plan["time_basis"], "quality_source_identity_mismatch")
        preview = contained(folder, row["preview_path"])
        frames = contained(folder, row["frames_metadata_path"])
        array_path = contained(folder, row["input_path"])
        require(preview.is_file() and preview.stat().st_size <= MAX_PREVIEW_BYTES, "missing_or_oversized_preview")
        require(array_path.is_file() and array_path.stat().st_size <= 8 * 1024 * 1024, "missing_or_oversized_array")
        details = bounded_json(frames)
        for path in (preview, frames, array_path):
            hashes[path] = pp.sha256(path)
        evidence = check if kind == "accident" else row
        require(hashes[frames] == evidence.get("frames_sha256") and hashes[array_path] == evidence.get("array_sha256"),
                "checked_input_changed")
        if kind == "accident":
            require(hashes[preview] == check.get("preview_sha256"), "checked_preview_changed")
        require(details.get("target") == target and details.get("transform", {}).get("crop") is None,
                "checked_frame_metadata_mismatch")
        # QMUL's old report lacks a contact-sheet hash. Check current displayed
        # cells against the already hashed array, then bind this exact PNG.
        array = np.load(array_path, mmap_mode="r", allow_pickle=False)
        require(array.shape == (16, 224, 224, 3) and array.dtype == np.uint8, "checked_array_shape_mismatch")
        with Image.open(preview) as sheet:
            require(sheet.size == (896, 1100), "preview_layout_mismatch")
            for index in range(16):
                x, y = (index % 4) * 224, 44 + (index // 4) * 264
                require(np.array_equal(array[index], np.asarray(sheet.crop((x, y, x + 224, y + 224)))),
                        "preview_does_not_match_checked_input")
        answers.append({"sample_id": sid, "source_video_id": row["source_video_id"],
                        "start_seconds": sample["start_seconds"], "end_seconds": sample["end_seconds"],
                        "preview_path": row["preview_path"], "preview_sha256": hashes[preview],
                        "frames_sha256": hashes[frames], "checks": {k: None for k in checks},
                        "reviewer": None, "reviewed_at": None, "response_source": None, "raw_response": None})
    return plan, {"schema_version": 1, "purpose": "input_quality_only", "source_kind": kind,
                  "parent_plan_sha256": hashes[plan_path], "parent_report_sha256": hashes[report_path],
                  "image_size": 224, "frame_count": 16, "responses": answers}, hashes


def apply_quality_answers(plan, template, answers):
    """Keep every original label intact, even when visibility is poor or unanswered."""
    from boram3_cctv.preprocessing_link import indexed
    require(set(answers) == set(template) and all(answers[k] == template[k] for k in template if k != "responses"),
            "stale_quality_answer_version")
    require(isinstance(answers["responses"], list), "quality_responses_required")
    responses = indexed(answers["responses"])
    require(set(responses) == {s["sample_id"] for s in plan["samples"]}, "quality_sample_set_mismatch")
    records = []
    for sample, blank in zip(plan["samples"], template["responses"]):
        answer = responses[sample["sample_id"]]
        editable = ("checks", "reviewer", "reviewed_at", "response_source", "raw_response")
        require(set(answer) == set(blank) and all(answer[k] == blank[k] for k in blank if k not in editable),
                "quality_answer_identity_mismatch")
        require(isinstance(answer["checks"], dict) and set(answer["checks"]) == set(blank["checks"]),
                "quality_check_fields_mismatch")
        require(all(v is None or type(v) is bool for v in answer["checks"].values()), "boolean_or_null_required")
        for key in editable[1:]:
            require(answer[key] is None or isinstance(answer[key], str) and len(answer[key]) <= 4000,
                    "invalid_answer_text")
        require(answer["response_source"] in (None, "user_message", "human_completed_file"), "invalid_response_source")
        if answer["reviewed_at"]:
            try:
                require(date.fromisoformat(answer["reviewed_at"]).isoformat() == answer["reviewed_at"], "invalid_review_date")
            except ValueError:
                raise pp.PreprocessingError("invalid_review_date") from None
        missing = [k for k, v in answer["checks"].items() if v is None]
        missing += [k for k in editable[1:] if not answer[k] or not answer[k].strip()]
        issues = [k for k, v in answer["checks"].items() if v is (False if k == "preview_visible" else True)]
        status = "awaiting_review" if missing else "reported_issue" if issues else "confirmed"
        target = pp.approved_target(sample, plan["policy_version"], sample["source_sha256"])
        require(target is not None, "approved_segment_label_required")
        records.append({"sample_id": sample["sample_id"], "approved_target": target,
                        "original_segment_record": deepcopy(sample), "quality_submission": deepcopy(answer),
                        "quality_status": status, "unanswered_fields": missing, "reported_issues": issues,
                        "training_authorized": False, "independent_evaluation_ready": False})
    return records


def run_quality(plan_path, report_path, output=None, answers_path=None, kind="accident", *, check_only=False):
    started = time.monotonic()
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    if check_only:
        require(output is None, "check_only_does_not_accept_output")
    else:
        require(output is not None, "output_required_for_recording")
        require(output.resolve().is_relative_to(artifacts.resolve()) and output.resolve() != artifacts.resolve(),
                "output_must_be_under_project_artifacts")
        require(not output.exists(), "output_already_exists")
    plan, template, hashes = quality_inputs(plan_path, report_path, kind)
    answers = bounded_json(answers_path) if answers_path else template
    if answers_path:
        hashes[answers_path] = pp.sha256(answers_path)
    records = apply_quality_answers(plan, template, answers)
    require(all(pp.sha256(path) == digest for path, digest in hashes.items()), "input_changed_during_recording")
    counts = dict(Counter(r["quality_status"] for r in records))
    report = {"status": "recorded" if counts.get("confirmed") == len(records) else "needs_attention"
              if counts.get("reported_issue") else "awaiting_review", "purpose": "input_quality_only",
              "counts": counts, "samples": records, "inputs_unchanged": True,
              "parent_plan_sha256": template["parent_plan_sha256"], "parent_report_sha256": template["parent_report_sha256"],
              "answers_sha256": hashes[answers_path] if answers_path else None,
              "labels_changed": False, "source_video_rechecked": False, "video_frames_decoded": 0,
              "new_preview_images": 0, "model_created": False, "training_run": False,
              "code_sha256": pp.sha256(Path(__file__)), "duration_seconds": time.monotonic() - started}
    if check_only:
        # A valid response is ready to record, not already recorded or ready to train.
        if report["status"] == "recorded":
            report["status"] = "ready_to_record"
        report.update(check_only=True, output_written=False)
        return report
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / "answers.template.json", template)
    pp.write_json(output / "quality_review.json", report)
    return report


def run_source_annotations(plan_path, manifest_path, policy_path, output):
    """Training-only labels from CSV points; never impersonate a human reviewer.

    Reuse saved PNGs and PTS. Existing human/evaluation approval gates remain
    unchanged; this prepares inputs, not an authorization to run a training job.
    """
    import numpy as np
    from PIL import Image
    from boram3_cctv.preprocessing_link import contained, indexed
    from boram3_cctv import train_candidates as tc

    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    plan_path, manifest_path, policy_path, output = (
        Path(p).resolve() for p in (plan_path, manifest_path, policy_path, output))
    require(output.resolve().is_relative_to((root / "artifacts").resolve())
            and output.resolve() != (root / "artifacts").resolve(), "output_must_be_under_project_artifacts")
    require(not output.exists(), "output_already_exists")
    hashes = {}

    def read(path):
        require(path.resolve().is_relative_to(root), "annotation_reference_outside_project")
        require(path.is_file() and path.stat().st_size <= MAX_JSON_BYTES, "missing_or_oversized_input")
        before = pp.sha256(path)
        value = bounded_json(path)
        require(pp.sha256(path) == before, "input_changed_during_recording")
        hashes[path] = before
        return value

    policy, plan, manifest = read(policy_path), read(plan_path), read(manifest_path)
    require(policy.get("schema_version") == 1 and policy.get("policy_version") == ANNOTATION_POLICY
            and policy.get("purpose") == "source_annotation_training_inputs_only"
            and policy.get("time_basis") == "local_clip_seconds"
            and policy.get("window_seconds") == 2
            and policy.get("training_execution_authorized") is False, "invalid_annotation_policy")
    auth = policy.get("authorization", {})
    require(auth.get("source") == "user_message" and auth.get("use_source_annotations") is True
            and isinstance(auth.get("raw_response"), str) and bool(auth["raw_response"].strip()),
            "source_annotation_authorization_required")
    require(policy.get("parent_plan_sha256") == hashes[plan_path]
            and policy.get("parent_manifest_sha256") == hashes[manifest_path], "annotation_parent_changed")
    pp.validate_plan(plan)
    require(plan["frame_count"] == 16 and plan["image_size"] == 224, "expected_16_frames_at_224")
    require(manifest.get("class_order") == pp.CLASS_ORDER
            and manifest.get("preprocessing_version") == pp.VERSION, "annotation_preprocessing_mismatch")
    require(policy.get("sample_ids") == [s["sample_id"] for s in plan["samples"]], "annotation_selection_mismatch")
    metadata_path = contained(root, policy["metadata_path"])
    require(metadata_path.name == "metadata-real.csv", "real_metadata_required")
    metadata, digest = tc.read_candidates_metadata(metadata_path.parent)
    require(digest == policy.get("metadata_sha256") == manifest.get("metadata_sha256"), "annotation_metadata_changed")
    hashes[metadata_path] = digest
    reserved_path = contained(root, policy["reserved_manifest"])
    reserved = read(reserved_path)
    require(hashes[reserved_path] == policy.get("reserved_manifest_sha256")
            and isinstance(reserved.get("segments"), list) and bool(reserved["segments"]),
            "annotation_reservations_changed")
    blocked = reserved["segments"]
    by_id = indexed(manifest["samples"])
    preview_hashes = {}
    for row in by_id.values():
        path = contained(manifest_path.parent, row["preview_path"])
        require(path.is_file() and path.stat().st_size <= MAX_PREVIEW_BYTES, "missing_or_oversized_preview")
        preview_hashes[row["sample_id"]] = hashes[path] = pp.sha256(path)
    # Reuse the original source/window/settings binding, but do NOT apply_answers.
    pending_answers(plan, manifest, hashes[plan_path], hashes[manifest_path], preview_hashes)
    prepared = []
    payloads = []
    for sample in plan["samples"]:
        sid = sample["sample_id"]
        row = by_id[sid]
        matches = metadata.get(sample["video_path"], [])
        require(len(matches) == 1, "annotation_unique_csv_row_required")
        entry = matches[0]
        require(entry == row.get("source_annotation"), "annotation_csv_row_mismatch")
        raw = entry["values"]
        require(raw.get("split_in_distribution") == "train"
                and row["official_splits"].get("split_in_distribution") == "train", "annotation_IID_test_forbidden")
        for reserved_sample in blocked:
            require(not (sample["video_path"] == reserved_sample.get("source_path")
                         or sample["source_sha256"] == reserved_sample.get("source_sha256")
                         or any(sample.get(k) and sample[k] == reserved_sample.get(k)
                                for k in ("event_id", "upstream_original_id"))), "annotation_reserved_source_forbidden")
        try:
            accident_time = pp.number(float(raw["accident_time"]))
            metadata_duration = pp.number(float(raw["duration"]))
        except (ValueError, TypeError, KeyError, pp.PreprocessingError):
            raise pp.PreprocessingError("annotation_invalid_time_or_duration") from None
        media = row.get("media", {})
        duration = pp.number(media.get("duration_seconds"))
        start, end = pp.validate_window(sample, duration)
        require(0 <= accident_time < min(duration, metadata_duration), "annotation_time_out_of_video")
        require(math.isclose(end-start, 2, rel_tol=0, abs_tol=1e-6)
                and math.isclose(start, accident_time-1, rel_tol=0, abs_tol=1e-6)
                and math.isclose(end, accident_time+1, rel_tol=0, abs_tol=1e-6)
                and start <= accident_time < end, "annotation_requires_existing_centered_2s_window")
        frames_path = contained(manifest_path.parent, row["frames_metadata_path"])
        details = read(frames_path)
        require(details == row.get("processed_frames"), "annotation_saved_frames_changed")
        times = [pp.number(f["actual_seconds"]) for f in details["frames"]]
        require(details.get("target") is None and details.get("model_input_written") is False
                and details.get("array_shape") == [16, 224, 224, 3]
                and details.get("array_layout") == "THWC" and details.get("dtype") == "uint8"
                and details.get("transform", {}).get("method") == "letterbox"
                and details["transform"].get("crop") is None
                and details.get("model_normalization") is None, "annotation_saved_input_mismatch")
        require(len(times) == 16 and all(start <= t < end for t in times)
                and all(b > a for a, b in zip(times, times[1:]))
                and times[0] <= accident_time <= times[-1]
                and all(math.isclose(pp.number(f["requested_seconds"]), start+(i+.5)*2/16,
                                     rel_tol=0, abs_tol=1e-6) for i, f in enumerate(details["frames"])),
                "annotation_frame_order_or_time_mismatch")
        preview_path = contained(manifest_path.parent, row["preview_path"])
        pixels = []
        with Image.open(preview_path) as sheet:
            require(sheet.mode == "RGB" and sheet.size == (896, 1100), "annotation_preview_layout_mismatch")
            for i in range(16):
                png = contained(frames_path.parent, f"frames/{i:03d}.png")
                require(png.is_file() and png.stat().st_size <= MAX_PREVIEW_BYTES, "annotation_frame_missing_or_oversized")
                hashes[png] = pp.sha256(png)
                with Image.open(png) as im:
                    require(im.mode == "RGB" and im.size == (224, 224), "annotation_frame_shape_mismatch")
                    pixels.append(np.array(im))
                x, y = i % 4 * 224, 44 + i // 4 * 264
                require(np.array_equal(pixels[-1], np.asarray(sheet.crop((x,y,x+224,y+224)))),
                        "annotation_preview_pixel_or_order_mismatch")
        provenance = {"kind": "source_annotation_derived", "policy_version": ANNOTATION_POLICY,
            "metadata_path": policy["metadata_path"], "metadata_sha256": digest,
            "csv_row": entry["csv_row"], "source_path": raw["path"],
            "source_sha256": sample["source_sha256"], "annotation_field": "accident_time",
            "annotation_raw": raw["accident_time"], "annotation_seconds": accident_time,
            "time_basis": "local_clip_seconds", "rule": "existing_2s_window_centered_on_source_accident_point",
            "point_is_not_accident_end": True, "binary_label_supplied_by_dataset": False,
            "human_reviewed": False, "policy_authorization_sha256": hashes[policy_path]}
        copied = deepcopy(details)
        copied.update(target=1, model_input_written=True, label_provenance=provenance)
        record = {**{k: sample.get(k) for k in IDENTITY_FIELDS}, "target": 1,
            "label_provenance": provenance, "label_review": deepcopy(sample["label_review"]),
            "input_quality_status": "technical_checks_passed_visual_review_not_claimed",
            "management_group": sample.get("source_group_id") or "source_annotations_unresolved",
            "event_id": sample.get("event_id"), "upstream_original_id": sample.get("upstream_original_id"),
            "split": "train", "official_splits": deepcopy(row["official_splits"]),
            "training_execution_authorized": False, "evaluation_authorized": False,
            "preview_path": str(preview_path.relative_to(root)), "preview_sha256": hashes[preview_path]}
        prepared.append(record)
        payloads.append((np.stack(pixels), copied))
    require(all(pp.sha256(p) == sha for p, sha in hashes.items()), "input_changed_during_recording")
    # No partial output is created when any label, split, timing or image fails.
    output.mkdir(parents=True, exist_ok=False)
    for record, (array, details) in zip(prepared, payloads):
        folder = output / record["sample_id"]
        folder.mkdir()
        array_path, frames_path = folder / "frames.npy", folder / "frames.json"
        np.save(array_path, array, allow_pickle=False)
        pp.write_json(frames_path, details)
        record.update(array_path=str(array_path.relative_to(root)), frames_path=str(frames_path.relative_to(root)),
                      array_sha256=pp.sha256(array_path), frames_sha256=pp.sha256(frames_path))
    pp.write_json(output / "prepared_inputs.json", prepared)
    report = {"status": "source_annotation_inputs_prepared", "purpose": policy["purpose"],
        "counts": {"source_annotation_accident": len(prepared), "normal_created": 0, "human_reviews_created": 0},
        "policy_version": ANNOTATION_POLICY, "samples": prepared,
        "input_sha256": {str(p.relative_to(root)): sha for p, sha in hashes.items()},
        "inputs_unchanged": True, "source_video_rechecked": False, "video_frames_decoded": 0,
        "training_run": False, "training_execution_authorized": False,
        "independent_evaluation_ready": False, "code_sha256": pp.sha256(Path(__file__)),
        "duration_seconds": time.monotonic()-started}
    pp.write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true", help="quality mode: read-only check; exit 3 for pending, 2 for issues/errors, 0 for ready")
    parser.add_argument("--input-quality", choices=("accident", "qmul"),
                        help="record quality of existing checked inputs without changing labels; --manifest is the link report")
    parser.add_argument("--source-annotation-policy", type=Path,
                        help="explicit training-only CSV-point policy; reuse saved PNGs, no human review fabrication")
    args = parser.parse_args()
    try:
        require(not args.check_only or args.input_quality is not None, "check_only_requires_input_quality")
        require(args.check_only or args.output is not None, "output_required_for_recording")
        if args.source_annotation_policy:
            require(not args.input_quality and not args.answers and not args.check_only, "annotation_mode_conflict")
            report = run_source_annotations(args.plan, args.manifest, args.source_annotation_policy, args.output)
        else:
            report = (run_quality(args.plan, args.manifest, args.output, args.answers, args.input_quality, check_only=args.check_only)
                      if args.input_quality else run(args.plan, args.manifest, args.output, args.answers))
        summary = {k: report[k] for k in ("status", "counts", "training_run")}
        if args.check_only:
            summary.update(check_only=True, output_written=False, labels_changed=False,
                           samples=[{k: row[k] for k in ("sample_id", "approved_target", "quality_status",
                                                         "unanswered_fields", "reported_issues")} for row in report["samples"]])
        print(json.dumps(summary, ensure_ascii=False))
        if args.check_only:
            return {"ready_to_record": 0, "awaiting_review": 3, "needs_attention": 2}[report["status"]]
        return 0
    except pp.PreprocessingError as exc:
        reason = str(exc)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        reason = "invalid_or_unreadable_input"
    print(json.dumps({"status": "invalid_request", "reason": reason, "training_run": False}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
