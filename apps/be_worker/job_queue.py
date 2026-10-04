"""Durable multi-video queue: enqueue -> dispatch/poll -> separate S3 collector.

Gemini runs only via explicit scene-describe, never in dispatch/collect.
An uncertain submission occupies a slot until its ID is reconciled.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.be_worker import worker
from apps.be_worker.common import MAX_VIDEO, WorkerError, checked_id, config_checked, locked, now, read_json, save
from apps.be_worker.queue_store import Store, connect
from apps.be_worker.runpod_api import RunpodAPI
from apps.be_worker.tracing import Recorder

ACTIVE = {'submitting', 'waiting', 'submission_unknown', 'storage_not_ready', 'verification_failed'}
TERMINAL = {'provider_failed', 'handoff_ready', 'no_candidates'}


def fingerprint(path):
    path = Path(path)
    sha, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        block = stream.read(1024 * 1024)
        if block[4:8] != b'ftyp':
            raise WorkerError('expected_mp4_container')
        while block:
            size += len(block)
            if size > MAX_VIDEO:
                raise WorkerError('invalid_video_size')
            sha.update(block)
            block = stream.read(1024 * 1024)
    return sha.hexdigest(), size


def enqueue(store, spool, video, config, request_key, *, dispatch_locked=False, analysis_profile=None):
    """Enrol input; callers using dispatch_locked must own the dispatch lock.

    P5 acquires that session lock outside its queue+flow SQL transaction so an
    aborted INSERT is rolled back before trying to release the session lock.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', request_key):
        raise WorkerError('invalid_request_key')
    config = config_checked(config)
    sha, size = fingerprint(video)
    identity = {}
    if analysis_profile is not None:
        from .analysis_profiles import validate_profile, gpu_identity
        snapshot = validate_profile(analysis_profile)
        identity = dict(analysis_profile=snapshot, gpu_key=gpu_identity(sha, snapshot)['gpu_key'])
    with (nullcontext() if dispatch_locked else store.lock('dispatch')):
        previous = store.by_key(request_key)
        if previous:
            state = previous['state']
            if (state['input_sha256'], state['input_bytes'], state['config']) != (sha, size, config):
                raise WorkerError('request_key_conflict_original_preserved')
            if any(state.get(name) != value for name, value in identity.items()) or (
                    not identity and state.get('analysis_profile') is not None):
                raise WorkerError('request_key_profile_conflict')
            return {'worker_job_id': state['worker_job_id'], 'already_present': True}
        spool = Path(spool)
        spool.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory_id = uuid.uuid4().hex
        state = worker.create_job(video, spool / directory_id, config)
        if (state['input_sha256'], state['input_bytes']) != (sha, size):
            raise WorkerError('source_changed_during_enqueue')
        if identity:
            worker.update(spool / directory_id, state, **identity)
        # A crash before INSERT leaves an unsubmitted private orphan, never a
        # charged remote job. Do not auto-delete it or infer a submission.
        store.insert(request_key, directory_id, state)
        return {'worker_job_id': state['worker_job_id'], 'already_present': False}


