"""Bounded R3D-18 arithmetic check. No real data, weights download or prediction.

Explicit device only; errors are recorded, never converted into CPU success.
The default one step includes all-parameter backward and SGD update.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_ENV = ("PYTORCH_ENABLE_MPS_FALLBACK", "PYTORCH_MPS_HIGH_WATERMARK_RATIO",
                 "PYTORCH_MPS_LOW_WATERMARK_RATIO")


def validate(config, steps, environ):
    required = {
        "purpose": "synthetic_operation_check_only", "model": "r3d_18",
        "weights": "random_initialization", "train_scope": "all_parameters",
        "batch_size": 1, "frames": 16, "height": 112, "width": 112,
        "dtype": "float32", "num_workers": 0, "num_classes": 2,
        "optimizer": "SGD_no_momentum",
        "normalization": "synthetic_uniform_0_1_no_dataset_normalization",
    }
    for key, value in required.items():
        if config.get(key) != value or type(config.get(key)) is not type(value):
            raise ValueError("unsupported_check_setting:" + key)
    if type(steps) is not int or not 1 <= steps <= 3:
        raise ValueError("steps_must_be_1_to_3")
    if type(config.get("seed")) is not int or not 0 <= config["seed"] < 2**32:
        raise ValueError("invalid_seed")
    if type(config.get("cpu_threads")) is not int or not 1 <= config["cpu_threads"] <= 4:
        raise ValueError("cpu_threads_must_be_1_to_4")
    rate = config.get("learning_rate")
    if type(rate) not in (int, float) or not math.isfinite(rate) or not 0 < rate <= 0.01:
        raise ValueError("invalid_learning_rate")
    if environ.get(PROTECTED_ENV[0]) not in (None, "0"):
        raise ValueError("implicit_cpu_fallback_forbidden")
    if any(environ.get(key) is not None for key in PROTECTED_ENV[1:]):
        raise ValueError("use_default_memory_protection_remove_watermark_overrides")


def environment(torch, torchvision):
    hardware = {}
    if platform.system() == "Darwin":
        for key in ("machdep.cpu.brand_string", "hw.memsize"):
            try:
                proc = subprocess.run(["sysctl", "-n", key], capture_output=True,
                                      text=True, timeout=3)
                hardware[key] = proc.stdout.strip() if proc.returncode == 0 else "access_denied"
            except (OSError, subprocess.TimeoutExpired):
                hardware[key] = "unverified"
    return {
        "system": platform.system(), "architecture": platform.machine(),
        "macos": platform.mac_ver()[0] or None, "python": platform.python_version(),
        "virtual_environment": sys.prefix != sys.base_prefix,
        "torch": torch.__version__, "torchvision": torchvision.__version__,
        "cpu_scalar_check": (torch.tensor([2.0], device="cpu") + 1).item() == 3.0,
        "mps_built": torch.backends.mps.is_built(),
        "mps_available": torch.backends.mps.is_available(),
        "cuda_build": torch.version.cuda, "hardware": hardware,
        "memory_protection": "library_defaults_no_override",
        "selected_environment": {key: os.environ.get(key) for key in PROTECTED_ENV},
    }


def memory_snapshot(torch, device, stage):
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result = {"stage": stage, "process_peak_rss_bytes": int(rss if platform.system() == "Darwin" else rss * 1024)}
    if device == "mps":
        for name in ("current_allocated_memory", "driver_allocated_memory", "recommended_max_memory"):
            try:
                result[name + "_bytes"] = int(getattr(torch.mps, name)())
            except (AttributeError, RuntimeError):
                result[name + "_bytes"] = None
    return result


def sync(torch, device):
    if device == "mps":
        torch.mps.synchronize()


def arithmetic_step(torch, model, optimizer, inputs, targets, device, report):
    """Check all trainable gradients and updates at both ends of the network."""
    params = dict(model.named_parameters())
    watched = ("stem.0.weight", "fc.weight")
    before = {key: params[key].detach().clone() for key in watched}
    record = {"step": len(report["steps"]) + 1, "completed": False}
    report["steps"].append(record)
    sync(torch, device)
    started = time.perf_counter()
    optimizer.zero_grad(set_to_none=True)
    for stage in ("forward", "loss", "backward", "gradient_check", "optimizer_step", "update_check"):
        report["stage"] = stage
        begin = time.perf_counter()
        if stage == "forward":
            logits = model(inputs)
            if logits.shape != (1, 2) or not torch.isfinite(logits).all().item():
                raise RuntimeError("invalid_logits")
            record["actual_input_device"] = str(inputs.device)
            record["actual_output_device"] = str(logits.device)
            record["actual_parameter_devices"] = sorted({str(p.device) for p in params.values()})
        elif stage == "loss":
            loss = torch.nn.functional.cross_entropy(logits, targets)
            if not torch.isfinite(loss).item():
                raise RuntimeError("nonfinite_loss")
            record["synthetic_loss_not_performance"] = loss.item()
        elif stage == "backward":
            loss.backward()
        elif stage == "gradient_check":
            trainable = [p for p in params.values() if p.requires_grad]
            if any(p.grad is None or not torch.isfinite(p.grad).all().item() for p in trainable):
                raise RuntimeError("missing_or_nonfinite_gradient")
            maximum = max(p.grad.detach().abs().max().item() for p in trainable)
            if maximum == 0:
                raise RuntimeError("all_gradients_zero")
            record["finite_gradient_tensor_count"] = len(trainable)
            record["gradient_abs_max"] = maximum
        elif stage == "optimizer_step":
            optimizer.step()
            record["optimizer_step_executed"] = True
        else:
            deltas = {key: (params[key].detach() - before[key]).abs().max().item() for key in watched}
            if any(not math.isfinite(delta) or delta <= 0 for delta in deltas.values()):
                raise RuntimeError("watched_weight_not_updated")
            record["weight_change_abs_max"] = deltas
        sync(torch, device)
        record[stage + "_seconds"] = time.perf_counter() - begin
        report["memory_snapshots"].append(memory_snapshot(torch, device, stage))
    record["elapsed_seconds"] = time.perf_counter() - started
    record["completed"] = True


def run(config, device, steps, inspect_only=False):
    started = time.perf_counter()
    report = {
        "status": "failed", "purpose": "synthetic_operation_check_only",
        "requested_device": device, "actual_model_device": None, "config": config,
        "requested_steps": steps, "steps": [], "memory_snapshots": [],
        "real_accident_training": False, "performance_evaluation": False,
        "real_data_read": False, "pretrained_weights_downloaded": False,
        "weights_source": "random_initialization_weights_None", "checkpoint_saved": False,
        "implicit_cpu_fallback": False, "target_semantics": "test_class_0_not_an_accident_label",
    }
    try:
        report["stage"] = "configuration"
        validate(config, steps, os.environ)
        report["stage"] = "imports"
        import torch
        import torchvision
        report["stage"] = "environment"
        report["environment"] = environment(torch, torchvision)
        if not report["environment"]["virtual_environment"]:
            raise RuntimeError("native_virtual_environment_required")
        if inspect_only:
            report["status"] = "environment_only"
            return report
        if device == "mps":
            if platform.system() != "Darwin" or platform.machine() != "arm64":
                raise RuntimeError("mps_not_verified_on_this_host")
            if not report["environment"]["mps_available"]:
                raise RuntimeError("mps_unavailable_no_cpu_fallback")
        torch.set_num_threads(config["cpu_threads"])
        torch.manual_seed(config["seed"])
        report["stage"] = "synthetic_input"
        generator = torch.Generator(device="cpu").manual_seed(config["seed"])
        samples = torch.rand((1, 3, 16, 112, 112), generator=generator, dtype=torch.float32)
        labels = torch.zeros((1,), dtype=torch.long)
        report["input"] = {"shape": list(samples.shape), "layout": "NCTHW", "dtype": "float32",
                           "sha256": hashlib.sha256(samples.numpy().tobytes()).hexdigest(),
                           "source": "seeded_random_tensor_not_ACCIDENT", "target_values": [0]}
        loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(samples, labels),
                                            batch_size=1, shuffle=False, num_workers=0)
        report["stage"] = "model_initialization"
        model = torchvision.models.video.r3d_18(weights=None, num_classes=2).train()
        report["parameter_count"] = sum(p.numel() for p in model.parameters())
        report["trainable_parameter_count"] = sum(p.numel() for p in model.parameters() if p.requires_grad)
        report["parameter_storage_bytes_calculated"] = sum(p.numel() * p.element_size() for p in model.parameters())
        report["stage"] = "device_transfer"
        model = model.to(device=device, dtype=torch.float32)
        report["actual_model_device"] = str(next(model.parameters()).device)
        optimizer = torch.optim.SGD(model.parameters(), lr=config["learning_rate"], momentum=0)
        inputs, targets = next(iter(loader))
        inputs, targets = inputs.to(device), targets.to(device)
        sync(torch, device)
        report["memory_snapshots"].append(memory_snapshot(torch, device, "before_steps"))
        for _ in range(steps):
            arithmetic_step(torch, model, optimizer, inputs, targets, device, report)
        report["status"] = "passed"
        report["stage"] = "complete"
    except Exception as error:
        message = str(error).replace(str(ROOT), "<project>").replace(str(Path.home()), "<home>")
        report["error"] = {"type": type(error).__name__, "message": message}
    finally:
        report["elapsed_seconds_including_imports"] = time.perf_counter() - started
        report["completed_steps"] = sum(step["completed"] for step in report["steps"])
        report["optimizer_updates"] = sum(step.get("optimizer_step_executed", False) for step in report["steps"])
        report["memory_notes"] = "RSS is process high-water mark; MPS values are stage snapshots, not GPU peak or system-wide memory."
    return report


def output_path(value):
    path = Path(value).resolve()
    allowed = (ROOT / "artifacts" / "model-check").resolve()
    if not path.is_relative_to(allowed) or path.suffix != ".json":
        raise ValueError("output_must_be_new_json_under_artifacts_model-check")
    if path.exists():
        raise FileExistsError("output_exists_no_overwrite")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/local_model_check.json")
    parser.add_argument("--device", required=True, choices=("mps", "cpu"))
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        output = output_path(args.output)
        config_bytes = args.config.read_bytes()
        config = json.loads(config_bytes)
        validate(config, args.steps, os.environ)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        message = str(error).replace(str(ROOT), "<project>").replace(str(Path.home()), "<home>")
        print(json.dumps({"status": "refused_before_run", "error": type(error).__name__, "message": message}))
        return 2
    # Reserve output before importing torch; never overwrite previous runs.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        report = run(config, args.device, args.steps, args.inspect_only)
        report["code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report["config_sha256"] = hashlib.sha256(config_bytes).hexdigest()
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: report.get(key) for key in ("status", "requested_device", "actual_model_device",
                     "stage", "completed_steps", "optimizer_updates", "elapsed_seconds_including_imports", "error")},
                     ensure_ascii=False, indent=2))
    return 0 if report["status"] in ("passed", "environment_only") else 2


if __name__ == "__main__":
    raise SystemExit(main())
