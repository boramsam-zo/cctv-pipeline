"""Read-only report capabilities and safe display text for saved reports."""
from html import escape
from urllib.parse import urlsplit

from .client import APIError

TEAM_REPORT_SCHEMA = 'cctv-team-report-v1'
TEAM_SCENE_SCHEMA = 'cctv-team-scene-v1'


def report_pipeline(getter):
    """Only an absent capability endpoint means the previous date-aware API."""
    try:
        value = getter('/report-pipeline')
    except APIError as exc:
        if exc.status != 404:
            raise
        return {'pipeline_id': 'legacy', 'document_reference_date_supported': True}
    if (not isinstance(value, dict) or not isinstance(value.get('pipeline_id'), str)
            or not value['pipeline_id']
            or type(value.get('document_reference_date_supported')) is not bool):
        raise RuntimeError('보고 경로의 문서 검색 기준일 지원 정보를 확인할 수 없습니다.')
    return value


def display_markdown(body):
    """Preserve the saved download while disabling HTML and embedded image fetches."""
    if not isinstance(body, str) or not body.strip():
        raise ValueError('stored_report_markdown_missing')
    return escape(body, quote=False).replace('![', r'\![')


def source_link(value):
    """Only explicit HTTP(S) source links become clickable controls."""
    if not isinstance(value, str) or any(character.isspace() or ord(character) < 32
                                         for character in value):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None):
            return None
        parts.port  # Reject malformed port text without following the URL.
    except ValueError:
        return None
    return value
