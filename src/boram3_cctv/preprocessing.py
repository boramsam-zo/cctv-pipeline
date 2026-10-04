"""Small, read-only video preprocessing. No model, inference, or training imports."""
from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import time

VERSION = "sample-preprocess-0.1"
CLASS_ORDER = ["normal", "accident"]
MAX_DECODED_FRAMES = 900
MAX_PACKETS = 2000
MAX_SEGMENT_SECONDS = 10.0


class PreprocessingError(Exception):
    """Stable, path-free error code suitable for the output manifest."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    def no_constant(value):
        raise PreprocessingError("nonfinite_json_number")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=no_constant)


def write_json(path: Path, value):
    # Callers write only into a new output directory or their own temporary directory.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PreprocessingError("invalid_time_value")
    return float(value)


def source_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise PreprocessingError("invalid_source_path")
    rel = PurePosixPath(relative)
    if (rel.is_absolute() or ".." in rel.parts or "\\" in relative or ":" in relative
            or rel.parts[0] != "real_videos" or rel.suffix.lower() != ".mp4"):
        raise PreprocessingError("only_relative_real_video_paths_allowed")
    path = root / relative
    real_root = root / "real_videos"
    if (not real_root.resolve().is_relative_to(root.resolve())
            or not path.resolve().is_relative_to(real_root.resolve())):
        raise PreprocessingError("source_outside_allowed_directory")
    return path


def validate_plan(plan):
    if (not isinstance(plan, dict) or plan.get("schema_version") != 1
            or plan.get("time_basis") != "local_clip_seconds"
            or not isinstance(plan.get("policy_version"), str) or not plan["policy_version"].strip()):
        raise PreprocessingError("invalid_plan_header")
    for key, lower, upper in [("frame_count", 2, 32), ("image_size", 32, 512)]:
        if type(plan.get(key)) is not int or not lower <= plan[key] <= upper:
            raise PreprocessingError("invalid_" + key)
    samples = plan.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= 3:
        raise PreprocessingError("select_1_to_3_samples")
    ids = []
    windows = []
    for sample in samples:
        if not isinstance(sample, dict) or not re.fullmatch(
                r"(?:sample_[0-9]{3}|devval_(?:accident|normal)_[0-9]{2})", str(sample.get("sample_id", ""))):
            raise PreprocessingError("invalid_sample_id")
        ids.append(sample["sample_id"])
        windows.append((sample.get("video_path"), sample.get("start_seconds"), sample.get("end_seconds")))
    if len(set(ids)) != len(ids):
        raise PreprocessingError("duplicate_sample_id")
    # Numeric/window errors are handled independently for each sample later.
    if len({repr(window) for window in windows}) != len(windows):
        raise PreprocessingError("duplicate_segment")


def validate_window(sample, duration=None):
    start = number(sample.get("start_seconds"))
    end = number(sample.get("end_seconds"))
    if start < 0 or end <= start:
        raise PreprocessingError("invalid_time_range")
    if end - start > MAX_SEGMENT_SECONDS:
        raise PreprocessingError("segment_exceeds_10_second_limit")
    if duration is not None and end > duration + 1e-6:
        raise PreprocessingError("time_out_of_video_range")
    return start, end


def approved_target(sample, policy_version, file_hash):
    review = sample.get("label_review")
    if not isinstance(review, dict) or review.get("status") not in ("unreviewed", "approved", "undetermined", "hold"):
        raise PreprocessingError("invalid_review_status")
    if review["status"] != "approved":
        if review.get("label") is not None:
            raise PreprocessingError("unapproved_label_must_be_null")
        return None
    if review.get("scope") != "segment":
        raise PreprocessingError("segment_specific_approval_required")
    if review.get("label") not in CLASS_ORDER:
        raise PreprocessingError("invalid_approved_label")
    for key in ("basis", "reviewer", "reviewed_at"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise PreprocessingError("missing_review_" + key)
    try:
        date.fromisoformat(review["reviewed_at"])
    except ValueError:
        raise PreprocessingError("invalid_review_date") from None
    if review.get("policy_version") != policy_version:
        raise PreprocessingError("review_policy_mismatch")
    if review.get("source_sha256") != file_hash:
        raise PreprocessingError("review_source_hash_mismatch")
    for key in ("start_seconds", "end_seconds"):
        if number(review.get(key)) != number(sample.get(key)):
            raise PreprocessingError("review_interval_mismatch")
    return CLASS_ORDER.index(review["label"])


def read_metadata(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if ("path" not in fields or len(set(fields)) != len(fields)
                or any(not field for field in fields)):
            raise PreprocessingError("invalid_metadata_header")
        result = {}
        for index, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise PreprocessingError("invalid_metadata_row")
            result.setdefault(row["path"], []).append({"csv_row": index, "values": row})
    return result


def letterbox(image, size):
    """Keep the entire source image; no crop, flip, text, or random augmentation."""
    from PIL import Image
    width, height = image.size
    ratio = min(size / width, size / height)
    new_width = max(1, min(size, round(width * ratio)))
    new_height = max(1, min(size, round(height * ratio)))
    left, top = (size - new_width) // 2, (size - new_height) // 2
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    canvas.paste(image.convert("RGB").resize((new_width, new_height), Image.Resampling.BILINEAR), (left, top))
    return canvas, {"method": "letterbox", "source_width": width, "source_height": height,
                    "resized_width": new_width, "resized_height": new_height,
                    "padding_left": left, "padding_top": top, "crop": None,
                    "color": "RGB", "interpolation": "bilinear"}


def sample_frames(path: Path, start: float, end: float, count: int, *, allow_repeated_frames=False):
    """Select nearest PTS to each bin centre, strictly inside [start, end).

    Decode from the preceding keyframe only as needed. Keep just the selected
    RGB images; never save the preroll or every decoded frame.
    """
    import av
    started = time.monotonic()
    requested = [start + (index + 0.5) * (end - start) / count for index in range(count)]
    selected = []
    decoded = 0
    packets = 0
    previous = None
    last_seen = None
    first_seen = None
    done = False
    with av.open(str(path), mode="r") as container:
        if not container.streams.video:
            raise PreprocessingError("no_video_stream")
        stream = container.streams.video[0]
        if stream.time_base is None or stream.duration is None or stream.duration <= 0:
            raise PreprocessingError("video_duration_unavailable")
        duration = float(stream.duration * stream.time_base)
        validate_window({"start_seconds": start, "end_seconds": end}, duration)
        origin = float((stream.start_time or 0) * stream.time_base)
        container.seek(int(start / stream.time_base) + (stream.start_time or 0), stream=stream,
                       backward=True, any_frame=False)

        def take(frame, actual):
            if selected and (actual < selected[-1]["actual_seconds"] or
                             actual == selected[-1]["actual_seconds"] and not allow_repeated_frames):
                raise PreprocessingError("insufficient_distinct_frames")
            selected.append({"requested_seconds": requested[len(selected)],
                             "actual_seconds": actual, "pts": frame.pts,
                             "pts_time_base": str(frame.time_base), "image": frame.to_image()})

        for packet in container.demux(stream):
            packets += 1
            if packets > MAX_PACKETS or time.monotonic() - started > 35:
                raise PreprocessingError("decode_budget_exceeded")
            for frame in packet.decode():
                decoded += 1
                if decoded > MAX_DECODED_FRAMES:
                    raise PreprocessingError("decode_budget_exceeded")
                if frame.is_corrupt or frame.pts is None or frame.time_base is None:
                    raise PreprocessingError("invalid_decoded_frame")
                actual = float(frame.pts * frame.time_base) - origin
                if last_seen is not None and actual <= last_seen:
                    raise PreprocessingError("nonincreasing_source_timestamps")
                last_seen = actual
                if first_seen is None:
                    first_seen = actual
                if actual < start:
                    continue
                if actual >= end:
                    done = True
                    break
                while len(selected) < count and requested[len(selected)] <= actual:
                    candidate = (frame, actual)
                    if previous is not None and requested[len(selected)] - previous[1] <= actual - requested[len(selected)]:
                        candidate = previous
                    take(*candidate)
                previous = (frame, actual)
                if len(selected) == count:
                    done = True
                    break
            if done:
                break
        while len(selected) < count and previous is not None:
            take(*previous)
        if len(selected) != count:
            raise PreprocessingError("insufficient_distinct_frames")
        info = {"duration_seconds": duration, "width": stream.codec_context.width,
                "height": stream.codec_context.height, "codec": stream.codec_context.name,
                "average_fps": float(stream.average_rate) if stream.average_rate else None,
                "fps_basis": "stream_average_rate; not proof of constant frame rate",
                "decoded_first_seconds_including_preroll": first_seen,
                "decoded_last_seconds_including_lookahead": last_seen,
                "header_frame_count": stream.frames, "source_start_pts_seconds": origin,
                "decoded_frame_count_including_preroll": decoded, "packets_read": packets,
                "saved_frame_count": len(selected), "full_video_extracted": False,
                "sampling": "nearest_PTS_to_uniform_bin_centres", "interval": "[start,end)"}
    if allow_repeated_frames:
        unique = len({f["actual_seconds"] for f in selected})
        info.update(sampling_policy="nearest-bin-repeat-v1", repeated_frames_allowed=True,
                    unique_frame_count=unique, repeated_selection_count=len(selected)-unique,
                    note="Repeated selections add no new motion information; source PTS must still increase.")
    return selected, info


def save_frames(directory: Path, frames, size, target, sample_id, *, label_caption=None, save_unlabeled_input=False):
    import numpy as np
    from PIL import Image, ImageDraw
    directory.mkdir(parents=True, exist_ok=True)
    frame_dir = directory / "frames"
    frame_dir.mkdir()
    arrays, timings = [], []
    columns = 4
    cell_height = size + 40
    sheet = Image.new("RGB", (columns * size, 44 + math.ceil(len(frames) / columns) * cell_height), "#18212b")
    draw = ImageDraw.Draw(sheet)
    status = label_caption or ("APPROVED SEGMENT LABEL" if target is not None else "UNCONFIRMED - NO TARGET")
    draw.text((8, 8), f"{sample_id} | {status}", fill="white")
    draw.text((8, 24), "Left to right, top to bottom. Verify content and timing before use.", fill="white")
    for index, frame in enumerate(frames):
        processed, transform = letterbox(frame["image"], size)
        processed.save(frame_dir / f"{index:03d}.png")
        arrays.append(np.asarray(processed, dtype=np.uint8))
        x, y = (index % columns) * size, 44 + (index // columns) * cell_height
        sheet.paste(processed, (x, y))
        draw.text((x + 4, y + size + 2), f"{index:02d} actual {frame['actual_seconds']:.3f}s", fill="white")
        draw.text((x + 4, y + size + 18), f"requested {frame['requested_seconds']:.3f}s", fill="#bcc8d4")
        timings.append({key: value for key, value in frame.items() if key != "image"})
    reference_index = len(frames) // 2
    frames[reference_index]["image"].save(directory / "source_reference.png")
    sheet.save(directory / "preview.png")
    array = np.stack(arrays)
    if target is not None or save_unlabeled_input:
        np.save(directory / "frames.npy", array, allow_pickle=False)
    details = {"frames": timings, "transform": transform,
               "source_reference_frame_index": reference_index,
               "array_shape": list(array.shape), "array_layout": "THWC", "dtype": "uint8",
               "range": [0, 255], "model_normalization": None,
               "strictly_increasing_timestamps": all(b["actual_seconds"] > a["actual_seconds"]
                                                      for a, b in zip(frames, frames[1:])),
               "model_input_written": target is not None or save_unlabeled_input, "target": target}
    write_json(directory / "frames.json", details)
    return details


def process_sample(root: Path, sample, settings, metadata_entry, directory: Path):
    """Called in an isolated worker; errors never create a normal label."""
    start, end = validate_window(sample)
    path = source_path(root, sample.get("video_path"))
    before = sha256(path)
    if not isinstance(sample.get("source_sha256"), str) or before != sample["source_sha256"]:
        raise PreprocessingError("source_hash_mismatch")
    target = approved_target(sample, settings["policy_version"], before)
    frames, info = sample_frames(path, start, end, settings["frame_count"])
    details = save_frames(directory, frames, settings["image_size"], target, sample["sample_id"])
    if sha256(path) != before:
        raise PreprocessingError("source_changed_during_processing")
    row = metadata_entry["values"]
    return {
        "sample_id": sample["sample_id"], "status": "prepared_labeled" if target is not None else "unconfirmed",
        "source_video_id": "sha256:" + before, "source_video_id_kind": "local_clip_content_hash",
        "source_path": sample["video_path"], "source_sha256": before,
        "upstream_original_id": sample.get("upstream_original_id"),
        "event_id": sample.get("event_id"), "source_group_id": sample.get("source_group_id"),
        "start_seconds": start, "end_seconds": end, "time_basis": "local_clip_seconds",
        "window_basis": sample.get("window_basis"), "label_review": sample["label_review"],
        "target": target, "label_basis": sample["label_review"].get("basis") if target is not None else None,
        "unconfirmed_reason": sample["label_review"]["status"] if target is None else None,
        "source_annotation": metadata_entry,
        "official_splits": {key: row.get(key) for key in ("split_in_distribution", "split_geo_aware")},
        "training_authorized": False, "independent_evaluation_ready": False,
        "original_unchanged": True, "media": info, "processed_frames": details,
        "preview_review_status": "not_reviewed", "automatic_accident_verification": False,
    }


DETRAC_FPS_SOURCE = 'https://www.albany.edu/faculty/mchang2/chang_download.html'


def sample_detrac_images(root: Path, sequence: str, start_seconds=0):
    """Read only 16 JPEGs in [0,2), using published 25fps; no measured PTS."""
    from PIL import Image
    if not re.fullmatch(r'MVI_[0-9]+', sequence):
        raise PreprocessingError('invalid_sequence_id')
    if type(start_seconds) is not int or start_seconds not in (0, 2, 4, 6):
        raise PreprocessingError("unsupported_detrac_window")
    folder = root / sequence
    if folder.is_symlink() or not folder.is_dir():
        raise PreprocessingError('sequence_missing_or_symlink')
    # All 50 expected names must exist; only the selected 16 are decoded.
    for number in range(start_seconds * 25 + 1, (start_seconds + 2) * 25 + 1):
        path = folder / f'img{number:05d}.jpg'
        if path.is_symlink() or not path.is_file():
            raise PreprocessingError('first_two_seconds_incomplete')
    frames = []
    resolution = None
    for index in range(16):
        requested = start_seconds + (index + .5) * 2 / 16
        number = math.floor(requested * 25 + .5) + 1
        path = folder / f'img{number:05d}.jpg'
        if path.stat().st_size > 16 * 1024 * 1024:
            raise PreprocessingError('image_size_budget_exceeded')
        digest = sha256(path)
        try:
            with Image.open(path) as image:
                if image.width * image.height > 4096 * 4096:
                    raise PreprocessingError('image_resolution_budget_exceeded')
                image.load()
                rgb = image.convert('RGB')
        except (OSError, ValueError) as exc:
            raise PreprocessingError('image_decode_failed') from exc
        if resolution is not None and rgb.size != resolution:
            raise PreprocessingError('inconsistent_frame_resolution')
        resolution = rgb.size
        if sha256(path) != digest:
            raise PreprocessingError('source_changed_during_read')
        frames.append({'image': rgb, 'requested_seconds': requested,
                       'actual_seconds': (number - 1) / 25,
                       'time_basis': 'frame_index_derived_not_measured_pts',
                       'source_frame_number': number, 'source_path': f'{sequence}/{path.name}',
                       'source_sha256': digest})
    return frames, {'fps': 25, 'fps_basis': 'published_dataset_rate_not_measured',
                    'fps_source': DETRAC_FPS_SOURCE, 'resolution': list(resolution),
                    'time_origin': 'img00001 = sequence-relative 0 seconds',
                    'duration_seconds': None, 'full_sequence_checked': False}


def prepare_detrac_preview(root: Path, sequences, output: Path):
    """Bounded preparation only; no train/evaluation manifest authorization."""
    import html
    started = time.monotonic()
    if not 1 <= len(sequences) <= 3 or len(set(sequences)) != len(sequences):
        raise PreprocessingError('select_one_to_three_unique_sequences')
    if any(not re.fullmatch(r'MVI_[0-9]+', s) for s in sequences):
        raise PreprocessingError('invalid_sequence_id')
    if not root.is_dir():
        raise PreprocessingError('data_root_missing')
    if output.exists():
        raise PreprocessingError('output_already_exists_choose_new_directory')
    if output.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(output.resolve()):
        raise PreprocessingError('output_overlaps_original_data')
    output.mkdir(parents=True)
    records = []
    for sequence in sequences:
        record = {'sequence_id': sequence, 'target': None, 'declared_target': 0,
                  'label_origin': 'user_assumed_normal', 'human_reviewed': False,
                  'source_group_id': f'ua-detrac:{sequence}', 'camera_group': None,
                  'independence_status': 'unconfirmed', 'official_split': None,
                  'split': 'unassigned', 'training_authorized': False,
                  'evaluation_authorized': False, 'start_seconds': 0, 'end_seconds': 2}
        try:
            if time.monotonic() - started > 70:
                raise PreprocessingError('preparation_time_budget_exceeded')
            frames, info = sample_detrac_images(root, sequence)
            details = save_frames(output / sequence, frames, 224, 0, sequence,
                                  label_caption='USER ASSUMED NORMAL - NOT VISUALLY REVIEWED')
            if any(sha256(root / f['source_path']) != f['source_sha256'] for f in frames):
                raise PreprocessingError('source_changed_during_preparation')
            record.update(status='prepared_not_split', target=0, info=info,
                          selected_source_files_unchanged=True,
                          frames_path=f'{sequence}/frames.npy',
                          frames_sha256=sha256(output / sequence / 'frames.npy'),
                          preview_path=f'{sequence}/preview.png', input=details)
        except (PreprocessingError, OSError, ValueError) as exc:
            record.update(status='error', reason=str(exc) if isinstance(exc, PreprocessingError)
                          else type(exc).__name__)
        records.append(record)
    report = {'status': 'prepared_not_split' if all(r['status'] != 'error' for r in records) else 'needs_attention',
              'purpose': 'detrac_input_preparation_not_training', 'samples': records,
              'elapsed_seconds': time.monotonic() - started, 'training_run': False,
              'model_run': False, 'user_visual_confirmation': 'pending',
              'limits': {'sequences': 3, 'decoded_images_per_sequence': 16, 'command_seconds': 90},
              'code_sha256': sha256(Path(__file__))}
    write_json(output / 'manifest.json', report)
    sections = []
    for row in records:
        sections.append('<h2>' + html.escape(row['sequence_id']) + '</h2>')
        if row['status'] == 'error':
            sections.append('<p>실패: ' + html.escape(row['reason']) + ' — 정상 입력으로 처리하지 않음</p>')
            continue
        sections.append('<p>선택 범위 [0,2)초 · 정상0(사용자 가정) · 미검수 · 분할 미배정</p>'
                        '<img style="max-width:100%" src="' + row['preview_path'] + '">')
        sections.append('<table border="1"><tr><th>원본 파일</th><th>프레임 번호</th><th>계산된 초</th></tr>')
        for frame in row['input']['frames']:
            sections.append(f"<tr><td>{html.escape(frame['source_path'])}</td>"
                            f"<td>{frame['source_frame_number']}</td><td>{frame['actual_seconds']:.3f}</td></tr>")
        sections.append('</table>')
    (output / 'report.html').write_text('<!doctype html><meta charset="utf-8"><title>DETRAC 정상 입력 준비</title>'
        '<h1>DETRAC 정상 입력 준비 — 모델 실행 없음</h1><p>목표: 다른 CCTV의 충돌·전복 구간 탐지.</p>'
        '<p>번호 순서 → 2초에서16장 → 기존224 letterbox. 시각은 공식25fps로 계산한 시퀀스 상대 시각이며 측정 PTS가 아닙니다. '
        '100시퀀스가100독립 카메라를 뜻하지 않습니다. 공식 분할·카메라 관계·이용조건은 별도 확인 대상입니다.</p>'
        + ''.join(sections) + '<p>입력 준비 시간: ' + f"{report['elapsed_seconds']:.3f}초"
        + ' (HTML 저장 전). 사고 점수·정확도·학습은 이번에 계산하지 않았습니다.</p>', encoding='utf-8')
    return report


def extract_detrac_selection(archive: Path, sequences, output: Path):
    """Read ZIP directory, extract only 50 JPEGs per explicit sequence (max3)."""
    import zipfile
    import stat
    from pathlib import PurePosixPath
    if not 1 <= len(sequences) <= 3 or len(set(sequences)) != len(sequences):
        raise PreprocessingError('select_one_to_three_unique_sequences')
    if any(not re.fullmatch(r'MVI_[0-9]+', s) for s in sequences):
        raise PreprocessingError('invalid_sequence_id')
    if output.exists():
        raise PreprocessingError('output_already_exists_choose_new_directory')
    if archive.resolve().is_relative_to(output.resolve()):
        raise PreprocessingError('output_overlaps_original_data')
    started = time.monotonic()
    wanted = {f'{s}/img{i:05d}.jpg' for s in sequences for i in range(1, 51)}
    selected = {}
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > 500000:
            raise PreprocessingError('archive_entry_budget_exceeded')
        for info in infos:
            path = PurePosixPath(info.filename)
            if '__MACOSX' in path.parts or len(path.parts) < 2:
                continue
            key = '/'.join(path.parts[-2:])
            if key not in wanted:
                continue
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename:
                raise PreprocessingError('unsafe_archive_member')
            if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                raise PreprocessingError('unsupported_archive_member')
            if key in selected:
                raise PreprocessingError('ambiguous_sequence_in_archive')
            if not 0 < info.file_size <= 16 * 1024 * 1024:
                raise PreprocessingError('archive_member_size_budget_exceeded')
            selected[key] = info
        if set(selected) != wanted:
            raise PreprocessingError('selected_sequence_frames_missing_in_archive')
        if sum(i.file_size for i in selected.values()) > 128 * 1024 * 1024:
            raise PreprocessingError('archive_selection_budget_exceeded')
        output.mkdir(parents=True)
        rows = []
        for key, info in sorted(selected.items()):
            if time.monotonic() - started > 60:
                raise PreprocessingError('archive_selection_timeout')
            destination = output / key
            destination.parent.mkdir(exist_ok=True)
            with zf.open(info) as src, destination.open('xb') as dst:
                payload = src.read(16 * 1024 * 1024 + 1)
                if len(payload) != info.file_size:
                    raise PreprocessingError('archive_member_size_mismatch')
                dst.write(payload)
            rows.append({'path': key, 'zip_member': info.filename,
                         'bytes': info.file_size, 'sha256': sha256(destination)})
    receipt = {'archive_name': archive.name, 'archive_bytes': archive.stat().st_size,
               'archive_sha256': None, 'archive_identity_note': 'full ZIP not hashed; selected files hashed',
               'selected_files': rows, 'file_count': len(rows),
               'extracted_bytes': sum(r['bytes'] for r in rows),
               'elapsed_seconds': time.monotonic() - started,
               'whole_archive_extracted': False, 'training_run': False}
    write_json(output / 'extraction.json', receipt)
    return receipt