class Coordinator:
    def __init__(self, store, spool, api_factory, reader_factory=None, slots=2):
        if slots not in (1, 2):
            raise WorkerError('pod_slots_must_be_one_or_two')
        self.store, self.spool = store, Path(spool)
        self.api_factory, self.reader_factory, self.slots = api_factory, reader_factory, slots
        self.traces = {}

    def directory(self, row):
        return self.spool / checked_id(row['directory_id'])

    def load(self, row):
        if (self.directory(row) / 'retarget.pending.json').exists():
            raise WorkerError('retarget_incomplete_repeat_retarget')
        state = worker.load_state(self.directory(row))
        old = row['state']
        for key in ('worker_job_id', 'input_sha256', 'input_bytes', 'config'):
            if state[key] != old[key]:
                raise WorkerError('queue_spool_identity_mismatch')
        if 'analysis_profile' in state or 'analysis_profile' in old:
            from apps.be_worker.runpod_api import analysis_identity
            analysis_identity(state)
            analysis_identity(old)
            if any(state.get(key) != old.get(key) for key in ('analysis_profile', 'gpu_key')):
                raise WorkerError('queue_spool_analysis_identity_mismatch')
        if old.get('provider_job_id') and old['provider_job_id'] != state.get('provider_job_id'):
            if not state.get('provider_job_id') and state['status'] in ('prepared', 'submitting', 'submission_unknown'):
                # The DB can retain a received ID even if writing its local
                # receipt failed. Recover that ID; never issue another POST.
                worker.update(self.directory(row), state, **old)
            else:
                raise WorkerError('queue_spool_provider_mismatch')
        if old['status'] == 'submitting' and state['status'] == 'prepared':
            worker.update(self.directory(row), state, status='submission_unknown',
                          error_code='committed_submit_intent_requires_reconciliation')
        elif state['status'] == 'submitting':
            worker.update(self.directory(row), state, status='submission_unknown',
                          error_code='interrupted_submit_requires_reconciliation')
        elif state['status'] == 'prepared' and old['status'] not in ('prepared', 'submission_rejected'):
            raise WorkerError('queue_spool_state_regressed')
        return state

    def trace(self, row, state, action):
        key = (row['worker_job_id'], action)
        if key not in self.traces:
            self.traces[key] = Recorder(self.directory(row), action, state)
        return self.traces[key]

    def dispatch_tick(self):
        with self.store.lock('dispatch'):
            # Poll only known IDs; incomplete POSTs are not retried. Collection
            # uses another lock/process, so S3 latency never holds this lock.
            for row in self.store.rows():
                if row['state']['status'] not in ACTIVE:
                    continue
                with locked(self.directory(row)):
                    state = self.load(row)
                    if state['status'] == 'waiting':
                        trace = self.trace(row, state, 'resume')
                        worker.poll(self.directory(row), state, self.api_factory(state), trace, wait_seconds=0)
                        trace.finish(state)
                    self.store.write(row, state)
            rows = self.store.rows()
            if any(row['state']['status'] in ACTIVE - {'waiting'} for row in rows):
                return  # Investigate ambiguous/mismatched jobs before more GPU submissions.
            active = sum(row['state']['status'] == 'waiting' for row in rows)
            for row in rows:
                state = row['state']
                retry = (state['status'] == 'submission_rejected' and state.get('error_code') == 'http_429')
                if state['status'] != 'prepared' and not retry:
                    continue
                if active >= self.slots or not row['ready'] or (retry and row['submit_attempts'] >= 5):
                    break
                with locked(self.directory(row)):
                    if (self.directory(row) / 'retarget.pending.json').exists():
                        raise WorkerError('retarget_incomplete_repeat_retarget')
                    # Do not reset unknown submissions. This is only a known
                    # HTTP 429 rejection with zero accepted provider job IDs.
                    state = worker.load_state(self.directory(row))
                    for key in ('worker_job_id', 'input_sha256', 'input_bytes', 'config'):
                        if state[key] != row['state'][key]:
                            raise WorkerError('queue_spool_identity_mismatch')
                    if 'analysis_profile' in state or 'analysis_profile' in row['state']:
                        from apps.be_worker.runpod_api import analysis_identity
                        analysis_identity(state)
                        analysis_identity(row['state'])
                        if any(state.get(key) != row['state'].get(key) for key in ('analysis_profile', 'gpu_key')):
                            raise WorkerError('queue_spool_analysis_identity_mismatch')
                    if retry:
                        if state['status'] not in ('submission_rejected', 'prepared') or state.get('provider_job_id'):
                            raise WorkerError('unsafe_retry_state')
                        worker.update(self.directory(row), state, status='prepared', post_attempts=0, error_code=None)
                    if state['status'] != 'prepared':
                        self.store.write(row, state)
                        return  # Recover the durable receipt on the next tick.
                    if fingerprint(self.directory(row) / 'input.mp4') != (state['input_sha256'], state['input_bytes']):
                        raise WorkerError('queued_input_changed')
                    api = self.api_factory(state)
                    if 'analysis_profile' in state:
                        api.health(expected_profile=state['analysis_profile'])
                    else:
                        api.health()  # A preflight failure creates no submission intent.
                    trace = self.trace(row, state, 'run')
                    intent_started = False
                    def commit_intent():
                        nonlocal intent_started
                        # A connection error may hide a committed write. Never
                        # replace that possibly committed intent with prepared.
                        intent_started = True
                        self.store.write(row, dict(state, status='submitting'), attempt=True)
                    try:
                        worker.submit_once(self.directory(row), state, api, trace,
                                           before_intent=commit_intent)
                    finally:
                        trace.finish(state)
                        # Pure preflight failures retain zero attempts. Once a
                        # DB intent starts, prepared cannot prove non-submission.
                        if not intent_started or state['status'] != 'prepared':
                            self.store.write(row, state, delay=30 if state.get('error_code') == 'http_429' else 0)
                    if state['status'] != 'waiting':
                        return
                    active += 1

    def collect_tick(self):
        with self.store.lock('collect'):
            for row in self.store.rows():
                if row['state']['status'] != 'evidence_ready' or not row['ready']:
                    continue
                with locked(self.directory(row)):
                    state = self.load(row)
                    trace = self.trace(row, state, 'collect')
                    receipt = None
                    try:
                        receipt = worker.collect(self.directory(row), state, self.reader_factory(state), trace)
                    except Exception:
                        worker.update(self.directory(row), state, error_code='collection_failed_retry_metadata_only')
                    finally:
                        trace.finish(state)
                        self.store.write(row, state, handoff=receipt,
                                         delay=30 if state.get('error_code') else 0)

    def recover(self, identifier, provider_id):
        with self.store.lock('dispatch'):
            row = next((r for r in self.store.rows() if r['worker_job_id'] == checked_id(identifier)), None)
            if row is None:
                raise WorkerError('queue_job_not_found')
            with locked(self.directory(row)):
                state = self.load(row)
                trace = self.trace(row, state, 'resume')
                worker.resume(self.directory(row), state, self.api_factory(state), trace,
                              wait_seconds=0, provider_job_id=checked_id(provider_id))
                trace.finish(state)
                self.store.write(row, state)

    def recover_s3(self, identifier):
        """Reconcile manually uploaded results without contacting the GPU API."""
        with self.store.lock('dispatch'), self.store.lock('collect'):
            row = next((r for r in self.store.rows() if r['worker_job_id'] == checked_id(identifier)), None)
            if row is None:
                raise WorkerError('queue_job_not_found')
            directory = self.directory(row)
            with locked(directory):
                state = self.load(row)
                if state['status'] not in ('provider_failed', 'handoff_ready', 'no_candidates'):
                    raise WorkerError('s3_recovery_requires_failed_job')
                identifier = checked_id(state.get('provider_job_id'))
                if not row['submit_attempts'] or not state['post_attempts']:
                    raise WorkerError('s3_recovery_requires_submitted_job')
                history = directory / 's3-recovery-before.json'
                if not history.exists():
                    save(history, {'database': row['state'], 'file': state})
                proposed = dict(state)
                if state['status'] == 'provider_failed':
                    proposed.update(status='evidence_ready', s3={
                        'bucket': state['config']['s3_bucket'],
                        'region': state['config']['s3_region'],
                        'manifest_key': f'cctv-evidence/{identifier}/manifest.json',
                        's3_uploaded': True})
                trace = self.trace(row, proposed, 'collect')
                # collect validates remote manifest/job/source SHA/index before saving success.
                receipt = worker.collect(directory, proposed, self.reader_factory(state), trace)
                trace.finish(proposed)
                self.store.write(row, proposed, handoff=receipt)

    def retarget(self, identifier, config):
        """Move an unsubmitted job after an authenticated health check, never POST.

        A private journal preserves both original records. If interrupted between
        the DB and file writes, dispatch is blocked until this command is repeated.
        """
        identifier, config = checked_id(identifier), config_checked(config)
        with self.store.lock('dispatch'):
            rows = self.store.rows()
            row = next((r for r in rows if r['worker_job_id'] == identifier), None)
            if row is None:
                raise WorkerError('queue_job_not_found')
            if any(r['state']['status'] in ACTIVE for r in rows):
                raise WorkerError('cannot_retarget_with_active_jobs')
            directory = self.directory(row)
            with locked(directory):
                state = worker.load_state(directory)
                if row['submit_attempts'] != 0:
                    raise WorkerError('retarget_requires_zero_submission_attempts')
                for record in (row['state'], state):
                    if (record['status'] != 'prepared' or record.get('provider_job_id')
                            or record.get('post_attempts') != 0):
                        raise WorkerError('retarget_requires_unsubmitted_job')
                    if record['worker_job_id'] != identifier:
                        raise WorkerError('queue_spool_identity_mismatch')
                    if any(record['config'][k] != config[k] for k in ('s3_bucket', 's3_region')):
                        raise WorkerError('retarget_cannot_change_s3_destination')
                for key in ('input_sha256', 'input_bytes'):
                    if row['state'][key] != state[key]:
                        raise WorkerError('queue_spool_identity_mismatch')
                if fingerprint(directory / 'input.mp4') != (state['input_sha256'], state['input_bytes']):
                    raise WorkerError('queued_input_changed')
                pending = directory / 'retarget.pending.json'
                journal = read_json(pending) if pending.exists() else None
                if journal is not None:
                    if (journal.get('schema_version') != 'be-queue-retarget-v1'
                            or journal.get('worker_job_id') != identifier
                            or journal.get('directory_id') != row['directory_id']
                            or journal['after']['config'] != config):
                        raise WorkerError('retarget_pending_target_mismatch')
                    if (row['state'] not in (journal['db_before'], journal['after'])
                            or state not in (journal['file_before'], journal['after'])):
                        raise WorkerError('retarget_pending_state_mismatch')
                    # The journal may only change config and the update time.
                    expected = dict(journal['file_before'], config=config,
                                    updated_at=journal['after']['updated_at'])
                    if journal['after'] != expected:
                        raise WorkerError('retarget_pending_state_mismatch')
                    checked_id(journal['operation_id'])
                elif row['state']['config'] != state['config']:
                    raise WorkerError('queue_spool_identity_mismatch')
                target = journal['after'] if journal else dict(state, config=config, updated_at=now())
                self.api_factory(target).health()
                if journal is None and state['config'] == config:
                    return dict(status='retargeted', worker_job_id=identifier,
                                already_applied=True, health_checked=True, jobs_submitted=0)
                if journal is None:
                    journal = dict(schema_version='be-queue-retarget-v1', operation_id=uuid.uuid4().hex,
                                   worker_job_id=identifier, directory_id=row['directory_id'],
                                   db_before=row['state'], file_before=state, after=target)
                    save(pending, journal)
                self.store.write(row, target)
                save(directory / 'state.json', target)
                # Keep the original config/state and completion history privately.
                pending.rename(directory / ('retarget-' + journal['operation_id'] + '.json'))
                return dict(status='retargeted', worker_job_id=identifier,
                            already_applied=False, health_checked=True, jobs_submitted=0)


