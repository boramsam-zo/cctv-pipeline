"""Private Compose-network API for the operator UI; never executes models."""
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlsplit, parse_qs

from .common import MAX_VIDEO, WorkerError, checked_id
from .queue_store import connect, Store
from . import flow_service as flow, team_settings
from .source_media import open_source, BLOCK_BYTES

_cache_lock = threading.Lock()
_cache = (0, None)


def connection_status():
    global _cache
    with _cache_lock:
        if time.monotonic() - _cache[0] < 15: return _cache[1]
        try:
            answer = dict(connected=True, **flow.api_for({'config':flow.config()}).health())
        except Exception as exc:
            answer = dict(connected=False, error_code=getattr(exc,'code','runpod_unavailable'))
        _cache = (time.monotonic(), answer)
        return answer


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(120)
    def log_message(self, *args): pass
    def send(self, value, status=200, content_type='application/json'):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)
    def send_source(self, stream, size):
        # A full upload can be 200 MiB; keep it out of the API's byte buffers.
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        while block := stream.read(BLOCK_BYTES):
            self.wfile.write(block)
    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            if path == '/report-pipeline': return self.send(team_settings.metadata())
            if path == '/connection': return self.send(connection_status())
            with connect() as conn:
                if path == '/jobs': return self.send(flow.view(conn))
                if path == '/selections': return self.send(flow.selection_views(conn))
                if path == '/worker':
                    row = conn.execute("SELECT payload, updated_at, EXTRACT(EPOCH FROM (now()-updated_at)) AS age_seconds FROM cctv_jobs.service_state WHERE name='worker'").fetchone()
                    return self.send(row or {'age_seconds':None})
                parts = path.strip('/').split('/')
                if len(parts)==2 and parts[0]=='selections':
                    rows = flow.selection_views(conn, parts[1])
                    return self.send(rows[0] if rows else {'error':'not_found'}, 200 if rows else 404)
                if len(parts)==2 and parts[0]=='jobs':
                    rows = flow.view(conn, parts[1])
                    return self.send(rows[0] if rows else {'error':'not_found'}, 200 if rows else 404)
                if len(parts)==3 and parts[0]=='jobs' and parts[2]=='source':
                    with open_source(Store(conn), flow.spool(), parts[1]) as (stream, size):
                        return self.send_source(stream, size)
                if len(parts)==5 and parts[0]=='jobs' and parts[2]=='media':
                    if parts[4] not in ('clip','frame','roi_clip','clip_preview','roi_clip_preview'):
                        return self.send({'error':'invalid_media_request'},400)
                    data = flow.media(conn, parts[1], parts[3], parts[4])
                    content_type = ('image/jpeg' if data[:3] == b'\xff\xd8\xff' else 'image/png') if parts[4]=='frame' else 'video/mp4'
                    return self.send(data, content_type=content_type)
            self.send({'error':'not_found'},404)
        except (BrokenPipeError, ConnectionResetError):
            # Browser cancellation during a streaming read does not advance jobs.
            return
        except Exception as exc:
            code = getattr(exc,'code','read_failed')
            status = (404 if code in ('queue_job_not_found', 'source_media_not_found') else
                      400 if code == 'invalid_provider_job_id' else 503)
            self.send({'error':code},status)
    def do_POST(self):
        temporary = None
        try:
            path = urlsplit(self.path).path
            if path in ('/jobs','/selections'):
                if self.headers.get('Content-Type') != 'video/mp4':
                    return self.send({'error':'expected_video_mp4'},415)
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= MAX_VIDEO:return self.send({'error':'invalid_video_size'},413)
                query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                reference_date=query.get('reference_date',[''])[0]
                if path == '/selections':
                    if (set(query)-{'selection','purpose','reference_date'} or
                            any(len(values)!=1 for values in query.values())):
                        return self.send({'error':'invalid_selection_query'},400)
                    selection=query.get('selection',[''])[0]
                    purpose=query.get('purpose',[''])[0]
                    if selection not in ('A','B','A+B','R2') or purpose not in ('gpu_comparison','full_report'):
                        return self.send({'error':'invalid_analysis_selection'},400)
                    if purpose=='gpu_comparison':reference_date=None
                    elif team_settings.enabled():
                        if 'reference_date' in query:
                            return self.send({'error':'team_date_filter_unsupported'},400)
                        reference_date=None
                if reference_date is not None:
                    if date.fromisoformat(reference_date).isoformat()!=reference_date:raise ValueError()
                incoming=flow.spool()/'incoming';incoming.mkdir(exist_ok=True,mode=0o700)
                with tempfile.NamedTemporaryFile(dir=incoming,suffix='.mp4',delete=False) as stream:
                    temporary=Path(stream.name)
                    remaining=length
                    while remaining:
                        block=self.rfile.read(min(1024*1024,remaining))
                        if not block:raise WorkerError('upload_incomplete')
                        stream.write(block);remaining-=len(block)
                    stream.flush();os.fsync(stream.fileno())
                with connect() as conn:
                    if path == '/selections':
                        answer=flow.admit_selection(Store(conn),temporary,reference_date,selection,purpose)
                    else:answer=flow.admit(Store(conn),temporary,reference_date)
                return self.send(answer,202)
            task_match=re.fullmatch(r'/tasks/([a-f0-9]{32})/resume',path)
            if task_match:
                with connect() as conn:
                    return self.send(flow.resume_task(conn,checked_id(task_match[1])))
            match=re.fullmatch(r'/jobs/([a-f0-9]{32})/resume',path)
            if match:
                identifier=checked_id(match[1])
                with connect() as conn:
                    row=next((r for r in Store(conn).rows() if r['worker_job_id']==identifier),None)
                    if not row:return self.send({'error':'not_found'},404)
                    if row['state']['status'] in ('submitting','submission_unknown','provider_failed','verification_failed','storage_not_ready'):
                        return self.send({'error':'manual_provider_reconciliation_required'},409)
                    conn.execute("UPDATE cctv_jobs.flows SET paused=false, error_code=NULL, updated_at=now() WHERE worker_job_id=%s AND paused AND finished_at IS NULL",(identifier,))
                    return self.send({'status':'resume_requested'})
            self.send({'error':'not_found'},404)
        except Exception as exc:
            self.send({'error':getattr(exc,'code','request_failed')},409)
        finally:
            if temporary:temporary.unlink(missing_ok=True)


if __name__=='__main__':
    ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
