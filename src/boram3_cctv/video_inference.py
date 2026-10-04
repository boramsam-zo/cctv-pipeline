"""Bounded offline, causal windows. This module never reads annotation files."""
from __future__ import annotations

from collections import deque
import math
from pathlib import Path
import time

import numpy as np

from . import preprocessing as pp


class InferenceError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise InferenceError(reason)


class WindowBuffer:
    """Decisions at d use only frames in [d-window, d), never the lookahead frame."""

    def __init__(self, window_seconds, stride_seconds, count=16, max_frames=512):
        require(all(math.isfinite(x) and x > 0 for x in (window_seconds, stride_seconds)), "invalid_window_or_stride")
        require(stride_seconds <= window_seconds and 2 <= count <= max_frames, "invalid_buffer_configuration")
        self.window, self.stride, self.count, self.max_frames = window_seconds, stride_seconds, count, max_frames
        self.next_decision, self.last_time = window_seconds, None
        self.frames = deque()
        self.peak_frames = 0

    def push(self, record):
        t = record["actual_seconds"]
        require(math.isfinite(t) and t >= 0, "invalid_source_timestamp")
        require(self.last_time is None or t > self.last_time, "nonincreasing_source_timestamps")
        require(t < self.next_decision, "advance_decisions_before_adding_future_frame")
        require(len(self.frames) < self.max_frames, "buffer_capacity_exceeded")
        self.frames.append(record)
        self.last_time = t
        self.peak_frames = max(self.peak_frames, len(self.frames))

    def advance(self, observed_time):
        while self.next_decision <= observed_time + 1e-9:
            decision = self.next_decision
            start = decision - self.window
            available = [r for r in self.frames if start <= r["actual_seconds"] < decision]
            requested = [start + (i + .5) * self.window / self.count for i in range(self.count)]
            selected = [min(available, key=lambda r: (abs(r["actual_seconds"] - t), r["actual_seconds"]))
                        for t in requested] if available else []
            distinct = len({r["actual_seconds"] for r in selected})
            # Reject sparse/gapped windows instead of stretching or duplicating images.
            max_distance = max((abs(t-r["actual_seconds"]) for t,r in zip(requested,selected)), default=math.inf)
            status = "ready" if distinct == self.count and max_distance <= self.window/self.count else "insufficient_frames_or_time_gap"
            self.next_decision += self.stride
            while self.frames and self.frames[0]["actual_seconds"] < self.next_decision - self.window:
                self.frames.popleft()
            yield {"start_seconds": start, "end_seconds": decision,
                   "decision_source_time_sec": decision, "status": status,
                   "frames": [{**r, "requested_seconds": t} for r,t in zip(selected,requested)] if status == "ready" else []}


def sequential_windows(video_path, window_seconds, stride_seconds, max_video_seconds,
                       stats, deadline, count=16, image_size=224, max_frames=512, clock=time.perf_counter):
    """Open once, decode once; retain only a bounded queue of resized RGB frames."""
    import av
    require(Path(video_path).is_file(), "video_file_missing")
    require(0 < max_video_seconds <= 60, "video_range_exceeds_60_seconds")
    buffer = WindowBuffer(window_seconds, stride_seconds, count, max_frames)
    stats.update(decode_seconds=0.0, preprocess_seconds=0.0, decoded_frames=0, preprocessed_frames=0,
                 stream_open_count=0, first_input_seconds=None, last_input_seconds=None,
                 decode_lookahead_seconds=None, buffer_peak_frames=0, buffer_limit_frames=max_frames,
                 buffer_limit_pixel_bytes=max_frames*image_size*image_size*3, full_frames_saved=False)
    first, last, last_duration = None, None, 0.0
    began = clock()
    with av.open(str(video_path), mode="r") as container:
        stats["stream_open_count"] += 1
        require(bool(container.streams.video), "no_video_stream")
        stream = container.streams.video[0]
        require(stream.time_base is not None, "time_base_missing_no_fps_guess")
        origin = float((stream.start_time or 0) * stream.time_base)
        duration = float(stream.duration * stream.time_base) if stream.duration else None
        end = min(max_video_seconds, duration) if duration and duration > 0 else max_video_seconds
        stats.update(source_fps=float(stream.average_rate) if stream.average_rate else None,
                     source_width=stream.codec_context.width, source_height=stream.codec_context.height,
                     codec=stream.codec_context.name, source_start_pts_seconds=origin,
                     header_duration_seconds=duration, requested_range_seconds=[0.0,max_video_seconds],
                     effective_end_limit_seconds=end, timestamp_method="PTS*time_base - stream_start; no CFR fallback")
        stats["decode_seconds"] += clock()-began
        decoder = iter(container.decode(stream))
        reached_end = False
        while True:
            if clock() >= deadline:
                raise TimeoutError("inference_loop_time_limit")
            began = clock()
            try:
                frame = next(decoder)
            except StopIteration:
                break
            stats["decode_seconds"] += clock()-began
            stats["decoded_frames"] += 1
            require(stats["decoded_frames"] <= 20000, "decoded_frame_safety_limit")
            require(not frame.is_corrupt and frame.pts is not None and frame.time_base is not None,
                    "invalid_decoded_frame_or_timestamp")
            t = float(frame.pts * frame.time_base)-origin
            require(math.isfinite(t) and t >= 0 and (last is None or t > last), "invalid_source_timestamp_order")
            for window in buffer.advance(min(t, end)):
                yield window
            if t >= end:
                stats["decode_lookahead_seconds"] = t
                reached_end = True
                break  # Boundary lookahead is neither resized nor used by the model.
            began = clock()
            image, _ = pp.letterbox(frame.to_image(), image_size)
            pixel = np.asarray(image, dtype=np.uint8)
            buffer.push({"actual_seconds": t, "pts": frame.pts, "pts_time_base": str(frame.time_base), "pixels": pixel})
            stats["preprocess_seconds"] += clock()-began
            stats["preprocessed_frames"] += 1
            first = t if first is None else first
            last = t
            last_duration = float((getattr(frame, "duration", 0) or 0) * frame.time_base)
            stats.update(first_input_seconds=first, last_input_seconds=last,
                         buffer_peak_frames=buffer.peak_frames,
                         buffer_peak_pixel_bytes=buffer.peak_frames*image_size*image_size*3)
        require(last is not None, "no_decoded_frames")
        coverage_end = end if reached_end else min(end, last+last_duration if last_duration > 0 else last)
        for window in buffer.advance(coverage_end):
            yield window
        stats.update(observed_coverage_end_seconds=coverage_end,
                     warmup_range_seconds=[0.0,min(window_seconds,coverage_end)],
                     tail_without_new_decision_seconds=[max(0.0,buffer.next_decision-stride_seconds),coverage_end]
                     if coverage_end >= window_seconds else [0.0,coverage_end],
                     buffer_peak_frames=buffer.peak_frames)


