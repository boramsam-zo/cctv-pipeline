"""Bounded CPU H.264 browser copies of verified B full/ROI MPEG4 clips.

These display derivatives never replace evidence, become Gemini input, or upload
anywhere. The worker calls prepare after evidence verification. GET/UI rendering
only reads an already verified preview. No model libraries are imported.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time

SCHEMA = 'cctv-browser-preview-v1'
RECIPE = {'source_codec':'mpeg4', 'output_codec':'h264', 'encoder':'libx264',
          'pixel_format':'yuv420p', 'crf':'23', 'preset':'fast', 'thread_count':1,
          'audio_included':False, 'movflags':'+faststart'}
MAX_BYTES = 200 * 1024 * 1024


def _require(value, message):
    if not value:
        raise ValueError('browser_preview_' + message)


def _sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def _file(path):
    _require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_BYTES,
             'missing_or_invalid_file')
    return path


def _arguments(source, source_sha256, expected_frames, fps):
    _require(isinstance(source_sha256,str) and re.fullmatch('[a-f0-9]{64}',source_sha256),'invalid_source_hash')
    _require(type(expected_frames) is int and 1 <= expected_frames <= 120000,'invalid_frame_count')
    _require(type(fps) in (int,float) and math.isfinite(fps) and .001 <= fps <= 1000,'invalid_fps')
    _require(expected_frames/fps <= 120,'duration_limit')
    path=_file(Path(source))
    _require(_sha(path)==source_sha256,'source_integrity')
    return path


def _parse(path):
    _file(path)
    _require(path.stat().st_size <= 64*1024,'receipt_too_large')
    def pairs(items):
        data={}
        for key,value in items:
            _require(key not in data,'duplicate_receipt_key')
            data[key]=value
        return data
    def invalid(value):
        raise ValueError('browser_preview_invalid_receipt')
    data=json.loads(path.read_text(),object_pairs_hook=pairs,parse_constant=invalid)
    json.dumps(data,allow_nan=False)
    return data


def _metadata(data, expected_frames, fps, codec):
    _require(isinstance(data,dict) and data.get('codec')==codec
             and type(data.get('decoded_frames')) is int and data['decoded_frames']==expected_frames
             and data.get('pts_verified') is True,'metadata_mismatch')
    _require(type(data.get('fps')) in (int,float) and math.isfinite(data['fps'])
             and abs(data['fps']-fps)<=1e-6,'metadata_fps')
    for key in ('width','height'):
        _require(type(data.get(key)) is int and 2 <= data[key] <= 8192 and data[key]%2==0,'metadata_resolution')
    _require(type(data.get('duration_s')) in (int,float) and math.isfinite(data['duration_s'])
             and abs(data['duration_s']-expected_frames/fps)<=1e-6,'metadata_duration')


def validate(source, destination, *, source_sha256, expected_frames, fps):
    """Hash-check cached original/preview and its recorded decode verification.

    Cache validation does not run a subprocess or decode media again. Altered or
    partial output is rejected; callers must never silently overwrite/retry it.
    """
    source=_arguments(source,source_sha256,expected_frames,fps)
    target=Path(destination)
    _require(target.is_dir() and not target.is_symlink(),'missing_or_invalid_directory')
    receipt=_parse(target/'receipt.json')
    _require(receipt.get('schema_version')==SCHEMA and receipt.get('recipe')==RECIPE
             and receipt.get('source_sha256')==source_sha256
             and receipt.get('source_bytes')==source.stat().st_size
             and receipt.get('output_file')=='preview.mp4'
             and receipt.get('evidence_replaced') is False
             and receipt.get('model_calls')==0,'receipt_identity')
    preview=_file(target/'preview.mp4')
    _require(receipt.get('output_bytes')==preview.stat().st_size
             and receipt.get('output_sha256')==_sha(preview),'output_integrity')
    _metadata(receipt['source_metadata'],expected_frames,fps,'mpeg4')
    _metadata(receipt['output_metadata'],expected_frames,fps,'h264')
    _require(all(receipt['source_metadata'][key]==receipt['output_metadata'][key]
                 for key in ('width','height','decoded_frames','fps','duration_s')),'output_dimensions')
    _require(set(p.name for p in target.iterdir())=={'preview.mp4','receipt.json'},'unexpected_files')
    return receipt


def prepare(source, destination, *, source_sha256, expected_frames, fps,
            timeout_seconds=45, python_executable=None):
    """Create once, or verify the cache. Failed partial output is preserved."""
    source=_arguments(source,source_sha256,expected_frames,fps)
    _require(type(timeout_seconds) in (int,float) and math.isfinite(timeout_seconds)
             and 1 <= timeout_seconds <= 120,'invalid_timeout')
    target=Path(destination)
    if target.exists() or target.is_symlink():
        return validate(source,target,source_sha256=source_sha256,expected_frames=expected_frames,fps=fps)
    _require(target.resolve()!=source.parent.resolve()
             and not source.resolve().is_relative_to(target.resolve()),'destination_contains_source')
    target.mkdir(parents=True,exist_ok=False)
    command=[str(python_executable or sys.executable),'-B',str(Path(__file__).resolve()),'--encode']
    payload={'source':str(source.resolve()),'output':str((target/'preview.mp4').resolve()),
             'frames':expected_frames,'fps':fps}
    started=time.monotonic()
    try:
        completed=subprocess.run(command,input=json.dumps(payload),text=True,capture_output=True,
                                 timeout=timeout_seconds,check=False)
    except subprocess.TimeoutExpired:
        raise ValueError('browser_preview_timeout_partial_preserved') from None
    except OSError:
        raise ValueError('browser_preview_python_unavailable') from None
    _require(completed.returncode==0,'encoding_failed_partial_preserved')
    try:
        metadata=json.loads(completed.stdout)
        _metadata(metadata['source'],expected_frames,fps,'mpeg4')
        _metadata(metadata['output'],expected_frames,fps,'h264')
    except (ValueError,KeyError,TypeError):
        raise ValueError('browser_preview_subprocess_metadata_invalid') from None
    _require(_sha(source)==source_sha256,'source_changed_during_encoding')
    output=_file(target/'preview.mp4')
    receipt={'schema_version':SCHEMA,'recipe':dict(RECIPE),'source_sha256':source_sha256,
             'source_bytes':source.stat().st_size,'source_metadata':metadata['source'],
             'output_file':'preview.mp4','output_sha256':_sha(output),'output_bytes':output.stat().st_size,
             'output_metadata':metadata['output'],'elapsed_seconds':round(time.monotonic()-started,6),
             'evidence_replaced':False,'model_calls':0}
    with (target/'receipt.json').open('x') as stream:
        json.dump(receipt,stream,indent=2,allow_nan=False)
    return validate(source,target,source_sha256=source_sha256,expected_frames=expected_frames,fps=fps)


def _inspect(path, expected_frames, fps, expected_codec):
    import av
    origin=None
    with av.open(str(path)) as source:
        _require(len(source.streams.video)==1 and not source.streams.audio,'stream_layout')
        stream=source.streams.video[0]
        _require(stream.codec_context.name==expected_codec,'codec_mismatch')
        count=0
        for frame in source.decode(video=0):
            _require(count<expected_frames,'too_many_frames')
            _require(frame.time is not None and math.isfinite(frame.time),'missing_pts')
            if origin is None: origin=frame.time
            _require(abs(frame.time-origin-count/fps)<=min(.002,.05/fps),'pts_mismatch')
            _require(frame.width==stream.width and frame.height==stream.height,'resolution_changed')
            count+=1
        data={'codec':stream.codec_context.name,'decoded_frames':count,'fps':fps,
              'duration_s':count/fps,'width':stream.width,'height':stream.height,'pts_verified':True}
    _metadata(data,expected_frames,fps,expected_codec)
    return data


def _encode(payload):
    # Import only in a child bounded by prepare's timeout, never in API/UI loads.
    import av
    from fractions import Fraction
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,(110,115))
        resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_BYTES,MAX_BYTES))
        resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
    except (ImportError,ValueError,OSError):
        raise ValueError('browser_preview_resource_limits_unavailable') from None
    source_path,output_path=Path(payload['source']),Path(payload['output'])
    expected_frames,fps=payload['frames'],payload['fps']
    _require(not output_path.exists(),'output_exists')
    rate=Fraction(str(fps)).limit_denominator(100000)
    count=0;origin=None
    with av.open(str(source_path)) as source, av.open(str(output_path),'w',
            options={'movflags':'+faststart'}) as destination:
        _require(len(source.streams.video)==1 and not source.streams.audio,'stream_layout')
        original=source.streams.video[0]
        _require(original.codec_context.name=='mpeg4','source_codec')
        width,height=original.width,original.height
        _require(2 <= width <= 8192 and 2 <= height <= 8192 and width%2==0 and height%2==0,
                 'source_resolution')
        original.codec_context.thread_count=1
        stream=destination.add_stream('libx264',rate=rate)
        stream.width,stream.height,stream.pix_fmt=width,height,'yuv420p'
        stream.codec_context.thread_count=1
        stream.options={'crf':'23','preset':'fast','threads':'1'}
        for frame in source.decode(video=0):
            _require(count<expected_frames,'too_many_frames')
            _require(frame.width==width and frame.height==height,'resolution_changed')
            _require(frame.time is not None and math.isfinite(frame.time),'missing_pts')
            if origin is None: origin=frame.time
            _require(abs(frame.time-origin-count/fps)<=min(.002,.05/fps),'pts_mismatch')
            picture=frame.reformat(format='yuv420p')
            picture.pts,picture.time_base=count,1/rate
            for packet in stream.encode(picture):destination.mux(packet)
            count+=1
        for packet in stream.encode():destination.mux(packet)
    source_metadata={'codec':'mpeg4','decoded_frames':count,'fps':fps,'duration_s':count/fps,
                     'width':width,'height':height,'pts_verified':True}
    _metadata(source_metadata,expected_frames,fps,'mpeg4')
    output_metadata=_inspect(output_path,expected_frames,fps,'h264')
    return {'source':source_metadata,'output':output_metadata}


if __name__=='__main__':
    if sys.argv[1:]!=['--encode']:
        raise SystemExit('browser_preview must be called through prepare')
    print(json.dumps(_encode(json.loads(sys.stdin.read())),allow_nan=False))
