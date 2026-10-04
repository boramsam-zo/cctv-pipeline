"""Portable, bounded development evaluation of reviewed, prepared inputs.

Video selection/review stays local. Reuses the training loader and normalization;
never downloads weights, trains, or invents labels for reservations.
"""
from __future__ import annotations

import argparse
from datetime import date
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from . import preprocessing as pp
from .training_data import CLASS_MAPPING, TrainingError, fingerprint

SETTINGS = {"version": "r3d-letterbox-kinetics-v1", "frame_count": 16, "image_size": 224,
            "sampling": "nearest_PTS_to_uniform_bin_centres", "spatial": "letterbox_no_crop",
            "normalization": "KINETICS400_V1", "layout": "CTHW", "dtype": "float32"}
REFERENCE_PURPOSE = "reference_comparison_unconfirmed_independence"


def annotation_reference_metrics(candidates, predictions, metadata):
    """Post-prediction proxy-label comparison; never independent or reviewed truth."""
    expected = {r['sample_id']: r for r in candidates}
    if len(expected) != len(candidates) or not candidates:
        raise TrainingError('duplicate_or_empty_reference_candidates')
    lookup = {r['sample_id']: r for r in predictions}
    if len(lookup) != len(predictions) or set(lookup)-set(expected):
        raise TrainingError('duplicate_or_unknown_prediction')
    rows=[]; tp=tn=fp=fn=0; scored=[]
    for sid,c in expected.items():
        if c['requested_class']=='accident':
            match=metadata.get(c['source_path'],[])
            if len(match)!=1 or match[0]['csv_row']!=c['csv_row'] or match[0]['values']['split_in_distribution']!='train':
                raise TrainingError('reference_official_train_row_required')
            point=float(match[0]['values']['accident_time'])
            if not math.isfinite(point) or not math.isclose(c['start_seconds'],point-1,abs_tol=1e-8) or not math.isclose(c['end_seconds'],point+1,abs_tol=1e-8):
                raise TrainingError('reference_annotation_window_changed')
            target,origin=1,'source_annotation_derived_reference_only'
        elif c['requested_class']=='normal' and c.get('label_origin')=='user_assumed_pre_accident_normal':
            match=metadata.get(c['source_path'],[])
            if (len(match)!=1 or match[0]['csv_row']!=c['csv_row']
                    or match[0]['values']['split_in_distribution']!='train'):
                raise TrainingError('pre_accident_official_train_row_required')
            point=float(match[0]['values']['accident_time'])
            if (not math.isfinite(point) or c['start_seconds']!=0 or c['end_seconds']!=2
                    or c['end_seconds']>point-4):
                raise TrainingError('pre_accident_four_second_margin_required')
            target,origin=0,'user_assumed_pre_accident_normal'
        elif c['requested_class']=='normal' and c.get('label_origin')=='user_assumed_normal':
            target,origin=0,'user_assumed_normal'
        else:raise TrainingError('reference_label_basis_missing')
        p=lookup.get(sid,{'status':'not_run','reason':'prediction_missing'})
        row={'sample_id':sid,'source':c.get('source_path') or c.get('sequence_id'),
             'start_seconds':c['start_seconds'],'end_seconds':c['end_seconds'],
             'reference_target':target,'reference_label_origin':origin,'human_reviewed':False,
             'status':p['status'],'prediction':None,'accident_score':None,'correct':None,'loss':None}
        if p['status']=='predicted':
            score=p.get('accident_score');pred=p.get('prediction')
            if type(score) not in (float,int) or not math.isfinite(score) or not 0<=score<=1 or type(pred) is not int or pred not in (0,1) or pred!=int(score>.5):
                raise TrainingError('invalid_reference_prediction')
            loss=-math.log(max(1e-15,score if target else 1-score))
            row.update(prediction=pred,accident_score=score,correct=target==pred,loss=loss)
            tp+=int(target==1 and pred==1);tn+=int(target==0 and pred==0)
            fp+=int(target==0 and pred==1);fn+=int(target==1 and pred==0)
            scored.append((score,target,loss))
        else:row['reason']=p.get('reason','prediction_failed')
        rows.append(row)
    divide=lambda a,b:a/b if b else None
    positives=sum(t for _,t,_ in scored);negatives=len(scored)-positives
    ap=None
    if positives and negatives:
        ap=0.;cum_tp=0;cum_n=0
        for score in sorted({s for s,_,_ in scored},reverse=True):
            group=[t for s,t,_ in scored if s==score];added=sum(group)
            cum_tp+=added;cum_n+=len(group);ap+=(added/positives)*(cum_tp/cum_n)
    return {'purpose':'source_annotation_assumption_reference_metrics','independent_validation':False,
            'label_policy':'accident_point_plus_minus_1_second_vs_user_assumed_normal',
            'metrics_scope':'successfully_predicted_segments_only','expected_segments':len(rows),
            'scored_segments':len(scored),'failed_or_missing':len(rows)-len(scored),
            'class_counts':{'accident':sum(r['reference_target'] for r in rows),'normal':sum(r['reference_target']==0 for r in rows)},
            'distinct_source_identifiers':len({r['source'] for r in rows}),'independent_original_count':None,
            'confusion_matrix':{'tn':tn,'fp':fp,'fn':fn,'tp':tp},
            'metrics':{'accuracy':divide(tp+tn,len(scored)),'accident_precision':divide(tp,tp+fp),
                       'accident_recall':divide(tp,tp+fn),'accident_f1':divide(2*tp,2*tp+fp+fn),
                       'average_precision':ap,'mean_loss':divide(sum(x[2] for x in scored),len(scored))},
            'rows':rows,'errors':[r for r in rows if r['correct'] is False or r['status']!='predicted'],
            'limitations':['주석·정상 가정 기준 참고 지표이며 사람 검수/독립 검증 아님',
                          'ACCIDENT 사고/DETRAC 정상 출처 편향 가능성',
                          'AP는 계단형 precision-recall 요약이며 사다리꼴 PR-AUC와 구분',
                          '고정 모델 점수 CE이며 학습 online loss와 별도. log확률 하한1e-15']}


def reference_scope_sha256(manifest):
    """Bind an explicit scope approval to this model, inputs, labels and limits."""
    keys = ("sample_id", "source_sha256", "management_group", "start_seconds", "end_seconds",
            "target", "array_sha256", "frames_sha256", "preview_sha256")
    return fingerprint({
        "purpose": manifest["purpose"], "model_sha256": manifest["model"]["sha256"],
        "training_inputs_sha256": manifest["training_inputs"]["sha256"],
        "policy_version": manifest["policy_version"], "preprocessing": manifest["preprocessing"],
        "decision_rule": manifest["decision_rule"], "max_segments": manifest["max_segments"],
        "max_inference_seconds": manifest["max_inference_seconds"],
        "segments": [{k: s.get(k) for k in keys} for s in manifest["segments"]]})


def result_scope(purpose):
    if purpose not in ("development_validation", REFERENCE_PURPOSE):
        raise TrainingError("evaluation_protocol_mismatch")
    reference = purpose == REFERENCE_PURPOSE
    return {"purpose": purpose, "independent_validation": not reference,
            "independence_status": "unconfirmed" if reference else "reviewed",
            "limitation": ("독립성 미확인 참고용 예비 비교. 선택 구간의 서술값이며 독립 검증 성능이 아님. "
                           "파일 수는 독립 원본·사건 수를 뜻하지 않음." if reference else
                           "small development segment evaluation, not final test or event-level evaluation")}


