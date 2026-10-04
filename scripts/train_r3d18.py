"""Readiness first, explicit device, no downloads; bounded local/Colab CLI."""
import argparse
from collections import Counter
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from boram3_cctv import preprocessing as pp
from boram3_cctv import training_data as td

REVIEW_CHECK_SECONDS = 55  # Leave room within the requested 60-second command cap.


def review_input_path(root, value):
    """Explicit project-local records only; never follow a path outside the repo."""
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise td.TrainingError("review_reference_must_be_project_relative")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise td.TrainingError("review_reference_outside_project")
    return path


def inspect_reviewed_inputs(root, config_path, request_path):
    # Reuse the same binding checks that recorded the human responses. No torch,
    # model, raw video, or training preflight is invoked in this diagnostic mode.
    if __package__:
        from . import record_segment_reviews as reviews
    else:
        import record_segment_reviews as reviews
    from boram3_cctv import train_candidates as tc
    hashes = {}

    def load(path):
        path = path.resolve()
        if not path.is_relative_to(root.resolve()):
            raise td.TrainingError("review_reference_outside_project")
        digest = pp.sha256(path) if path.is_file() and path.stat().st_size <= reviews.MAX_JSON_BYTES else None
        value = reviews.bounded_json(path)
        if digest != pp.sha256(path):
            raise td.TrainingError("review_record_changed_during_read")
        hashes[str(path.relative_to(root.resolve()))] = digest
        return value

    config, request = load(config_path), load(request_path)
    td.validate_config(config)
    required = {"schema_version", "purpose", "batches"}
    optional = {"protocol_selection", "candidate_history", "received_inspection"}
    if (not required <= set(request) or not set(request) <= required | optional or request["schema_version"] != 1
            or request["purpose"] != "reviewed_input_readiness_only"
            or not isinstance(request["batches"], list) or not 1 <= len(request["batches"]) <= 2):
        raise td.TrainingError("invalid_readiness_request")
    data_root = review_input_path(root, config["data_root"])
    if review_input_path(root, config["metadata_path"]) != data_root / "metadata-real.csv":
        raise td.TrainingError("real_metadata_path_mismatch")
    metadata, metadata_hash = tc.read_candidates_metadata(data_root)
    hashes[str((data_root / "metadata-real.csv").relative_to(root.resolve()))] = metadata_hash
    batches, references, kinds, prepared_inputs = [], [], set(), {}
    for batch in request["batches"]:
        if (not isinstance(batch, dict) or set(batch) != {"kind", "plan", "link_report", "answers", "quality_report"}
                or batch["kind"] not in ("accident", "qmul") or batch["kind"] in kinds):
            raise td.TrainingError("invalid_or_duplicate_review_batch")
        kinds.add(batch["kind"])
        paths = {k: review_input_path(root, batch[k]) for k in ("plan", "link_report", "answers", "quality_report")}
        values = {k: load(p) for k, p in paths.items()}
        fresh = reviews.run_quality(paths["plan"], paths["link_report"], answers_path=paths["answers"],
                                    kind=batch["kind"], check_only=True)
        stored = values["quality_report"]
        expected_status = "recorded" if fresh["status"] == "ready_to_record" else fresh["status"]
        if (stored.get("status") != expected_status or stored.get("purpose") != "input_quality_only"
                or stored.get("labels_changed") is not False or stored.get("training_run") is not False
                or any(stored.get(k) != fresh[k] for k in ("samples", "counts", "parent_plan_sha256",
                                                           "parent_report_sha256", "answers_sha256"))):
            raise td.TrainingError("saved_quality_record_mismatch")
        batches.append({"kind": batch["kind"], "policy_version": values["plan"]["policy_version"], "quality": fresh})
        references.append(batch)
        if request.get("protocol_selection"):
            folder = paths["link_report"].parent
            if batch["kind"] == "accident":
                folder = folder / "prepared"
                manifest = load(folder / "manifest.json")
                checked = {s["sample_id"]:s for s in manifest["samples"]}
            else:
                checked = {s["sample_id"]:s for s in values["link_report"]["samples"]}
            for sample in values["plan"]["samples"]:
                sid = sample["sample_id"]
                item = checked[sid]
                files = {key:review_input_path(root, str((folder / item[field]).relative_to(root.resolve())))
                         for key,field in (("array_path","input_path"),("frames_path","frames_metadata_path"))}
                # The existing quality binder already checked pixels, times and
                # hashes; retain their exact model-input references for A.
                prepared_inputs[sid] = {key:str(path.relative_to(root.resolve())) for key,path in files.items()}
                for key,path in files.items():
                    digest=pp.sha256(path)
                    prepared_inputs[sid][key.replace("_path","_sha256")]=digest
                    hashes[str(path.relative_to(root.resolve()))]=digest
                if batch["kind"] == "accident":
                    source=pp.source_path(data_root,sample["video_path"])
                    if not source.is_file() or source.stat().st_size > 512*1024*1024:
                        raise td.TrainingError("selected_original_missing_or_oversized")
                    digest=pp.sha256(source)
                    if digest != sample["source_sha256"]:
                        raise td.TrainingError("selected_original_hash_changed")
                    hashes[str(source.relative_to(root.resolve()))]=digest
                    prepared_inputs[sid]["original_file_status"]="present_hash_verified"
                else:
                    prepared_inputs[sid]["original_file_status"]="external_source_not_reopened_by_this_mode"
    result = td.reviewed_input_readiness(config, batches, metadata, protocol_selection=request.get("protocol_selection"))
    if request.get("protocol_selection"):
        for row in result["samples"]:
            row["prepared_input"] = prepared_inputs[row["sample_id"]]
        smoke = result["A_short_training_check"]
        smoke["proposed_inputs"] = [{**prepared_inputs[r["sample_id"]],
            **{k:r[k] for k in ("sample_id","target","source_sha256","start_seconds","end_seconds")}}
            for r in result["samples"] if r["sample_id"] in smoke["proposed_smoke_sample_ids"]]
    if request.get("candidate_history"):
        if not request.get("received_inspection"):
            raise td.TrainingError("received_metadata_version_required")
        history=load(review_input_path(root,request["candidate_history"]))
        received=load(review_input_path(root,request["received_inspection"]))
        comparison=td.compare_candidate_history(history,metadata,metadata_hash,result["samples"],received["metadata"]["sha256"])
        for row in comparison["rows"]:
            row["current_local_file_exists"]=pp.source_path(data_root,row["path"]).is_file()
        result["candidate_history_comparison"]=comparison
    if any(pp.sha256(root / name) != digest for name, digest in hashes.items()):
        raise td.TrainingError("review_inputs_changed_during_check")
    result.update(input_sha256=hashes, inputs_unchanged=True, review_references=references,
                  metadata_rows=len(metadata),
                  unselected_metadata_video_checks={"status": "inspection_excluded", "count": len(metadata) -
                      sum(s["csv_row"] is not None for s in result["samples"])})
    return result


