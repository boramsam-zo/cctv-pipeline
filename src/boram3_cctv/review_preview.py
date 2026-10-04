"""Bounded, label-free review movies. Not model input or a training loader."""
from fractions import Fraction
from pathlib import Path
import re
import resource
import sys
import time

from . import preprocessing as pp

VERSION = "review-preview-0.1"
MAX_DECODED = 900
MAX_PACKETS = 2000
MAX_ENCODED = 600
WORK_SECONDS = 25
PREVIEW_TIME_BASE = Fraction(1, 90000)


class PreviewError(ValueError):
    """Stable error codes, without private paths or original metadata."""


def validate_plan(plan):
    if (not isinstance(plan, dict) or plan.get("schema_version") != 1
            or plan.get("purpose") != "human_review_only"
            or plan.get("time_basis") != "local_file_pts_seconds"
            or not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("source_sha256", "")))):
        raise PreviewError("invalid_plan")
    segments = plan.get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= 3:
        raise PreviewError("select_1_to_3_segments")
    ids, intervals = set(), []
    for segment in segments:
        if not isinstance(segment, dict):
            raise PreviewError("invalid_segment")
        ident = segment.get("segment_id", "")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", ident) or ident in ids:
            raise PreviewError("invalid_or_duplicate_segment_id")
        # This is deliberately not an approved-label manifest.
        if set(segment) != {"segment_id", "start_seconds", "end_seconds"}:
            raise PreviewError("unexpected_segment_fields")
        start, end = pp.validate_window(segment)
        ids.add(ident)
        intervals.append((start, end))
    ordered = sorted(intervals)
    if any(b[0] < a[1] for a, b in zip(ordered, ordered[1:])):
        raise PreviewError("overlapping_segments")


def peak_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def verify_movie(path, mapping, width, height, deadline):
    """Read back every preview frame and check its presentation-time mapping."""
    import av
    seen = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.codec_context.name != "h264" or container.streams.audio:
            raise PreviewError("unexpected_preview_streams")
        stream.codec_context.thread_count = 1
        for frame in container.decode(stream):
            if time.monotonic() > deadline or len(seen) >= MAX_ENCODED:
                raise PreviewError("verification_budget_exceeded")
            if frame.is_corrupt or frame.pts is None or frame.time_base is None:
                raise PreviewError("invalid_preview_frame")
            if (frame.width, frame.height) != (width, height):
                raise PreviewError("preview_size_mismatch")
            seen.append(float(frame.pts * frame.time_base))
    if len(seen) != len(mapping) or not seen:
        raise PreviewError("preview_frame_count_mismatch")
    errors = [abs(actual - row["preview_seconds"]) for actual, row in zip(seen, mapping)]
    if max(errors) > float(PREVIEW_TIME_BASE) * 1.1 or any(b <= a for a, b in zip(seen, seen[1:])):
        raise PreviewError("preview_timing_mismatch")
    return {"decoded_frames": len(seen), "strictly_increasing": True,
            "max_pts_error_seconds": max(errors), "codec": "h264",
            "verified_by": "PyAV_full_preview_readback", "user_player_verified": False}


