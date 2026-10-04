"""작은 임시 fixture와 mock은 검사기 테스트 전용이며 학습/평가 데이터가 아니다."""
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from scripts import inspect_video_sample as checker


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "real_videos").mkdir()
        self.selected = "real_videos/fixture.mp4"
        (self.root / self.selected).write_bytes(b"test-only-not-a-video")
        (self.root / "annotation_classes.yaml").write_text("names:\n  0: Unlabeled\n  14: Car\n")
        self.row = {
            "path": self.selected, "type": "single", "rollover": "0", "weather": "normal",
            "accident_time": "0.5", "accident_frame": "5", "duration": "2",
            "no_frames": "20", "width": "32", "height": "24",
            "center_x": "0.5", "center_y": "0.5", "x1": "0.4", "x2": "0.6",
            "y1": "0.4", "y2": "0.6", "split_in_distribution": "test",
        }
        self.media = {"status": "ok", "duration_seconds": 2.0, "header_frame_count": 20,
                      "average_fps": 10.0, "width": 32, "height": 24}
        self.write_csv([self.row])

    def write_csv(self, rows):
        with (self.root / "metadata-real.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(self.row))
            writer.writeheader()
            writer.writerows(rows)

    def inspect(self, **kwargs):
        with patch.object(checker, "read_class_map", return_value={"status": "ok"}):
            return checker.inspect(self.root, [self.selected], probe=lambda _: self.media, **kwargs)

    def test_unique_match_preserves_raw_labels_and_original(self):
        result = self.inspect(unit="seconds", time_source="test_fixture_definition")
        self.assertEqual(result["status"], "checked")
        selected = result["selected"][0]
        self.assertEqual(selected["annotations"], [self.row])
        self.assertTrue(selected["original_unchanged"])
        self.assertEqual(selected["annotation_checks"]["binary_label"], "not_derived")
        self.assertFalse(result["predictions_generated"])

    def test_unselected_rows_do_not_open_files_or_become_errors(self):
        self.write_csv([self.row, {**self.row, "path": "real_videos/not-downloaded.mp4"}])
        visited = []
        def probe(path):
            visited.append(path)
            return self.media
        with patch.object(checker, "read_class_map", return_value={"status": "ok"}):
            result = checker.inspect(self.root, [self.selected], probe=probe)
        self.assertEqual(visited, [self.root / self.selected])
        self.assertEqual(result["excluded_metadata_rows"], 1)
        self.assertEqual(result["excluded_video_files_opened"], 0)

    def test_missing_selected_video_is_not_normal(self):
        (self.root / self.selected).unlink()
        result = self.inspect()
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(result["selected"][0]["media"]["status"], "file_missing")

    def test_absent_data_root_reports_not_run(self):
        result = checker.inspect(self.root / "absent", [self.selected])
        self.assertEqual(result["metadata"]["status"], "file_missing")
        self.assertEqual(result["selected"][0]["media"]["status"], "file_missing")
        self.assertIsNone(result["excluded_metadata_rows"])

    def test_missing_metadata_does_not_prevent_file_inspection(self):
        (self.root / "metadata-real.csv").unlink()
        result = self.inspect()
        self.assertEqual(result["selected"][0]["label_match"], "metadata_unavailable")
        self.assertEqual(result["selected"][0]["media"]["status"], "ok")

    def test_missing_label_is_explicit(self):
        self.write_csv([{**self.row, "path": "real_videos/different.mp4"}])
        result = self.inspect()
        self.assertEqual(result["selected"][0]["label_match"], "missing")
        self.assertEqual(result["status"], "needs_attention")

    def test_duplicate_labels_not_first_match(self):
        self.write_csv([self.row, {**self.row, "type": "t-bone"}])
        result = self.inspect()
        self.assertEqual(result["selected"][0]["label_match"], "ambiguous")
        self.assertEqual(len(result["selected"][0]["annotations"]), 2)
        self.assertNotIn("annotation_checks", result["selected"][0])

    def test_missing_key_and_malformed_csv(self):
        for content, expected in [("file,type\na,b\n", "missing_key_column"),
                                  ("path,path\na,b\n", "invalid_csv_header"),
                                  ("path,type\na,b,c\n", "invalid_csv_row_width"),
                                  ("path,type\na\n", "invalid_csv_row_width"),
                                  ("path,type\n", "empty_metadata")]:
            with self.subTest(content=content):
                (self.root / "metadata-real.csv").write_text(content)
                self.assertEqual(self.inspect()["metadata"]["status"], expected)

    def test_unknown_unit_is_not_inferred(self):
        checks = self.inspect()["selected"][0]["annotation_checks"]
        self.assertIn("time_unit_unverified", checks["issues"])
        self.assertNotIn("frame_div_average_fps_seconds", checks)

    def test_known_unit_requires_evidence(self):
        with self.assertRaisesRegex(checker.InspectionError, "time_unit_requires_source"):
            self.inspect(unit="seconds")

    def test_invalid_and_nonfinite_time_values(self):
        for value in ("", "NaN", "inf", "abc"):
            with self.subTest(value=value):
                checks = checker.check_annotation({**self.row, "accident_time": value}, self.media, "seconds")
                self.assertIn("missing_or_invalid_time_annotation", checks["issues"])
                json.dumps(checks, allow_nan=False)

    def test_out_of_range_time_and_frame(self):
        for timestamp in ("-1", "2", "200"):
            checks = checker.check_annotation({**self.row, "accident_time": timestamp,
                                                "accident_frame": "100"}, self.media, "seconds")
            self.assertIn("time_out_of_range", checks["issues"])
            self.assertIn("frame_out_of_range", checks["issues"])

    def test_metadata_disagreement_is_reported(self):
        checks = checker.check_annotation({**self.row, "width": "100", "duration": "30"},
                                           self.media, "seconds")
        self.assertIn("width_mismatch", checks["issues"])
        self.assertIn("duration_mismatch_over_0.05s", checks["issues"])

    def test_fractional_frame_and_coordinate_errors(self):
        checks = checker.check_annotation({**self.row, "accident_frame": "1.5", "x1": "2"},
                                           self.media, "seconds")
        self.assertIn("invalid_frame_index", checks["issues"])
        self.assertIn("missing_or_invalid_normalized_coordinates", checks["issues"])

    def test_missing_av_is_separate(self):
        with patch.object(checker.importlib, "import_module", side_effect=ImportError):
            self.assertEqual(checker.probe_video(self.root / self.selected)["status"], "tool_missing")
            self.assertEqual(checker.read_class_map(self.root / "annotation_classes.yaml")["status"], "tool_missing")

    def test_subprocess_timeout_and_failure(self):
        with patch.object(checker.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 20)):
            self.assertEqual(checker.run_probe(self.root / self.selected)["status"], "decode_timeout")
        with patch.object(checker.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "private path")):
            self.assertEqual(checker.run_probe(self.root / self.selected), {"status": "probe_process_failed"})

    def test_access_denied_is_explicit(self):
        with patch.object(checker, "fingerprint", side_effect=PermissionError):
            result = self.inspect()
        self.assertEqual(result["selected"][0]["media"]["status"], "access_denied")

    def test_unsafe_and_synthetic_paths_rejected(self):
        for path in ("../outside.mp4", "/tmp/outside.mp4", "synthetic_videos/a.mp4", "https://example.com/a.mp4"):
            with self.subTest(path=path), self.assertRaises(checker.InspectionError):
                checker.inspect(self.root, [path])

    def test_symlink_outside_root_is_rejected(self):
        path = self.root / "real_videos/link.mp4"
        path.symlink_to(self.root.parent / "outside.mp4")
        with self.assertRaisesRegex(checker.InspectionError, "outside_data_root"):
            checker.inspect(self.root, ["real_videos/link.mp4"])

    def test_symlink_to_synthetic_is_rejected(self):
        path = self.root / "real_videos/link.mp4"
        path.symlink_to(self.root / "synthetic_videos/fixture.mp4")
        with self.assertRaisesRegex(checker.InspectionError, "outside_real_video_scope"):
            checker.inspect(self.root, ["real_videos/link.mp4"])

    def test_limit_and_duplicate_selection(self):
        for selected in ([], [self.selected] * 2, [f"real_videos/{i}.mp4" for i in range(4)]):
            with self.subTest(selected=selected), self.assertRaises(checker.InspectionError):
                checker.inspect(self.root, selected)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML not installed")
    def test_yaml_is_safe_and_preserves_noncontiguous_ids(self):
        path = self.root / "annotation_classes.yaml"
        path.write_text("names:\n  0: Unlabeled\n  29: Van\n")
        result = checker.read_class_map(path)
        self.assertEqual(result["names"], {0: "Unlabeled", 29: "Van"})
        self.assertFalse(result["applied_to_binary_labels"])
        for content in ("names:\n  0: Car\n  0: Road\n", "names: [Car]\n", "!!python/object/apply:os.system ['false']"):
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaises(checker.InspectionError):
                    checker.read_class_map(path)

    @unittest.skipUnless(importlib.util.find_spec("av"), "PyAV not installed")
    def test_real_decoder_rejects_corrupt_fixture(self):
        result = checker.run_probe(self.root / self.selected)
        self.assertEqual(result["status"], "media_open_or_decode_failed")

    @unittest.skipUnless(importlib.util.find_spec("av"), "PyAV not installed")
    def test_real_decoder_detects_audio_only_fixture(self):
        path = self.root / "audio.wav"
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(8000)
            f.writeframes(b"\x00\x00" * 8000)
        self.assertEqual(checker.run_probe(path)["status"], "no_video_stream")

    @unittest.skipUnless(importlib.util.find_spec("av"), "PyAV not installed")
    def test_real_decoder_reads_only_nine_frames_of_fixture(self):
        import av
        path = self.root / self.selected
        with av.open(str(path), "w") as container:
            stream = container.add_stream("mpeg4", rate=10)
            stream.width, stream.height, stream.pix_fmt = 32, 24, "yuv420p"
            for index in range(30):
                frame = av.VideoFrame(32, 24, "yuv420p")
                for plane in frame.planes:
                    plane.update(bytes([index * 4]) * plane.buffer_size)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        result = checker.run_probe(path)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["header_frame_count"], 30)
        self.assertEqual(sum(len(s["frames"]) for s in result["decode_samples"]), 9)
        self.assertFalse(result["full_video_checked"])


