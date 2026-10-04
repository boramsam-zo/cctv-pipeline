"""읽기 전용 소수 검사. 기본은 ACCIDENT real_videos, --file-only는 외부 파일 수신.

CSV/YAML은 원문 의미를 보존한다. 초 단위 확정은 --time-unit seconds와
--time-source 근거를 함께 명시해야 한다. 디코딩은 별도 프로세스/시간 제한 적용.
외부 수신은 명시한 상대 경로 최대3개만 읽으며 주석·정답·분할을 만들지 않는다.
"""
import argparse
from collections import Counter
import csv
import hashlib
import importlib
import itertools
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time

RECEIPT_TIMEOUT_SECONDS = 20
MAX_RECEIPT_BYTES = 512 * 1024 * 1024


class InspectionError(Exception):
    pass


def safe_file(root, relative):
    """상대 경로만 허용. 심볼릭 링크로 지정 폴더 밖에 접근하지 않는다."""
    rel = PurePosixPath(relative)
    if (not relative or rel.is_absolute() or ".." in rel.parts
            or "\\" in relative or ":" in relative):
        raise InspectionError("unsafe_path")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise InspectionError("outside_data_root")
    return path


def fingerprint(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": h.hexdigest()}


def read_metadata(path, key):
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, strict=True)
        columns = reader.fieldnames
        if (not columns or any(not c.strip() for c in columns)
                or len(set(columns)) != len(columns)):
            raise InspectionError("invalid_csv_header")
        if key not in columns:
            raise InspectionError("missing_key_column")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise InspectionError("invalid_csv_row_width")
            rows.append(row)
    if not rows:
        raise InspectionError("empty_metadata")
    counts = Counter(row[key] for row in rows)
    return rows, {
        "columns": columns,
        "row_count": len(rows),
        "key_column": key,
        "unique_key_count": len(counts),
        "duplicate_key_count": sum(n > 1 for n in counts.values()),
        "empty_key_rows": counts.get("", 0),
        "empty_value_counts": {c: sum(not r[c].strip() for r in rows) for c in columns},
        "categorical_values": {c: sorted({r[c] for r in rows}) for c in columns
                               if c != key and len({r[c] for r in rows}) <= 30},
        **fingerprint(path),
    }


def read_class_map(path):
    try:
        yaml = importlib.import_module("yaml")
    except ImportError:
        return {"status": "tool_missing", "tool": "PyYAML"}

    class UniqueLoader(yaml.SafeLoader):
        pass

    def unique_mapping(loader, node):
        pairs = loader.construct_pairs(node, deep=True)
        mapping = {}
        for key, value in pairs:
            if key in mapping:
                raise InspectionError("duplicate_yaml_key")
            mapping[key] = value
        return mapping

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
    try:
        data = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=UniqueLoader)
        if not isinstance(data, dict) or not isinstance(data.get("names"), dict):
            raise InspectionError("invalid_yaml_class_map")
        names = data["names"]
        if not names or any(type(k) is not int or k < 0 or not isinstance(v, str)
                            for k, v in names.items()):
            raise InspectionError("invalid_yaml_classes")
        return {"status": "ok", "top_level_keys": list(data), "names": names,
                "class_count": len(names), "applied_to_binary_labels": False,
                **fingerprint(path)}
    except (yaml.YAMLError, TypeError, ValueError):
        raise InspectionError("invalid_yaml") from None


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def probe_video(path):
    """각 위치의 이전 키프레임부터 3프레임. 목표 시각과 실제 PTS를 구분한다."""
    try:
        av = importlib.import_module("av")
    except ImportError:
        return {"status": "tool_missing", "tool": "av"}
    result = {"backend": "PyAV", "version": av.__version__}
    try:
        with av.open(str(path), mode="r") as container:
            if not container.streams.video:
                return {**result, "status": "no_video_stream"}
            stream = container.streams.video[0]
            duration = (float(stream.duration * stream.time_base)
                        if stream.duration is not None and stream.time_base else None)
            container_duration = (float(container.duration / av.time_base)
                                  if container.duration is not None else None)
            result.update({
                "duration_seconds": duration,
                "container_duration_seconds": container_duration,
                "average_fps": float(stream.average_rate) if stream.average_rate else None,
                "average_fps_fraction": str(stream.average_rate) if stream.average_rate else None,
                "base_fps": float(stream.base_rate) if stream.base_rate else None,
                "header_frame_count": stream.frames or None,
                "width": stream.codec_context.width, "height": stream.codec_context.height,
                "codec": stream.codec_context.name,
                "pixel_format": stream.codec_context.pix_fmt,
                "time_base": str(stream.time_base),
                "start_time_seconds": float((stream.start_time or 0) * stream.time_base),
                "decode_method": "3 frames from previous keyframe at 0%, 50%, 90%",
                "full_video_checked": False,
                "constant_frame_rate_verified": False,
                "decode_samples": [],
            })
            length = duration or container_duration
            targets = [0.0, length * 0.5, length * 0.9] if length and length > 0 else [0.0]
            for target in targets:
                container.seek(int(target / stream.time_base) + (stream.start_time or 0),
                               stream=stream, backward=True, any_frame=False)
                frames = []
                packets_read = 0
                for packet in itertools.islice(container.demux(stream), 96):
                    packets_read += 1
                    for frame in packet.decode():
                        if frame.is_corrupt:
                            raise InspectionError("corrupt_frame")
                        # 디코딩된 프레임의 픽셀 버퍼가 존재하는지 확인한다.
                        if not frame.planes or not frame.planes[0].buffer_size:
                            raise InspectionError("empty_decoded_frame")
                        frames.append({"pts_seconds": frame.time,
                                       "width": frame.width, "height": frame.height})
                        if len(frames) == 3:
                            break
                    if len(frames) == 3:
                        break
                result["decode_samples"].append({"requested_seconds": target,
                                                  "packets_read": packets_read,
                                                  "frames": frames})
                if len(frames) != 3:
                    raise InspectionError("insufficient_decoded_frames")
        result["status"] = "ok"
    except FileNotFoundError:
        result["status"] = "file_missing"
    except PermissionError:
        result["status"] = "access_denied"
    except InspectionError as exc:
        result["status"] = str(exc)
    except Exception as exc:
        # 라이브러리 오류의 개인 절대 경로/원본 내용은 출력하지 않는다.
        result.update(status="media_open_or_decode_failed", error_type=type(exc).__name__)
    return result


