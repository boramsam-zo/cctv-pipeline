"""Validate and package frozen A/B outputs without model, media or network work.

The caller supplies a trusted normalized-result SHA and a frozen source profile.
A matching version label alone is not provenance. Original artifact bytes are kept;
canonical evidence names are additional copies, never rewritten media/context.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil

from .analysis_profiles import PORTABLE_GPU, gpu_identity, valid_gpu_name, validate_profile

SCHEMA = 'runpod-evidence-v2'
MAX_ASSET_BYTES = 200 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024


def _require(ok, reason):
    if not ok:
        raise ValueError('ab_evidence_' + reason)


def _number(value, low=0, high=1e9):
    _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
             'invalid_number')
    return value


def _integer(value, low=0, high=1000000):
    _require(type(value) is int and low <= value <= high, 'invalid_integer')
    return value


def _digest(value):
    _require(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value), 'invalid_sha256')
    return value


def _name(value):
    _require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_./-]+', value), 'unsafe_path')
    path = PurePosixPath(value)
    _require(not path.is_absolute() and str(path) == value and '..' not in path.parts
             and '.' not in path.parts, 'unsafe_path')
    return value


def _file(root, name):
    _require(not root.is_symlink(), 'symlink_root')
    path = root / _name(name)
    _require(path.is_file() and not path.is_symlink()
             and path.resolve().is_relative_to(root.resolve()), 'missing_or_unsafe_file')
    _require(not any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root)),
             'symlink_parent')
    return path


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for data in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def _json(path):
    _require(path.stat().st_size <= MAX_JSON_BYTES, 'json_too_large')
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('ab_evidence_nonfinite_json')
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                       parse_constant=invalid)
    _finite_json(value)
    return value


def _finite_json(value):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('ab_evidence_invalid_json') from None


def _records(value):
    if isinstance(value, dict):
        rows = [dict(entry, path=name) for name, entry in value.items()]
    else:
        _require(isinstance(value, list), 'artifact_records_required')
        rows = value
    _require(len(rows) <= 2048, 'too_many_artifacts')
    records = {}
    for row in rows:
        _require(isinstance(row, dict), 'invalid_artifact')
        name = _name(row['path'])
        _require(name not in records, 'duplicate_artifact')
        records[name] = {'bytes': _integer(row['bytes'], 1, MAX_ASSET_BYTES),
                         'sha256': _digest(row['sha256'])}
    _require(sum(v['bytes'] for v in records.values()) <= 1024 ** 3, 'artifacts_too_large')
    return records


def _verify_files(root, records):
    for name, record in records.items():
        path = _file(root, name)
        _require(path.stat().st_size == record['bytes'] and _sha(path) == record['sha256'],
                 'artifact_integrity')
        if path.suffix == '.json':
            _json(path)


def _close(a, b):
    return abs(_number(a) - _number(b)) <= 1e-6


def _classifier(raw, snapshot):
    from .model_catalog import contract_sha256
    expected = snapshot.get('classifier')
    if expected is not None:
        actual = raw.get('classifier')
        _require(isinstance(actual, dict), 'classifier_required')
        _require(contract_sha256({key: actual.get(key) for key in expected}) == contract_sha256(expected),
                 'classifier_contract_mismatch')


def _windows(raw, normalized, duration):
    if normalized['version'] in ('R2', 'LC'):
        return _r2_windows(raw, normalized, duration)
    x3d = raw['x3d']
    _require(x3d['model'] == 'x3d_s_real_so_tad_v1_epoch7'
             and x3d['label_mapping'] == {'0': 'Normal', '1': 'Collision'}
             and x3d['threshold'] == .5 and x3d['window_seconds'] == 2
             and x3d['stride_seconds'] == 1, 'x3d_contract')
    if duration <= 2:
        starts = [0.0]
    else:
        starts = [float(i) for i in range(math.floor(duration - 2 + 1e-6) + 1)]
        if duration - 2 - starts[-1] > 1e-6:
            starts.append(duration - 2)
    windows = x3d['windows']
    _require(isinstance(windows, list) and len(windows) == len(starts)
             and normalized['window_count'] == len(windows), 'window_coverage')
    for row, start in zip(windows, starts):
        score = _number(row['collision_probability'], 0, 1)
        _require(_close(row['start_s'], start) and _close(row['end_s'], min(start + 2, duration))
                 and type(row['is_positive']) is bool and row['is_positive'] == (score >= .5),
                 'window_values')
    positive = any(row['is_positive'] for row in windows)
    _require(bool(raw['events']) == positive, 'event_window_state')
    _require(normalized['status'] == ('completed' if positive else 'no_candidates'), 'result_status')
    _require(x3d['decision'] == ('Collision' if positive else 'Normal')
             and _close(x3d['max_collision_probability'], max(x['collision_probability'] for x in windows)),
             'x3d_summary')


def _r2_windows(raw, normalized, duration):
    classifier = raw['classifier']
    _require(raw.get('schema_version') == 'cctv-classifier-result-v1'
             and classifier['label_mapping'] == {'0': 'Normal', '1': 'Collision'}
             and classifier['threshold'] == .5 and classifier['window_seconds'] == 2
             and classifier['stride_seconds'] == 1 and duration >= 2, 'r2_contract')
    windows = classifier['windows']
    starts = [float(i) for i in range(math.floor(duration - 2 + 1e-9) + 1)]
    _require(isinstance(windows, list) and len(windows) == len(starts)
             and normalized['window_count'] == len(windows), 'window_coverage')
    for row, start in zip(windows, starts):
        logits = row['logits']
        _require(isinstance(logits, list) and len(logits) == 2, 'r2_logits')
        for logit in logits:
            _number(logit, -1e9, 1e9)
        delta = max(-700, min(700, logits[0] - logits[1]))
        score = 1 / (1 + math.exp(delta))
        predicted = int(logits[1] > logits[0])
        _require(_close(row['start_s'], start) and _close(row['end_s'], start + 2)
                 and _close(row['collision_probability'], score)
                 and type(row['predicted_class']) is int and row['predicted_class'] == predicted
                 and type(row['is_positive']) is bool and row['is_positive'] == bool(predicted),
                 'window_values')
        requested, observed = row['requested_sample_times_s'], row['sample_times_s']
        _require(isinstance(requested, list) and isinstance(observed, list)
                 and len(requested) == len(observed) == 16, 'r2_sample_count')
        for index, (wanted, actual) in enumerate(zip(requested, observed)):
            _require(_close(wanted, start + (index + .5) * 2 / 16)
                     and start <= _number(actual, 0, duration) < start + 2
                     and abs(actual - wanted) <= .125 + 1e-9,
                     'r2_sample_timing')
        _require(observed == sorted(observed) and len(set(observed)) == 16, 'r2_sample_order')
    positive = any(row['is_positive'] for row in windows)
    _require(bool(raw['events']) == positive
             and normalized['status'] == ('completed' if positive else 'no_candidates')
             and classifier['decision'] == ('Collision' if positive else 'Normal')
             and _close(classifier['max_collision_probability'],
                        max(row['collision_probability'] for row in windows)), 'r2_summary')


def _handoff_classifier(handoff, frozen):
    if frozen['version'] in ('R2', 'LC'):
        from .model_catalog import contract_sha256
        _require(handoff.get('classifier_collision_candidate') is True
                 and 'x3d_collision_candidate' not in handoff, 'r2_handoff_classifier')
        _require(contract_sha256(handoff.get('classifier')) == contract_sha256(frozen['classifier']),
                 'classifier_contract_mismatch')
    else:
        _require(handoff.get('x3d_collision_candidate') is True, 'x3d_handoff_classifier')


def _class_mapping(mapping, version):
    _require(isinstance(mapping, dict), 'class_mapping')
    if version == 'A':
        names = {'0':'Pedestrian', '1':'Car', '2':'Truck', '3':'Bus',
                 '4':'Motorcycle', '5':'Bicycle', '6':'Dynamic'}
        _require(mapping == {'checkpoint_names': names, 'effective_names': names}, 'a_class_mapping')
    else:
        names = {'0':'Car', '1':'Truck', '2':'Bus', '3':'Motorcycle'}
        generic = {str(i):f'class_{i}' for i in range(4)}
        _require(mapping.get('effective_names') == names
                 and mapping.get('checkpoint_names') in (generic,names)
                 and mapping.get('independent_training_metadata_verified') is False
                 and mapping.get('mapping_source') == ('provided_sidecar' if mapping['checkpoint_names'] == generic
                                                       else 'checkpoint_and_sidecar'), 'b_class_mapping')


def _event_base(name, candidate, fps, count, version, display):
    _require(isinstance(name, str) and re.fullmatch('event_[0-9]+', name), 'event_id')
    duration = count / fps
    _number(candidate, 0, duration)
    _require(candidate < duration, 'candidate_outside_source')
    post = 3.0 if version == 'A' else 5.0
    if version == 'A':
        start = max(0, math.floor((candidate - 3) * fps + 1e-7))
        end = min(count, math.ceil((candidate + 3) * fps - 1e-7))
    else:
        start = max(0, round(max(0., candidate - 3) * fps))
        end = max(start + 1, round(min(duration, candidate + 5) * fps))
    _number(display, 0, duration)
    representative = min(count - 1, round(display * fps))
    # B may choose a display frame after its candidate-centered clip. Keep this
    # factual distinction instead of claiming the display always lies in it.
    return dict(event_id=name, evidence_version=SCHEMA, version=version,
                candidate_time_s=candidate, source_start_s=start/fps, source_end_s=end/fps,
                start_frame=start, end_frame_exclusive=end, clip_frame_count=end-start,
                representative_frame=representative, representative_source_time_s=representative/fps,
                display_sample_time_s=display, representative_within_clip=start <= representative < end,
                candidate_clip_time_s=candidate-start/fps, requested_pre_s=3.0, requested_post_s=post,
                pre_context_truncated=candidate-3 < 0, post_context_truncated=candidate+post > duration,
                clip=f'evidence/{name}/clip.mp4',
                frame=f'evidence/{name}/frame.' + ('png' if version == 'A' else 'jpg'),
                frame_role='unannotated_frame' if version == 'A' else 'annotated_frame',
                frame_mime='image/png' if version == 'A' else 'image/jpeg')


def _source_index(root, snapshot, input_sha256, result_sha256, normalized_name='result.json'):
    frozen = validate_profile(snapshot)
    identity = gpu_identity(input_sha256, frozen)
    normalized_path = _file(root, normalized_name)
    _require(_sha(normalized_path) == _digest(result_sha256), 'normalized_result_integrity')
    normalized = _json(normalized_path)
    version = frozen['version']
    _integer(normalized['event_count'], 0, 60)
    _integer(normalized['window_count'], 1, 121)
    _require(normalized['schema_version'] == 'cctv-ab-job-v1'
             and normalized['version'] == version and normalized['input_sha256'] == input_sha256
             and normalized['training'] is False and normalized['gemini_calls'] == 0
             and normalized['embedding_calls'] == 0, 'normalized_identity')
    _require(normalized['raw_result'] == 'raw/result.json'
             and normalized['raw_manifest'] == 'raw/manifest.json', 'raw_paths')
    artifacts = _records(normalized['artifacts'])
    _require(all(name.startswith('raw/') for name in artifacts)
             and {'raw/result.json','raw/runtime.json','raw/vlm_handoffs.json','raw/manifest.json'} <= artifacts.keys(),
             'raw_artifacts_required')
    _verify_files(root, artifacts)
    manifest = _json(_file(root, 'raw/manifest.json'))
    _require(manifest['input_sha256'] == input_sha256, 'raw_input_identity')
    if manifest['schema_version'] == 'cctv-ab-raw-v1':
        _require(manifest['version'] == version, 'raw_version')
    else:
        _require(version == 'A' and manifest['schema_version'] == 'runpod-evidence-v1', 'raw_schema')
    inner = {'raw/' + name: entry for name, entry in _records(manifest['files']).items()}
    _require(inner == {n: r for n, r in artifacts.items() if n != 'raw/manifest.json'}, 'raw_manifest_differs')
    raw = _json(_file(root, 'raw/result.json'))
    runtime = _json(_file(root, 'raw/runtime.json'))
    handoffs = _json(_file(root, 'raw/vlm_handoffs.json'))
    _require(raw['vlm_handoffs'] == handoffs and isinstance(handoffs, list)
             and len(raw['events']) == len(handoffs) == normalized['event_count'] <= 60, 'handoff_mismatch')
    _require(runtime['version'] == version and runtime['input_sha256'] == input_sha256
             and runtime['source_pts_verified'] is True and runtime['training'] is False
             and runtime['gemini_calls'] == 0 and runtime['embedding_calls'] == 0
             and runtime['batch_size'] == frozen['execution_settings']['batch_size'], 'runtime_identity')
    for key in ('python', 'cuda_runtime'):
        _require(runtime[key] == frozen['runtime'][key], 'runtime_environment')
    expected_gpu = frozen['runtime']['gpu']
    _require(valid_gpu_name(runtime['gpu']) if expected_gpu == PORTABLE_GPU
             else runtime['gpu'] == expected_gpu, 'runtime_environment')
    _require(set(runtime['packages']) == {'torch','torchvision','ultralytics','pytorchvideo',
                                         'numpy','opencv-python','av'}
             and all(frozen['runtime']['packages'].get(k) == v
             for k,v in runtime['packages'].items()), 'runtime_packages')
    _require(normalized['gpu'] == runtime['gpu'] and normalized['class_mapping'] == runtime['class_mapping'],
             'runtime_summary')
    _class_mapping(normalized['class_mapping'], version)
    checked = runtime['video_check']
    fps = _number(checked['fps'], .001, 1000)
    count = _integer(checked['decoded_frames'], 2)
    duration = _number(checked['duration_s'], .001, 120)
    _require(_close(duration, count/fps) and _close(raw['duration_s'], duration)
             and _close(raw['fps'], fps) and raw['device'] == 'cuda', 'source_timing')
    _windows(raw, normalized, duration)
    _classifier(raw, frozen)
    _require(normalized['candidate_times_s'] == [e['candidate_time_s'] for e in raw['events']], 'candidate_summary')
    media = {}
    for entry in normalized['media']:
        key = (entry['event_id'], entry['role'])
        _require(key not in media and entry['path'] in artifacts, 'media_record')
        media[key] = entry
    original_index = _json(_file(root, 'raw/evidence.json')) if version == 'A' else None
    if original_index:
        _require(original_index['schema_version'] == 'runpod-evidence-v1'
                 and original_index['pts_verified'] is True and _close(original_index['source_fps'], fps)
                 and len(original_index['events']) == len(raw['events']), 'a_evidence_index')
    events, aliases, seen = [], {}, set()
    for number, (event, handoff) in enumerate(zip(raw['events'], handoffs)):
        name, candidate = event['event_id'], event['candidate_time_s']
        _require(name not in seen and handoff['event_id'] == name
                 and handoff['candidate_time_s'] == candidate,
                 'event_handoff_identity')
        _handoff_classifier(handoff, frozen)
        seen.add(name)
        if version == 'A':
            display = candidate
            _require(handoff['schema_version'] == '1.0' and event['yolo_objects'] == handoff['yolo_objects']
                     and isinstance(event['yolo_objects'], list), 'a_objects')
        else:
            context = event['incident_context']
            _require(handoff['schema_version'] == '2.0' and context == handoff['incident_context']
                     and context['schema_version'] == '2.0'
                     and context['event_id'] == name and context['candidate_time_s'] == candidate
                     and _close(context['video_fps'], fps), 'b_context_identity')
            context_path = f'raw/{name}/{name}_context.json'
            _require(context_path in artifacts and _json(_file(root, context_path)) == context, 'b_context_differs')
            display = context['display_frame_time_s']
        record = _event_base(name, candidate, fps, count, version, display)
        record['handoff'] = deepcopy(handoff)
        if version == 'A':
            old = original_index['events'][number]
            _require(all(old.get(key) == value for key, value in record.items()
                         if key not in {'evidence_version','version','display_sample_time_s',
                                        'representative_within_clip','frame_role','frame_mime','handoff'}),
                     'a_evidence_timing')
            _require(old['yolo_objects'] == event['yolo_objects'], 'a_evidence_objects')
            record['yolo_objects'] = deepcopy(event['yolo_objects'])
            sources = {'full_clip': 'raw/' + old['clip'], 'frame': 'raw/' + old['frame']}
        else:
            record['incident_context'] = deepcopy(context)
            record['roi_clip'] = f'evidence/{name}/roi_clip.mp4'
            record['context'] = f'evidence/{name}/context.json'
            sources = {'full_clip': f'raw/{name}/{name}_full.mp4',
                       'roi_clip': f'raw/{name}/{name}_roi.mp4',
                       'annotated_frame': f'raw/{name}/{name}_objects.jpg'}
            # Old absolute paths are inert metadata; only exact frozen filenames
            # map to verified artifacts. Never open or resolve those old paths.
            for role, source in sources.items():
                old_path = context['files'][role]
                _require(isinstance(old_path, str) and (old_path.endswith('/' + source)
                         or old_path == source.removeprefix('raw/')), 'b_context_path')
            aliases[record['context']] = context_path
        for role, source in sources.items():
            entry = media.pop((name, role), None)
            clip = role in ('full_clip', 'roi_clip')
            _require(entry is not None and entry['path'] == source
                     and type(entry['decoded_frames']) is int
                     and entry['decoded_frames'] == (record['clip_frame_count'] if clip else 1)
                     and entry['pts_verified'] is clip, 'media_coverage')
            codec = ('h264' if version == 'A' else 'mpeg4') if clip else ('png' if version == 'A' else 'mjpeg')
            _require(entry['codec'] == codec, 'media_codec')
            destination = record['clip'] if role == 'full_clip' else record['roi_clip'] if role == 'roi_clip' else record['frame']
            aliases[destination] = source
        events.append(record)
    _require(not media, 'unexpected_media')
    index = dict(schema_version=SCHEMA, analysis_profile=frozen, gpu_key=identity['gpu_key'],
                 input_sha256=input_sha256, normalized_result_sha256=result_sha256,
                 status=normalized['status'], window_count=normalized['window_count'],
                 source_fps=fps, source_frame_count=count, source_duration_s=duration,
                 source_pts_verified=True, time_basis='seconds_from_first_decoded_frame',
                 audio_included=False, events=events, class_mapping=normalized['class_mapping'],
                 note='Candidate and participant outputs require review; no_candidates does not establish normality.')
    return validate_index(index), artifacts, aliases


def validate_index(index, snapshot=None, input_sha256=None):
    """Validate identity/timing/roles, without trusting or opening embedded paths."""
    _require(isinstance(index, dict) and index.get('schema_version') == SCHEMA, 'index_schema')
    _finite_json(index)
    frozen = validate_profile(index['analysis_profile'])
    if snapshot is not None:
        _require(validate_profile(snapshot) == frozen, 'expected_profile')
    digest = _digest(index['input_sha256'])
    if input_sha256 is not None:
        _require(digest == _digest(input_sha256), 'expected_input')
    _require(index['gpu_key'] == gpu_identity(digest, frozen)['gpu_key'], 'gpu_identity')
    _digest(index['normalized_result_sha256'])
    _class_mapping(index['class_mapping'], frozen['version'])
    fps, count = _number(index['source_fps'], .001, 1000), _integer(index['source_frame_count'], 2)
    duration = _number(index['source_duration_s'], .001, 120)
    _require(_close(duration,count/fps) and index['source_pts_verified'] is True
             and index['time_basis'] == 'seconds_from_first_decoded_frame' and index['audio_included'] is False,
             'index_timing')
    _integer(index['window_count'], 1, 121)
    events = index['events']
    _require(isinstance(events,list) and len(events) <= 60
             and index['status'] == ('completed' if events else 'no_candidates'), 'index_state')
    seen = set()
    for event in events:
        version, name = frozen['version'], event['event_id']
        _require(name not in seen, 'duplicate_event')
        seen.add(name)
        expected = _event_base(name,event['candidate_time_s'],fps,count,version,event['display_sample_time_s'])
        _require(all(event.get(k) == v and type(event.get(k)) is type(v) for k,v in expected.items()),
                 'index_event_values')
        handoff = event['handoff']
        _require(handoff['event_id'] == name and handoff['candidate_time_s'] == event['candidate_time_s'], 'index_handoff')
        _handoff_classifier(handoff, frozen)
        if version == 'A':
            _require(handoff['schema_version'] == '1.0' and event['yolo_objects'] == handoff['yolo_objects']
                     and isinstance(event['yolo_objects'],list) and event['display_sample_time_s'] == event['candidate_time_s']
                     and not {'incident_context','roi_clip','context'} & event.keys(), 'index_a_semantics')
        else:
            context = event['incident_context']
            _require(handoff['schema_version'] == '2.0' and context == handoff['incident_context']
                     and context['schema_version'] == '2.0'
                     and context['event_id'] == name and context['candidate_time_s'] == event['candidate_time_s']
                     and context['display_frame_time_s'] == event['display_sample_time_s']
                     and _close(context['video_fps'],fps) and 'yolo_objects' not in event
                     and event['roi_clip'] == f'evidence/{name}/roi_clip.mp4'
                     and event['context'] == f'evidence/{name}/context.json', 'index_b_semantics')
    return deepcopy(index)


def selected_event(index, event_id):
    """Return one detached event with its validated parent identity attached."""
    checked = validate_index(index)
    events = [event for event in checked['events'] if event['event_id'] == event_id]
    _require(len(events) == 1, 'event_not_found')
    return dict(events[0], **{key: checked[key] for key in ('analysis_profile','gpu_key','input_sha256','class_mapping')})


def validate_local(output, manifest=None):
    """Verify packaged bytes and reconstruct the index from original artifacts."""
    root = Path(output)
    saved = _json(_file(root, 'manifest.json'))
    if manifest is not None:
        _require(saved == manifest, 'manifest_argument_mismatch')
    _require(saved['schema_version'] == SCHEMA, 'manifest_schema')
    records = _records(saved['files'])
    required = {'result.json','runtime.json','vlm_handoffs.json','normalized-result.json','evidence.json'}
    _require(required <= records.keys() and 'manifest.json' not in records, 'manifest_required_files')
    _verify_files(root, records)
    index, originals, aliases = _source_index(root,saved['analysis_profile'],saved['input_sha256'],
                                             saved['normalized_result_sha256'],'normalized-result.json')
    _require(saved['gpu_key'] == index['gpu_key'] and _json(_file(root,'evidence.json')) == index,
             'packaged_index_differs')
    for name in ('result.json','runtime.json','vlm_handoffs.json'):
        aliases[name] = 'raw/' + name
    _require(set(records) == set(originals) | set(aliases) | {'normalized-result.json','evidence.json'},
             'manifest_coverage')
    for name, source in aliases.items():
        _require(records[name] == originals[source], 'copied_asset_differs')
    actual = set()
    for path in root.rglob('*'):
        _require(not path.is_symlink(), 'unexpected_symlink')
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    _require(actual - {'manifest.json','s3_receipt.json'} == set(records), 'unlisted_local_file')
    return deepcopy(saved)


def prepare(source_output, destination, *, snapshot, input_sha256, result_sha256):
    """Write one new package after verification; existing directories are refused.

    This copies previously decoded media and reports its recorded PTS verification.
    It performs no new decoding, transcode, inference, publication or DB operation.
    """
    source, target = Path(source_output), Path(destination)
    _require(not target.exists() and not target.is_symlink(), 'destination_exists')
    _require(not target.resolve().is_relative_to(source.resolve()), 'destination_inside_source')
    index, artifacts, aliases = _source_index(source,snapshot,input_sha256,result_sha256)
    target.mkdir(parents=True, exist_ok=False)
    for name in ('result.json','runtime.json','vlm_handoffs.json'):
        aliases[name] = 'raw/' + name
    aliases['normalized-result.json'] = 'result.json'
    for name in sorted(set(artifacts) | set(aliases)):
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with _file(source,aliases.get(name,name)).open('rb') as origin, path.open('xb') as out:
            shutil.copyfileobj(origin,out)
    with (target/'evidence.json').open('x',encoding='utf-8') as out:
        json.dump(index,out,ensure_ascii=False,indent=2,allow_nan=False)
    records = {p.relative_to(target).as_posix(): {'bytes':p.stat().st_size,'sha256':_sha(p)}
               for p in sorted(target.rglob('*')) if p.is_file()}
    manifest = dict(schema_version=SCHEMA,analysis_profile=index['analysis_profile'],
                    gpu_key=index['gpu_key'],input_sha256=input_sha256,
                    normalized_result_sha256=result_sha256,files=records,storage='temporary_container_disk')
    with (target/'manifest.json').open('x',encoding='utf-8') as out:
        json.dump(manifest,out,ensure_ascii=False,indent=2,allow_nan=False)
    validate_local(target,manifest)
    return dict(index=index,manifest=manifest,output=str(target),status='ab_evidence_prepared',
                model_calls=0,media_decodes=0,s3_requests=0)
