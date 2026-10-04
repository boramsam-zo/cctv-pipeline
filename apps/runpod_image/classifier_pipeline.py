"""R2 classifier with the existing B detector, evidence, and handoff workflow.

This module never changes the frozen X3D implementation. Its R2 time sampling
matches the training payload: full 2-second windows, stride 1, nearest PTS to bin
centres, no future/boundary frame, and no stretched short or trailing window.
"""
from collections import deque
import json
import math
from pathlib import Path
import time

from apps.runpod_image.classifier_adapter import letterbox


def window_starts(duration):
    if not math.isfinite(duration) or duration < 2:
        raise ValueError('r2_requires_complete_two_second_window')
    return [float(i) for i in range(math.floor(duration - 2 + 1e-9) + 1)]


class WindowBuffer:
    def __init__(self):
        self.frames, self.decision, self.last = deque(), 2.0, None

    def push(self, timestamp, pixels):
        if (not math.isfinite(timestamp) or timestamp < 0 or timestamp >= self.decision
                or (self.last is not None and timestamp <= self.last) or len(self.frames) >= 256):
            raise ValueError('r2_frame_time_or_buffer_invalid')
        self.frames.append((timestamp, pixels))
        self.last = timestamp

    def advance(self, timestamp):
        while self.decision <= timestamp + 1e-9:
            start = self.decision - 2
            available = [(t, p) for t, p in self.frames if start <= t < self.decision]
            targets = [start + (i + .5) * 2 / 16 for i in range(16)]
            if not available:
                raise ValueError('r2_window_has_no_frames')
            selected = [min(available, key=lambda row: (abs(row[0] - t), row[0])) for t in targets]
            if (len({t for t, _ in selected}) != 16
                    or max(abs(t - row[0]) for t, row in zip(targets, selected)) > 2 / 16):
                raise ValueError('r2_insufficient_frames_or_time_gap')
            yield start, self.decision, selected, targets
            self.decision += 1
            while self.frames and self.frames[0][0] < self.decision - 2:
                self.frames.popleft()


def windows(path, checked):
    import av
    import numpy as np
    window_starts(checked['duration_s'])
    buffer, count, last, last_duration = WindowBuffer(), 0, None, 0.0
    with av.open(str(path)) as source:
        stream = source.streams.video[0]
        origin = float((stream.start_time or 0) * stream.time_base)
        for frame in source.decode(stream):
            if frame.is_corrupt or frame.pts is None or frame.time_base is None:
                raise ValueError('r2_corrupt_frame_or_missing_pts')
            timestamp = float(frame.pts * frame.time_base) - origin
            if not math.isfinite(timestamp) or timestamp < 0 or (last is not None and timestamp <= last):
                raise ValueError('r2_timestamp_order_invalid')
            for start, end, selected, targets in buffer.advance(timestamp):
                yield {'start_s': start, 'end_s': end,
                       'sample_times_s': [row[0] for row in selected],
                       'requested_sample_times_s': targets}, np.stack([row[1] for row in selected])
            buffer.push(timestamp, np.asarray(letterbox(frame.to_image()), dtype=np.uint8))
            count += 1
            if count > checked['decoded_frames']:
                raise ValueError('r2_unexpected_decoded_frame_count')
            last, last_duration = timestamp, float((frame.duration or 0) * frame.time_base)
    observed = last + last_duration if last is not None else 0
    if count != checked['decoded_frames'] or last_duration <= 0 or abs(observed - checked['duration_s']) > .1:
        raise ValueError('r2_incomplete_video_decode')
    for start, end, selected, targets in buffer.advance(min(observed, checked['duration_s'])):
        yield {'start_s': start, 'end_s': end,
               'sample_times_s': [row[0] for row in selected],
               'requested_sample_times_s': targets}, np.stack([row[1] for row in selected])


def fixed_b_detector(pipeline, checkpoint):
    """Reuse B's exact object-detection method without loading its X3D model."""
    from ultralytics import YOLO
    detector = object.__new__(pipeline.ModelBundle)
    detector.device = 'cuda'
    detector.yolo = YOLO(str(checkpoint))
    if detector.yolo.task != 'detect':
        raise ValueError('b_yolo_detection_model_required')
    sidecar = Path(checkpoint).with_name('yolo11s_so_tad_985_no_class4_960_b16_classes.json')
    names = json.loads(sidecar.read_text())['class_names']
    detector.yolo_names = {int(k): str(v) for k, v in names.items()}
    return detector