def review_worker():
    import resource
    started = time.monotonic()
    try:
        request = json.load(sys.stdin)
        result = inspect_reviewed_inputs(Path(request["root"]), Path(request["config"]), Path(request["request"]))
    except (td.TrainingError, pp.PreprocessingError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        result = {"status": "failed", "reason": str(error) if isinstance(error, (td.TrainingError, pp.PreprocessingError))
                  else "invalid_or_unreadable_review_input", "training_run": False}
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result.update(worker_wall_seconds=time.monotonic() - started,
                  worker_peak_rss_bytes=int(rss if sys.platform == "darwin" else rss * 1024))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


def run_review_readiness(config_path, request_path, output):
    output = output.resolve()
    if output == ROOT / "artifacts" or not output.is_relative_to((ROOT / "artifacts").resolve()):
        raise td.TrainingError("output_must_be_under_project_artifacts")
    if output.exists():
        raise td.TrainingError("output_exists_choose_new_directory")
    for path in (config_path, request_path):
        if not path.resolve().is_relative_to(ROOT.resolve()):
            raise td.TrainingError("review_reference_outside_project")
    started = time.monotonic()
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--review-worker"],
            input=json.dumps({"root": str(ROOT), "config": str(config_path.resolve()), "request": str(request_path.resolve())}),
            capture_output=True, text=True, timeout=REVIEW_CHECK_SECONDS)
        result = json.loads(process.stdout)
        if process.returncode or result.get("status") not in ("held", "failed"):
            raise td.TrainingError("invalid_review_worker_result")
    except subprocess.TimeoutExpired:
        result = {"status": "failed", "reason": "review_check_timeout", "training_run": False}
    elapsed = time.monotonic() - started
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / "review_readiness.json", result)
    code_root = Path(__file__).resolve().parents[1]
    pp.write_json(output / "run.json", {"status": result["status"], "wall_seconds": elapsed,
        "worker_timeout_seconds": REVIEW_CHECK_SECONDS,
        "environment": {"system": platform.system(), "architecture": platform.machine(),
                        "python": platform.python_version(), "device": "CPU_records_only"},
        "code_sha256": {str(p.relative_to(code_root)): pp.sha256(p) for p in (
            Path(__file__).resolve(), Path(td.__file__), Path(pp.__file__),
            code_root / "scripts/record_segment_reviews.py", code_root / "src/boram3_cctv/train_candidates.py")},
        "training_run": False, "downloads": False, "video_decode_run": False})
    return {key: result[key] for key in ("status", "inspection_status", "selected_segments", "approved_label_counts",
                                        "quality_counts", "chosen_evaluation_protocol", "A_short_training_check",
                                        "B_independent_evaluation", "issues", "reason", "training_run") if key in result}


def supervise(command, output, seconds, probe_seconds=None):
    """Hard wall deadline covers initialization, loop, saving and reload check."""
    started = time.monotonic()
    with (output / "worker.stdout.log").open("w") as stdout, (output / "worker.stderr.log").open("w") as stderr:
        with subprocess.Popen(command, stdout=stdout, stderr=stderr) as proc:
            while proc.poll() is None:
                elapsed = time.monotonic() - started
                probe_expired = (probe_seconds is not None and elapsed >= probe_seconds
                                 and not (output / "probe-completed.json").exists())
                if elapsed >= seconds or probe_expired:
                    proc.kill()
                    proc.wait()
                    status = "probe_time_limit" if probe_expired else "worker_time_limit"
                    break
                time.sleep(0.05)
            else:
                status = "worker_completed" if proc.returncode == 0 else "worker_failed"
    return {"status": status, "worker_wall_seconds": time.monotonic() - started,
            "checkpoint_may_exist": (output / "loop/checkpoint.pt").exists()}


