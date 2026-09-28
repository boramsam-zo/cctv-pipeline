"""Read saved BE receipts only. No DB connection, remote request or state write."""
import argparse
import json
from pathlib import Path
import re
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.be_worker.common import WorkerError, checked_id, read_json

STATES = {'prepared', 'submitting', 'waiting', 'submission_unknown', 'submission_rejected',
          'storage_not_ready', 'verification_failed', 'provider_failed', 'evidence_ready',
          'handoff_ready', 'no_candidates'}
SCENE_STATES = {'media_verified', 'describing', 'description_failed', 'description_ready',
                'database_pending', 'description_stored'}


def count(value):
    return value if type(value) is int and value >= 0 else 0


def code(value):
    # These are structured application codes, never exception messages or URLs.
    if isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', value):
        return value
    return None


def read_inside(path, root):
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise WorkerError('flow_status_invalid_receipt_path')
    value = read_json(path)
    if not isinstance(value, dict):
        raise WorkerError('flow_status_invalid_receipt')
    return value


def summarize(directory, root):
    state = read_inside(directory/'state.json', root)
    identifier = checked_id(state.get('worker_job_id'))
    provider = state.get('provider_job_id')
    if provider is not None:
        provider = checked_id(provider)
    stage = state.get('status') if state.get('status') in STATES else 'unknown'
    events = []
    scene_events = state.get('scene_events', {})
    if not isinstance(scene_events, dict):
        raise WorkerError('flow_status_invalid_scene_records')
    probe_requests = 0
    for event_id, saved in sorted(scene_events.items()):
        if not re.fullmatch(r'event_[0-9]{3,}', event_id) or not isinstance(saved, dict):
            raise WorkerError('flow_status_invalid_scene_records')
        scene_stage = saved.get('status') if saved.get('status') in SCENE_STATES else 'unknown'
        latest_probe = None
        event_dir = directory/'scenes'/event_id
        for receipt_path in sorted((event_dir/'text-probes').glob('*/request.json')):
            request = read_inside(receipt_path, root)
            if (request.get('worker_job_id'), request.get('provider_job_id'), request.get('event_id')) != (identifier, provider, event_id):
                raise WorkerError('flow_status_probe_identity_mismatch')
            probe_requests += count(request.get('generation_requests'))
            result_path = receipt_path.with_name('result.json')
            probe = read_inside(result_path, root) if result_path.exists() else request
            if (probe.get('worker_job_id'), probe.get('provider_job_id'), probe.get('event_id')) != (identifier, provider, event_id):
                raise WorkerError('flow_status_probe_identity_mismatch')
            candidate = {'status': code(probe.get('status')) or 'incomplete',
                         'error_code': code(probe.get('error_code')),
                         'created_at': str(request.get('created_at', ''))}
            if latest_probe is None or candidate['created_at'] > latest_probe['created_at']:
                latest_probe = candidate
        if latest_probe:
            latest_probe.pop('created_at')
        events.append({'event_id': event_id, 'media_verified_record': saved.get('media_bytes_verified') is True,
                       'scene_state': scene_stage, 'error_code': code(saved.get('error_code')),
                       'scene_request_intents': count(saved.get('gemini_api_calls')),
                       'database_stored_record': saved.get('database_loaded') is True and scene_stage == 'description_stored',
                       'last_text_probe': latest_probe})
    if stage in {'submitting', 'submission_unknown', 'storage_not_ready', 'verification_failed'}:
        next_step = '제출/저장 상태부터 확인. 같은 영상을 자동 재제출하지 않음'
    elif stage == 'prepared':
        next_step = 'Runpod URL·토큰·S3 준비 확인 후 dispatch'
    elif stage == 'waiting':
        next_step = '기존 Runpod 작업 ID의 상태 조회 재개'
    elif stage == 'provider_failed':
        next_step = '기존 worker.log로 실패 단계 확인. S3 업로드만 실패했다면 기존 결과 복구'
    elif stage == 'evidence_ready':
        next_step = 'collect로 S3 근거 인계 확인'
    elif stage == 'no_candidates':
        next_step = '후보 없음 기록 검토. 실패나 정상 확정과 구분'
    elif stage == 'handoff_ready' and any(e['error_code'] == 'gemini_http_503' for e in events):
        next_step = 'Gemini 503 원인/가용성 확인 후 명시적 재개. Runpod 재분석 불필요'
    elif stage == 'handoff_ready' and any(e['scene_state'] == 'database_pending' for e in events):
        next_step = '기존 Gemini 결과로 DB 저장만 재개'
    elif stage == 'handoff_ready' and events and all(e['database_stored_record'] for e in events):
        next_step = '장면별 DB 저장 기록 확인됨. RAG/보고 연결은 별도 확인'
    elif stage == 'handoff_ready':
        next_step = '사건별 scene-fetch → scene-describe 확인. 이미 실패한 요청은 재시도 규칙 확인'
    else:
        next_step = '기존 오류 기록 확인'
    return {'worker_job_id': identifier, 'provider_job_id': provider, 'queue_state': stage,
            'submit_request_intents': count(state.get('post_attempts')), 'poll_count': count(state.get('poll_count')),
            's3_uploaded_record': isinstance(state.get('s3'), dict) and state['s3'].get('s3_uploaded') is True,
            'event_count': count(state.get('event_count')), 'scene_request_intents': count(state.get('gemini_api_calls')),
            'text_probe_request_intents': probe_requests, 'events': events, 'next_step': next_step}


