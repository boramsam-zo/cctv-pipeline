"""Existing Pod API adapter. Submission is deliberately never retried."""
import urllib.error
import urllib.request
import urllib.parse

from .common import JSON_LIMIT, WorkerError, checked_id, checked_url, parse


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ApiError(WorkerError):
    def __init__(self, code, status=None):
        self.status = status
        super().__init__(code)


class RunpodAPI:
    def __init__(self, base_url, token):
        self.base_url = checked_url(base_url)
        if not token or not token.isascii() or any(c.isspace() for c in token):
            raise WorkerError('invalid_analysis_api_token')
        # Identify this client explicitly; the Pod proxy rejects Python's default UA.
        self.headers = {'Authorization': 'Bearer ' + token,
                        'User-Agent': 'boram3-cctv-be-worker/1.0'}
        # No redirect may forward the bearer token or replay the MP4 POST.
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, method='GET', data=None, size=None, extra_headers=None):
        headers = dict(self.headers)
        headers.update(extra_headers or {})
        if size is not None:
            headers.update({'Content-Type': 'video/mp4', 'Content-Length': str(size)})
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=95 if method == 'POST' else 15) as response:
                raw = response.read(JSON_LIMIT + 1)
                if len(raw) > JSON_LIMIT:
                    raise ApiError('response_too_large')
                if response.status != (202 if method == 'POST' else 200):
                    raise ApiError('unexpected_http_status', response.status)
                value = parse(raw)
                if not isinstance(value, dict):
                    raise ApiError('invalid_api_response')
                return value
        except urllib.error.HTTPError as exc:
            # Never output a proxy error page, signed URL, request headers or response body.
            code = f'http_{exc.code}'
            if exc.code == 429:
                try:
                    error = parse(exc.read(JSON_LIMIT + 1)).get('error')
                    if error in ('service_job_budget_exhausted', 'service_time_budget_exhausted',
                                 'uncertain_execution_requires_reconciliation'):
                        code = 'provider_' + error
                except (WorkerError, AttributeError):
                    pass
            exc.close()
            raise ApiError(code, exc.code) from None
        except (OSError, urllib.error.URLError) as exc:
            raise ApiError('network_error') from None

    def health(self, expected_profile=None):
        value = self.request('/health')
        if expected_profile is not None or value.get('api_schema') == 'runpod-ab-api-v1':
            from .analysis_profiles import gpu_identity, profile, deployment_for_profile, supported_versions, companion_profiles
            deployment = None
            if expected_profile is not None:
                try:
                    deployment = deployment_for_profile(expected_profile)
                except ValueError:
                    raise WorkerError('pod_versioned_profile_not_ready') from None
            # A saved task is pinned to its original provider release. Checking
            # its companion version must not mix in the current deployment.
            versions = supported_versions(deployment)
            companions = companion_profiles(expected_profile) if expected_profile is not None else {
                v: profile(v, deployment=deployment) for v in versions}
            expected = {v: gpu_identity('0' * 64, companions[v])['profile_sha256'] for v in versions}
            if (value.get('status') != 'ok' or value.get('api_schema') != 'runpod-ab-api-v1'
                    or value.get('storage') != 's3' or value.get('gpu_concurrency') != 1
                    or value.get('supported_versions') != list(versions)
                    or value.get('profiles') != expected):
                raise WorkerError('pod_versioned_profile_not_ready')
            return {key: value[key] for key in ('api_schema', 'storage', 'supported_versions',
                    'profiles', 'limits', 'deadline_at', 'admissions') if key in value}
        if (value.get('status') != 'ok' or value.get('api_schema') != 'runpod-local-api-v1'
                or value.get('storage') != 's3'):
            raise WorkerError('pod_not_ready_for_s3_pipeline')
        return {'api_schema': value['api_schema'], 'storage': 's3'}

    def submit(self, path, size):
        with path.open('rb') as stream:
            value = self.request('/jobs', 'POST', stream, size)
        checked_id(value.get('job_id'))
        if value.get('status') != 'queued':
            raise WorkerError('invalid_submit_acknowledgement')
        return value['job_id']

    def submit_versioned(self, path, state):
        identity = analysis_identity(state)
        headers = {'X-CCTV-Version': identity['profile']['version'],
                   'X-CCTV-Input-SHA256': state['input_sha256'],
                   'X-CCTV-GPU-Key': identity['gpu_key']}
        with path.open('rb') as stream:
            value = self.request('/jobs', 'POST', stream, state['input_bytes'], headers)
        validate_analysis_identity(value, state)
        checked_id(value.get('job_id'))
        if value.get('status') not in ('uploading', 'queued', 'running', 'succeeded', 'failed'):
            raise WorkerError('invalid_submit_acknowledgement')
        return value['job_id']

    def status(self, job_id):
        return self.request('/jobs/' + checked_id(job_id))

    def lookup_versioned(self, state):
        identity = analysis_identity(state)
        value = self.request('/jobs/by-gpu-key?' + urllib.parse.urlencode({'gpu_key': identity['gpu_key']}))
        identifier = checked_id(value.get('job_id'))
        validate_status(value, dict(state, provider_job_id=identifier))
        return identifier

    def resume_evidence(self, state):
        analysis_identity(state)
        identifier = checked_id(state.get('provider_job_id'))
        value = self.request('/jobs/' + identifier + '/resume-evidence', 'POST', b'')
        validate_status(value, state)
        return identifier