def run_limited_real_worker(request, torch, torchvision, tr):
    """Bounded measured probe, then resume within the same epoch/time budget."""
    import resource
    from boram3_cctv.model_cost_check import frozen_fingerprints
    config, output, provenance = request["config"], Path(request["output"]), request["provenance"]
    started = time.perf_counter()
    result = {"status": "failed", "purpose": config["purpose"], "generalization_evaluation": False,
              "validation_run": False, "training_updates": 0, "timing": {}, "memory_samples": []}
    continuation = config.get("initialization") == "project_model_fresh_optimizer"
    result["initialization"] = "project_model_fresh_optimizer" if continuation else "official_pretrained_fresh_head"
    result["previous_project_model_used_as_initialization"] = continuation
    result["initial_model_sha256"] = request.get("initial_model_sha256")
    stage = "device"
    try:
        device = tr.select_device(config["device"])
        result["actual_device"] = str(device)
        stage = "load_official_weights"
        began = time.perf_counter()
        if continuation:
            stage = "load_previous_project_model"
            model = tr.build_saved_model(request["weights"], request["initial_model_sha256"],
                                        config["preprocessing"]).to(device)
        else:
            model = tr.build_pretrained_model(request["weights"], "head").to(device)
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
            result["device_name"] = torch.cuda.get_device_name(device)
        tr.sync(device)
        result["timing"]["model_load_seconds"] = time.perf_counter() - began
        frozen_before = frozen_fingerprints(model)
        head_before = {k: v.detach().cpu().clone() for k, v in model.fc.state_dict().items()}
        records = [{**r, "array_path": str(ROOT / r["array_path"]), "frames_path": str(ROOT / r["frames_path"])}
                   for r in request["prepared"]]
        dataset = tr.SegmentDataset(records, config["preprocessing"])

        def memory(label):
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            sample = {"stage": label, "rss_peak_bytes": int(rss if sys.platform == "darwin" else rss * 1024)}
            if device.type == "mps":
                sample.update(mps_tensor_bytes=torch.mps.current_allocated_memory(),
                              mps_driver_bytes=torch.mps.driver_allocated_memory())
            if device.type == "cuda":
                sample.update(cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(device),
                              cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(device))
            result["memory_samples"].append(sample)
            if (sample["rss_peak_bytes"] > config["max_process_rss_bytes"]
                    or sample.get("mps_driver_bytes", 0) > config["max_mps_driver_bytes"]
                    or sample.get("cuda_peak_reserved_bytes", 0) > config.get("max_cuda_bytes", 8*1024**3)):
                return "resource_limit"
            return None

        def observe(record):
            result["training_updates"] = record["step"]
            halt = memory("update_" + str(record["step"]))
            pp.write_json(output / f"progress-{record['step']:03d}.json", {"stage": stage, **record, "resource_stop": halt})
            return halt or ("excessive_step_delay" if record["step_seconds"] > config["max_step_seconds"] else None)

        stage = "warmup_forward"
        began = time.perf_counter()
        batch = dataset[0][0].unsqueeze(0).to(device)
        model.eval()
        with torch.no_grad():
            logits = model(batch)
        tr.sync(device)
        if not torch.isfinite(logits).all().item():
            raise td.TrainingError("nonfinite_warmup")
        result["timing"]["warmup_seconds"] = time.perf_counter() - began
        if memory("after_warmup"):
            raise td.TrainingError("resource_limit_before_training")
        del logits, batch
        stage = "probe_training"
        began = time.perf_counter()
        probe = tr.run_loop(model, dataset, config, provenance, output / "probe", device,
                            stop_after=config["probe_steps"], step_observer=observe)
        result["timing"]["probe_seconds"] = time.perf_counter() - began
        result["probe"] = probe
        if probe["steps_this_run"] != config["probe_steps"] or probe["stop_reason"] != "phase_step_limit":
            raise td.TrainingError("probe_did_not_pass:" + probe["stop_reason"])
        if time.perf_counter() - started > config["probe_max_seconds"]:
            raise td.TrainingError("probe_time_limit")
        step_records = [json.loads(line) for line in (output / "probe/train.jsonl").read_text().splitlines()]
        mean_step = sum(r["step_seconds"] + r["checkpoint_seconds"] for r in step_records) / len(step_records)
        result["estimate"] = {"measured_steps": len(step_records), "mean_step_with_checkpoint_seconds": mean_step,
                              "remaining_steps": config["max_steps"] - probe["progress"]["step"],
                              "remaining_loop_seconds_estimate": mean_step * (config["max_steps"] - probe["progress"]["step"]),
                              "includes_final_save_reload": False}
        pp.write_json(output / "probe-completed.json", {"status": "passed", "probe": probe,
                                                         "estimate": result["estimate"]})
        stage = "resume_training"
        began = time.perf_counter()
        loop = tr.run_loop(model, dataset, config, provenance, output / "loop", device,
                           resume=output / "probe/checkpoint.pt", step_observer=observe,
                           checkpoint_every=50 if config.get("execution_scope") in ("fixed_606_epoch_bounded_v1", "fixed_609_epoch_bounded_v1", "fixed_631_epoch_bounded_v1", "fixed_707_epoch_bounded_v1", "fixed_711_epoch_bounded_v1", "balanced_631_round_bounded_v1") else 1)
        result["timing"]["resumed_loop_seconds"] = time.perf_counter() - began
        result["loop"] = loop
        result["training_updates"] = loop["progress"]["step"]
        if loop["stop_reason"] in ("resource_limit", "excessive_step_delay"):
            raise td.TrainingError(loop["stop_reason"])
        stage = "frozen_state_verification"
        frozen_after = frozen_fingerprints(model)
        result["frozen_state"] = {"before": frozen_before, "after": frozen_after,
                                  "unchanged": frozen_before == frozen_after}
        result["head_max_abs_change"] = {k: float((v.detach().cpu() - head_before[k]).abs().max())
                                         for k, v in model.fc.state_dict().items()}
        if not result["frozen_state"]["unchanged"] or not any(result["head_max_abs_change"].values()):
            raise td.TrainingError("frozen_or_trainable_state_verification_failed")
        stage = "reload_inference"
        began = time.perf_counter()
        model.eval()
        batch = dataset[0][0].unsqueeze(0)
        with torch.no_grad():
            reference = model(batch.to(device)).detach().cpu()
        logits = tr.reload_inference(output / "loop/model.pt",
                                    lambda: torchvision.models.video.r3d_18(weights=None, num_classes=2), batch, device)
        torch.testing.assert_close(logits, reference, rtol=1e-4, atol=1e-5)
        result["timing"]["reload_seconds"] = time.perf_counter() - began
        result.update(status="completed_limited_real_training", reload_verified=True,
                      reload_output_shape=list(logits.shape), resume_verified=True,
                      completed_epochs=loop["progress"].get("completed_epochs", 0),
                      pretrained_weights="previous_project_model" if continuation else "KINETICS400_V1_local_verified_file", train_scope="head",
                      training_segments=len(dataset), class_counts={"accident": sum(r["target"] == 1 for r in records),
                                                                    "normal": sum(r["target"] == 0 for r in records)})
        result["label_origin_counts"] = dict(Counter(
            r.get("label_provenance", {}).get("kind", r.get("label_origin", "human_review")) for r in records))
        if memory("after_reload"):
            raise td.TrainingError("resource_limit_after_reload")
    except Exception as error:
        result.update(status="failed", failed_stage=stage, error_type=type(error).__name__,
                      error=str(error).replace(str(ROOT), "<project>").replace(str(Path.home()), "<home>"))
    result["timing"]["worker_total_seconds"] = time.perf_counter() - started
    result["versions"] = {}
    for name in ("torch", "torchvision", "av", "numpy", "Pillow"):
        try:
            result["versions"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result["versions"][name] = "not_installed; not_required_for_saved_arrays"
    result["python"] = platform.python_version()
    pp.write_json(output / "result.json", result)
    return 0 if result["status"] == "completed_limited_real_training" else 2


def worker(request_path):
    request = pp.read_json(request_path)
    output = Path(request["output"])
    stage = "imports"
    result = {"status": "failed", "generalization_evaluation": False}
    try:
        import torch
        import torchvision
        from boram3_cctv import training as tr
        config, provenance = request["config"], request["provenance"]
        torch.set_num_threads(config["cpu_threads"])
        torch.manual_seed(config["seed"])
        if config.get("purpose") in (td.LIMITED_REAL, td.SOURCE_REAL, td.EXPANDED_REAL):
            return run_limited_real_worker(request, torch, torchvision, tr)
        stage = "device"
        device = tr.select_device(config["device"])
        stage = "load_official_weights"
        model = tr.build_pretrained_model(request["weights"], config["train_scope"])
        dataset = tr.SegmentDataset(request["prepared"], config["preprocessing"])
        stage = "training_loop"
        result = tr.run_loop(model, dataset, config, provenance, output / "loop", device,
                             resume=request.get("resume"))
        stage = "reload_inference"
        model.eval()
        batch = dataset[0][0].unsqueeze(0)
        with torch.no_grad():
            reference = model(batch.to(device)).detach().cpu()
        logits = tr.reload_inference(output / "loop/model.pt",
                                      lambda: torchvision.models.video.r3d_18(weights=None, num_classes=2), batch, device)
        torch.testing.assert_close(logits, reference, rtol=1e-4, atol=1e-5)
        result.update(reload_inference_verified=True, reload_output_shape=list(logits.shape),
                      actual_device=str(next(model.parameters()).device), train_scope=config["train_scope"],
                      pretrained_weights="KINETICS400_V1_local_verified_file",
                      versions={n: importlib.metadata.version(n) for n in ("torch", "torchvision", "av", "numpy", "Pillow")},
                      python=platform.python_version())
    except Exception as error:
        result.update(status="failed", failed_stage=stage, error_type=type(error).__name__,
                      error=str(error).replace(str(ROOT), "<project>").replace(str(Path.home()), "<home>"))
    pp.write_json(output / "result.json", result)
    return 0 if result["status"] == "completed_limited_loop" else 2


def run(config_path, output, check_only=False, resume=None):
    command_started = time.monotonic()
    config = pp.read_json(config_path)
    td.validate_config(config)
    output = output.resolve()
    if not output.is_relative_to((ROOT / "artifacts").resolve()):
        raise td.TrainingError("output_must_be_under_project_artifacts")
    data_root = td.resolve_path(ROOT, config["data_root"])
    if output.is_relative_to(data_root) or data_root.is_relative_to(output):
        raise td.TrainingError("output_overlaps_source_data")
    if output.exists():
        raise td.TrainingError("output_exists_choose_new_directory")
    source_run = config.get("purpose") == td.SOURCE_REAL
    limited = config.get("purpose") in (td.LIMITED_REAL, td.SOURCE_REAL)
    if limited and resume:
        raise td.TrainingError("limited_real_resume_is_internal_with_shared_budget")
    preflight = td.source_real_preflight if source_run else td.limited_real_preflight if limited else td.preflight
    report = preflight(config, ROOT)
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / "preflight.json", report)
    pp.write_json(output / "config.json", config)
    summary = {k: report[k] for k in ("status", "issues", "selected_segments", "approved_segment_labels", "train_segments")}
    summary.update(training_run=False, weights_downloaded=False)
    if report["status"] != "ready" or check_only:
        summary["mode"] = "check_only" if check_only else "training_held"
        pp.write_json(output / "summary.json", summary)
        return summary
    weights = td.resolve_path(ROOT, config.get("pretrained_weights_path")) if config.get("pretrained_weights_path") else None
    if weights is None or not weights.is_file():
        summary.update(status="held", issues=["official_weights_missing_download_requires_approval"])
        pp.write_json(output / "summary.json", summary)
        return summary
    # Validate official URL hash before any decoding or model construction.
    weight_hash = pp.sha256(weights)
    if not weight_hash.startswith("b3b3357e"):
        raise td.TrainingError("official_weights_hash_mismatch")
    try:
        prepared = report["prepared"] if limited else td.prepare_train_segments(config, ROOT, report, output / "inputs")
    except (td.TrainingError, pp.PreprocessingError, OSError) as error:
        reason = str(error) if isinstance(error, (td.TrainingError, pp.PreprocessingError)) else type(error).__name__
        summary.update(status="failed", failed_stage="segment_preparation", issues=[reason])
        pp.write_json(output / "summary.json", summary)
        return summary
    repeated = preflight(config, ROOT)
    if repeated["status"] != "ready" or repeated["versions"] != report["versions"]:
        raise td.TrainingError("data_changed_during_preparation")
    versions = dict(report["versions"])
    versions.update(weights_sha256=weight_hash, weights="R3D_18_Weights.KINETICS400_V1",
                    prepared_inputs_sha256=td.fingerprint([{k:v for k,v in r.items() if k not in ("array_path", "frames_path")} for r in prepared]),
                    code_sha256={p.name: pp.sha256(p) for p in (Path(__file__), Path(td.__file__), Path(pp.__file__),
                                                               ROOT / "src/boram3_cctv/training.py")})
    pp.write_json(output / "versions.json", versions)
    pp.write_json(output / "class_mapping.json", td.CLASS_MAPPING)
    pp.write_json(output / "prepared_inputs.json", prepared)
    request = {"output": str(output), "config": config, "provenance": versions,
               "prepared": prepared, "weights": str(weights),
               "resume": str(resume.resolve()) if resume else None}
    pp.write_json(output / "worker-request.json", request)
    worker_budget = config["max_seconds"] - (time.monotonic()-command_started) if source_run else config["max_seconds"]
    if worker_budget <= 0:
        raise td.TrainingError("source_training_preparation_exceeded_time_budget")
    outcome = supervise([sys.executable, str(Path(__file__).resolve()), "--worker", str(output / "worker-request.json")],
                        output, worker_budget, probe_seconds=config["probe_max_seconds"] if limited else None)
    # This says the worker was launched, not that a step or training succeeded.
    summary.update(status=outcome["status"], training_run=True, **{k:v for k,v in outcome.items() if k != "status"})
    summary["command_seconds_before_summary_save"] = time.monotonic()-command_started
    if (output / "result.json").exists():
        summary["worker_result"] = pp.read_json(output / "result.json")
    pp.write_json(output / "summary.json", summary)
    return summary