def run_probe(path, timeout=20):
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                               "--probe-internal", str(path)], capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "decode_timeout"}
    except OSError:
        return {"status": "probe_launch_failed"}
    try:
        result = json.loads(proc.stdout)
        if proc.returncode or not isinstance(result, dict) or "status" not in result:
            raise ValueError
        return result
    except (ValueError, TypeError):
        return {"status": "probe_process_failed"}


def receipt_file(path):
    """Worker: bound hashing and the reused decoder together, not decoding alone."""
    started = time.monotonic()
    try:
        if not path.is_file():
            return {"media": {"status": "not_regular_file" if path.exists() else "file_missing"}}
        if path.stat().st_size > MAX_RECEIPT_BYTES:
            return {"media": {"status": "file_size_exceeds_512_mib"}}
        before = fingerprint(path)
        media = probe_video(path)
        after = fingerprint(path)
        result = {"file": before, "media": media, "original_unchanged": before == after}
        if before != after:
            result["media"] = {**media, "status": "source_changed_during_check"}
    except FileNotFoundError:
        result = {"media": {"status": "file_missing"}}
    except PermissionError:
        result = {"media": {"status": "access_denied"}}
    except OSError:
        result = {"media": {"status": "file_unreadable"}}
    result["worker_seconds"] = time.monotonic() - started
    return result


def run_receipt_probe(path):
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                               "--receipt-internal", str(path)], capture_output=True,
                              text=True, timeout=RECEIPT_TIMEOUT_SECONDS)
        result = json.loads(proc.stdout)
        if (proc.returncode or not isinstance(result, dict)
                or not isinstance(result.get("media"), dict)
                or not isinstance(result["media"].get("status"), str)):
            raise ValueError
        return result
    except subprocess.TimeoutExpired:
        return {"media": {"status": "receipt_timeout"}}
    except OSError:
        return {"media": {"status": "receipt_launch_failed"}}
    except (ValueError, TypeError):
        return {"media": {"status": "receipt_process_failed"}}