def report(spool, identifier=None):
    if identifier is not None:
        checked_id(identifier)
    root = Path(spool).resolve()
    if not root.is_dir():
        raise WorkerError('flow_status_spool_not_found')
    jobs = []
    seen = set()
    for directory in sorted(root.iterdir()):
        if not re.fullmatch(r'[a-f0-9]{32}', directory.name):
            continue
        if directory.is_symlink():
            raise WorkerError('flow_status_invalid_receipt_path')
        if not (directory/'state.json').is_file():
            continue
        job = summarize(directory, root)
        if job['worker_job_id'] in seen:
            raise WorkerError('flow_status_duplicate_job')
        seen.add(job['worker_job_id'])
        if identifier is None or job['worker_job_id'] == identifier:
            jobs.append(job)
    if identifier is not None and not jobs:
        raise WorkerError('flow_status_job_not_found')
    return {'schema_version': 'be-flow-status-v1', 'source': 'saved_local_receipts',
            'live_db_checked': False, 'remote_requests': 0, 'state_modified': False, 'jobs': jobs}


def render(result):
    lines = ['저장된 BE 기록 조회 — DB/S3/Runpod/Gemini 실시간 확인 없음']
    if not result['jobs']:
        lines.append('작업 기록 없음')
    for job in result['jobs']:
        lines += ['', f"BE 작업: {job['worker_job_id']}",
                  f"Runpod 작업: {job['provider_job_id'] or '미접수'}",
                  f"대기열 기록: {job['queue_state']}",
                  f"제출 의도 {job['submit_request_intents']}회 / 상태 조회 {job['poll_count']}회",
                  f"S3 업로드 기록: {'있음' if job['s3_uploaded_record'] else '미확인'} / 사건 {job['event_count']}개",
                  f"장면 요청 의도 {job['scene_request_intents']}회 / 별도 텍스트 진단 {job['text_probe_request_intents']}회"]
        for e in job['events']:
            lines += [f"  {e['event_id']}: 미디어검증={e['media_verified_record']}, 장면={e['scene_state']}, DB저장={e['database_stored_record']}",
                      f"    오류: {e['error_code'] or '없음'}"]
            if e['last_text_probe']:
                p = e['last_text_probe']
                lines.append(f"    마지막 텍스트 진단: {p['status']} / {p['error_code'] or '없음'}")
        lines.append('다음: '+job['next_step'])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spool', type=Path, required=True)
    parser.add_argument('--worker-job-id')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        value = report(args.spool, args.worker_job_id)
        print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else render(value))
        return 0
    except (WorkerError, OSError, ValueError, TypeError):
        print(json.dumps({'error': 'flow_status_unavailable_check_saved_receipts', 'state_modified': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
