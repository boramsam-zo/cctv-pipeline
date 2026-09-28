"""CPU evidence export for the existing CFR whole-file detector (no model imports)."""
import json
import math
from pathlib import Path
import re
from fractions import Fraction

from verify_bundle import sha256


def probe(path):
    import av
    with av.open(str(path)) as source:
        return [{'best_effort_timestamp_time': frame.time} for frame in source.decode(video=0)]


def encode(video, clip, image, record, fps):
    import av
    rate = Fraction(str(fps)).limit_denominator(100000)
    with av.open(str(video)) as source, av.open(str(clip), 'w',
            options={'movflags': '+faststart'}) as target:
        stream = target.add_stream('libx264', rate=rate)
        stream.pix_fmt = 'yuv420p'
        stream.options = {'crf': '23', 'preset': 'fast'}
        stream.codec_context.thread_count = 2
        written = 0
        for index, frame in enumerate(source.decode(video=0)):
            if index >= record['end_frame_exclusive']:
                break
            if index == record['representative_frame']:
                with av.open(str(image), 'w', format='image2') as still:
                    png = still.add_stream('png', rate=1)
                    png.width, png.height, png.pix_fmt = frame.width, frame.height, 'rgb24'
                    picture = frame.reformat(format='rgb24')
                    picture.pts, picture.time_base = 0, Fraction(1, 1)
                    for packet in png.encode(picture):
                        still.mux(packet)
                    for packet in png.encode():
                        still.mux(packet)
            if index < record['start_frame']:
                continue
            if frame.width % 2 or frame.height % 2:
                raise ValueError('Odd source dimensions unsupported by this H.264 evidence profile')
            stream.width, stream.height = frame.width, frame.height
            converted = frame.reformat(format='yuv420p')
            converted.pts, converted.time_base = written, 1/rate
            for packet in stream.encode(converted):
                target.mux(packet)
            written += 1
        for packet in stream.encode():
            target.mux(packet)
    if written != record['end_frame_exclusive'] - record['start_frame']:
        raise ValueError('Incomplete evidence decode')


def verify_timeline(frames, fps, count):
    if not math.isfinite(fps) or fps <= 0 or len(frames) != count or count < 2:
        raise ValueError('Evidence timeline/frame count mismatch')
    times = [float(f['best_effort_timestamp_time']) for f in frames]
    origin = times[0]
    tolerance = min(0.002, 0.05 / fps)
    if any(not math.isfinite(t) or abs(t - origin - i/fps) > tolerance
           for i, t in enumerate(times)):
        raise ValueError('VFR/discontinuous PTS unsupported by the current FPS-based detector')
    return origin


def plan(handoffs, fps, count, pre=3.0, post=3.0):
    if not all(math.isfinite(x) and 0 <= x <= 10 for x in (pre, post)) or pre + post <= 0:
        raise ValueError('Context must be finite, 0..10 seconds per side, nonempty')
    if len(handoffs) > 60:
        raise ValueError('Too many evidence events')
    seen, records = set(), []
    for event in handoffs:
        name, t = event['event_id'], float(event['candidate_time_s'])
        if not re.fullmatch(r'event_[0-9]+', name) or name in seen:
            raise ValueError('Unsafe or duplicate event ID')
        seen.add(name)
        if not math.isfinite(t) or not 0 <= t < count/fps:
            raise ValueError('Candidate outside source video')
        start = max(0, math.floor((t-pre)*fps + 1e-7))
        end = min(count, math.ceil((t+post)*fps - 1e-7))
        representative = min(count-1, round(t*fps))
        if not start <= representative < end:
            raise ValueError('Representative frame outside context')
        records.append(dict(event_id=name, candidate_time_s=t,
            source_start_s=start/fps, source_end_s=end/fps,
            start_frame=start, end_frame_exclusive=end, representative_frame=representative,
            representative_source_time_s=representative/fps,
            candidate_clip_time_s=t-start/fps,
            requested_pre_s=pre, requested_post_s=post,
            pre_context_truncated=t-pre < 0, post_context_truncated=t+post > count/fps,
            clip=f'evidence/{name}/clip.mp4', frame=f'evidence/{name}/frame.png',
            yolo_objects=event['yolo_objects']))
    return records


def create(video, output, checked, pre=3.0, post=3.0):
    output = Path(output)
    handoffs = json.loads((output/'vlm_handoffs.json').read_text())
    fps, count = checked['fps'], checked['decoded_frames']
    origin = verify_timeline(probe(video), fps, count)
    records = plan(handoffs, fps, count, pre, post)
    for record in records:
        clip, frame = output/record['clip'], output/record['frame']
        clip.parent.mkdir(parents=True, exist_ok=True)
        encode(video, clip, frame, record, fps)
        clip_count = record['end_frame_exclusive'] - record['start_frame']
        verify_timeline(probe(clip), fps, clip_count)
        if not frame.is_file() or frame.stat().st_size == 0:
            raise ValueError('Missing representative PNG')
        record['clip_frame_count'] = clip_count
    document = dict(schema_version='runpod-evidence-v1', events=records,
        source_pts_origin_s=origin, time_basis='seconds_from_first_decoded_frame',
        pts_verified=True, source_fps=fps, audio_included=False,
        note='Whole-file analysis; candidate midpoint is not verified impact time. Original images have no boxes.')
    (output/'evidence.json').write_text(json.dumps(document, indent=2))
    return document


def file_record(path):
    return dict(bytes=path.stat().st_size, sha256=sha256(path))


def write_manifest(output, source_sha):
    output = Path(output)
    document = json.loads((output/'evidence.json').read_text())
    names = ['result.json','vlm_handoffs.json','runtime.json','evidence.json']
    for event in document['events']:
        names.extend([event['clip'],event['frame']])
    (output/'manifest.json').write_text(json.dumps(dict(
        schema_version='runpod-evidence-v1', input_sha256=source_sha,
        files={name:file_record(output/name) for name in names},
        storage='temporary_container_disk'), indent=2))


if __name__ == '__main__':
    # Reuse received inference JSON: never load a model or modify original outputs.
    import argparse
    import os
    import shutil
    import time
    from storage import verified_files
    parser = argparse.ArgumentParser(description='Create evidence from an existing verified result, no GPU inference')
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--input-results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = verified_files(args.input_results)
    source_sha = sha256(args.video)
    if source_sha != manifest['input_sha256']:
        raise ValueError('Video SHA differs from the original inference input')
    runtime = json.loads((args.input_results/'runtime.json').read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    for name in ('result.json','vlm_handoffs.json'):
        shutil.copyfile(args.input_results/name,args.output/name)
    started = time.perf_counter()
    document = create(args.video,args.output,runtime['video_check'],
        float(os.environ.get('EVIDENCE_PRE_SECONDS','3')),
        float(os.environ.get('EVIDENCE_POST_SECONDS','3')))
    runtime.update(evidence_media_created=bool(document['events']),
        evidence_event_count=len(document['events']), evidence_pts_verified=True,
        evidence_seconds=time.perf_counter()-started, s3_uploaded=False,
        storage='temporary_container_disk',
        evidence_generation='postprocessed_existing_inference_no_model_execution')
    (args.output/'runtime.json').write_text(json.dumps(runtime,indent=2))
    write_manifest(args.output,source_sha)
    print(json.dumps({'evidence_events':len(document['events']),'output':str(args.output),
                      'new_model_inference':False}))
