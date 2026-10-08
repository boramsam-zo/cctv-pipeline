"""UI server-side client. No credentials or Runpod addresses in browser code."""
import json
import os
import re
import urllib.request
import urllib.error


_SHARE_READ_PATH = re.compile(
    r'(?:/jobs|/selections|/ui-snapshot|/ui-revision|/jobs/[a-f0-9]{32}/source|'
    r'/jobs/[a-f0-9]{32}/media/event_[0-9]+/'
    r'(?:clip|roi_clip|frame|clip_preview|roi_clip_preview))'
)


def is_read_only():
    """Only deployment configuration controls sharing; browser state cannot."""
    return os.environ.get('CCTV_UI_READ_ONLY') == '1'


class APIError(RuntimeError):
    """Keep HTTP status for optional API capabilities without hiding other errors."""

    def __init__(self, detail, status):
        super().__init__(detail)
        self.status = status


def request(path, data=None, binary=False):
    if os.environ.get('CCTV_DEMO_MODE') == '1':
        from .demo_gateway import route
        if not isinstance(path, str) or route(path, 'POST' if data is not None else 'GET') is None:
            raise APIError('시연에서 허용되지 않은 요청입니다.', 403)
    if is_read_only() and (data is not None or not isinstance(path, str)
                           or _SHARE_READ_PATH.fullmatch(path) is None):
        raise APIError('공유 화면에서는 저장된 결과만 조회할 수 있습니다.', 403)
    req=urllib.request.Request(os.environ.get('CCTV_API_URL','http://be-api:8080')+path,
        data=data, headers={'Content-Type':'video/mp4'} if data is not None else {})
    try:
        with urllib.request.urlopen(req,timeout=120 if data is not None else 25) as response:
            raw=response.read()
    except urllib.error.HTTPError as exc:
        try:detail=json.load(exc).get('error','api_error')
        except Exception:detail='api_error'
        raise APIError(detail, exc.code) from None
    except (OSError,urllib.error.URLError):
        raise RuntimeError('API 응답을 확인할 수 없습니다. 작업 목록에서 접수 여부를 먼저 확인하세요. 같은 영상·분석 버전·실행 범위는 기존 요청을 재사용하며 자동 재제출하지 않습니다.') from None
    return raw if binary else json.loads(raw)


def fetch_media(identifier, event, kind):
    """Fetch stored media through the private server-side API only.

    ``source`` is the entire uploaded video. Event clips and annotated frames
    keep their existing routes and validation; this never submits analysis.
    """
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('invalid_media_identifier')
    if kind in ('source', 'original'):
        return request(f'/jobs/{identifier}/source', binary=True)
    if (not isinstance(event, str) or not re.fullmatch(r'event_[0-9]+', event) or
            kind not in ('clip', 'roi_clip', 'frame', 'clip_preview', 'roi_clip_preview')):
        raise ValueError('invalid_media_request')
    return request(f'/jobs/{identifier}/media/{event}/{kind}', binary=True)
