"""Bounded, offline CSV/file-name check; writes only to a new artifacts folder."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from boram3_cctv import preprocessing as pp
from boram3_cctv import train_candidates as tc

WORKER_SECONDS = 30


def worker():
    try:
        request = json.load(sys.stdin)
        result = tc.inspect(Path(request["data_root"]), request["limit"])
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["worker_peak_rss_bytes"] = int(rss if sys.platform == "darwin" else rss * 1024)
    except Exception as exc:
        result = {"status": "error", "reason": str(exc) if isinstance(exc, pp.PreprocessingError)
                  else "input_read_failed", "error_type": type(exc).__name__, "training_run": False}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


def run(root, data_root, output, limit):
    tc.require(not output.exists(), "output_already_exists")
    tc.require(output.resolve().is_relative_to((root / "artifacts").resolve()), "output_must_be_under_artifacts")
    tc.require(not output.resolve().is_relative_to(data_root.resolve())
               and not data_root.resolve().is_relative_to(output.resolve()), "output_overlaps_input")
    started = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
                              input=json.dumps({"data_root": str(data_root.resolve()), "limit": limit}),
                              text=True, capture_output=True, timeout=WORKER_SECONDS)
        tc.require(proc.returncode == 0, "inspection_worker_failed")
        report = json.loads(proc.stdout)
        tc.require(isinstance(report, dict) and report.get("status") in
                   ("candidate_list_prepared", "no_eligible_candidates", "error"), "invalid_worker_report")
    except subprocess.TimeoutExpired:
        report = {"status": "error", "reason": "inspection_timeout", "training_run": False}
    elapsed = time.monotonic() - started
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / "report.json", report)
    with (output / "candidate_paths.txt").open("x", encoding="utf-8") as stream:
        stream.writelines(c["path"] + "\n" for c in report.get("candidates", []))
    pp.write_json(output / "run.json", {
        "status": report["status"], "worker_wall_seconds": elapsed, "worker_timeout_seconds": WORKER_SECONDS,
        "environment": {"system": platform.system(), "architecture": platform.machine(),
                        "python": platform.python_version(), "device": "CPU_metadata_only"},
        "code_sha256": {str(path.relative_to(ROOT)): pp.sha256(path) for path in
                        (Path(__file__).resolve(), ROOT / "src/boram3_cctv/train_candidates.py",
                         ROOT / "src/boram3_cctv/preprocessing.py")},
        "video_decode_run": False, "training_run": False, "downloads": False,
    })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data/local/accident"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker()
        return 0
    if args.output is None:
        parser.error("--output is required; choose a new folder under artifacts/")
    try:
        report = run(ROOT, args.data_root, args.output, args.limit)
        print(json.dumps({key: report[key] for key in
                          ("status", "selected_count", "selected_missing_count", "reason") if key in report}))
        return 1 if report["status"] == "error" else 0
    except Exception as exc:
        print(json.dumps({"status": "error", "reason": str(exc) if isinstance(exc, pp.PreprocessingError)
                          else "inspection_or_output_failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
