"""Head-only arithmetic/cost diagnostics on explicit test input, never model quality."""
import hashlib
import math
import statistics
import time


def validate(config, environ):
    fixed = {
        "purpose": "synthetic_test_operation_cost_only", "model": "r3d_18",
        "weights": "random_initialization", "train_scope": "head",
        "batch_size": 1, "frames": 16, "height": 224, "width": 224,
        "dtype": "float32", "num_workers": 0, "num_classes": 2,
        "warmup_forward_calls": 1, "optimizer_updates": 1, "eval_calls": 3,
        "max_seconds": 180, "normalization": "existing_tensor_from_array_KINETICS400_V1",
    }
    for key, value in fixed.items():
        if type(config.get(key)) is not type(value) or config.get(key) != value:
            raise ValueError("unsupported_cost_check_setting:" + key)
    for key, lower, upper in (("seed", 0, 2**32 - 1), ("cpu_threads", 1, 4)):
        if type(config.get(key)) is not int or not lower <= config[key] <= upper:
            raise ValueError("invalid_" + key)
    for key, lower, upper in (("learning_rate", 0, 0.01), ("momentum", 0, 0.99)):
        value = config.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper or (key == "learning_rate" and value == 0):
            raise ValueError("invalid_" + key)
    if environ.get("PYTORCH_ENABLE_MPS_FALLBACK") not in (None, "0"):
        raise ValueError("implicit_cpu_fallback_forbidden")
    if any(environ.get(k) is not None for k in ("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "PYTORCH_MPS_LOW_WATERMARK_RATIO")):
        raise ValueError("memory_protection_override_forbidden")


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def timed(operation, synchronize, clock=time.perf_counter):
    """Exclude prior queued work and wait for this operation before stopping time."""
    synchronize()
    start = clock()
    result = operation()
    synchronize()
    return result, clock() - start


def frozen_fingerprints(model):
    """Hash all frozen state plus BN buffers; transfers are diagnostic overhead."""
    import torch
    frozen, bn = hashlib.sha256(), hashlib.sha256()
    bn_names = {name + "." + key for name, module in model.named_modules()
                if isinstance(module, torch.nn.modules.batchnorm._BatchNorm)
                for key, _ in module.named_buffers(recurse=False)}
    for name, tensor in model.state_dict().items():
        if name.startswith("fc."):
            continue
        header = (name + str(tensor.dtype) + str(tuple(tensor.shape))).encode()
        data = tensor.detach().cpu().contiguous().numpy().tobytes()
        frozen.update(header); frozen.update(data)
        if name in bn_names:
            bn.update(header); bn.update(data)
    return {"frozen_state_sha256": frozen.hexdigest(), "batchnorm_buffers_sha256": bn.hexdigest(),
            "batchnorm_buffer_count": len(bn_names)}


def measure_model(model, inputs, targets, config, device, report, progress, memory):
    """One warmup forward, one measured update, three eval forwards. No saved weights."""
    import torch
    from .training import set_training_scope, sync

    synchronize = lambda: sync(device)
    set_training_scope(model, "head")
    trainable = [p for p in model.parameters() if p.requires_grad]
    names = [n for n, p in model.named_parameters() if p.requires_grad]
    require(names == ["fc.weight", "fc.bias"], "unexpected_trainable_parameters")
    optimizer = torch.optim.SGD(trainable, lr=config["learning_rate"], momentum=config["momentum"])
    progress("state_snapshot_before")
    began = time.perf_counter()
    before = frozen_fingerprints(model)
    head_before = {name: p.detach().cpu().clone() for name, p in model.fc.named_parameters()}
    report["state_snapshot_before_seconds"] = time.perf_counter() - began
    report["trainable_parameter_names"] = names
    report["trainable_parameter_count"] = sum(p.numel() for p in trainable)
    report["parameter_count"] = sum(p.numel() for p in model.parameters())
    memory("before_warmup")

    progress("warmup_forward")
    model.eval()
    with torch.no_grad():
        logits, elapsed = timed(lambda: model(inputs), synchronize)
    require(logits.shape == (inputs.shape[0], 2) and torch.isfinite(logits).all().item(), "invalid_warmup_output")
    report["warmup"] = {"forward_calls": 1, "optimizer_updates": 0, "forward_seconds": elapsed,
                        "mode": "eval_no_grad_forward_only; backward_not_warmed"}
    memory("after_warmup")
    del logits

    progress("training_forward")
    set_training_scope(model, "head")
    optimizer.zero_grad(set_to_none=True)
    step_start = time.perf_counter()
    logits, forward = timed(lambda: model(inputs), synchronize)
    require(logits.shape == (inputs.shape[0], 2) and torch.isfinite(logits).all().item(), "invalid_training_output")
    require(inputs.device == logits.device == next(model.parameters()).device, "actual_device_mismatch")
    report["actual_output_device"] = str(logits.device)
    progress("training_loss")
    loss, loss_time = timed(lambda: torch.nn.functional.cross_entropy(logits, targets), synchronize)
    require(torch.isfinite(loss).item(), "nonfinite_test_loss")
    progress("training_backward")
    _, backward = timed(loss.backward, synchronize)
    require(all(p.grad is not None and torch.isfinite(p.grad).all().item() for p in trainable), "missing_or_nonfinite_head_gradient")
    require(any(p.grad.abs().max().item() > 0 for p in trainable), "zero_head_gradients")
    require(all(p.grad is None for p in model.parameters() if not p.requires_grad), "frozen_parameter_has_gradient")
    memory("after_backward_before_update")
    progress("optimizer_step")
    _, update = timed(optimizer.step, synchronize)
    report["optimizer_updates"] = 1
    report["training"] = {"forward_seconds": forward, "loss_seconds": loss_time,
                          "backward_seconds": backward, "optimizer_step_seconds": update,
                          "compute_seconds_sum": forward + loss_time + backward + update,
                          "first_step_wall_seconds_including_checks": time.perf_counter() - step_start,
                          "loss_is_finite": True, "head_gradient_tensors": len(trainable),
                          "frozen_gradients_absent": True}
    memory("after_update")
    del logits, loss

    model.eval()
    report["eval_forward_seconds"] = []
    with torch.no_grad():
        for index in range(config["eval_calls"]):
            progress("eval_forward_" + str(index + 1))
            logits, elapsed = timed(lambda: model(inputs), synchronize)
            require(logits.shape == (inputs.shape[0], 2) and torch.isfinite(logits).all().item(), "invalid_eval_output")
            report["eval_forward_seconds"].append(elapsed)
            memory("after_eval_" + str(index + 1))
            del logits
    values = report["eval_forward_seconds"]
    report["eval_summary"] = {"calls": len(values), "median_seconds": statistics.median(values),
                              "min_seconds": min(values), "max_seconds": max(values), "p95": None,
                              "scope": "synchronized forward only; resident input; validation/memory logging excluded"}
    progress("state_verification")
    began = time.perf_counter()
    after = frozen_fingerprints(model)
    deltas = {name: float((p.detach().cpu() - head_before[name]).abs().max())
              for name, p in model.fc.named_parameters()}
    require(before == after, "frozen_state_or_batchnorm_changed")
    require(all(math.isfinite(v) and v > 0 for v in deltas.values()), "head_weight_or_bias_not_updated")
    require(all(not m.training for m in model.modules() if isinstance(m, torch.nn.modules.batchnorm._BatchNorm)), "batchnorm_not_frozen")
    report["state_verification_seconds"] = time.perf_counter() - began
    report["state_checks"] = {"before": before, "after": after, "frozen_state_unchanged": True,
                              "batchnorm_buffers_unchanged": True, "fc_update_abs_max": deltas}
    memory("after_verification")
    report["forward_calls_total"] = 1 + 1 + len(values)
