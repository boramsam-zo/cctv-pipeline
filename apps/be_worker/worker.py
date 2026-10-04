"""One durable BE job -> Runpod -> verified S3 metadata -> scene input configs."""
import argparse
from copy import deepcopy
import getpass
import hashlib
import os
from pathlib import Path
import sys
import time
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.be_worker.common import (JSON_LIMIT, MAX_VIDEO, VERSION, WorkerError, checked_id,
    config_checked, digest, locked, now, parse, read_json, save)
from apps.be_worker.runpod_api import ApiError, RunpodAPI, validate_status
from apps.be_worker.tracing import LangSmith, Recorder, export_traces


def create_job(video, output, config):
    config = config_checked(config)
    video, output = Path(video), Path(output)
    if not video.is_file() or not 0 < video.stat().st_size <= MAX_VIDEO:
        raise WorkerError('invalid_video_size')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output.chmod(0o700)
    sha, size = hashlib.sha256(), 0
    # A bounded private snapshot keeps the exact submitted bytes stable across resumes.
    with video.open('rb') as source, (output / 'input.mp4').open('xb') as target:
        os.chmod(target.name, 0o600)
        first = source.read(1024 * 1024)
        if first[4:8] != b'ftyp':
            raise WorkerError('expected_mp4_container')
        block = first
        while block:
            size += len(block)
            if size > MAX_VIDEO:
                raise WorkerError('invalid_video_size')
            target.write(block)
            sha.update(block)
            block = source.read(1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    state = {'schema_version': VERSION, 'worker_job_id': uuid.uuid4().hex,
             'provider_job_id': None, 'config': config, 'input_sha256': sha.hexdigest(),
             'input_bytes': size, 'status': 'prepared', 'created_at': now(), 'updated_at': now(),
             'post_attempts': 0, 'poll_count': 0, 'gemini_api_calls': 0, 'error_code': None}
    save(output / 'state.json', state)
    return state


def load_state(directory):
    state = read_json(Path(directory) / 'state.json')
    if state.get('schema_version') != VERSION:
        raise WorkerError('invalid_worker_state')
    config_checked(state['config'])
    checked_id(state['worker_job_id'])
    if state.get('provider_job_id'):
        checked_id(state['provider_job_id'])
    return state


def update(directory, state, **values):
    state.update(values, updated_at=now())
    save(Path(directory) / 'state.json', state)


def submit_once(directory, state, api, trace, *, before_intent=None):
    from apps.be_worker import hour_session, report_continuation
    if hour_session.configured() and report_continuation.selected(hour_session.current_marker()):
        raise WorkerError('report_continuation_gpu_submission_forbidden')
    if state['post_attempts'] != 0 or state['status'] != 'prepared':
        raise WorkerError('submission_must_not_be_repeated')
    from apps.be_worker.mlops_jobs import binding_for, freeze_binding
    snapshot = deepcopy(state.get('analysis_profile'))
    binding = freeze_binding(directory, snapshot)
    with trace.span('runpod.health'):
        if 'analysis_profile' in state:
            from apps.be_worker.runpod_api import analysis_identity
            analysis_identity(state)
            health = api.health(expected_profile=state['analysis_profile'])
            from apps.be_worker import hour_session
            if hour_session.configured():
                marker = hour_session.current_marker()
                if marker.get('schema') == 'cctv-model-session-v1':
                    limits = health.get('limits') if isinstance(health, dict) else None
                    expected = {'max_jobs': marker['max_jobs'],
                                'per_job_seconds': marker['per_job_seconds'],
                                'whole_service_seconds': marker['window_seconds']}
                    if limits != expected:
                        raise WorkerError('pod_model_session_limits_mismatch')
        else:
            api.health()
    # The Registry/network preflight must finish before the queue commits a
    # submission attempt. Recheck the local immutable contract after health;
    # no second Registry request may fail after that durable intent is written.
    if state.get('analysis_profile') != snapshot:
        raise WorkerError('submission_profile_changed_after_preflight')
    if binding is not None and (binding_for(snapshot) != binding
            or read_json(Path(directory) / 'model-binding.json') != binding):
        raise WorkerError('mlflow_frozen_binding_changed')
    if before_intent is not None:
        before_intent()
    # Durable intent before the first byte is sent. Any ambiguous failure is NOT retried.
    update(directory, state, status='submitting', post_attempts=1, error_code=None)
    try:
        with trace.span('runpod.submit', state) as result:
            if 'analysis_profile' in state:
                identifier = api.submit_versioned(Path(directory) / 'input.mp4', state)
            else:
                identifier = api.submit(Path(directory) / 'input.mp4', state['input_bytes'])
            update(directory, state, provider_job_id=checked_id(identifier), status='waiting')
            result.update(provider_job_id=identifier, status='waiting')
    except (ApiError, WorkerError) as exc:
        rejected = isinstance(exc, ApiError) and exc.status in (400, 401, 403, 404, 413, 415, 422, 429)
        update(directory, state, status='submission_rejected' if rejected else 'submission_unknown',
               error_code=exc.code)


def poll(directory, state, api, trace, wait_seconds=300, interval=2, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + wait_seconds
    with trace.span('runpod.poll', state) as result:
        while True:
            try:
                raw = api.status(state['provider_job_id'])
            except (ApiError, WorkerError):
                update(directory, state, status='waiting', error_code='status_unavailable_resume_only')
                break
            try:
                remote = validate_status(raw, state)
            except WorkerError as exc:
                update(directory, state, status='storage_not_ready' if exc.code == 's3_upload_not_complete'
                       else 'verification_failed', error_code=exc.code)
                break
            update(directory, state, poll_count=state['poll_count'] + 1, error_code=None)
            save(Path(directory) / 'provider-status.json', remote)
            if remote['status'] == 'failed':
                update(directory, state, status='provider_failed', error_code='provider_failed_check_log')
                break
            if remote['status'] == 'succeeded':
                update(directory, state, status='evidence_ready', s3=remote['s3'])
                break
            if clock() >= deadline:
                update(directory, state, status='waiting', error_code='wait_limit_resume_only')
                break
            sleep(min(interval, max(0, deadline - clock())))
        result.update(state)


def resume(directory, state, api, trace, wait_seconds=300, provider_job_id=None):
    if state['status'] in ('handoff_ready', 'no_candidates', 'evidence_ready'):
        return
    if provider_job_id:
        identifier = checked_id(provider_job_id)
        if state.get('provider_job_id') and state['provider_job_id'] != identifier:
            raise WorkerError('cannot_rebind_provider_job')
        # A lost POST acknowledgement can only be recovered with a known ID + exact source match.
        proposed = dict(state, provider_job_id=identifier)
        raw = api.status(identifier)
        validate_status(raw, proposed)
        update(directory, state, provider_job_id=identifier, status='waiting', error_code=None)
    if not state.get('provider_job_id'):
        raise WorkerError('provider_job_id_required_no_resubmission')
    poll(directory, state, api, trace, wait_seconds)


def collect(directory, state, reader, trace):
    if state['status'] in ('handoff_ready', 'no_candidates'):
        return read_json(Path(directory) / 'handoff.json')
    if state['status'] != 'evidence_ready':
        raise WorkerError('runpod_s3_completion_required')
    from apps.scene_description import scene
    identifier = checked_id(state['provider_job_id'])
    config = {'bucket': state['config']['s3_bucket'], 'region': state['config']['s3_region'],
              'job_id': identifier, 'event_id': 'event_000', 'expected_input_sha256': state['input_sha256']}
    is_ab = 'analysis_profile' in state
    if is_ab:
        from apps.scene_description import ab_scene
        config.update(analysis_profile=state['analysis_profile'], gpu_key=state['gpu_key'])
        ab_scene.identity(config)
    with trace.span('s3.verify', state) as result:
        raw_manifest = reader.get(config['bucket'], f'cctv-evidence/{identifier}/manifest.json', JSON_LIMIT)
        if len(raw_manifest) > JSON_LIMIT:
            raise WorkerError('manifest_too_large')
        if is_ab and digest(raw_manifest) != state['s3'].get('manifest_sha256'):
            raise WorkerError('provider_manifest_digest_mismatch')
        manifest = parse(raw_manifest)
        prefix = scene.validate_manifest(manifest, config)
        entry = scene.manifest_entry(manifest, prefix, 'evidence.json', JSON_LIMIT)
        raw_index = scene.checked_bytes(reader.get(config['bucket'], entry['key'], entry['bytes']), entry)
        index = parse(raw_index)
        if is_ab:
            index = ab_scene.checked_index(raw_index, config, manifest)
            normalized_entry = scene.manifest_entry(manifest, prefix, 'normalized-result.json', JSON_LIMIT)
            normalized_bytes = scene.checked_bytes(reader.get(config['bucket'], normalized_entry['key'],
                                                    normalized_entry['bytes']), normalized_entry)
            if digest(normalized_bytes) != index['normalized_result_sha256']:
                raise WorkerError('normalized_result_identity_mismatch')
            normalized = parse(normalized_bytes)
            if (normalized.get('input_sha256'), normalized.get('version'), normalized.get('status')) != (
                    state['input_sha256'],state['analysis_profile']['version'],index['status']):
                raise WorkerError('normalized_result_summary_mismatch')
        elif (index.get('schema_version') != 'runpod-evidence-v1' or index.get('pts_verified') is not True
                or index.get('time_basis') != 'seconds_from_first_decoded_frame'
                or not isinstance(index.get('events'), list) or len(index['events']) > 100):
            raise WorkerError('invalid_evidence_index')
        configs = []
        for event in index['events']:
            selected_config = dict(config, event_id=event['event_id'])
            scene.validate_config(selected_config)
            checked = scene.selected_event(index, event['event_id'])
            if is_ab:
                ab_scene.entries(manifest, prefix, checked)
            else:
                assets = [scene.manifest_entry(manifest, prefix, checked[kind], scene.MEDIA_LIMIT)
                          for kind in ('clip', 'frame')]
                if sum(asset['bytes'] for asset in assets) > scene.MEDIA_LIMIT:
                    raise WorkerError('scene_media_too_large')
            configs.append(selected_config)
        handoff = {'schema_version': 'be-scene-handoff-v1', 'worker_job_id': state['worker_job_id'],
                   'provider_job_id': identifier, 'input_sha256': state['input_sha256'],
                   's3': state['s3'], 'manifest_sha256': digest(raw_manifest),
                   'evidence_index_sha256': digest(raw_index), 'event_count': len(configs),
                   'media_bytes_verified': False, 'gemini_api_calls': 0,
                   'status': 'handoff_ready' if configs else 'no_candidates',
                   'scene_configs': [f"scene-{item['event_id']}.json" for item in configs]}
        if is_ab:
            handoff.update(analysis_profile=state['analysis_profile'], gpu_key=state['gpu_key'],
                           evidence_version='runpod-evidence-v2',
                           metrics={name:normalized.get(name,{}) for name in ('timings','memory')})
        # Completion receipt is written only after ALL metadata and event paths validate.
        directory = Path(directory)
        if is_ab:
            (directory / 'evidence.raw.json').write_bytes(raw_index)
            (directory / 'normalized-result.json').write_bytes(normalized_bytes)
        save(directory / 'manifest.json', manifest)
        save(directory / 'evidence.json', index)
        for item, filename in zip(configs, handoff['scene_configs']):
            save(directory / filename, item)
        save(directory / 'handoff.json', handoff)
        update(directory, state, status=handoff['status'], event_count=len(configs), error_code=None,
               manifest_sha256=handoff['manifest_sha256'])
        result.update(state, s3_reference_verified=True)
        return handoff


def secret(path, variable, prompt):
    value = Path(path).read_text().strip() if path else os.environ.get(variable, '')
    if not value:
        if not sys.stdin.isatty():
            raise WorkerError('secret_not_configured_use_file_or_environment')
        value = getpass.getpass(prompt).strip()
    return value


def summary(state):
    keys = ('worker_job_id', 'provider_job_id', 'status', 'error_code', 'input_bytes', 'post_attempts',
            'poll_count', 'event_count', 'gemini_api_calls', 'scene_events')
    return {key: state[key] for key in keys if key in state}


def optional_export(directory, exporter):
    try:
        return export_traces(directory, exporter)
    except Exception:
        # Telemetry validation/disk/network failure must not overwrite business results.
        return {'status': 'pending', 'error_code': 'trace_export_failed'}


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    configure = sub.add_parser('configure', help='Prepare non-secret connection config locally; no API calls')
    configure.add_argument('--output', type=Path, required=True)
    configure.add_argument('--s3-bucket', help='Override the example bucket for this deployment')
    configure.add_argument('--s3-region', default=None, help='Override the example region')
    for action in ('run', 'resume', 'collect', 'status', 'trace'):
        command = sub.add_parser(action)
        if action == 'run':
            command.add_argument('--config', type=Path, required=True)
            command.add_argument('--video', type=Path, required=True)
            command.add_argument('--output', type=Path, required=True)
        else:
            command.add_argument('--job-dir', type=Path, required=True)
        if action in ('run', 'resume'):
            command.add_argument('--token-file', type=Path)
            command.add_argument('--wait-seconds', type=int, default=300)
        if action == 'resume':
            command.add_argument('--provider-job-id')
        if action == 'collect':
            command.add_argument('--aws-profile', help='Mac AWS CLI profile; omit on EC2 with an instance role')
        if action != 'status':
            command.add_argument('--langsmith', action='store_true', help='Export allowlisted spans after processing')
            command.add_argument('--langsmith-key-file', type=Path)
            command.add_argument('--langsmith-project', default=os.environ.get('LANGSMITH_PROJECT', 'cctv-be-runpod'))
            command.add_argument('--langsmith-endpoint', default=os.environ.get('LANGSMITH_ENDPOINT', 'https://api.smith.langchain.com'))
            command.add_argument('--langsmith-workspace', default=os.environ.get('LANGSMITH_WORKSPACE_ID'))
    args = parser.parse_args()
    state, trace, exporter = None, None, None
    try:
        if args.action == 'configure':
            if args.output.exists():
                raise WorkerError('config_already_exists_choose_new_path')
            config = read_json(Path(__file__).with_name('config.example.json'))
            config['runpod_url'] = input('Runpod Port 8000 HTTPS service URL: ').strip()
            if args.s3_bucket:
                config['s3_bucket'] = args.s3_bucket
            if args.s3_region:
                config['s3_region'] = args.s3_region
            config = config_checked(config)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            save(args.output, config)
            print(json.dumps({'status': 'configured', 'api_calls': 0}))
            return 0
        if hasattr(args, 'wait_seconds') and not 0 <= args.wait_seconds <= 1800:
            raise WorkerError('wait_seconds_out_of_range')
        if args.action != 'status' and (args.langsmith or args.action == 'trace'):
            exporter = LangSmith(secret(args.langsmith_key_file, 'LANGSMITH_API_KEY', 'LangSmith API key (hidden): '),
                args.langsmith_project, args.langsmith_endpoint, args.langsmith_workspace)
        directory = args.output if args.action == 'run' else args.job_dir
        if args.action == 'run':
            state = create_job(args.video, directory, read_json(args.config))
        with locked(directory):
            state = load_state(directory)
            if args.action == 'status':
                print(json.dumps(summary(state), indent=2))
                return 0
            if args.action == 'trace':
                outcome = optional_export(directory, exporter)
                print(json.dumps({'langsmith': outcome, 'new_model_inference': False}, indent=2))
                return 0 if outcome['status'] == 'delivered' else 2
            trace = Recorder(directory, args.action, state)
            try:
                if args.action in ('run', 'resume'):
                    api = RunpodAPI(state['config']['runpod_url'],
                        secret(args.token_file, 'RUNPOD_WORKER_TOKEN', 'Runpod analysis API token (hidden): '))
                    if args.action == 'run':
                        submit_once(directory, state, api, trace)
                        if state['status'] == 'waiting':
                            poll(directory, state, api, trace, args.wait_seconds)
                    else:
                        resume(directory, state, api, trace, args.wait_seconds, args.provider_job_id)
                elif args.action == 'collect':
                    from apps.scene_description.scene import AwsCliReader, InstanceRoleReader
                    region = state['config']['s3_region']
                    reader = AwsCliReader(region, args.aws_profile) if args.aws_profile else InstanceRoleReader(region)
                    collect(directory, state, reader, trace)
            except KeyboardInterrupt:
                update(directory, state, error_code='interrupted_resume_only')
            except Exception as exc:
                update(directory, state, error_code=exc.code if isinstance(exc, WorkerError) else 'step_failed_check_configuration')
            finally:
                trace.finish(state)
            result = summary(state)
            if exporter:
                result['langsmith'] = optional_export(directory, exporter)
            print(json.dumps(result, indent=2))
            return 0 if state['status'] in ('evidence_ready', 'handoff_ready', 'no_candidates') and not state['error_code'] else 2
    except (OSError, ValueError, KeyError, WorkerError) as exc:
        print(json.dumps({'error': exc.code if isinstance(exc, WorkerError) else 'invalid_local_input_or_configuration',
                          'hint': 'Check README; do not repeat a possibly accepted submission.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
