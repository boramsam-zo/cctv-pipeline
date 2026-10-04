"""Read-only normalization of saved CCTV jobs for the control-room UI.

This module never submits jobs, contacts a provider, decodes videos or writes to
the business database. A recorded asset is distinct from a playable local file.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAVED_SNAPSHOT = Path(__file__).with_name('data') / 'saved-control-room.json'
KST = ZoneInfo('Asia/Seoul')
TEAM_REPORT = 'cctv-team-report-v1'
STATUS_LABELS = {
    'completed': '처리 완료', 'comparison_completed': '근거 비교 완료',
    'paused': '확인 필요 · 일시중지', 'handoff_ready': '근거 인계 완료',
    'no_candidates': '후보 없음 · 정상 판정 아님',
    'prepared': '대기', 'queued': '대기', 'submitting': '접수 중',
    'waiting': '분석·저장 중', 'evidence_ready': '근거 수집 대기',
    'describing': '장면 설명 중', 'reporting': '보고 작성 중',
    'provider_failed': '분석·저장 실패', 'verification_failed': '근거 확인 실패',
    'storage_not_ready': '저장 확인 필요', 'submission_unknown': '접수 확인 필요',
    'submission_rejected': '접수 거절 · 확인 필요',
}
TYPE_LABELS = {
    'collision': '충돌', 'side_collision': '측면 충돌', 'multi_vehicle': '다중 차량',
    'rear_end': '추돌', 'rollover': '전복', 'single_vehicle': '차량 단독',
}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, (list, tuple)) else []


def _timestamp(value):
    """Return an aware timestamp; unrecorded timezone remains unknown."""
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def format_kst(value):
    timestamp = _timestamp(value)
    return timestamp.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S KST') if timestamp else None


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float('inf') else None


def _state(job):
    flow = _dict(job.get('flow'))
    if flow.get('paused') is True or flow.get('status') == 'paused':
        return 'paused'
    return str(flow.get('status') or job.get('stage') or 'unknown')


def unique_jobs(jobs, selections=()):
    """The same worker job in /jobs and /selections counts exactly once.

    /jobs is authoritative for the job body. Selection-only flow and MLflow
    view fields are added on a copy when the job row does not contain them.
    """
    selected = {}
    for selection in _list(selections):
        for job in _list(_dict(selection).get('jobs')):
            identifier = _dict(job).get('worker_job_id')
            if isinstance(identifier, str) and identifier:
                selected.setdefault(identifier, job)
    result = {}
    for job in _list(jobs):
        if not isinstance(job, dict):
            continue
        identifier = job.get('worker_job_id')
        if not isinstance(identifier, str) or not identifier or identifier in result:
            continue
        row = dict(job)
        for field in ('flow', 'mlflow'):
            if not row.get(field) and _dict(selected.get(identifier)).get(field):
                row[field] = selected[identifier][field]
        result[identifier] = row
    for identifier, job in selected.items():
        result.setdefault(identifier, dict(job))
    return list(result.values())


def event_ids(job):
    identifiers = set(_dict(job.get('scene_events')))
    for item in _list(job.get('evidence_events')) + _list(job.get('descriptions')):
        identifier = _dict(item).get('event_id')
        if isinstance(identifier, str) and re.fullmatch(r'event_[0-9]+', identifier):
            identifiers.add(identifier)
    return sorted(identifier for identifier in identifiers
                  if isinstance(identifier, str) and re.fullmatch(r'event_[0-9]+', identifier))


def _description(job, event_id, analysis_id=None):
    matches = [item for item in _list(job.get('descriptions'))
               if _dict(item).get('event_id') == event_id]
    if analysis_id is not None:
        selected = next((item for item in matches if _dict(item).get('analysis_id') == analysis_id), None)
        if selected is None:
            raise ValueError('selected_analysis_not_in_event')
        return selected
    return next((item for item in matches if _dict(_dict(item).get('payload')).get('schema_version')
                 == 'cctv-team-scene-v1'), matches[-1] if matches else None)


def _reports(job, description, analysis_id=None):
    event_id = _dict(description).get('event_id')
    analysis_ids = {_dict(item).get('analysis_id') for item in _list(job.get('descriptions'))
                    if _dict(item).get('event_id') == event_id and _dict(item).get('analysis_id')}
    if analysis_id is not None:
        analysis_ids = {analysis_id}
    if not event_id or not analysis_ids:
        return []
    return [item for item in _list(job.get('reports'))
            if _dict(_dict(item).get('settings')).get('analysis_id') in analysis_ids
            or _dict(_dict(_dict(item).get('state')).get('result')).get('analysis_id') in analysis_ids]


def _report_revisions(job, description, original):
    """Keep reviewed copies tied to their immutable source and scene identity."""
    if not original:
        return [], []
    source_state = _dict(original.get('state'))
    source = _dict(source_state.get('result'))
    source_id = original.get('report_id')
    source_sha = source_state.get('result_sha256')
    def fingerprint(value):
        # Match the persisted report contract (spaces are part of these bytes).
        try:
            return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                             allow_nan=False).encode()).hexdigest()
        except (TypeError, ValueError, RecursionError):
            return None

    revisions, rejected = [], []
    if original.get('quality_review_error'):
        rejected.append('stored_report_review_unavailable')
    seen = {source_id}
    for candidate in _list(original.get('quality_reviews')):
        row = _dict(candidate)
        review, settings, state = (_dict(row.get(key)) for key in ('review', 'settings', 'state'))
        result = _dict(state.get('result'))
        identifier = row.get('report_id')
        valid = (
            source.get('schema_version') == TEAM_REPORT
            and source_state.get('status') == 'report_stored'
            and isinstance(source_sha, str) and re.fullmatch(r'[a-f0-9]{64}', source_sha)
            and source_sha == fingerprint(source) and source.get('report_id') == source_id
            and isinstance(identifier, str)
            and re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', identifier)
            and identifier not in seen
            and review.get('schema') == 'cctv-team-report-review-v1'
            and review.get('source_report_id') == settings.get('source_report_id') == source_id
            and review.get('source_result_sha256') == source_sha
            and isinstance(review.get('policy_sha256'), str)
            and re.fullmatch(r'[a-f0-9]{64}', review['policy_sha256'])
            and type(review.get('excluded_count')) is int and review['excluded_count'] >= 0
            and type(review.get('model_calls')) is int and review['model_calls'] == 0
            and state.get('status') == 'report_stored'
            and isinstance(state.get('result_sha256'), str)
            and re.fullmatch(r'[a-f0-9]{64}', state['result_sha256'])
            and state['result_sha256'] == fingerprint(result)
            and result.get('schema_version') == TEAM_REPORT
            and result.get('report_id') == identifier
            and _dict(description).get('analysis_id') is not None
            and result.get('analysis_id') == settings.get('analysis_id')
                == source.get('analysis_id') == _dict(description).get('analysis_id')
            and result.get('worker_job_id') == source.get('worker_job_id') == job.get('worker_job_id')
            and settings.get('worker_job_id', job.get('worker_job_id')) == job.get('worker_job_id')
            and result.get('event_id') == source.get('event_id') == _dict(description).get('event_id')
            and result.get('scene_sha256') == source.get('scene_sha256')
            and settings.get('scene_sha256', source.get('scene_sha256')) == source.get('scene_sha256')
            and result.get('provider_job_id') == source.get('provider_job_id')
        )
        if valid:
            revisions.append(row)
            seen.add(identifier)
        else:
            rejected.append('stored_report_review_identity_mismatch')
    # Reviewed copies precede the source; the API exposes supported policies only.
    return list(reversed(revisions)) + [original], rejected


def normalize_case(job, event_id=None, analysis_id=None, report_id=None):
    identifiers = event_ids(job)
    if event_id is None:
        event_id = identifiers[0] if identifiers else None
    description = _description(job, event_id, analysis_id)
    payload = _dict(_dict(description).get('payload'))
    scene = _dict(payload.get('team_event') or payload.get('description'))
    reports = _reports(job, description, analysis_id)
    # Prefer the agreed team report when legacy and team reports coexist.
    original = next((item for item in reports if _dict(_dict(item.get('state')).get('result'))
                   .get('schema_version') == TEAM_REPORT), reports[-1] if reports else None)
    revisions, review_errors = _report_revisions(job, description, original)
    report = revisions[0] if revisions else None
    if report_id is not None:
        report = next((item for item in revisions if item.get('report_id') == report_id), None)
        if report is None:
            raise ValueError('selected_report_revision_not_in_case')
    report_json = _dict(_dict(_dict(report).get('state')).get('result')).get('report_json')
    report_json = _dict(report_json)
    evidence = next((item for item in _list(job.get('evidence_events'))
                     if _dict(item).get('event_id') == event_id), {})
    source = _dict(payload.get('source'))
    location = _dict(scene.get('location'))
    summary = scene.get('description') or scene.get('summary') or ''
    summary = summary if isinstance(summary, str) else ''
    types = _list(_dict(scene.get('accident')).get('forms') or scene.get('incident_types'))
    status = _state(job)
    worker_id = job['worker_job_id']
    return {
        'case_id': worker_id + (':' + event_id if event_id else ''),
        'worker_job_id': worker_id, 'event_id': event_id, 'job': job,
        'title': (summary[:68] + ('…' if len(summary) > 68 else '')) if summary
                 else '사고 후보 · ' + worker_id[:8] if event_id else '영상 작업 · ' + worker_id[:8],
        'status': status, 'status_label': STATUS_LABELS.get(status, '상태 확인 필요'),
        'scene': scene, 'description': description, 'reports': reports, 'report': report,
        'report_revisions': revisions, 'report_review_errors': review_errors,
        'report_review': _dict(_dict(report).get('review')),
        'analysis_id': _dict(description).get('analysis_id'),
        'summary': summary, 'observations': _list(scene.get('evidence') or scene.get('observations')),
        'uncertainties': _list(scene.get('uncertainties')),
        'verification_needed': _list(report_json.get('verification_needed')),
        'agency_responses': _list(report_json.get('agency_responses')),
        'references': _list(report_json.get('references')),
        'type_tags': [str(item) for item in types],
        'type_labels': [TYPE_LABELS.get(str(item), str(item)) for item in types],
        'human_review': payload.get('human_review') or None,
        'location': location.get('registered_location'), 'camera_id': location.get('camera_id'),
        'incident_at': scene.get('incident_at'),
        'submitted_at': job.get('submitted_at'), 'submitted_at_kst': format_kst(job.get('submitted_at')),
        'version': job.get('version') or _dict(job.get('analysis_profile')).get('version'),
        'candidate_time_s': evidence.get('candidate_time_s', source.get('candidate_time_s')),
        'source_start_s': evidence.get('source_start_s', source.get('source_start_s')),
        'source_end_s': evidence.get('source_end_s', source.get('source_end_s')),
        'elapsed_seconds': _number(_dict(job.get('flow')).get('elapsed_seconds')),
    }


def build_statistics(jobs):
    """Record counts, not model accuracy, unique cameras or confirmed crashes."""
    rows = unique_jobs(jobs)
    states = Counter(_state(job) for job in rows)
    hours, dates, unknown_times = Counter(), Counter(), 0
    reports, stored_reports, events, types = set(), set(), set(), Counter()
    for job in rows:
        timestamp = _timestamp(job.get('submitted_at'))
        if timestamp:
            local = timestamp.astimezone(KST)
            hours[local.hour] += 1
            dates[local.date().isoformat()] += 1
        else:
            unknown_times += 1
        for report in _list(job.get('reports')):
            report = _dict(report)
            identifier = report.get('report_id')
            if identifier:
                reports.add(identifier)
                if _dict(report.get('state')).get('status') == 'report_stored':
                    stored_reports.add(identifier)
        for event_id in event_ids(job):
            events.add((job['worker_job_id'], event_id))
            case = normalize_case(job, event_id)
            types.update(set(case['type_tags']))
    return {
        'job_count': len(rows), 'completed_count': (states['completed'] + states['comparison_completed']
                                                    + states['no_candidates']),
        'attention_count': sum(count for state, count in states.items()
                               if state in ('paused', 'provider_failed', 'verification_failed',
                                            'storage_not_ready', 'submission_unknown', 'submission_rejected')),
        'states': dict(sorted(states.items())), 'report_count': len(reports),
        'stored_report_count': len(stored_reports), 'candidate_event_count': len(events),
        'type_tags': dict(sorted(types.items())), 'hour_counts': dict(sorted(hours.items())),
        'date_counts': dict(sorted(dates.items())), 'unknown_submission_time_count': unknown_times,
        'timezone': 'Asia/Seoul', 'unit': 'unique_worker_job',
        'confirmed_accident_count': None, 'connected_camera_count': None,
    }


def normalize_snapshot(jobs, selections=(), metadata=None):
    rows = unique_jobs(jobs, selections)
    rows.sort(key=lambda job: job.get('submitted_at') or '', reverse=True)
    cases = [normalize_case(job, event_id) for job in rows for event_id in (event_ids(job) or [None])]
    cases.sort(key=lambda case: case.get('submitted_at') or '', reverse=True)
    return {'jobs': rows, 'selections': list(_list(selections)), 'cases': cases,
            'stats': build_statistics(rows), 'metadata': dict(metadata or {})}


def public_snapshot(jobs, selections, metadata, local_media=()):
    """Project the large receipt without changing saved VLM or report results.

    The event's frame-by-frame tracking context is not needed by this UI. Raw
    VLM descriptions and all report JSON/Markdown are copied without edits.
    Source receipts remain the authority for the omitted detector context.
    """
    rows = json.loads(json.dumps(unique_jobs(jobs, selections)))
    event_fields = {
        'event_id', 'version', 'evidence_version', 'candidate_time_s',
        'source_start_s', 'source_end_s', 'start_frame', 'end_frame_exclusive',
        'clip_frame_count', 'representative_frame', 'representative_source_time_s',
        'display_sample_time_s', 'representative_within_clip', 'candidate_clip_time_s',
        'requested_pre_s', 'requested_post_s', 'pre_context_truncated',
        'post_context_truncated', 'clip', 'roi_clip', 'frame', 'frame_role', 'frame_mime',
    }
    for job in rows:
        if 'evidence_events' in job:
            job['evidence_events'] = [{key: value for key, value in _dict(event).items()
                                      if key in event_fields}
                                     for event in _list(job['evidence_events'])]
        for match in local_media:
            if _dict(match).get('job') != job['worker_job_id']:
                continue
            kind = match['kind']
            target = job.setdefault('local_media', {})
            if kind not in ('source', 'thumbnail'):
                target = target.setdefault(match['event'], {})
            target.setdefault(kind, {key: match[key] for key in ('path', 'sha256', 'bytes', 'source_sha256',
                                                                'role', 'mime', 'frame_time_s', 'receipt')
                                     if key in match})
            if kind == 'source':
                job['input_bytes'] = match['bytes']
    groups = [{key: value for key, value in _dict(selection).items() if key != 'jobs'}
              for selection in _list(selections)]
    info = dict(metadata)
    info.update(source_type='saved_snapshot', snapshot_projection={
        'deduplicated_by': 'worker_job_id',
        'omitted': ['selection.jobs duplicate bodies', 'evidence_events detector tracking context'],
        'unchanged': ['descriptions including VLM facts', 'reports including report JSON and Markdown'],
        'local_media_identity': 'exact SHA256 and byte count match to recorded input/asset',
    })
    return {'metadata': info, 'jobs': rows, 'selections': groups}


def load_saved_snapshot(path=None):
    """Load bundled public data or an explicitly selected saved API receipt."""
    target = Path(path) if path is not None else SAVED_SNAPSHOT
    value = json.loads(target.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('saved_snapshot_invalid')
    if isinstance(value.get('stdout'), str):
        payload = json.loads(value['stdout'])
        metadata = {'source_type': 'saved_snapshot', 'recorded_at_utc': value.get('recorded_at_utc'),
                    'source': target.name, 'model_calls_by_reader': value.get('model_calls_by_reader')}
    else:
        payload = value
        metadata = dict(_dict(value.get('metadata')))
        metadata.setdefault('source_type', 'saved_snapshot')
    if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
        raise ValueError('saved_snapshot_jobs_missing')
    return normalize_snapshot(payload['jobs'], payload.get('selections', []), metadata)


def safe_local_path(value, root=None):
    """Only an existing file under the explicitly allowed project root is used."""
    if not isinstance(value, str) or not value or '\x00' in value or '://' in value:
        return None
    allowed = Path(root) if root is not None else PROJECT_ROOT
    allowed = allowed.resolve()
    candidate = Path(value)
    if any(part == '..' for part in candidate.parts):
        return None
    candidate = (candidate if candidate.is_absolute() else allowed / candidate).resolve()
    try:
        candidate.relative_to(allowed)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _verified_file(value, expected_sha, expected_bytes, root):
    path = safe_local_path(value, root)
    if path is None or not isinstance(expected_sha, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha):
        return None
    if expected_bytes is not None and path.stat().st_size != expected_bytes:
        return None
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return path if digest.hexdigest() == expected_sha else None


def media_reference(job, event_id, kind, root=None):
    """Resolve a recorded media identity without network access or conversion.

    A relative S3 key is metadata only. An API path requires a separate explicit
    read by the UI; it does not imply the server or asset is currently available.
    """
    kind = 'source' if kind == 'original' else kind
    if kind not in ('source', 'thumbnail', 'clip', 'roi_clip', 'frame', 'clip_preview', 'roi_clip_preview'):
        raise ValueError('invalid_media_kind')
    identifier = job.get('worker_job_id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('invalid_worker_job_id')
    if kind not in ('source', 'thumbnail') and (not isinstance(event_id, str)
                                               or not re.fullmatch(r'event_[0-9]+', event_id)):
        raise ValueError('invalid_event_id')
    original_kind = kind.removesuffix('_preview')
    payload = _dict(_dict(_description(job, event_id)).get('payload'))
    source = _dict(payload.get('source'))
    asset = _dict(_dict(source.get('assets')).get(original_kind))
    local = _dict(job.get('local_media'))
    mapping = _dict(local.get(event_id)) if kind not in ('source', 'thumbnail') else local
    record = mapping.get(kind)
    record = {'path': record} if isinstance(record, str) else _dict(record)
    if kind == 'thumbnail':
        linked = (record.get('source_sha256') == job.get('input_sha256')
                  and record.get('role') == 'source_first_frame_thumbnail'
                  and record.get('mime') == 'image/jpeg'
                  and isinstance(record.get('path'), str)
                  and Path(record['path']).suffix.lower() in ('.jpg', '.jpeg'))
        expected_sha = record.get('sha256') if linked else None
        expected_bytes = record.get('bytes')
        recorded, api_path = bool(record), None
    elif kind == 'source':
        expected_sha, expected_bytes = job.get('input_sha256'), job.get('input_bytes')
        recorded = bool(expected_sha)
        api_path = '/jobs/' + identifier + '/source'
    else:
        expected_sha, expected_bytes = asset.get('sha256'), asset.get('bytes')
        # Previews are separately derived files; never pass off original bytes as a preview.
        if kind.endswith('_preview'):
            expected_sha = record.get('sha256') if record.get('source_sha256') == expected_sha else None
            expected_bytes = record.get('bytes')
        recorded = bool(asset)
        api_path = f'/jobs/{identifier}/media/{event_id}/{kind}'
    path = _verified_file(record.get('path'), expected_sha, expected_bytes, root)
    if kind == 'thumbnail' and path is not None:
        with path.open('rb') as stream:
            if stream.read(3) != b'\xff\xd8\xff':
                path = None
    # A missing local copy is distinct from an existing file that failed integrity.
    integrity_failed = safe_local_path(record.get('path'), root) is not None and path is None
    return {
        'kind': kind, 'status': ('local_available' if path else 'integrity_failed' if integrity_failed
                                  else 'api_read_required' if recorded and api_path else 'unavailable'),
        'local_path': str(path) if path else None, 'api_path': api_path if recorded else None,
        'asset_recorded': recorded, 'sha256': expected_sha, 'bytes': expected_bytes,
        'source_start_s': source.get('source_start_s'), 'source_end_s': source.get('source_end_s'),
    }


def historical_frame_reference(job, event_id, root=None):
    """Verify a separate past-analysis image; never replace a current frame asset.

    The same source, event timing and artifact manifests must agree. The image
    remains a reference from the past run, outside the current VLM/report input.
    This reads saved JSON and bytes only, with no decoding or provider calls.
    """
    reference = _dict(_dict(job.get('historical_frame_references')).get(event_id))
    result = {'status': 'unavailable', 'local_path': None, 'api_path': None,
              'origin': 'past_analysis', 'current_vlm_evidence': False,
              'label': '동일 원본의 과거 B 분석 참고 · 박스 주석 정지이미지'}
    if not reference:
        return result
    current = next((item for item in _list(job.get('evidence_events'))
                    if _dict(item).get('event_id') == event_id), None)
    current_payload = _dict(_dict(_description(job, event_id)).get('payload'))
    current_frame = _dict(_dict(_dict(current_payload.get('source')).get('assets')).get('frame'))
    if (reference.get('worker_job_id') != job.get('worker_job_id')
            or reference.get('event_id') != event_id
            or reference.get('input_sha256') != job.get('input_sha256') or current is None
            or reference.get('current_frame_sha256') != current_frame.get('sha256')):
        return dict(result, status='identity_mismatch', error_code='historical_source_or_event_mismatch')
    files = {}
    for role in ('normalized_result', 'raw_result', 'manifest', 'context', 'image'):
        record = _dict(_dict(reference.get('files')).get(role))
        path = safe_local_path(record.get('path'), root)
        if path is None:
            return dict(result, error_code='historical_reference_local_file_unavailable')
        path = _verified_file(record.get('path'), record.get('sha256'), record.get('bytes'), root)
        if path is None:
            return dict(result, status='integrity_failed', error_code='historical_reference_file_changed')
        files[role] = path
    try:
        normalized = json.loads(files['normalized_result'].read_text(encoding='utf-8'))
        raw = json.loads(files['raw_result'].read_text(encoding='utf-8'))
        manifest = json.loads(files['manifest'].read_text(encoding='utf-8'))
        context = json.loads(files['context'].read_text(encoding='utf-8'))
        old_event = next(item for item in raw['events'] if item['event_id'] == event_id)
        if (normalized['input_sha256'] != job['input_sha256']
                or manifest['input_sha256'] != job['input_sha256']
                or normalized['version'] != 'B' or manifest['version'] != 'B'
                or (job.get('version') or _dict(job.get('analysis_profile')).get('version')) != 'B'
                or context != old_event['incident_context'] or context['event_id'] != event_id):
            raise ValueError('historical_source_or_event_mismatch')
        fps = float(raw['fps'])
        duration = float(raw['duration_s'])
        candidate = float(old_event['candidate_time_s'])
        display = float(context['display_frame_time_s'])
        if fps <= 0 or duration <= 0 or not (0 <= candidate < duration) or not (0 <= display < duration):
            raise ValueError('historical_timing_invalid')
        # Frozen B selection rule: sampled display target -> nearest source frame.
        old_timing = {'candidate_time_s': candidate,
                      'source_start_s': round(max(0., candidate - 3.) * fps) / fps,
                      'source_end_s': round(min(duration, candidate + 5.) * fps) / fps,
                      'representative_source_time_s': round(display * fps) / fps}
        for key, value in old_timing.items():
            observed = _number(current.get(key))
            if observed is None or abs(observed - value) > 1e-6:
                raise ValueError('historical_current_timing_mismatch')
        if abs(float(context['candidate_time_s']) - candidate) > 1e-6:
            raise ValueError('historical_timing_invalid')
        if (_number(current.get('representative_frame')) != round(display * fps)
                or _number(current.get('display_sample_time_s')) is None
                or abs(float(current['display_sample_time_s']) - display) > 1e-6
                or abs(float(context['video_fps']) - fps) > 1e-6):
            raise ValueError('historical_current_timing_mismatch')
        image_record = reference['files']['image']
        image_relative = files['image'].relative_to(files['manifest'].parent).as_posix()
        manifest_image = next(item for item in manifest['files'] if item['path'] == image_relative)
        normalized_image = next(item for item in normalized['artifacts']
                                if item['path'] == 'raw/' + image_relative)
        if (manifest_image['sha256'] != image_record['sha256']
                or manifest_image['bytes'] != image_record['bytes']
                or normalized_image['sha256'] != image_record['sha256']
                or normalized_image['bytes'] != image_record['bytes']
                or not context['files']['annotated_frame'].endswith('/' + image_relative)
                or reference.get('mime') != 'image/jpeg'
                or reference.get('role') != 'past_annotated_frame'):
            raise ValueError('historical_image_manifest_mismatch')
        with files['image'].open('rb') as stream:
            if stream.read(3) != b'\xff\xd8\xff':
                raise ValueError('historical_image_mime_mismatch')
    except (KeyError, TypeError, ValueError, StopIteration):
        return dict(result, status='identity_mismatch', error_code='historical_reference_identity_mismatch')
    provenance = {
        'origin': 'past_analysis', 'run_id': reference.get('run_id'),
        'run_date': reference.get('run_date'), 'run_date_basis': reference.get('run_date_basis'),
        'analysis_started_at': None, 'input_sha256': job['input_sha256'],
        'event_id': event_id, 'candidate_time_s': candidate, 'display_sample_time_s': display,
        'representative_frame': round(display * fps), 'representative_source_time_s': round(display * fps) / fps,
        'source_frame_time_is_code_derived': True, 'image_pts_verified': False,
        'current_vlm_evidence': False, 'current_frame_sha256': reference.get('current_frame_sha256'),
        'image_sha256': image_record['sha256'], 'image_bytes': image_record['bytes'],
        'normalized_result': reference['files']['normalized_result'],
        'manifest': reference['files']['manifest'], 'image': image_record,
    }
    return dict(result, status='local_available', local_path=str(files['image']), provenance=provenance,
                sha256=image_record['sha256'], bytes=image_record['bytes'], mime='image/jpeg',
                run_id=reference.get('run_id'), run_date=reference.get('run_date'),
                run_date_basis=reference.get('run_date_basis'), analysis_started_at=None,
                display_sample_time_s=display, representative_frame=round(display * fps),
                source_frame_time_is_code_derived=True, image_pts_verified=False,
                current_frame_sha256=reference.get('current_frame_sha256'), **old_timing)
