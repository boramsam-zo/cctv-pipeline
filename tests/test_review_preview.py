"""Small generated videos test software behaviour, never traffic ground truth."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import av
import numpy as np

from scripts import prepare_review_previews as cli
from boram3_cctv import preprocessing as pp
from boram3_cctv import review_preview as rp


class ReviewPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "originals").mkdir()
        self.source = self.base / "originals/fixture.avi"
        self.make_video()
        self.plan = {"schema_version": 1, "purpose": "human_review_only",
                     "time_basis": "local_file_pts_seconds", "source_filename": self.source.name,
                     "source_sha256": pp.sha256(self.source),
                     "segments": [{"segment_id": "review_001", "start_seconds": 0.2, "end_seconds": 1.4}]}
        self.plan_path = self.base / "plan.json"
        self.output = self.base / "output"

    def make_video(self, pts=None):
        values = list(range(20)) if pts is None else pts
        with av.open(str(self.source), "w") as container:
            stream = container.add_stream("mpeg4", rate=10)
            stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv420p"
            for index, timestamp in enumerate(values):
                image = np.full((32, 64, 3), 20 + index * 8, dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                frame.pts, frame.time_base = timestamp, Fraction(1, 10)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

    def run_plan(self):
        self.plan_path.write_text(json.dumps(self.plan))
        return cli.run(self.source, self.plan_path, self.output)

    def test_mp4_roundtrip_preserves_order_times_source_and_unknown_label(self):
        before = pp.sha256(self.source)
        report = self.run_plan()
        self.assertEqual(report["status"], "completed_unreviewed")
        record = report["segments"][0]
        self.assertEqual(record["encoded_frames"], 12)
        self.assertIsNone(record["target"])
        self.assertFalse(record["training_authorized"])
        self.assertFalse(report["model_run"])
        self.assertTrue(report["inputs_unchanged"])
        self.assertEqual(pp.sha256(self.source), before)
        self.assertNotIn(str(self.base), json.dumps(report))
        self.assertEqual(list(self.output.rglob("*.npy")), [])
        movie = self.output / record["preview_path"]
        with av.open(str(movie)) as c:
            frames = list(c.decode(video=0))
            means = [float(f.to_ndarray(format="rgb24").mean()) for f in frames]
        self.assertTrue(all(b > a for a, b in zip(means, means[1:])))
        self.assertAlmostEqual(float(frames[0].time), 0)
        for index, row in enumerate(record["frame_mapping"]):
            self.assertAlmostEqual(row["source_seconds"], 0.2 + index / 10)
            self.assertAlmostEqual(row["preview_seconds"], index / 10)
        self.assertEqual(record["source_media"]["width"], 64)
        self.assertEqual(record["preview_verification"]["codec"], "h264")

    def test_irregular_pts_are_not_replaced_with_frame_index_over_fps(self):
        self.make_video([0, 1, 3, 4, 6, 8, 10, 13, 15, 18])
        self.plan["source_sha256"] = pp.sha256(self.source)
        self.plan["segments"][0]["end_seconds"] = 1.9
        record = self.run_plan()["segments"][0]
        self.assertEqual(record["status"], "preview_ready_unreviewed")
        actual = [round(r["source_seconds"], 3) for r in record["frame_mapping"]]
        self.assertEqual(actual, [0.3, 0.4, 0.6, 0.8, 1.0, 1.3, 1.5, 1.8])
        self.assertEqual([round(r["preview_seconds"], 3) for r in record["frame_mapping"]],
                         [0, 0.1, 0.3, 0.5, 0.7, 1, 1.2, 1.5])

    def test_short_source_rejected_without_padding_or_published_movie(self):
        self.plan["segments"][0]["end_seconds"] = 3
        record = self.run_plan()["segments"][0]
        self.assertEqual(record["reason"], "time_out_of_video_range")
        self.assertIsNone(record["target"])
        self.assertEqual(list(self.output.rglob("*.mp4")), [])
        self.assertEqual(list(self.output.glob(".worker-*")), [])

    def test_missing_file(self):
        self.source.unlink()
        with self.assertRaisesRegex(rp.PreviewError, "file_missing"):
            self.run_plan()
        self.assertFalse(self.output.exists())

    def test_corrupt_input_is_error_not_normal(self):
        self.source.write_bytes(b"broken fixture")
        self.plan["source_sha256"] = pp.sha256(self.source)
        record = self.run_plan()["segments"][0]
        self.assertEqual(record["reason"], "media_or_encoding_failed")
        self.assertIsNone(record["label"])
        self.assertEqual(list(self.output.rglob("*.mp4")), [])

    def test_invalid_ranges_and_values_fail_before_work(self):
        for start, end in [(-1, 1), (1, 1), (2, 1), (False, 1), ("0", 1),
                           (0, float("inf")), (0, float("nan")), (0, 10.01)]:
            with self.subTest(start=start, end=end):
                candidate = copy.deepcopy(self.plan)
                candidate["segments"][0].update(start_seconds=start, end_seconds=end)
                with self.assertRaises((rp.PreviewError, pp.PreprocessingError)):
                    rp.validate_plan(candidate)

    def test_no_overwrite_or_source_directory_output(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("user file")
        with self.assertRaisesRegex(rp.PreviewError, "output_already_exists"):
            self.run_plan()
        self.assertEqual(marker.read_text(), "user file")
        self.output = self.source.parent / "new"
        with self.assertRaisesRegex(rp.PreviewError, "overlaps_original"):
            self.run_plan()

    def test_mismatched_hash_blocks_decoding(self):
        self.plan["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(rp.PreviewError, "source_identity_mismatch"):
            self.run_plan()
        self.assertFalse(self.output.exists())

    def test_plan_caps_overlap_paths_and_label_injection(self):
        for changes in [{"segment_id": "../bad"}, {"label": "normal"}]:
            candidate = copy.deepcopy(self.plan)
            candidate["segments"][0].update(changes)
            with self.assertRaises(rp.PreviewError):
                rp.validate_plan(candidate)
        candidate = copy.deepcopy(self.plan)
        candidate["segments"] *= 4
        with self.assertRaisesRegex(rp.PreviewError, "1_to_3"):
            rp.validate_plan(candidate)
        candidate["segments"] = [self.plan["segments"][0],
                                 {"segment_id": "another", "start_seconds": 1, "end_seconds": 2}]
        with self.assertRaisesRegex(rp.PreviewError, "overlapping"):
            rp.validate_plan(candidate)

    def test_worker_timeout_keeps_unknown_target_and_no_partial_movie(self):
        with patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 30)):
            report = self.run_plan()
        self.assertEqual(report["status"], "needs_attention")
        self.assertEqual(report["segments"][0]["reason"], "worker_timeout")
        self.assertIsNone(report["segments"][0]["target"])
        self.assertEqual(list(self.output.rglob("*.mp4")), [])

    def test_same_source_segments_share_one_group(self):
        self.plan["segments"] = [{"segment_id": f"review_{i}", "start_seconds": start,
                                  "end_seconds": start + 0.4} for i, start in enumerate([0.2, 0.7, 1.2])]
        report = self.run_plan()
        self.assertEqual(report["status"], "completed_unreviewed")
        self.assertEqual(len({r["source_group_id"] for r in report["segments"]}), 1)
        self.assertEqual(report["independent_source_files"], 1)

    def test_preview_timestamp_mismatch_is_detected(self):
        import time
        report = self.run_plan()
        record = report["segments"][0]
        bad = copy.deepcopy(record["frame_mapping"])
        bad[1]["preview_seconds"] += 0.1
        with self.assertRaisesRegex(rp.PreviewError, "preview_timing_mismatch"):
            rp.verify_movie(self.output / record["preview_path"], bad, 64, 32, time.monotonic() + 5)

    def test_supervisor_timeout_kills_whole_process_group(self):
        fake = MagicMock(pid=43210)
        fake.communicate.side_effect = [subprocess.TimeoutExpired("fixture", 90), ("", "")]
        with patch.object(cli.subprocess, "Popen", return_value=fake), \
             patch.object(cli.os, "killpg") as kill, patch("builtins.print"):
            self.assertEqual(cli.supervise([]), 2)
        kill.assert_called_once_with(43210, cli.signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
