"""Read-only demo status and bounded admission over the existing worker ledger."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys

from . import demo_session, hour_session
from .common import WorkerError


# Reuse the BE image's installed PyAV. Metadata only: no frame decode, model,
# external process binary, network protocol, or package installation.
_PYAV_METADATA = r'''
import av,json,math,sys
with av.open(sys.argv[1], mode='r', format='mov', options={'protocol_whitelist':'file,pipe','enable_drefs':'0'}) as container:
    durations=[]
    if container.duration is not None:
        durations.append(float(container.duration / av.time_base))
    streams=[]
    for stream in container.streams:
        streams.append({'codec_type':stream.type})
        if stream.type in ('video','audio') and stream.duration is not None and stream.time_base is not None:
            durations.append(float(stream.duration * stream.time_base))
    if not durations or any(not math.isfinite(value) or value < 0 for value in durations):
        raise ValueError('invalid_media_duration')
    print(json.dumps({'format':{'duration':max(durations)},'streams':streams},allow_nan=False))
'''


def status(conn):
    marker = hour_session.current_marker()
    if not demo_session.selected(marker):
        return {'active': False, 'can_submit': False, 'reason': 'demo_not_started'}
    p = hour_session.get(conn)
    now = hour_session.clock(conn)
    active = not p['stopped'] and p['started_at'] <= now < p['deadline_at']
    busy = conn.execute('''SELECT count(*) AS count FROM cctv_jobs.analysis_tasks
        WHERE worker_job_id = ANY(%s) AND finished_at IS NULL AND NOT paused''',
        (list(p['bindings']),)).fetchone()['count'] if p['bindings'] else 0
    remaining = max(0, p['max_jobs'] - len(p['bindings']))
    return {'active': active, 'can_submit': active and remaining > 0 and busy == 0,
            'session_name': p['name'], 'remaining_jobs': remaining,
            'used_jobs': len(p['bindings']), 'max_jobs': p['max_jobs'],
            'busy_jobs': busy, 'max_concurrent_jobs': 1,
            'expires_at': datetime.fromtimestamp(p['deadline_at'], timezone.utc).isoformat(),
            'expires_at_epoch': p['deadline_at'], 'server_time_epoch': now,
            'max_upload_bytes': p['max_upload_bytes'], 'max_video_seconds': p['max_video_seconds'],
            'allowed_selections': p['versions'], 'candidate_policy': demo_session.CANDIDATE_POLICY}


def require_active(conn):
    p = hour_session.get(conn)
    if not demo_session.selected(p):
        raise WorkerError('demo_not_started')
    hour_session.active(conn, p)
    return p


def validate_video(path, policy, *, run=subprocess.run):
    path = Path(path)
    if not 0 < path.stat().st_size <= policy['max_upload_bytes']:
        raise WorkerError('demo_video_size_limit')
    with path.open('rb') as stream:
        header = stream.read(12)
    if len(header) < 12 or header[4:8] != b'ftyp':
        raise WorkerError('demo_expected_mp4')
    try:
        result = run([sys.executable, '-I', '-c', _PYAV_METADATA, str(path)],
                     capture_output=True, text=True, timeout=15, check=True)
        value = json.loads(result.stdout)
        seconds = float(value['format']['duration'])
        if (not math.isfinite(seconds) or not 0 < seconds <= policy['max_video_seconds']
                or not any(s.get('codec_type') == 'video' for s in value['streams'])):
            raise WorkerError('demo_video_duration_limit')
    except (ValueError, KeyError, TypeError, subprocess.SubprocessError, OSError):
        raise WorkerError('demo_video_metadata_invalid') from None
    return seconds


def reuse_selection(conn, video, selection):
    """Viewing a known GPU identity never rebinds a historical job or spends quota."""
    from . import analysis_profiles, flow_service, job_queue
    sha, _ = job_queue.fingerprint(video)
    gpu_key = analysis_profiles.gpu_identity(sha, analysis_profiles.profile(selection))['gpu_key']
    for item in flow_service.selection_views(conn):
        if (item['input_sha256'] == sha and item['selection'] == selection
                and item['purpose'] == 'full_report' and len(item['jobs']) == 1
                and item['jobs'][0].get('gpu_key') == gpu_key):
            return {'selection_id': item['selection_id'], 'already_present': True,
                    'result_reused': True, 'new_gpu_calls': 0,
                    'children': item['children']}
    return None


def job_summary(job):
    """Projection of preserved candidates; no assumption that all were described."""
    candidates = [row['event_id'] for row in job.get('evidence_events', [])]
    described = sorted({row.get('event_id') for row in job.get('descriptions', []) if row.get('event_id')})
    return {'representative_event_ids': described,
            'unexplained_candidates': len(set(candidates) - set(described)),
            'policy': demo_session.CANDIDATE_POLICY}
