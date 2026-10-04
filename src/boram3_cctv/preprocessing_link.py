"""Audit prepared pixels and reviewed labels. Never build or run a model."""
from pathlib import Path
from copy import deepcopy
import math

from . import preprocessing as pp


def require(condition, reason):
    if not condition:
        raise pp.PreprocessingError(reason)


def contained(root, relative):
    require(isinstance(relative, str) and bool(relative), "missing_artifact_path")
    path = root / relative
    require(not Path(relative).is_absolute() and path.resolve().is_relative_to(root.resolve()),
            "artifact_path_outside_output")
    return path


def indexed(records):
    result = {r["sample_id"]: r for r in records}
    require(len(result) == len(records), "duplicate_artifact_sample_id")
    return result


def verify_saved_preview_link(plan_path, reference, settings, output, quality_path):
    """Reuse exactly the reviewed PNGs; audit the loader without resizing/decoding.

    A different configured training size remains unverified. Human quality answers
    supplement, never replace, the original segment label review.
    """
    import numpy as np
    import torch
    from PIL import Image
    from .training import MEAN, STD, SegmentDataset

    plan = pp.read_json(plan_path)
    pp.validate_plan(plan)
    old = pp.read_json(reference / "manifest.json")
    quality = pp.read_json(quality_path)
    require(quality.get("parent_review_plan_sha256") == pp.sha256(plan_path), "quality_parent_mismatch")
    require(quality.get("scope") == "same_reviewed_inputs_only" and
            quality.get("source") == "user_message" and bool(quality.get("raw_response")), "invalid_quality_scope")
    require(quality.get("crop_or_missing_problem") is False and quality.get("needs_more_context") is False,
            "human_context_quality_unconfirmed")
    ids = {s["sample_id"] for s in plan["samples"]}
    require(isinstance(quality.get("sample_ids"), list) and len(quality["sample_ids"]) == len(ids)
            and set(quality["sample_ids"]) == ids, "quality_sample_selection_mismatch")
    require(plan.get("parent_manifest_sha256") == pp.sha256(reference / "manifest.json"), "reviewed_manifest_changed")
    require(plan.get("parent_plan_sha256") == old.get("plan_sha256"), "reviewed_parent_plan_mismatch")
    require(old.get("preprocessing_version") == pp.VERSION, "unknown_preview_layout_version")
    expected_settings = {k: plan[k] for k in ("frame_count", "image_size", "policy_version", "time_basis")}
    require(old["settings"] == expected_settings and old["class_order"] == pp.CLASS_ORDER,
            "reference_settings_mismatch")
    require(old.get("inputs_unchanged") is True, "reference_inputs_changed")
    previous = indexed(old["samples"])
    require(set(previous) == ids, "sample_selection_mismatch")
    require(not output.exists(), "output_already_exists_choose_new_directory")
    output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(1)
    loader_settings = {**settings, "frame_count": plan["frame_count"], "image_size": plan["image_size"]}
    config_matches = all(settings[k] == plan[k] for k in ("frame_count", "image_size"))
    results = []
    for sample in plan["samples"]:
        sid = sample["sample_id"]
        target = pp.approved_target(sample, plan["policy_version"], sample["source_sha256"])
        require(target is not None, "segment_label_unconfirmed")
        # Even a failed diagnostic preserves the approved label separately.
        result = {"sample_id": sid, "approved_target": target, "label_review": sample["label_review"],
                  "training_authorized": False, "model_input_human_approval": False}
        try:
            item = previous[sid]
            for key in ("source_sha256", "start_seconds", "end_seconds", "source_group_id", "upstream_original_id", "event_id"):
                require(item.get(key) == sample.get(key), "reference_provenance_mismatch:" + key)
            require(item["source_path"] == sample["video_path"] and
                    item["source_video_id"] == "sha256:" + sample["source_sha256"], "source_identity_mismatch")
            require(item["official_splits"] == sample["official_splits"], "reviewed_official_split_mismatch")
            require(item.get("original_unchanged") is True and item["time_basis"] == plan["time_basis"], "reference_inputs_changed")
            require(sample.get("training_authorized") is False, "unexpected_training_authorization")
            preview_path = contained(reference, item["preview_path"])
            require(pp.sha256(preview_path) == sample["human_submission"]["preview_sha256"], "reviewed_preview_changed")
            frames_path = contained(reference, item["frames_metadata_path"])
            details = pp.read_json(frames_path)
            require(details == item["processed_frames"], "reviewed_frame_metadata_changed")
            require(details["transform"].get("crop") is None, "unexpected_spatial_crop")
            pp.validate_window(sample)
            times = [pp.number(f["actual_seconds"]) for f in details["frames"]]
            count, size = plan["frame_count"], plan["image_size"]
            require(len(times) == count and all(sample["start_seconds"] <= t < sample["end_seconds"] for t in times)
                    and all(b > a for a, b in zip(times, times[1:])), "frame_order_or_range_mismatch")
            pixels, png_hashes = [], []
            # Layout contract of sample-preprocess-0.1's existing save_frames().
            with Image.open(preview_path) as sheet:
                require(sheet.size == (4 * size, 44 + math.ceil(count / 4) * (size + 40)), "preview_layout_mismatch")
                for index in range(count):
                    png = contained(frames_path.parent, f"frames/{index:03d}.png")
                    with Image.open(png) as image:
                        require(image.mode == "RGB" and image.size == (size, size), "reviewed_frame_shape_mismatch")
                        frame = np.array(image)
                    x, y = (index % 4) * size, 44 + (index // 4) * (size + 40)
                    require(np.array_equal(frame, np.asarray(sheet.crop((x, y, x + size, y + size)))),
                            "preview_display_and_png_order_mismatch")
                    pixels.append(frame)
                    png_hashes.append(pp.sha256(png))
            array = np.stack(pixels)
            folder = output / sid
            folder.mkdir()
            array_path, copied_frames_path = folder / "frames.npy", folder / "frames.json"
            np.save(array_path, array, allow_pickle=False)
            copied = deepcopy(details)
            copied.update(target=target, model_input_written=True, purpose="review_size_loader_diagnostic_only")
            pp.write_json(copied_frames_path, copied)
            record = {**item, "target": target, "array_path": array_path, "frames_path": copied_frames_path,
                      "array_sha256": pp.sha256(array_path), "frames_sha256": pp.sha256(copied_frames_path)}
            tensor, read_target, read_sid = SegmentDataset([record], loader_settings)[0]
            restored = ((tensor.numpy() * np.array(STD, dtype=np.float32)[:, None, None, None]
                         + np.array(MEAN, dtype=np.float32)[:, None, None, None]) * 255).transpose(1, 2, 3, 0)
            require(tensor.device.type == "cpu" and tensor.dtype == torch.float32 and
                    np.isfinite(restored).all() and np.array_equal(np.rint(restored).astype(np.uint8), array),
                    "loader_pixels_or_temporal_axis_changed")
            require(read_target == target and read_sid == sid, "dataset_label_or_id_mismatch")
            result.update(status="passed", loader_target=read_target, source_video_id=item["source_video_id"],
                source_path=item["source_path"], interval=[sample["start_seconds"], sample["end_seconds"]],
                actual_frame_seconds=times, tensor_shape=list(tensor.shape), tensor_dtype="float32", device="cpu",
                png_sha256=png_hashes, array_sha256=record["array_sha256"], frames_sha256=record["frames_sha256"],
                official_splits=item["official_splits"], source_group_id=sample.get("source_group_id"), event_id=sample.get("event_id"),
                technical_order="verified_same_pngs_pts_and_tensor", preview_display_order="verified_left_to_right_top_to_bottom",
                human_quality="reported_no_crop_missing_or_extra_context", configured_training_input_verified=config_matches)
        except Exception as exc:
            result.update(status="needs_attention", reason=str(exc) if isinstance(exc, pp.PreprocessingError) else type(exc).__name__)
        results.append(result)
    return {"status": "passed" if all(r["status"] == "passed" for r in results) else "needs_attention",
        "purpose": "reuse_reviewed_pngs_loader_diagnostic", "samples": results,
        "reviewed_input_settings": expected_settings, "configured_training_settings": settings,
        "configured_training_input_verified": config_matches and all(r["status"] == "passed" for r in results),
        "quality_followup": quality, "quality_followup_sha256": pp.sha256(quality_path),
        "decoded_frame_count": 0, "new_preview_images": 0, "source_video_rechecked": False,
        "training_run": False, "model_created": False, "performance_evaluation": False}


def configured_size_plan(reviewed_plan, reference, settings):
    """Derive only a smaller spatial size; keep segment labels and review evidence."""
    pp.validate_plan(reviewed_plan)
    old = pp.read_json(reference / "manifest.json")
    require(reviewed_plan.get("parent_manifest_sha256") == pp.sha256(reference / "manifest.json"),
            "reviewed_manifest_changed")
    require(reviewed_plan.get("parent_plan_sha256") == old.get("plan_sha256"), "reviewed_parent_plan_mismatch")
    require(old.get("inputs_unchanged") is True and old.get("preprocessing_version") == pp.VERSION,
            "unverified_reference")
    require(old["settings"] == {k: reviewed_plan[k] for k in
            ("frame_count", "image_size", "policy_version", "time_basis")}, "reference_settings_mismatch")
    require(settings["frame_count"] == reviewed_plan["frame_count"], "temporal_settings_change_not_allowed")
    require(type(settings["image_size"]) is int and 32 <= settings["image_size"] < reviewed_plan["image_size"],
            "expected_smaller_configured_size")
    previous = indexed(old["samples"])
    require(set(previous) == {s["sample_id"] for s in reviewed_plan["samples"]}, "sample_selection_mismatch")
    for sample in reviewed_plan["samples"]:
        require(pp.approved_target(sample, reviewed_plan["policy_version"], sample["source_sha256"]) is not None,
                "segment_label_unconfirmed")
        require(sample.get("training_authorized") is False, "unexpected_training_authorization")
        prior = previous[sample["sample_id"]]
        require(pp.sha256(contained(reference, prior["preview_path"])) == sample["human_submission"]["preview_sha256"],
                "reviewed_preview_changed")
        require(pp.read_json(contained(reference, prior["frames_metadata_path"])) == prior["processed_frames"],
                "reviewed_frame_metadata_changed")
    derived = deepcopy(reviewed_plan)
    derived.update(image_size=settings["image_size"], purpose="configured_size_link_check_only")
    pp.validate_plan(derived)
    return derived


def verify_link(plan, prepared, reference, settings, *, reviewed_plan=None):
    """Read back existing SegmentDataset on CPU, without the training loop.

    Reference must be the output actually reviewed by the human. Equality proves
    reproducibility, not independent accident recognition or training readiness.
    """
    import numpy as np
    import torch
    from PIL import Image
    from .training import MEAN, STD, SegmentDataset

    pp.validate_plan(plan)
    torch.set_num_threads(1)
    manifest = pp.read_json(prepared / "manifest.json")
    old = pp.read_json(reference / "manifest.json")
    require(manifest["class_order"] == old["class_order"] == pp.CLASS_ORDER, "class_order_mismatch")
    expected_settings = {k: plan[k] for k in ("frame_count", "image_size", "policy_version", "time_basis")}
    resized = reviewed_plan is not None
    if resized:
        require(plan == configured_size_plan(reviewed_plan, reference, settings), "unexpected_derived_plan_change")
        require(manifest["settings"] == expected_settings, "prepared_settings_mismatch")
    else:
        require(manifest["settings"] == old["settings"] == expected_settings, "reference_settings_mismatch")
    require(all(settings[k] == plan[k] for k in ("frame_count", "image_size")), "model_input_settings_mismatch")
    require(manifest["inputs_unchanged"] is True, "preprocessing_inputs_changed")
    current, previous = indexed(manifest["samples"]), indexed(old["samples"])
    require(set(current) == set(previous) == {s["sample_id"] for s in plan["samples"]}, "sample_selection_mismatch")
    results = []
    for sample in plan["samples"]:
        sid = sample["sample_id"]
        item, prior = current[sid], previous[sid]
        result = {"sample_id": sid, "target": None, "training_authorized": False}
        try:
            target = pp.approved_target(sample, plan["policy_version"], sample["source_sha256"])
            result.update(approved_target=target, label_review=sample["label_review"])
            require(item["status"] == "prepared_labeled", "sample_not_prepared:" + item.get("reason", item["status"]))
            require(target is not None and type(item["target"]) is int and item["target"] == target,
                    "approved_target_mismatch")
            require(item["label_review"] == sample["label_review"], "review_evidence_mismatch")
            require(item["training_authorized"] is False and item["independent_evaluation_ready"] is False,
                    "unexpected_training_or_evaluation_authorization")
            for key in ("source_sha256", "start_seconds", "end_seconds", "upstream_original_id", "event_id", "source_group_id"):
                require(item.get(key) == prior.get(key) == sample.get(key), "reference_provenance_mismatch:" + key)
            for key in ("source_video_id", "source_path", "official_splits", "time_basis"):
                require(item[key] == prior[key], "reference_provenance_mismatch:" + key)
            require(item["source_path"] == sample["video_path"] and
                    item["source_video_id"] == "sha256:" + sample["source_sha256"], "source_identity_mismatch")
            require(item["official_splits"] == sample["official_splits"], "reviewed_official_split_mismatch")
            array_path = contained(prepared, item["input_path"])
            frames_path = contained(prepared, item["frames_metadata_path"])
            old_frames_path = contained(reference, prior["frames_metadata_path"])
            details, old_details = pp.read_json(frames_path), pp.read_json(old_frames_path)
            require(details["frames"] == old_details["frames"], "reviewed_frame_times_changed")
            if not resized:
                require(details["transform"] == old_details["transform"], "reviewed_spatial_transform_changed")
            record = {**item, "array_path": array_path, "frames_path": frames_path,
                      "array_sha256": pp.sha256(array_path), "frames_sha256": pp.sha256(frames_path)}
            tensor, read_target, read_sid = SegmentDataset([record], settings)[0]
            array = np.load(array_path, mmap_mode="r", allow_pickle=False)
            png_hashes = []
            preview_path = contained(prepared, item["preview_path"]) if resized else None
            if resized:
                require(details == item["processed_frames"], "prepared_frame_metadata_changed")
                with Image.open(preview_path) as sheet:
                    size, count = plan["image_size"], plan["frame_count"]
                    require(sheet.size == (4 * size, 44 + math.ceil(count / 4) * (size + 40)), "preview_layout_mismatch")
                    for index in range(count):
                        x, y = (index % 4) * size, 44 + (index // 4) * (size + 40)
                        require(np.array_equal(array[index], np.asarray(sheet.crop((x, y, x + size, y + size)))),
                                "preview_display_and_array_order_mismatch")
            for index in range(plan["frame_count"]):
                new_png = frames_path.parent / "frames" / f"{index:03d}.png"
                old_png = old_frames_path.parent / "frames" / f"{index:03d}.png"
                # Images and time metadata, not the changed approval banner, are compared.
                with Image.open(new_png) as image, Image.open(old_png) as old_image:
                    require(np.array_equal(array[index], np.asarray(image)), "array_preview_pixels_differ")
                    if not resized:
                        require(np.array_equal(array[index], np.asarray(old_image)), "reviewed_pixels_changed")
                png_hashes.append(pp.sha256(new_png))
            with Image.open(frames_path.parent / "source_reference.png") as image, Image.open(old_frames_path.parent / "source_reference.png") as old_image:
                require(np.array_equal(np.asarray(image), np.asarray(old_image)), "source_reference_changed")
                if resized:
                    # Resizing the 512 PNG again would change the approved transform.
                    # Anchor both sizes to the same original-resolution reference frame.
                    expected_image, transform = pp.letterbox(image, plan["image_size"])
                    old_expected, old_transform = pp.letterbox(old_image, reviewed_plan["image_size"])
                    require(details["transform"] == transform and old_details["transform"] == old_transform,
                            "unexpected_spatial_transform")
                    index = details["source_reference_frame_index"]
                    require(index == old_details["source_reference_frame_index"] == plan["frame_count"] // 2,
                            "source_reference_index_changed")
                    require(np.array_equal(array[index], np.asarray(expected_image)), "source_resize_pixels_differ")
                    with Image.open(old_frames_path.parent / "frames" / f"{index:03d}.png") as old_png:
                        require(np.array_equal(np.asarray(old_png), np.asarray(old_expected)), "reviewed_source_pixels_differ")
            actual = tensor.numpy()
            expected = array.transpose(3, 0, 1, 2).astype(np.float32) / np.float32(255)
            expected = (expected - np.array(MEAN, dtype=np.float32)[:, None, None, None]) / np.array(STD, dtype=np.float32)[:, None, None, None]
            require(tensor.device.type == "cpu" and tensor.dtype == torch.float32 and
                    np.isfinite(actual).all() and np.allclose(actual, expected, rtol=0, atol=1e-6),
                    "model_input_transform_mismatch")
            require(read_target == target and read_sid == sid, "dataset_label_or_id_mismatch")
            times = [f["actual_seconds"] for f in details["frames"]]
            result.update(status="passed", target=target, source_video_id=item["source_video_id"],
                          interval=[item["start_seconds"], item["end_seconds"]],
                          actual_first_last_seconds=[times[0], times[-1]],
                          array_shape=list(array.shape), tensor_shape=list(tensor.shape),
                          tensor_dtype="float32", actual_tensor_device=str(tensor.device),
                          normalized_min_max=[float(tensor.min()), float(tensor.max())],
                          array_sha256=record["array_sha256"], frames_sha256=record["frames_sha256"],
                          png_sha256=png_hashes, reviewed_pixels_and_times_equal=not resized,
                          reviewed_frame_times_equal=True, actual_frame_seconds=times,
                          official_splits=item["official_splits"], label_review=item["label_review"],
                          decoded_frame_count_including_preroll=item["media"]["decoded_frame_count_including_preroll"])
            if resized:
                result.update(spatial_transform_changed=True, transform=details["transform"],
                              preview_path=item["preview_path"], preview_sha256=pp.sha256(preview_path),
                              configured_training_input_verified=True, model_input_human_approval=False,
                              human_visibility_status="pending_at_configured_size",
                              source_group_id=sample.get("source_group_id"), event_id=sample.get("event_id"))
        except Exception as exc:
            # Diagnostic failure remains separate from either binary class.
            result.update(status="needs_attention", reason=str(exc) if isinstance(exc, pp.PreprocessingError)
                          else type(exc).__name__)
        results.append(result)
    passed = all(r["status"] == "passed" for r in results)
    return {"status": "passed" if passed else "needs_attention",
            "purpose": "reviewed_input_label_link_only", "samples": results,
            "training_run": False, "model_created": False, "performance_evaluation": False,
            "configured_training_input_verified": passed,
            "human_visibility_status": "pending_at_configured_size" if resized else "unchanged_reviewed_size",
            "preview_note": ("Source hashes/intervals/PTS/labels match. Spatial pixels differ by explicit resize; human visibility needs review."
                             if resized else "Frame pixels/PTS match the human-reviewed output; approval banner differs intentionally.")}
