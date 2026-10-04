"""Audit <=3 reviewed clips; configured-size/reuse modes have a 60-second cap."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from boram3_cctv import preprocessing as pp
from boram3_cctv.preprocessing_link import configured_size_plan, require, verify_link, verify_saved_preview_link

MAX_SECONDS = 180


def execution_limits(args):
    if getattr(args, "reuse_reviewed_frames", False):
        return 60, None
    if getattr(args, "configured_size", False):
        return 60, 15
    return MAX_SECONDS, 45


def protected_files(args):
    plan = pp.read_json(args.plan)
    pp.validate_plan(plan)
    paths = [args.plan, args.config]
    if getattr(args, "reuse_reviewed_frames", False):
        require(args.quality_review is not None, "quality_followup_required")
        paths += [args.quality_review]
    else:
        paths += [args.data_root / "metadata-real.csv"]
        paths += [pp.source_path(args.data_root, s["video_path"]) for s in plan["samples"]]
    paths += [p for p in args.reference.rglob("*") if p.is_file()]
    for path in paths + [args.data_root, args.reference]:
        require(not args.output.resolve().is_relative_to(path.resolve()) and
                not path.resolve().is_relative_to(args.output.resolve()), "output_overlaps_input")
    # Do not put personal absolute paths into reports.
    require(all(p.resolve().is_relative_to(ROOT) for p in paths + [args.output]), "use_project_local_paths")
    return {p.resolve().relative_to(ROOT).as_posix(): pp.sha256(p) for p in paths}


def worker(args):
    from scripts.preprocess_samples import run
    from boram3_cctv.training_data import validate_config
    started = time.perf_counter()
    config = pp.read_json(args.config)
    validate_config(config)  # Only use the existing input transform settings, no training preflight.
    reuse = getattr(args, "reuse_reviewed_frames", False)
    resize = getattr(args, "configured_size", False)
    reviewed_plan = pp.read_json(args.plan) if resize else None
    plan_path = args.plan
    if resize:
        derived = configured_size_plan(reviewed_plan, args.reference, config["preprocessing"])
        plan_path = args.output / "configured_plan.json"
        pp.write_json(plan_path, derived)
    if not reuse:
        prepared = args.output / "prepared"
        manifest = run(args.data_root, plan_path, prepared, timeout=execution_limits(args)[1])
    preprocessing_seconds = 0.0 if reuse else time.perf_counter() - started
    began = time.perf_counter()
    if reuse:
        verification = verify_saved_preview_link(args.plan, args.reference, config["preprocessing"],
                                                 args.output / "diagnostic_arrays", args.quality_review)
    else:
        verification = verify_link(pp.read_json(plan_path), prepared, args.reference, config["preprocessing"],
                                   reviewed_plan=reviewed_plan)
    if resize:
        verification.update(reviewed_plan_sha256=pp.sha256(args.plan), derived_plan_sha256=pp.sha256(plan_path),
                            reviewed_image_size=reviewed_plan["image_size"],
                            configured_image_size=config["preprocessing"]["image_size"],
                            purpose="configured_size_input_link_only")
    verification.update(preprocessing_counts=None if reuse else manifest["counts"],
                        preprocessing_seconds=preprocessing_seconds,
                        verification_seconds=time.perf_counter() - began,
                        memory={"worker_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                                "decoder_children_max_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                                "method": "getrusage(RUSAGE_SELF).ru_maxrss",
                                "scope": "link worker including CPU torch import/transform; decoder children excluded",
                                "children_method": "getrusage(RUSAGE_CHILDREN).ru_maxrss; largest child RSS, not a sum or simultaneous total",
                                "gpu_peak": None})
    pp.write_json(args.output / "verification.json", verification)
    return 0 if verification["status"] == "passed" else 2


def bounded_worker(command, stdout, stderr, timeout=MAX_SECONDS):
    process = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
    try:
        return process.wait(timeout=timeout), False
    except subprocess.TimeoutExpired:
        # Include any decoder grandchildren: no detached long-running work.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return process.returncode, True


def run_check(args):
    require(not args.output.exists(), "output_already_exists_choose_new_directory")
    before = protected_files(args)
    args.output.mkdir(parents=True, exist_ok=False)
    pp.write_json(args.output / "inputs_before.json", before)
    started = time.perf_counter()
    command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
    hard_limit, per_video_limit = execution_limits(args)
    with (args.output / "worker.stdout.log").open("w") as stdout, (args.output / "worker.stderr.log").open("w") as stderr:
        returncode, timed_out = bounded_worker(command, stdout, stderr,
                                              timeout=hard_limit)
    elapsed = time.perf_counter() - started
    changed = [name for name, digest in before.items() if not (ROOT / name).is_file() or pp.sha256(ROOT / name) != digest]
    output_bytes = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
    report = {"status": "passed" if returncode == 0 and not changed else "needs_attention",
              "created_utc": datetime.now(timezone.utc).isoformat(), "worker_returncode": returncode,
              "timed_out": timed_out, "hard_limit_seconds": hard_limit,
              "per_video_limit_seconds": per_video_limit,
              "elapsed_worker_wall_seconds": elapsed, "protected_inputs_unchanged": not changed,
              "changed_inputs": changed, "output_bytes_before_run_report": output_bytes,
              "training_run": False, "model_created": False, "performance_evaluation": False,
              "device": "CPU decode and tensor transform only; no model or MPS operation",
              "environment": {"system": platform.system(), "machine": platform.machine(),
                              "macos": platform.mac_ver()[0], "python": platform.python_version(),
                              "packages": {n: importlib.metadata.version(n) for n in ("av", "numpy", "Pillow", "torch", "torchvision")}},
              "code_sha256": {p.relative_to(ROOT).as_posix(): pp.sha256(p) for p in
                              [Path(__file__).resolve(), ROOT / "scripts/preprocess_samples.py",
                               ROOT / "src/boram3_cctv/preprocessing.py", ROOT / "src/boram3_cctv/preprocessing_link.py",
                               ROOT / "src/boram3_cctv/training.py"]}}
    pp.write_json(args.output / "run.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data-root", "plan", "reference", "config", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--reuse-reviewed-frames", action="store_true", help="reuse reviewed PNGs at their original size; do not decode video")
    modes.add_argument("--configured-size", action="store_true", help="decode the same reviewed times at the configured smaller size; cap 60 seconds")
    parser.add_argument("--quality-review", type=Path, help="human quality follow-up bound to the reviewed plan")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        return worker(args) if args.worker else run_check(args)
    except Exception as exc:
        print(json.dumps({"status": "needs_attention", "reason": str(exc) if isinstance(exc, pp.PreprocessingError)
                          else type(exc).__name__, "training_run": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
