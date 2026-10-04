"""Create 1–3 short, unlabelled review MP4s from one explicitly supplied file."""
import argparse
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from boram3_cctv import preprocessing as pp
from boram3_cctv import review_preview as rp

WORKER_TIMEOUT_SECONDS = 30
TOTAL_SECONDS = 90


def worker():
    try:
        request = json.load(sys.stdin)
        result = rp.create_preview(Path(request["source"]), request["segment"], Path(request["output"]))
    except (rp.PreviewError, pp.PreprocessingError) as exc:
        result = {"status": "error", "reason": str(exc), "target": None}
    except FileNotFoundError:
        result = {"status": "error", "reason": "file_missing", "target": None}
    except Exception as exc:
        result = {"status": "error", "reason": "media_or_encoding_failed",
                  "error_type": type(exc).__name__, "target": None}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


def run(source, plan_path, output):
    started = time.monotonic()
    plan = pp.read_json(plan_path)
    rp.validate_plan(plan)
    if output.exists():
        raise rp.PreviewError("output_already_exists")
    if (source.resolve().is_relative_to(output.resolve())
            or plan_path.resolve().is_relative_to(output.resolve())
            or output.resolve().is_relative_to(source.resolve().parent)):
        raise rp.PreviewError("output_overlaps_original_location")
    if not source.is_file():
        raise rp.PreviewError("file_missing")
    source_hash = pp.sha256(source)
    if source_hash != plan["source_sha256"] or source.name != plan.get("source_filename"):
        raise rp.PreviewError("source_identity_mismatch")
    plan_hash = pp.sha256(plan_path)
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / "plan.json", plan)
    records = []
    for segment in plan["segments"]:
        remaining = TOTAL_SECONDS - (time.monotonic() - started)
        base = {**segment, "target": None, "label": None, "training_authorized": False,
                "source_video_id": "sha256:" + source_hash,
                "source_group_id": "local-file-sha256:" + source_hash,
                "source_group_scope": "same_local_file_only_upstream_relations_unverified"}
        if remaining <= 0:
            records.append({**base, "status": "error", "reason": "total_time_budget_exceeded"})
            continue
        with tempfile.TemporaryDirectory(prefix=".worker-", dir=output) as temporary:
            destination = Path(temporary) / "result"
            request = {"source": str(source.resolve()), "segment": segment, "output": str(destination)}
            try:
                proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
                                      input=json.dumps(request), capture_output=True, text=True,
                                      timeout=min(WORKER_TIMEOUT_SECONDS, remaining))
                result = json.loads(proc.stdout)
                if proc.returncode or result.get("status") not in ("error", "preview_ready_unreviewed"):
                    raise ValueError
            except subprocess.TimeoutExpired:
                result = {"status": "error", "reason": "worker_timeout"}
            except (OSError, ValueError, TypeError, AttributeError):
                result = {"status": "error", "reason": "worker_failed"}
            record = {**base, **result}
            if result["status"] == "preview_ready_unreviewed":
                relative = Path(segment["segment_id"])
                destination.rename(output / relative)
                record.update(preview_path=(relative / "review.mp4").as_posix(),
                              sheet_path=(relative / "stills/preview.png").as_posix())
            records.append(record)
    unchanged = pp.sha256(source) == source_hash and pp.sha256(plan_path) == plan_hash
    if not unchanged:
        for record in records:
            record.update(status="error", reason="source_or_plan_changed", preview_path=None, sheet_path=None)
    success = unchanged and all(r["status"] == "preview_ready_unreviewed" for r in records)
    report = {"version": rp.VERSION, "status": "completed_unreviewed" if success else "needs_attention",
              "purpose": "human_review_only", "source_filename": source.name,
              "source_sha256": source_hash, "source_bytes": source.stat().st_size,
              "source_location": "explicit_user_path_not_logged", "plan_sha256": plan_hash,
              "code_sha256": {"review_preview.py": pp.sha256(Path(rp.__file__)),
                              "prepare_review_previews.py": pp.sha256(Path(__file__)),
                              "preprocessing.py": pp.sha256(Path(pp.__file__))},
              "environment": {"system": platform.system(), "machine": platform.machine(),
                              "python": platform.python_version()},
              "inputs_unchanged": unchanged, "segments": records,
              "elapsed_seconds": time.monotonic() - started,
              "limits": {"worker_seconds": WORKER_TIMEOUT_SECONDS, "total_seconds": TOTAL_SECONDS,
                         "max_segments": 3, "max_segment_seconds": 10,
                         "max_decoded_frames_per_segment": rp.MAX_DECODED,
                         "max_encoded_frames_per_segment": rp.MAX_ENCODED},
              "training_run": False, "model_run": False, "approved_targets": 0,
              "independent_source_files": 1, "independent_evaluation_ready": False}
    pp.write_json(output / "manifest.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path("configs/qmul_review_candidates.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.source, args.plan, args.output)
        print(json.dumps({k: report[k] for k in ("status", "elapsed_seconds", "inputs_unchanged",
                                                "approved_targets", "training_run")}, ensure_ascii=False))
        return 0 if report["status"] == "completed_unreviewed" else 2
    except (rp.PreviewError, pp.PreprocessingError) as exc:
        reason = str(exc)
    except (OSError, ValueError, KeyError, TypeError):
        reason = "invalid_or_unreadable_input"
    print(json.dumps({"status": "invalid_request", "reason": reason, "training_run": False}))
    return 2


def supervise(argv):
    """Hard wall-clock limit covers hashing, workers and final verification."""
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--run", *argv],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    try:
        stdout, _ = process.communicate(timeout=TOTAL_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        print(json.dumps({"status": "error", "reason": "supervisor_timeout", "training_run": False}))
        return 2
    print(stdout, end="")
    return process.returncode


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        worker()
    elif sys.argv[1:2] == ["--run"]:
        del sys.argv[1]
        raise SystemExit(main())
    else:
        raise SystemExit(supervise(sys.argv[1:]))
