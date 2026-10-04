"""One offline video, <=60 source seconds, <=300 worker seconds; no annotation input."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from boram3_cctv import preprocessing as pp
from boram3_cctv.video_inference import InferenceError, require, sequential_windows, save_preview


def write(path, value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def worker(args):
    import torch
    import numpy as np
    from torchvision.models.video import r3d_18
    from boram3_cctv import training as tr
    # Refuse before entering the error writer, so even an internal retry cannot overwrite results.
    require(not (args.output/'result.json').exists(),'existing_results_protected')
    started=time.perf_counter()
    result={"status":"running","mode":"offline_sequential","scope":getattr(args,"source_scope","training_source_functional_check_not_independent_validation"),
            "source_video_id":args.video.stem,"requested_video_seconds":args.seconds,
            "training_updates":0,"prediction_count":0,"unclassified_window_count":0,"unlabelled_windows":True,"independent_validation":False,
            "timing":{},"video":{},"memory_samples":[],"user_review_status":"pending"}
    output=args.output
    try:
        require(args.video.is_file(),"video_file_missing")
        require(args.model.is_file(),"model_file_missing")
        stat=args.video.stat()
        model_hash=pp.sha256(args.model)
        total_windows=max(1,int((args.seconds-args.window_seconds)/args.stride_seconds)+1)
        preview_slots={1,(total_windows+1)//2,total_windows} if getattr(args,'preview_schedule','first')=='start-middle-end' else {1,2,3}
        config={"window_seconds":args.window_seconds,"stride_seconds":args.stride_seconds,"source_range_seconds":[0,args.seconds],
                "max_loop_seconds":300,"max_buffer_frames":512,"max_preview_count":3,
                "preview_selection":sorted(preview_slots),
                "decision_rule":"argmax logits; tie -> normal 0; unchanged from saved-input review",
                "gap_rule":"require16 distinct PTS and distance to each bin centre <= one bin width",
                "model_sha256":model_hash,"device":args.device}
        write(output/'config.json',config)
        torch.set_num_threads(4)
        device=tr.select_device(args.device)
        began=time.perf_counter()
        state=torch.load(args.model,map_location='cpu',weights_only=True)
        require(state.get('model_name')=='r3d_18' and state.get('class_mapping')=={'normal':0,'accident':1},"incompatible_model_or_classes")
        settings=state['config']['preprocessing']
        require(settings=={"version":"r3d-letterbox-kinetics-v1","frame_count":16,"image_size":224,
                          "sampling":"nearest_PTS_to_uniform_bin_centres","spatial":"letterbox_no_crop",
                          "normalization":"KINETICS400_V1","layout":"CTHW","dtype":"float32"},"incompatible_preprocessing")
        model=r3d_18(weights=None,num_classes=2)
        model.load_state_dict(state['model_state'],strict=True)
        model.requires_grad_(False).eval().to(device)
        tr.sync(device)
        result['timing']['model_load_seconds']=time.perf_counter()-began
        result.update(actual_device=str(device),model_sha256=model_hash,class_mapping=state['class_mapping'],preprocessing=settings,
                      model_sample_rate_hz=16/args.window_seconds,model_call_interval_seconds=args.stride_seconds,
                      model_origin="existing trained checkpoint; no downloads",warmup_forward_calls=0)
        # Keep the saved state on CPU for an exact post-inference state comparison.
        loop_start=time.perf_counter()
        infer_times=[]
        preview_seconds=0.0
        log_seconds=0.0
        scheduled=0
        deadline=loop_start+300
        with (output/'predictions.jsonl').open('x') as log, torch.inference_mode():
            for window in sequential_windows(args.video,args.window_seconds,args.stride_seconds,args.seconds,result['video'],deadline):
                scheduled+=1
                base={k:v for k,v in window.items() if k!='frames'}
                base.update(window_id=f'window_{scheduled:03d}',source_video_id=args.video.stem,truth=None,loss=None,correct=None,
                            frame_count=len(window['frames']),model_sha256=model_hash)
                memory_stop=False
                if time.perf_counter()>=deadline:
                    raise TimeoutError('inference_loop_time_limit')
                if window['status']=='ready':
                    began=time.perf_counter()
                    array=np.stack([f['pixels'] for f in window['frames']])
                    inputs=tr.tensor_from_array(array,settings).unsqueeze(0).to(device)
                    logits=model(inputs)
                    tr.sync(device)
                    require(torch.isfinite(logits).all().item() and list(logits.shape)==[1,2],"nonfinite_or_invalid_model_output")
                    scores=torch.softmax(logits,dim=1)[0].cpu().tolist()
                    infer_times.append(time.perf_counter()-began)
                    prediction=int(logits.argmax(dim=1).item())
                    result['prediction_count']+=1
                    pid=f'prediction_{result["prediction_count"]:03d}'
                    base.update(status='predicted',prediction_id=pid,prediction=prediction,accident_score=scores[1],
                                logits=logits[0].cpu().tolist(),decision_rule=config['decision_rule'],
                                model_input_shape=list(inputs.shape),model_input_dtype=str(inputs.dtype),
                                inference_seconds=infer_times[-1],elapsed_worker_seconds=time.perf_counter()-started,
                                frames=[{k:v for k,v in f.items() if k!='pixels'} for f in window['frames']])
                    if scheduled in preview_slots:
                        began=time.perf_counter()
                        path=output/f'{pid}.png'
                        save_preview(path,window,pid)
                        base['preview']=path.name
                        preview_seconds+=time.perf_counter()-began
                    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    memory={"rss_peak_bytes":int(rss if sys.platform=='darwin' else rss*1024)}
                    if device.type=='mps':
                        memory.update(mps_driver_bytes=torch.mps.driver_allocated_memory(),mps_tensor_bytes=torch.mps.current_allocated_memory())
                    if device.type=='cuda':
                        memory.update(cuda_allocated_bytes=torch.cuda.max_memory_allocated(device),cuda_reserved_bytes=torch.cuda.max_memory_reserved(device))
                    result['memory_samples'].append(memory)
                    memory_stop=memory['rss_peak_bytes']>8*1024**3 or memory.get('mps_driver_bytes',0)>8*1024**3 or memory.get('cuda_reserved_bytes',0)>8*1024**3
                else:
                    result['unclassified_window_count']+=1
                began=time.perf_counter()
                log.write(json.dumps(base,ensure_ascii=False,allow_nan=False)+'\n');log.flush()
                log_seconds+=time.perf_counter()-began
                result['timing'].update(loop_seconds=time.perf_counter()-loop_start,inference_seconds=sum(infer_times),
                                       prediction_calls_seconds=infer_times,preview_seconds=preview_seconds,log_write_seconds=log_seconds)
                write(output/'result.json',result)
                require(not memory_stop,'memory_limit')
        result['timing']['loop_seconds']=time.perf_counter()-loop_start
        result['status']='completed' if result['prediction_count'] else 'completed_no_predictions'
        if result['unclassified_window_count']:
            result['status']='completed_with_unclassified_windows'
        result['weights_and_buffers_unchanged']=all(torch.equal(v.cpu(),state['model_state'][k]) for k,v in model.state_dict().items())
        require(result['weights_and_buffers_unchanged'],'model_state_changed')
        require(pp.sha256(args.model)==model_hash,'saved_model_changed')
        current=args.video.stat()
        require((current.st_size,current.st_mtime_ns)==(stat.st_size,stat.st_mtime_ns),'source_file_changed')
        result['source_stat_unchanged']=True
        result['source_file_size_bytes']=stat.st_size
        coverage=result['video']['observed_coverage_end_seconds']
        result['timing']['rtf_loop_over_source_coverage']=result['timing']['loop_seconds']/coverage if coverage else None
        result['timing']['worker_seconds']=time.perf_counter()-started
        result['timing']['rtf_worker_over_source_coverage']=result['timing']['worker_seconds']/coverage if coverage else None
        result['output_bytes_before_final_result']=sum(p.stat().st_size for p in output.iterdir() if p.is_file())
    except Exception as error:
        result.update(status='partial' if result['prediction_count'] else 'failed',error_type=type(error).__name__,
                      error=str(error).replace(str(ROOT),'<project>').replace(str(Path.home()),'<home>'))
        result['timing']['worker_seconds']=time.perf_counter()-started
    write(output/'result.json',result)
    print(json.dumps({k:result[k] for k in ('status','prediction_count','timing')},ensure_ascii=False))
    return 0 if result['status'].startswith('completed') else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--window-seconds',type=float,required=True)
    parser.add_argument('--stride-seconds',type=float,default=1.0)
    parser.add_argument('--seconds',type=float,default=60)
    parser.add_argument('--device',choices=['cpu','mps','cuda'],default='mps')
    parser.add_argument('--preview-schedule',choices=['first','start-middle-end'],default='first')
    parser.add_argument('--source-scope',choices=['training_source_functional_check_not_independent_validation','development_seen_source_not_independent_validation'],default='training_source_functional_check_not_independent_validation')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    require(0<args.seconds<=60 and args.window_seconds in (2,6) and 0<args.stride_seconds<=args.window_seconds,'invalid_execution_bounds')
    for path in (args.video,args.model):
        require(not args.output.resolve().is_relative_to(path.resolve()) and not path.resolve().is_relative_to(args.output.resolve()),'output_overlaps_input')
    if args.worker:
        return worker(args)
    args.output.mkdir(parents=True,exist_ok=False)
    argv=[sys.executable,str(Path(__file__).resolve()),*sys.argv[1:],'--worker']
    began=time.perf_counter()
    with (args.output/'worker.stdout.log').open('x') as stdout,(args.output/'worker.stderr.log').open('x') as stderr:
        try:
            done=subprocess.run(argv,stdout=stdout,stderr=stderr,timeout=300,check=False)
            code=done.returncode
        except subprocess.TimeoutExpired:
            code=124
            result=pp.read_json(args.output/'result.json') if (args.output/'result.json').is_file() else {}
            result.update(status='partial_timeout' if result.get('prediction_count') else 'failed_timeout',error='outer_300_second_limit',user_review_status='pending')
            write(args.output/'result.json',result)
    write(args.output/'command.json',{'argv':[str(x).replace(str(ROOT),'<project>').replace(str(Path.home()),'<home>') for x in argv],
                                     'exit_code':code,'wall_seconds':time.perf_counter()-began,'worker_timeout_seconds':300})
    return code


if __name__=='__main__':
    raise SystemExit(main())
