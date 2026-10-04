"""One approved report from immutable, completed B evidence; never a GPU grant."""
import hashlib
import json
import math
from pathlib import Path
import re

from . import analysis_profiles as profiles, team_settings
from .common import MAX_VIDEO, checked_id, read_json

SCHEMA = 'cctv-report-continuation-v1'
FIELDS = {'schema', 'name', 'purpose', 'max_jobs', 'window_seconds', 'source', 'generation_settings'}
SOURCE_FIELDS = {'session', 'session_sha256', 'worker_job_id', 'comparison_task_id',
                 'input_sha256', 'input_bytes', 'gpu_key', 'provider_job_id', 'event_id',
                 'analysis_profile', 'handoff_sha256', 'manifest_sha256', 'result_sha256',
                 'files'}


def selected(value):
    return isinstance(value, dict) and value.get('schema') == SCHEMA


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def file_sha(path):
    path = Path(path)
    from .hour_session import require
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents))
            and path.is_file(), 'report_continuation_source_path_invalid')
    before = path.stat()
    require(before.st_size <= MAX_VIDEO, 'report_continuation_source_too_large')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    require((before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_ino, after.st_size, after.st_mtime_ns), 'report_continuation_source_changed')
    return digest.hexdigest()


def validate_marker(value):
    from . import hour_session as session
    require = session.require
    require(set(value) == FIELDS and value['purpose'] == 'full_report'
            and type(value['max_jobs']) is int and value['max_jobs'] == 1
            and type(value['window_seconds']) is int and value['window_seconds'] == 900,
            'report_continuation_policy_invalid')
    source = value['source']
    require(isinstance(source, dict) and set(source) == SOURCE_FIELDS,
            'report_continuation_source_invalid')
    # Reuse the existing date/name validator without weakening the GPU policy.
    for name in (value['name'], source['session']):
        session._validate_marker(dict(schema=session.MODEL_SESSION_SCHEMA, name=name,
            versions=['B', 'R2'], purpose='gpu_comparison', max_jobs=2,
            per_job_seconds=300, window_seconds=900, approved_input_sha256=source['input_sha256']))
    require(value['name'] != source['session'], 'report_continuation_original_session_preserved')
    for key in ('worker_job_id', 'provider_job_id', 'comparison_task_id'):
        checked_id(source[key])
    require(type(source['input_bytes']) is int and 0 < source['input_bytes'] <= MAX_VIDEO
            and isinstance(source['event_id'], str)
            and re.fullmatch(r'event_[0-9]+', source['event_id']), 'report_continuation_source_invalid')
    for key in ('session_sha256', 'input_sha256', 'handoff_sha256', 'manifest_sha256', 'result_sha256'):
        require(session.valid_sha(source[key]), 'report_continuation_source_hash_invalid')
    profile = profiles.validate_profile(source['analysis_profile'], expected_version='B')
    require(profile.get('deployment') == profiles.MLOPS_DEPLOYMENT
            and profiles.gpu_identity(source['input_sha256'], profile)['gpu_key'] == source['gpu_key'],
            'report_continuation_gpu_identity_changed')
    settings = session.settings(value['generation_settings'], analysis_profile=profile)
    require(team_settings.is_team(settings), 'report_continuation_team_pipeline_required')
    files = source['files']
    event = source['event_id']
    required = {'input.mp4', 'handoff.json', 'normalized-result.json', 'model-binding.json',
                f'scene-{event}.json', f'scenes/{event}/input/manifest.json',
                f'scenes/{event}/input/fetch.complete.json'}
    require(isinstance(files, dict) and required <= set(files) and len(files) <= 32,
            'report_continuation_evidence_files_missing')
    for name, digest in files.items():
        path = Path(name)
        require(isinstance(name, str) and name and not path.is_absolute()
                and '..' not in path.parts and str(path) == name and session.valid_sha(digest),
                'report_continuation_evidence_path_invalid')
    require(files['input.mp4'] == source['input_sha256']
            and files['normalized-result.json'] == source['result_sha256']
            and files[f'scenes/{event}/input/manifest.json'] == source['manifest_sha256'],
            'report_continuation_result_hash_changed')
    return value


