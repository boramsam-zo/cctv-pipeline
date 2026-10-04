"""Small generated pixels test linkage failures; never ACCIDENT labels/model quality."""
import copy
from argparse import Namespace
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

from scripts import check_preprocessing_link as cli
from boram3_cctv import preprocessing as pp
from boram3_cctv.preprocessing_link import configured_size_plan, verify_link, verify_saved_preview_link


class LinkTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.reference, self.prepared = self.base / "reference", self.base / "prepared"
        self.settings = {"frame_count": 4, "image_size": 32}
        review = {"status": "approved", "scope": "segment", "label": "accident",
                  "source_sha256": "a" * 64, "start_seconds": 0.0, "end_seconds": 2.0,
                  "policy_version": "fixture-policy", "reviewer": "software-test",
                  "reviewed_at": "2026-09-20", "basis": "generated fixture only"}
        self.sample = {"sample_id": "sample_001", "source_sha256": "a" * 64,
                       "start_seconds": 0.0, "end_seconds": 2.0, "video_path": "real_videos/fixture.mp4",
                       "label_review": review,
                       "official_splits": {"split_in_distribution": "test", "split_geo_aware": "test"}}
        self.plan = {"schema_version": 1, "policy_version": "fixture-policy", "time_basis": "local_clip_seconds",
                     **self.settings, "samples": [self.sample]}
        frames = [{"image": Image.fromarray(np.full((16, 32, 3), 30 + i * 40, dtype=np.uint8)),
                   "actual_seconds": 0.25 + i * 0.5, "requested_seconds": 0.25 + i * 0.5,
                   "pts": 1 + i * 2, "pts_time_base": "1/4"} for i in range(4)]
        for root, target in [(self.reference, None), (self.prepared, 1)]:
            pp.save_frames(root / "sample_001", frames, 32, target, "sample_001")
            item = {**self.sample, "status": "unconfirmed" if target is None else "prepared_labeled",
                    "target": target, "source_video_id": "sha256:" + "a" * 64,
                    "source_path": self.sample["video_path"], "time_basis": "local_clip_seconds",
                    "training_authorized": False, "independent_evaluation_ready": False,
                    "frames_metadata_path": "sample_001/frames.json", "input_path": "sample_001/frames.npy",
                    "media": {"decoded_frame_count_including_preroll": 4}}
            pp.write_json(root / "manifest.json", {"class_order": pp.CLASS_ORDER, "inputs_unchanged": True,
                          "settings": {k: self.plan[k] for k in ("frame_count", "image_size", "policy_version", "time_basis")},
                          "samples": [item]})

    def verify(self):
        return verify_link(self.plan, self.prepared, self.reference, self.settings)

    def change_json(self, path, edit):
        value = pp.read_json(path)
        edit(value)
        path.write_text(json.dumps(value), encoding="utf-8")  # Mutate only temporary test fixtures.

    def assert_held(self, reason):
        report = self.verify()
        self.assertEqual(report["status"], "needs_attention")
        self.assertIsNone(report["samples"][0]["target"])
        self.assertIn(reason, report["samples"][0]["reason"])
        self.assertFalse(report["training_run"])

    def test_readback_matches_pixels_times_label_and_cpu_tensor(self):
        report = self.verify()
        self.assertEqual(report["status"], "passed")
        result = report["samples"][0]
        self.assertEqual(result["target"], 1)
        self.assertEqual(result["tensor_shape"], [3, 4, 32, 32])
        self.assertEqual(result["actual_tensor_device"], "cpu")
        self.assertTrue(result["reviewed_pixels_and_times_equal"])
        self.assertFalse(result["training_authorized"])

    def test_changed_reviewed_frame_requires_attention(self):
        from PIL import Image
        Image.new("RGB", (32, 32), "red").save(self.reference / "sample_001/frames/001.png")
        self.assert_held("reviewed_pixels_changed")

    def test_array_frame_reordering_is_rejected(self):
        import numpy as np
        path = self.prepared / "sample_001/frames.npy"
        np.save(path, np.load(path)[::-1])
        self.assert_held("array_preview_pixels_differ")

    def test_pts_change_requires_new_review(self):
        self.change_json(self.prepared / "sample_001/frames.json",
                         lambda d: d["frames"][0].update(actual_seconds=0.3))
        self.assert_held("reviewed_frame_times_changed")

    def test_nonincreasing_times_are_rejected_even_if_reference_matches(self):
        for root in (self.prepared, self.reference):
            self.change_json(root / "sample_001/frames.json",
                             lambda d: d["frames"][1].update(actual_seconds=0.25))
        self.assert_held("TrainingError")

    def test_missing_array_is_not_normal(self):
        (self.prepared / "sample_001/frames.npy").unlink()
        self.assert_held("FileNotFoundError")

    def test_pending_sample_is_not_given_a_target(self):
        self.change_json(self.prepared / "manifest.json", lambda d: d["samples"][0].update(status="unconfirmed", target=None))
        self.assert_held("sample_not_prepared")

    def test_target_or_official_split_cannot_change(self):
        path = self.prepared / "manifest.json"
        original = pp.read_json(path)
        for changes, reason in [({"target": 0}, "approved_target_mismatch"),
                                ({"official_splits": {"split_in_distribution": "train"}}, "reference_provenance_mismatch"),
                                ({"training_authorized": True}, "unexpected_training")]:
            with self.subTest(changes=changes):
                value = copy.deepcopy(original)
                value["samples"][0].update(changes)
                path.write_text(json.dumps(value), encoding="utf-8")
                self.assert_held(reason)

    def test_selection_and_settings_must_match_reference(self):
        self.settings["image_size"] = 64
        with self.assertRaisesRegex(pp.PreprocessingError, "model_input_settings_mismatch"):
            self.verify()

    def test_artifact_paths_cannot_escape_output(self):
        self.change_json(self.prepared / "manifest.json", lambda d: d["samples"][0].update(input_path="../elsewhere.npy"))
        self.assert_held("artifact_path_outside_output")

    def test_existing_output_is_protected_before_any_worker(self):
        args = Mock(output=self.prepared)
        with self.assertRaisesRegex(pp.PreprocessingError, "output_already_exists"), patch.object(cli, "bounded_worker") as worker:
            cli.run_check(args)
        worker.assert_not_called()

    def test_timeout_kills_worker_process_group_and_is_explicit(self):
        process = Mock(pid=999999, returncode=-9)
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 180), -9]
        with patch.object(cli.subprocess, "Popen", return_value=process), patch.object(cli.os, "killpg") as kill:
            code, timed_out = cli.bounded_worker(["fixture"], None, None)
        self.assertTrue(timed_out)
        self.assertEqual(code, -9)
        kill.assert_called_once_with(999999, signal.SIGKILL)