def colab_continuation_preflight(config, root):
    """Pinned, already reviewed transfer; no loosening of other training gates."""
    root = Path(root).resolve()
    td.validate_config(config)
    fixed = {'purpose': td.SOURCE_REAL, 'initialization': 'project_model_fresh_optimizer',
             'device': 'cuda', 'batch_size': 1, 'train_scope': 'head', 'num_workers': 0,
             'max_epochs': 5, 'max_steps': 40, 'max_seconds': 300,
             'probe_steps': 3, 'probe_max_seconds': 60, 'learning_rate': .001, 'momentum': .9,
             'max_cuda_bytes': 8*1024**3,
             'prepared_inputs_sha256': 'b41b9bce9202654be6efe26910ac3f8c15dffa647e43f7d85366d0552e5222a7',
             'initial_model_sha256': '2b0c4818f5aa442ce6eff11a240f319e47ae966b52644c9a0c37c851089f5947'}
    if any(config.get(k) != v for k, v in fixed.items()):
        raise td.TrainingError('colab_approved_scope_changed')
    auth = config.get('authorization', {})
    if (auth.get('source') != 'user_message' or auth.get('raw_response') != '그렇게 해줄래?'
            or auth.get('training_authorized') is not True):
        raise td.TrainingError('colab_training_authorization_missing')
    paths = {}
    for key in ('prepared_inputs', 'initial_model', 'source_windows'):
        path = review_input_path(root, config[key])
        if not path.is_file():
            raise td.TrainingError('colab_missing_' + key)
        if key != 'source_windows' and pp.sha256(path) != config[key+'_sha256']:
            raise td.TrainingError('colab_changed_' + key)
        paths[key] = path
    prepared = pp.read_json(paths['prepared_inputs'])
    td.match_colab_source_windows(prepared, pp.read_json(paths['source_windows']))
    expected = ['sample_008', 'sample_009', 'qmul_001', 'qmul_002', 'qmul_003',
                'sample_010', 'sample_011', 'sample_012']
    if ([r['sample_id'] for r in prepared['records']] != expected
            or prepared['preprocessing'] != config['preprocessing']
            or prepared['class_counts'] != {'normal': 3, 'accident': 5}):
        raise td.TrainingError('colab_training_inputs_changed')
    records = []
    for saved in prepared['records']:
        row = dict(saved)
        for kind in ('array', 'frames'):
            path = review_input_path(root, row[kind+'_path'])
            if not path.is_file() or pp.sha256(path) != row[kind+'_sha256']:
                raise td.TrainingError('colab_prepared_file_changed')
        row['previous_management_group'] = row['management_group']
        if row['target'] == 1:
            row['management_group'] = 'accident_training_unresolved'
        row.update(training_execution_authorized=True, evaluation_authorized=False)
        records.append(row)
    return prepared, records, paths


