"""Authenticated A/B service with durable deduplication and one GPU consumer.

Model execution is disabled unless the deployment supplies a finite service
budget. The unchanged image runtime owns the shared GPU lock and per-run ledger.
"""
import asyncio
from contextlib import asynccontextmanager
import fcntl
import hashlib
import hmac
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import time
import uuid

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from apps.be_worker.analysis_profiles import gpu_identity, profile
from apps.be_worker.common import MAX_VIDEO, checked_id, now, read_json, save
from apps.runpod_image.service_job import Runtime
from apps.runpod_worker.storage import settings

SCHEMA = 'runpod-ab-api-v1'
ROOT = Path('/workspace/cctv-image-state/service/jobs')


def limits_from_environment():
    """No implicit inference allowance; the user supplies these at deployment."""
    return validate_limits({
        'max_jobs': int(os.environ['CCTV_GPU_MAX_JOBS']),
        'per_job_seconds': float(os.environ['CCTV_GPU_JOB_SECONDS']),
        'whole_service_seconds': float(os.environ['CCTV_GPU_TOTAL_SECONDS']),
    })


def validate_limits(value):
    if (set(value) != {'max_jobs', 'per_job_seconds', 'whole_service_seconds'}
            or type(value['max_jobs']) is not int or not 1 <= value['max_jobs'] <= 100
            or any(type(value[k]) not in (int, float) or not math.isfinite(value[k]) or value[k] <= 0
                   for k in ('per_job_seconds', 'whole_service_seconds'))):
        raise ValueError('explicit_finite_service_limits_required')
    return dict(value)


def validate_identity(state):
    identity = gpu_identity(state['input_sha256'], state['analysis_profile'])
    if (state['schema_version'] != SCHEMA or state['gpu_key'] != identity['gpu_key']
            or state['profile_sha256'] != identity['profile_sha256']
            or type(state['input_bytes']) is not int or not 0 < state['input_bytes'] <= MAX_VIDEO
            or type(state['attempts']) is not int or state['attempts'] not in (0, 1)):
        raise ValueError('service_job_identity_mismatch')
    checked_id(state['job_id'])
    return identity