def binding(marker):
    source = marker['source']
    return {**{k: source[k] for k in ('input_sha256', 'input_bytes', 'gpu_key')},
            'version': 'B', 'settings': marker['generation_settings'],
            'reference_date': None, 'event_ids': [source['event_id']]}


def validate_state(value, marker):
    from . import hour_session as session
    require = session.require
    validate_marker(marker)
    require(isinstance(value, dict) and set(value) == FIELDS |
            {'started_at', 'deadline_at', 'stopped', 'bindings', 'claims'}
            and all(value[k] == marker[k] and type(value[k]) is type(marker[k]) for k in FIELDS),
            'report_continuation_policy_changed')
    require(all(type(value[k]) in (int, float) and math.isfinite(value[k])
                for k in ('started_at', 'deadline_at'))
            and value['deadline_at'] - value['started_at'] == 900
            and type(value['stopped']) is bool, 'report_continuation_window_invalid')
    source = marker['source']
    require(value['bindings'] == {source['worker_job_id']: binding(marker)}
            and isinstance(value['claims'], list), 'report_continuation_binding_changed')
    seen = set()
    for claim in value['claims']:
        require(isinstance(claim, dict) and set(claim) ==
                {'worker_job_id', 'event_id', 'kind', 'request_sha256', 'model', 'claimed_at'},
                'report_continuation_claim_invalid')
        kind = claim['kind']
        require(kind in ('scene', 'cpu_retrieval') and kind not in seen
                and claim['worker_job_id'] == source['worker_job_id']
                and claim['event_id'] == source['event_id']
                and claim['model'] == session.model_for(kind)
                and session.valid_sha(claim['request_sha256'])
                and type(claim['claimed_at']) in (int, float) and math.isfinite(claim['claimed_at'])
                and value['started_at'] <= claim['claimed_at'] < value['deadline_at'],
                'report_continuation_claim_invalid')
        require(kind != 'cpu_retrieval' or 'scene' in seen, 'report_continuation_scene_required')
        seen.add(kind)
    return value


def check_origin(conn, marker):
    from . import hour_session as session
    source = marker['source']
    previous = conn.execute('SELECT payload FROM cctv_jobs.service_state WHERE name=%s',
                            (source['session'],)).fetchone()
    session.require(previous is not None and fingerprint(previous['payload']) == source['session_sha256'],
                    'report_continuation_original_session_changed')
    old = previous['payload']
    session.validate(old, expected_marker=session.marker_from_state(old))
    approved = old['bindings'].get(source['worker_job_id'])
    session.require(old['schema'] == session.MODEL_SESSION_SCHEMA and old['purpose'] == 'gpu_comparison'
                    and old['claims'] == [] and approved is not None
                    and approved == dict(binding(marker), settings=None),
                    'report_continuation_original_binding_changed')
    session.require(old['stopped'] or session.clock(conn) >= old['deadline_at'],
                    'report_continuation_original_window_active')


def check_row(marker, row):
    from . import hour_session as session
    require = session.require
    source, state = marker['source'], row['state']
    require(row['worker_job_id'] == state.get('worker_job_id') == source['worker_job_id']
            and row.get('submit_attempts') == state.get('post_attempts') == 1
            and state.get('status') == 'handoff_ready' and not state.get('error_code'),
            'report_continuation_completed_job_required')
    require(all(state.get(k) == source[k] for k in
                ('input_sha256', 'input_bytes', 'gpu_key', 'provider_job_id', 'analysis_profile'))
            and fingerprint(row.get('handoff')) == source['handoff_sha256']
            and state.get('manifest_sha256') == source['manifest_sha256']
            and session.handoff(row) == [source['event_id']], 'report_continuation_identity_changed')
    directory = session.marker_path().parent / checked_id(row['directory_id'])
    for name, expected in source['files'].items():
        require(file_sha(directory / name) == expected, 'report_continuation_evidence_changed')
    require(read_json(directory / 'handoff.json') == row['handoff'],
            'report_continuation_handoff_changed')
