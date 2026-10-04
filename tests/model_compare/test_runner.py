"""Bounded orchestration tests: synthetic files and stdlib subprocesses only."""

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

from apps.model_compare import runner


REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / "artifacts/runpod/model-comparison-p2-20261001-01/comparison-contract.json"


def save_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def file_spec(path):
    payload = path.read_bytes()
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cctv-runner-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "work"
        self.root.mkdir()
        self.bundle = Path(self.temporary.name) / "bundle"
        self.bundle.mkdir()
        self.contract = json.loads(CONTRACT.read_text())
        inputs = self.root / runner.INPUTS
        inputs.mkdir()
        for item in self.contract["inputs"]:
            path = inputs / item["file"]
            path.write_bytes(("synthetic, not a video: " + item["input_id"]).encode())
            item.update(file_spec(path))
        self.identity = {"contract.json": "test-contract", "runner.py": "test-runner"}
        self.machine = {"host": "synthetic-host", "boot_id": "synthetic-boot"}
        self.launched = []

    @property
    def trial(self):
        return self.root / runner.TRIAL

    def execute(self, process=None):
        if process is None:
            process = self.successful_process
        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(runner, "run_process", side_effect=process) as launch:
                with runner.trial_lock(self.root) as lock_fd:
                    state = runner.execute(self.contract, self.identity, self.root, self.bundle,
                                           Path(sys.executable), self.machine, lock_fd)
        return state, launch

    def make_result(self, output, version="A", input_id="input_001", candidates=0):
        output.mkdir(parents=True)
        raw = output / "raw.json"
        raw.write_text('{"synthetic":true}')
        artifacts = [{"path": raw.name, **file_spec(raw)}]
        if candidates:
            # Bytes test identity only; model/media decoding belongs to job.py tests.
            for name in ("clip.mp4", "frame.jpg"):
                path = output / name
                path.write_bytes(b"synthetic artifact; never decoded")
                artifacts.append({"path": name, **file_spec(path)})
        item = next(i for i in self.contract["inputs"] if i["input_id"] == input_id)
        value = {"version": version, "input_sha256": item["sha256"],
                 "status": "completed" if candidates else "no_candidates",
                 "event_count": candidates, "gpu": "NVIDIA RTX A4500",
                 "artifacts": artifacts, "timings": {"total_seconds": 0.01},
                 "memory": {"peak_allocated_bytes": 1234}}
        save_json(output / "result.json", value)
        return value

    def successful_process(self, command, log_path, seconds, lock_fd, env):
        output = Path(command[command.index("--output") + 1])
        input_id = Path(command[command.index("--input") + 1]).stem
        version = command[command.index("--version") + 1]
        key = input_id + "-" + version
        before = runner.json_read(self.trial / "state.json")
        stored = next(job for job in before["jobs"] if runner.job_key(job) == key)
        self.assertEqual((stored["status"], stored["attempts"]), ("running", 1))
        self.assertNotIn(key, self.launched)
        self.assertGreater(seconds, 0)
        self.assertLessEqual(seconds, 300)
        self.launched.append(key)
        self.make_result(output, version, input_id, candidates=int(version == "B"))
        log_path.write_text("synthetic child completed")
        return {"status": "exited", "returncode": 0, "subprocess_wall_seconds": 0.01}

    def store_state(self, state):
        self.trial.mkdir()
        runner.atomic_json(self.trial / "state.json", state)

    def test_twenty_intents_before_launch_and_completed_results_reused(self):
        state, launch = self.execute()
        self.assertEqual(launch.call_count, 20)
        self.assertEqual(state["status"], "completed_reference_trial")
        self.assertEqual(sum(job["attempts"] for job in state["jobs"]), 20)
        self.assertEqual([job["status"] for job in state["jobs"]],
                         [status for _ in range(10) for status in ("no_candidates", "completed")])
        before = {p.relative_to(self.trial): file_spec(p) for p in self.trial.glob("*/output/*")}
        again, launch = self.execute(lambda *a: self.fail("Completed result was resubmitted"))
        self.assertEqual(launch.call_count, 0)
        self.assertEqual(again["deadline_monotonic"], state["deadline_monotonic"])
        self.assertEqual(before, {p.relative_to(self.trial): file_spec(p)
                                 for p in self.trial.glob("*/output/*")})

    def test_failed_subprocess_stops_following_jobs_and_restart(self):
        def failed(*args):
            return {"status": "failed", "returncode": 7, "subprocess_wall_seconds": 0.01}
        state, launch = self.execute(failed)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(state["status"], "stopped_on_failure")
        self.assertEqual(state["jobs"][0]["status"], "failed")
        self.assertTrue(all(job["attempts"] == 0 for job in state["jobs"][1:]))
        again, launch = self.execute(lambda *a: self.fail("Failed attempt was repeated"))
        self.assertEqual(launch.call_count, 0)
        self.assertEqual(again["status"], "blocked_previous_attempt")

    def test_persisted_running_intent_becomes_unknown_without_launch(self):
        state = runner.new_state(self.contract, self.identity, self.machine)
        state["jobs"][0].update(status="running", attempts=1)
        self.store_state(state)
        first, launch = self.execute(lambda *a: self.fail("Unknown attempt was repeated"))
        self.assertEqual(launch.call_count, 0)
        self.assertEqual(first["jobs"][0]["status"], "unknown_after_interruption")
        second, launch = self.execute(lambda *a: self.fail("Unknown attempt was repeated twice"))
        self.assertEqual(launch.call_count, 0)
        self.assertEqual(second["jobs"][0]["attempts"], 1)

    def test_expired_deadline_is_preserved_and_launches_nothing(self):
        state = runner.new_state(self.contract, self.identity, self.machine,
                                 now=time.monotonic() - 1900)
        self.store_state(state)
        result, launch = self.execute(lambda *a: self.fail("Expired trial launched a process"))
        self.assertEqual(launch.call_count, 0)
        self.assertEqual(result["status"], "whole_trial_time_limit")
        self.assertEqual(result["deadline_monotonic"], state["deadline_monotonic"])
        self.assertTrue(all(j["status"] == "not_run_time_limit" and j["attempts"] == 0
                            for j in result["jobs"]))

    def test_global_remaining_time_limits_first_launch(self):
        state = runner.new_state(self.contract, self.identity, self.machine,
                                 now=time.monotonic() - 1799)
        self.store_state(state)
        allowances = []
        def timed_out(command, log, seconds, lock, env):
            allowances.append(seconds)
            return {"status": "timed_out", "returncode": -9, "subprocess_wall_seconds": seconds}
        result, launch = self.execute(timed_out)
        self.assertEqual(launch.call_count, 1)
        self.assertGreater(allowances[0], 0)
        self.assertLessEqual(allowances[0], 1)
        self.assertEqual(result["deadline_monotonic"], state["deadline_monotonic"])
        self.assertEqual(result["jobs"][0]["status"], "timed_out")

    def test_changed_code_pod_roster_and_deadline_refuse_resume(self):
        original = runner.new_state(self.contract, self.identity, self.machine)
        for mutation in ("identity", "machine", "deadline", "roster", "attempts"):
            with self.subTest(mutation=mutation):
                state = copy.deepcopy(original)
                if mutation in {"identity", "machine"}:
                    state[mutation] = {"altered": True}
                elif mutation == "deadline":
                    state["deadline_monotonic"] += 1
                elif mutation == "roster":
                    state["jobs"][0]["version"] = "B"
                else:
                    state["jobs"][0]["attempts"] = 1
                with self.assertRaises(ValueError):
                    runner.validate_state(state, self.contract, self.identity, self.machine)

    def test_concurrent_process_cannot_obtain_trial_lock(self):
        script = ("import fcntl,sys; f=open(sys.argv[1],'a+');\n"
                  "try: fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                  "except BlockingIOError: sys.exit(23)\n"
                  "sys.exit(0)\n")
        with runner.trial_lock(self.root):
            result = subprocess.run([sys.executable, "-B", "-c", script,
                                     str(self.root / ".ab-comparison.lock")], timeout=5,
                                    capture_output=True)
            self.assertEqual(result.returncode, 23, result.stderr.decode())
        with runner.trial_lock(self.root):
            pass

    def test_real_short_timeout_kills_parent_and_descendant(self):
        child_info = self.root / "processes.json"
        code = ("import json,os,subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                "pass_fds=(int(sys.argv[2]),)); "
                "open(sys.argv[1],'w').write(json.dumps([os.getpid(),p.pid])); "
                "time.sleep(60)")
        with runner.trial_lock(self.root) as lock_fd:
            result = runner.run_process([sys.executable, "-B", "-c", code, str(child_info), str(lock_fd)],
                                        self.root / "timeout.log", 0.5, lock_fd, os.environ.copy())
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(result["returncode"], -signal.SIGKILL)
        self.assertLess(result["subprocess_wall_seconds"], 5)
        self.assertTrue(child_info.is_file(), "Synthetic child never started")
        pids = json.loads(child_info.read_text())
        try:
            # Both descendants inherited the locked open-file description. The
            # GPU lock can be reacquired only once both have closed it/exited.
            # This also treats a reparented zombie as dead without invoking ps.
            deadline = time.monotonic() + 2
            while True:
                try:
                    with runner.trial_lock(self.root):
                        break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        self.fail("Timed-out descendants still hold the GPU lock")
                time.sleep(0.02)
        finally:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_invalid_result_artifacts_identity_and_measurements_rejected(self):
        cases = ("missing", "tampered", "path_escape", "symlink", "duplicate", "wrong_version",
                 "wrong_input", "wrong_gpu", "false_no_candidates", "nan", "missing_media")
        for case in cases:
            with self.subTest(case=case):
                output = self.root / ("result-" + case)
                value = self.make_result(output, candidates=int(case == "missing_media"))
                raw = output / "raw.json"
                if case == "missing":
                    raw.unlink()
                elif case == "tampered":
                    raw.write_text("changed")
                elif case == "path_escape":
                    value["artifacts"][0]["path"] = "../outside.json"
                elif case == "symlink":
                    target = self.root / "outside.json"
                    target.write_bytes(raw.read_bytes())
                    raw.unlink()
                    raw.symlink_to(target)
                elif case == "duplicate":
                    value["artifacts"].append(copy.deepcopy(value["artifacts"][0]))
                elif case == "wrong_version":
                    value["version"] = "B"
                elif case == "wrong_input":
                    value["input_sha256"] = "0" * 64
                elif case == "wrong_gpu":
                    value["gpu"] = "different GPU"
                elif case == "false_no_candidates":
                    value["event_count"] = 1
                elif case == "nan":
                    value["timings"]["total_seconds"] = float("nan")
                else:
                    value["artifacts"] = value["artifacts"][:1]
                save_json(output / "result.json", value)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    runner.verified_result(output, {"version": "A"}, self.contract["inputs"][0])

    def test_zero_exit_without_result_is_failed_and_not_replayed(self):
        def missing(*args):
            return {"status": "exited", "returncode": 0, "subprocess_wall_seconds": 0.01}
        state, launch = self.execute(missing)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(state["jobs"][0]["status"], "failed")
        self.assertEqual(state["status"], "stopped_on_failure")
        again, launch = self.execute(lambda *a: self.fail("Incomplete attempt was replayed"))
        self.assertEqual(launch.call_count, 0)

    def prepare_preflight(self):
        source_specs = {}
        for version, dirname in runner.SOURCE_DIRS.items():
            source = self.root / dirname
            source.mkdir()
            path = source / "pipeline.py"
            path.write_text("# Synthetic source identity fixture only\n")
            source_specs[version] = {"pipeline.py": file_spec(path)}
        python = self.root / runner.SOURCE_DIRS["A"] / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("synthetic nonexecutable Python marker")
        save_json(self.bundle / "contract.json", self.contract)
        save_json(self.bundle / "source-files.json", source_specs)
        (self.bundle / "runner.py").write_text("# synthetic runner marker\n")
        (self.bundle / "job.py").write_text("# synthetic job marker\n")
        save_json(self.bundle / "bundle-manifest.json",
                  {"files": {p.name: file_spec(p) for p in self.bundle.iterdir()}})

    def test_preflight_checks_read_only_and_rejects_tampered_input_or_code(self):
        self.prepare_preflight()
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("Preflight launched process")):
            contract, identity, python = runner.preflight(self.bundle, self.root)
            self.assertEqual(len(contract["inputs"]), 10)
            self.assertEqual(set(identity), {"contract.json", "source-files.json", "runner.py", "job.py"})
            self.assertFalse(self.trial.exists())
            for target in (self.root / runner.INPUTS / "input_001.mp4",
                           self.root / runner.SOURCE_DIRS["B"] / "pipeline.py",
                           self.bundle / "contract.json"):
                old = target.read_bytes()
                target.write_bytes(old + b"altered")
                with self.assertRaises(ValueError):
                    runner.preflight(self.bundle, self.root)
                target.write_bytes(old)

    def test_contract_rejects_changed_limits_extra_versions_and_reordered_jobs(self):
        for mutation in ("limits", "versions", "order", "input_count", "filename"):
            with self.subTest(mutation=mutation):
                contract = copy.deepcopy(self.contract)
                if mutation == "limits":
                    contract["limits"]["max_attempts_per_input_version"] = 2
                elif mutation == "versions":
                    contract["versions"]["C"] = {}
                elif mutation == "order":
                    contract["run_order"].reverse()
                elif mutation == "input_count":
                    contract["inputs"].pop()
                else:
                    contract["inputs"][0]["file"] = "../video.mp4"
                with self.assertRaises(ValueError):
                    runner.validate_contract(contract)

    def test_export_roundtrip_contains_originals_ledger_raw_logs_and_verified_manifest(self):
        state, _ = self.execute()
        for item in self.contract["inputs"]:
            runner.verify_file(self.trial / "inputs", item["file"], item)
        expected = {p.relative_to(self.trial).as_posix(): file_spec(p)
                    for p in self.trial.rglob("*") if p.is_file()}
        with mock.patch.object(runner, "run_process", side_effect=AssertionError("Export invoked model job")):
            with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("Export launched process")):
                receipt = runner.export_results(self.root)
        archive = Path(receipt["archive"])
        self.assertEqual(receipt["status"], "ab_results_exported")
        self.assertEqual(receipt["model_calls"], 0)
        self.assertEqual(receipt["files"], len(expected))
        self.assertEqual(receipt["sha256"], file_spec(archive)["sha256"])
        self.assertEqual(receipt["bytes"], archive.stat().st_size)
        self.assertEqual(json.loads(archive.with_suffix(".json").read_text()), receipt)
        with zipfile.ZipFile(archive) as saved:
            self.assertIsNone(saved.testzip())
            root = runner.TRIAL + "/"
            self.assertEqual(set(saved.namelist()),
                             {root + name for name in expected} | {root + "export-manifest.json"})
            self.assertEqual(len(saved.namelist()), len(expected) + 1)
            manifest = json.loads(saved.read(root + "export-manifest.json"))
            self.assertEqual(manifest["schema_version"], "cctv-ab-export-v1")
            self.assertEqual(manifest["files"], expected)
            for name, spec in expected.items():
                payload = saved.read(root + name)
                self.assertEqual(len(payload), spec["bytes"])
                self.assertEqual(hashlib.sha256(payload).hexdigest(), spec["sha256"])
            self.assertEqual(json.loads(saved.read(root + "state.json")), state)
            for item in self.contract["inputs"]:
                self.assertEqual(saved.read(root + "inputs/" + item["file"]),
                                 (self.root / runner.INPUTS / item["file"]).read_bytes())
            for key in ("input_001-A", "input_001-B"):
                self.assertIn(root + key + "/worker.log", saved.namelist())
                self.assertIn(root + key + "/output/raw.json", saved.namelist())
            review = saved.read(root + "comparison.html").decode()
            self.assertIn('src="inputs/input_001.mp4"', review)
        self.assertEqual(expected, {p.relative_to(self.trial).as_posix(): file_spec(p)
                                    for p in self.trial.rglob("*") if p.is_file()})

    def test_identical_export_refused_preserving_archive_and_receipt(self):
        self.execute()
        receipt = runner.export_results(self.root)
        archive = Path(receipt["archive"])
        paths = (archive, archive.with_suffix(".json"))
        before = {p: (file_spec(p), p.stat().st_mtime_ns) for p in paths}
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("Duplicate export launched process")):
            with self.assertRaises(FileExistsError):
                runner.export_results(self.root)
        self.assertEqual(before, {p: (file_spec(p), p.stat().st_mtime_ns) for p in paths})
        self.assertEqual(list(self.root.glob("*-results-*.zip")), [archive])

    def test_export_cli_does_not_require_model_environment_or_launch_jobs(self):
        self.execute()
        before = file_spec(self.trial / "state.json")
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["runner.py", "--work-root", str(self.root), "--export"]):
            with mock.patch.object(runner, "preflight", side_effect=AssertionError("Export checked model environment")):
                with mock.patch.object(runner, "run_process", side_effect=AssertionError("Export launched a job")):
                    with contextlib.redirect_stdout(stdout):
                        runner.main()
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt["status"], "ab_results_exported")
        self.assertEqual(receipt["model_calls"], 0)
        self.assertTrue(Path(receipt["archive"]).is_file())
        self.assertEqual(before, file_spec(self.trial / "state.json"))

    def test_export_refuses_linked_trial_or_result_without_archive(self):
        self.execute()
        outside = self.root / "outside.json"
        outside.write_text("must never be exported through a link")
        link = self.trial / "linked.json"
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            runner.export_results(self.root)
        self.assertEqual(list(self.root.glob("*-results-*.zip")), [])
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_text(), "must never be exported through a link")
        linked_root = Path(self.temporary.name) / "linked-work"
        linked_root.mkdir()
        (linked_root / runner.TRIAL).symlink_to(self.trial, target_is_directory=True)
        with self.assertRaises(ValueError):
            runner.export_results(linked_root)
        self.assertEqual(list(linked_root.glob("*-results-*.zip")), [])

    def test_export_reserved_manifest_refused_without_overwriting_retained_file(self):
        self.execute()
        reserved = self.trial / "export-manifest.json"
        reserved.write_bytes(b'{"retained":"must not be shadowed in ZIP"}\n')
        before = file_spec(reserved), reserved.stat().st_mtime_ns
        with self.assertRaises(ValueError):
            runner.export_results(self.root)
        self.assertEqual(before, (file_spec(reserved), reserved.stat().st_mtime_ns))
        self.assertEqual(list(self.root.glob("*-results-*.zip")), [])
        self.assertEqual(list(self.root.glob("*-results-*.json")), [])

    def test_original_review_copy_reused_and_tampering_not_overwritten(self):
        self.execute()
        originals = list((self.trial / "inputs").glob("*.mp4"))
        self.assertEqual(len(originals), 10)
        before = {p: (file_spec(p), p.stat().st_mtime_ns) for p in originals}
        self.execute(lambda *a: self.fail("Reuse should not launch jobs"))
        self.assertEqual(before, {p: (file_spec(p), p.stat().st_mtime_ns) for p in originals})
        changed = originals[0]
        changed.write_bytes(b"damaged review copy")
        with self.assertRaises(ValueError):
            self.execute(lambda *a: self.fail("Corrupt review copy must stop before launch"))
        self.assertEqual(changed.read_bytes(), b"damaged review copy")


if __name__ == "__main__":
    unittest.main()