def run_colab_continuation(config_path, output):
    began = time.monotonic()
    config = pp.read_json(config_path)
    output = output.resolve()
    if output.exists() or not output.is_relative_to((ROOT/'artifacts').resolve()):
        raise td.TrainingError('new_colab_output_under_artifacts_required')
    prepared, records, paths = colab_continuation_preflight(config, ROOT)
    output.mkdir(parents=True, exist_ok=False)
    provenance = {'purpose': td.SOURCE_REAL, 'initialization': config['initialization'],
                  'initial_model_sha256': config['initial_model_sha256'],
                  'prepared_inputs_sha256': config['prepared_inputs_sha256'],
                  'source_windows_sha256': pp.sha256(paths['source_windows']),
                  'class_mapping': td.CLASS_MAPPING,
                  'code_sha256': {str(p.relative_to(ROOT)): pp.sha256(p) for p in
                      (Path(__file__).resolve(), Path(td.__file__).resolve(),
                       ROOT/'src/boram3_cctv/training.py', ROOT/'src/boram3_cctv/preprocessing.py',
                       ROOT/'src/boram3_cctv/model_cost_check.py')}}
    for name, value in [('config.json', config), ('versions.json', provenance),
                        ('prepared_inputs.json', records), ('class_mapping.json', td.CLASS_MAPPING)]:
        pp.write_json(output/name, value)
    request = {'config':config, 'output':str(output), 'provenance':provenance,
               'prepared':records, 'weights':str(paths['initial_model']),
               'initial_model_sha256':config['initial_model_sha256']}
    pp.write_json(output/'worker-request.json', request)
    budget = 300 - (time.monotonic()-began)
    if budget <= 0:
        raise td.TrainingError('colab_preparation_exceeded_budget')
    outcome = supervise([sys.executable, str(Path(__file__).resolve()), '--worker', str(output/'worker-request.json')],
                        output, budget, probe_seconds=60)
    summary = {**outcome, 'training_worker_launched':True, 'validation_run':False,
               'command_seconds_before_summary_save':time.monotonic()-began}
    if (output/'result.json').exists():
        summary['worker_result'] = pp.read_json(output/'result.json')
    pp.write_json(output/'summary.json', summary)
    return summary




