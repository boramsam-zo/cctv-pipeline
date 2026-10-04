"""No GPU/model imports: synthetic bytes, process results, and bounded processes."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from apps.model_compare import runner
from apps.runpod_image import image_runtime as runtime

REPO = Path(__file__).resolve().parents[2]


def spec(path):
    return {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def write_json(path, data):
    path.write_text(json.dumps(data))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cctv-image-runtime-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.image = self.root / "image"
        for name in ("runtime", "sources/cctv-runpod-a4000", "sources/deployment_handoff_1001"):
            (self.image / name).mkdir(parents=True)
        for name in ("runner.py", "job.py"):
            (self.image / "runtime" / name).write_bytes((REPO / "apps/model_compare" / name).read_bytes())
        (self.image / "runtime/image_runtime.py").write_bytes(Path(runtime.__file__).read_bytes())
        for name in ("cctv-runpod-a4000", "deployment_handoff_1001"):
            (self.image / "sources" / name / "source.py").write_text("# synthetic source\n")
        self.manifest = {"default_version": "A", "supported_versions": ["A", "B"],
                         "gpu": runtime.GPU, "python_version": "3.12.3", "packages": {"torch": "2.8.0+cu128"}}
        self.refresh_image()
        self.state_root = self.root / "state"
        self.state_root.mkdir()
        video = self.root / "input.mp4"
        video.write_bytes(b"Synthetic bytes; never decoded or used for inference")
        self.plan = {"schema_version": "cctv-image-run-plan-v1", "approved": True,
                     "plan_id": "new-synthetic-plan", "image_manifest_sha256": self.manifest_sha,
                     "state_root": str(runtime.STATE_ROOT),
                     "jobs": [{"input_id": "input_001", "input": str(video), "version": version,
                               **spec(video)} for version in ("A", "B")],
                     "limits": {"max_jobs": 2, "max_attempts_per_job": 1, "gpu_concurrency": 1,
                                "per_job_timeout_seconds": 2, "whole_plan_timeout_seconds": 20,
                                "automatic_retries": 0, "gemini_calls": 0, "embedding_calls": 0}}
        self.plan_file = self.root / "plan.json"
        self.refresh_plan()
        self.launched = []

    def refresh_image(self):
        self.manifest["files"] = {p.relative_to(self.image).as_posix(): spec(p)
                                  for p in self.image.rglob("*") if p.is_file() and p.name != "image-manifest.json"}
        write_json(self.image / "image-manifest.json", self.manifest)
        self.manifest_sha = runtime.file_hash(self.image / "image-manifest.json")

    def refresh_plan(self):
        write_json(self.plan_file, self.plan)
        self.plan, plan_sha = runtime.load_plan(self.plan_file, self.manifest_sha)
        self.identity = {"plan_sha256": plan_sha, "image_manifest_sha256": self.manifest_sha}

    @property
    def folder(self):
        return self.state_root / self.plan["plan_id"]

    def execute(self, version="A", process=None):
        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(runner, "run_process", side_effect=process or self.success) as launched:
                with runner.trial_lock(self.state_root) as lock:
                    result = runtime.execute(self.plan, self.identity, self.image, self.state_root,
                                             version, runner, lock)
        return result, launched

    def success(self, command, log, seconds, lock, env):
        version = command[command.index("--version") + 1]
        copied_input = Path(command[command.index("--input") + 1])
        output = Path(command[command.index("--output") + 1])
        intent = runtime.read_json(self.folder / "state.json")
        job = next(row for row in intent["jobs"] if row["version"] == version)
        self.assertEqual((job["status"], job["attempts"]), ("running", 1))
        self.assertEqual(copied_input.parent.parent, self.folder)
        self.assertNotEqual(str(copied_input), job["input"])
        self.assertEqual(runtime.file_hash(copied_input), job["sha256"])
        self.assertGreater(seconds, 0)
        self.assertLessEqual(seconds, self.plan["limits"]["per_job_timeout_seconds"])
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("GEMINI_API_KEY", env)
        self.launched.append(version)
        output.mkdir()
        (output / "raw.json").write_text('{"synthetic":true}')
        artifacts = [{"path": "raw.json", **spec(output / "raw.json")}]
        if version == "B":
            for name in ("clip.mp4", "frame.jpg"):
                (output / name).write_bytes(b"Not decoded; synthetic artifact identity test")
                artifacts.append({"path": name, **spec(output / name)})
        value = {"version": version, "status": "no_candidates" if version == "A" else "completed",
                 "input_sha256": job["sha256"], "event_count": int(version == "B"),
                 "gpu": runtime.GPU, "timings": {"total_seconds": 0.001},
                 "memory": {"peak_allocated_bytes": 1}, "artifacts": artifacts}
        write_json(output / "result.json", value)
        log.write_text("Synthetic child")
        return {"status": "exited", "returncode": 0, "subprocess_wall_seconds": 0.01}

    def save_initial(self, age=0, mutate=None):
        state = runtime.new_state(self.plan, self.identity, time.time() - age)
        if mutate:
            mutate(state)
        self.folder.mkdir()
        write_json(self.folder / "plan.json", self.plan)
        write_json(self.folder / "state.json", state)
        return state

    def test_default_inspects_only_and_imports_no_model(self):
        before = set(sys.modules)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = runtime.main(["--image-root", str(self.image)])
        self.assertEqual(result, 0)
        self.assertFalse(json.loads(output.getvalue())["automatic_inference"])
        self.assertEqual(list(self.state_root.iterdir()), [])
        self.assertFalse({"torch", "cv2", "ultralytics", "pytorchvideo", "av"} & (set(sys.modules) - before))
        with mock.patch.object(runtime.importlib.metadata, "version", return_value="2.8.0+cu128"), \
                mock.patch.object(runtime.platform, "python_version", return_value="3.12.3"):
            self.assertEqual(runtime.check_environment(self.manifest)["status"], "environment_matches")
        with mock.patch.object(runtime.importlib.metadata, "version", return_value="wrong"):
            self.assertEqual(runtime.check_environment(self.manifest)["status"], "environment_mismatch")

    def test_invalid_versions_never_fall_back_to_a(self):
        for value in (None, "", "C", "a"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.execute(value)
        self.assertEqual(list(self.state_root.iterdir()), [self.state_root / ".ab-comparison.lock"])

    def test_selected_versions_share_ledger_completed_results_reused(self):
        first, launched = self.execute("A")
        self.assertEqual(launched.call_count, 1)
        self.assertEqual(first["status"], "ready_for_other_version")
        self.assertEqual([j["status"] for j in first["jobs"]], ["no_candidates", "not_started"])
        second, launched = self.execute("B")
        self.assertEqual(second["status"], "completed_plan")
        self.assertEqual(launched.call_count, 1)
        self.assertEqual(second["deadline_at"], first["deadline_at"])
        for version in ("A", "B"):
            _, launched = self.execute(version, lambda *a: self.fail("Repeated inference"))
            self.assertEqual(launched.call_count, 0)
        self.assertEqual(self.launched, ["A", "B"])

    def test_production_cli_rejects_alternate_state_root_without_replaying_same_plan(self):
        state, _ = self.execute("A")
        self.assertEqual(state["jobs"][0]["attempts"], 1)
        for alternate in (self.state_root, self.root / "second-ledger"):
            with self.subTest(alternate=alternate), \
                    mock.patch.object(runtime, "inspect_image") as inspect, \
                    mock.patch.object(runtime, "execute") as execute:
                with self.assertRaisesRegex(ValueError, "fixed persistent state root"):
                    runtime.main(["--image-root", str(self.image), "run", "--version", "A",
                                  "--plan", str(self.plan_file), "--state-root", str(alternate)])
                inspect.assert_not_called()
                execute.assert_not_called()
        self.assertEqual(self.launched, ["A"])
        self.assertFalse((self.root / "second-ledger").exists())

    def test_plan_requires_fixed_state_root_for_all_plans(self):
        for name in ("new-plan-one", "new-plan-two"):
            self.plan["plan_id"] = name
            self.refresh_plan()
            self.assertEqual(self.plan["state_root"], str(runtime.STATE_ROOT))
        for root in (None, str(self.state_root), "/workspace/other-state"):
            value = copy.deepcopy(self.plan)
            value["state_root"] = root
            write_json(self.plan_file, value)
            with self.subTest(root=root), self.assertRaisesRegex(ValueError, "fixed persistent"):
                runtime.load_plan(self.plan_file, self.manifest_sha)

    def test_failure_blocks_other_version_and_all_restarts(self):
        first, launched = self.execute(process=lambda *a: {"status": "failed", "returncode": 7})
        self.assertEqual(first["status"], "stopped_on_failure")
        self.assertEqual(first["jobs"][0]["attempts"], 1)
        for version in ("A", "B"):
            state, launched = self.execute(version, lambda *a: self.fail("Failed job repeated"))
            self.assertEqual(state["status"], "blocked_previous_attempt")
            self.assertEqual(launched.call_count, 0)

    def test_uncertain_attempt_with_result_is_not_resubmitted(self):
        def running(state):
            state["jobs"][0].update(status="running", attempts=1)
        self.save_initial(mutate=running)
        first, launch = self.execute("B", lambda *a: self.fail("Unknown job resubmitted"))
        self.assertEqual(first["jobs"][0]["status"], "unknown_after_interruption")
        second, launch = self.execute("A", lambda *a: self.fail("Unknown job resubmitted"))
        self.assertEqual(second["jobs"][0]["attempts"], 1)
        self.assertEqual(launch.call_count, 0)

    def test_deadline_expired_across_restart_and_clock_rollback(self):
        original = self.save_initial(age=30)
        state, launch = self.execute(process=lambda *a: self.fail("Expired job launched"))
        self.assertEqual(state["status"], "whole_plan_time_limit")
        self.assertEqual(state["deadline_at"], original["deadline_at"])
        self.assertEqual(launch.call_count, 0)
        state["last_observed_at"] = time.time() + 100
        write_json(self.folder / "state.json", state)
        with self.assertRaisesRegex(ValueError, "clock"):
            self.execute()

    def test_remaining_global_budget_bounds_subprocess(self):
        self.save_initial(age=19.7)
        def timed_out(command, log, seconds, lock, env):
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, 0.3)
            return {"status": "timed_out", "returncode": -9}
        state, _ = self.execute(process=timed_out)
        self.assertEqual(state["jobs"][0]["status"], "timed_out")

    def test_plan_schema_approval_bounds_duplicates_and_traversal(self):
        cases = []
        for approval in (False, None, 1, "true"):
            p = copy.deepcopy(self.plan); p["approved"] = approval; cases.append(p)
        for limit, value in (("max_jobs", 20), ("per_job_timeout_seconds", None),
                             ("per_job_timeout_seconds", 0), ("whole_plan_timeout_seconds", float("inf")),
                             ("gpu_concurrency", 2), ("automatic_retries", 1), ("gemini_calls", True)):
            p = copy.deepcopy(self.plan); p["limits"][limit] = value; cases.append(p)
        p = copy.deepcopy(self.plan); p["plan_id"] = "comparison-20261001-01"; cases.append(p)
        p = copy.deepcopy(self.plan); p["plan_id"] = "../escape"; cases.append(p)
        p = copy.deepcopy(self.plan); p["jobs"][0]["input"] = "relative.mp4"; cases.append(p)
        p = copy.deepcopy(self.plan); p["jobs"][0]["version"] = "C"; cases.append(p)
        p = copy.deepcopy(self.plan); p["jobs"][1] = copy.deepcopy(p["jobs"][0]); cases.append(p)
        p = copy.deepcopy(self.plan); p["jobs"][1]["sha256"] = "0" * 64; cases.append(p)
        for plan in cases:
            with self.subTest(plan=plan):
                write_json(self.plan_file, plan)
                with self.assertRaises(ValueError):
                    runtime.load_plan(self.plan_file, self.manifest_sha)
        self.assertEqual(list(self.state_root.iterdir()), [])

    def test_image_file_tamper_and_unregistered_code_rejected(self):
        job = self.image / "runtime/job.py"
        original = job.read_bytes()
        job.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "identity"):
            runtime.inspect_image(self.image)
        job.write_bytes(original)
        extra = self.image / "sources/cctv-runpod-a4000/extra.py"
        extra.write_text("# unregistered")
        with self.assertRaisesRegex(ValueError, "Unregistered"):
            runtime.inspect_image(self.image)
        extra.unlink()
        job.unlink()
        job.symlink_to(REPO / "apps/model_compare/job.py")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            runtime.inspect_image(self.image)

    def test_input_tamper_prevents_launch_and_source_changes_do_not_change_copy(self):
        video = Path(self.plan["jobs"][0]["input"])
        original = video.read_bytes()
        video.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "identity"):
            self.execute()
        self.assertEqual(self.launched, [])
        # The partially prepared directory is preserved and requires inspection.
        video.write_bytes(original)
        with self.assertRaises(FileExistsError):
            self.execute()

    def test_readonly_frozen_input_used_when_external_input_changes(self):
        def changed_after_copy(*args):
            Path(self.plan["jobs"][0]["input"]).write_bytes(b"external mutation after copy")
            return self.success(*args)
        state, _ = self.execute(process=changed_after_copy)
        self.assertEqual(state["jobs"][0]["status"], "no_candidates")
        _, launched = self.execute(process=lambda *a: self.fail("Completed job repeated"))
        self.assertEqual(launched.call_count, 0)

    def test_plan_identity_and_completed_artifact_tamper_refuse_resume(self):
        self.execute()
        identity = self.identity.copy()
        self.identity["plan_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "identity"):
            self.execute("B")
        self.identity = identity
        (self.folder / "input_001-A/output/raw.json").write_text("changed")
        with self.assertRaisesRegex(ValueError, "identity"):
            self.execute("B")

    def test_global_lock_covers_different_plans_and_versions(self):
        code = ("import fcntl,sys; f=open(sys.argv[1], 'a+');\n"
                "try: fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                "except BlockingIOError: sys.exit(23)\n")
        with runner.trial_lock(self.state_root):
            result = subprocess.run([sys.executable, "-B", "-c", code,
                                     str(self.state_root / ".ab-comparison.lock")], timeout=3)
        self.assertEqual(result.returncode, 23)

    def test_real_synthetic_subprocess_timeout_is_retained(self):
        job = self.image / "runtime/job.py"
        job.write_text("import time\ntime.sleep(20)\n")
        self.refresh_image()
        self.plan["image_manifest_sha256"] = self.manifest_sha
        self.plan["limits"]["per_job_timeout_seconds"] = 0.08
        self.refresh_plan()
        with contextlib.redirect_stdout(io.StringIO()), runner.trial_lock(self.state_root) as lock:
            state = runtime.execute(self.plan, self.identity, self.image, self.state_root,
                                    "A", runtime.load_helpers(self.image), lock)
        self.assertEqual(state["jobs"][0]["status"], "timed_out")
        self.assertEqual(state["jobs"][0]["attempts"], 1)
        _, launch = self.execute("B", lambda *a: self.fail("Timed-out plan continued"))
        self.assertEqual(launch.call_count, 0)


if __name__ == "__main__":
    unittest.main()
