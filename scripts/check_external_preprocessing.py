"""Bounded external-video input/label check. No training or downloads."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from boram3_cctv import preprocessing as pp
from boram3_cctv import external_preprocessing as ep
from boram3_cctv.preprocessing_link import require, contained
from boram3_cctv.review_preview import peak_rss_bytes
from boram3_cctv.training_data import validate_config, TrainingError

TOTAL_SECONDS = 90
WORKER_SECONDS = 40


def worker():
    try:
        r = json.load(sys.stdin)
        result = ep.prepare_one(Path(r["source"]), r["sample"], r["policy"], r["settings"],
                                r["prior"], Path(r["output"]))
        result["decoder_peak_rss_bytes"] = peak_rss_bytes()
    except pp.PreprocessingError as exc:
        result = {"status": "error", "reason": str(exc), "target": None}
    except Exception as exc:
        result = {"status": "error", "reason": "decode_or_write_failed", "error_type": type(exc).__name__, "target": None}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


def run(root, source, review_path, config_path, output):
    began = time.monotonic()
    require(not output.exists(), "output_already_exists")
    require(output.resolve().is_relative_to((root / "artifacts").resolve()), "output_must_be_under_artifacts")
    require(not output.resolve().is_relative_to(source.resolve().parent), "output_overlaps_source_directory")
    review, config = pp.read_json(review_path), pp.read_json(config_path)
    validate_config(config)
    parent_path = contained(root, review["parent_manifest"])
    parent = pp.read_json(parent_path)
    require(pp.sha256(parent_path) == review["parent_manifest_sha256"], "parent_manifest_hash_mismatch")
    parents = ep.validate_review(review, parent)
    # Only the explicitly supplied source and referenced existing previews are read.
    protected = {"review": review_path, "config": config_path, "parent_manifest": parent_path, "source": source}
    require(source.is_file(), "source_missing")
    require(source.name == review["source_filename"], "source_filename_mismatch")
    for sample in review["samples"]:
        preview = contained(root, sample["reviewed_preview"])
        expected = parent_path.parent / parents[sample["sample_id"]]["preview_path"]
        require(preview.resolve() == expected.resolve(), "reviewed_preview_path_mismatch")
        require(sample["reviewed_preview_sha256"] == parents[sample["sample_id"]]["preview_sha256"]
                == pp.sha256(preview), "reviewed_preview_hash_mismatch")
        protected[sample["sample_id"] + "_reviewed_preview"] = preview
    for path in protected.values():
        require(not path.resolve().is_relative_to(output.resolve())
                and not output.resolve().is_relative_to(path.resolve()), "output_overlaps_input")
    before = {k: pp.sha256(v) for k, v in protected.items()}
    require(before["source"] == review["source_sha256"], "source_hash_mismatch")
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / "review.json", review)
    pp.write_json(output / "preprocessing.json", config["preprocessing"])
    records = []
    for sample in review["samples"]:
        base = {"sample_id": sample["sample_id"], "target": None, "training_authorized": False}
        remaining = TOTAL_SECONDS - (time.monotonic() - began)
        if remaining <= 0:
            records.append({**base, "status": "error", "reason": "total_time_budget_exceeded"})
            continue
        with tempfile.TemporaryDirectory(prefix=".decode-", dir=output) as temporary:
            destination = Path(temporary) / "result"
            request = {"source": str(source.resolve()), "sample": sample, "policy": review["policy_version"],
                       "settings": config["preprocessing"], "prior": parents[sample["sample_id"]], "output": str(destination)}
            try:
                proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
                                      input=json.dumps(request), capture_output=True, text=True,
                                      timeout=min(WORKER_SECONDS, remaining))
                result = json.loads(proc.stdout)
                require(proc.returncode == 0 and result.get("status") in
                        ("error", "unconfirmed", "prepared_labeled_diagnostic"), "decode_worker_failed")
            except subprocess.TimeoutExpired:
                result = {"status": "error", "reason": "decode_timeout"}
            except (OSError, ValueError, pp.PreprocessingError):
                result = {"status": "error", "reason": "decode_worker_failed"}
            record = {**base, **result}
            if result["status"] != "error":
                folder = output / sample["sample_id"]
                destination.rename(folder)
                record.update(frames_metadata_path=f"{sample['sample_id']}/frames.json",
                              preview_path=f"{sample['sample_id']}/preview.png")
                if record["target"] is not None:
                    record.update(input_path=f"{sample['sample_id']}/frames.npy",
                                  array_sha256=pp.sha256(folder / "frames.npy"),
                                  frames_sha256=pp.sha256(folder / "frames.json"))
            records.append(record)
    verification = []
    try:
        verification = ep.verify_inputs(output, records, config["preprocessing"])
        verify_status = "passed" if all(r["status"] == "passed" for r in verification) else "needs_attention"
    except Exception as exc:
        verify_status = "failed"
        verification = [{"status": "failed", "reason": str(exc) if isinstance(exc, (pp.PreprocessingError, TrainingError))
                         else type(exc).__name__}]
    unchanged = all(path.is_file() and pp.sha256(path) == before[key] for key, path in protected.items())
    success = unchanged and verify_status == "passed"
    report = {"version": ep.VERSION, "status": "passed" if success else "needs_attention",
              "purpose": "external_reviewed_input_label_link_only", "samples": records,
              "verification": verification, "inputs_unchanged": unchanged, "input_sha256": before,
              "source_filename": source.name, "source_location": "explicit_user_path_not_logged",
              "class_order": pp.CLASS_ORDER, "preprocessing": config["preprocessing"],
              "elapsed_seconds": time.monotonic() - began, "parent_peak_rss_bytes": peak_rss_bytes(),
              "memory_scope": "parent process including CPU torch import/verification; decoder processes excluded",
              "environment": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version(),
                              "packages": {n: importlib.metadata.version(n) for n in ("av", "numpy", "Pillow", "torch")}},
              "device": "cpu", "training_run": False, "model_created": False, "performance_evaluation": False,
              "training_readiness": "held", "model_input_human_approval": False,
              "group_note": "Selected intervals share one local file; upstream relations/splits remain unverified.",
              "limits": {"total_seconds": TOTAL_SECONDS, "worker_seconds": WORKER_SECONDS, "max_samples": 3},
              "code_sha256": {p.relative_to(ROOT).as_posix(): pp.sha256(p) for p in
                              [Path(__file__).resolve(), Path(ep.__file__), Path(pp.__file__), ROOT / "src/boram3_cctv/training.py"]}}
    pp.write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/train_r3d18.local.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(ROOT, args.source, args.review, args.config, args.output)
        print(json.dumps({k: report[k] for k in ("status", "elapsed_seconds", "inputs_unchanged", "training_run")}, ensure_ascii=False))
        return 0 if report["status"] == "passed" else 2
    except Exception as exc:
        print(json.dumps({"status": "invalid_request", "reason": str(exc) if isinstance(exc, (pp.PreprocessingError, TrainingError))
                          else type(exc).__name__, "training_run": False}))
        return 2


def supervise(argv):
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--run", *argv],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, _ = process.communicate(timeout=TOTAL_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
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
