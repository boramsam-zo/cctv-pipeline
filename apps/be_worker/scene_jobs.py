"""Explicit, resumable scene steps for an existing handoff; never submits a GPU job."""
from pathlib import Path
import re
import uuid

from . import worker
from .common import WorkerError, checked_id, locked, now, read_json, save
from apps.scene_description import scene, db


class SceneJobs:
    def __init__(self, coordinator, persist_result=None, preflight=None):
        self.queue = coordinator
        self.store = coordinator.store
        self.persist_result = persist_result or (lambda result: db.store(self.store.connection, result))
        self.preflight = preflight or self.check_schema

    def check_schema(self):
        # Check migration/privileges before a potentially billable request.
        conn = self.store.connection
        conn.execute('SELECT accident_suspicion, incident_types FROM cctv_scene.descriptions LIMIT 0')
        row = conn.execute("SELECT has_table_privilege(current_user, 'cctv_scene.descriptions', 'INSERT') AS ok").fetchone()
        if not row['ok']:
            raise WorkerError('scene_schema_insert_permission_required')

    def _context(self, identifier, event_id):
        if not isinstance(event_id, str) or not re.fullmatch(r'event_[0-9]+', event_id):
            raise WorkerError('invalid_event_id')
        row = next((r for r in self.store.rows() if r['worker_job_id'] == checked_id(identifier)), None)
        if row is None:
            raise WorkerError('queue_job_not_found')
        return row, self.queue.directory(row)

    def _identity(self, row, directory, event_id):
        state = self.queue.load(row)
        if state['status'] != 'handoff_ready':
            raise WorkerError('scene_requires_handoff_ready')
        handoff = read_json(directory/'handoff.json')
        for name in ('worker_job_id', 'provider_job_id', 'input_sha256'):
            if handoff.get(name) != state.get(name):
                raise WorkerError('scene_handoff_identity_mismatch')
        if handoff != row.get('handoff'):
            raise WorkerError('scene_handoff_database_mismatch')
        expected = {'bucket': state['config']['s3_bucket'], 'region': state['config']['s3_region'],
                    'job_id': state['provider_job_id'], 'event_id': event_id,
                    'expected_input_sha256': state['input_sha256']}
        path = directory/f'scene-{event_id}.json'
        if (path.name not in handoff.get('scene_configs', [])
                or not path.is_file() or read_json(path) != expected):
            raise WorkerError('scene_event_not_in_handoff')
        return state, handoff, expected

    def _media(self, path, expected, handoff):
        config, manifest, event, media, digest = scene.load_bundle(path)
        if (config != expected or digest != handoff['manifest_sha256']
                or not (path/'fetch.complete.json').is_file()):
            raise WorkerError('scene_media_differs_from_handoff')
        return manifest, event, media

    def _save(self, row, directory, state, event_id, **changes):
        events = dict(state.get('scene_events', {}))
        events[event_id] = dict(events.get(event_id, {}), **changes, updated_at=now())
        attempts = sum(item.get('gemini_api_calls', 0) for item in events.values())
        worker.update(directory, state, scene_events=events, gemini_api_calls=attempts)
        self.store.write(row, state)
        return dict(worker_job_id=state['worker_job_id'], provider_job_id=state['provider_job_id'],
                    event_id=event_id, **events[event_id])

    def _description_output(self, event_dir):
        selection = event_dir/'description-selection.json'
        name = read_json(selection)['directory'] if selection.exists() else 'description'
        if not isinstance(name, str) or not re.fullmatch(r'description(?:-retry-[a-f0-9-]{36})?', name):
            raise WorkerError('scene_invalid_description_directory')
        return event_dir/name

    def _retry_output(self, event_dir, expected, failed_id, model, fps):
        try:
            if str(uuid.UUID(failed_id)) != failed_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise WorkerError('scene_invalid_failed_analysis_id') from None
        current = self._description_output(event_dir)
        target = event_dir/f'description-retry-{failed_id}'
        plan_file = event_dir/f'retry-{failed_id}.json'
        if current == target:
            # Repeating this exact command never starts a further attempt.
            plan = read_json(plan_file)
            if (plan.get('directory'), plan.get('failed_analysis_id'), plan.get('model'), plan.get('fps')) != (
                    target.name, failed_id, model, fps):
                raise WorkerError('scene_retry_settings_differ')
            return target
        if target.exists() or (event_dir/'database.json').exists():
            raise WorkerError('scene_retry_conflicts_with_existing_result')
        if not (current/'failure.json').exists() or (current/'result.json').exists():
            raise WorkerError('scene_retry_requires_failed_attempt')
        if (current/'response.raw.json').exists():
            raise WorkerError('scene_saved_response_requires_review_before_retry')
        request, failure = read_json(current/'request.json'), read_json(current/'failure.json')
        if (request.get('analysis_id') != failed_id or failure.get('analysis_id') != failed_id
                or failure.get('status') != 'failed'
                or request.get('provider_job_id') != expected['job_id'] or request.get('event_id') != expected['event_id']):
            raise WorkerError('scene_retry_identity_mismatch')
        if (request.get('model'), request.get('video_sample_fps')) != (model, fps):
            raise WorkerError('scene_retry_settings_differ')
        plan = {'directory': target.name, 'previous_directory': current.name,
                'failed_analysis_id': failed_id, 'model': model, 'fps': fps,
                'request_sha256': scene.sha((current/'request.json').read_bytes()),
                'failure_sha256': scene.sha((current/'failure.json').read_bytes())}
        if plan_file.exists():
            if read_json(plan_file) != plan:
                raise WorkerError('scene_retry_plan_changed')
        else:
            scene.write_new(plan_file, plan)
        # A separate selector leaves every original attempt in its original path.
        save(event_dir/'description-selection.json', plan)
        return target

    def fetch(self, identifier, event_id):
        row, directory = self._context(identifier, event_id)
        with locked(directory):
            state, handoff, expected = self._identity(row, directory, event_id)
            event_dir = directory/'scenes'/event_id
            event_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination = event_dir/'input'
            if not destination.exists():
                # Preserve incomplete downloads for inspection; only a validated
                # complete directory becomes the stable input of a model call.
                temporary = event_dir/('fetch-' + uuid.uuid4().hex)
                try:
                    scene.fetch(expected, temporary, self.queue.reader_factory(state))
                    self._media(temporary, expected, handoff)
                    temporary.rename(destination)
                except Exception:
                    self._save(row, directory, state, event_id, status='media_failed',
                               error_code='scene_fetch_failed_check_s3', media_bytes_verified=False)
                    raise WorkerError('scene_fetch_failed_check_s3') from None
            self._media(destination, expected, handoff)
            previous = state.get('scene_events', {}).get(event_id, {})
            # Rechecking bytes must not regress a completed model/DB stage.
            return self._save(row, directory, state, event_id,
                              status=previous.get('status', 'media_verified') if previous.get('media_bytes_verified') else 'media_verified',
                              media_bytes_verified=True,
                              error_code=previous.get('error_code') if previous.get('media_bytes_verified') else None)

    def diagnose(self, identifier, event_id, *, key_loader, checker=None, text_probe=False):
        """Explicit metadata/text check, preserving the scene attempt and DB state."""
        row, directory = self._context(identifier, event_id)
        with locked(directory):
            if (row['state']['status'] != 'handoff_ready'
                    or read_json(directory/'state.json')['status'] != 'handoff_ready'):
                raise WorkerError('scene_requires_handoff_ready')
            _, handoff, expected = self._identity(row, directory, event_id)
            event_dir = directory/'scenes'/event_id
            self._media(event_dir/'input', expected, handoff)
            request = read_json(self._description_output(event_dir)/'request.json')
            if (request.get('provider_job_id'), request.get('event_id')) != (expected['job_id'], event_id):
                raise WorkerError('scene_request_identity_mismatch')
            model = request.get('model')
            if not isinstance(model, str) or not re.fullmatch(r'gemini-[A-Za-z0-9.-]+', model) or 'embedding' in model:
                raise WorkerError('scene_invalid_saved_model')
            analysis_id = request.get('analysis_id')
            if text_probe:
                try:
                    valid_id = isinstance(analysis_id, str) and str(uuid.UUID(analysis_id)) == analysis_id
                except ValueError:
                    valid_id = False
                if not valid_id:
                    raise WorkerError('scene_invalid_failed_analysis_id')
                output = self._description_output(event_dir)
                if not (output/'failure.json').is_file() or (output/'result.json').exists():
                    raise WorkerError('scene_probe_requires_failed_attempt')
                failure = read_json(output/'failure.json')
                if failure.get('analysis_id') != analysis_id or failure.get('status') != 'failed':
                    raise WorkerError('scene_probe_requires_failed_attempt')
                destination = event_dir/'text-probes'/analysis_id
                # A probe is at most once per failed scene analysis, even on rerun.
                if destination.exists():
                    if not (destination/'result.json').exists():
                        raise WorkerError('scene_probe_incomplete_no_auto_retry')
                    previous = read_json(destination/'result.json')
                    if (previous.get('worker_job_id'), previous.get('provider_job_id'), previous.get('event_id'),
                            previous.get('model'), previous.get('failed_analysis_id')) != (
                                identifier, expected['job_id'], event_id, model, analysis_id):
                        raise WorkerError('scene_probe_identity_mismatch')
                    return dict(previous, saved_receipt_reused=True, generation_requests_this_run=0)
            else:
                destination = event_dir/'diagnostics'/uuid.uuid4().hex
            key = key_loader()
            if not key or any(c.isspace() for c in key):
                raise WorkerError('gemini_key_invalid_format')
            destination.mkdir(parents=True, mode=0o700)
            receipt = {'schema_version': 'be-scene-text-probe-v1' if text_probe else 'be-scene-diagnostic-v1', 'worker_job_id': identifier,
                       'provider_job_id': expected['job_id'], 'event_id': event_id, 'model': model,
                       'created_at': now(), 'metadata_requests_attempted': 0 if text_probe else 1,
                       'generation_requests': 1 if text_probe else 0, 'media_uploaded': False, 'database_modified': False}
            if text_probe:
                receipt.update(failed_analysis_id=analysis_id, prompt='Reply only with OK.', max_output_tokens=256,
                               generation_requests_this_run=1, video_generation_validated=False)
            scene.write_new(destination/'request.json', receipt)
            try:
                result = (checker or (scene.probe_text_generation if text_probe else scene.check_model_access))(model, key)
            except scene.GeminiError as exc:
                result = {'status': 'text_generation_failed' if text_probe else 'metadata_access_failed',
                          **exc.diagnostic, 'generation_validated': False}
            except Exception:
                result = {'status': 'text_probe_failed' if text_probe else 'metadata_check_failed', 'generation_validated': False}
            receipt.update(result, finished_at=now())
            scene.write_new(destination/'result.json', receipt)
            return receipt

    def describe(self, identifier, event_id, *, model, fps, key_loader, caller=None, retry_failed_analysis_id=None):
        row, directory = self._context(identifier, event_id)
        with locked(directory):
            state, handoff, expected = self._identity(row, directory, event_id)
            event_dir = directory/'scenes'/event_id
            inputs, output = event_dir/'input', self._description_output(event_dir)
            manifest, event, _ = self._media(inputs, expected, handoff)
            if retry_failed_analysis_id is not None:
                output = self._retry_output(event_dir, expected, retry_failed_analysis_id, model, fps)
            result_file = output/'result.json'
            if result_file.exists():
                result = read_json(result_file)
                # A resume must keep the original model/request settings.
                if (result.get('model'), result.get('video_sample_fps')) != (model, fps):
                    raise WorkerError('scene_existing_result_settings_differ')
            elif output.exists():
                raise WorkerError('scene_attempt_incomplete_inspect_saved_response_no_auto_retry')
            else:
                if output.name != 'description' and retry_failed_analysis_id is None:
                    raise WorkerError('scene_retry_requires_explicit_failed_analysis_id')
                self.preflight()
                key = key_loader()  # Existing successful results never require a key.
                attempts = state.get('scene_events', {}).get(event_id, {}).get('gemini_api_calls', 0)
                def call_once(model_id, data, api_key):
                    self._save(row, directory, state, event_id, status='describing',
                               media_bytes_verified=True, gemini_api_calls=attempts + 1, error_code=None,
                               description_directory=output.name)
                    # This durable intent is committed before leaving the host.
                    return (caller or scene.call_gemini)(model_id, data, api_key)
                try:
                    result = scene.describe(inputs, output, model, fps, key, call_once)
                except Exception as exc:
                    code = exc.code if isinstance(exc, scene.GeminiError) else 'scene_attempt_failed_no_auto_retry'
                    self._save(row, directory, state, event_id, status='description_failed',
                               error_code=code)
                    raise WorkerError(code) from None
            db.validate_result(result)
            source = result['source']
            if (result['schema_version'] != 'cctv-scene-v2'
                    or result['provider_job_id'] != expected['job_id'] or result['event_id'] != event_id
                    or result['input_sha256'] != expected['expected_input_sha256']
                    or source['bucket'] != expected['bucket'] or source['region'] != expected['region']
                    or source['manifest_sha256'] != handoff['manifest_sha256']
                    or (source['source_start_s'], source['source_end_s']) != (event['source_start_s'], event['source_end_s'])
                    or any({k: source['assets'][kind][k] for k in ('key', 'sha256', 'bytes')}
                           != {k: manifest['files'][event[kind]][k] for k in ('key', 'sha256', 'bytes')}
                           for kind in ('clip', 'frame'))):
                raise WorkerError('scene_result_identity_mismatch')
            digest = db.validate_result(result)
            previous = state.get('scene_events', {}).get(event_id, {})
            if previous.get('result_sha256') and previous['result_sha256'] != digest:
                raise WorkerError('scene_saved_result_changed')
            self._save(row, directory, state, event_id, status='description_ready',
                       analysis_id=result['analysis_id'], result_sha256=digest, error_code=None)
            try:
                receipt = self.persist_result(result)
                if receipt.get('committed') is not True or receipt.get('result_sha256') != digest:
                    raise ValueError('DB receipt mismatch')
            except Exception:
                self._save(row, directory, state, event_id, status='database_pending',
                           error_code='scene_database_failed_retry_saved_result')
                raise WorkerError('scene_database_failed_retry_saved_result') from None
            save(event_dir/'database.json', receipt)
            # RAG teammates consume the immutable result and identity, not a
            # reclassified legacy observation or another job's description.
            save(event_dir/'rag-handoff.json', {
                'schema_version': 'be-rag-handoff-v1', 'worker_job_id': identifier,
                'provider_job_id': expected['job_id'], 'event_id': event_id,
                'analysis_id': result['analysis_id'], 'result_sha256': digest,
                'scene_result': f'{output.name}/result.json', 'database_loaded': True,
                'rag_executed': False, 'report_generated': False})
            return self._save(row, directory, state, event_id, status='description_stored',
                              database_loaded=True, error_code=None,
                              accident_suspicion=result['description']['accident_suspicion'],
                              incident_types=result['description']['incident_types'])
