"""Tiny software fixtures; no real traffic labels or training calls."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import av
import numpy as np
from PIL import Image

from scripts import check_external_preprocessing as cli
from boram3_cctv import external_preprocessing as ep
from boram3_cctv import preprocessing as pp
from boram3_cctv import review_preview as rp


class ExternalPreprocessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "originals").mkdir()
        self.source = self.root / "originals/fixture.avi"
        with av.open(str(self.source), "w") as c:
            stream = c.add_stream("mpeg4", rate=10)
            stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv420p"
            for i in range(40):
                frame = av.VideoFrame.from_ndarray(np.full((32, 64, 3), i * 5, dtype=np.uint8), format="rgb24")
                frame.pts, frame.time_base = i, Fraction(1, 10)
                for packet in stream.encode(frame):
                    c.mux(packet)
            for packet in stream.encode():
                c.mux(packet)
        digest = pp.sha256(self.source)
        segment = {"segment_id": "external_001", "start_seconds": 0.2, "end_seconds": 1.8}
        original = rp.create_preview(self.source, segment, self.root / "review/external_001")
        original.update(source_video_id="sha256:" + digest, source_group_id="local-file-sha256:" + digest,
                        source_group_scope="same_local_file_only_upstream_relations_unverified",
                        preview_path="external_001/review.mp4")
        self.parent = {"purpose": "human_review_only", "status": "completed_unreviewed", "inputs_unchanged": True,
                       "source_sha256": digest, "source_filename": self.source.name, "segments": [original]}
        self.parent_path = self.root / "review/manifest.json"
        self.sample = {k: original[k] for k in ("source_video_id", "source_group_id", "source_group_scope",
                                               "start_seconds", "end_seconds", "official_split", "event_id")}
        self.sample.update(sample_id="external_001", source_sha256=digest, target=0,
                           reviewed_preview="review/external_001/review.mp4", reviewed_preview_sha256=original["preview_sha256"],
                           training_authorized=False, model_input_approved=False,
                           preview_review={"order_problem": False, "cropping_or_missing_content": None,
                                           "additional_context_needed": None})
        self.sample["label_review"] = {"status": "approved", "label": "normal", "scope": "segment",
                                       "start_seconds": 0.2, "end_seconds": 1.8, "source_sha256": digest,
                                       "policy_version": "fixture-only", "reviewer": "software-test",
                                       "reviewed_at": "2026-09-20", "basis": "generated colour video, not real traffic"}
        self.review = {"schema_version": 1, "review_purpose": "human_review_record_only",
                       "time_basis": "local_file_pts_seconds", "interval": "[start,end)", "class_order": pp.CLASS_ORDER,
                       "policy_version": "fixture-only", "source_sha256": digest, "source_filename": self.source.name,
                       "parent_manifest": "review/manifest.json", "samples": [self.sample]}
        self.config = pp.read_json(cli.ROOT / "configs/train_r3d18.local.json")
        self.config["preprocessing"].update(frame_count=4, image_size=64)
        self.review_path = self.root / "review.json"
        self.config_path = self.root / "config.json"
        self.output = self.root / "artifacts/linked"

    def write_inputs(self):
        self.parent_path.write_text(json.dumps(self.parent))
        self.review["parent_manifest_sha256"] = pp.sha256(self.parent_path)
        self.review_path.write_text(json.dumps(self.review))
        self.config_path.write_text(json.dumps(self.config))

    def run_check(self):
        self.write_inputs()
        return cli.run(self.root, self.source, self.review_path, self.config_path, self.output)

    def test_original_avi_tensor_normal_target_and_provenance(self):
        before = pp.sha256(self.source)
        report = self.run_check()
        self.assertEqual(report["status"], "passed")
        item, verification = report["samples"][0], report["verification"][0]
        self.assertEqual(item["target"], 0)
        self.assertEqual(item["label_review"], self.sample["label_review"])
        self.assertEqual(verification["tensor_shape"], [3, 4, 64, 64])
        self.assertEqual(verification["tensor_dtype"], "float32")
        self.assertEqual(verification["tensor_device"], "cpu")
        self.assertTrue(item["selected_pts_present_in_reviewed_preview"])
        self.assertFalse(item["reviewed_mp4_used_as_model_input"])
        self.assertFalse(report["training_run"])
        self.assertFalse(report["model_created"])
        self.assertFalse(item["model_input_approved"])
        self.assertFalse(item["training_authorized"])
        self.assertIsNone(item["official_split"])
        self.assertIsNone(item["preview_review"]["cropping_or_missing_content"])
        self.assertEqual(item["source_group_id"], self.sample["source_group_id"])
        self.assertEqual(pp.sha256(self.source), before)
        self.assertNotIn(str(self.root), json.dumps(report))
        array = np.load(self.output / item["input_path"])
        means = array[:, 16:48].mean(axis=(1, 2, 3))
        self.assertTrue(np.all(np.diff(means) > 0))
        self.assertEqual(item["processed_frames"]["transform"]["crop"], None)

    def test_unconfirmed_keeps_null_and_no_array(self):
        self.sample["label_review"] = {"status": "unreviewed", "label": None}
        self.sample["target"] = None
        report = self.run_check()
        self.assertEqual(report["status"], "needs_attention")
        self.assertIsNone(report["samples"][0]["target"])
        self.assertEqual(report["verification"][0]["status"], "not_loaded")
        self.assertEqual(list(self.output.rglob("*.npy")), [])

    def test_missing_source_does_not_make_normal(self):
        self.source.unlink()
        with self.assertRaisesRegex(pp.PreprocessingError, "source_missing"):
            self.run_check()
        self.assertFalse(self.output.exists())

    def test_short_video_is_error_without_padding(self):
        for r in (self.sample, self.sample["label_review"], self.parent["segments"][0]):
            r["end_seconds"] = 5
        report = self.run_check()
        self.assertEqual(report["samples"][0]["reason"], "time_out_of_video_range")
        self.assertIsNone(report["samples"][0]["target"])
        self.assertEqual(list(self.output.rglob("*.npy")), [])

    def test_invalid_times_rejected(self):
        for value in (-1, True, float("inf"), float("nan"), "0.2"):
            sample = copy.deepcopy(self.review)
            sample["samples"][0]["start_seconds"] = value
            with self.subTest(value=value), self.assertRaises(pp.PreprocessingError):
                ep.validate_review(sample, self.parent)

    def test_review_window_cannot_expand_silently(self):
        self.sample["end_seconds"] = self.sample["label_review"]["end_seconds"] = 2
        with self.assertRaisesRegex(pp.PreprocessingError, "parent_provenance_mismatch:end_seconds"):
            self.run_check()

    def test_hash_mismatch_blocks_before_output(self):
        with self.source.open("ab") as f:
            f.write(b"changed")
        with self.assertRaisesRegex(pp.PreprocessingError, "source_hash_mismatch"):
            self.run_check()
        self.assertFalse(self.output.exists())

    def test_corrupt_source_is_error_not_zero(self):
        self.source.write_bytes(b"not a video")
        digest = pp.sha256(self.source)
        self.parent["source_sha256"] = self.review["source_sha256"] = self.sample["source_sha256"] = digest
        self.sample["label_review"]["source_sha256"] = digest
        for row in (self.parent["segments"][0], self.sample):
            row.update(source_video_id="sha256:" + digest, source_group_id="local-file-sha256:" + digest)
        report = self.run_check()
        self.assertEqual(report["samples"][0]["status"], "error")
        self.assertIsNone(report["samples"][0]["target"])

    def test_parent_mapping_must_cover_selected_pts(self):
        self.parent["segments"][0]["frame_mapping"] = []
        report = self.run_check()
        self.assertEqual(report["samples"][0]["reason"], "selected_frame_outside_reviewed_preview")
        self.assertEqual(list(self.output.rglob("*.npy")), [])

    def test_reviewed_preview_changed_is_rejected(self):
        (self.root / self.sample["reviewed_preview"]).write_bytes(b"changed preview")
        with self.assertRaisesRegex(pp.PreprocessingError, "reviewed_preview_hash_mismatch"):
            self.run_check()

    def test_no_output_overwrite_or_input_overlap(self):
        self.output.mkdir(parents=True)
        marker = self.output / "keep.txt"
        marker.write_text("keep")
        with self.assertRaisesRegex(pp.PreprocessingError, "output_already_exists"):
            self.run_check()
        self.assertEqual(marker.read_text(), "keep")
        self.output = self.root / "originals/output"
        with self.assertRaisesRegex(pp.PreprocessingError, "output_must_be_under_artifacts"):
            self.run_check()

    def test_external_record_paths_cannot_escape(self):
        self.review["parent_manifest"] = "../unknown.json"
        with self.assertRaisesRegex(pp.PreprocessingError, "artifact_path_outside_output"):
            self.run_check()

    def test_sample_cap_ids_and_target_mismatch(self):
        cases = []
        r = copy.deepcopy(self.review); r["samples"] *= 4; cases.append(r)
        r = copy.deepcopy(self.review); r["samples"] *= 2; cases.append(r)
        r = copy.deepcopy(self.review); r["samples"][0]["sample_id"] = "../outside"; cases.append(r)
        r = copy.deepcopy(self.review); r["samples"][0]["target"] = 1; cases.append(r)
        r = copy.deepcopy(self.review); r["samples"][0]["target"] = False; cases.append(r)
        r = copy.deepcopy(self.review); r["samples"][0]["label_review"]["basis"] = ""; cases.append(r)
        for review in cases:
            with self.subTest(review=review["samples"][0]["sample_id"]), self.assertRaises(pp.PreprocessingError):
                ep.validate_review(review, self.parent)

    def test_worker_timeout_produces_error_and_cleans_partial_directory(self):
        with patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 40)):
            report = self.run_check()
        self.assertEqual(report["samples"][0]["reason"], "decode_timeout")
        self.assertIsNone(report["samples"][0]["target"])
        self.assertEqual(list(self.output.glob(".decode-*")), [])

    def test_supervisor_hard_timeout(self):
        proc = MagicMock(pid=12345)
        proc.communicate.side_effect = [subprocess.TimeoutExpired("fixture", 90), ("", "")]
        with patch.object(cli.subprocess, "Popen", return_value=proc), patch.object(cli.os, "killpg") as kill:
            self.assertEqual(cli.supervise([]), 2)
        kill.assert_called_once_with(12345, cli.signal.SIGKILL)

    def test_readback_rejects_reversed_timestamps_even_with_fresh_hash(self):
        report = self.run_check()
        item = report["samples"][0]
        path = self.output / item["frames_metadata_path"]
        content = pp.read_json(path)
        content["frames"].reverse()
        path.write_text(json.dumps(content))
        item["frames_sha256"] = pp.sha256(path)
        with self.assertRaisesRegex(cli.TrainingError, "frame_timing_or_target_mismatch"):
            ep.verify_inputs(self.output, [item], self.config["preprocessing"])

    def test_png_corruption_caught_independently_of_array(self):
        report = self.run_check()
        item = report["samples"][0]
        Image.new("RGB", (64, 64), "red").save(self.output / item["sample_id"] / "frames/000.png")
        with self.assertRaisesRegex(pp.PreprocessingError, "array_preview_pixels_differ"):
            ep.verify_inputs(self.output, [item], self.config["preprocessing"])


if __name__ == "__main__":
    unittest.main()
