"""Existing Pod API adapter. Submission is deliberately never retried."""
import urllib.error
import urllib.request

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

    def request(self, path, method='GET', data=None, size=None):
        headers = dict(self.headers)
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
            exc.close()
            raise ApiError(f'http_{exc.code}', exc.code) from None
        except (OSError, urllib.error.URLError) as exc:
            raise ApiError('network_error') from None

    def health(self):
        value = self.request('/health')
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

    def status(self, job_id):
        return self.request('/jobs/' + checked_id(job_id))


def validate_status(value, state):
    job_id = checked_id(value.get('job_id'))
    if (value.get('schema_version') != 'runpod-local-api-v1'
            or job_id != state['provider_job_id']
            or value.get('input_sha256') != state['input_sha256']
            or type(value.get('input_bytes')) is not int
            or value['input_bytes'] != state['input_bytes']
            or value.get('status') not in ('queued', 'running', 'failed', 'succeeded')):
        raise WorkerError('provider_result_mismatch')
    result = {'job_id': job_id, 'status': value['status'],
              'input_sha256': state['input_sha256'], 'input_bytes': state['input_bytes']}
    if value['status'] == 'succeeded':
        receipt = value.get('s3')
        if value.get('storage') != 's3' or value.get('s3_uploaded') is not True or not isinstance(receipt, dict):
            raise WorkerError('s3_upload_not_complete')
        config = state['config']
        expected = {'bucket': config['s3_bucket'], 'region': config['s3_region'],
                    'manifest_key': f'cctv-evidence/{job_id}/manifest.json', 's3_uploaded': True}
        if any(receipt.get(key) != val for key, val in expected.items()):
            raise WorkerError('s3_reference_mismatch')
        result['s3'] = expected
    # Provider error details stay in Runpod's authenticated log; never copy arbitrary text.
    return result
