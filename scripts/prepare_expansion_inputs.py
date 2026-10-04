"""Bounded input preparation only; shares the existing video/image preprocessing."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from boram3_cctv import preprocessing as pp


def validate(request, receipt):
    expected = request['accident_candidates'] + request['normal_candidates']
    if (request.get('purpose') != 'colab_expansion_file_check_only' or
        request.get('settings') != {'seconds': 2, 'frames': 16, 'image_size': 224, 'fps_detrac': 25} or
        not 1 <= len(expected) <= 400 or len(receipt['records']) != len(expected)):
        raise pp.PreprocessingError('invalid_request_or_receipt')
    seen = set()
    for c, r in zip(expected, receipt['records']):
        key = (c.get('source_path', c.get('sequence_id')), c['start_seconds'], c['end_seconds'])
        if key in seen:
            raise pp.PreprocessingError('duplicate_segment')
        seen.add(key)
        if 'source_path' in c:
            if c.get('target') != 1 or c.get('label_origin') != 'source_annotation_derived' or c['source_path'] in request.get('reserved_accident_paths', []):
                raise pp.PreprocessingError('invalid_accident_policy')
        elif c.get('target') != 0 or c.get('label_origin') != 'user_assumed_normal' or c['sequence_id'] in request.get('reserved_normal_sequences', []) + request.get('official_normal_test', []):
            raise pp.PreprocessingError('invalid_normal_policy')
        if any(r.get(k) != v for k, v in c.items()) or r.get('status') != 'file_connected_not_decoded':
            raise pp.PreprocessingError('receipt_mismatch')
        pp.validate_window(c)
        if not math.isclose(c['end_seconds'] - c['start_seconds'], 2.0, rel_tol=0.0, abs_tol=1e-9):
            raise pp.PreprocessingError('expected_two_seconds')
    return expected


def prepare_one(c, videos, archive, output):
    if 'source_path' in c:
        rel = PurePosixPath(c['source_path'])
        if len(rel.parts) != 2 or rel.parts[0] != 'real_videos' or rel.suffix != '.mp4':
            raise pp.PreprocessingError('invalid_video_path')
        path = videos / rel.name
        if path.is_symlink() or not path.is_file():
            raise pp.PreprocessingError('missing_video_or_symlink')
        before = (path.stat().st_size, path.stat().st_mtime_ns)
        frames, info = pp.sample_frames(path, c['start_seconds'], c['end_seconds'], 16)
        if before != (path.stat().st_size, path.stat().st_mtime_ns):
            raise pp.PreprocessingError('source_changed_during_read')
    else:
        sequence, start = c['sequence_id'], c['start_seconds']
        import re
        if not re.fullmatch(r'MVI_\d+', sequence) or type(start) is not int or start not in (0,2,4,6):
            raise pp.PreprocessingError('invalid_normal_window')
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive) as z:
            root = Path(tmp); (root / sequence).mkdir()
            for n in range(start * 25 + 1, (start + 2) * 25 + 1):
                name = f'{sequence}/img{n:05d}.jpg'
                member = z.getinfo('DETRAC-Images/' + name)
                if member.file_size > 16*1024*1024 or member.flag_bits & 1 or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise pp.PreprocessingError('unsafe_zip_member')
                # Only selected 50 names, never the full archive.
                (root / name).write_bytes(z.read(member))
            frames, info = pp.sample_detrac_images(root, sequence, start)
    details = pp.save_frames(output, frames, 224, c['target'], output.name,
                            label_caption=(c['label_origin'].upper() + (' - INPUT QUALITY PENDING'
                                           if c['label_origin'] == 'user_segment_review'
                                           else ' - NOT HUMAN REVIEW')),
                            save_unlabeled_input=c.get('split') == 'validation_reserved')
    if not details['strictly_increasing_timestamps']:
        raise pp.PreprocessingError('nonincreasing_timestamps')
    return {'status': 'prepared_not_split', 'input_path': str(output.name + '/frames.npy'),
            'preview_path': str(output.name + '/preview.png'),
            'frames_sha256': pp.sha256(output / 'frames.npy'), 'video_info': info,
            'input': details}


def validate_reserved(request):
    import re
    rows = request.get('records', [])
    if request.get('status') != 'reserved_candidates_connected_not_evaluation_ready' or not 1 <= len(rows) <= 10:
        raise pp.PreprocessingError('invalid_validation_reservations')
    seen = set()
    result = []
    for row in rows:
        c = dict(row)
        sid = c.get('sample_id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', sid) or sid in seen:
            raise pp.PreprocessingError('invalid_reserved_id')
        seen.add(sid)
        pp.validate_window(c)
        if (not math.isclose(c['end_seconds']-c['start_seconds'], 2., rel_tol=0., abs_tol=1e-9)
                or c.get('training_authorized') is not False or c.get('evaluation_authorized') is not False
                or c.get('split') != 'validation_reserved'):
            raise pp.PreprocessingError('invalid_reserved_scope')
        if c.get('requested_class') == 'accident':
            if c.get('target') is not None or c.get('official_iid_split') != 'train':
                raise pp.PreprocessingError('unapproved_accident_or_official_test')
        elif c.get('requested_class') == 'normal':
            if c.get('target') != 0 or c.get('label_origin') != 'user_assumed_normal' or c['start_seconds'] != 0:
                raise pp.PreprocessingError('invalid_reserved_normal')
            c['start_seconds'], c['end_seconds'] = 0, 2
        else:
            raise pp.PreprocessingError('invalid_requested_class')
        result.append(c)
    return result


def load_previous(previous_output, records, expected_sha256):
    """Verify provenance before reuse; never decode or rewrite previous inputs."""
    manifest_path = previous_output / 'manifest.json'
    if pp.sha256(manifest_path) != expected_sha256:
        raise pp.PreprocessingError('previous_manifest_hash_mismatch')
    previous = pp.read_json(manifest_path)['records']
    if len(previous) != len(records):
        raise pp.PreprocessingError('previous_record_count_mismatch')
    for index, (c, r) in enumerate(zip(records, previous)):
        if r.get('sample_id') != f'expansion_{index:04d}' or any(
                r.get(k) != c.get(k) for k in ('source_path','sequence_id','start_seconds','end_seconds','label_origin')):
            raise pp.PreprocessingError('previous_record_mismatch')
        if r.get('declared_target') != c['target'] or r.get('target') != (c['target'] if r.get('status') == 'prepared_not_split' else None):
            raise pp.PreprocessingError('previous_target_mismatch')
        if r['status'] not in ('prepared_not_split', 'error', 'not_run'):
            raise pp.PreprocessingError('invalid_previous_status')
        if r['status'] == 'prepared_not_split':
            for key in ('input_path', 'preview_path'):
                relative = Path(r[key])
                path = previous_output / relative
                if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(previous_output.resolve()) or not path.is_file():
                    raise pp.PreprocessingError('previous_input_missing_or_unsafe')
            if pp.sha256(previous_output / r['input_path']) != r['frames_sha256']:
                raise pp.PreprocessingError('previous_array_hash_mismatch')
    return previous


def recover_reserved_previews(previous_output, output, manifest_sha256):
    """Recover unlabeled arrays from already saved PNGs; never reopen source videos."""
    import shutil
    import numpy as np
    from PIL import Image
    previous_output, output = Path(previous_output), Path(output)
    if output.exists() or output.resolve().is_relative_to(previous_output.resolve()) or previous_output.resolve().is_relative_to(output.resolve()):
        raise pp.PreprocessingError('recovery_output_overlaps_or_exists')
    if pp.sha256(previous_output/'manifest.json') != manifest_sha256:
        raise pp.PreprocessingError('previous_manifest_hash_mismatch')
    old = pp.read_json(previous_output/'manifest.json')
    if old.get('purpose') != 'reserved_validation_input_preparation':
        raise pp.PreprocessingError('wrong_recovery_purpose')
    candidates = validate_reserved(dict(status='reserved_candidates_connected_not_evaluation_ready', records=old['records']))
    output.mkdir(parents=True)
    rows = []
    started = time.monotonic()
    for candidate, row in zip(candidates, old['records']):
        sid = candidate['sample_id']; source = previous_output/sid; dest = output/sid
        result = dict(row)
        try:
            if time.monotonic()-started > 110:
                raise pp.PreprocessingError('recovery_time_limit')
            if source.is_symlink():raise pp.PreprocessingError('unsafe_preview_directory')
            details = pp.read_json(source/'frames.json')
            times = [f['actual_seconds'] for f in details['frames']]
            if (len(times) != 16 or details['array_shape'] != [16,224,224,3]
                    or details.get('target') != candidate['target']
                    or any(not math.isfinite(t) or not candidate['start_seconds'] <= t < candidate['end_seconds'] for t in times)
                    or any(b <= a for a,b in zip(times,times[1:]))):
                raise pp.PreprocessingError('saved_preview_timing_or_label_mismatch')
            names = ['preview.png','source_reference.png','frames.json'] + [f'frames/{i:03d}.png' for i in range(16)]
            arrays = []
            for name in names:
                p=source/name
                if p.is_symlink() or not p.resolve().is_relative_to(previous_output.resolve()) or not p.is_file():
                    raise pp.PreprocessingError('saved_preview_missing_or_unsafe')
                if name.startswith('frames/'):
                    with Image.open(p) as im:
                        if im.mode != 'RGB' or im.size != (224,224):raise pp.PreprocessingError('saved_preview_shape')
                        arrays.append(np.array(im))
            dest.mkdir()
            for name in names:
                target=dest/name;target.parent.mkdir(exist_ok=True);shutil.copyfile(source/name,target)
            array=np.stack(arrays)
            if row['status'] == 'prepared_not_split':
                if pp.sha256(source/'frames.npy') != row['frames_sha256']:
                    raise pp.PreprocessingError('previous_array_hash_mismatch')
                if not np.array_equal(np.load(source/'frames.npy',allow_pickle=False),array):
                    raise pp.PreprocessingError('previous_array_preview_mismatch')
                shutil.copyfile(source/'frames.npy',dest/'frames.npy')
                origin='existing_array_verified_reused'
            elif row['status']=='error' and row.get('reason')=='FileNotFoundError' and candidate['target'] is None:
                np.save(dest/'frames.npy',array,allow_pickle=False)
                details['model_input_written']=True
                (dest/'frames.json').write_text(json.dumps(details,ensure_ascii=False,indent=2))
                origin='reconstructed_from_saved_processed_pngs_not_redecoded'
            else:
                raise pp.PreprocessingError('unsupported_recovery_status')
            result.update(status='prepared_not_split',reason=None,input_path=f'{sid}/frames.npy',
                          preview_path=f'{sid}/preview.png',frames_sha256=pp.sha256(dest/'frames.npy'),
                          input=details,input_origin=origin,target=candidate['target'])
        except (OSError,ValueError,KeyError,pp.PreprocessingError) as exc:
            result.update(status='error',target=None,reason=str(exc) if isinstance(exc,pp.PreprocessingError) else type(exc).__name__)
        rows.append(result)
        print(f'{len(rows)}/{len(candidates)} {sid}: {result["status"]}',flush=True)
    manifest=dict(old,records=rows,counts=dict(Counter(r['status'] for r in rows)),
                  previous_output=str(previous_output),recovery_seconds=time.monotonic()-started,
                  video_decode_run=False,model_run=False,training_run=False)
    pp.write_json(output/'manifest.json',manifest)
    if (previous_output/'report.html').is_file():
        (output/'report.html').write_text('<meta charset="utf-8"><h1>복구 결과</h1><p>실패 여부는 manifest.json을 확인하세요. 기존 PNG 재사용, 영상 재디코딩 없음.</p>'+''.join(
            f'<p>{r["sample_id"]}: {r["status"]} '+(f'<a href="{r["sample_id"]}/preview.png">미리보기</a>' if r['status']=='prepared_not_split' else '')+'</p>' for r in rows))
    return manifest


def run(request, receipt, videos, archive, output, max_seconds=600, *,
        previous_output=None, previous_sha256=None, validation_reservations=False):
    if not 0 < max_seconds <= 600:
        raise pp.PreprocessingError('invalid_time_limit')
    if validation_reservations and (max_seconds > 300 or previous_output is not None):
        raise pp.PreprocessingError('reserved_preparation_scope_exceeded')
    records = validate_reserved(request) if validation_reservations else validate(request, receipt)
    output, videos, archive = Path(output), Path(videos), Path(archive)
    if output.exists() or output.resolve().is_relative_to(videos.resolve()) or videos.resolve().is_relative_to(output.resolve()) or archive.resolve().is_relative_to(output.resolve()):
        raise pp.PreprocessingError('output_exists_or_overlaps_input')
    started = time.monotonic()
    previous = None
    if previous_output is not None:
        previous_output = Path(previous_output)
        if output.resolve().is_relative_to(previous_output.resolve()) or previous_output.resolve().is_relative_to(output.resolve()):
            raise pp.PreprocessingError('output_overlaps_previous')
        previous = load_previous(previous_output, records, previous_sha256)
    output.mkdir(parents=True)
    results = []; attempted = 0; reused = 0; preserved_errors = 0
    for index, c in enumerate(records):
        remaining = max_seconds - (time.monotonic() - started)
        base = dict(c, sample_id=c['sample_id'] if validation_reservations else f'expansion_{index:04d}', declared_target=c['target'],
                    target=None, split='validation_reserved' if validation_reservations else 'unassigned', training_authorized=False, evaluation_authorized=False)
        prior = previous[index] if previous else None
        if prior and (prior['status'] == 'prepared_not_split' or 'source_path' in c):
            result = dict(prior, input_origin='previous', input_base=str(previous_output), attempted_this_run=False)
            if prior['status'] == 'prepared_not_split': reused += 1
            else: preserved_errors += 1
        elif remaining <= 0:
            result = dict(status='not_run', reason='total_time_limit')
        else:
            attempted += 1
            try:
                payload = dict(candidate=c, videos=str(videos), archive=str(archive),
                               output=str(output / base['sample_id']))
                child = subprocess.run([sys.executable, __file__, '--worker'], input=json.dumps(payload),
                                       capture_output=True, text=True, timeout=min(45, remaining))
                result = json.loads(child.stdout)
                if child.returncode or result.get('status') not in ('prepared_not_split', 'error'):
                    raise ValueError('worker_failed')
            except subprocess.TimeoutExpired:
                result = dict(status='error', reason='worker_timeout')
            except (ValueError, OSError):
                result = dict(status='error', reason='worker_failed')
        if result['status'] == 'prepared_not_split': base['target'] = c['target']
        result.setdefault('input_origin', 'current')
        result.setdefault('attempted_this_run', remaining > 0)
        results.append(dict(base, **result))
        manifest = dict(purpose='reserved_validation_input_preparation' if validation_reservations else 'expanded_inputs_not_training', records=results,
                        counts=dict(Counter(r['status'] for r in results)), expected_count=len(records),
                        elapsed_seconds=time.monotonic()-started, max_seconds=max_seconds,
                        attempted_this_run=attempted, reused_success_count=reused,
                        preserved_accident_error_count=preserved_errors,
                        previous_output=str(previous_output) if previous_output else None,
                        model_run=False, training_run=False, independent_validation=False)
        # This file belongs only to this new run. Atomic updates retain timeout evidence.
        temp = output / 'manifest.partial.json'; temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        temp.replace(output / 'manifest.json')
        print(f'{index+1}/{len(records)} {base["sample_id"]}: {result["status"]}', flush=True)
    import html
    rows = []
    for r in results:
        link = ('<a href="' + html.escape(r['preview_path'], quote=True) + '">16프레임 미리보기</a>') if r['status'] == 'prepared_not_split' else html.escape(r.get('reason', ''))
        if r.get('input_origin') == 'previous' and r['status'] == 'prepared_not_split':
            link = '기존 결과 재사용: ' + html.escape(str(previous_output / r['preview_path']))
        rows.append('<tr>' + ''.join('<td>' + html.escape(str(r.get(k, ''))) + '</td>' for k in
                    ('sample_id', 'source_path', 'sequence_id', 'start_seconds', 'end_seconds', 'target', 'label_origin', 'status')) + '<td>' + link + '</td></tr>')
    (output / 'report.html').write_text('<!doctype html><meta charset="utf-8"><title>400구간 입력 생성</title><h1>입력 생성 결과 — 학습·검증 미실행</h1><p>정상은 사용자 가정, 사고는 원본 시점 파생. 독립 사건·카메라 분할 미확정. 미리보기는 왼쪽→오른쪽, 위→아래 순서.</p><table border="1"><tr><th>ID</th><th>영상</th><th>시퀀스</th><th>시작</th><th>끝</th><th>정답</th><th>근거</th><th>상태</th><th>확인</th></tr>' + ''.join(rows) + '</table>')
    if validation_reservations:
        p = output / 'report.html'
        p.write_text(p.read_text().replace('400구간 입력 생성', '검증 예약 입력 미리보기').replace(
            '정상은 사용자 가정, 사고는 원본 시점 파생.', '정상은 사용자 가정. 사고 구간은 원본 시점으로 선택했지만 검증 정답은 미확정입니다.'))
    return manifest


if __name__ == '__main__':
    if '--worker' in sys.argv:
        try:
            p = json.load(sys.stdin)
            result = prepare_one(p['candidate'], Path(p['videos']), Path(p['archive']), Path(p['output']))
        except Exception as exc:
            result = {'status': 'error', 'reason': str(exc) if isinstance(exc, pp.PreprocessingError) else type(exc).__name__}
        print(json.dumps(result))
    else:
        parser = argparse.ArgumentParser()
        for flag in ('request','videos','archive','output'): parser.add_argument('--'+flag, required=True, type=Path)
        parser.add_argument('--receipt', type=Path)
        parser.add_argument('--validation-reservations', action='store_true')
        parser.add_argument('--previous-output', type=Path)
        parser.add_argument('--previous-sha256')
        args = parser.parse_args()
        run(pp.read_json(args.request), pp.read_json(args.receipt) if args.receipt else {}, args.videos, args.archive, args.output,
            max_seconds=290 if args.validation_reservations else 590,
            previous_output=args.previous_output, previous_sha256=args.previous_sha256,
            validation_reservations=args.validation_reservations)


def run_reference_benchmark(root, videos, archive, output):
    """Reuse ten saved predictions; prepare/predict only twenty remaining reservations."""
    import shutil
    import html
    from boram3_cctv import evaluation as ev
    root,output=Path(root),Path(output)
    plan=pp.read_json(root/'benchmark-plan.json')
    rows=plan['records']; previous=pp.read_json(root/'existing-predictions.json')
    if len(rows)!=30 or len(previous)!=10 or len({r['sample_id'] for r in rows})!=30:
        raise pp.PreprocessingError('fixed_benchmark_counts_required')
    done={r['sample_id'] for r in previous};remaining=[r for r in rows if r['sample_id'] not in done]
    if len(remaining)!=20:raise pp.PreprocessingError('benchmark_reuse_mismatch')
    if pp.sha256(root/'checkpoint.pt')!=plan['model_sha256']:raise pp.PreprocessingError('benchmark_model_changed')
    output.mkdir(parents=True,exist_ok=False)
    results=list(previous);stages=[];prep_spent=0.;inference_spent=0.
    for batch_index in range(2):
        batch=remaining[batch_index*10:(batch_index+1)*10]
        request=dict(status='reserved_candidates_connected_not_evaluation_ready',records=batch)
        directory=output/f'batch-{batch_index+1}'
        budget=290-prep_spent
        if budget<=0:
            results.extend(dict(sample_id=r['sample_id'],status='not_run',reason='preparation_time_limit') for r in batch)
            continue
        start=time.monotonic()
        prepared=run(request,None,videos,archive,directory,max_seconds=budget,validation_reservations=True)
        prep_spent+=time.monotonic()-start
        good=[r for r in prepared['records'] if r['status']=='prepared_not_split']
        for r in prepared['records']:
            if r['status']!='prepared_not_split':results.append(dict(sample_id=r['sample_id'],status='failed',reason=r.get('reason','input_failed')))
        if not good:continue
        good_ids={r['sample_id'] for r in good}
        # Original full manifest remains; a separate folder is used for prediction binding.
        runtime=output/f'runtime-{batch_index+1}';runtime.mkdir()
        for r in good:
            shutil.copytree(directory/r['sample_id'],runtime/r['sample_id'])
        for name in ('checkpoint.pt','prepared_inputs.json','training-input-proposal.json'):
            shutil.copyfile(root/name,runtime/name)
        pp.write_json(runtime/'manifest.json',dict(prepared,records=good))
        pp.write_json(runtime/'reservations.json',dict(request,records=[r for r in batch if r['sample_id'] in good_ids]))
        binding={'model_manifest':{k:{'path':n,'sha256':pp.sha256(runtime/n)} for k,n in
                 [('model','checkpoint.pt'),('training_inputs','prepared_inputs.json'),('training_proposal','training-input-proposal.json')]},
                 'prepared_sha256':pp.sha256(runtime/'manifest.json'),'reservations_sha256':pp.sha256(runtime/'reservations.json')}
        pp.write_json(runtime/'binding.json',binding)
        budget=300-inference_spent
        predictions=output/f'predictions-{batch_index+1}'
        start=time.monotonic()
        timed_out=False
        if budget>0:
            worker="import sys;from pathlib import Path;sys.path.insert(0,str(Path(sys.argv[1])/'src'));from boram3_cctv.evaluation import reserved_prediction_worker;reserved_prediction_worker(sys.argv[2],sys.argv[3])"
            with (output/f'worker-{batch_index+1}.log').open('w') as log:
                try:
                    p=subprocess.run([sys.executable,'-c',worker,str(root),str(runtime),str(predictions)],stdout=log,stderr=subprocess.STDOUT,timeout=budget)
                    rc=p.returncode
                except subprocess.TimeoutExpired:timed_out=True;rc=None
        else:timed_out=True;rc=None
        inference_spent+=time.monotonic()-start
        parsed=[]
        if (predictions/'predictions.jsonl').exists():
            for line in (predictions/'predictions.jsonl').read_text().splitlines():
                try:parsed.append(json.loads(line))
                except ValueError:break
        found={p['sample_id'] for p in parsed}
        results.extend(parsed)
        results.extend(dict(sample_id=r['sample_id'],status='not_run',reason='worker_failed_or_time_limit') for r in good if r['sample_id'] not in found)
        stages.append(dict(batch=batch_index+1,returncode=rc,timed_out=timed_out))
    metrics=ev.annotation_reference_metrics(rows,results,pp.read_metadata(root/'metadata-real.csv'))
    metrics.update(model_sha256=plan['model_sha256'],reused_predictions=10,new_candidates=20,
                   preparation_seconds=prep_spent,inference_workers_seconds=inference_spent,stages=stages,
                   label_policy_chosen_after_initial_ten_predictions=True)
    pp.write_json(output/'reference-metrics.json',metrics)
    pp.write_json(output/'all-predictions.json',results)
    table=''.join('<tr>'+''.join('<td>'+html.escape(str(r[k]))+'</td>' for k in
                 ('sample_id','source','reference_target','reference_label_origin','status','prediction','accident_score','correct','loss'))+'</tr>' for r in metrics['rows'])
    (output/'report.html').write_text('<meta charset="utf-8"><h1>고정 30구간 주석·가정 기준 참고 평가</h1><p>독립 검증 아님. 기존10예측 재사용/신규20. 실패·미실행은 지표 분모에서 제외되며 아래에 모두 표시합니다.</p><pre>'+html.escape(json.dumps({k:metrics[k] for k in ['metrics','confusion_matrix','expected_segments','scored_segments','failed_or_missing','limitations']},ensure_ascii=False,indent=2))+'</pre><table border="1">'+table+'</table>')
    print(json.dumps({k:metrics[k] for k in ['metrics','confusion_matrix','scored_segments','failed_or_missing']},ensure_ascii=False),flush=True)
    return metrics


def low_fps_preview_one(data_root, candidate, output, *, reviewed_normal=False):
    """Versioned diagnostic input only: never authorized for old model loaders."""
    if reviewed_normal and (candidate.get('target') != 0 or candidate.get('label_origin') != 'user_segment_review'
                            or candidate.get('training_execution_authorized') is not False
                            or candidate.get('start_seconds') != 0 or candidate.get('end_seconds') != 2):
        raise pp.PreprocessingError('reviewed_normal_scope_required')
    root=Path(data_root);out=Path(output)
    if out.exists():raise pp.PreprocessingError('new_preview_output_required')
    start,end=pp.validate_window(candidate)
    if not math.isclose(end-start,2.,abs_tol=1e-8):raise pp.PreprocessingError('two_seconds_required')
    path=pp.source_path(root,candidate['source'])
    before=pp.sha256(path)
    frames,media=pp.sample_frames(path,start,end,16,allow_repeated_frames=True)
    details=pp.save_frames(out,frames,224,None,candidate['sample_id'],
                          label_caption='LOW FPS REPEAT POLICY REVIEW / NOT MODEL EVALUATION',save_unlabeled_input=True)
    if pp.sha256(path)!=before:raise pp.PreprocessingError('source_changed')
    record=dict(candidate,status='prepared_policy_review_only',source_sha256=before,
                sampling_policy='nearest-bin-repeat-v1',target=None,source_reference_target=0 if reviewed_normal else 1,
                human_reviewed=False,training_authorized=False,evaluation_authorized=False,
                input=details,media=media,array_sha256=pp.sha256(out/'frames.npy'))
    if reviewed_normal:
        times=[f['actual_seconds'] for f in details['frames']]
        if len(times)!=16 or any(not start<=t<end for t in times) or any(b<a for a,b in zip(times,times[1:])):
            raise pp.PreprocessingError('invalid_repeat_timestamps')
        record.update(target=0, reviewed_target=0, human_reviewed=True,
                      input_quality_review='pending_repeat_policy_preview', training_input_usable=False,
                      distinct_frames=len(set(times)), repeated_frame_slots=len(times)-len(set(times)),
                      note='User normal label preserved; array is policy review only, not training-ready.')
    pp.write_json(out/'policy-result.json',record)
    return record
