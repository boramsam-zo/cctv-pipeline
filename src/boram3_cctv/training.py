"""Small, resumable supervised loop; only the CLI's readiness gate starts it."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import time

import numpy as np
import torch

from . import preprocessing as pp
from .training_data import CLASS_MAPPING, LIMITED_REAL, SOURCE_REAL, EXPANDED_REAL, TrainingError, fingerprint

MEAN = [0.43216, 0.394666, 0.37645]
STD = [0.22803, 0.22145, 0.216989]


def tensor_from_array(array, settings):
    expected = (settings["frame_count"], settings["image_size"], settings["image_size"], 3)
    if array.dtype != np.uint8 or array.shape != expected:
        raise TrainingError("unexpected_preprocessed_array")
    # Entire letterboxed frame, same temporal order, no new crop or augmentation.
    tensor = torch.from_numpy(np.array(array, copy=True)).permute(3, 0, 1, 2).float().div_(255)
    mean = torch.tensor(MEAN).view(3, 1, 1, 1)
    std = torch.tensor(STD).view(3, 1, 1, 1)
    return ((tensor - mean) / std).contiguous()


class SegmentDataset(torch.utils.data.Dataset):
    def __init__(self, records, settings):
        self.records, self.settings = records, settings

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        record = self.records[index]
        if type(record["target"]) is not int or record["target"] not in (0, 1):
            raise TrainingError("unconfirmed_target")
        array_path, frames_path = Path(record["array_path"]), Path(record["frames_path"])
        if pp.sha256(array_path) != record["array_sha256"] or pp.sha256(frames_path) != record["frames_sha256"]:
            raise TrainingError("prepared_input_changed")
        details = pp.read_json(frames_path)
        times = [frame["actual_seconds"] for frame in details["frames"]]
        if record.get('label_origin') == 'user_segment_review':
            from .training_data import validate_reviewed_normal_details
            validate_reviewed_normal_details(record, details)
        elif record.get('label_origin') == 'assistant_observation_provisional_normal':
            from .training_data import validate_provisional_normal_details
            validate_provisional_normal_details(record, details)
        elif record.get('label_origin') == 'user_assumed_pre_accident_normal':
            from .training_data import validate_pre_accident_normal_details
            validate_pre_accident_normal_details(record, details)
        else:
            if (len(times) != self.settings["frame_count"] or
                    not all(record["start_seconds"] <= t < record["end_seconds"] for t in times) or
                    not all(b > a for a, b in zip(times, times[1:])) or details["target"] != record["target"]):
                raise TrainingError("frame_timing_or_target_mismatch")
        array = np.load(array_path, mmap_mode="r", allow_pickle=False)
        return tensor_from_array(array, self.settings), record["target"], record["sample_id"]


def select_device(name):
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") not in (None, "0"):
        raise TrainingError("implicit_cpu_fallback_forbidden")
    if any(os.environ.get(key) is not None for key in ("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "PYTORCH_MPS_LOW_WATERMARK_RATIO")):
        raise TrainingError("memory_protection_override_forbidden")
    if name == "mps" and not torch.backends.mps.is_available():
        raise TrainingError("mps_unavailable_no_cpu_fallback")
    if name == "cuda" and not torch.cuda.is_available():
        raise TrainingError("cuda_unavailable_no_cpu_fallback")
    return torch.device(name)


def set_training_scope(model, scope):
    if scope not in ("head", "all"):
        raise TrainingError("unsupported_train_scope")
    model.requires_grad_(scope == "all")
    if scope == "head":
        model.eval()  # Frozen BatchNorm buffers must not update during head training.
        model.fc.requires_grad_(True)
        model.fc.train()
    else:
        model.train()


def build_pretrained_model(weights_path, scope):
    from torchvision.models.video import R3D_18_Weights, r3d_18
    path = Path(weights_path)
    if not path.is_file():
        raise TrainingError("official_weights_missing_download_requires_approval")
    expected = R3D_18_Weights.KINETICS400_V1.url.rsplit("-", 1)[1].split(".")[0]
    if not pp.sha256(path).startswith(expected):
        raise TrainingError("official_weights_hash_mismatch")
    # Loading an explicitly supplied local file never invokes hub downloads.
    model = r3d_18(weights=None)
    model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True), strict=True)
    model.fc = torch.nn.Linear(model.fc.in_features, 2)
    set_training_scope(model, scope)
    return model


def sync(device):
    if device.type == "mps":
        torch.mps.synchronize()
    elif device.type == "cuda":
        torch.cuda.synchronize(device)


def build_saved_model(path, expected_sha256, settings, scope="head"):
    """Warm-start weights, including the learned head; use a new optimizer."""
    from torchvision.models.video import r3d_18
    path = Path(path)
    if not path.is_file() or pp.sha256(path) != expected_sha256:
        raise TrainingError("initial_project_model_missing_or_changed")
    saved = torch.load(path, map_location="cpu", weights_only=True)
    if (saved.get("model_name") != "r3d_18" or saved.get("class_mapping") != CLASS_MAPPING
            or saved.get("config", {}).get("preprocessing") != settings):
        raise TrainingError("initial_project_model_classes_or_preprocessing_mismatch")
    model = r3d_18(weights=None, num_classes=2)
    model.load_state_dict(saved["model_state"], strict=True)
    set_training_scope(model, scope)
    return model


def optimizer_for(model, config):
    return torch.optim.SGD([p for p in model.parameters() if p.requires_grad],
                           lr=config["learning_rate"], momentum=config["momentum"])


def cpu_copy(value):
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().clone()
    if isinstance(value, dict):
        return {k: cpu_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [cpu_copy(v) for v in value]
    if isinstance(value, tuple):
        return tuple(cpu_copy(v) for v in value)
    return copy.deepcopy(value)


def atomic_save(value, path):
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(value, temporary)
    os.replace(temporary, path)


def compatibility(config, provenance):
    # Runtime location/device may change for Colab; training semantics may not.
    keys = ("batch_size", "num_workers", "train_scope", "learning_rate", "momentum", "seed", "preprocessing")
    return fingerprint({"config": {k: config[k] for k in keys}, "provenance": provenance,
                        "class_mapping": CLASS_MAPPING})


def checkpoint(model, optimizer, config, provenance, progress, device):
    rng = {"cpu": torch.get_rng_state()}
    if device.type == "mps":
        rng["mps"] = torch.mps.get_rng_state()
    elif device.type == "cuda":
        rng["cuda"] = torch.cuda.get_rng_state(device)
    return {"format_version": 1, "model_name": "r3d_18", "class_mapping": CLASS_MAPPING,
            "model_state": cpu_copy(model.state_dict()), "optimizer_state": cpu_copy(optimizer.state_dict()),
            "config": config, "provenance": provenance, "progress": dict(progress),
            "rng": rng, "device_type": device.type, "compatibility": compatibility(config, provenance)}


def restore(path, model, optimizer, config, provenance, device):
    state = torch.load(path, map_location="cpu", weights_only=True)
    if state.get("format_version") != 1 or state.get("class_mapping") != CLASS_MAPPING or state.get("compatibility") != compatibility(config, provenance):
        raise TrainingError("checkpoint_data_config_or_code_mismatch")
    if state["progress"]["step"] > config["max_steps"]:
        raise TrainingError("checkpoint_exceeds_step_budget")
    model.load_state_dict(state["model_state"], strict=True)
    if optimizer is not None:
        optimizer.load_state_dict(state["optimizer_state"])
        for item in optimizer.state.values():
            for key, value in item.items():
                if isinstance(value, torch.Tensor):
                    item[key] = value.to(device)
        torch.set_rng_state(state["rng"]["cpu"])
        if device.type in state["rng"]:
            if device.type == "mps":
                torch.mps.set_rng_state(state["rng"]["mps"])
            elif device.type == "cuda":
                torch.cuda.set_rng_state(state["rng"]["cuda"], device)
        set_training_scope(model, config["train_scope"])
    return state



def source_balanced_order(records, config, round_index):
    """Deterministic 631 draws; each source pool cycles only after exhaustion.

    A sampling round is not a complete pass over all unique records.
    Private RNG makes probe/resume independent of worker RNG consumption.
    """
    import random
    if (config.get('sampling_policy') != 'source-balanced-631-v1'
            or config.get('execution_scope') != 'balanced_631_round_bounded_v1'
            or config.get('batch_size') != 1 or len(records) != 631):
        raise TrainingError('balanced_sampling_scope_changed')
    groups={'accident':[], 'accident_source_normal':[], 'detrac_normal':[]}
    ids=set()
    for i,r in enumerate(records):
        if r['sample_id'] in ids:raise TrainingError('duplicate_sampling_record')
        ids.add(r['sample_id'])
        source=r.get('source_path')
        if source and source.startswith('real_videos/'):
            if 'Z_GJQ1KrRJw' in source:raise TrainingError('excluded_source')
            key='accident' if r['target']==1 else 'accident_source_normal'
        elif r.get('sequence_id') and r['target']==0:key='detrac_normal'
        else:raise TrainingError('sampling_source_unknown')
        if r['target'] not in (0,1):raise TrainingError('sampling_label_unknown')
        groups[key].append(i)
    if {k:len(v) for k,v in groups.items()} != {'accident':403,'accident_source_normal':28,'detrac_normal':200}:
        raise TrainingError('balanced_pool_counts_changed')
    rng=random.Random(config['seed']+100003*round_index)
    result=[]
    for key,n in [('accident',315),('accident_source_normal',158),('detrac_normal',158)]:
        pool=sorted(groups[key],key=lambda i:records[i]['sample_id']);picked=[]
        while len(picked)<n:
            cycle=list(pool);rng.shuffle(cycle);picked.extend(cycle[:n-len(picked)])
        result.extend(picked)
    rng.shuffle(result)
    return result

def loop_time_expired(config, elapsed):
    if config.get('max_seconds') is None:
        expected_steps = {'fixed_606_epoch_bounded_v1': 3030, 'fixed_609_epoch_bounded_v1': 3045, 'fixed_631_epoch_bounded_v1': 3155, 'fixed_707_epoch_bounded_v1': 3535, 'fixed_711_epoch_bounded_v1': 3555, 'balanced_631_round_bounded_v1': 3155}.get(config.get('execution_scope'))
        if (expected_steps is None
                or config.get('training_execution_authorized') is not True
                or config.get('max_steps') != expected_steps or config.get('max_epochs') != 5):
            raise TrainingError('epoch_bounded_scope_required')
        return False
    return elapsed >= config['max_seconds']


def run_loop(model, dataset, config, provenance, output, device, resume=None, clock=time.monotonic, stop_after=None,
             step_observer=None, checkpoint_every=1):
    """Cooperative time cap plus CLI process timeout; no metrics on test data.

    stop_after bounds an internal measured probe or a unit-test interruption;
    resumed work shares the same total step/time budget.
    """
    if type(checkpoint_every) is not int or not 1 <= checkpoint_every <= 50:
        raise TrainingError("invalid_checkpoint_interval")
    if len(dataset) < config["batch_size"]:
        raise TrainingError("insufficient_training_segments")
    sampling_orders = None
    if config.get('sampling_policy') is not None:
        sampling_orders = [source_balanced_order(dataset.records, config, r) for r in range(5)]
    output.mkdir(parents=True, exist_ok=False)
    model.to(device)
    set_training_scope(model, config["train_scope"])
    optimizer = optimizer_for(model, config)
    progress = {"step": 0, "cursor": 0, "training_seconds": 0.0}
    if resume:
        progress = restore(resume, model, optimizer, config, provenance, device)["progress"]
    start, previous_seconds = clock(), progress["training_seconds"]
    executed = 0
    timing = {"input_seconds": 0.0, "compute_seconds": 0.0, "checkpoint_seconds": 0.0}
    reason = "step_limit"
    log = output / "train.jsonl"
    import json
    # Also leave a resumable state if the first batch is unreadable or times out.
    atomic_save(checkpoint(model, optimizer, config, provenance, progress, device), output / "checkpoint.pt")
    while progress["step"] < config["max_steps"]:
        if loop_time_expired(config, previous_seconds + clock() - start):
            reason = "time_limit"
            break
        if stop_after is not None and executed >= stop_after:
            reason = "phase_step_limit" if config.get("purpose") in (LIMITED_REAL, SOURCE_REAL, EXPANDED_REAL) else "unit_test_interruption"
            break
        set_training_scope(model, config["train_scope"])
        began = clock()
        input_started = time.perf_counter()
        indices = list(range(progress["cursor"], min(len(dataset), progress["cursor"] + config["batch_size"])))
        if sampling_orders is not None:
            indices = [sampling_orders[progress['step']//len(dataset)][i] for i in indices]
        loader = torch.utils.data.DataLoader(torch.utils.data.Subset(dataset, indices), batch_size=len(indices),
                                            num_workers=0, shuffle=False,
                                            generator=torch.Generator().manual_seed(config["seed"] + progress["step"]))
        inputs, targets, ids = next(iter(loader))  # Errors propagate, never skip or invent normal.
        if loop_time_expired(config, previous_seconds + clock() - start):
            reason = "time_limit_before_forward"
            break
        inputs, targets = inputs.to(device), targets.to(device)
        sync(device)
        input_seconds = time.perf_counter() - input_started
        compute_started = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        logits = model(inputs)
        loss = torch.nn.functional.cross_entropy(logits, targets)
        if not torch.isfinite(loss).item():
            raise TrainingError("nonfinite_loss")
        loss.backward()
        if any(p.grad is None or not torch.isfinite(p.grad).all().item() for p in model.parameters() if p.requires_grad):
            raise TrainingError("invalid_gradient")
        if loop_time_expired(config, previous_seconds + clock() - start):
            reason = "time_limit_before_update"
            break
        optimizer.step()
        sync(device)
        compute_seconds = time.perf_counter() - compute_started
        progress["step"] += 1
        progress["cursor"] = (progress["cursor"] + len(ids)) % len(dataset)
        progress["completed_epochs"] = progress["step"] // ((len(dataset) + config["batch_size"] - 1) // config["batch_size"])
        progress["training_seconds"] = previous_seconds + clock() - start
        record = {"step": progress["step"], "sample_ids": list(ids), "loss": loss.item(),
                  "step_seconds": clock() - began, "device": str(device), "generalization_metric": False,
                  "completed_epochs": progress["completed_epochs"], "input_seconds": input_seconds,
                  "compute_seconds": compute_seconds}
        if sampling_orders is not None:
            record.update(sampling_policy=config['sampling_policy'], completed_sampling_rounds=progress['completed_epochs'], full_dataset_epoch=False)
        save_started = time.perf_counter()
        if progress["step"] % checkpoint_every == 0 or progress["cursor"] == 0:
            atomic_save(checkpoint(model, optimizer, config, provenance, progress, device), output / "checkpoint.pt")
        record["checkpoint_seconds"] = time.perf_counter() - save_started
        for key in timing:
            timing[key] += record[key]
        halt = step_observer(record) if step_observer else None
        with log.open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        executed += 1
        if halt:
            reason = halt
            break
    progress["training_seconds"] = previous_seconds + clock() - start
    final = checkpoint(model, optimizer, config, provenance, progress, device)
    atomic_save(final, output / "checkpoint.pt")
    atomic_save({k: v for k, v in final.items() if k not in ("optimizer_state", "rng")}, output / "model.pt")
    return {"status": "completed_limited_loop", "stop_reason": reason, "steps_this_run": executed,
            "progress": progress, "timing": timing, "generalization_evaluation": False}


def reload_inference(checkpoint_path, model_factory, inputs, device):
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if state.get("class_mapping") != CLASS_MAPPING:
        raise TrainingError("invalid_checkpoint_classes")
    model = model_factory().to(device)
    model.load_state_dict(state["model_state"], strict=True)
    model.eval()
    with torch.no_grad():
        logits = model(inputs.to(device))
    if not torch.isfinite(logits).all().item() or logits.shape[-1] != 2:
        raise TrainingError("invalid_reload_output")
    return logits.detach().cpu()


def inspect_reviewed_normal_input(record, settings):
    """Read-only input diagnostic; never changes label/quality approval or creates a model."""
    from .training_data import validate_reviewed_normal_details
    before = fingerprint(record)
    paths = [Path(record[k+'_path']) for k in ('array','frames')]
    for key,path in zip(('array','frames'),paths):
        if not path.is_file() or pp.sha256(path)!=record[key+'_sha256']:
            raise TrainingError('prepared_input_missing_or_changed')
    details=pp.read_json(paths[1])
    validate_reviewed_normal_details(record,details,inspection_only=True)
    array=np.load(paths[0],mmap_mode='r',allow_pickle=False)
    tensor=tensor_from_array(array,settings)
    if not torch.isfinite(tensor).all():raise TrainingError('nonfinite_input')
    if fingerprint(record)!=before:raise TrainingError('inspection_changed_record')
    times=[f['actual_seconds'] for f in details['frames']]
    return dict(sample_id=record['sample_id'],status='input_verified_not_training',
                target=record['target'],shape=list(tensor.shape),dtype=str(tensor.dtype),
                sampling_policy=record['sampling_policy'],distinct_frames=len(set(times)),
                actual_seconds=times,input_quality_review=record['input_quality_review'],
                training_run=False,model_run=False)