class SavedPreviewLinkTests(unittest.TestCase):
    # Reuse the existing generated pixel fixture, without rerunning its test methods.
    change_json = LinkTests.change_json

    def setUp(self):
        LinkTests.setUp(self)
        self.sample["training_authorized"] = False
        self.sample["human_submission"] = {"preview_sha256": pp.sha256(self.reference / "sample_001/preview.png")}
        self.plan_path, self.quality_path = self.base / "plan.json", self.base / "quality.json"
        old = pp.read_json(self.reference / "manifest.json")
        old.update(preprocessing_version=pp.VERSION, plan_sha256="b" * 64)
        old["samples"][0].update(original_unchanged=True,
                                preview_path="sample_001/preview.png",
                                processed_frames=pp.read_json(self.reference / "sample_001/frames.json"))
        (self.reference / "manifest.json").write_text(json.dumps(old))
        self.plan.update(parent_plan_sha256="b" * 64, parent_manifest_sha256=pp.sha256(self.reference / "manifest.json"))
        pp.write_json(self.plan_path, self.plan)
        self.quality = {"parent_review_plan_sha256": pp.sha256(self.plan_path), "scope": "same_reviewed_inputs_only",
                        "sample_ids": ["sample_001"], "source": "user_message", "raw_response": "fixture quality response",
                        "crop_or_missing_problem": False, "needs_more_context": False}
        pp.write_json(self.quality_path, self.quality)

    def verify(self):
        return verify_saved_preview_link(self.plan_path, self.reference, self.settings,
                                         self.base / "diagnostic", self.quality_path)

    def rebind_fixture(self):
        self.plan["parent_manifest_sha256"] = pp.sha256(self.reference / "manifest.json")
        self.plan_path.write_text(json.dumps(self.plan))
        self.quality["parent_review_plan_sha256"] = pp.sha256(self.plan_path)
        self.quality_path.write_text(json.dumps(self.quality))

    def test_reuses_reviewed_pngs_with_existing_loader_and_no_decoder(self):
        with patch.object(pp, "sample_frames", side_effect=AssertionError("decoder must not run")):
            report = self.verify()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["samples"][0]["loader_target"], 1)
        self.assertEqual(report["samples"][0]["tensor_shape"], [3, 4, 32, 32])
        self.assertEqual(report["samples"][0]["label_review"], self.sample["label_review"])
        self.assertEqual(report["decoded_frame_count"], 0)
        self.assertFalse(report["training_run"])
        self.assertFalse(list((self.base / "diagnostic").rglob("*.png")))

    def test_review_size_does_not_silently_become_configured_training_size(self):
        self.settings = {"image_size": 64, "frame_count": 4}
        report = self.verify()
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["configured_training_input_verified"])
        self.assertEqual(report["samples"][0]["tensor_shape"], [3, 4, 32, 32])

    def test_swapped_pngs_are_detected_against_reviewed_contact_sheet(self):
        a, b = self.reference / "sample_001/frames/000.png", self.reference / "sample_001/frames/001.png"
        first, second = a.read_bytes(), b.read_bytes()
        a.write_bytes(second); b.write_bytes(first)
        report = self.verify()
        self.assertEqual(report["status"], "needs_attention")
        self.assertEqual(report["samples"][0]["reason"], "preview_display_and_png_order_mismatch")
        self.assertEqual(report["samples"][0]["approved_target"], 1)

    def test_changed_preview_is_not_treated_as_reviewed(self):
        (self.reference / "sample_001/preview.png").write_bytes(b"changed")
        report = self.verify()
        self.assertEqual(report["samples"][0]["reason"], "reviewed_preview_changed")

    def test_changed_frame_timestamps_are_rejected(self):
        self.change_json(self.reference / "sample_001/frames.json", lambda d: d["frames"][0].update(actual_seconds=1.9))
        report = self.verify()
        self.assertEqual(report["samples"][0]["reason"], "reviewed_frame_metadata_changed")

    def test_consistently_recorded_reversed_times_are_still_rejected(self):
        details_path = self.reference / "sample_001/frames.json"
        self.change_json(details_path, lambda d: d["frames"].reverse())
        self.change_json(self.reference / "manifest.json", lambda d: d["samples"][0].update(processed_frames=pp.read_json(details_path)))
        self.rebind_fixture()
        report = self.verify()
        self.assertEqual(report["samples"][0]["reason"], "frame_order_or_range_mismatch")

    def test_identity_failure_keeps_original_approved_accident(self):
        self.plan["samples"][0]["video_path"] = "real_videos/other.mp4"
        self.rebind_fixture()
        report = self.verify()
        self.assertEqual(report["samples"][0]["reason"], "source_identity_mismatch")
        self.assertEqual(report["samples"][0]["approved_target"], 1)
        self.assertEqual(report["samples"][0]["label_review"]["status"], "approved")

    def test_missing_png_is_an_error_not_a_normal_target(self):
        (self.reference / "sample_001/frames/001.png").unlink()
        report = self.verify()
        self.assertEqual(report["status"], "needs_attention")
        self.assertEqual(report["samples"][0]["approved_target"], 1)
        self.assertNotIn("loader_target", report["samples"][0])

    def test_unconfirmed_or_wrong_scope_quality_is_not_invented(self):
        for key, value in [("crop_or_missing_problem", None), ("needs_more_context", None),
                           ("parent_review_plan_sha256", "a" * 64), ("sample_ids", ["sample_002"])]:
            changed = {**self.quality, key: value}
            self.quality_path.write_text(json.dumps(changed))
            with self.subTest(key=key), self.assertRaises(pp.PreprocessingError):
                self.verify()

    def test_original_review_parent_and_output_are_protected(self):
        self.change_json(self.reference / "manifest.json", lambda d: d.update(training_run=True))
        with self.assertRaisesRegex(pp.PreprocessingError, "reviewed_manifest_changed"):
            self.verify()


