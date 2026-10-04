"""Inspect an offline A/B image; run only an explicit, separately approved plan.

The state root must be the same persistent volume for every container using a
GPU. Its lock covers all plans/versions; lost state must never be reconstructed
from an old approval. The manifest binds the exact job code. Legacy plans retain
their A4500 default. Portable v3 plans bind the device observed at API startup;
completed results retain that device identity when a later Pod uses another GPU.
"""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import sys
import time

IMAGE_ROOT = Path("/opt/cctv-image")
STATE_ROOT = Path("/workspace/cctv-image-state")
VERSIONS = ("B", "R2")
GPU = "NVIDIA RTX A4500"
RTX_GPU = "NVIDIA RTX 2000 Ada Generation"
PORTABLE_GPU = "NVIDIA CUDA GPU"
SUPPORTED_GPUS = (GPU, RTX_GPU, PORTABLE_GPU)
SUCCESS = {"completed", "no_candidates"}
UNATTEMPTED = {"not_started", "not_run_time_limit"}
ATTEMPTED = SUCCESS | {"running", "failed", "timed_out", "interrupted", "unknown_after_interruption"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def valid_gpu_name(value):
    """An observed CUDA device name, distinct from a selector or contract marker."""
    return (isinstance(value, str) and 1 <= len(value) <= 160 and value == value.strip()
            and value.isascii() and value.isprintable()
            and value.casefold() not in {'auto', 'nvidia cuda gpu', 'gpu', 'cuda', 'unknown', 'none', 'null'}
            and re.search(r'\bcpu\b', value, re.IGNORECASE) is None)


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result
    return json.loads(path.read_bytes(), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def file_hash(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def plain_path(path):
    """Reject links in every component, including links that remain in the root."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("An absolute path without parent traversal is required")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Symlinks are not accepted")
    return path


def relative_file(root, name):
    if (not isinstance(name, str) or not name or "\\" in name
            or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
            or str(PurePosixPath(name)) != name):
        raise ValueError("Unsafe or noncanonical image path")
    path = plain_path(root / name)
    if not path.is_file():
        raise ValueError("Required file missing: " + name)
    return path


def validate_spec(spec):
    if (type(spec.get("bytes")) is not int or spec["bytes"] <= 0
            or not isinstance(spec.get("sha256"), str) or not SHA.fullmatch(spec["sha256"])):
        raise ValueError("Expected positive bytes and a SHA-256")


def check_file(path, spec):
    validate_spec(spec)
    path = plain_path(path)
    if not path.is_file() or path.stat().st_size != spec["bytes"] or file_hash(path) != spec["sha256"]:
        raise ValueError("File identity mismatch: " + path.name)
    return path


def inspect_image(image_root):
    image_root = plain_path(image_root)
    manifest_path = relative_file(image_root, "image-manifest.json")
    manifest = read_json(manifest_path)
    if (manifest.get("default_version") != "B" or manifest.get("supported_versions") != list(VERSIONS)
            or manifest.get("gpu") not in SUPPORTED_GPUS
            or (manifest.get("gpu") == RTX_GPU
                and manifest.get("schema_version") != "cctv-runpod-image-v2")
            or (manifest.get("gpu") == PORTABLE_GPU
                and manifest.get("schema_version") != "cctv-runpod-image-v4")
            or not isinstance(manifest.get("files"), dict)):
        raise ValueError("Unexpected image version/GPU contract")
    files = manifest["files"]
    required = {"runtime/image_runtime.py", "runtime/runner.py", "runtime/job.py"}
    if not required.issubset(files) or "image-manifest.json" in files:
        raise ValueError("Missing runtime identity or recursive image manifest")
    for name, spec in files.items():
        check_file(relative_file(image_root, name), spec)
    for directory in ("runtime", "sources/cctv-runpod-a4000", "sources/deployment_handoff_1001"):
        source = plain_path(image_root / directory)
        if not source.is_dir():
            raise ValueError("Source directory missing")
        for path in source.rglob("*"):
            if path.is_symlink() or (path.is_file() and path.relative_to(image_root).as_posix() not in files):
                raise ValueError("Unregistered or linked source/runtime file")
    return manifest, file_hash(manifest_path)


def check_environment(manifest):
    expected = manifest.get("packages")
    if not isinstance(expected, dict) or not expected or not isinstance(manifest.get("python_version"), str):
        raise ValueError("Expected Python and package versions are missing")
    actual = {}
    for name, version in expected.items():
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError("Invalid package version contract")
        try:
            actual[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual[name] = None
    mismatches = {name: {"expected": version, "actual": actual[name]}
                  for name, version in expected.items() if actual[name] != version}
    python = platform.python_version()
    if python != manifest["python_version"]:
        mismatches["python"] = {"expected": manifest["python_version"], "actual": python}
    return {"status": "environment_matches" if not mismatches else "environment_mismatch",
            "python": python, "packages": actual, "mismatches": mismatches,
            "model_calls": 0, "model_libraries_imported": False,
            "gpu_inference_verified": False}


def load_helpers(image_root):
    path = relative_file(image_root, "runtime/runner.py")
    spec = importlib.util.spec_from_file_location("_cctv_image_frozen_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def plan_gpu(plan, expected_gpu=GPU):
    """Resolve the saved actual device without consulting the current machine."""
    if expected_gpu not in SUPPORTED_GPUS or plan.get("gpu", GPU) != expected_gpu:
        raise ValueError("Plan GPU differs from the image GPU contract")
    if expected_gpu == PORTABLE_GPU:
        observed = plan.get('observed_gpu')
        if not valid_gpu_name(observed):
            raise ValueError("Portable plan requires an observed GPU name")
        return observed
    if 'observed_gpu' in plan and plan['observed_gpu'] != expected_gpu:
        raise ValueError("Plan observed GPU differs from its fixed GPU contract")
    return expected_gpu


def verified_result(helpers, output, job, item, expected_gpu=GPU):
    """Retain the frozen legacy helper API for historical A4500 images."""
    if not valid_gpu_name(expected_gpu):
        raise ValueError("Invalid expected GPU name")
    if expected_gpu == GPU:
        return helpers.verified_result(output, job, item)
    return helpers.verified_result(output, job, item, expected_gpu=expected_gpu)


def load_plan(plan_path, manifest_sha, expected_gpu=GPU):
    plan_path = plain_path(plan_path)
    plan = read_json(plan_path)
    if plan.get("schema_version") != "cctv-image-run-plan-v1" or plan.get("approved") is not True:
        raise ValueError("A separately approved image run plan is required")
    if plan.get("state_root") != str(STATE_ROOT):
        raise ValueError("Plan must use the fixed persistent state root")
    name = plan.get("plan_id")
    if (not isinstance(name, str) or not ID.fullmatch(name)
            or name == "comparison-20261001-01"):
        raise ValueError("A new safe plan_id is required; the P3 trial is consumed")
    if plan.get("image_manifest_sha256") != manifest_sha:
        raise ValueError("Plan does not name this exact image manifest")
    plan_gpu(plan, expected_gpu)
    jobs, limits = plan.get("jobs"), plan.get("limits")
    if not isinstance(jobs, list) or not jobs or not isinstance(limits, dict):
        raise ValueError("Explicit jobs and finite limits are required")
    fixed = {"max_attempts_per_job": 1, "gpu_concurrency": 1, "automatic_retries": 0,
             "gemini_calls": 0, "embedding_calls": 0}
    if any(type(limits.get(k)) is not int or limits[k] != v for k, v in fixed.items()):
        raise ValueError("Only one attempt/slot and zero retries/API calls are supported")
    if type(limits.get("max_jobs")) is not int or limits["max_jobs"] != len(jobs):
        raise ValueError("max_jobs must equal the explicit job list length")
    for name in ("per_job_timeout_seconds", "whole_plan_timeout_seconds"):
        if not positive_number(limits.get(name)):
            raise ValueError("Positive finite timeout required: " + name)
    seen = set()
    inputs = {}
    for job in jobs:
        if not isinstance(job, dict) or set(job) != {"input_id", "version", "input", "sha256", "bytes"}:
            raise ValueError("Unexpected job fields")
        if (not isinstance(job["input_id"], str) or not ID.fullmatch(job["input_id"])
                or job["version"] not in VERSIONS):
            raise ValueError("Invalid input ID or version")
        key = (job["input_id"], job["version"])
        if key in seen:
            raise ValueError("Duplicate input/version in plan")
        seen.add(key)
        validate_spec(job)
        plain_path(job["input"])
        input_spec = (job["input"], job["sha256"], job["bytes"])
        if job["input_id"] in inputs and inputs[job["input_id"]] != input_spec:
            raise ValueError("An input ID must identify identical bytes for A and B")
        inputs[job["input_id"]] = input_spec
    return plan, file_hash(plan_path)


def key(job):
    return job["input_id"] + "-" + job["version"]


def new_state(plan, identity, now):
    deadline = now + plan["limits"]["whole_plan_timeout_seconds"]
    if not positive_number(deadline) or deadline <= now:
        raise ValueError("Plan deadline cannot be represented")
    return {"schema_version": "cctv-image-run-state-v1", "plan_id": plan["plan_id"],
            "identity": identity, "started_at": now, "deadline_at": deadline,
            "last_observed_at": now, "status": "ready", "gemini_calls": 0, "embedding_calls": 0,
            "jobs": [{**job, "status": "not_started", "attempts": 0} for job in plan["jobs"]]}


def validate_state(state, plan, identity, now):
    if (state.get("schema_version") != "cctv-image-run-state-v1"
            or state.get("identity") != identity or state.get("plan_id") != plan["plan_id"]
            or len(state.get("jobs", [])) != len(plan["jobs"])):
        raise ValueError("Plan or image identity changed; resume denied")
    times = [state.get(name) for name in ("started_at", "deadline_at", "last_observed_at")]
    if (not all(positive_number(value) for value in times)
            or state["deadline_at"] != state["started_at"] + plan["limits"]["whole_plan_timeout_seconds"]
            or state["last_observed_at"] < state["started_at"] or now < state["last_observed_at"]):
        raise ValueError("Invalid deadline or clock moved backwards; resume denied")
    for stored, expected in zip(state["jobs"], plan["jobs"]):
        if any(stored.get(name) != value for name, value in expected.items()):
            raise ValueError("Job identity changed")
        status = stored.get("status")
        if (status not in UNATTEMPTED | ATTEMPTED or type(stored.get("attempts")) is not int
                or stored["attempts"] != (0 if status in UNATTEMPTED else 1)):
            raise ValueError("Inconsistent attempt ledger")


def copy_input(item, target):
    """Freeze the input into the ledger before recording a launch intent."""
    target = plain_path(target)
    if not target.exists():
        source = check_file(Path(item["input"]), item)
        with source.open("rb") as src, target.open("xb") as dst:
            for block in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        target.chmod(0o400)
    check_file(target, item)


def execute(plan, identity, image_root, state_root, version, helpers, lock_fd):
    """Caller holds the global lock. Tests supply synthetic process helpers."""
    if version not in VERSIONS or not any(job["version"] == version for job in plan["jobs"]):
        raise ValueError("Selected version is invalid or absent from the plan")
    manifest, manifest_sha = inspect_image(image_root)
    if manifest_sha != identity["image_manifest_sha256"] or plan["image_manifest_sha256"] != manifest_sha:
        raise ValueError("Image manifest identity changed before execution")
    expected_gpu = plan_gpu(plan, manifest["gpu"])
    folder = plain_path(state_root / plan["plan_id"])
    now = time.time()
    if not folder.exists():
        folder.mkdir(mode=0o700)
        state = new_state(plan, identity, now)
        helpers.atomic_json(folder / "plan.json", plan)
        helpers.atomic_json(folder / "state.json", state)
    else:
        stored_plan = read_json(relative_file(folder, "plan.json"))
        if stored_plan != plan:
            raise ValueError("Saved plan changed")
        state = read_json(relative_file(folder, "state.json"))
        validate_state(state, plan, identity, now)
    deadline_monotonic = time.monotonic() + max(0, state["deadline_at"] - now)

    def save():
        observed = time.time()
        if observed < state["last_observed_at"]:
            raise ValueError("Clock moved backwards; processing stopped")
        state["last_observed_at"] = observed
        helpers.atomic_json(folder / "state.json", state)

    # Reconcile every version before permitting any new work in this plan.
    for job in state["jobs"]:
        if job["status"] in SUCCESS:
            check_file(plain_path(folder / key(job) / "input.mp4"), job)
            output = plain_path(folder / key(job) / "output")
            result, result_sha = verified_result(helpers, output, job, job, expected_gpu)
            if job.get("result_sha256") != result_sha:
                raise ValueError("A retained result changed")
            job["result"] = result
        elif job["status"] not in {"not_started"}:
            if job["status"] == "running":
                job["status"] = "unknown_after_interruption"
            state["status"] = "blocked_previous_attempt"
            save()
            return state
    for job in state["jobs"]:
        if job["version"] != version or job["status"] in SUCCESS:
            continue
        remaining = min(state["deadline_at"] - time.time(), deadline_monotonic - time.monotonic())
        if remaining <= 0:
            job["status"] = "not_run_time_limit"
            state["status"] = "whole_plan_time_limit"
            save()
            return state
        # Verify source/runtime identity again immediately before a fresh launch.
        _, current_sha = inspect_image(image_root)
        if current_sha != identity["image_manifest_sha256"]:
            raise ValueError("Image manifest changed before launch")
        jobdir = plain_path(folder / key(job))
        jobdir.mkdir(mode=0o700)  # An interrupted/untracked directory is never overwritten.
        video = jobdir / "input.mp4"
        copy_input(job, video)
        remaining = min(state["deadline_at"] - time.time(), deadline_monotonic - time.monotonic())
        if remaining <= 0:
            job["status"] = "not_run_time_limit"
            state["status"] = "whole_plan_time_limit"
            save()
            return state
        job.update(status="running", attempts=1, started_at=time.time())
        state["status"] = "running"
        save()  # Intent and count reach stable storage before subprocess creation.
        command = [sys.executable, "-B", str(image_root / "runtime/job.py"),
                   "--work-root", str(image_root / "sources"), "--version", version,
                   "--input", str(video), "--output", str(jobdir / "output")]
        if manifest['gpu'] == PORTABLE_GPU or expected_gpu != GPU:
            command.extend(["--expected-gpu", expected_gpu])
        print(json.dumps({"job": key(job), "status": "running", "attempt": 1}), flush=True)
        try:
            allowance = min(plan["limits"]["per_job_timeout_seconds"],
                            state["deadline_at"] - time.time(), deadline_monotonic - time.monotonic())
            if allowance <= 0:
                # Durable intent exists: stop conservatively, never roll its count back.
                raise TimeoutError("Deadline reached after recording intent")
            outcome = helpers.run_process(command, jobdir / "worker.log", allowance,
                                          lock_fd, helpers.clean_environment(jobdir))
            job.update(outcome)
            check_file(video, job)
            if outcome["status"] == "exited":
                result, result_sha = verified_result(
                    helpers, plain_path(jobdir / "output"), job, job, expected_gpu)
                job.update(status=result["status"], result=result, result_sha256=result_sha)
            elif outcome["status"] not in {"failed", "timed_out"}:
                raise ValueError("Unexpected subprocess outcome")
        except BaseException as exc:
            job.update(status="failed" if isinstance(exc, Exception) else "interrupted",
                       error=type(exc).__name__ + ": " + str(exc))
        job["finished_at"] = time.time()
        state["status"] = "running" if job["status"] in SUCCESS else "stopped_on_failure"
        save()
        print(json.dumps({"job": key(job), "status": job["status"],
                          "event_count": job.get("result", {}).get("event_count")}), flush=True)
        if job["status"] not in SUCCESS:
            return state
    state["status"] = ("completed_plan" if all(j["status"] in SUCCESS for j in state["jobs"])
                       else "ready_for_other_version")
    save()
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-root", type=Path, default=IMAGE_ROOT)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("inspect")
    commands.add_parser("check-environment")
    run = commands.add_parser("run")
    run.add_argument("--version", choices=VERSIONS, required=True)
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args(argv)
    # A selectable ledger directory would replay approvals and split the GPU lock.
    # Production always uses one persistent mount, across all plans and versions.
    if args.command == "run" and args.state_root != STATE_ROOT:
        raise ValueError("Use the fixed persistent state root: " + str(STATE_ROOT))
    root = plain_path(args.image_root)
    manifest, manifest_sha = inspect_image(root)
    if args.command in (None, "inspect"):
        print(json.dumps({"status": "image_files_verified", "files_verified": len(manifest["files"]),
                          "image_manifest_sha256": manifest_sha, "default_version": "A",
                          "supported_versions": list(VERSIONS), "gpu": manifest["gpu"], "model_calls": 0,
                          "automatic_inference": False, "gpu_inference_verified": False}))
        return 0
    environment = check_environment(manifest)
    if args.command == "check-environment":
        print(json.dumps(environment))
        return int(bool(environment["mismatches"]))
    if environment["mismatches"]:
        raise ValueError("Image environment differs from the comparison; no job launched")
    if sys.platform != "linux":
        raise RuntimeError("Actual inference is restricted to the approved Linux GPU Pod")
    plan, plan_sha = load_plan(args.plan, manifest_sha, manifest["gpu"])
    if plan["state_root"] != str(args.state_root):
        raise ValueError("Plan and CLI state roots differ")
    state_root = plain_path(STATE_ROOT)
    state_root.mkdir(mode=0o700, exist_ok=True)
    helpers = load_helpers(root)
    def interrupted(signum, frame):
        raise KeyboardInterrupt("Received signal " + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    with helpers.trial_lock(state_root) as lock_fd:
        state = execute(plan, {"plan_sha256": plan_sha, "image_manifest_sha256": manifest_sha},
                        root, state_root, args.version, helpers, lock_fd)
    print(json.dumps({"status": state["status"], "plan_id": plan["plan_id"],
                      "attempts": sum(j["attempts"] for j in state["jobs"]),
                      "results": str(state_root / plan["plan_id"]), "gemini_calls": 0,
                      "embedding_calls": 0}))
    return int(state["status"] not in {"completed_plan", "ready_for_other_version"})


if __name__ == "__main__":
    raise SystemExit(main())
