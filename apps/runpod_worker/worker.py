"""Run one received X3D/YOLO analysis on the remote CUDA host only."""

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time

from verify_bundle import sha256, verify
from evidence import create as create_evidence, write_manifest

ROOT = Path(__file__).resolve().parent


def environment():
    if sys.platform != "linux":
        raise RuntimeError("Run this worker on the Runpod Linux host, not on the Mac")
    import torch
    import torchvision

    if torch.__version__.split("+")[0] != "2.8.0" or torchvision.__version__.split("+")[0] != "0.23.0":
        raise RuntimeError("Expected torch 2.8.0 / torchvision 0.23.0 from the selected image")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; CPU fallback is disabled")
    versions = {name: importlib.metadata.version(name) for name in (
        "torch", "torchvision", "ultralytics", "pytorchvideo", "numpy", "opencv-python", "av"
    )}
    if versions["ultralytics"] != "8.4.155" or versions["pytorchvideo"] != "0.1.5":
        raise RuntimeError("Model library versions differ from this deployment profile")
    return torch, {
        "python": platform.python_version(), "packages": versions,
        "cuda_runtime": torch.version.cuda, "gpu": torch.cuda.get_device_name(0),
        "vram_bytes": torch.cuda.get_device_properties(0).total_memory,
    }


def check_video(path):
    """Reject unreadable/truncated inputs before allowing a normal verdict.

    This preserves the received CFR/FPS window convention. It is not PTS validation.
    """
    import cv2
    import math

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("Cannot open the input video")
        fps = cap.get(cv2.CAP_PROP_FPS)
        expected = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(fps) or fps <= 0 or not math.isfinite(expected) or expected < 2:
            raise ValueError("Missing valid FPS or declared frame count")
        count = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame is None:
                raise ValueError("Decoded frame is empty")
            count += 1
            if count / fps > 120.0 + 1e-6:
                raise ValueError("Input exceeds the 120-second deployment limit")
        if count != round(expected):
            raise ValueError("Decoded frame count differs from container metadata")
        return {"decoded_frames": count, "declared_frames": round(expected),
                "fps": fps, "duration_s": count / fps, "pts_verified": False}
    finally:
        cap.release()


def main():
    if sys.platform == "linux":
        # Do not leave a CUDA child running if the API parent is killed.
        import ctypes
        import signal

        parent = os.getppid()
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise RuntimeError("Could not configure worker parent-death handling")
        if os.getppid() != parent:
            raise RuntimeError("Worker parent exited during startup")
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    verify(ROOT)
    torch, env = environment()
    if args.check_only:
        print(json.dumps(env, indent=2))
        return
    if args.video is None or args.output is None:
        parser.error("--video and --output are required")
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    source_sha = sha256(args.video)
    checked = check_video(args.video)
    decoded_at = time.perf_counter()
    os.environ["PYTORCHVIDEO_REPO"] = str(ROOT / "local_hub")
    os.environ["YOLO_CONFIG_DIR"] = str(ROOT / "runtime" / "yolo-config")
    sys.path.insert(0, str(ROOT / "handoff"))
    from pipeline import ModelBundle, analyze_video

    torch.cuda.reset_peak_memory_stats()
    models = ModelBundle(
        ROOT / "handoff/models/x3d_s_real_so_tad_epoch7_best_model.pt",
        ROOT / "handoff/models/yolo11s_accident_150_e30_best.pt", device="cuda",
    )
    torch.cuda.synchronize()
    loaded_at = time.perf_counter()
    result = analyze_video(args.video, args.output, models, batch_size=8)
    torch.cuda.synchronize()
    ended = time.perf_counter()
    if abs(result["duration_s"] - checked["duration_s"]) > 1e-6:
        raise ValueError("Analysis coverage differs from the decoded input")
    evidence = create_evidence(args.video, args.output, checked,
        float(os.environ.get("EVIDENCE_PRE_SECONDS", "3")),
        float(os.environ.get("EVIDENCE_POST_SECONDS", "3")))
    evidence_ended = time.perf_counter()
    env.update({
        "schema_version": "runpod-local-v1", "input_sha256": source_sha,
        "video_check": checked, "batch_size": 8, "training": False,
        "preflight_video_seconds": decoded_at - started,
        "model_load_seconds": loaded_at - decoded_at,
        "analysis_and_json_seconds": ended - loaded_at,
        "total_worker_seconds": evidence_ended - started,
        "evidence_seconds": evidence_ended - ended,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "timing_note": "Wall time; analysis includes CPU decode/YOLO and is not pure GPU forward time",
        "storage": "temporary_container_disk", "s3_uploaded": False,
        "evidence_media_created": bool(evidence["events"]),
        "evidence_event_count": len(evidence["events"]),
        "evidence_pts_verified": True,
    })
    (args.output / "runtime.json").write_text(json.dumps(env, indent=2))
    write_manifest(args.output, source_sha)
    print(json.dumps({"status": "succeeded", "windows": len(result["x3d"]["windows"]),
                      "candidates": len(result["events"]), "training": False}))


if __name__ == "__main__":
    main()
