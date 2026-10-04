"""One CUDA job. The parent owns the durable attempt record and time limits.

Imports at module level are standard library only. Run this script once per
input/version in a fresh process; do not import both model pipelines together.
The source bundles stay unchanged, and raw outputs are retained under raw/.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import sys
import time
import types


GPU = "NVIDIA RTX A4500"


def valid_gpu_name(value):
    """An observed CUDA device name, distinct from a selector or contract marker."""
    return (isinstance(value, str) and 1 <= len(value) <= 160 and value == value.strip()
            and value.isascii() and value.isprintable()
            and value.casefold() not in {'auto', 'nvidia cuda gpu', 'gpu', 'cuda', 'unknown', 'none', 'null'}
            and re.search(r'\bcpu\b', value, re.IGNORECASE) is None)


def validate_gpu(environment, device_count, expected_gpu=GPU):
    """Match an explicit device contract without changing the observed name."""
    if not valid_gpu_name(expected_gpu):
        raise ValueError("Invalid expected GPU name")
    if (environment.get("gpu") != expected_gpu
            or type(device_count) is not int or device_count != 1):
        raise RuntimeError("This job requires its approved GPU x1")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new_json(path, document):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def block_network_event(event, args):
    """Audit all Python socket use before importing the model libraries.

    The verified pipelines use local torch.hub and local checkpoint paths.
    This is a download guard for those libraries, not an OS network namespace.
    """
    if event == "socket.__new__" and args[1] in (socket.AF_INET, socket.AF_INET6):
        raise PermissionError("Network disabled for offline model comparison")
    if event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
                 "urllib.Request"):
        raise PermissionError("Network disabled for offline model comparison")


def require_remote_process():
    if sys.platform != "linux":
        raise RuntimeError("Run model jobs on the approved Linux Pod; local inference disabled")
    parent = os.getppid()
    if parent <= 1 or ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise RuntimeError("Cannot establish parent-death handling")
    if os.getppid() != parent:
        raise RuntimeError("Parent exited during startup")


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve() != Path(path).resolve():
        raise RuntimeError("Source module origin mismatch")
    return module


def names_dict(names):
    rows = names.items() if isinstance(names, dict) else enumerate(names)
    result = {int(key): str(value) for key, value in rows}
    if not result or set(result) != set(range(len(result))):
        raise ValueError("Checkpoint class IDs must be contiguous from zero")
    return result


def validate_b_classes(checkpoint_names, sidecar_names, effective_names):
    raw, sidecar, effective = map(names_dict,
                                (checkpoint_names, sidecar_names, effective_names))
    expected = {0: "Car", 1: "Truck", 2: "Bus", 3: "Motorcycle"}
    if sidecar != expected or effective != sidecar or set(raw) != set(sidecar):
        raise ValueError("B checkpoint/sidecar class IDs or effective mapping differ")
    generic = all(name == f"class_{index}" for index, name in raw.items())
    if not generic and raw != sidecar:
        raise ValueError("B named checkpoint classes conflict with the provided sidecar")
    return {"checkpoint_names": raw, "effective_names": effective,
            "mapping_source": "provided_sidecar" if generic else "checkpoint_and_sidecar",
            "independent_training_metadata_verified": False}


def validate_result(result, checked, starts):
    """Do not let empty/truncated/NaN inference become a no-candidate result."""
    duration = checked["duration_s"]
    if not math.isfinite(result["duration_s"]) or abs(result["duration_s"] - duration) > 1e-6:
        raise ValueError("Analysis duration differs from the fully decoded input")
    if not math.isfinite(result["fps"]) or abs(result["fps"] - checked["fps"]) > 1e-6:
        raise ValueError("Analysis FPS differs from the fully decoded input")
    windows = result["x3d"]["windows"]
    expected_starts = starts(duration)
    if not windows or len(windows) != len(expected_starts):
        raise ValueError("Missing inference windows")
    for row, start in zip(windows, expected_starts):
        score = row["collision_probability"]
        values = (score, row["start_s"], row["end_s"])
        if any(not math.isfinite(value) for value in values) or not 0 <= score <= 1:
            raise ValueError("Invalid inference window values")
        if abs(row["start_s"] - start) > 1e-6 or abs(row["end_s"] - min(start + 2, duration)) > 1e-6:
            raise ValueError("Inference window coverage mismatch")
        if row["is_positive"] != (score >= 0.5):
            raise ValueError("Inference threshold flag mismatch")
    events, handoffs = result["events"], result["vlm_handoffs"]
    if not isinstance(events, list) or not isinstance(handoffs, list) or len(events) != len(handoffs):
        raise ValueError("Events and handoffs differ")
    if bool(events) != any(row["is_positive"] for row in windows):
        raise ValueError("Positive windows and candidate state differ")
    seen = set()
    for event, handoff in zip(events, handoffs):
        name, candidate = event["event_id"], event["candidate_time_s"]
        if not re.fullmatch(r"event_[0-9]+", name) or name in seen:
            raise ValueError("Invalid or duplicate event ID")
        if handoff["event_id"] != name or handoff["candidate_time_s"] != candidate:
            raise ValueError("Event/handoff identity mismatch")
        if not math.isfinite(candidate) or not 0 <= candidate < duration:
            raise ValueError("Candidate lies outside input")
        seen.add(name)
    return "completed" if events else "no_candidates"


def contained_file(root, path):
    root = Path(root).resolve()
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError("Output media path is missing or escapes the job")
    # Reject symlinks in parent components too, including links inside the job.
    if any(part.is_symlink() for part in (path, *path.parents) if part != root and part.is_relative_to(root)):
        raise ValueError("Output media contains a symlink")
    if path.stat().st_size <= 0:
        raise ValueError("Output media is empty")
    return path


def artifact_records(root):
    records = []
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink found in result artifacts")
        if path.is_file():
            records.append({"path": path.relative_to(root).as_posix(),
                            "bytes": path.stat().st_size, "sha256": sha256(path)})
    return records


def verify_media(path, expected_frames, fps=None):
    """Decode the generated asset, including its final frame, before success."""
    import av

    count, times = 0, []
    with av.open(str(path)) as source:
        codec = source.streams.video[0].codec_context.name
        for frame in source.decode(video=0):
            if frame.width <= 0 or frame.height <= 0:
                raise ValueError("Empty decoded evidence frame")
            count += 1
            if count > expected_frames:
                raise ValueError("Evidence has more frames than its intended interval")
            times.append(frame.time)
    if count != expected_frames:
        raise ValueError("Evidence output is incomplete")
    if fps is not None:
        tolerance = min(0.002, 0.05 / fps)
        if any(t is None or not math.isfinite(t) for t in times):
            raise ValueError("Evidence PTS missing")
        if any(abs(t - times[0] - i / fps) > tolerance for i, t in enumerate(times)):
            raise ValueError("Evidence PTS timeline differs from expected CFR")
    return {"decoded_frames": count, "codec": codec, "pts_verified": fps is not None}


def media_records(version, result, raw, checked, evidence=None):
    records = []
    if version == "A":
        if len(evidence["events"]) != len(result["events"]):
            raise ValueError("A evidence event count mismatch")
        for event in evidence["events"]:
            for role, key, count, fps in (
                ("full_clip", "clip", event["clip_frame_count"], checked["fps"]),
                ("frame", "frame", 1, None),
            ):
                path = contained_file(raw, event[key])
                records.append({"event_id": event["event_id"], "role": role,
                                "path": "raw/" + path.relative_to(raw).as_posix(),
                                **verify_media(path, count, fps)})
    else:
        for event in result["events"]:
            context = event["incident_context"]
            candidate = event["candidate_time_s"]
            # B's frozen _write_clip uses round and VLM_BEFORE=3/AFTER=5.
            start = max(0, round(max(0.0, candidate - 3.0) * checked["fps"]))
            end = max(start + 1, round(min(checked["duration_s"], candidate + 5.0) * checked["fps"]))
            if context["event_id"] != event["event_id"]:
                raise ValueError("B context event identity mismatch")
            for role in ("full_clip", "roi_clip", "annotated_frame"):
                path = contained_file(raw, context["files"][role])
                count = 1 if role == "annotated_frame" else end - start
                fps = None if role == "annotated_frame" else checked["fps"]
                records.append({"event_id": event["event_id"], "role": role,
                                "path": "raw/" + path.relative_to(raw).as_posix(),
                                **verify_media(path, count, fps)})
            contained_file(raw, raw / event["event_id"] / (event["event_id"] + "_context.json"))
    return records


def run_job(work_root, version, video, output, *, expected_gpu=GPU):
    started = time.perf_counter()
    if not valid_gpu_name(expected_gpu):
        raise ValueError("Invalid expected GPU name")
    require_remote_process()
    sys.addaudithook(block_network_event)
    a_root = (work_root / "cctv-runpod-a4000").resolve(strict=True)
    b_root = (work_root / "deployment_handoff_1001").resolve(strict=True)
    video = video.resolve(strict=True)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    output = output.resolve()
    raw = output / "raw"
    raw.mkdir()
    local_hub = a_root / "local_hub"
    if not (local_hub / "hubconf.py").is_file():
        raise FileNotFoundError("Verified local X3D hub is missing")
    os.environ["PYTORCHVIDEO_REPO"] = str(local_hub)
    os.environ["YOLO_CONFIG_DIR"] = str(output / "yolo-config")
    os.environ["YOLO_OFFLINE"] = "true"
    os.environ["WANDB_DISABLED"] = "true"
    os.environ["HF_HUB_OFFLINE"] = "1"
    sys.path.insert(0, str(a_root))
    worker = load_source("_ab_frozen_a_worker", a_root / "worker.py")
    evidence_module = importlib.import_module("evidence")
    if Path(evidence_module.__file__).resolve() != a_root / "evidence.py":
        raise RuntimeError("A evidence module origin mismatch")
    torch, environment = worker.environment()
    validate_gpu(environment, torch.cuda.device_count(), expected_gpu)
    torch.cuda.reset_peak_memory_stats()
    source_sha = sha256(video)
    checked = worker.check_video(video)
    # Both pipelines share the same validated source time basis, including B.
    evidence_module.verify_timeline(evidence_module.probe(video),
                                    checked["fps"], checked["decoded_frames"])
    decoded_at = time.perf_counter()
    if version == "A":
        sys.path.insert(0, str(a_root / "handoff"))
        pipeline = load_source("_ab_a_pipeline", a_root / "handoff/pipeline.py")
        x3d_path = a_root / "handoff/models/x3d_s_real_so_tad_epoch7_best_model.pt"
        yolo_path = a_root / "handoff/models/yolo11s_accident_150_e30_best.pt"
    else:
        package = types.ModuleType("_ab_b_source")
        package.__path__ = [str(b_root)]
        sys.modules[package.__name__] = package
        pipeline = importlib.import_module("_ab_b_source.pipeline")
        if Path(pipeline.__file__).resolve() != b_root / "pipeline.py":
            raise RuntimeError("B pipeline origin mismatch")
        x3d_path = b_root / "models/x3d_s_real_so_tad_epoch7_best_model.pt"
        yolo_path = b_root / "models/yolo11s_so_tad_985_no_class4_960_b16_best.pt"
    models = pipeline.ModelBundle(x3d_path, yolo_path, device="cuda")
    class_mapping = {"checkpoint_names": names_dict(models.yolo.names),
                     "effective_names": names_dict(models.yolo_names)}
    if version == "B":
        sidecar = json.loads(yolo_path.with_name("yolo11s_so_tad_985_no_class4_960_b16_classes.json").read_text())
        class_mapping = validate_b_classes(models.yolo.names, sidecar["class_names"], models.yolo_names)
    torch.cuda.synchronize()
    loaded_at = time.perf_counter()
    embedded_media_seconds = [0.0]
    if version == "B":
        import cv2
        context = importlib.import_module("_ab_b_source.incident_context")
        if (context.VLM_BEFORE_S, context.VLM_AFTER_S) != (3.0, 5.0):
            raise ValueError("B evidence context differs from the fixed comparison")
        original_clip, original_image = context._write_clip, cv2.imwrite

        def measured(function):
            def call(*args, **kwargs):
                begin = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    embedded_media_seconds[0] += time.perf_counter() - begin
            return call

        context._write_clip, cv2.imwrite = measured(original_clip), measured(original_image)
        try:
            result = pipeline.analyze_video(video, raw, models, batch_size=8)
        finally:
            context._write_clip, cv2.imwrite = original_clip, original_image
    else:
        result = pipeline.analyze_video(video, raw, models, batch_size=8)
    torch.cuda.synchronize()
    analyzed_at = time.perf_counter()
    status = validate_result(result, checked, pipeline.window_starts)
    json.dumps(result, allow_nan=False)
    if json.loads((raw / "result.json").read_text()) != result:
        raise ValueError("Saved raw result differs from the returned analysis")
    if json.loads((raw / "vlm_handoffs.json").read_text()) != result["vlm_handoffs"]:
        raise ValueError("Saved handoffs differ from the analysis")
    evidence = worker.create_evidence(video, raw, checked, 3.0, 3.0) if version == "A" else None
    exported_at = time.perf_counter()
    media = media_records(version, result, raw, checked, evidence)
    verified_at = time.perf_counter()
    timings = {
        "startup_and_input_validation_seconds": decoded_at - started,
        "model_load_seconds": loaded_at - decoded_at,
        "analysis_and_json_seconds": analyzed_at - loaded_at,
        "media_export_seconds": exported_at - analyzed_at if version == "A" else embedded_media_seconds[0],
        "media_validation_seconds": verified_at - exported_at,
        "job_seconds_before_manifest": verified_at - started,
    }
    memory = {"peak_allocated_bytes": torch.cuda.max_memory_allocated(),
              "peak_reserved_bytes": torch.cuda.max_memory_reserved()}
    runtime = {**environment, "input_sha256": source_sha, "version": version,
               "video_check": checked, "source_pts_verified": True,
               "batch_size": 8, "training": False, "class_mapping": class_mapping,
               "torch_threads": torch.get_num_threads(),
               "torch_interop_threads": torch.get_num_interop_threads(),
               "timings": timings, "memory": memory,
               "timing_note": "A media export is separate evidence generation; B analysis includes media export, whose clip/image write time is also reported. Timings are wall time, not GPU-only forward time.",
               "memory_note": "PyTorch peak allocator memory only, not whole-device usage.",
               "network": "local_hub_and_checkpoints; Python network audit guard",
               "gemini_calls": 0, "embedding_calls": 0, "s3_uploaded": False}
    write_new_json(raw / "runtime.json", runtime)
    write_new_json(raw / "manifest.json", {"schema_version": "cctv-ab-raw-v1",
                   "input_sha256": source_sha, "version": version, "files": artifact_records(raw)})
    artifacts = [{**record, "path": "raw/" + record["path"]} for record in artifact_records(raw)]
    timings = {**timings, "job_seconds": time.perf_counter() - started}
    normalized = {"schema_version": "cctv-ab-job-v1", "status": status,
                  "version": version, "input_sha256": source_sha, "event_count": len(result["events"]),
                  "window_count": len(result["x3d"]["windows"]), "gpu": environment["gpu"],
                  "timings": timings, "memory": memory, "media": media, "artifacts": artifacts,
                  "candidate_times_s": [event["candidate_time_s"] for event in result["events"]],
                  "class_mapping": class_mapping, "raw_result": "raw/result.json",
                  "raw_manifest": "raw/manifest.json", "training": False,
                  "interpretation": "Candidate outputs for human review; no_candidates does not establish normality.",
                  "gemini_calls": 0, "embedding_calls": 0}
    # Only this final, exclusive file tells the parent that all checks passed.
    write_new_json(output / "result.json", normalized)
    print(json.dumps({"status": status, "version": version, "event_count": len(result["events"])}), flush=True)
    return normalized


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--version", choices=("A", "B"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-gpu", default=GPU)
    args = parser.parse_args()
    if not valid_gpu_name(args.expected_gpu):
        parser.error("--expected-gpu must name an observed CUDA device")
    run_job(args.work_root, args.version, args.input, args.output, expected_gpu=args.expected_gpu)


if __name__ == "__main__":
    main()