def relative_file(root, value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise TrainingError("relative_file_required")
    p = Path(value)
    if p.is_absolute() or ".." in p.parts or not (root / p).resolve().is_relative_to(root.resolve()):
        raise TrainingError("file_outside_bundle")
    path = root / p
    if not path.is_file():
        raise TrainingError("missing_file:" + value)
    return path


def checked_file(root, value, expected):
    path = relative_file(root, value)
    if not isinstance(expected, str) or pp.sha256(path) != expected:
        raise TrainingError("file_hash_mismatch:" + value)
    return path


def signed_review(value):
    if not isinstance(value, dict) or value.get("status") != "approved":
        raise TrainingError("human_review_required")
    for key in ("reviewer", "reviewed_at", "basis"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise TrainingError("review_evidence_required:" + key)
    date.fromisoformat(value["reviewed_at"])


def preflight(root, manifest, *, execution_location="cloud"):
    """Fail closed before model execution. Does not assert independence from names."""
    root = Path(root)
    if execution_location not in ("local", "cloud"):
        raise TrainingError("explicit_execution_location_required")
    if (manifest.get("schema_version") != 1 or manifest.get("purpose") not in ("development_validation", REFERENCE_PURPOSE)
            or manifest.get("class_mapping") != CLASS_MAPPING or manifest.get("preprocessing") != SETTINGS
            or manifest.get("decision_rule") != "argmax_logits_tie_normal"
            or manifest.get("window_seconds") != 2):
        raise TrainingError("evaluation_protocol_mismatch")
    if type(manifest.get("max_segments")) is not int or not 1 <= manifest["max_segments"] <= 10:
        raise TrainingError("segment_limit_must_be_1_to_10")
    if type(manifest.get("max_inference_seconds")) is not int or not 1 <= manifest["max_inference_seconds"] <= 300:
        raise TrainingError("inference_limit_must_be_1_to_300")
    segments = manifest.get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= manifest["max_segments"]:
        raise TrainingError("approved_validation_segments_required; reservations_are_not_labels")
    signed_review(manifest.get("evaluation_approval"))
    reference = manifest["purpose"] == REFERENCE_PURPOSE
    if reference:
        if manifest["max_segments"] > 3:
            raise TrainingError("reference_comparison_limit_must_be_at_most_3")
        approval = manifest.get("reference_comparison_approval")
        signed_review(approval)
        if (approval.get("purpose") != REFERENCE_PURPOSE or
                approval.get("independence_unconfirmed_accepted") is not True or
                approval.get("not_independent_validation") is not True or
                approval.get("scope_sha256") != reference_scope_sha256(manifest)):
            raise TrainingError("reference_scope_approval_required")
    model = manifest["model"]
    checked_file(root, model["path"], model["sha256"])
    training = manifest["training_inputs"]
    trained = pp.read_json(checked_file(root, training["path"], training["sha256"]))
    hashes = {r["source_sha256"] for r in trained}
    groups = {r["management_group"] for r in trained}
    known_ids = {r.get(k) for r in trained for k in ("upstream_original_id", "event_id")} - {None}
    ids, windows, source_groups, relationship_groups, records = set(), set(), {}, {}, []
    for s in segments:
        sid = s.get("sample_id")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sid) or sid in ids:
            raise TrainingError("invalid_or_duplicate_segment_id")
        ids.add(sid)
        start, end = pp.validate_window(s)
        if not math.isclose(end - start, 2, abs_tol=1e-6):
            raise TrainingError("approved_two_second_interval_required")
        sha = s.get("source_sha256")
        group = s.get("management_group")
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha) or not group:
            raise TrainingError("source_hash_and_group_required")
        if (sha in hashes or group in groups or
                any(s.get(k) in known_ids for k in ("upstream_original_id", "event_id"))):
            raise TrainingError("training_source_or_event_overlap")
        window = (sha, start, end)
        if window in windows:
            raise TrainingError("duplicate_source_interval")
        windows.add(window)
        if sha in source_groups and source_groups[sha] != group:
            raise TrainingError("same_source_must_share_group")
        source_groups[sha] = group
        for key in ("upstream_original_id", "event_id"):
            identifier = s.get(key)
            if identifier is not None:
                if (key, identifier) in relationship_groups and relationship_groups[key, identifier] != group:
                    raise TrainingError("same_event_must_share_group")
                relationship_groups[key, identifier] = group
        if s.get("split") != "validation" or s.get("training_authorized") is not False or s.get("final_test") is not False:
            raise TrainingError("validation_only_no_final_test_required")
        if not reference:
            signed_review(s.get("independence_review"))
            if s["independence_review"].get("independent_from_training") is not True:
                raise TrainingError("independence_unconfirmed")
            # Bind review to this original and to the actual training group list.
            if (s["independence_review"].get("source_sha256") != sha or
                    set(s["independence_review"].get("compared_training_groups", [])) != groups):
                raise TrainingError("independence_review_not_bound_to_sources")
        signed_review(s.get("usage_review"))
        # Location is explicit: a CPU can also run in a cloud runtime.
        if s["usage_review"].get(execution_location + "_evaluation_allowed") is not True:
            raise TrainingError(execution_location + "_use_approval_required")
        if not s.get("source_reference") or not s.get("source_path"):
            raise TrainingError("source_reference_required")
        split = s.get("official_iid_split")
        if split not in ("train", "not_applicable"):
            raise TrainingError("official_test_or_unknown_split_forbidden")
        if s.get("dataset") == "ACCIDENT":
            md = s["metadata"]
            rows = pp.read_metadata(checked_file(root, md["path"], md["sha256"]))
            matched = rows.get(s["source_path"], [])
            if (split != "train" or len(matched) != 1 or matched[0]["csv_row"] != md["csv_row"] or
                    matched[0]["values"]["split_in_distribution"] != "train"):
                raise TrainingError("official_iid_train_row_required")
        else:
            signed_review(s.get("split_review"))
        target = pp.approved_target(s, manifest["policy_version"], sha)
        if target is None or type(s.get("target")) is not int or s["target"] != target:
            raise TrainingError("approved_segment_label_required")
        signed_review(s.get("input_quality_review"))
        quality = s["input_quality_review"]
        for key in ("array_sha256", "frames_sha256", "preview_sha256"):
            if quality.get(key) != s.get(key):
                raise TrainingError("quality_review_not_bound_to_input")
        if quality.get("no_crop_or_missing_scene") is not True:
            raise TrainingError("input_quality_unconfirmed")
        record = dict(s)
        for kind in ("array", "frames", "preview"):
            record[kind + "_path"] = str(checked_file(root, s[kind + "_path"], s[kind + "_sha256"]))
        details = pp.read_json(Path(record["frames_path"]))
        requested = [f["requested_seconds"] for f in details["frames"]]
        expected = [start + (i + .5) * 2 / 16 for i in range(16)]
        transform = details["transform"]
        if (len(requested) != 16 or any(not math.isclose(a, b, abs_tol=1e-6) for a, b in zip(requested, expected))
                or transform.get("method") != "letterbox" or transform.get("crop") is not None
                or details.get("array_shape") != [16, 224, 224, 3]):
            raise TrainingError("prepared_sampling_or_spatial_mismatch")
        records.append(record)
    return records


def validate_saved_configuration(root, manifest, state):
    """Check transfer bytes and historical training content as separate hashes.

    The training writer excludes machine-specific array/frames paths before
    fingerprinting prepared records. Preserve that definition for saved models;
    never compare it directly with the JSON file's transfer checksum.
    """
    entry = manifest["training_inputs"]
    trained = pp.read_json(checked_file(Path(root), entry["path"], entry["sha256"]))
    if not isinstance(trained, list) or not trained or any(not isinstance(r, dict) for r in trained):
        raise TrainingError("invalid_training_inputs")
    content_sha256 = fingerprint([{k: v for k, v in r.items() if k not in ("array_path", "frames_path")}
                                  for r in trained])
    if (state.get("format_version") != 1 or state.get("model_name") != "r3d_18" or
            state.get("class_mapping") != CLASS_MAPPING or state.get("config", {}).get("preprocessing") != SETTINGS):
        raise TrainingError("saved_model_configuration_mismatch")
    if state.get('config', {}).get('purpose') == 'expanded_source_training_no_validation':
        proposal_entry = manifest.get('training_proposal', {})
        proposal = pp.read_json(checked_file(Path(root), proposal_entry['path'], proposal_entry['sha256']))
        provenance = state.get('provenance', {})
        if (provenance.get('input_proposal_sha256') != proposal_entry['sha256']
                or state['config'].get('input_proposal_sha256') != proposal_entry['sha256']):
            raise TrainingError('saved_model_training_proposal_mismatch')
        original = proposal.get('records', [])
        by_id = {r['sample_id']: r for r in original}
        order = provenance.get('record_order', [])
        if (not original or len(by_id) != len(original) or len(order) != len(original)
                or len(set(order)) != len(order) or set(order) != set(by_id)
                or [r.get('sample_id') for r in trained] != order):
            raise TrainingError('saved_model_training_order_mismatch')
        # Staging changes only the two runtime-local paths; labels/hashes/groups must match.
        clean = lambda r: {k: v for k, v in r.items() if k not in ('array_path', 'frames_path')}
        if [clean(r) for r in trained] != [clean(by_id[sid]) for sid in order]:
            raise TrainingError('saved_model_training_content_mismatch')
    elif state.get("provenance", {}).get("prepared_inputs_sha256") != content_sha256:
        raise TrainingError("saved_model_training_content_mismatch")
    return {"training_inputs_file_sha256": entry["sha256"],
            "prepared_inputs_content_sha256": content_sha256}


