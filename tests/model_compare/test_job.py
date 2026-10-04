"""Adapter contract tests using stdlib and synthetic objects, without media decode."""
import copy
import hashlib
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from apps.model_compare import job


class JobContractTests(unittest.TestCase):
    def fixture(self):
        return {"duration_s": 2.0, "fps": 25.0,
                "x3d": {"windows": [{"start_s": 0.0, "end_s": 2.0,
                                      "collision_probability": 0.1, "is_positive": False}]},
                "events": [], "vlm_handoffs": []}

    def validate(self, result):
        return job.validate_result(result, {"duration_s": 2.0, "fps": 25.0}, lambda _: [0.0])

    def test_no_candidates_requires_valid_complete_windows(self):
        self.assertEqual(self.validate(self.fixture()), "no_candidates")
        for field, value in (("duration_s", 1.0), ("fps", 24.0)):
            with self.subTest(field=field):
                result = self.fixture()
                result[field] = value
                with self.assertRaises(ValueError):
                    self.validate(result)
        result = self.fixture()
        result["x3d"]["windows"] = []
        with self.assertRaises(ValueError):
            self.validate(result)

    def test_nonfinite_scores_and_window_gaps_fail(self):
        for field, value in (("collision_probability", float("nan")),
                             ("collision_probability", float("inf")),
                             ("collision_probability", 1.1), ("start_s", 0.1),
                             ("end_s", 1.5), ("is_positive", True)):
            with self.subTest(field=field, value=value):
                result = self.fixture()
                result["x3d"]["windows"][0][field] = value
                with self.assertRaises(ValueError):
                    self.validate(result)

    def test_positive_windows_require_matching_event_and_handoff(self):
        result = self.fixture()
        result["x3d"]["windows"][0].update(collision_probability=0.9, is_positive=True)
        with self.assertRaises(ValueError):
            self.validate(result)
        event = {"event_id": "event_000", "candidate_time_s": 1.0}
        result["events"], result["vlm_handoffs"] = [event], [copy.deepcopy(event)]
        self.assertEqual(self.validate(result), "completed")
        result["vlm_handoffs"][0]["event_id"] = "event_001"
        with self.assertRaises(ValueError):
            self.validate(result)

    def test_sidecar_preserves_generic_origin_and_rejects_wrong_names(self):
        mapping = {0: "Car", 1: "Truck", 2: "Bus", 3: "Motorcycle"}
        generic = {i: f"class_{i}" for i in range(4)}
        checked = job.validate_b_classes(generic, mapping, mapping)
        self.assertEqual(checked["checkpoint_names"], generic)
        self.assertEqual(checked["mapping_source"], "provided_sidecar")
        self.assertFalse(checked["independent_training_metadata_verified"])
        for raw in ({0: "Car"}, {**mapping, 3: "Bicycle"}, {**generic, 3: "class_4"}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                job.validate_b_classes(raw, mapping, mapping)

    def test_paths_symlinks_and_manifest_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "raw"
            root.mkdir()
            (root / "data.json").write_bytes(b"{}")
            found = job.artifact_records(root)
            self.assertEqual(found, [{"path": "data.json", "bytes": 2,
                                      "sha256": hashlib.sha256(b"{}").hexdigest()}])
            self.assertEqual(job.contained_file(root, "data.json"), root.resolve() / "data.json")
            outside = Path(temp) / "outside.mp4"
            outside.write_bytes(b"x")
            with self.assertRaises(ValueError):
                job.contained_file(root, outside)
            (root / "linked.mp4").symlink_to(outside)
            with self.assertRaises(ValueError):
                job.contained_file(root, "linked.mp4")
            with self.assertRaises(ValueError):
                job.artifact_records(root)

    def test_success_file_is_exclusive(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.json"
            job.write_new_json(path, {"status": "completed"})
            initial = path.read_bytes()
            with self.assertRaises(FileExistsError):
                job.write_new_json(path, {"status": "no_candidates"})
            self.assertEqual(path.read_bytes(), initial)

    def test_download_guard_denies_python_network_before_connect(self):
        for event, args in (("socket.__new__", (None, socket.AF_INET, 1, 0)),
                            ("socket.__new__", (None, socket.AF_INET6, 1, 0)),
                            ("socket.getaddrinfo", ("example.invalid", 443)),
                            ("urllib.Request", ("https://example.invalid/",))):
            with self.subTest(event=event), self.assertRaises(PermissionError):
                job.block_network_event(event, args)
        job.block_network_event("socket.__new__", (None, socket.AF_UNIX, 1, 0))

    def test_source_import_is_stdlib_only(self):
        script = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch','torchvision','ultralytics','pytorchvideo','numpy','cv2','av'}:
            raise AssertionError('unexpected model/media import: ' + fullname)
sys.meta_path.insert(0, Block())
from apps.model_compare import job
assert callable(job.run_job)
print('stdlib_only')
"""
        result = subprocess.run([sys.executable, "-B", "-c", script],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "stdlib_only")

    def test_output_decode_guard_rejects_truncation_and_bad_pts(self):
        class Source:
            def __init__(self, times):
                self.times = times
                self.streams = types.SimpleNamespace(video=[types.SimpleNamespace(
                    codec_context=types.SimpleNamespace(name="synthetic"))])
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def decode(self, **_):
                return iter(types.SimpleNamespace(width=640, height=480, time=t) for t in self.times)
        for times, passed in (([0, .04, .08], True), ([0, .04], False),
                              ([0, .04, .03], False), ([0, .04, None], False),
                              ([0, .04, .08, .12], False)):
            with self.subTest(times=times), patch.dict(sys.modules, {
                "av": types.SimpleNamespace(open=lambda _: Source(times))
            }):
                if passed:
                    result = job.verify_media(Path("synthetic.mp4"), 3, 25)
                    self.assertEqual(result["decoded_frames"], 3)
                    self.assertTrue(result["pts_verified"])
                else:
                    with self.assertRaises(ValueError):
                        job.verify_media(Path("synthetic.mp4"), 3, 25)

    def test_b_asset_frame_contract_uses_existing_round_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp) / "raw"
            event_root = raw / "event_000"
            event_root.mkdir(parents=True)
            files = {}
            for role, name in (("full_clip", "event_000_full.mp4"),
                               ("roi_clip", "event_000_roi.mp4"),
                               ("annotated_frame", "event_000_objects.jpg")):
                path = event_root / name
                path.write_bytes(b"synthetic only")
                files[role] = str(path)
            (event_root / "event_000_context.json").write_text("{}")
            event = {"event_id": "event_000", "candidate_time_s": 1.0,
                     "incident_context": {"event_id": "event_000", "files": files}}
            with patch.object(job, "verify_media", return_value={}) as decoder:
                records = job.media_records("B", {"events": [event]}, raw,
                                            {"duration_s": 7.0, "fps": 25.0})
            self.assertEqual(len(records), 3)
            self.assertEqual([call.args[1] for call in decoder.call_args_list], [150, 150, 1])
            self.assertTrue(all(row["path"].startswith("raw/event_000/") for row in records))


if __name__ == "__main__":
    unittest.main()
