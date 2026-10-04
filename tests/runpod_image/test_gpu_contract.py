"""Exact GPU contracts with synthetic files/processes; no model or device calls."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from apps.model_compare import job, runner
from apps.runpod_image import image_runtime as runtime


def write_json(path, value):
    path.write_text(json.dumps(value))


def spec(path):
    return {"bytes": path.stat().st_size, "sha256": runtime.file_hash(path)}


class GPUContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cctv-gpu-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.image = self.root / "image"
        for name in ("runtime", "sources/cctv-runpod-a4000", "sources/deployment_handoff_1001"):
            (self.image / name).mkdir(parents=True)
        for name in ("image_runtime.py", "runner.py", "job.py"):
            (self.image / "runtime" / name).write_text("# synthetic identity-only source\n")
        self.manifest = {"schema_version": "cctv-runpod-image-v2", "default_version": "A",
                         "supported_versions": ["A", "B"], "gpu": runtime.RTX_GPU,
                         "files": {p.relative_to(self.image).as_posix(): spec(p)
                                   for p in self.image.rglob("*.py")}}
        self.manifest_file = self.image / "image-manifest.json"
        write_json(self.manifest_file, self.manifest)
        video = self.root / "input.mp4"
        video.write_bytes(b"Synthetic input, never decoded")
        self.plan = {"schema_version": "cctv-image-run-plan-v1", "approved": True,
                     "plan_id": "rtx-synthetic", "state_root": str(runtime.STATE_ROOT),
                     "image_manifest_sha256": runtime.file_hash(self.manifest_file),
                     "gpu": runtime.RTX_GPU,
                     "jobs": [{"input_id": "input", "input": str(video), "version": "B", **spec(video)}],
                     "limits": {"max_jobs": 1, "max_attempts_per_job": 1, "gpu_concurrency": 1,
                                "per_job_timeout_seconds": 2, "whole_plan_timeout_seconds": 20,
                                "automatic_retries": 0, "gemini_calls": 0, "embedding_calls": 0}}
        self.plan_file = self.root / "plan.json"
        write_json(self.plan_file, self.plan)
        self.identity = {"plan_sha256": runtime.file_hash(self.plan_file),
                         "image_manifest_sha256": self.plan["image_manifest_sha256"]}
        self.state_root = self.root / "state"
        self.state_root.mkdir()
        # Any accidental model import or external operation fails these tests.
        self.modules = patch.dict(sys.modules, {name: None for name in
            ("torch", "torchvision", "pytorchvideo", "ultralytics", "cv2", "av", "numpy")})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.network = patch("socket.socket", side_effect=AssertionError("No network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    @property
    def folder(self):
        return self.state_root / self.plan["plan_id"]

    def execute(self, callback):
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(runner, "run_process", side_effect=callback) as process, \
                runner.trial_lock(self.state_root) as lock:
            state = runtime.execute(self.plan, self.identity, self.image, self.state_root, "B", runner, lock)
        return state, process

    def success(self, command, log, seconds, lock, env):
        self.assertEqual(command[-2:], ["--expected-gpu", runtime.RTX_GPU])
        self.assertGreater(seconds, 0)
        self.assertLessEqual(seconds, self.plan["limits"]["per_job_timeout_seconds"])
        intent = runtime.read_json(self.folder / "state.json")
        self.assertEqual((intent["jobs"][0]["status"], intent["jobs"][0]["attempts"]), ("running", 1))
        output = Path(command[command.index("--output") + 1])
        output.mkdir()
        raw = output / "raw.json"
        raw.write_text('{"synthetic":true}')
        write_json(output / "result.json", {"version": "B", "status": "no_candidates",
            "input_sha256": self.plan["jobs"][0]["sha256"], "event_count": 0,
            "gpu": runtime.RTX_GPU, "timings": {"total_seconds": .01},
            "memory": {"peak_allocated_bytes": 1}, "artifacts": [{"path": raw.name, **spec(raw)}]})
        return {"status": "exited", "returncode": 0, "subprocess_wall_seconds": .01}

    def test_actual_device_must_match_explicit_contract_without_relabeling(self):
        environment = {"gpu": runtime.RTX_GPU, "memory_total_mib": 16380}
        original = deepcopy(environment)
        job.validate_gpu(environment, 1, runtime.RTX_GPU)
        self.assertEqual(environment, original)
        for expected, count in ((runtime.GPU, 1), (runtime.RTX_GPU, 0),
                                (runtime.RTX_GPU, 2), (runtime.RTX_GPU, True)):
            with self.subTest(expected=expected, count=count), self.assertRaises(RuntimeError):
                job.validate_gpu(environment, count, expected)
        with self.assertRaises(ValueError):
            job.validate_gpu(environment, 1, "CPU")
        job.validate_gpu({"gpu": runtime.GPU}, 1)  # Historical default remains exact.

    def test_child_cli_forwards_gpu_and_defaults_to_legacy(self):
        argv = ["job.py", "--work-root", str(self.root), "--version", "B",
                "--input", str(self.root / "input.mp4"), "--output", str(self.root / "output")]
        for extra, expected in (([], runtime.GPU), (["--expected-gpu", runtime.RTX_GPU], runtime.RTX_GPU)):
            with patch.object(sys, "argv", argv + extra), patch.object(job, "run_job") as call:
                job.main()
                self.assertEqual(call.call_args.kwargs, {"expected_gpu": expected})

    def test_manifest_requires_versioned_exact_rtx_and_reports_manifest_gpu(self):
        manifest, digest = runtime.inspect_image(self.image)
        self.assertEqual(manifest["gpu"], runtime.RTX_GPU)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runtime.main(["--image-root", str(self.image), "inspect"]), 0)
        self.assertEqual(json.loads(output.getvalue())["gpu"], runtime.RTX_GPU)
        for changed in ({"schema_version": "cctv-runpod-image-v1"}, {"gpu": "NVIDIA RTX 4090"}):
            write_json(self.manifest_file, dict(self.manifest, **changed))
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                runtime.inspect_image(self.image)
        write_json(self.manifest_file, dict(self.manifest, schema_version="cctv-runpod-image-v1", gpu=runtime.GPU))
        self.assertEqual(runtime.inspect_image(self.image)[0]["gpu"], runtime.GPU)

    def test_rtx_plan_requires_explicit_gpu_and_legacy_default_never_accepts_it(self):
        loaded, _ = runtime.load_plan(self.plan_file, self.plan["image_manifest_sha256"], runtime.RTX_GPU)
        self.assertEqual(loaded["gpu"], runtime.RTX_GPU)
        with self.assertRaisesRegex(ValueError, "Plan GPU"):
            runtime.load_plan(self.plan_file, self.plan["image_manifest_sha256"])
        for value in (None, runtime.GPU, "NVIDIA RTX 4090"):
            changed = dict(self.plan)
            if value is None:
                changed.pop("gpu")
            else:
                changed["gpu"] = value
            write_json(self.plan_file, changed)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Plan GPU"):
                runtime.load_plan(self.plan_file, self.plan["image_manifest_sha256"], runtime.RTX_GPU)
        changed.pop("gpu")
        write_json(self.plan_file, changed)
        self.assertNotIn("gpu", runtime.load_plan(self.plan_file, self.plan["image_manifest_sha256"])[0])

    def test_rtx_completion_and_resume_preserve_actual_gpu_attempt_and_deadline(self):
        first, process = self.execute(self.success)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(first["status"], "completed_plan")
        self.assertEqual(first["jobs"][0]["result"]["gpu"], runtime.RTX_GPU)
        second, process = self.execute(lambda *args: self.fail("Repeated GPU attempt"))
        self.assertEqual(process.call_count, 0)
        self.assertEqual(second["jobs"][0]["attempts"], 1)
        self.assertEqual(second["deadline_at"], first["deadline_at"])
        result = self.folder / "input-B/output/result.json"
        before = result.read_bytes()
        # The new result cannot be silently consumed through the old default.
        with self.assertRaisesRegex(ValueError, "GPU"):
            runner.verified_result(result.parent, self.plan["jobs"][0], self.plan["jobs"][0])
        self.assertEqual(result.read_bytes(), before)

    def test_wrong_gpu_result_blocks_completion_and_retained_result_resume(self):
        def mislabeled(*args):
            outcome = self.success(*args)
            path = self.folder / "input-B/output/result.json"
            value = runtime.read_json(path)
            value["gpu"] = runtime.GPU
            write_json(path, value)
            return outcome
        state, _ = self.execute(mislabeled)
        self.assertEqual(state["status"], "stopped_on_failure")
        self.assertEqual(state["jobs"][0]["attempts"], 1)
        state, process = self.execute(lambda *args: self.fail("Failed attempt repeated"))
        self.assertEqual(state["status"], "blocked_previous_attempt")
        self.assertEqual(process.call_count, 0)

    def test_saved_success_gpu_tamper_rejects_resume_even_with_new_result_digest(self):
        state, _ = self.execute(self.success)
        path = self.folder / "input-B/output/result.json"
        value = runtime.read_json(path)
        value["gpu"] = runtime.GPU
        write_json(path, value)
        state["jobs"][0]["result_sha256"] = runtime.file_hash(path)
        write_json(self.folder / "state.json", state)
        with self.assertRaisesRegex(ValueError, "GPU"):
            self.execute(lambda *args: self.fail("Tampered result caused new attempt"))

    def test_execution_rejects_unbound_gpu_or_wrong_manifest_before_state_creation(self):
        self.plan.pop("gpu")
        with self.assertRaisesRegex(ValueError, "Plan GPU"):
            self.execute(lambda *args: self.fail("Unbound GPU launched"))
        self.assertFalse(self.folder.exists())
        self.plan["gpu"] = runtime.RTX_GPU
        self.identity["image_manifest_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "manifest identity"):
            self.execute(lambda *args: self.fail("Changed manifest launched"))
        self.assertFalse(self.folder.exists())

    def test_historical_helpers_keep_three_argument_interface(self):
        def legacy(output, job, item):
            return (output, job, item)
        helpers = types.SimpleNamespace(verified_result=legacy)
        self.assertEqual(runtime.verified_result(helpers, "output", "job", "item"),
                         ("output", "job", "item"))


if __name__ == "__main__":
    unittest.main()
