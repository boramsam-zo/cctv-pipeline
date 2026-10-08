"""Private, fixed-upstream GET boundary for the public saved-results viewer.

This service has no credentials, database connection or writable result volume.
Only it joins the existing API network; its port must never be published.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_JSON = 16 * 1024 * 1024
MAX_MEDIA = 200 * 1024 * 1024
JSON_PATHS = frozenset(('/jobs', '/selections', '/ui-snapshot', '/ui-revision'))
MEDIA_PATH = re.compile(r'/jobs/[a-f0-9]{32}/(?:source|media/event_[0-9]+/(?:clip|frame|roi_clip|clip_preview|roi_clip_preview))')
JOB_FIELDS = frozenset(('worker_job_id', 'provider_job_id', 'stage', 'error_code',
    'submitted_at', 'queue_updated_at', 'post_attempts', 'scene_calls', 'scene_events',
    'descriptions', 'reports', 'input_sha256', 'version', 'evidence_events', 'metrics'))
REPORT_IDENTITIES = frozenset(('report_id', 'analysis_id', 'worker_job_id', 'event_id',
    'scene_sha256', 'source_report_id', 'source_result_sha256'))
EVENT_FIELDS = frozenset(('event_id', 'version', 'candidate_time_s', 'source_start_s',
    'source_end_s', 'start_frame', 'end_frame_exclusive', 'clip_frame_count',
    'representative_frame', 'representative_source_time_s', 'display_sample_time_s',
    'representative_within_clip', 'candidate_clip_time_s', 'requested_pre_s',
    'requested_post_s', 'pre_context_truncated', 'post_context_truncated',
    'clip', 'roi_clip', 'frame', 'frame_role', 'frame_mime'))


def allowed_path(path):
    # Match the exact wire path: no percent decoding, query, absolute URL or redirects.
    return isinstance(path, str) and (path in JSON_PATHS or MEDIA_PATH.fullmatch(path) is not None)


def project_job(job):
    result = {key: value for key, value in job.items() if key in JOB_FIELDS}
    profile = job.get('analysis_profile') or {}
    if isinstance(profile, dict):
        result['analysis_profile'] = {key: profile[key] for key in ('version', 'schema_version') if key in profile}
    flow = job.get('flow') or {}
    if isinstance(flow, dict):
        result['flow'] = {key: flow[key] for key in ('status', 'paused', 'elapsed_seconds') if key in flow}
    tracking = job.get('mlflow') or {}
    if isinstance(tracking, dict):
        result['mlflow'] = {key: tracking[key] for key in ('model_id', 'model_version', 'status') if key in tracking}
    result['reports'] = [project_report(report) for report in job.get('reports', [])]
    result['scene_events'] = {key: {} for key in job.get('scene_events', {}) if re.fullmatch(r'event_[0-9]+', key)}
    if 'evidence_events' in job:
        result['evidence_events'] = [{key: value for key, value in event.items() if key in EVENT_FIELDS}
                                     for event in job['evidence_events']]
    if 'metrics' in job:
        result['metrics'] = {'timings': (job['metrics'] or {}).get('timings', {})}
    # Saved report/scene results and their hashes stay exact. Do not send admission
    # settings, task/session records, local-media paths or MLflow management metadata.
    return result


def project_report(report):
    result = {key: report[key] for key in ('report_id', 'review', 'quality_review_error') if key in report}
    if 'settings' in report:
        result['settings'] = {key: value for key, value in report['settings'].items() if key in REPORT_IDENTITIES}
    if 'state' in report:
        result['state'] = {key: value for key, value in report['state'].items()
                           if key in ('status', 'result', 'result_sha256', 'elapsed_seconds', 'error_code')}
    if 'quality_reviews' in report:
        result['quality_reviews'] = [project_report(row) for row in report['quality_reviews']]
    return result


def project_response(path, value):
    if path in ('/ui-revision', '/ui-snapshot'):
        if (not isinstance(value, dict)
                or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('revision', '')))):
            raise ValueError('invalid_ui_revision')
        answer = {'revision': value['revision']}
        if path == '/ui-snapshot':
            answer.update(jobs=project_response('/jobs', value.get('jobs')),
                          selections=project_response('/selections', value.get('selections')))
        return answer
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError('invalid_saved_result_list')
    if path == '/jobs':
        return [project_job(row) for row in value]
    return [{**{key: row[key] for key in ('selection_id', 'input_sha256', 'selection', 'purpose', 'created_at') if key in row},
             'jobs': [project_job(job) for job in row.get('jobs', [])]} for row in value]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def upstream_url(value):
    parts = urlsplit(value)
    if (parts.scheme != 'http' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ('', '/')
            or parts.query or parts.fragment or any(char.isspace() for char in value)):
        raise ValueError('invalid_share_upstream')
    parts.port
    return value.rstrip('/')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *args):
        pass

    def send_error_json(self, status, code):
        data = json.dumps({'error': code}).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def deny(self):
        # Reject before consuming request bodies or touching the upstream.
        self.send_error_json(403, 'share_read_only')

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_CONNECT = do_TRACE = do_HEAD = deny

    def do_GET(self):
        if self.path == '/_health':
            self.send_error_json(200, 'ok')
            return
        raw_target = self.raw_requestline.split()[1].decode('iso-8859-1')
        if (raw_target != self.path or not allowed_path(self.path) or self.headers.get('Transfer-Encoding') is not None
                or self.headers.get('Content-Length', '0') != '0'):
            self.deny()
            return
        response_started = False
        try:
            req = Request(self.server.upstream + self.path, method='GET',
                          headers={'Accept': 'application/json' if self.path in JSON_PATHS else '*/*'})
            with self.server.opener.open(req, timeout=25) as response:
                if response.status != 200:
                    raise ValueError('unexpected_upstream_status')
                if self.path in JSON_PATHS:
                    raw = response.read(MAX_JSON + 1)
                    if len(raw) > MAX_JSON:
                        raise ValueError('result_too_large')
                    data = json.dumps(project_response(self.path, json.loads(raw)), ensure_ascii=False).encode()
                    size, content_type = len(data), 'application/json'
                else:
                    size = int(response.headers.get('Content-Length', '-1'))
                    content_type = response.headers.get('Content-Type', '').split(';')[0]
                    if not 0 < size <= MAX_MEDIA or content_type not in ('video/mp4', 'image/jpeg', 'image/png'):
                        raise ValueError('invalid_media_response')
                    data = None
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(size))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                response_started = True
                if data is not None:
                    self.wfile.write(data)
                else:
                    remaining = size
                    while remaining:
                        block = response.read(min(1024 * 1024, remaining))
                        if not block:
                            self.close_connection = True
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
        except HTTPError as exc:
            self.send_error_json(404 if exc.code == 404 else 502, 'saved_result_unavailable')
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (ValueError, TypeError, KeyError, OSError, URLError):
            if response_started:
                self.close_connection = True
            else:
                self.send_error_json(502, 'saved_result_unavailable')


def make_server(address=('0.0.0.0', 8081), upstream=None):
    server = ThreadingHTTPServer(address, Handler)
    server.upstream = upstream_url(upstream or os.environ.get('CCTV_SHARE_UPSTREAM', 'http://be-api:8080'))
    server.opener = build_opener(ProxyHandler({}), NoRedirect())
    return server


if __name__ == '__main__':
    make_server().serve_forever()