def create_preview(source: Path, segment, output: Path):
    """Stream only a short segment; hold at most 16 RGB images for the sheet."""
    import av
    start, end = pp.validate_window(segment)
    # Decimal seconds must not be compared with binary floats: 1/5 < float(0.2).
    # Source PTS already uses exact rational arithmetic.
    start_time, end_time = Fraction(str(start)), Fraction(str(end))
    started = time.monotonic()
    deadline = started + WORK_SECONDS
    output.mkdir(parents=True, exist_ok=False)
    movie = output / "review.mp4"
    mapping, samples = [], []
    ticks = [start + (i + 0.5) * (end - start) / 16 for i in range(16)]
    decoded = packets = 0
    first = last = None
    with av.open(str(source), mode="r") as source_container:
        if not source_container.streams.video:
            raise PreviewError("no_video_stream")
        stream = source_container.streams.video[0]
        stream.codec_context.thread_count = 1
        if stream.time_base is None or stream.duration is None or stream.duration <= 0:
            raise PreviewError("source_duration_unavailable")
        duration = float(stream.duration * stream.time_base)
        pp.validate_window(segment, duration)
        rate = stream.average_rate
        if rate is None or not 0 < float(rate) <= 60:
            raise PreviewError("unsupported_source_rate")
        width, height = stream.codec_context.width, stream.codec_context.height
        if min(width, height) <= 0 or width % 2 or height % 2 or width * height > 1920 * 1080:
            raise PreviewError("unsupported_source_dimensions")
        origin = (stream.start_time or 0) * stream.time_base
        source_info = {"duration_seconds": duration, "average_fps": str(rate),
                       "time_base": str(stream.time_base), "origin_seconds": float(origin),
                       "width": width, "height": height, "codec": stream.codec_context.name}
        source_container.seek(int(start_time / stream.time_base) + (stream.start_time or 0),
                              stream=stream, backward=True, any_frame=False)
        with av.open(str(movie), mode="w", format="mp4", options={"movflags": "+faststart"}) as dest:
            encoder = dest.add_stream("libx264", rate=rate)
            encoder.width, encoder.height, encoder.pix_fmt = width, height, "yuv420p"
            encoder.time_base = PREVIEW_TIME_BASE
            encoder.codec_context.time_base = PREVIEW_TIME_BASE
            encoder.codec_context.thread_count = 1
            encoder.options = {"crf": "18", "preset": "veryfast", "bf": "0"}
            done = False
            for packet in source_container.demux(stream):
                packets += 1
                if packets > MAX_PACKETS or time.monotonic() > deadline:
                    raise PreviewError("decode_budget_exceeded")
                for frame in packet.decode():
                    decoded += 1
                    if decoded > MAX_DECODED or time.monotonic() > deadline:
                        raise PreviewError("decode_budget_exceeded")
                    if frame.is_corrupt or frame.pts is None or frame.time_base is None:
                        raise PreviewError("invalid_source_frame")
                    actual = frame.pts * frame.time_base - origin
                    if last is not None and actual <= last:
                        raise PreviewError("nonincreasing_source_timestamps")
                    last = actual
                    if actual < start_time:
                        continue
                    if actual >= end_time:
                        done = True
                        break
                    if len(mapping) >= MAX_ENCODED:
                        raise PreviewError("preview_frame_budget_exceeded")
                    if first is None:
                        first = actual
                    pts = round((actual - first) / PREVIEW_TIME_BASE)
                    row = {"source_pts": frame.pts, "source_time_base": str(frame.time_base),
                           "source_seconds": float(actual), "preview_pts": pts,
                           "preview_time_base": str(PREVIEW_TIME_BASE),
                           "preview_seconds": float(pts * PREVIEW_TIME_BASE)}
                    if mapping and pts <= mapping[-1]["preview_pts"]:
                        raise PreviewError("unrepresentable_preview_timing")
                    mapping.append(row)
                    if len(samples) < 16 and float(actual) >= ticks[len(samples)]:
                        samples.append({"requested_seconds": ticks[len(samples)],
                                        "actual_seconds": float(actual), "pts": frame.pts,
                                        "pts_time_base": str(frame.time_base), "image": frame.to_image()})
                    encoded = frame.reformat(format="yuv420p")
                    encoded.pts, encoded.time_base = pts, PREVIEW_TIME_BASE
                    for encoded_packet in encoder.encode(encoded):
                        dest.mux(encoded_packet)
                if done:
                    break
            if len(mapping) < 2:
                raise PreviewError("insufficient_segment_frames")
            for encoded_packet in encoder.encode():
                dest.mux(encoded_packet)
    # A truncated input must not silently produce a shorter review interval.
    if mapping[-1]["source_seconds"] + 1 / float(rate) < end - 1e-6:
        raise PreviewError("segment_ends_before_requested_end")
    if not samples:
        raise PreviewError("no_contact_sheet_frames")
    verification = verify_movie(movie, mapping, width, height, deadline)
    sheet = pp.save_frames(output / "stills", samples, 360, None, segment["segment_id"])
    result = {"segment_id": segment["segment_id"], "status": "preview_ready_unreviewed",
              "start_seconds": start, "end_seconds": end, "interval": "[start,end)",
              "time_basis": "local_file_pts_seconds", "source_media": source_info,
              "preview_zero_source_seconds": float(first), "frame_mapping": mapping,
              "decoded_frames_including_preroll": decoded, "packets_read": packets,
              "encoded_frames": len(mapping), "saved_stills": len(samples),
              "preview_bytes": movie.stat().st_size, "preview_sha256": pp.sha256(movie),
              "preview_verification": verification, "stills": sheet,
              "elapsed_seconds": time.monotonic() - started,
              "worker_peak_rss_bytes": peak_rss_bytes(), "execution_device": "cpu",
              "label": None, "target": None, "human_review_status": "unreviewed",
              "training_authorized": False, "official_split": None, "event_id": None,
              "model_input_written": False, "audio_included": False,
              "transform": "native_dimensions_no_crop_no_retiming_h264_lossy_review_only"}
    pp.write_json(output / "segment.json", result)
    return result