def fixed_comparison_candidates(plan, previous):
    """Selection is fixed by old success, never by new model scores."""
    rows=previous['rows']; ids=[r['sample_id'] for r in rows if r['status']=='predicted']
    candidates=plan['records']; by_id={r['sample_id']:r for r in candidates}
    if (len(candidates)!=30 or len(by_id)!=30 or len(rows)!=30
            or len({r['sample_id'] for r in rows})!=30 or set(by_id)!={r['sample_id'] for r in rows}
            or len(ids)!=23 or previous['scored_segments']!=23 or previous['failed_or_missing']!=7
            or previous['independent_validation'] is not False or previous['model_sha256']!=plan['model_sha256']):
        raise TrainingError('fixed_23_comparison_scope_changed')
    return [by_id[s] for s in ids]


def stage_epoch_comparison(root, output, model_folder, input_folders):
    """Copy only saved 23 inputs + new model. No raw video or training arrays."""
    root,output=Path(root),Path(output)
    plan=pp.read_json(root/'benchmark-plan.json');previous=pp.read_json(root/'previous-metrics.json')
    selected=fixed_comparison_candidates(plan,previous)
    output.mkdir(parents=True,exist_ok=False)
    manifests=[(Path(folder),pp.read_json(Path(folder)/'manifest.json')) for folder in input_folders]
    rows=[]
    for c in selected:
        matches=[(folder,r) for folder,m in manifests for r in m['records'] if r['sample_id']==c['sample_id']]
        if len(matches)!=1:raise TrainingError('saved_input_missing_or_duplicate_'+c['sample_id'])
        folder,r=matches[0]
        if r['status']!='prepared_not_split':raise TrainingError('saved_input_not_prepared')
        for name in (r['input_path'],r['preview_path'],r['sample_id']+'/frames.json'):
            source=relative_file(folder,name);destination=output/name
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
        rows.append(r)
        print(f'입력 재사용 {len(rows)}/23',flush=True)
    for src,dst in [('loop/model.pt','model.pt'),('prepared_inputs.json','trained.json'),('result.json','training-result.json')]:
        shutil.copyfile(Path(model_folder)/src,output/dst)
    for name in ('benchmark-plan.json','previous-metrics.json','training-input-proposal.json','metadata-real.csv'):
        shutil.copyfile(root/name,output/name)
    pp.write_json(output/'manifest.json',{'purpose':'reserved_validation_input_preparation','records':rows})
    names=['model.pt','trained.json','training-result.json','training-input-proposal.json','manifest.json',
           'benchmark-plan.json','previous-metrics.json','metadata-real.csv']
    pp.write_json(output/'binding.json',{n:pp.sha256(output/n) for n in names})


def epoch_comparison_worker(root, output):
    root,output=Path(root),Path(output)
    for name,digest in pp.read_json(root/'binding.json').items():checked_file(root,name,digest)
    plan=pp.read_json(root/'benchmark-plan.json');previous=pp.read_json(root/'previous-metrics.json')
    selected=fixed_comparison_candidates(plan,previous)
    prepared=pp.read_json(root/'manifest.json');trained=pp.read_json(root/'trained.json')
    if [r['sample_id'] for r in prepared['records']]!=[r['sample_id'] for r in selected]:
        raise TrainingError('comparison_input_order_changed')
    model_manifest={k:{'path':n,'sha256':pp.sha256(root/n)} for k,n in
                    [('model','model.pt'),('training_inputs','trained.json'),('training_proposal','training-input-proposal.json')]}
    info=inspect_saved_model(root,model_manifest);receipt=pp.read_json(root/'training-result.json')
    if (info['training_progress']['step']!=1910 or info['training_progress']['completed_epochs']!=5
            or receipt['parent_checkpoint_sha256']!=previous['model_sha256']
            or receipt['status']!='completed_limited_loop' or receipt['progress']!=info['training_progress']):
        raise TrainingError('expected_epoch5_model_required')
    records=[]
    for start in range(0,23,10):
        records+=reserved_prediction_records(root,dict(prepared,records=prepared['records'][start:start+10]),
                                            {'records':selected[start:start+10]},trained)
    output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    model,device=load_model(root,model_manifest,'cuda')
    # Parent hard timeout bounds load, all23 inference, reporting together to 300s.
    predict(model,ReservedPredictionDataset(records),device,output,300)
    predictions=[json.loads(line) for line in (output/'predictions.jsonl').read_text().splitlines()]
    # Keep every old failure visible. Never substitute an old success for a new failure.
    predictions += [dict(sample_id=r['sample_id'],status='not_run',reason='previous_input_failure_not_retried')
                    for r in previous['rows'] if r['status']!='predicted']
    metrics=annotation_reference_metrics(plan['records'],predictions,pp.read_metadata(root/'metadata-real.csv'))
    metrics.update(model_sha256=model_manifest['model']['sha256'],previous_model_sha256=previous['model_sha256'],
                   training_run=False,comparison_selection='fixed_previous_23_successes',
                   worker_seconds_including_load=time.monotonic()-started)
    pp.write_json(output/'reference-metrics.json',metrics)
    old={r['sample_id']:r for r in previous['rows']}
    paired=[{'sample_id':r['sample_id'],'reference_target':r['reference_target'],
             'previous_prediction':old[r['sample_id']]['prediction'],'previous_score':old[r['sample_id']]['accident_score'],
             'new_prediction':r['prediction'],'new_score':r['accident_score'],'new_correct':r['correct'],
             'status':r['status'],'reason':r.get('reason'),'start_seconds':r['start_seconds'],'end_seconds':r['end_seconds']}
            for r in metrics['rows']]
    comparison={'purpose':'epoch5_reference_comparison','independent_validation':False,'training_run':False,
                'previous_metrics':previous['metrics'],'new_metrics':metrics['metrics'],
                'same_scored_samples':metrics['scored_segments']==23,
                'note':'新規失敗があれば分母差あり。独立検証ではない。','rows':paired}
    comparison['note']='새 예측 실패가 있으면 비교 분모가 다릅니다. 원본 주석·정상 가정 기준 참고평가이며 독립 검증 아님.'
    pp.write_json(output/'comparison.json',comparison)
    for r in records:shutil.copyfile(relative_file(root,r['preview_path']),output/(r['sample_id']+'.png'))
    headers=['구간','원본 초','참고 정답','이전 예측','이전 사고 점수','새 예측','새 사고 점수','정오','상태','미리보기']
    lines=[]
    for r in paired:
        values=[r['sample_id'],f"{r['start_seconds']}–{r['end_seconds']}",r['reference_target'],r['previous_prediction'],r['previous_score'],
                r['new_prediction'],r['new_score'],r['new_correct'],r['reason'] or r['status']]
        link=f'<a href="{r["sample_id"]}.png">16장</a>' if r['sample_id'] in {c['sample_id'] for c in selected} else '기존 입력 실패'
        lines.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'<td>'+link+'</td></tr>')
    (output/'report.html').write_text('<meta charset="utf-8"><h1>누적5epoch 모델 참고 비교</h1><p>'+comparison['note']+
        '</p><p>0 정상 / 1 사고. 동일 argmax 판정. 기존30 중 성공23만 새 추론, 실패7은 재처리하지 않음.</p><pre>'+html.escape(json.dumps({k:v for k,v in comparison.items() if k!='rows'},ensure_ascii=False,indent=2))+
        '</pre><table border="1"><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr>'+''.join(lines)+'</table>')
    if reviewed711:
        report=output/'report.html'
        report.write_text(report.read_text().replace('382→603','631→711'))
    pp.write_json(output/'input-times.json',[{'sample_id':r['sample_id'],'frames':r['input']['frames']} for r in records])
    print(json.dumps(metrics['metrics']),flush=True)


