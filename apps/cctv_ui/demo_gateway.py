"""Private finite-demo gateway. Only typed UI operations reach the private API."""
import json
import os
import re
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, urlencode
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

from . import share_gateway as share

_ITEM = re.compile(r'/(jobs|selections)/[a-f0-9]{32}')
_MLFLOW = re.compile(r'/mlflow/(?:models|runs(?:/[a-f0-9]{32}(?:/artifact)?)?|traces/(?:tr-)?[a-f0-9]{32})')


def route(target, method='GET'):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment or len(target) > 1500:
        return None
    path = parsed.path
    if '%' in path or '//' in path or any(p in ('.', '..') for p in path.split('/')):
        return None
    if path in ('/ui-snapshot', '/ui-revision') and '?' in target:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(len(v) != 1 for v in query.values()):
        return None
    if method == 'POST':
        if (path == '/selections' and set(query) == {'selection', 'purpose'}
                and query['selection'][0] in ('B', 'R2', 'LC') and query['purpose'] == ['full_report']):
            return '/demo/selections?' + urlencode({k: v[0] for k, v in query.items()})
        return None
    if method != 'GET':
        return None
    if not query and (path in ('/demo/status', '/report-pipeline', '/connection', '/worker')
                      or share.allowed_path(path) or _ITEM.fullmatch(path)):
        return path
    if _MLFLOW.fullmatch(path):
        allowed = ({'path'} if path.endswith('/artifact') else {'run_id'} if '/traces/' in path
                   else {'worker_job_id'} if path == '/mlflow/runs' else set())
        if set(query) <= allowed:
            return '/demo' + target
    return None


def projection(path, value):
    if path in ('/ui-snapshot', '/ui-revision'):
        answer = share.project_response(path, value)
        if path == '/ui-snapshot':
            answer.update(jobs=projection('/jobs', value['jobs']),
                          selections=projection('/selections', value['selections']))
        return answer
    if path in ('/jobs', '/selections'):
        answer = share.project_response(path, value)
        old_jobs = value if path == '/jobs' else [j for v in value for j in v.get('jobs', [])]
        new_jobs = answer if path == '/jobs' else [j for v in answer for j in v.get('jobs', [])]
        for old, new in zip(old_jobs, new_jobs):
            events = {e['event_id'] for e in old.get('evidence_events', [])}
            described = {d['event_id'] for d in old.get('descriptions', []) if d.get('event_id')}
            new['demo_summary'] = {'representative_event_ids': sorted(described),
                'unexplained_candidates': len(events - described),
                'policy': 'saved-descriptions-only'}
            for key in ('task_id', 'purpose'):
                if key in old: new[key] = old[key]
            if (old.get('mlflow') or {}).get('run_id'):
                new.setdefault('mlflow', {})['run_id'] = old['mlflow']['run_id']
        return answer
    if _ITEM.fullmatch(path):
        return projection('/' + path.split('/')[1], [value])[0]
    if path == '/worker':
        return {'age_seconds': value.get('age_seconds'),
                'payload': {'stage': (value.get('payload') or {}).get('stage')}}
    if path == '/connection':
        return {k: value[k] for k in ('connected', 'status', 'error_code', 'remaining_jobs') if k in value}
    if path == '/report-pipeline':
        return {k: value[k] for k in ('pipeline', 'pipeline_id', 'document_reference_date_supported',
                'analysis_models', 'schema_version', 'report_model') if k in value}
    return value


class Handler(share.Handler):
    def wire_safe(self):
        parts = self.requestline.split()
        lengths = self.headers.get_all('Content-Length', [])
        return (len(parts) == 3 and parts[1] == self.path and not self.headers.get('Transfer-Encoding')
                and len(lengths) <= 1 and (self.command == 'POST' or lengths in ([], ['0'])))

    def json_read(self, target):
        with self.server.opener.open(Request(self.server.upstream + target), timeout=15) as response:
            raw = response.read(share.MAX_JSON + 1)
            if len(raw) > share.MAX_JSON: raise ValueError('demo_response_too_large')
            return json.loads(raw)

    def window(self):
        return self.json_read('/demo/status')

    def respond(self, data, status=200, content_type='application/json'):
        if not isinstance(data, bytes): data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.wire_safe(): return self.send_error_json(403, 'demo_route_not_allowed')
        target = route(self.path)
        if target is None: return self.send_error_json(403, 'demo_route_not_allowed')
        try:
            window = self.window()
            if target == '/demo/status': return self.respond(window)
            if not window.get('active'): return self.send_error_json(410, 'demo_closed')
            if share.MEDIA_PATH.fullmatch(target):
                # Existing streaming read validates content type and byte length.
                return super().do_GET()
            value = self.json_read(target)
            return self.respond(projection(urlsplit(self.path).path, value))
        except HTTPError as exc:
            return self.send_error_json(exc.code if exc.code in (400, 403, 404, 409, 410) else 502, 'demo_read_unavailable')
        except (ValueError, TypeError, KeyError, OSError, URLError):
            return self.send_error_json(503, 'demo_read_unavailable')

    def do_POST(self):
        if not self.wire_safe(): return self.send_error_json(403, 'demo_route_not_allowed')
        target = route(self.path, 'POST')
        if target is None: return self.send_error_json(403, 'demo_route_not_allowed')
        if not self.server.admission.acquire(blocking=False):
            return self.send_error_json(409, 'demo_admission_busy')
        try:
            window = self.window()
            if not window.get('active'): return self.send_error_json(410, 'demo_closed')
            selection = parse_qs(urlsplit(target).query)['selection'][0]
            if selection not in window.get('allowed_selections', ('B', 'R2')):
                return self.send_error_json(403, 'demo_model_not_allowed')
            length = int(self.headers.get('Content-Length', '0'))
            if (self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type') != 'video/mp4'
                    or not 0 < length <= window['max_upload_bytes']):
                return self.send_error_json(413, 'demo_video_size_or_type')
            payload = self.rfile.read(length)
            if len(payload) != length: return self.send_error_json(400, 'upload_incomplete')
            # The API rechecks time, video metadata, DB cap, and active job under its ledger lock.
            req = Request(self.server.upstream + target, data=payload, headers={'Content-Type': 'video/mp4'})
            with self.server.opener.open(req, timeout=120) as response:
                raw = response.read(1024 * 1024)
                self.respond(json.loads(raw), response.status)
        except HTTPError as exc:
            try: code = json.loads(exc.read(4096)).get('error', 'demo_submission_failed')
            except (ValueError, OSError): code = 'demo_submission_failed'
            if not isinstance(code, str) or not re.fullmatch('[a-z0-9_]{1,100}', code): code = 'demo_submission_failed'
            self.send_error_json(exc.code if exc.code in (400, 403, 409, 410, 413, 415) else 502, code)
        except (ValueError, TypeError, KeyError, OSError, URLError):
            self.send_error_json(503, 'demo_submission_unknown_check_history')
        finally:
            self.server.admission.release()


def make_server(address=('0.0.0.0', 8082), upstream=None):
    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = True
    server.admission = threading.Lock()
    server.upstream = share.upstream_url(upstream or os.environ.get('CCTV_DEMO_UPSTREAM', 'http://be-api:8080'))
    server.opener = build_opener(ProxyHandler({}), share.NoRedirect())
    return server


if __name__ == '__main__':
    make_server().serve_forever()