class ConfiguredSizeLinkTests(unittest.TestCase):
    """Only generated pixels: prove one explicit spatial change, not visibility."""
    change_json = LinkTests.change_json

    def setUp(self):
        import numpy as np
        from PIL import Image
        SavedPreviewLinkTests.setUp(self)
        old = pp.read_json(self.reference / "manifest.json")
        self.reference = self.base / "reference64"
        frames = [{"image": Image.fromarray(np.full((16, 32, 3), 30 + i * 40, dtype=np.uint8)),
                   "actual_seconds": 0.25 + i * 0.5, "requested_seconds": 0.25 + i * 0.5,
                   "pts": 1 + i * 2, "pts_time_base": "1/4"} for i in range(4)]
        details = pp.save_frames(self.reference / "sample_001", frames, 64, None, "sample_001")
        old["settings"]["image_size"] = 64
        old["samples"][0]["processed_frames"] = details
        pp.write_json(self.reference / "manifest.json", old)
        self.plan["image_size"] = 64
        self.plan["parent_manifest_sha256"] = pp.sha256(self.reference / "manifest.json")
        self.sample["human_submission"]["preview_sha256"] = pp.sha256(self.reference / "sample_001/preview.png")
        self.derived = configured_size_plan(self.plan, self.reference, self.settings)
        self.change_json(self.prepared / "manifest.json", lambda d: d["samples"][0].update(
            preview_path="sample_001/preview.png", processed_frames=pp.read_json(self.prepared / "sample_001/frames.json")))

    def verify(self):
        return verify_link(self.derived, self.prepared, self.reference, self.settings, reviewed_plan=self.plan)

    def assert_held(self, reason):
        report = self.verify()
        self.assertEqual(report["status"], "needs_attention")
        self.assertFalse(report["configured_training_input_verified"])
        item = report["samples"][0]
        self.assertIsNone(item["target"])
        self.assertEqual(item["approved_target"], 1)
        self.assertEqual(item["label_review"], self.sample["label_review"])
        self.assertIn(reason, item["reason"])

    def test_same_pts_and_label_with_explicit_resize_leaves_visibility_pending(self):
        original = copy.deepcopy(self.plan)
        report = self.verify()
        self.assertEqual(self.plan, original)
        self.assertEqual(self.derived["samples"], self.plan["samples"])
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["training_run"])
        self.assertTrue(report["configured_training_input_verified"])
        item = report["samples"][0]
        self.assertEqual(item["tensor_shape"], [3, 4, 32, 32])
        self.assertEqual(item["target"], 1)
        self.assertEqual(item["official_splits"]["split_in_distribution"], "test")
        self.assertTrue(item["reviewed_frame_times_equal"])
        self.assertFalse(item["reviewed_pixels_and_times_equal"])
        self.assertFalse(item["model_input_human_approval"])
        self.assertEqual(item["human_visibility_status"], "pending_at_configured_size")

    def test_size_change_requires_explicit_mode(self):
        with self.assertRaisesRegex(pp.PreprocessingError, "reference_settings_mismatch"):
            verify_link(self.derived, self.prepared, self.reference, self.settings)

    def test_temporal_change_or_unexpected_size_is_rejected(self):
        for changed in ({"frame_count": 8, "image_size": 32}, {"frame_count": 4, "image_size": 64},
                        {"frame_count": 4, "image_size": 16}):
            with self.subTest(changed=changed), self.assertRaises(pp.PreprocessingError):
                configured_size_plan(self.plan, self.reference, changed)

    def test_changed_interval_or_review_is_not_allowed_in_derived_plan(self):
        self.derived["samples"][0]["end_seconds"] = 2.1
        with self.assertRaisesRegex(pp.PreprocessingError, "unexpected_derived_plan_change"):
            self.verify()

    def test_changed_reference_or_preview_is_not_assumed_reviewed(self):
        (self.reference / "sample_001/preview.png").write_bytes(b"changed")
        with self.assertRaisesRegex(pp.PreprocessingError, "reviewed_preview_changed"):
            self.verify()
        self.change_json(self.reference / "manifest.json", lambda d: d.update(plan_sha256="c" * 64))
        with self.assertRaisesRegex(pp.PreprocessingError, "reviewed_manifest_changed"):
            self.verify()

    def test_no_automatic_target_for_unapproved_label(self):
        self.plan["samples"][0]["label_review"].update(status="unreviewed", label=None)
        with self.assertRaisesRegex(pp.PreprocessingError, "segment_label_unconfirmed"):
            configured_size_plan(self.plan, self.reference, self.settings)

    def test_changed_pts_keeps_label_but_rejects_input(self):
        self.change_json(self.prepared / "sample_001/frames.json", lambda d: d["frames"][0].update(actual_seconds=0.3))
        self.assert_held("reviewed_frame_times_changed")

    def test_reordered_array_is_rejected_against_new_preview(self):
        import numpy as np
        path = self.prepared / "sample_001/frames.npy"
        np.save(path, np.load(path)[::-1])
        self.assert_held("preview_display_and_array_order_mismatch")

    def test_spatial_crop_is_rejected_even_if_manifest_matches(self):
        self.change_json(self.prepared / "sample_001/frames.json", lambda d: d["transform"].update(crop=[0, 0, 16, 16]))
        self.change_json(self.prepared / "manifest.json", lambda d: d["samples"][0].update(
            processed_frames=pp.read_json(self.prepared / "sample_001/frames.json")))
        self.assert_held("unexpected_spatial_transform")

    def test_incorrect_pixels_shared_by_preview_and_array_fail_source_anchor(self):
        import numpy as np
        from PIL import Image
        folder = self.prepared / "sample_001"
        array = np.load(folder / "frames.npy")
        array[2] = 255
        np.save(folder / "frames.npy", array)
        image = Image.fromarray(array[2])
        image.save(folder / "frames/002.png")
        with Image.open(folder / "preview.png") as sheet:
            sheet.paste(image, (64, 44))
            sheet.save(folder / "preview.png")
        self.assert_held("source_resize_pixels_differ")

    def test_missing_array_keeps_approved_accident_separate(self):
        (self.prepared / "sample_001/frames.npy").unlink()
        self.assert_held("FileNotFoundError")

    def test_official_test_cannot_be_reassigned_to_train(self):
        self.change_json(self.prepared / "manifest.json", lambda d: d["samples"][0]["official_splits"].update(split_in_distribution="train"))
        self.assert_held("reference_provenance_mismatch")

    def test_configured_size_is_capped_at_60_seconds_and_15_per_video(self):
        self.assertEqual(cli.execution_limits(Namespace(reuse_reviewed_frames=False, configured_size=True)), (60, 15))
        self.assertEqual(cli.execution_limits(Namespace(reuse_reviewed_frames=True, configured_size=False)), (60, None))
        self.assertEqual(cli.execution_limits(Namespace(reuse_reviewed_frames=False, configured_size=False)), (180, 45))


if __name__ == "__main__":
    unittest.main()