def low_fps_seven_records(root, low, plan, previous, trained):
    """A separate prediction-only policy; original strict loader stays default."""
    selected=fixed_comparison_candidates(plan,previous)
    success={r['sample_id'] for r in selected}
    expected={r['sample_id']:r for r in plan['records'] if r['sample_id'] not in success}
    rows=low.get('records',[])
    if (low.get('sampling_policy')!='nearest-bin-repeat-v1' or len(rows)!=7
            or {r['sample_id'] for r in rows}!=set(expected)):
        raise TrainingError('fixed_seven_repeat_inputs_required')
    adapted=[];candidates=[]
    for r in rows:
        sid=r['sample_id'];c=expected[sid]
        times=[f['actual_seconds'] for f in r['input']['frames']]
        if (r['status']!='prepared_policy_review_only' or r['target'] is not None
                or r['training_authorized'] is not False or r['evaluation_authorized'] is not False
                or r['source']!=c['source_path'] or r['start_seconds']!=c['start_seconds']
                or r['end_seconds']!=c['end_seconds'] or r['sampling_policy']!='nearest-bin-repeat-v1'
                or r['media']['unique_frame_count']!=len(set(times))
                or r['media']['repeated_selection_count']!=16-len(set(times))):
            raise TrainingError('low_fps_record_changed')
        adapted.append(dict(c,status='prepared_not_split',input=r['input'],input_path=sid+'/frames.npy',
                            frames_sha256=r['array_sha256'],preview_path=sid+'/preview.png',
                            sampling_policy='nearest-bin-repeat-v1'))
        candidates.append(c)
    return reserved_prediction_records(root,{'purpose':'reserved_validation_input_preparation','records':adapted},
                                       {'records':candidates},trained,repeat_policy=True)


def check_strict_reuse_metadata(plan,previous,manifests):
    """Strict successes have no repeated PTS; opt-in repetition leaves those picks unchanged."""
    selected=fixed_comparison_candidates(plan,previous);hashes={}
    for c in selected:
        matches=[r for m in manifests for r in m['records'] if r['sample_id']==c['sample_id']]
        if len(matches)!=1:raise TrainingError('reuse_manifest_missing_or_duplicate')
        r=matches[0];d=r['input'];frames=d['frames'];times=[f['actual_seconds'] for f in frames]
        if (r['status']!='prepared_not_split' or any(r.get(k)!=c.get(k) for k in
                 ('source_path','sequence_id','start_seconds','end_seconds','target','label_origin'))
                or d['array_shape']!=[16,224,224,3] or len(times)!=16
                or not all(a<b for a,b in zip(times,times[1:]))
                or not all(c['start_seconds']<=t<c['end_seconds'] for t in times)
                or d['transform']['method']!='letterbox' or d['transform'].get('crop') is not None):
            raise TrainingError('strict_reuse_input_incompatible')
        hashes[c['sample_id']]=r['frames_sha256']
    return hashes


def stage_complete_thirty(root,output,model_folder,low_folder,reuse_folders):
    root,output,low_folder=Path(root),Path(output),Path(low_folder)
    expected=pp.read_json(root/'request.json')
    if fingerprint(pp.read_json(low_folder/'manifest.json')) != expected['low_manifest_content_sha256']:
        raise TrainingError('low_manifest_changed')
    output.mkdir(parents=True,exist_ok=False)
    low=pp.read_json(low_folder/'manifest.json')
    for r in low['records']:
        sid=r['sample_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+',sid):raise TrainingError('invalid_id')
        for n in ('frames.npy','frames.json','preview.png'):
            src=relative_file(low_folder,sid+'/'+n);dst=output/sid/n
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    shutil.copyfile(low_folder/'manifest.json',output/'low-manifest.json')
    for src,dst in [('loop/model.pt','model.pt'),('prepared_inputs.json','trained.json')]:
        shutil.copyfile(Path(model_folder)/src,output/dst)
    for i,folder in enumerate(reuse_folders):shutil.copyfile(Path(folder)/'manifest.json',output/f'reuse-{i}.json')
    for n in ('benchmark-plan.json','previous-metrics.json','training-input-proposal.json','metadata-real.csv'):
        shutil.copyfile(root/n,output/n)
    names=['model.pt','trained.json','low-manifest.json','benchmark-plan.json','previous-metrics.json',
           'training-input-proposal.json','metadata-real.csv']+[f'reuse-{i}.json' for i in range(3)]
    pp.write_json(output/'binding.json',{n:pp.sha256(output/n) for n in names})


def complete_thirty_worker(root,output):
    root,output=Path(root),Path(output)
    binding=pp.read_json(root/'binding.json')
    for n,h in binding.items():checked_file(root,n,h)
    plan=pp.read_json(root/'benchmark-plan.json');previous=pp.read_json(root/'previous-metrics.json')
    # The old plan carries ancestor identity; current predictions must be from exactly the supplied model.
    if previous['model_sha256']!=pp.sha256(root/'model.pt'):
        raise TrainingError('must_reuse_same_epoch5_model_predictions')
    current_plan=dict(plan,model_sha256=previous['model_sha256'])
    reuse=check_strict_reuse_metadata(current_plan,previous,[pp.read_json(root/f'reuse-{i}.json') for i in range(3)])
    low=pp.read_json(root/'low-manifest.json');trained=pp.read_json(root/'trained.json')
    records=low_fps_seven_records(root,low,current_plan,previous,trained)
    model_manifest={k:{'path':n,'sha256':pp.sha256(root/n)} for k,n in
                    [('model','model.pt'),('training_inputs','trained.json'),('training_proposal','training-input-proposal.json')]}
    info=inspect_saved_model(root,model_manifest)
    if info['training_progress']['step']!=1910:raise TrainingError('epoch5_required')
    output.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    model,device=load_model(root,model_manifest,'cuda')
    predict(model,ReservedPredictionDataset(records),device,output,300)
    new=[json.loads(line) for line in (output/'predictions.jsonl').read_text().splitlines()]
    old=[{'sample_id':r['sample_id'],'status':'predicted','prediction':r['prediction'],'accident_score':r['accident_score']}
         for r in previous['rows'] if r['status']=='predicted']
    result=annotation_reference_metrics(plan['records'],old+new,pp.read_metadata(root/'metadata-real.csv'))
    result.update(model_sha256=previous['model_sha256'],sampling_policy='nearest-bin-repeat-v1',reused_predictions=23,
                  newly_requested_predictions=7,training_run=False,worker_seconds_including_load=time.monotonic()-start,
                  reuse_evidence={'basis':'saved strictly increasing input timestamps; opt-in repeat changes only duplicate rejection',
                                  'saved_array_hashes':reuse,'old_23_arrays_redecoded':False},
                  comparison_note='Coverage and input policy differ from strict23. Do not attribute metric change to training.')
    pp.write_json(output/'reference-metrics.json',result)
    mapping={r['sample_id']:r for r in records}
    body=[]
    for r in result['rows']:
        sid=r['sample_id'];link='기존23 예측 재사용'
        if sid in mapping:
            shutil.copyfile(relative_file(root,mapping[sid]['preview_path']),output/(sid+'.png'))
            link=f'<a href="{sid}.png">반복 정책 16장</a>'
        values=[sid,r['reference_target'],r['prediction'],r['accident_score'],r['correct'],r['status'],r.get('reason','')]
        body.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'<td>'+link+'</td></tr>')
    (output/'report.html').write_text('<meta charset="utf-8"><h1>반복 프레임 정책 · 고정30후보 참고평가</h1><p>독립 검증 아님. 동일5epoch모델. 기존23점수재사용+신규7. 정책과평가범위가달라기존23점수와단순개선비교금지.</p><pre>'+html.escape(json.dumps({k:result[k] for k in ('metrics','confusion_matrix','expected_segments','scored_segments','failed_or_missing')},ensure_ascii=False,indent=2))+'</pre><table border="1"><tr><th>ID</th><th>참고 정답</th><th>예측</th><th>사고 점수</th><th>정오</th><th>상태</th><th>오류</th><th>입력</th></tr>'+''.join(body)+'</table>')
    if reviewed711:
        report=output/'report.html'
        report.write_text(report.read_text().replace('382→603','631→711'))
    pp.write_json(output/'input-times.json',[{'sample_id':r['sample_id'],'frames':r['input']['frames']} for r in records])
    print(json.dumps(result['metrics']),flush=True)