def summarize_expansion_logs(output, segment_count):
    """Online loss from actual updates, never a validation or fixed-model metric."""
    from collections import defaultdict
    epochs=defaultdict(list); seen=set()
    for name in ('probe/train.jsonl','loop/train.jsonl'):
        path=output/name
        if not path.exists(): continue
        for line in path.read_text().splitlines():
            row=json.loads(line)
            if row['step'] in seen: raise td.TrainingError('duplicate_training_log_step')
            seen.add(row['step']);epochs[(row['step']-1)//segment_count+1].append(row)
    return [{'epoch':epoch,'recorded_updates':len(rows),'epoch_complete':len(rows)==segment_count,
             'online_mean_loss':sum(r['loss'] for r in rows)/len(rows),
             'train_f1':None,'validation_f1':None,'validation_recall':None,'validation_pr_auc':None,
             'metric_note':'Online unweighted batch1 cross entropy. Fixed-model train metrics not computed; independent validation unavailable.'}
            for epoch,rows in sorted(epochs.items())]


def stage_expansion_inputs(sources, cache, output, max_seconds=300, clock=time.monotonic):
    """Resume verified local files; each remote copy gets the remaining deadline."""
    started=clock(); records=[]; reused=0; copied=0; reason=None
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    def report(status):
        value={'status':status,'reason':reason,'verified_segments':len(records),
               'expected_segments':len(sources),'reused_files':reused,'copied_files':copied,
               'input_cache_path':str(cache),'staging_seconds':clock()-started,
               'training_worker_launched':False}
        temporary=output/'staging-progress.partial.json'
        temporary.write_text(json.dumps(value,indent=2));temporary.replace(output/'staging-progress.json')
        return value
    report('staging')
    for source in sources:
        record=dict(source)
        sid=source['sample_id']
        if Path(sid).name!=sid or sid in ('.','..'):
            raise td.TrainingError('invalid_cache_sample_id')
        folder=cache/sid
        if folder.is_symlink(): raise td.TrainingError('cache_symlink_forbidden')
        folder.mkdir(exist_ok=True)
        try:
            for prefix,name in (('array','frames.npy'),('frames','frames.json')):
                if clock()-started>=max_seconds:
                    reason='input_staging_time_limit';break
                destination=folder/name; expected=source[prefix+'_sha256']
                if destination.is_symlink(): raise td.TrainingError('cache_symlink_forbidden')
                if destination.exists():
                    if pp.sha256(destination)!=expected: raise td.TrainingError('cached_input_hash_changed')
                    reused+=1
                else:
                    # Copy into a unique temporary file; timeout never leaves a false complete input.
                    import tempfile
                    with tempfile.NamedTemporaryFile(dir=folder,prefix='.copy-',delete=False) as f: temporary=Path(f.name)
                    try:
                        remaining=max_seconds-(clock()-started)
                        if remaining<=0: raise subprocess.TimeoutExpired('input_copy',max_seconds)
                        subprocess.run([sys.executable,'-c','import shutil,sys;shutil.copyfile(sys.argv[1],sys.argv[2])',
                                        source[prefix+'_path'],str(temporary)],capture_output=True,check=True,timeout=remaining)
                        if pp.sha256(temporary)!=expected: raise td.TrainingError('expanded_input_hash_changed')
                        temporary.rename(destination);copied+=1
                    finally:
                        temporary.unlink(missing_ok=True)
                record[prefix+'_path']=str(destination)
        except subprocess.TimeoutExpired:
            reason='input_staging_time_limit'
        except (OSError,subprocess.CalledProcessError,td.TrainingError) as error:
            reason=str(error) if isinstance(error,td.TrainingError) else 'input_copy_failed'
        if reason: break
        records.append(record)
        report('staging')
        print(f'input cache: {len(records)}/{len(sources)} verified, reused files={reused}, copied files={copied}',flush=True)
    value=report('ready' if len(records)==len(sources) else 'input_staging_incomplete')
    value['records']=records
    return value

def run_colab_expansion(config_path, output, check_only=False):
    """Explicit new purpose, leaving all earlier 8/33-input gates unchanged."""
    config = pp.read_json(config_path)
    proposal_path = Path(config['input_proposal'])
    if pp.sha256(proposal_path) != config['input_proposal_sha256']:
        raise td.TrainingError('expansion_proposal_hash_changed')
    proposal = pp.read_json(proposal_path)
    td.validate_expansion_execution(config, proposal, require_authorization=not check_only)
    if check_only:
        return {'status':'ready', 'scope':'config_and_proposal_only', 'training_worker_launched':False,
                'training_authorized':config.get('training_execution_authorized') is True,
                'remote_input_files_checked':False, 'weights_checked':False}
    output = output.resolve()
    if output.exists() or not output.is_relative_to((ROOT/'artifacts').resolve()):
        raise td.TrainingError('new_expansion_output_under_artifacts_required')
    weights = Path(config['pretrained_weights_path'])
    if not weights.is_file() or not pp.sha256(weights).startswith('b3b3357e'):
        raise td.TrainingError('official_weights_missing_or_changed_no_download')
    output.mkdir(parents=True, exist_ok=False)
    preparation_started = time.monotonic()
    cache = Path(config.get('input_cache_path') or output/'inputs')
    # Reuse only runtime-local caches. Never write into the source Drive directories.
    if not any(cache.resolve().is_relative_to(Path(base).resolve()) for base in ('/tmp','/content')) or cache.resolve().is_relative_to(Path('/content/drive').resolve()):
        raise td.TrainingError('runtime_local_input_cache_required')
    staging = stage_expansion_inputs(proposal['records'], cache, output)
    if staging['status'] != 'ready':
        summary={**staging,'training_worker_launched':False,'validation_run':False}
        pp.write_json(output/'summary.json',summary)
        return summary
    records = staging.pop('records')
    # A fixed seeded order avoids an accidental all-accident then all-normal curriculum.
    import random
    random.Random(config['seed']).shuffle(records)
    provenance={'purpose':td.EXPANDED_REAL,'input_proposal_sha256':config['input_proposal_sha256'],
                'class_mapping':td.CLASS_MAPPING,'initialization':'official_pretrained_fresh_head',
                'record_order':[r['sample_id'] for r in records],
                'code_sha256':{str(p.relative_to(ROOT)):pp.sha256(p) for p in
                    (Path(__file__),Path(td.__file__),ROOT/'src/boram3_cctv/training.py',
                     ROOT/'src/boram3_cctv/preprocessing.py',ROOT/'src/boram3_cctv/model_cost_check.py')}}
    for name,value in [('config.json',config),('versions.json',provenance),
                       ('prepared_inputs.json',records),('class_mapping.json',td.CLASS_MAPPING)]:
        pp.write_json(output/name,value)
    request={'config':config,'output':str(output),'prepared':records,'weights':str(weights),'provenance':provenance}
    pp.write_json(output/'worker-request.json',request)
    staging_seconds=time.monotonic()-preparation_started
    outcome=supervise([sys.executable,str(Path(__file__)),'--worker',str(output/'worker-request.json')],
                      output,config['max_seconds'],probe_seconds=config['probe_max_seconds'])
    summary={**outcome,'staging_seconds':staging_seconds,'training_worker_launched':True,
             'validation_run':False,'training_segments':len(records)}
    if (output/'result.json').exists():summary['worker_result']=pp.read_json(output/'result.json')
    epoch_rows = summarize_expansion_logs(output, len(records))
    pp.write_json(output/'epoch-results.json', epoch_rows)
    pp.write_json(output/'summary.json',summary)
    return summary

EPOCH_PARENT_SHA = 'b3dc759bde671037eedc04caa13f02b524cfde89e46b4a85e08ca3e3953d38c1'


EPOCH603_PARENT_SHA = '469662e9be86579c3a0059d887e966e7bba554cdf274ec9be259180afe0db233'

def continuation_scope(request):
    scope=request.get('continuation_scope','legacy382')
    if scope not in ('legacy382','fixed603'):raise td.TrainingError('unknown_continuation_scope')
    return (EPOCH603_PARENT_SHA,603) if scope=='fixed603' else (EPOCH_PARENT_SHA,382)


def continuation_config(state, records, proposal, proposal_hash):
    """Only the approved 1412 -> 1910 continuation; no new data or optimizer."""
    c = dict(state['config']); p = state['progress']; provenance = state['provenance']
    expanded = c.get('execution_scope') == 'fixed_603_v1'
    count,start,cursor,epochs = (603,1678,472,2) if expanded else (382,1412,266,3)
    if (state.get('class_mapping') != td.CLASS_MAPPING or p.get('step') != start
            or p.get('cursor') != cursor or p.get('completed_epochs') != epochs
            or c.get('train_scope') != 'head' or c.get('device') != 'cuda'
            or not 0 <= p.get('training_seconds', -1) <= 1800):
        raise td.TrainingError('unexpected_continuation_checkpoint')
    if (provenance.get('input_proposal_sha256') != proposal_hash
            or c.get('input_proposal_sha256') != proposal_hash):
        raise td.TrainingError('continuation_proposal_changed')
    td.validate_expansion_execution(c, proposal)
    by_id = {r['sample_id']: r for r in proposal['records']}
    def stable(r): return {k:v for k,v in r.items() if k not in ('array_path','frames_path')}
    if (len(records) != count or len(by_id) != count
            or [r['sample_id'] for r in records] != provenance.get('record_order')
            or len({r['sample_id'] for r in records}) != count
            or any(stable(r) != stable(by_id.get(r['sample_id'], {})) for r in records)):
        raise td.TrainingError('continuation_training_records_changed')
    # run_loop keeps cumulative time and steps, so grant exactly 900 NEW seconds.
    c.update(max_steps=count*5, max_epochs=5, max_seconds=p['training_seconds'] + 900)
    return c


def continuation_progress(row, started, now, count=382):
    start,total = (1678,1337) if count==603 else (1412,498)
    completed = row['step'] - start
    elapsed = max(0, now-started)
    return {'phase':'training', 'additional_updates':completed, 'additional_total':total,
            'percent':100*completed/total, 'global_step':row['step'],
            'completed_epochs':row['step']//count, 'epoch_position':row['step']%count,
            'loss':row['loss'], 'elapsed_seconds':elapsed,
            'eta_seconds_estimate':elapsed/completed*(total-completed) if completed else None,
            'note':'ETA is an estimate; loss is this training batch, not accuracy'}


def epoch_continuation_worker(request_path):
    import torch
    from torchvision.models.video import r3d_18
    from boram3_cctv import training as tr
    from boram3_cctv.model_cost_check import frozen_fingerprints
    import resource
    r=pp.read_json(request_path); out=Path(r['output']); began=time.monotonic()
    result={'status':'failed','training_run':False,'validation_run':False}
    parent_sha,count=continuation_scope(r)
    try:
        if pp.sha256(Path(r['checkpoint'])) != parent_sha:
            raise td.TrainingError('continuation_checkpoint_changed')
        state=torch.load(r['checkpoint'],map_location='cpu',weights_only=True)
        c=continuation_config(state,r['prepared'],pp.read_json(Path(r['proposal'])),pp.sha256(Path(r['proposal'])))
        device=tr.select_device('cuda');torch.set_num_threads(c['cpu_threads'])
        model=r3d_18(weights=None,num_classes=2)
        model.load_state_dict(state['model_state'],strict=True)
        before=frozen_fingerprints(model)
        head={k:v.clone() for k,v in model.fc.state_dict().items()}
        dataset=tr.SegmentDataset(r['prepared'],c['preprocessing'])
        torch.cuda.reset_peak_memory_stats(device)
        started=time.monotonic()
        def observe(row):
            progress=continuation_progress(row,started,time.monotonic(),count)
            progress.update(cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(device),
                            process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)
            temporary=out/'continuation-progress.tmp'
            temporary.write_text(json.dumps(progress));temporary.replace(out/'continuation-progress.json')
            print(json.dumps(progress),flush=True)
            if (progress['cuda_peak_reserved_bytes']>c['max_cuda_bytes'] or
                    progress['process_peak_rss_bytes']>c['max_process_rss_bytes']):return 'resource_limit'
            if row['step_seconds']>c['max_step_seconds']:return 'step_time_limit'
        result['training_run']=True
        loop=tr.run_loop(model,dataset,c,state['provenance'],out/'loop',device,
                         resume=Path(r['checkpoint']),step_observer=observe,checkpoint_every=50 if count==603 else 1)
        frozen_ok=before==frozen_fingerprints(model)
        if not frozen_ok:raise td.TrainingError('frozen_features_or_bn_changed')
        result.update(loop,features_and_bn_unchanged=frozen_ok,
                      head_changed=any(not torch.equal(head[k],v.cpu()) for k,v in model.fc.state_dict().items()),
                      actual_device=torch.cuda.get_device_name(device),parent_checkpoint_sha256=parent_sha)
        # State-dict equality checks saving/reloading without a new evaluation run.
        saved=torch.load(out/'loop/model.pt',map_location='cpu',weights_only=True)
        reloaded=r3d_18(weights=None,num_classes=2);reloaded.load_state_dict(saved['model_state'],strict=True)
        if any(not torch.equal(v.cpu(),reloaded.state_dict()[k]) for k,v in model.state_dict().items()):
            raise td.TrainingError('reloaded_model_mismatch')
        result.update(saved_state_reload_verified=True,reload_inference_run=False)
        pp.write_json(out/'config.json',c)
        pp.write_json(out/'versions.json',state['provenance'])
        pp.write_json(out/'class_mapping.json',td.CLASS_MAPPING)
    except Exception as e:
        result.update(status='failed',error_type=type(e).__name__,reason=str(e))
    finally:
        result['worker_wall_seconds']=time.monotonic()-began
        pp.write_json(out/'result.json',result)
    return 0 if result['status']=='completed_limited_loop' else 2


def run_epoch_continuation(request_path, output):
    """Stage only the original training inputs, then restore the bounded loop."""
    import torch
    request=pp.read_json(request_path)
    if request.get('training_execution_authorized') is not True:
        raise td.TrainingError('explicit_continuation_approval_required')
    output=output.resolve()
    if output.exists() or not output.is_relative_to((ROOT/'artifacts').resolve()):
        raise td.TrainingError('new_continuation_output_required')
    checkpoint=Path(request['checkpoint']); proposal_path=Path(request['proposal'])
    parent_sha,count=continuation_scope(request)
    if pp.sha256(checkpoint)!=parent_sha:raise td.TrainingError('continuation_checkpoint_changed')
    state=torch.load(checkpoint,map_location='cpu',weights_only=True)
    proposal=pp.read_json(proposal_path); records=pp.read_json(Path(request['prepared_inputs']))
    continuation_config(state,records,proposal,pp.sha256(proposal_path))
    # Saved runtime paths can be gone. Recover original approved Drive paths by ID.
    by_id={r['sample_id']:r for r in proposal['records']}
    sources=[dict(by_id[r['sample_id']]) for r in records]
    cache=Path(request['input_cache_path']).resolve()
    if (not any(cache.is_relative_to(Path(base)) for base in ('/tmp','/content'))
            or cache.is_relative_to(Path('/content/drive'))):raise td.TrainingError('runtime_cache_required')
    output.mkdir(parents=True)
    staged=stage_expansion_inputs(sources,cache,output,max_seconds=900 if count==603 else 300)
    if staged['status']!='ready':
        pp.write_json(output/'summary.json',staged);return staged
    records=staged.pop('records')
    pp.write_json(output/'prepared_inputs.json',records)
    child={**request,'prepared':records,'output':str(output)}
    pp.write_json(output/'continuation-request.json',child)
    # 900s loop budget plus bounded loading/final checkpoint serialization allowance.
    try:
        with (output/'training.log').open('w') as log:
            child_result=subprocess.run([sys.executable,str(Path(__file__)), '--epoch-worker',str(output/'continuation-request.json')],
                                        stdout=log,stderr=subprocess.STDOUT,timeout=960)
        outcome={'returncode':child_result.returncode,'timed_out':False}
    except subprocess.TimeoutExpired:
        outcome={'returncode':None,'timed_out':True}
    rows=summarize_expansion_logs(output,count)
    # Partial epoch 4 contains only new records; never label this a full epoch mean.
    pp.write_json(output/'epoch-results.json',rows)
    summary={**outcome,'status':'worker_completed' if outcome['returncode']==0 else 'worker_failed',
             'staging':staged,'parent_checkpoint_sha256':parent_sha,'validation_run':False,
             'result_available':(output/'result.json').exists(),'checkpoint_available':(output/'loop/checkpoint.pt').exists()}
    pp.write_json(output/'summary.json',summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/train_r3d18.local.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--colab-expansion", action="store_true", help="explicitly approved 382-input CUDA head run")
    parser.add_argument("--colab-continuation", action="store_true",
                        help="approved fixed eight-input CUDA warm-start; no new downloads")
    parser.add_argument("--review-readiness", type=Path,
                        help="bounded record-only audit of saved human reviews; never starts training")
    parser.add_argument("--epoch-continuation", type=Path)
    args = parser.parse_args()
    try:
        if args.epoch_continuation:
            if args.resume or args.colab_expansion or args.colab_continuation or args.check_only or args.review_readiness:
                raise td.TrainingError("continuation_mode_conflict")
            result = run_epoch_continuation(args.epoch_continuation, args.output)
        elif args.colab_expansion:
            if args.resume or args.review_readiness or args.colab_continuation:
                raise td.TrainingError("expansion_mode_conflict")
            result = run_colab_expansion(args.config, args.output, check_only=args.check_only)
        elif args.colab_continuation:
            if args.resume or args.review_readiness or args.check_only:
                raise td.TrainingError("colab_continuation_mode_conflict")
            result = run_colab_continuation(args.config, args.output)
        elif args.review_readiness:
            if args.resume or args.check_only:
                raise td.TrainingError("review_readiness_cannot_resume_or_use_training_check")
            result = run_review_readiness(args.config, args.review_readiness, args.output)
        else:
            result = run(args.config, args.output, args.check_only, args.resume)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("ready", "worker_completed") else 2
    except (td.TrainingError, pp.PreprocessingError, OSError, ValueError, KeyError, TypeError) as error:
        reason = str(error) if isinstance(error, (td.TrainingError, pp.PreprocessingError)) else type(error).__name__
        print(json.dumps({"status": "failed", "reason": reason, "weights_downloaded": False}))
        return 2


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--epoch-worker":
        raise SystemExit(epoch_continuation_worker(Path(sys.argv[2])))
    if sys.argv[1:] == ["--review-worker"]:
        review_worker()
        raise SystemExit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        raise SystemExit(worker(Path(sys.argv[2])))
    raise SystemExit(main())