def analyze_video(video_path, output_dir, classifier, detector, *, checked, contract,
                  context_builder, group_alerts, batch_size=8, window_source=windows):
    """Run classifier windows and B evidence creation; no VLM/API call occurs."""
    if type(batch_size) is not int or not 1 <= batch_size <= 8:
        raise ValueError('r2_batch_size_invalid')
    begin = time.perf_counter()
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True, parents=True)
    rows, arrays, bounds = [], [], []

    def flush():
        predictions = classifier.predict(arrays)
        if len(predictions) != len(bounds):
            raise ValueError('r2_window_prediction_count')
        rows.extend(dict(bound, **prediction) for bound, prediction in zip(bounds, predictions))
        bounds.clear()
        arrays.clear()

    for bound, array in window_source(video_path, checked):
        arrays.append(array)
        bounds.append(bound)
        if len(arrays) == batch_size:
            flush()
    if arrays:
        flush()
    if [row['start_s'] for row in rows] != window_starts(checked['duration_s']):
        raise ValueError('r2_window_coverage_mismatch')
    # B's group function uses >= .5. Remove binary ties/negative windows first
    # to retain the candidate's strict argmax rule while reusing event grouping.
    events = group_alerts([row for row in rows if row['is_positive']])
    handoffs = []
    for index, event in enumerate(events):
        event_id = f'event_{index:03d}'
        event['event_id'] = event_id
        context = context_builder(Path(video_path), event['candidate_time_s'], output_dir / event_id,
                                  detector, event_id=event_id,
                                  event_range_s=[event['start_s'], event['end_s']])
        # Only the explanatory label is adapted; media, objects, geometry and
        # candidate timestamps are produced by the unchanged B context builder.
        if context.get('display_frame_reason') == 'x3d_candidate_time':
            context['display_frame_reason'] = 'classifier_candidate_time'
            context_path = output_dir / event_id / (event_id + '_context.json')
            # The builder just created this job-local document; its retained
            # raw file and handoff must contain exactly the same metadata.
            if context_path.is_symlink() or not context_path.is_file():
                raise ValueError('r2_context_file_missing')
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2,
                                               allow_nan=False) + '\n')
        event['incident_context'] = context
        handoffs.append({'schema_version': '2.0', 'event_id': event_id,
            'video_id': Path(video_path).name, 'classifier_collision_candidate': True,
            'classifier': contract, 'candidate_time_s': event['candidate_time_s'],
            'available_after_video_time_s': event['available_after_video_time_s'],
            'candidate_window_s': [event['first_window_start_s'], event['first_window_end_s']],
            'classifier_probability_at_trigger': event['first_window_probability'],
            'yolo_role': 'multi_frame_object_and_participant_candidate_evidence',
            'incident_context': context,
            'server_action': 'Send full clip, ROI clip, annotated frame, and context JSON to VLM',
            'note': 'Candidate time is the first positive classifier window midpoint, not a verified impact timestamp.'})
    result = {'schema_version': 'cctv-classifier-result-v1', 'video_filename': Path(video_path).name,
        'duration_s': checked['duration_s'], 'fps': checked['fps'], 'device': 'cuda',
        'classifier': {**contract, 'label_mapping': {'0': 'Normal', '1': 'Collision'},
            'threshold': .5, 'window_seconds': 2.0, 'stride_seconds': 1.0,
            'decision': 'Collision' if events else 'Normal',
            'max_collision_probability': max(row['collision_probability'] for row in rows), 'windows': rows},
        'events': events, 'vlm_handoffs': handoffs, 'processing_seconds': time.perf_counter() - begin,
        'warning': 'Candidate times are not verified crash times; no_candidates does not establish normality.'}
    for name, value in (('result.json', result), ('vlm_handoffs.json', handoffs)):
        with (output_dir / name).open('x', encoding='utf-8') as target:
            json.dump(value, target, ensure_ascii=False, allow_nan=False, indent=2)
            target.write('\n')
    return result