def analysis_identity(state):
    from .analysis_profiles import gpu_identity
    try:
        identity = gpu_identity(state['input_sha256'], state['analysis_profile'])
        if state.get('gpu_key') != identity['gpu_key']:
            raise ValueError('gpu_key_changed')
        return identity
    except (ValueError, KeyError, TypeError):
        raise WorkerError('analysis_identity_mismatch') from None


def validate_analysis_identity(value, state):
    identity = analysis_identity(state)
    from .analysis_profiles import validate_profile
    try:
        validate_profile(value.get('analysis_profile'), expected_version=identity['profile']['version'])
    except (ValueError, TypeError, KeyError):
        raise WorkerError('provider_analysis_identity_mismatch') from None
    if (value.get('schema_version') != 'runpod-ab-api-v1'
            or value.get('analysis_profile') != identity['profile']
            or value.get('profile_sha256') != identity['profile_sha256']
            or value.get('gpu_key') != identity['gpu_key']
            or value.get('input_sha256') != state['input_sha256']
            or type(value.get('input_bytes')) is not int or value['input_bytes'] != state['input_bytes']):
        raise WorkerError('provider_analysis_identity_mismatch')


def validate_status(value, state):
    job_id = checked_id(value.get('job_id'))
    versioned = 'analysis_profile' in state
    if versioned:
        validate_analysis_identity(value, state)
    if (value.get('schema_version') != ('runpod-ab-api-v1' if versioned else 'runpod-local-api-v1')
            or job_id != state['provider_job_id']
            or value.get('input_sha256') != state['input_sha256']
            or type(value.get('input_bytes')) is not int
            or value['input_bytes'] != state['input_bytes']
            or value.get('status') not in (('uploading', 'queued', 'running', 'failed', 'succeeded')
                                           if versioned else ('queued', 'running', 'failed', 'succeeded'))):
        raise WorkerError('provider_result_mismatch')
    result = {'job_id': job_id, 'status': value['status'],
              'input_sha256': state['input_sha256'], 'input_bytes': state['input_bytes']}
    if versioned:
        phases = {'input', 'queued', 'analysis', 'package', 's3_upload', 'complete'}
        errors = {'upload_failed', 'analysis_failed', 'package_failed', 's3_upload_failed',
                  'interrupted_requires_reconciliation', 'service_time_budget_exhausted',
                  'waiting_for_uncertain_execution', None}
        if ('phase' in value and value['phase'] not in phases) or ('error' in value and value['error'] not in errors):
            raise WorkerError('invalid_provider_stage')
        result.update({key: value[key] for key in ('phase', 'error') if key in value})
        if 'attempts' in value:
            if type(value['attempts']) is not int or value['attempts'] not in (0, 1):
                raise WorkerError('invalid_provider_attempt_count')
            result['attempts'] = value['attempts']
        if 'outcome' in value:
            if (value['outcome'] not in ('completed', 'no_candidates')
                    or type(value.get('event_count')) is not int or value['event_count'] < 0
                    or (value['event_count'] == 0) != (value['outcome'] == 'no_candidates')):
                raise WorkerError('invalid_provider_outcome')
            result.update(outcome=value['outcome'], event_count=value['event_count'])
    if value['status'] == 'succeeded':
        receipt = value.get('s3')
        if value.get('storage') != 's3' or value.get('s3_uploaded') is not True or not isinstance(receipt, dict):
            raise WorkerError('s3_upload_not_complete')
        config = state['config']
        expected = {'bucket': config['s3_bucket'], 'region': config['s3_region'],
                    'manifest_key': f'cctv-evidence/{job_id}/manifest.json', 's3_uploaded': True}
        if any(receipt.get(key) != val for key, val in expected.items()):
            raise WorkerError('s3_reference_mismatch')
        if versioned:
            identity = analysis_identity(state)
            expected.update(analysis_profile=identity['profile'], gpu_key=identity['gpu_key'],
                            input_sha256=state['input_sha256'])
            if any(receipt.get(key) != val for key, val in expected.items()):
                raise WorkerError('s3_analysis_identity_mismatch')
            digest = receipt.get('manifest_sha256')
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise WorkerError('s3_manifest_digest_required')
            expected['manifest_sha256'] = digest
        result['s3'] = expected
    # Provider error details stay in Runpod's authenticated log; never copy arbitrary text.
    return result