def listing(store):
    return [dict(sequence=row['sequence'], directory_id=row['directory_id'],
                 submit_attempts=row['submit_attempts'], **worker.summary(row['state'])) for row in store.rows()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spool', type=Path, default=Path('/spool'))
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('enqueue')
    add.add_argument('--video', type=Path, required=True)
    add.add_argument('--config', type=Path, required=True)
    add.add_argument('--request-key', required=True)
    commands.add_parser('list')
    for name in ('dispatch', 'collect'):
        run = commands.add_parser(name)
        run.add_argument('--seconds', type=int, default=300)
        if name == 'dispatch':
            run.add_argument('--slots', type=int, choices=(1, 2), default=2)
            run.add_argument('--token-file', type=Path)
        else:
            run.add_argument('--aws-profile')
    recover = commands.add_parser('recover')
    recover.add_argument('--worker-job-id', required=True)
    recover.add_argument('--provider-job-id', required=True)
    recover.add_argument('--token-file', type=Path)
    retarget = commands.add_parser('retarget')
    retarget.add_argument('--worker-job-id', required=True)
    retarget.add_argument('--config', type=Path, required=True)
    retarget.add_argument('--token-file', type=Path)
    recover_s3 = commands.add_parser('recover-s3')
    recover_s3.add_argument('--worker-job-id', required=True)
    recover_s3.add_argument('--aws-profile')
    fetch_scene = commands.add_parser('scene-fetch')
    fetch_scene.add_argument('--worker-job-id', required=True)
    fetch_scene.add_argument('--event-id', required=True)
    fetch_scene.add_argument('--aws-profile')
    describe_scene = commands.add_parser('scene-describe')
    describe_scene.add_argument('--worker-job-id', required=True)
    describe_scene.add_argument('--event-id', required=True)
    describe_scene.add_argument('--model', default='gemini-3.5-flash-lite')
    describe_scene.add_argument('--fps', type=float, default=4)
    describe_scene.add_argument('--key-file', type=Path)
    describe_scene.add_argument('--retry-failed-analysis-id',
                                help='Explicitly retry this failed analysis once, preserving its original files')
    report = commands.add_parser('scene-report')
    report.add_argument('--worker-job-id', required=True)
    report.add_argument('--event-id', required=True)
    report.add_argument('--reference-date', required=True,
                        help='Document reference date, not the unknown incident date')
    report.add_argument('--model', default='gemini-3.5-flash-lite')
    report.add_argument('--key-file', type=Path)
    diagnose_scene = commands.add_parser('scene-diagnose', help='Check saved Gemini model metadata; no generation')
    diagnose_scene.add_argument('--worker-job-id', required=True)
    diagnose_scene.add_argument('--event-id', required=True)
    diagnose_scene.add_argument('--key-file', type=Path)
    probe_scene = commands.add_parser('scene-probe', help='One fixed text generation request; no video upload')
    probe_scene.add_argument('--worker-job-id', required=True)
    probe_scene.add_argument('--event-id', required=True)
    probe_scene.add_argument('--key-file', type=Path)
    args = parser.parse_args()
    try:
        if hasattr(args, 'seconds') and not 0 <= args.seconds <= 1800:
            raise WorkerError('seconds_out_of_range')
        with connect() as conn:
            store = Store(conn)
            if args.command == 'enqueue':
                answer = enqueue(store, args.spool, args.video, read_json(args.config), args.request_key)
            elif args.command == 'list':
                answer = listing(store)
            else:
                token = worker.secret(args.token_file, 'RUNPOD_WORKER_TOKEN', 'Runpod analysis API token (hidden): ') if args.command in ('dispatch', 'recover', 'retarget') else None
                def api_factory(state):
                    return RunpodAPI(state['config']['runpod_url'], token)
                def reader_factory(state):
                    from apps.scene_description.scene import AwsCliReader, InstanceRoleReader
                    profile = getattr(args, 'aws_profile', None)
                    return (AwsCliReader(state['config']['s3_region'], profile) if profile
                            else InstanceRoleReader(state['config']['s3_region']))
                coordinator = Coordinator(store, args.spool, api_factory, reader_factory, getattr(args, 'slots', 2))
                if args.command == 'scene-report':
                    from apps.be_worker.report_jobs import ReportJobs
                    answer = ReportJobs(coordinator).run(args.worker_job_id, args.event_id,
                        args.reference_date, args.model,
                        lambda: worker.secret(args.key_file, 'GEMINI_API_KEY', 'Gemini API key (hidden): '))
                elif args.command in ('scene-fetch', 'scene-describe', 'scene-diagnose', 'scene-probe'):
                    from apps.be_worker.scene_jobs import SceneJobs
                    scenes = SceneJobs(coordinator)
                    if args.command == 'scene-fetch':
                        answer = scenes.fetch(args.worker_job_id, args.event_id)
                    elif args.command in ('scene-diagnose', 'scene-probe'):
                        answer = scenes.diagnose(args.worker_job_id, args.event_id,
                            text_probe=args.command == 'scene-probe',
                            key_loader=lambda: worker.secret(args.key_file, 'GEMINI_API_KEY', 'Gemini API key (hidden): '))
                    else:
                        answer = scenes.describe(args.worker_job_id, args.event_id,
                            model=args.model, fps=args.fps, retry_failed_analysis_id=args.retry_failed_analysis_id,
                            key_loader=lambda: worker.secret(
                                args.key_file, 'GEMINI_API_KEY', 'Gemini API key (hidden): '))
                elif args.command == 'recover-s3':
                    coordinator.recover_s3(args.worker_job_id)
                elif args.command == 'recover':
                    coordinator.recover(args.worker_job_id, args.provider_job_id)
                elif args.command == 'retarget':
                    answer = coordinator.retarget(args.worker_job_id, read_json(args.config))
                else:
                    end = time.monotonic() + args.seconds
                    while True:
                        getattr(coordinator, args.command + '_tick')()
                        if time.monotonic() >= end:
                            break
                        time.sleep(min(2, max(0, end - time.monotonic())))
                if args.command not in ('scene-report', 'retarget', 'scene-fetch', 'scene-describe', 'scene-diagnose', 'scene-probe'):
                    answer = listing(store)
            print(json.dumps(answer, ensure_ascii=False, indent=2))
            return 0
    except KeyboardInterrupt:
        print(json.dumps({'status': 'stopped', 'jobs_preserved': True}))
        return 2
    except Exception as exc:
        print(json.dumps({'error': exc.code if isinstance(exc, WorkerError) else 'queue_operation_failed_check_local_setup',
                          'jobs_preserved': True}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