class Jobs:
    def __init__(self, root, limits, s3_config, *, runner=None, prepare=None, publisher=None,
                 runtime=None, clock=time.time):
        self.root, self.limits = Path(root), validate_limits(limits)
        if (not s3_config or s3_config.get('prefix') != 'cctv-evidence'
                or not s3_config.get('bucket') or s3_config.get('region') != 'ap-northeast-2'):
            raise ValueError('versioned_service_requires_s3_seoul_evidence')
        self.s3_config, self.clock = dict(s3_config), clock
        self.runtime = runtime or Runtime()
        self.synthetic = runner is not None
        self.runner = runner or self.run_process
        self.prepare = prepare or self.runtime.prepare_output
        self.publisher = publisher or self.publish_process
        self.queue, self.lock_file, self.task = asyncio.Queue(), None, None
        self.by_gpu = {}

    async def start(self):
        if not self.synthetic:
            self.runtime.startup()
        if self.root.is_symlink():
            raise ValueError('service_root_symlink')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_file = (self.root / 'service.lock').open('a')
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.root / 'budget.json'
            if path.exists():
                self.budget = read_json(path)
                if (self.budget['limits'] != self.limits or self.budget['s3_config'] != self.s3_config
                        or self.clock() < self.budget['last_observed_at']):
                    raise ValueError('service_budget_changed_or_clock_regressed')
            else:
                if any(self.root.glob('*/status.json')):
                    raise ValueError('service_budget_missing_existing_jobs_preserved')
                started = self.clock()
                self.budget = {'limits': self.limits, 's3_config': self.s3_config,
                               'started_at': started, 'last_observed_at': started,
                               'deadline_at': started + self.limits['whole_service_seconds']}
                save(path, self.budget)
            if self.budget['deadline_at'] != self.budget['started_at'] + self.limits['whole_service_seconds']:
                raise ValueError('service_budget_deadline_changed')
            for path in sorted(self.root.glob('*/status.json')):
                if path.is_symlink() or path.parent.is_symlink():
                    raise ValueError('service_job_symlink')
                state = read_json(path)
                validate_identity(state)
                if (path.parent.name != state['job_id'] or state['gpu_key'] in self.by_gpu
                        or state['s3_config'] != self.s3_config):
                    raise ValueError('service_duplicate_or_changed_job')
                self.by_gpu[state['gpu_key']] = state['job_id']
                if state['status'] == 'queued' and state['attempts'] == 0:
                    self.queue.put_nowait((state['job_id'], False))
                elif state['status'] in ('uploading', 'running'):
                    self.update(state['job_id'], status='failed', error='interrupted_requires_reconciliation',
                                finished_at=now())
            self.task = asyncio.create_task(self.consume())
        except BaseException:
            self.lock_file.close()
            self.lock_file = None
            raise

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None

    def get(self, identifier):
        path = self.root / checked_id(identifier) / 'status.json'
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('service_job_symlink')
        state = read_json(path)
        validate_identity(state)
        return state

    def update(self, identifier, **fields):
        state = self.get(identifier)
        state.update(fields, updated_at=now())
        save(self.root / identifier / 'status.json', state)
        return state

    def remaining(self):
        observed = self.clock()
        if observed < self.budget['last_observed_at']:
            raise ValueError('service_clock_regressed')
        self.budget['last_observed_at'] = observed
        save(self.root / 'budget.json', self.budget)
        return self.budget['deadline_at'] - observed

    def reserve(self, identity, size):
        previous = self.by_gpu.get(identity['gpu_key'])
        if previous:
            state = self.get(previous)
            if (state['input_bytes'] != size or state['analysis_profile'] != identity['profile']):
                raise ValueError('duplicate_request_identity_conflict')
            return state, True
        if self.remaining() <= 0:
            raise ValueError('service_time_budget_exhausted')
        records = [self.get(identifier) for identifier in self.by_gpu.values()]
        if len(records) >= self.limits['max_jobs']:
            raise ValueError('service_job_budget_exhausted')
        if any(r['status'] == 'failed' and r['error'] == 'interrupted_requires_reconciliation'
               and r['attempts'] == 1 for r in records):
            raise ValueError('uncertain_execution_requires_reconciliation')
        if sum(r['status'] in ('uploading', 'queued', 'running') for r in records) >= 2:
            raise ValueError('queue_full')
        if shutil.disk_usage(self.root).free < 1024 ** 3 + 2 * MAX_VIDEO:
            raise ValueError('insufficient_disk_space')
        identifier = uuid.uuid4().hex
        directory = self.root / identifier
        directory.mkdir(mode=0o700)
        state = {'schema_version': SCHEMA, 'job_id': identifier, 'status': 'uploading',
                 'phase': 'input', 'created_at': now(), 'updated_at': now(), 'error': None,
                 'input_sha256': identity['input_sha256'], 'input_bytes': size,
                 'analysis_profile': identity['profile'], 'profile_sha256': identity['profile_sha256'],
                 'gpu_key': identity['gpu_key'], 'attempts': 0, 'storage': 'temporary_container_disk',
                 's3_config': self.s3_config, 's3_uploaded': False}
        save(directory / 'status.json', state)
        self.by_gpu[identity['gpu_key']] = identifier
        return state, False

    async def process(self, command, directory, seconds, *, clean=False):
        with (directory / 'worker.log').open('ab') as log:
            env = None
            if clean:
                # The GPU process never receives the API or cloud credentials.
                from apps.model_compare.runner import clean_environment
                env = clean_environment(directory)
            child = await asyncio.create_subprocess_exec(*command, stdout=log,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True, env=env)
            try:
                await asyncio.wait_for(child.wait(), timeout=seconds)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # The frozen runtime catches TERM and kills its own GPU child,
                # whose inherited lock otherwise prevents another GPU launch.
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(child.wait(), timeout=10)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await child.wait()
                raise
            if child.returncode:
                raise RuntimeError('service_child_failed_check_private_log')

    async def run_process(self, directory, state, seconds):
        began = time.monotonic()
        if state['analysis_profile']['version'] == 'R2':
            # This explicit preparation is inside the already durable one-shot
            # attempt. It may access private S3, but never imports/executes models.
            await self.process([sys.executable, '-B', '-m', 'apps.runpod_image.model_prepare',
                                '--version', 'R2'], directory, seconds)
        remaining = seconds - (time.monotonic() - began)
        if remaining <= 0:
            raise TimeoutError('model_preparation_consumed_job_budget')
        command = self.runtime.command(directory, state, remaining)
        await self.process(command, directory, remaining, clean=True)

    async def publish_process(self, directory, state):
        await self.process([sys.executable, '-B', '-m', 'apps.runpod_image.service_job',
                            '--publish-existing', str(directory)], directory, 300)
        return read_json(directory / 'output/s3_receipt.json')

    async def consume(self):
        while True:
            identifier, evidence_only = await self.queue.get()
            directory, phase = self.root / identifier, 'analysis'
            try:
                state = self.get(identifier)
                validate_identity(state)
                if not evidence_only:
                    if state['attempts'] != 0 or state['status'] != 'queued':
                        raise ValueError('gpu_attempt_must_not_repeat')
                    if any((other := self.get(value))['status'] == 'failed'
                           and other['error'] == 'interrupted_requires_reconciliation'
                           and other['attempts'] == 1 for value in self.by_gpu.values()):
                        self.update(identifier, error='waiting_for_uncertain_execution')
                        continue
                    seconds = min(self.limits['per_job_seconds'], self.remaining())
                    if seconds <= 0:
                        self.update(identifier, status='failed', error='service_time_budget_exhausted',
                                    finished_at=now())
                        continue
                    state = self.update(identifier, status='running', phase=phase, attempts=1, started_at=now())
                    await self.runner(directory, state, seconds)
                phase = 'package'
                state = self.update(identifier, status='running', phase=phase)
                outcome = self.prepare(directory, state)
                if (outcome['outcome'] not in ('completed', 'no_candidates')
                        or type(outcome['event_count']) is not int
                        or (outcome['event_count'] == 0) != (outcome['outcome'] == 'no_candidates')):
                    raise ValueError('invalid_analysis_outcome')
                phase = 's3_upload'
                state = self.update(identifier, phase=phase, **outcome)
                receipt = await self.publisher(directory, state)
                expected = {'bucket': state['s3_config']['bucket'], 'region': state['s3_config']['region'],
                            'manifest_key': f'cctv-evidence/{identifier}/manifest.json', 's3_uploaded': True,
                            'analysis_profile': state['analysis_profile'], 'gpu_key': state['gpu_key'],
                            'input_sha256': state['input_sha256']}
                if any(receipt.get(key) != value for key, value in expected.items()) or not re.fullmatch(
                        '[a-f0-9]{64}', receipt.get('manifest_sha256', '')):
                    raise ValueError('s3_receipt_identity_mismatch')
                self.update(identifier, status='succeeded', phase='complete', error=None,
                            finished_at=now(), storage='s3', s3_uploaded=True, s3=receipt)
                for value in self.by_gpu.values():
                    paused = self.get(value)
                    if paused['status'] == 'queued' and paused.get('error') == 'waiting_for_uncertain_execution':
                        self.update(value, error=None)
                        self.queue.put_nowait((value, False))
            except asyncio.CancelledError:
                self.update(identifier, status='failed', error='interrupted_requires_reconciliation',
                            phase=phase, finished_at=now())
                raise
            except Exception as exc:
                with (directory / 'worker.log').open('a') as log:
                    log.write(type(exc).__name__ + ': ' + str(exc) + '\n')
                self.update(identifier, status='failed', error=phase + '_failed', phase=phase, finished_at=now())
            finally:
                self.queue.task_done()

    def resume_evidence(self, identifier):
        state = self.get(identifier)
        if state['status'] == 'succeeded':
            return state
        if state['status'] != 'failed' or state['attempts'] != 1:
            raise ValueError('failed_completed_output_required')
        # This performs receipt/file checks only. A missing completed result
        # leaves the uncertain GPU request blocked and is never re-executed.
        self.prepare(self.root / identifier, state)
        state = self.update(identifier, status='running', phase='package', error=None)
        self.queue.put_nowait((identifier, True))
        return state


