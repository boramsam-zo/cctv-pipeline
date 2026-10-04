"""UI server-side client. No credentials or Runpod addresses in browser code."""
import json
import os
import re
import urllib.request
import urllib.error


class APIError(RuntimeError):
    """Keep HTTP status for optional API capabilities without hiding other errors."""

    def __init__(self, detail, status):
        super().__init__(detail)
        self.status = status


def request(path, data=None, binary=False):
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