def inspect_files(root, selected, probe=run_receipt_probe):
    """External receipt only. Success never authorizes a label or a data split."""
    if not 1 <= len(selected) <= 3:
        raise InspectionError("select_1_to_3_distinct_videos")
    # Known ACCIDENT roots must retain their CSV/split checks in the default mode.
    if ((root / "metadata-real.csv").exists()
            or (root.name == "real_videos" and (root.parent / "metadata-real.csv").exists())):
        raise InspectionError("use_accident_metadata_mode")
    paths = []
    for relative in selected:
        path = safe_file(root, relative)
        if (path.suffix.lower() not in {".mp4", ".avi", ".mov", ".mkv"}
                or "synthetic_videos" in path.parts):
            raise InspectionError("unsupported_receipt_path")
        paths.append(path)
    if len({p.resolve() for p in paths}) != len(paths):
        raise InspectionError("select_1_to_3_distinct_videos")
    report = {"scope": "selected_external_file_receipt_only", "selected": [],
              "metadata": {"status": "not_supplied_in_file_only_mode"},
              "predictions_generated": False, "training_run": False,
              "worker_timeout_seconds": RECEIPT_TIMEOUT_SECONDS,
              "max_selected_files": 3, "max_file_bytes": MAX_RECEIPT_BYTES,
              "unselected_video_files_opened": 0, "frame_files_saved": 0}
    for relative, path in zip(selected, paths):
        # Missing files do not launch a hash/decode worker, including an all-missing request.
        try:
            if not path.is_file():
                result = {"media": {"status": "not_regular_file" if path.exists() else "file_missing"}}
            else:
                result = probe(path)
        except PermissionError:
            result = {"media": {"status": "access_denied"}}
        except OSError:
            result = {"media": {"status": "file_unreadable"}}
        report["selected"].append({"path": relative, **result,
            "label_match": "not_supplied", "annotation_time_unit": "unknown", "target": None,
            "approved_segment": None, "review_status": "unreviewed",
            "official_split": None, "upstream_original_id": None, "event_id": None,
            "group_relationship": "unverified", "training_authorized": False})
    missing = all(item["media"]["status"] == "file_missing" for item in report["selected"])
    success = all(item["media"]["status"] == "ok" and item.get("original_unchanged") is True
                  for item in report["selected"])
    report["status"] = ("not_run_files_missing" if missing else
                        "files_checked_labels_unreviewed" if success else "needs_attention")
    return report


def check_annotation(row, media, unit):
    result = {"time_unit": unit, "frame_index_base": "unverified",
              "binary_label": "not_derived", "interval_label": "not_derived",
              "issues": [], "metadata_comparison": {}}
    for column, measured in [("width", "width"), ("height", "height"),
                              ("no_frames", "header_frame_count")]:
        value = finite_number(row.get(column))
        actual = media.get(measured)
        if value is not None and actual is not None:
            result["metadata_comparison"][column] = {"csv": value, "media": actual,
                                                       "equal": value == actual}
            if value != actual:
                result["issues"].append(column + "_mismatch")
    timestamp = finite_number(row.get("accident_time"))
    frame = finite_number(row.get("accident_frame"))
    if timestamp is None or frame is None:
        result["issues"].append("missing_or_invalid_time_annotation")
    if frame is not None and (frame < 0 or not frame.is_integer()):
        result["issues"].append("invalid_frame_index")
    if unit != "seconds":
        result["issues"].append("time_unit_unverified")
        return result
    duration = media.get("duration_seconds")
    if timestamp is not None and (timestamp < 0 or (duration is not None and timestamp >= duration)):
        result["issues"].append("time_out_of_range")
    count = media.get("header_frame_count")
    if frame is not None and count and frame > count:
        result["issues"].append("frame_out_of_range")
    csv_duration = finite_number(row.get("duration"))
    if csv_duration is not None and duration is not None:
        delta = duration - csv_duration
        result["duration_delta_seconds"] = delta
        if abs(delta) > 0.05:
            result["issues"].append("duration_mismatch_over_0.05s")
    fps = media.get("average_fps")
    if timestamp is not None and frame is not None and fps and fps > 0:
        result["frame_div_average_fps_seconds"] = frame / fps
        result["time_minus_frame_div_average_fps_seconds"] = timestamp - frame / fps
        result["conversion_note"] = "diagnostic_only_not_frame_alignment_proof"
    coords = {c: finite_number(row.get(c)) for c in ("center_x", "center_y", "x1", "y1", "x2", "y2")}
    if any(v is None or not 0 <= v <= 1 for v in coords.values()):
        result["issues"].append("missing_or_invalid_normalized_coordinates")
    elif coords["x1"] > coords["x2"] or coords["y1"] > coords["y2"]:
        result["issues"].append("invalid_box_order")
    else:
        result["center_equals_box_midpoint"] = all(
            math.isclose(coords[center], (coords[low] + coords[high]) / 2, abs_tol=1e-9)
            for center, low, high in [("center_x", "x1", "x2"), ("center_y", "y1", "y2")])
    return result


