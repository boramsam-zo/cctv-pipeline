"""Extract an explicit small SO-TAD selection; preserve archives and past receipts."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--record',type=Path,required=True)
    parser.add_argument('--members',nargs='+',required=True)
    args=parser.parse_args()
    if not 1<=len(args.members)<=10 or len(set(args.members))!=len(args.members):
        raise ValueError('Select 1 to 10 distinct members')
    if args.output.exists() or args.record.exists():
        raise FileExistsError('Use new output and receipt folders; originals are preserved')
    original=json.loads((ROOT/'artifacts/data-review/so-tad-intake-20260926/inventory.json').read_text())
    selected={x['name']:x for x in original['entries'] if x['name'] in args.members}
    if set(selected)!=set(args.members) or any(not n.startswith('train/') or not n.endswith('.mp4') for n in selected):
        raise ValueError('Only explicit known train MP4 members are allowed')
    if any(x['bytes']>20_000_000 for x in selected.values()) or sum(x['bytes'] for x in selected.values())>150_000_000:
        raise ValueError('Selection exceeds bounded extraction size')
    args.record.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('sotad_split_reader',ROOT/'artifacts/data-review/so-tad-intake-20260926/inspect_samples.py')
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    reader.SOURCE=args.source;reader.RECORD=args.record;reader.SELECTED=args.members
    parts,infos=reader.index()  # inventory is written only into the new receipt folder
    actual=json.loads((args.record/'inventory.json').read_text())
    if actual['central_directory_sha256']!=original['central_directory_sha256']:
        raise ValueError('Archive catalog changed; stop before extraction')
    before=[(p.stat().st_size,p.stat().st_mtime_ns) for p in parts]
    args.output.mkdir(parents=True,exist_ok=False)
    import av
    rows=[];started=time.monotonic()
    for name in args.members:
        data=reader.member_bytes(parts,infos[name])  # CRC, length, path and bounded decompression
        target=args.output/name.replace('/','_')
        with target.open('xb') as stream:stream.write(data)
        sha=hashlib.sha256(data).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:raise ValueError('Output copy mismatch')
        count=0;last=None;first=None
        with av.open(str(target)) as container:
            video=container.streams.video[0]
            duration=float(video.duration*video.time_base) if video.duration is not None else None
            codec=video.codec_context.name;width=video.width;height=video.height
            for frame in container.decode(video=0):
                if frame.pts is None:raise ValueError('Missing frame timestamp')
                pts=float(frame.pts*frame.time_base)
                if last is not None and pts<=last:raise ValueError('Non-increasing frame timestamp')
                if first is None:first=pts
                last=pts;count+=1
        if count==0 or duration is None or duration>120:raise ValueError('Invalid/over-limit video')
        row=dict(archive_member=name,output=str(target),bytes=len(data),sha256=sha,
                 crc32_verified=True,full_decode_verified=True,frames=count,duration_seconds=duration,
                 width=width,height=height,codec=codec,first_pts=first,last_pts=last,
                 label='not_assigned_for_this_ui_trial',purpose='ui_pipeline_trial_candidate')
        rows.append(row)
        (args.record/'extraction.json').write_text(json.dumps({'status':'in_progress','files':rows},ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'file':target.name,'seconds':duration,'frames':count,'verified':True}),flush=True)
    after=[(p.stat().st_size,p.stat().st_mtime_ns) for p in parts]
    if before!=after:raise ValueError('Source archive stat changed')
    result=dict(status='completed',files=rows,total_bytes=sum(r['bytes'] for r in rows),
                elapsed_seconds=time.monotonic()-started,archive_size_mtime_unchanged=True,
                python_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                reader_script_sha256=hashlib.sha256(Path(reader.__file__).read_bytes()).hexdigest(),
                av_version=av.__version__,model_calls=0,uploads=0,
                excluded_previous_previews=['train/100.mp4','train/1000.mp4'],
                prior_training_exposure=True,independent_evaluation=False)
    (args.record/'extraction.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'completed','files':len(rows),'total_bytes':result['total_bytes'],'model_calls':0},ensure_ascii=False))


if __name__=='__main__':main()