def load_model(root, manifest, device_name="cpu", factory=None):
    """Saved trained checkpoint only; factory injection is for tiny unit fixtures."""
    import torch
    from .training import select_device
    path = checked_file(Path(root), manifest["model"]["path"], manifest["model"]["sha256"])
    state = torch.load(path, map_location="cpu", weights_only=True)
    validate_saved_configuration(root, manifest, state)
    if factory is None:
        from torchvision.models.video import r3d_18
        model = r3d_18(weights=None)
        model.fc = torch.nn.Linear(model.fc.in_features, 2)
    else:
        model = factory()
    model.load_state_dict(state["model_state"], strict=True)
    device = select_device(device_name)
    model.to(device).requires_grad_(False)
    model.eval()
    return model, device


def inspect_saved_model(root, manifest):
    """Notebook load cell: CPU deserialization, no forward, GPU allocated in worker."""
    import torch
    path = checked_file(Path(root), manifest["model"]["path"], manifest["model"]["sha256"])
    state = torch.load(path, map_location="cpu", weights_only=True)
    provenance_check = validate_saved_configuration(root, manifest, state)
    return {"model": state["model_name"], "class_mapping": state["class_mapping"],
            "preprocessing": state["config"]["preprocessing"], "training_progress": state["progress"],
            "historical_training_device": state["device_type"], "provenance_check": provenance_check,
            "next": "Device load occurs inside bounded worker; no inference performed here"}


def reserved_prediction_records(root, prepared, reservations, trained, *, repeat_policy=False):
    """Inference-only path. Unknown targets remain unknown; not an evaluation gate."""
    rows = prepared.get('records', [])
    expected = reservations.get('records', [])
    if (prepared.get('purpose') != 'reserved_validation_input_preparation'
            or not 1 <= len(rows) <= 10 or len(rows) != len(expected)):
        raise TrainingError('invalid_reserved_prediction_inputs')
    from .train_candidates import filename_family
    used = {r.get('source_path') for r in trained} - {None}
    families = {filename_family(p) for p in used}
    sequences = {r.get('sequence_id') for r in trained} - {None}
    result, seen = [], set()
    for r, approved_candidate in zip(rows, expected):
        sid = r.get('sample_id')
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', sid) or sid in seen:
            raise TrainingError('invalid_reserved_prediction_id')
        seen.add(sid)
        keys = ('sample_id','source_path','sequence_id','start_seconds','end_seconds','target','label_origin')
        if (any(r.get(k) != approved_candidate.get(k) for k in keys)
                or r.get('status') != 'prepared_not_split' or r.get('training_authorized') is not False):
            raise TrainingError('reserved_prediction_record_changed_or_failed')
        if repeat_policy and r.get('sampling_policy') != 'nearest-bin-repeat-v1':
            raise TrainingError('explicit_repeat_policy_required')
        path = r.get('source_path')
        if path in used or path and filename_family(path) in families or r.get('sequence_id') in sequences:
            raise TrainingError('reserved_training_overlap')
        start, end = pp.validate_window(r)
        if not math.isclose(end-start,2.,rel_tol=0.,abs_tol=1e-9):raise TrainingError('two_seconds_required')
        if r.get('requested_class') == 'accident':
            if r['target'] is not None or r.get('official_iid_split') != 'train':
                raise TrainingError('unconfirmed_accident_target_required')
        elif r.get('requested_class') != 'normal' or r['target'] != 0 or r['label_origin'] != 'user_assumed_normal':
            raise TrainingError('normal_assumption_required')
        array = checked_file(root,r['input_path'],r['frames_sha256'])
        frames = relative_file(root,f'{sid}/frames.json')
        details = pp.read_json(frames)
        times = [f['actual_seconds'] for f in details['frames']]
        requested = [f['requested_seconds'] for f in details['frames']]
        if (details != r['input'] or details['target'] != r['target']
                or details['array_shape'] != [16,224,224,3] or len(times) != 16
                or not all(math.isfinite(t) and start <= t < end for t in times)
                or not all(b>=a if repeat_policy else b>a for a,b in zip(times,times[1:]))
                or len(requested)!=16 or any(not math.isclose(t,start+(i+.5)*2/16,abs_tol=1e-8) for i,t in enumerate(requested))
                or details['transform'].get('method')!='letterbox' or details['transform'].get('crop') is not None):
            raise TrainingError('reserved_input_timing_or_transform_changed')
        result.append(dict(r,array_path=str(array),array_sha256=r['frames_sha256'],
                           frames_path=str(frames),frames_sha256=pp.sha256(frames)))
    return result


class ReservedPredictionDataset:
    """Uses identical tensor transform, but never fabricates a target for inference."""
    def __init__(self, records):self.records=records
    def __getitem__(self,index):
        import numpy as np
        from .training import tensor_from_array
        r=self.records[index]
        if pp.sha256(Path(r['array_path']))!=r['array_sha256'] or pp.sha256(Path(r['frames_path']))!=r['frames_sha256']:
            raise TrainingError('reserved_input_changed_after_check')
        return tensor_from_array(np.load(r['array_path'],allow_pickle=False),SETTINGS), None, r['sample_id']


def reserved_prediction_worker(root, output):
    """Fixed prepared candidates, predictions only; does not call compare/preflight."""
    root,output=Path(root),Path(output)
    binding=pp.read_json(root/'binding.json')
    prepared=pp.read_json(checked_file(root,'manifest.json',binding['prepared_sha256']))
    reservations=pp.read_json(checked_file(root,'reservations.json',binding['reservations_sha256']))
    manifest=binding['model_manifest']
    trained=pp.read_json(checked_file(root,manifest['training_inputs']['path'],manifest['training_inputs']['sha256']))
    records=reserved_prediction_records(root,prepared,reservations,trained)
    import torch
    torch.set_num_threads(4)
    output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    model,device=load_model(root,manifest,'cuda')
    predict(model,ReservedPredictionDataset(records),device,output,300)
    predictions=[json.loads(line) for line in (output/'predictions.jsonl').read_text().splitlines()]
    result={'purpose':'reserved_candidates_prediction_only','independent_validation':False,
            'training_run':False,'model_run':True,'model_sha256':manifest['model']['sha256'],
            'decision_rule':'argmax_logits_tie_normal','class_mapping':CLASS_MAPPING,
            'metrics':None,'metrics_note':'사고 정답 미확정, 정상은 사용자 가정, 사건·카메라 독립성 미확인',
            'worker_seconds_including_load':time.monotonic()-started,'predictions':predictions}
    pp.write_json(output/'result.json',result)
    table=[]
    for r,p in zip(records,predictions):
        shutil.copyfile(relative_file(root,r['preview_path']),output/f"{r['sample_id']}.png")
        values=[r['sample_id'],r.get('source_path') or r.get('sequence_id'),
                f"{r['start_seconds']:.3f}–{r['end_seconds']:.3f}",
                '미확정' if r['target'] is None else '정상(사용자 가정)',p['status'],
                {0:'정상',1:'사고'}.get(p.get('prediction'),'미실행'),p.get('accident_score'),
                [f['actual_seconds'] for f in r['input']['frames']]]
        table.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+
                     f'<td><a href="{r["sample_id"]}.png">16장 미리보기</a></td></tr>')
    (output/'report.html').write_text('<meta charset="utf-8"><h1>예약 후보 예측 — 독립 검증 아님</h1><p>사고 점수는 두 클래스 softmax의 사고 값. 큰 logit의 클래스를 선택하며 동률은 정상. 정답은 예측에 사용하지 않음. 손실·정확도·F1 미측정.</p><table border="1"><tr><th>ID</th><th>원본</th><th>초</th><th>정답 상태</th><th>상태</th><th>예측</th><th>사고 점수</th><th>읽은 시각</th><th>장면</th></tr>'+''.join(table)+'</table>')


