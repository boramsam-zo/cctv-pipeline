"""Connect one explicitly supplied external original to reviewed segment inputs.

Diagnostic preparation only. Does not alter ACCIDENT metadata or train a model.
"""
from pathlib import Path
import re
import time

from . import preprocessing as pp
from .preprocessing_link import require, contained, indexed

VERSION = "external-segment-link-0.1"


def validate_review(review, parent):
    require(isinstance(review, dict) and review.get("schema_version") == 1
            and review.get("review_purpose") == "human_review_record_only"
            and review.get("time_basis") == "local_file_pts_seconds"
            and review.get("interval") == "[start,end)"
            and review.get("class_order") == pp.CLASS_ORDER, "invalid_external_review")
    digest = review.get("source_sha256")
    require(isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest), "invalid_source_hash")
    require(isinstance(parent, dict) and parent.get("purpose") == "human_review_only"
            and parent.get("status") == "completed_unreviewed"
            and parent.get("inputs_unchanged") is True
            and parent.get("source_sha256") == digest
            and parent.get("source_filename") == review.get("source_filename"), "parent_identity_mismatch")
    samples = review.get("samples")
    require(isinstance(samples, list) and 1 <= len(samples) <= 3, "select_1_to_3_samples")
    parents = {s["segment_id"]: s for s in parent["segments"]}
    require(len(parents) == len(parent["segments"]), "duplicate_parent_id")
    ids = set()
    for sample in samples:
        sid = sample.get("sample_id")
        require(isinstance(sid, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", sid)
                and sid not in ids and sid in parents, "invalid_or_duplicate_sample_id")
        ids.add(sid)
        pp.validate_window(sample)
        require(sample.get("source_sha256") == digest, "sample_source_hash_mismatch")
        prior = parents[sid]
        require(prior.get("status") == "preview_ready_unreviewed", "parent_preview_failed")
        for key in ("start_seconds", "end_seconds", "source_video_id", "source_group_id", "official_split", "event_id"):
            require(key in sample and sample[key] == prior.get(key), "parent_provenance_mismatch:" + key)
        require(sample["source_video_id"] == "sha256:" + digest
                and sample["source_group_id"] == "local-file-sha256:" + digest,
                "external_same_file_group_required")
        target = pp.approved_target(sample, review["policy_version"], digest)
        require(sample.get("target") == target and (target is None or type(sample["target"]) is int),
                "declared_target_mismatch")
    return parents


def prepare_one(source, sample, policy, settings, prior, output):
    """Use the existing PTS sampler and letterbox, retaining the reviewed window."""
    began = time.monotonic()
    target = pp.approved_target(sample, policy, sample["source_sha256"])
    frames, media = pp.sample_frames(source, sample["start_seconds"], sample["end_seconds"],
                                     settings["frame_count"])
    reviewed_pts = {(r["source_pts"], r["source_time_base"]) for r in prior["frame_mapping"]}
    require(all((f["pts"], f["pts_time_base"]) in reviewed_pts for f in frames),
            "selected_frame_outside_reviewed_preview")
    details = pp.save_frames(output, frames, settings["image_size"], target, sample["sample_id"])
    record = {k: sample.get(k) for k in ("sample_id", "source_sha256", "source_video_id", "source_group_id",
                                       "source_group_scope", "official_split", "event_id", "start_seconds",
                                       "end_seconds", "label_review", "preview_review")}
    record.update(status="prepared_labeled_diagnostic" if target is not None else "unconfirmed",
                  target=target, time_basis="local_file_pts_seconds", interval="[start,end)",
                  media=media, processed_frames=details, elapsed_seconds=time.monotonic() - began,
                  original_frames_used=True, reviewed_mp4_used_as_model_input=False,
                  selected_pts_present_in_reviewed_preview=True,
                  model_input_approved=False, training_authorized=False, independent_evaluation_ready=False)
    pp.write_json(output / "provenance.json", record)
    return record


def verify_inputs(root, records, settings):
    """Read CPU tensors using SegmentDataset. No model/weights are constructed."""
    import numpy as np
    import torch
    from PIL import Image
    from .training import MEAN, STD, SegmentDataset
    torch.set_num_threads(1)
    indexed(records)
    verified = []
    for record in records:
        if record["status"] != "prepared_labeled_diagnostic":
            verified.append({"sample_id": record["sample_id"], "status": "not_loaded",
                             "target": None, "reason": record["status"]})
            continue
        array_path = contained(root, record["input_path"])
        frames_path = contained(root, record["frames_metadata_path"])
        item = {**record, "array_path": array_path, "frames_path": frames_path}
        tensor, target, sid = SegmentDataset([item], settings)[0]
        array = np.load(array_path, mmap_mode="r", allow_pickle=False)
        details = pp.read_json(frames_path)
        for index in range(settings["frame_count"]):
            with Image.open(frames_path.parent / "frames" / f"{index:03d}.png") as picture:
                require(np.array_equal(array[index], np.asarray(picture)), "array_preview_pixels_differ")
        expected = array.transpose(3, 0, 1, 2).astype(np.float32) / np.float32(255)
        expected = (expected - np.array(MEAN, dtype=np.float32)[:, None, None, None]) / np.array(STD, dtype=np.float32)[:, None, None, None]
        require(sid == record["sample_id"] and target == record["target"]
                and tensor.device.type == "cpu" and tensor.dtype == torch.float32
                and np.isfinite(tensor.numpy()).all()
                and np.allclose(tensor.numpy(), expected, atol=1e-6, rtol=0), "tensor_label_link_mismatch")
        verified.append({"sample_id": sid, "status": "passed", "target": target,
                         "array_shape": list(array.shape), "tensor_shape": list(tensor.shape),
                         "tensor_device": str(tensor.device), "tensor_dtype": "float32",
                         "preview_pixels_match_array": True,
                         "actual_first_last_seconds": [details["frames"][i]["actual_seconds"] for i in (0, -1)],
                         "model_input_human_approval": False})
    return verified