class FileReceiptTests(unittest.TestCase):
    """CSV 없는 수신 경로. 생성 fixture는 실제 사고/정상 자료가 아니다."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "candidate.mp4"
        self.path.write_bytes(b"fixture-not-a-real-video")
        self.success = {"media": {"status": "ok", "full_video_checked": False},
                        "file": {"bytes": 24, "sha256": "0" * 64}, "original_unchanged": True}

    def test_receipt_does_not_derive_labels_or_groups_without_metadata(self):
        (self.root / "unselected.mp4").write_bytes(b"do-not-open")
        with patch.object(checker, "read_metadata", side_effect=AssertionError("no CSV")), \
             patch.object(checker, "read_class_map", side_effect=AssertionError("no YAML")):
            visited = []
            def probe(path):
                visited.append(path)
                return self.success
            result = checker.inspect_files(self.root, [self.path.name], probe=probe)
        self.assertEqual(visited, [self.path])
        self.assertEqual(result["status"], "files_checked_labels_unreviewed")
        item = result["selected"][0]
        for key in ("target", "approved_segment", "official_split", "upstream_original_id", "event_id"):
            self.assertIsNone(item[key])
        self.assertFalse(item["training_authorized"])
        self.assertFalse(result["training_run"])
        self.assertEqual(item["annotation_time_unit"], "unknown")
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_all_missing_launches_no_worker(self):
        self.path.unlink()
        with patch.object(checker, "run_receipt_probe", side_effect=AssertionError("no worker")) as probe:
            result = checker.inspect_files(self.root, [self.path.name], probe=probe)
        self.assertEqual(result["status"], "not_run_files_missing")
        probe.assert_not_called()

    def test_mixed_missing_and_existing_never_claims_all_checked(self):
        result = checker.inspect_files(self.root, [self.path.name, "missing.avi"], probe=lambda _: self.success)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(result["selected"][1]["media"]["status"], "file_missing")

    def test_limits_aliases_and_unsafe_paths_rejected(self):
        selections = [[], ["a.mp4", "b.mp4", "c.mp4", "d.mp4"],
                      ["candidate.mp4", "./candidate.mp4"], ["../outside.mp4"],
                      [str(self.path)], ["synthetic_videos/a.mp4"], ["notes.txt"]]
        for selected in selections:
            with self.subTest(selected=selected), self.assertRaises(checker.InspectionError):
                checker.inspect_files(self.root, selected)
        (self.root / "link.mp4").symlink_to(self.root.parent / "outside.mp4")
        with self.assertRaisesRegex(checker.InspectionError, "outside_data_root"):
            checker.inspect_files(self.root, ["link.mp4"])

    def test_known_accident_metadata_cannot_be_bypassed(self):
        (self.root / "metadata-real.csv").write_text("path\n")
        (self.root / "real_videos").mkdir()
        for root in (self.root, self.root / "real_videos"):
            with self.subTest(root=root.name), self.assertRaisesRegex(checker.InspectionError, "use_accident_metadata_mode"):
                checker.inspect_files(root, [self.path.name])

    def test_worker_timeout_or_invalid_output_is_explicit(self):
        with patch.object(checker.subprocess, "run", side_effect=subprocess.TimeoutExpired("receipt", 20)) as call:
            self.assertEqual(checker.run_receipt_probe(self.path)["media"]["status"], "receipt_timeout")
            self.assertEqual(call.call_args.kwargs["timeout"], 20)
        for payload in ("[]", '{"media": []}', "not-json"):
            with patch.object(checker.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload, "")):
                self.assertEqual(checker.run_receipt_probe(self.path)["media"]["status"], "receipt_process_failed")

    def test_oversized_and_nonregular_sources_are_not_opened(self):
        with patch.object(checker, "MAX_RECEIPT_BYTES", 1), \
             patch.object(checker, "fingerprint", side_effect=AssertionError("must not hash")):
            self.assertEqual(checker.receipt_file(self.path)["media"]["status"], "file_size_exceeds_512_mib")
        result = checker.receipt_file(self.root)
        self.assertEqual(result["media"]["status"], "not_regular_file")

    def test_source_change_and_permission_error_are_visible(self):
        with patch.object(checker, "fingerprint", side_effect=[{"sha256": "a"}, {"sha256": "b"}]), \
             patch.object(checker, "probe_video", return_value={"status": "ok"}):
            result = checker.receipt_file(self.path)
        self.assertFalse(result["original_unchanged"])
        self.assertEqual(result["media"]["status"], "source_changed_during_check")
        with patch.object(checker, "fingerprint", side_effect=PermissionError):
            self.assertEqual(checker.receipt_file(self.path)["media"]["status"], "access_denied")

    def test_cli_missing_files_returns_failure_and_keeps_target_null(self):
        cmd = [checker.sys.executable, str(Path(checker.__file__)), "--file-only",
               "--data-root", str(self.root), "--video", "missing.mp4"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 2)
        result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "not_run_files_missing")
        self.assertIsNone(result["selected"][0]["target"])
        self.assertNotIn(str(self.root), proc.stdout)

    def test_cli_annotation_options_rejected_in_file_only_mode(self):
        with patch.object(checker.sys, "argv", ["inspect", "--file-only", "--data-root", str(self.root),
                                                "--video", self.path.name, "--time-unit", "seconds"]), \
             patch("builtins.print") as printed:
            self.assertEqual(checker.main(), 2)
        self.assertEqual(json.loads(printed.call_args.args[0])["reason"], "annotation_options_require_metadata_mode")

    @unittest.skipUnless(importlib.util.find_spec("av"), "PyAV not installed")
    def test_real_receipt_worker_keeps_corrupt_file_unlabelled(self):
        before = self.path.read_bytes()
        result = checker.inspect_files(self.root, [self.path.name])
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(result["selected"][0]["media"]["status"], "media_open_or_decode_failed")
        self.assertIsNone(result["selected"][0]["target"])
        self.assertEqual(self.path.read_bytes(), before)

    @unittest.skipUnless(importlib.util.find_spec("av"), "PyAV not installed")
    def test_real_receipt_worker_reports_nine_frames_without_saving_frames(self):
        import av
        path = self.root / "test-only.avi"
        with av.open(str(path), "w") as container:
            stream = container.add_stream("mpeg4", rate=10)
            stream.width, stream.height, stream.pix_fmt = 32, 24, "yuv420p"
            for index in range(30):
                frame = av.VideoFrame(32, 24, "yuv420p")
                for plane in frame.planes:
                    plane.update(bytes([index * 4]) * plane.buffer_size)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        before = checker.fingerprint(path)
        result = checker.inspect_files(self.root, [path.name])
        self.assertEqual(result["status"], "files_checked_labels_unreviewed")
        item = result["selected"][0]
        self.assertEqual(sum(len(s["frames"]) for s in item["media"]["decode_samples"]), 9)
        self.assertFalse(item["media"]["full_video_checked"])
        self.assertEqual(result["frame_files_saved"], 0)
        self.assertEqual(checker.fingerprint(path), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["candidate.mp4", "test-only.avi"])


if __name__ == "__main__":
    unittest.main()