def inspect(root, selected, key="path", unit="unknown", time_source=None, probe=run_probe):
    if not 1 <= len(selected) <= 3 or len(set(selected)) != len(selected):
        raise InspectionError("select_1_to_3_distinct_videos")
    if unit not in ("unknown", "seconds") or (unit == "seconds" and not time_source):
        raise InspectionError("time_unit_requires_source")
    paths = []
    for relative in selected:
        path = safe_file(root, relative)
        if PurePosixPath(relative).parts[0] != "real_videos" or path.suffix.lower() != ".mp4":
            raise InspectionError("only_selected_real_mp4_allowed")
        if not path.resolve().is_relative_to((root / "real_videos").resolve()):
            raise InspectionError("outside_real_video_scope")
        paths.append(path)
    report = {"scope": "S0_selected_real_only", "time_source": time_source,
              "metadata": {}, "class_map": {}, "selected": [], "issues": [],
              "predictions_generated": False}
    rows = None
    for name, target in [("metadata-real.csv", "metadata"), ("annotation_classes.yaml", "class_map")]:
        try:
            path = safe_file(root, name)
            if target == "metadata":
                rows, summary = read_metadata(path, key)
                report[target] = {"status": "ok", **summary}
            else:
                report[target] = read_class_map(path)
        except (OSError, UnicodeError, csv.Error, InspectionError) as exc:
            status = ("file_missing" if isinstance(exc, FileNotFoundError)
                      else "access_denied" if isinstance(exc, PermissionError)
                      else str(exc) if isinstance(exc, InspectionError) else "invalid_or_unreadable")
            report[target] = {"status": status}
    report["excluded_metadata_rows"] = (sum(row[key] not in selected for row in rows)
                                         if rows is not None else None)
    report["excluded_video_files_opened"] = 0
    for relative, path in zip(selected, paths):
        matches = [(index + 2, row) for index, row in enumerate(rows or []) if row[key] == relative]
        item = {"path": relative, "label_match": "metadata_unavailable" if rows is None
                else "missing" if not matches else "unique" if len(matches) == 1 else "ambiguous",
                "csv_rows": [index for index, _ in matches], "annotations": [row for _, row in matches]}
        try:
            before = fingerprint(path)
            item["media"] = probe(path)
            after = fingerprint(path)
            item.update(file=before, original_unchanged=before == after)
        except FileNotFoundError:
            item["media"] = {"status": "file_missing"}
        except PermissionError:
            item["media"] = {"status": "access_denied"}
        except OSError:
            item["media"] = {"status": "file_unreadable"}
        if len(matches) == 1:
            item["annotation_checks"] = check_annotation(matches[0][1], item["media"], unit)
        report["selected"].append(item)
    failures = (any(report[k]["status"] != "ok" for k in ("metadata", "class_map"))
                or any(i["media"]["status"] != "ok" or i["label_match"] != "unique"
                       or i.get("original_unchanged") is not True for i in report["selected"]))
    if report["metadata"].get("duplicate_key_count") or report["metadata"].get("empty_key_rows"):
        report["issues"].append("metadata_key_integrity_issue")
    warnings = bool(report["issues"]) or any(i.get("annotation_checks", {}).get("issues")
                                           for i in report["selected"])
    report["status"] = "needs_attention" if failures else "checked_with_warnings" if warnings else "checked"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--video", action="append", required=True, help="data-root 기준의 정확한 상대 경로")
    parser.add_argument("--file-only", action="store_true", help="CSV 없는 외부 영상 수신만 검사, 정답 생성 안 함")
    parser.add_argument("--key-column", default="path")
    parser.add_argument("--time-unit", choices=["unknown", "seconds"], default="unknown")
    parser.add_argument("--time-source")
    args = parser.parse_args()
    try:
        if args.file_only:
            if args.time_unit != "unknown" or args.time_source or args.key_column != "path":
                raise InspectionError("annotation_options_require_metadata_mode")
            result = inspect_files(args.data_root, args.video)
        else:
            result = inspect(args.data_root, args.video, args.key_column, args.time_unit, args.time_source)
    except InspectionError as exc:
        result = {"status": "invalid_request", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 2 if result["status"] in ("needs_attention", "invalid_request", "not_run_files_missing") else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe-internal":
        print(json.dumps(probe_video(Path(sys.argv[2])), ensure_ascii=False, allow_nan=False))
    elif len(sys.argv) == 3 and sys.argv[1] == "--receipt-internal":
        print(json.dumps(receipt_file(Path(sys.argv[2])), ensure_ascii=False, allow_nan=False))
    else:
        raise SystemExit(main())