def save_preview(path, window, prediction_id):
    from PIL import Image, ImageDraw
    size = window["frames"][0]["pixels"].shape[0]
    sheet = Image.new("RGB", (size*4, 36+(size+24)*4), "#18212b")
    draw = ImageDraw.Draw(sheet)
    draw.text((8,8), f"{prediction_id} | unlabelled sequential input | left-to-right, top-to-bottom", fill="white")
    for i, record in enumerate(window["frames"]):
        x, y = i%4*size, 36+i//4*(size+24)
        sheet.paste(Image.fromarray(record["pixels"]), (x,y))
        draw.text((x+4,y+size+3), f"{i:02d} {record['actual_seconds']:.6f}s", fill="white")
    sheet.save(path)


def summarize_logged_losses(phases, sample_ids, batch_size):
    """For the verified unweighted, non-ignored, mean-reduced CE training run."""
    rows = [{**r,"phase":phase} for phase, records in phases for r in records]
    steps = [r["step"] for r in rows]
    require(len(steps)==len(set(steps)), "duplicate_global_step")
    require(steps==sorted(steps) and all(s>0 for s in steps), "invalid_global_step_order")
    batches = math.ceil(len(sample_ids)/batch_size)
    for r in rows:
        require(math.isfinite(r["loss"]) and r["loss"]>=0 and 0<len(r["sample_ids"])<=batch_size, "invalid_loss_record")
    epochs = []
    for epoch in range(1, max(steps,default=0)//batches+2):
        part = [r for r in rows if (r["step"]-1)//batches+1==epoch]
        if not part:
            continue
        seen = [sid for r in part for sid in r["sample_ids"]]
        complete = seen==sample_ids and [r["step"] for r in part]==list(range((epoch-1)*batches+1,epoch*batches+1))
        numerator = sum(r["loss"]*len(r["sample_ids"]) for r in part)
        epochs.append({"epoch":epoch,"complete":complete,"steps":[r["step"] for r in part],
                       "sample_count":len(seen),"numerator":numerator,"denominator":len(seen),
                       "observed_mean":numerator/len(seen),"epoch_mean":numerator/len(seen) if complete else None})
    total = sum(len(r["sample_ids"]) for r in rows)
    return {"rows":rows,"epochs":epochs,"observed_sample_visits":total,
            "observed_run_mean":sum(r["loss"]*len(r["sample_ids"]) for r in rows)/total if total else None,
            "meaning":"changing-model batch CE, sample-weighted; repeated training visits, not fixed-model evaluation"}


def fixed_saved_losses(rows):
    """Recompute unweighted CE from already saved logits on CPU; zero model forwards."""
    import torch
    require(bool(rows), 'empty_fixed_predictions')
    require(all(type(r['truth']) is int and r['truth'] in (0,1) for r in rows),'unapproved_or_invalid_truth')
    logits=torch.tensor([r['logits'] for r in rows],dtype=torch.float32)
    require(list(logits.shape)==[len(rows),2] and torch.isfinite(logits).all().item(),'invalid_saved_logits')
    target=torch.tensor([r['truth'] for r in rows],dtype=torch.long)
    with torch.inference_mode():
        losses=torch.nn.functional.cross_entropy(logits,target,weight=None,ignore_index=-100,
                                                reduction='none',label_smoothing=0.0)
    values=[float(value) for value in losses]
    return {'rows':[{**row,'loss':value} for row,value in zip(rows,values)],
            'numerator':sum(values),'denominator':len(values),'mean_loss':sum(values)/len(values),
            'basis':'CPU cross_entropy from existing saved float32 logits; no new model forward',
            'independent_validation':False}