class BearerAuth:
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and not hmac.compare_digest(
                dict(scope['headers']).get(b'authorization', b''), ('Bearer ' + self.token).encode()):
            await JSONResponse({'error': 'unauthorized'}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(root=None, token=None, limits=None, s3_config=None, **injected):
    if token is None:
        token = Path(os.environ['CCTV_API_TOKEN_FILE']).read_text().strip()
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('private_ascii_api_token_required')
    jobs = Jobs(root or os.environ.get('CCTV_SERVICE_ROOT') or ROOT,
                limits if limits is not None else limits_from_environment(),
                s3_config if s3_config is not None else settings(), **injected)

    @asynccontextmanager
    async def lifespan(app):
        await jobs.start()
        try:
            yield
        finally:
            await jobs.close()

    def public(state):
        return {k: v for k, v in state.items() if k != 's3_config'}

    async def health(request):
        versions = ('B', 'R2') if os.environ.get('CCTV_ANALYSIS_DEPLOYMENT') == 'mlops-20261004-v1' else ('A', 'B')
        return JSONResponse({'status': 'ok', 'api_schema': SCHEMA, 'storage': 's3',
            'supported_versions': list(versions), 'profiles': {
                v: gpu_identity('0' * 64, profile(v))['profile_sha256'] for v in versions},
            'gpu_concurrency': 1, 'max_video_seconds': 120, 'checks_model_inference': False,
            'limits': jobs.limits, 'deadline_at': jobs.budget['deadline_at'],
            'admissions': len(jobs.by_gpu)})

    async def submit(request):
        if request.headers.get('content-type', '').split(';')[0] != 'video/mp4':
            return JSONResponse({'error': 'send_raw_mp4'}, status_code=415)
        try:
            size = int(request.headers.get('content-length', '0'))
            if not 0 < size <= MAX_VIDEO:
                return JSONResponse({'error': 'upload_limit_200_MiB'}, status_code=413)
            identity = gpu_identity(request.headers.get('x-cctv-input-sha256'),
                                    profile(request.headers.get('x-cctv-version')))
            if identity['gpu_key'] != request.headers.get('x-cctv-gpu-key'):
                raise ValueError('request_gpu_identity_mismatch')
        except (ValueError, TypeError):
            return JSONResponse({'error': 'invalid_analysis_request'}, status_code=400)
        try:
            state, reused = jobs.reserve(identity, size)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=429)
        directory = jobs.root / state['job_id']
        count, digest, target = 0, hashlib.sha256(), None
        try:
            if not reused:
                target = (directory / 'input.mp4').open('xb')
            async with asyncio.timeout(90):
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > size:
                        raise ValueError('upload_size_mismatch')
                    digest.update(chunk)
                    if target is not None:
                        target.write(chunk)
            if count != size or digest.hexdigest() != state['input_sha256']:
                raise ValueError('upload_identity_mismatch')
            if target is not None:
                target.flush()
                os.fsync(target.fileno())
                target.close()
                target = None
                state = jobs.update(state['job_id'], status='queued', phase='queued')
                jobs.queue.put_nowait((state['job_id'], False))
            else:
                state = jobs.get(state['job_id'])
            return JSONResponse(dict(public(state), reused=reused), status_code=202)
        except BaseException as exc:
            if target is not None:
                target.close()
            if not reused:
                jobs.update(state['job_id'], status='failed', phase='input', error='upload_failed', finished_at=now())
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            return JSONResponse({'error': 'upload_failed_or_identity_mismatch'}, status_code=400)

    async def status(request):
        try:
            return JSONResponse(public(jobs.get(request.path_params['job_id'])))
        except Exception:
            return JSONResponse({'error': 'job_not_found_or_invalid'}, status_code=404)

    async def lookup(request):
        key = request.query_params.get('gpu_key', '')
        if not re.fullmatch(r'gpu-v1:[a-f0-9]{64}', key) or key not in jobs.by_gpu:
            return JSONResponse({'error': 'job_not_found'}, status_code=404)
        return JSONResponse(public(jobs.get(jobs.by_gpu[key])))

    async def resume_evidence(request):
        try:
            state = jobs.resume_evidence(checked_id(request.path_params['job_id']))
            return JSONResponse(public(state), status_code=202)
        except Exception:
            return JSONResponse({'error': 'verified_completed_outputs_required_no_gpu_retry'}, status_code=409)

    app = Starlette(lifespan=lifespan, routes=[Route('/health', health),
        Route('/jobs', submit, methods=['POST']), Route('/jobs/by-gpu-key', lookup),
        Route('/jobs/{job_id}', status),
        Route('/jobs/{job_id}/resume-evidence', resume_evidence, methods=['POST'])])
    app.add_middleware(BearerAuth, token=token)
    app.state.jobs = jobs
    return app