def predict(model, dataset, device, output, seconds, clock=time.monotonic):
    """Save predictions without ground truth; hard GPU timeout supplied by parent."""
    import torch
    from .training import sync
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    before = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    model.eval().requires_grad_(False)
    sync(device)
    pp.write_json(output / "loop_started.json", {"monotonic": clock()})
    start = clock()
    stop = False
    with (output / "predictions.jsonl").open("x", encoding="utf-8") as stream:
        for index, record in enumerate(dataset.records):
            row = {"sample_id": record["sample_id"], "status": "not_run", "reason": "time_limit"}
            if not stop and clock() - start < seconds:
                try:
                    inputs, _, sid = dataset[index]  # Targets never reach model.forward.
                    if clock() - start >= seconds:
                        stop = True
                    else:
                        with torch.inference_mode():
                            logits = model(inputs.unsqueeze(0).to(device))
                        sync(device)
                        if clock() - start >= seconds:
                            stop = True
                            row["reason"] = "time_limit_after_forward"
                        else:
                            if tuple(logits.shape) != (1, 2) or not torch.isfinite(logits).all().item():
                                raise TrainingError("invalid_logits")
                            values = logits[0].float().cpu()
                            row = {"sample_id": sid, "status": "predicted", "logits": values.tolist(),
                                   "prediction": values.argmax().item(),
                                   "accident_score": values.softmax(dim=0)[1].item()}
                except (Exception,) as exc:
                    # Preserve failures; do not use exception text containing private paths.
                    row.update(status="failed", reason=type(exc).__name__)
                    stop = True  # No more GPU calls after any input/device failure.
            elif stop:
                row["reason"] = "stopped_after_failure_or_time_limit"
            stream.write(json.dumps(row, allow_nan=False) + "\n")
            stream.flush()
    sync(device)
    loop_seconds = clock() - start
    unchanged = all(torch.equal(before[k], v.detach().cpu()) for k, v in model.state_dict().items())
    pp.write_json(output / "worker_result.json", {"inference_loop_seconds": loop_seconds,
                  "model_and_bn_unchanged": unchanged, "device": str(device),
                  "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(device) if device.type == "cuda" else None,
                  "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(device) if device.type == "cuda" else None})
    if not unchanged:
        raise TrainingError("model_state_changed")


def compare(records, predictions, *, purpose="development_validation"):
    """Comparison happens only after prediction file is closed, including failures."""
    import torch
    lookup = {r["sample_id"]: r for r in predictions}
    rows = []
    for record in records:
        p = lookup.get(record["sample_id"], {"sample_id": record["sample_id"], "status": "not_run", "reason": "worker_stopped"})
        row = dict(p, target=record["target"], management_group=record["management_group"],
                   source_path=record["source_path"], source_sha256=record["source_sha256"],
                   start_seconds=record["start_seconds"], end_seconds=record["end_seconds"],
                   actual_seconds=[f["actual_seconds"] for f in pp.read_json(Path(record["frames_path"]))["frames"]])
        row["correct"], row["loss"] = None, None
        if p["status"] == "predicted":
            row["correct"] = p["prediction"] == record["target"]
            row["loss"] = torch.nn.functional.cross_entropy(torch.tensor([p["logits"]], dtype=torch.float32),
                                                           torch.tensor([record["target"]])).item()
        rows.append(row)
    good = [r for r in rows if r["status"] == "predicted"]
    tn, fp, fn, tp = (sum(r["target"] == t and r["prediction"] == p for r in good)
                      for t, p in ((0, 0), (0, 1), (1, 0), (1, 1)))
    ratio = lambda n, d: n / d if d else None
    counts = {}
    for label, target in CLASS_MAPPING.items():
        subset = [r for r in records if r["target"] == target]
        counts[label] = {"segments": len(subset), "source_files": len({r["source_sha256"] for r in subset}),
                         "management_groups": len({r["management_group"] for r in subset})}
    return {**result_scope(purpose), "rows": rows, "counts": counts,
            "total_source_files": len({r["source_sha256"] for r in records}),
            "independent_event_count": None,
            "coverage": {"requested": len(rows), "predicted": len(good),
            "failed_or_not_run": len(rows) - len(good)}, "metrics_scope": "successfully_predicted_segments_only",
            "confusion_matrix_true_rows_normal_accident": [[tn, fp], [fn, tp]], "false_positives": fp,
            "false_negatives": fn, "accuracy": ratio(tp + tn, len(good)),
            "accident_precision": ratio(tp, tp + fp), "accident_recall": ratio(tp, tp + fn),
            "accident_f1": ratio(2 * tp, 2 * tp + fp + fn),
            "mean_loss": ratio(sum(r["loss"] for r in good), len(good)),
            "loss_definition": "unweighted CrossEntropyLoss, per segment; mean over successful final-model predictions"}


def write_report(output, result, records):
    assets = output / "previews"
    assets.mkdir()
    title = ("독립성 미확인 · 참고용 예비 비교" if result.get("purpose") == REFERENCE_PURPOSE
             else "새 독립 원본의 예비 검증")
    parts = ['<!doctype html><html lang="ko"><meta charset="utf-8"><title>' + title + '</title>',
             '<style>body{max-width:1100px;margin:32px auto;font-family:sans-serif}td,th{padding:8px;border:1px solid #aaa}img{max-width:100%}pre{white-space:pre-wrap}</style>',
             '<h1>' + title + '</h1><p>' + html.escape(result["limitation"]) + '</p>',
             '<p>사고 점수는 softmax의 클래스 1 값입니다. argmax로 판정하며 동점은 정상입니다. 보정된 실제 사고 확률이 아닙니다.</p>']
    parts.append('<h2>표본·지표·실패·시간</h2><pre>' + html.escape(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2)) + '</pre>')
    parts.append('<h2>예측 저장 후 정답 비교</h2><table><tr><th>ID</th><th>정답</th><th>예측</th><th>사고 점수</th><th>맞음</th><th>loss</th><th>상태</th></tr>')
    for row in result["rows"]:
        parts.append('<tr>' + ''.join('<td>' + html.escape(str(row.get(k))) + '</td>' for k in
                      ("sample_id", "target", "prediction", "accident_score", "correct", "loss", "status")) + '</tr>')
    parts.append('</table><h2>검수한 입력과 원본 시각</h2>')
    for rec, row in zip(records, result["rows"]):
        name = rec["sample_id"] + '.png'
        shutil.copyfile(rec["preview_path"], assets / name)
        parts.append('<h3>' + html.escape(rec["sample_id"]) + '</h3><p>' + html.escape(
            f'{rec["source_path"]} [{rec["start_seconds"]}, {rec["end_seconds"]}) / PTS {row["actual_seconds"]}') + '</p><img src="previews/' + name + '">')
    parts.append('<p><a href="timing.json">준비·보고서 저장까지 실제 시간</a> / <a href="drive_export_timing.json">Drive 복사 포함 시간</a></p>')
    parts.append('<p>코드 실행 결과이며 사용자 직접 확인은 대기입니다.</p></html>')
    (output / "report.html").write_text('\n'.join(parts), encoding='utf-8')


def supervise(child, output, child_started, seconds, clock=time.monotonic, pause=time.sleep):
    """Hard deadline also covers an unresponsive device call; no CPU retry."""
    while child.poll() is None:
        marker = output / "loop_started.json"
        if marker.exists():
            try:
                started = pp.read_json(marker)["monotonic"]
            except (ValueError, KeyError):
                started = child_started
            expired = clock() - started >= seconds
            reason = "inference_hard_timeout"
        else:
            expired = clock() - child_started >= 120
            reason = "startup_timeout"
        if expired:
            child.kill()
            child.wait()
            return reason
        pause(.05)
    return "completed"


def run_evaluation(root, manifest_path, output, device="cuda", *, execution_location="cloud",
                   runtime_copy_seconds=0.0, model_inspect_seconds=0.0):
    """Parent supervisor: <=300s inference, independent <=120s startup budget."""
    began = time.monotonic()
    root, output = Path(root).resolve(), Path(output).resolve()
    manifest = pp.read_json(relative_file(root, manifest_path))
    records = preflight(root, manifest, execution_location=execution_location)
    if device not in ("cuda", "mps", "cpu"):
        raise TrainingError("explicit_device_required")
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / "fixed_manifest.json", manifest)  # Frozen before scores.
    pp.write_json(output / "run_environment.json", {"python": sys.version.split()[0], "requested_device": device,
                                                   "execution_location": execution_location})
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    child_started = time.monotonic()
    reason = "completed"
    with (output / "worker.log").open('x') as log:
        child = subprocess.Popen([sys.executable, "-m", "boram3_cctv.evaluation", "--worker", str(root),
                                  str(output / "fixed_manifest.json"), str(output), device, execution_location],
                                 stdout=log, stderr=log, env=env)
        try:
            reason = supervise(child, output, child_started, manifest["max_inference_seconds"])
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    child_seconds = time.monotonic() - child_started
    predictions = []
    path = output / "predictions.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                predictions.append(json.loads(line))
            except ValueError:  # A killed worker may leave only its last line incomplete.
                break
    result = compare(records, predictions, purpose=manifest["purpose"])
    worker = pp.read_json(output / "worker_result.json") if (output / "worker_result.json").exists() else None
    result.update(model=manifest["model"], preprocessing=SETTINGS, class_mapping=CLASS_MAPPING,
                  evaluation_code_sha256={p.name: pp.sha256(p) for p in
                      (Path(__file__), Path(__file__).with_name('training.py'),
                       Path(__file__).with_name('preprocessing.py'), Path(__file__).with_name('training_data.py'))},
                  decision_rule=manifest["decision_rule"], device=device, execution_location=execution_location, worker=worker,
                  exit_code=child.returncode, completion=reason if child.returncode == 0 else reason + "_worker_failed",
                  model_state_confirmation="confirmed" if worker and worker["model_and_bn_unchanged"] else "unconfirmed",
                  timing={"runtime_copy_seconds": runtime_copy_seconds, "model_inspect_seconds": model_inspect_seconds, "child_seconds": child_seconds,
                          "inference_loop_seconds": worker["inference_loop_seconds"] if worker else None},
                  user_review="pending")
    pp.write_json(output / "comparison.json", result)
    write_report(output, result, records)
    # Separate receipt includes preparation, loading, prediction, comparison and report saving.
    pp.write_json(output / "timing.json", {"evaluation_through_report_save_seconds": time.monotonic() - began,
                   "runtime_copy_seconds": runtime_copy_seconds, "model_inspect_seconds": model_inspect_seconds,
                   "active_total_before_drive_export_seconds": runtime_copy_seconds + model_inspect_seconds + time.monotonic() - began,
                   "excludes": ["package setup", "Drive mount", "interactive pauses", "Drive export", "timing receipt write"]})
    return result


