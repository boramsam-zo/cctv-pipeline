"""Generated tiny fixtures are software tests, never real accident ground truth."""
import copy
import csv
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import preprocess_samples as cli
from boram3_cctv import preprocessing as pp

HAS_DEPS = all(importlib.util.find_spec(name) for name in ("av", "numpy", "PIL"))


@unittest.skipUnless(HAS_DEPS, "preprocessing dependencies not installed")
class PreprocessingTests(unittest.TestCase):
    def setUp(self):
        import av
        import numpy as np
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "originals"
        (self.root / "real_videos").mkdir(parents=True)
        self.relative = "real_videos/fixture.mp4"
        self.video = self.root / self.relative
        self.make_video()
        self.sample = {"sample_id": "sample_001", "video_path": self.relative,
                       "source_sha256": pp.sha256(self.video), "start_seconds": 0.2, "end_seconds": 1.8,
                       "window_basis": "test fixture definition, not accident data",
                       "source_group_id": "fixture-group", "event_id": "fixture-event",
                       "label_review": {"status": "unreviewed", "label": None}}
        self.plan = {"schema_version": 1, "policy_version": "test-policy", "time_basis": "local_clip_seconds",
                     "frame_count": 4, "image_size": 64, "samples": [self.sample]}
        self.plan_path = self.base / "plan.json"
        self.write_metadata()

    def make_video(self, pts=None):
        import av
        import numpy as np
        pts = list(range(40)) if pts is None else pts
        with av.open(str(self.video), "w") as container:
            stream = container.add_stream("mpeg4", rate=10)
            stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv420p"
            stream.time_base = Fraction(1, 10)
            for index, timestamp in enumerate(pts):
                array = np.full((32, 64, 3), index * 5 % 256, dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(array, format="rgb24")
                frame.pts, frame.time_base = timestamp, Fraction(1, 10)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

    def write_metadata(self, extra=None):
        self.row = {"path": self.relative, "accident_time": "0.8", "accident_frame": "8", "type": "single",
                    "weather": "normal", "split_in_distribution": "test", "split_geo_aware": "test"}
        with (self.root / "metadata-real.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.row)
            writer.writeheader()
            writer.writerow(self.row)
            if extra:
                writer.writerow({**self.row, **extra})

    def approve(self, label="accident"):
        self.sample["label_review"] = {"status": "approved", "label": label, "scope": "segment",
                                       "basis": "software-test fixture only", "reviewer": "test-fixture",
                                       "reviewed_at": "2026-09-18", "policy_version": "test-policy",
                                       "source_sha256": self.sample["source_sha256"],
                                       "start_seconds": self.sample["start_seconds"], "end_seconds": self.sample["end_seconds"]}

    def run_plan(self, name="output"):
        self.plan_path.write_text(json.dumps(self.plan))
        output = self.base / name
        result = cli.run(self.root, self.plan_path, output)
        return result, output

    def test_approved_segment_writes_array_and_target_preserving_source(self):
        import numpy as np
        self.approve()
        before_video = pp.sha256(self.video)
        before_metadata = pp.sha256(self.root / "metadata-real.csv")
        result, output = self.run_plan()
        record = result["samples"][0]
        self.assertEqual(record["status"], "prepared_labeled")
        self.assertEqual(record["target"], 1)
        array = np.load(output / record["input_path"], allow_pickle=False)
        self.assertEqual(array.shape, (4, 64, 64, 3))
        self.assertEqual(array.dtype, np.uint8)
        self.assertEqual(pp.sha256(self.video), before_video)
        self.assertEqual(pp.sha256(self.root / "metadata-real.csv"), before_metadata)
        self.assertTrue(result["inputs_unchanged"])
        self.assertEqual(record["source_video_id"], "sha256:" + before_video)
        self.assertEqual(record["event_id"], "fixture-event")
        self.assertEqual(record["official_splits"]["split_in_distribution"], "test")
        self.assertFalse(record["training_authorized"])

    def test_unreviewed_has_previews_but_no_target_or_array(self):
        result, output = self.run_plan()
        record = result["samples"][0]
        self.assertEqual(record["status"], "unconfirmed")
        self.assertIsNone(record["target"])
        self.assertIsNone(record["input_path"])
        self.assertTrue((output / record["preview_path"]).is_file())
        self.assertEqual(len(list(output.rglob("frames/*.png"))), 4)
        self.assertEqual(list(output.rglob("*.npy")), [])
        self.assertEqual(pp.read_json(output / "labeled.json")["samples"], [])

    def test_external_metadata_and_validation_id_preserve_unconfirmed_status(self):
        metadata=self.base/'received-metadata.csv'
        (self.root/'metadata-real.csv').rename(metadata)
        before=pp.sha256(metadata)
        self.sample['sample_id']='devval_accident_01'
        self.plan_path.write_text(json.dumps(self.plan))
        report=cli.run(self.root,self.plan_path,self.base/'external',timeout=20,metadata_path=metadata)
        record=report['samples'][0]
        self.assertEqual(record['sample_id'],'devval_accident_01')
        self.assertEqual(record['status'],'unconfirmed')
        self.assertIsNone(record['target'])
        self.assertFalse(record['training_authorized'])
        self.assertEqual(report['metadata_sha256'],before)
        self.assertEqual(pp.sha256(metadata),before)
        self.assertFalse((self.root/'metadata-real.csv').exists())
        self.assertEqual(report['metadata_selection'],'explicit_path')
        self.assertEqual(report['worker_timeout_seconds'],20)
        media=record['media']
        self.assertEqual(media['average_fps'],10.)
        self.assertLessEqual(media['decoded_first_seconds_including_preroll'],self.sample['start_seconds'])
        self.assertGreaterEqual(media['decoded_last_seconds_including_lookahead'],
                                record['processed_frames']['frames'][-1]['actual_seconds'])

    def test_explicit_metadata_missing_or_inside_output_rejected(self):
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaises(FileNotFoundError):
            cli.run(self.root,self.plan_path,self.base/'missing-output',metadata_path=self.base/'absent.csv')
        with self.assertRaisesRegex(pp.PreprocessingError,'output_overlaps_manifest_input'):
            cli.run(self.root,self.plan_path,self.base/'new-output',metadata_path=self.base/'new-output/metadata.csv')
        self.assertFalse((self.base/'missing-output').exists())

    def test_validation_ids_remain_path_safe_and_worker_limit_is_bounded(self):
        for value in ('devval_accident_01','devval_normal_02'):
            self.sample['sample_id']=value;pp.validate_plan(self.plan)
        for value in ('../devval_accident_01','devval_accident_01/child','devval_unknown_01'):
            self.sample['sample_id']=value
            with self.assertRaisesRegex(pp.PreprocessingError,'invalid_sample_id'):pp.validate_plan(self.plan)
        for timeout in (0,46,True):
            with self.assertRaisesRegex(pp.PreprocessingError,'worker_timeout_must_be'):
                cli.run(self.root,self.plan_path,self.base/'out',timeout=timeout)

    def test_hold_and_undetermined_never_become_normal(self):
        for status in ("hold", "undetermined"):
            with self.subTest(status=status):
                self.sample["label_review"]["status"] = status
                result, _ = self.run_plan(status)
                self.assertEqual(result["samples"][0]["unconfirmed_reason"], status)
                self.assertIsNone(result["samples"][0]["target"])

    def test_normal_label_zero_is_preserved_and_does_not_change_pixels(self):
        import numpy as np
        self.approve("normal")
        normal, normal_out = self.run_plan("normal")
        self.assertEqual(normal["samples"][0]["target"], 0)
        self.approve("accident")
        accident, accident_out = self.run_plan("accident")
        np.testing.assert_array_equal(np.load(normal_out / normal["samples"][0]["input_path"]),
                                      np.load(accident_out / accident["samples"][0]["input_path"]))

    def test_short_video_fails_without_padding_or_partial_output(self):
        self.make_video([0, 1, 2])
        self.sample.update(source_sha256=pp.sha256(self.video), start_seconds=0.0, end_seconds=0.25)
        self.plan["frame_count"] = 16
        result, output = self.run_plan()
        self.assertEqual(result["samples"][0]["reason"], "insufficient_distinct_frames")
        self.assertIsNone(result["samples"][0]["target"])
        self.assertEqual(list(output.rglob("*.png")), [])
        self.assertEqual(list(output.glob(".sample-*")), [])

    def test_missing_video_is_error(self):
        self.video.unlink()
        result, _ = self.run_plan()
        self.assertEqual(result["samples"][0]["reason"], "file_missing")
        self.assertEqual(result["counts"]["error"], 1)

    def test_corrupt_media_does_not_create_a_label(self):
        self.video.write_bytes(b"not a video")
        self.sample["source_sha256"] = pp.sha256(self.video)
        result, _ = self.run_plan()
        self.assertEqual(result["samples"][0]["reason"], "media_or_processing_failed")
        self.assertIsNone(result["samples"][0]["target"])

    def test_invalid_time_values_and_ranges(self):
        for start, end in [(-1, 1), (1, 1), (2, 1), (0, float("nan")), (0, float("inf")), (False, 1), ("0", 1)]:
            with self.subTest(start=start, end=end), self.assertRaises(pp.PreprocessingError):
                pp.validate_window({"start_seconds": start, "end_seconds": end})

    def test_out_of_range_end_is_not_clamped(self):
        self.sample["end_seconds"] = 5.0
        result, _ = self.run_plan()
        self.assertEqual(result["samples"][0]["reason"], "time_out_of_video_range")

    def test_full_video_window_over_limit_is_rejected(self):
        with self.assertRaisesRegex(pp.PreprocessingError, "segment_exceeds_10_second_limit"):
            pp.validate_window({"start_seconds": 0, "end_seconds": 30})

    def test_pts_sampling_uses_actual_irregular_timing(self):
        pts = [0, 1, 3, 6, 10, 15, 21, 28, 36]
        self.make_video(pts)
        frames, info = pp.sample_frames(self.video, 0.2, 3.6, 4)
        candidates = [value / 10 for value in pts if 0.2 <= value / 10 < 3.6]
        for frame in frames:
            expected = min(candidates, key=lambda value: abs(value - frame["requested_seconds"]))
            self.assertAlmostEqual(frame["actual_seconds"], expected, places=5)
        self.assertTrue(all(a["actual_seconds"] < b["actual_seconds"] for a, b in zip(frames, frames[1:])))
        self.assertEqual(info["saved_frame_count"], 4)

    def test_low_fps_repetition_is_opt_in_and_recorded(self):
        self.make_video([0, 5, 10, 15, 20, 25, 30, 35, 40])
        with self.assertRaisesRegex(pp.PreprocessingError, "insufficient_distinct_frames"):
            pp.sample_frames(self.video, 0, 2, 16)
        frames, info = pp.sample_frames(self.video, 0, 2, 16, allow_repeated_frames=True)
        times = [f['actual_seconds'] for f in frames]
        self.assertEqual(len(times), 16)
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(0 <= t < 2 for t in times))
        self.assertEqual(info['unique_frame_count'], 4)
        self.assertEqual(info['repeated_selection_count'], 12)
        self.assertEqual(info['sampling_policy'], 'nearest-bin-repeat-v1')

    def test_end_boundary_is_excluded_and_frames_are_ordered(self):
        frames, _ = pp.sample_frames(self.video, 0.2, 1.8, 4)
        actual = [f["actual_seconds"] for f in frames]
        self.assertEqual(actual, sorted(set(actual)))
        self.assertTrue(all(0.2 <= value < 1.8 for value in actual))

    def test_letterbox_retains_all_four_source_edges(self):
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (128, 64), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 127, 63), outline="red", width=4)
        transformed, spec = pp.letterbox(image, 64)
        self.assertEqual((spec["resized_width"], spec["resized_height"]), (64, 32))
        self.assertEqual(spec["padding_top"], 16)
        self.assertIsNone(spec["crop"])
        for point in [(0, 16), (63, 16), (0, 47), (63, 47)]:
            self.assertGreater(transformed.getpixel(point)[0], 150)

    def test_segment_approval_cannot_be_inherited_from_whole_video(self):
        self.approve()
        self.sample["label_review"]["scope"] = "whole_video"
        result, _ = self.run_plan()
        self.assertEqual(result["samples"][0]["reason"], "segment_specific_approval_required")

    def test_approval_is_bound_to_source_interval_policy_and_evidence(self):
        for key, value, reason in [("source_sha256", "0" * 64, "review_source_hash_mismatch"),
                                   ("start_seconds", 0.3, "review_interval_mismatch"),
                                   ("end_seconds", 1.9, "review_interval_mismatch"),
                                   ("policy_version", "different", "review_policy_mismatch"),
                                   ("basis", "", "missing_review_basis"),
                                   ("label", "single", "invalid_approved_label")]:
            with self.subTest(key=key):
                self.approve()
                self.sample["label_review"][key] = value
                with self.assertRaisesRegex(pp.PreprocessingError, reason):
                    pp.approved_target(self.sample, "test-policy", self.sample["source_sha256"])

    def test_pending_label_cannot_silently_be_used(self):
        self.sample["label_review"]["label"] = "accident"
        result, _ = self.run_plan()
        self.assertEqual(result["samples"][0]["reason"], "unapproved_label_must_be_null")

    def test_wrong_source_hash_is_rejected(self):
        self.sample["source_sha256"] = "0" * 64
        result, _ = self.run_plan()
        self.assertEqual(result["samples"][0]["reason"], "source_hash_mismatch")

    def test_only_selected_video_is_opened(self):
        self.write_metadata({"path": "real_videos/not-downloaded.mp4"})
        result, _ = self.run_plan()
        self.assertEqual(result["counts"]["error"], 0)
        self.assertEqual(len(result["samples"]), 1)

    def test_duplicate_or_missing_metadata_is_explicit(self):
        self.write_metadata({"path": self.relative})
        result, _ = self.run_plan("duplicate")
        self.assertEqual(result["samples"][0]["reason"], "metadata_ambiguous")
        self.sample["video_path"] = "real_videos/unknown.mp4"
        result, _ = self.run_plan("missing")
        self.assertEqual(result["samples"][0]["reason"], "metadata_missing")

    def test_unsafe_paths_and_synthetic_inputs_are_rejected(self):
        for path in ("../video.mp4", "/tmp/video.mp4", "synthetic_videos/video.mp4"):
            with self.subTest(path=path), self.assertRaises(pp.PreprocessingError):
                pp.source_path(self.root, path)
        (self.root / "real_videos/link.mp4").symlink_to(self.base / "outside.mp4")
        with self.assertRaises(pp.PreprocessingError):
            pp.source_path(self.root, "real_videos/link.mp4")

    def test_existing_output_and_original_directory_are_never_overwritten(self):
        self.run_plan()
        with self.assertRaisesRegex(pp.PreprocessingError, "output_already_exists"):
            cli.run(self.root, self.plan_path, self.base / "output")
        with self.assertRaisesRegex(pp.PreprocessingError, "output_overlaps_original_data"):
            cli.run(self.root, self.plan_path, self.root / "new-output")

    def test_timeout_is_reported_and_temporary_files_are_removed(self):
        with patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 45)):
            result, output = self.run_plan()
        self.assertEqual(result["samples"][0]["reason"], "processing_timeout")
        self.assertEqual(list(output.glob(".sample-*")), [])

    def test_selection_and_frame_limits(self):
        for key, value in [("frame_count", 33), ("samples", []), ("samples", [self.sample] * 4), ("samples", [self.sample] * 2)]:
            with self.subTest(key=key, value=value):
                plan = copy.deepcopy(self.plan)
                plan[key] = value
                with self.assertRaises(pp.PreprocessingError):
                    pp.validate_plan(plan)


if __name__ == "__main__":
    unittest.main()
