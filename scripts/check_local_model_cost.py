"""Bounded C-1 head/224 cost check; explicit test input, no download or real footage."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from boram3_cctv import preprocessing as pp
from boram3_cctv.model_cost_check import measure_model, validate
from scripts.check_local_model import environment, memory_snapshot


def output_directory(value):
    output = Path(value).resolve()
    allowed = (ROOT / "artifacts/model-check").resolve()
    if not output.is_relative_to(allowed) or output == allowed:
        raise ValueError("output_must_be_new_directory_under_artifacts_model-check")
    if output.exists():
        raise FileExistsError("output_exists_no_overwrite")
    return output


def worker(config, device_name, output):
    report = {"status": "failed", "purpose": "synthetic_test_operation_cost_only", "config": config,
              "requested_device": device_name, "actual_model_device": None, "optimizer_updates": 0,
              "real_accident_training": False, "real_data_read": False, "performance_evaluation": False,
              "weights_source": "random_initialization_weights_None", "pretrained_weights_downloaded": False,
              "checkpoint_saved": False, "implicit_cpu_fallback": False, "memory_snapshots": []}
    start = time.perf_counter()

    def progress(stage):
        report["stage"] = stage
        # Only this run's own diagnostic file is replaced; never an existing run.
        temporary = output / "progress.tmp"
        temporary.write_text(json.dumps({"stage": stage,
            "optimizer_updates": report["optimizer_updates"], "eval_calls_completed": len(report.get("eval_forward_seconds", [])),
            "elapsed_seconds": time.perf_counter() - start}, indent=2) + "\n")
        temporary.replace(output / "progress.json")

    try:
        validate(config, os.environ)
        progress("imports")
        import numpy as np
        import torch
        import torchvision
        from boram3_cctv.training import select_device, set_training_scope, sync, tensor_from_array
        report["imports_seconds"] = time.perf_counter() - start
        progress("environment")
        report["environment"] = environment(torch, torchvision)
        report["environment"]["av"] = importlib.metadata.version("av")
        if not report["environment"]["virtual_environment"]:
            raise RuntimeError("native_virtual_environment_required")
        if device_name == "mps" and (platform.system() != "Darwin" or platform.machine() != "arm64"):
            raise RuntimeError("mps_not_verified_on_this_host")
        device = select_device(device_name)
        torch.set_num_threads(config["cpu_threads"])
        torch.manual_seed(config["seed"])
        progress("synthetic_input")
        began = time.perf_counter()
        settings = {"frame_count": config["frames"], "image_size": config["height"]}
        array = np.random.default_rng(config["seed"]).integers(0, 256, (16, 224, 224, 3), dtype=np.uint8)
        inputs = tensor_from_array(array, settings).unsqueeze(0)
        targets = torch.zeros(1, dtype=torch.long)
        loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(inputs, targets), batch_size=1, num_workers=0, shuffle=False)
        inputs, targets = next(iter(loader))
        report["input"] = {"shape": list(inputs.shape), "dtype": str(inputs.dtype), "layout": "NCTHW",
                           "sha256": hashlib.sha256(inputs.numpy().tobytes()).hexdigest(),
                           "source": "seeded_generated_uint8_pixels_not_ACCIDENT_or_synthetic_videos",
                           "target_values": [0], "target_semantics": "arbitrary_test_class_index_not_normal_ground_truth",
                           "transform": "existing_tensor_from_array; no video decoding or spatial resize"}
        report["synthetic_input_prepare_seconds"] = time.perf_counter() - began
        progress("model_initialization")
        began = time.perf_counter()
        model = torchvision.models.video.r3d_18(weights=None, num_classes=2)
        set_training_scope(model, "head")
        report["model_initialization_seconds"] = time.perf_counter() - began
        progress("device_transfer")
        began = time.perf_counter()
        model = model.to(device=device, dtype=torch.float32)
        inputs, targets = inputs.to(device), targets.to(device)
        sync(device)
        report["device_transfer_seconds"] = time.perf_counter() - began
        report["actual_model_device"] = str(next(model.parameters()).device)
        report["actual_input_device"] = str(inputs.device)
        report["actual_parameter_devices"] = sorted({str(p.device) for p in model.parameters()})

        def memory(stage):
            report["memory_snapshots"].append(memory_snapshot(torch, device.type, stage))

        measure_model(model, inputs, targets, config, device, report, progress, memory)
        progress("complete")
        report["status"] = "passed"
    except Exception as exc:
        message = str(exc).replace(str(ROOT), "<project>").replace(str(Path.home()), "<home>")
        report["error"] = {"type": type(exc).__name__, "message": message}
    finally:
        report["elapsed_seconds_including_imports_and_diagnostics"] = time.perf_counter() - start
        report["memory_notes"] = "Process RSS is a high-water mark including diagnostics; MPS values are stage snapshots, not a GPU peak. Do not add unified-memory counters."
        report["timing_notes"] = "One eval forward warmup, zero warmup updates. First backward is cold; one step is not steady-state training throughput. Three eval calls are too few for robust P95. No decode/real-video RTF measured."
        pp.write_json(output / "report.json", report)
    return report


def supervise(command, log, timeout):
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return process.wait(timeout=timeout), False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return process.returncode, True


def diagnostic_json(path):
    if not path.is_file():
        return {}
    try:
        result = pp.read_json(path)
        return result if isinstance(result, dict) else {"status": "failed", "error": "invalid_diagnostic_report"}
    except (OSError, ValueError):
        return {"status": "failed", "error": "incomplete_diagnostic_report"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/local_model_cost_check.json")
    parser.add_argument("--device", required=True, choices=("cpu", "mps"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        config = pp.read_json(args.config)
        validate(config, os.environ)
        if args.worker:
            return 0 if worker(config, args.device, args.output)["status"] == "passed" else 2
        output = output_directory(args.output)
        output.mkdir(parents=True, exist_ok=False)
        pp.write_json(output / "config.json", config)
        code_paths = [Path(__file__).resolve(), ROOT / "scripts/check_local_model.py",
                      ROOT / "src/boram3_cctv/model_cost_check.py", ROOT / "src/boram3_cctv/training.py"]
        hashes = {p.relative_to(ROOT).as_posix(): pp.sha256(p) for p in code_paths}
        started = time.perf_counter()
        command = [sys.executable, str(Path(__file__).resolve()), "--config", str(output / "config.json"),
                   "--device", args.device, "--output", str(output), "--worker"]
        with (output / "worker.log").open("x") as log:
            code, timed_out = supervise(command, log, config["max_seconds"])
        unchanged = all(pp.sha256(ROOT / p) == h for p, h in hashes.items())
        report = diagnostic_json(output / "report.json")
        run = {"status": "passed" if code == 0 and report.get("status") == "passed" and unchanged else "failed",
               "worker_returncode": code, "timed_out": timed_out, "hard_limit_seconds": config["max_seconds"],
               "worker_wall_seconds": time.perf_counter() - started, "code_sha256": hashes,
               "executed_code_unchanged": unchanged, "config_sha256": pp.sha256(output / "config.json"),
               "last_progress": diagnostic_json(output / "progress.json"),
               "actual_model_device": report.get("actual_model_device"),
               "error": report.get("error"), "real_accident_training": False, "performance_evaluation": False}
        pp.write_json(output / "run.json", run)
        print(json.dumps(run, ensure_ascii=False, indent=2))
        return 0 if run["status"] == "passed" else 2
    except Exception as exc:
        print(json.dumps({"status": "refused_or_failed", "error_type": type(exc).__name__,
                          "message": str(exc).replace(str(ROOT), "<project>").replace(str(Path.home()), "<home>")}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