def export_results(output, destination):
    start = time.monotonic()
    output, destination = Path(output), Path(destination)
    shutil.copytree(output, destination)  # Refuse overwriting a prior run.
    timing = pp.read_json(output / "timing.json")
    pp.write_json(destination / "drive_export_timing.json", {
        "drive_copy_seconds": time.monotonic() - start,
        "active_total_through_drive_copy_seconds": timing["active_total_before_drive_export_seconds"] + time.monotonic() - start,
        "excludes": timing["excludes"][:3] + ["final timing receipt write"],
        "scope": "sum of measured active stages; not notebook elapsed time including human pauses"})
    return destination


def _worker(root, manifest_path, output, device, execution_location):
    import torch
    import torchvision
    from .training import SegmentDataset
    torch.set_num_threads(4)
    manifest = pp.read_json(Path(manifest_path))
    records = preflight(Path(root), manifest, execution_location=execution_location)
    model, actual_device = load_model(Path(root), manifest, device)
    pp.write_json(Path(output) / "device.json", {"torch": str(torch.__version__), "torchvision": str(torchvision.__version__),
        "device": str(actual_device), "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(actual_device) if actual_device.type == "cuda" else None})
    predict(model, SegmentDataset(records, SETTINGS), actual_device, Path(output), manifest["max_inference_seconds"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=5, required=True)
    args = parser.parse_args()
    _worker(*args.worker)


MODEL603_SHA = 'bb3e939ab28ed9c53f788b316cc071d08cbca5fa57d0fa182a9cee3d29d9595e'


def stage_603_comparison(root, output, model_folder, low_folder, input_folders):
    """Reuse the same strict23 and repeat7 arrays; no original video decoding."""
    stage_epoch_comparison(root,output,model_folder,input_folders)
    root,output,low_folder=Path(root),Path(output),Path(low_folder)
    if fingerprint(pp.read_json(low_folder/'manifest.json')) != pp.read_json(root/'request.json')['low_manifest_content_sha256']:
        raise TrainingError('low_manifest_changed')
    low=pp.read_json(low_folder/'manifest.json')
    for r in low['records']:
        sid=r['sample_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+',sid):raise TrainingError('invalid_id')
        for name in ('frames.npy','frames.json','preview.png'):
            dst=output/sid/name;dst.parent.mkdir(exist_ok=True,parents=True)
            shutil.copyfile(relative_file(low_folder,sid+'/'+name),dst)
    shutil.copyfile(low_folder/'manifest.json',output/'low-manifest.json')
    shutil.copyfile(root/'baseline-thirty.json',output/'baseline-thirty.json')
    binding=pp.read_json(output/'binding.json')
    for name in ('low-manifest.json','baseline-thirty.json'):binding[name]=pp.sha256(output/name)
    (output/'binding.json').write_text(json.dumps(binding))


def check_603_comparison(info, baseline, plan):
    if info['training_progress']['step']!=3015 or info['training_progress']['completed_epochs']!=5:
        raise TrainingError('603_five_epochs_required')
    check_thirty_baseline(baseline,plan)

def check_thirty_baseline(baseline,plan):
    ids=[r['sample_id'] for r in plan['records']]
    rows=baseline['rows']
    if (len(ids)!=30 or len(set(ids))!=30 or len(rows)!=30
            or {r['sample_id'] for r in rows}!=set(ids)
            or any(r['status']!='predicted' for r in rows)
            or baseline.get('independent_validation') is not False):
        raise TrainingError('fixed_thirty_baseline_required')


def comparison_603_worker(root, output, *, reviewed606=False, reviewed609=False, reviewed631=False, reviewed711=False):
    root,output=Path(root),Path(output)
    for name,digest in pp.read_json(root/'binding.json').items():checked_file(root,name,digest)
    model_sha=pp.sha256(root/'model.pt')
    if not reviewed606 and not reviewed609 and not reviewed631 and not reviewed711 and model_sha!=MODEL603_SHA:raise TrainingError('603_model_changed')
    plan=pp.read_json(root/'benchmark-plan.json');old23=pp.read_json(root/'previous-metrics.json')
    baseline=pp.read_json(root/'baseline-thirty.json');prepared=pp.read_json(root/'manifest.json')
    trained=pp.read_json(root/'trained.json')
    selected=fixed_comparison_candidates(plan,old23)
    manifest={k:{'path':n,'sha256':pp.sha256(root/n)} for k,n in
              [('model','model.pt'),('training_inputs','trained.json'),('training_proposal','training-input-proposal.json')]}
    info=inspect_saved_model(root,manifest)
    if reviewed711:
        check_711_comparison(info, baseline, plan, trained, model_sha)
    elif reviewed631:
        check_631_comparison(info, baseline, plan, trained, model_sha)
    elif reviewed609:
        check_609_comparison(info, baseline, plan, trained)
    elif reviewed606:
        if info['training_progress']['step']!=3030 or info['training_progress']['completed_epochs']!=5 or len(trained)!=606:
            raise TrainingError('606_five_epochs_required')
        check_thirty_baseline(baseline,plan)
        if baseline['model_sha256']!=MODEL603_SHA:raise TrainingError('603_baseline_required')
    else:check_603_comparison(info,baseline,plan)
    records=[]
    for start in range(0,23,10):
        records+=reserved_prediction_records(root,dict(prepared,records=prepared['records'][start:start+10]),
                    {'records':selected[start:start+10]},trained)
    records+=low_fps_seven_records(root,pp.read_json(root/'low-manifest.json'),plan,old23,trained)
    if {r['sample_id'] for r in records}!={r['sample_id'] for r in plan['records']}:raise TrainingError('comparison_ids_changed')
    if reviewed606 or reviewed609 or reviewed631:
        records.append(pp.read_json(root/'normal-case.json'))
    output.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    model,device=load_model(root,manifest,'cuda')
    predict(model,ReservedPredictionDataset(records),device,output,300)
    predictions=[json.loads(line) for line in (output/'predictions.jsonl').read_text().splitlines()]
    if reviewed606 or reviewed609 or reviewed631:
        case=[p for p in predictions if p['sample_id']=='continuous_normal_1_3']
        pp.write_json(output/'normal-case-comparison.json',dict(previous_score=0.9591450691223145 if reviewed631 else (0.9177208542823792 if reviewed609 else 0.9229256510734558),previous_prediction=1,target=0,new_result=case,independent_validation=False,label_review_after_previous_prediction=True))
    metrics=annotation_reference_metrics(plan['records'],[p for p in predictions if p['sample_id']!='continuous_normal_1_3'],pp.read_metadata(root/'metadata-real.csv'))
    metrics.update(model_sha256=model_sha,training_run=False,independent_validation=False,
                   worker_seconds_including_load=time.monotonic()-started,reused_predictions=0)
    pp.write_json(output/'reference-metrics.json',metrics)
    old={r['sample_id']:r for r in baseline['rows']}
    paired=[dict(sample_id=r['sample_id'],reference_target=r['reference_target'],previous_prediction=old[r['sample_id']]['prediction'],
                 previous_score=old[r['sample_id']]['accident_score'],new_prediction=r['prediction'],new_score=r['accident_score'],
                 correct=r['correct'],status=r['status'],reason=r.get('reason')) for r in metrics['rows']]
    comparison=dict(purpose='631_vs_711_same_thirty_reference' if reviewed711 else '609_vs_631_same_thirty_reference' if reviewed631 else '606_vs_609_same_thirty_reference' if reviewed609 else ('603_vs_606_same_thirty_reference' if reviewed606 else '382_vs_603_same_thirty_reference'),training_run=False,independent_validation=False,
                    previous_model_sha256=baseline['model_sha256'],new_model_sha256=model_sha,
                    previous_metrics=baseline['metrics'],new_metrics=metrics['metrics'],rows=paired,
                    note='Same30 inputs and decision rule. Reference labels/independence limitations remain. No tuning.')
    pp.write_json(output/'comparison.json',comparison)
    body=[]
    for r in paired:
        sid=r['sample_id'];shutil.copyfile(root/sid/'preview.png',output/(sid+'.png'))
        body.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in r.values())+f'<td><a href="{sid}.png">16장</a></td></tr>')
    (output/'report.html').write_text('<meta charset="utf-8"><h1>382→603 학습자료 확대 · 같은30구간 참고비교</h1><p>독립 검증 아님. 정답은 원본 주석/정상 가정. 가중치/판정 기준 변경 없음.</p><pre>'+html.escape(json.dumps({k:v for k,v in comparison.items() if k!='rows'},ensure_ascii=False,indent=2))+'</pre><table border="1"><tr>'+''.join('<th>'+h+'</th>' for h in ['ID','참고정답','이전예측','이전점수','새예측','새점수','정오','상태','사유','미리보기'])+'</tr>'+''.join(body)+'</table>')
    if reviewed606 or reviewed609 or reviewed631:
        report=output/'report.html'
        report.write_text(report.read_text().replace('382→603','609→631' if reviewed631 else ('606→609' if reviewed609 else '603→606'))+'<h2>정상 통행 1–3초 별도 사례</h2><p>참고30 지표에 합산하지 않습니다. 이전 예측 후 사람이 검수한 개발 사례입니다.</p><pre>'+html.escape((output/'normal-case-comparison.json').read_text())+'</pre><img src="normal-case.png">')
        shutil.copyfile(root/'continuous_normal_1_3/preview.png',output/'normal-case.png')
    if reviewed711:
        report=output/'report.html'
        report.write_text(report.read_text().replace('382→603','631→711'))
    pp.write_json(output/'input-times.json',[{'sample_id':r['sample_id'],'frames':r['input']['frames']} for r in records])
    print(json.dumps(metrics['metrics']),flush=True)


def stage_606_comparison(root, output, model_folder, low_folder, video, input_folders):
    """Same saved30 + reconstructed normal case, locked to previous source PTS."""
    stage_603_comparison(root,output,model_folder,low_folder,input_folders)
    root,output=Path(root),Path(output)
    trained=pp.read_json(output/'trained.json')
    source='real_videos/xSMybsal-zs_00.mp4'
    if any(r.get('source_path')==source for r in trained):raise TrainingError('normal_case_training_overlap')
    frames,media=pp.sample_frames(Path(video),1.,3.,16)
    old=pp.read_json(root/'normal-case-baseline.json')
    times=[r['actual_seconds'] for r in frames]
    if times!=[r['actual_seconds'] for r in old['frames']]:raise TrainingError('normal_case_sampling_changed')
    dest=output/'continuous_normal_1_3'
    details=pp.save_frames(dest,frames,224,None,dest.name,label_caption='NORMAL CASE / RECONSTRUCTED INPUT / NOT INDEPENDENT',save_unlabeled_input=True)
    record=dict(sample_id=dest.name,array_path=str(dest/'frames.npy'),array_sha256=pp.sha256(dest/'frames.npy'),frames_path=str(dest/'frames.json'),frames_sha256=pp.sha256(dest/'frames.json'),input=details)
    pp.write_json(output/'normal-case.json',record)
    binding=pp.read_json(output/'binding.json');binding['normal-case.json']=pp.sha256(output/'normal-case.json')
    (output/'binding.json').write_text(json.dumps(binding))


def check_609_comparison(info, baseline, plan, trained):
    if (info['training_progress']['step'] != 3045
            or info['training_progress']['completed_epochs'] != 5
            or len(trained) != 609):
        raise TrainingError('609_five_epochs_required')
    check_thirty_baseline(baseline, plan)
    if baseline['model_sha256'] != '2221931e905781f350733a8d470cc60a5886c99ad6c9b920ec61771b907fb656':
        raise TrainingError('606_baseline_required')


def check_631_comparison(info, baseline, plan, trained, model_sha):
    if (info['training_progress']['step'] != 3155
            or info['training_progress']['completed_epochs'] != 5 or len(trained) != 631):
        raise TrainingError('631_five_epochs_required')
    if model_sha != '7dbd352a3992e2c788ba517522698f5dd1353f5df6c46f4247a74820c59b891c':
        raise TrainingError('631_model_changed')
    check_thirty_baseline(baseline, plan)
    if baseline['model_sha256'] != '436b0e5bf84c09471fa0ae0d74eecf6c40be0f3ce80de16b6742aaf8ddfc9eac':
        raise TrainingError('609_baseline_required')


def check_711_comparison(info, baseline, plan, trained, model_sha):
    if (info['training_progress']['step'] != 3555
            or info['training_progress']['completed_epochs'] != 5 or len(trained) != 711):
        raise TrainingError('711_five_epochs_required')
    if model_sha != 'd81a8a12c7b0ccb2235f655a1e96abf919e8a309927e52d9ae522e3d78fd1941':
        raise TrainingError('711_model_changed')
    check_thirty_baseline(baseline, plan)
    if baseline['model_sha256'] != '7dbd352a3992e2c788ba517522698f5dd1353f5df6c46f4247a74820c59b891c':
        raise TrainingError('631_baseline_required')
