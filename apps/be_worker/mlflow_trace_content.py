"""Readable, bounded MLflow content projections; never transport secrets or media."""
from copy import deepcopy
import hashlib
import json
import math
import re
from urllib.parse import urlsplit, parse_qsl

CONTENT_VERSION = 'cctv-readable-trace-v1'
TEXT_LIMIT = 24000
ITEM_LIMIT = 64
RECORD_LIMIT = 256 * 1024
READABLE_FIELDS = {'request', 'response', 'report_markdown', 'scene_json', 'queries',
                   'title', 'excerpt', 'agency', 'query', 'generation',
                   'retrieval_config', 'report_summary', 'section', 'reasons', 'reason'}
# Keep measured accounting/provenance untouched even when content must shrink.
PROTECTED_FIELDS = {'usage', 'new_model_calls', 'status', 'capture_status', 'reused',
                    'provider_model', 'provider_model_version', 'model_id', 'model_version',
                    'error_type', 'error_code', 'document_count', 'query_count',
                    'content_version', 'prompt_provenance', 'generation_config',
                    'video_metadata', 'mime_type', 'finish_reason', 'reference_id',
                    'selected_chunk_ids', 'bm25_top20', 'embedding_top20', 'search',
                    'rrf', 'score', 'rank', 'accepted'}
_SECRET_KEY = re.compile(r'(?i)^(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret(?:[_-]?access[_-]?key)?|aws[_-]?(?:access[_-]?key[_-]?id|secret[_-]?access[_-]?key|session[_-]?token)|x-api-key)$')
_URL = re.compile(r'https?://[^\s<>"\']+')
_ASSIGNMENT = re.compile(r'(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret[_-]?access[_-]?key|aws[_-]?session[_-]?token)["\']?\s*[:=]\s*["\']?(?:Bearer\s+)?[^\s,;"\']+')
_KNOWN_SECRET = re.compile(r'\b(?:AIza[0-9A-Za-z_-]{30,}|(?:AKIA|ASIA)[0-9A-Z]{16}|sk-[0-9A-Za-z_-]{20,})\b')
_BASE64 = re.compile(r'(?:data:[^\s,;]+;base64,)?[A-Za-z0-9+/]{256,}={0,2}')
_PRIVATE_KEY = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', re.S)


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def _url(match):
    value = match.group(0)
    try:
        parsed = urlsplit(value)
        keys = [k.lower() for k, _ in parse_qsl(parsed.query)]
        if parsed.username or parsed.password or any(
                k.startswith(('x-amz-', 'x-goog-')) or k in
                ('signature', 'sig', 'token', 'access_token', 'key', 'api_key', 'credential') for k in keys):
            return '[signed or authenticated URL omitted]'
    except ValueError:
        return '[invalid URL omitted]'
    return value


def clean_text(value, *, limit=TEXT_LIMIT, secrets=()):
    """Keep natural language; redact narrowly and mark every size truncation."""
    if not isinstance(value, str):
        return None
    for secret in secrets:
        if isinstance(secret, str) and secret:
            value = value.replace(secret, '[credential omitted]')
    value = _PRIVATE_KEY.sub('[private key omitted]', value)
    value = _URL.sub(_url, value)
    value = _ASSIGNMENT.sub(lambda m: m.group(1) + '=[credential omitted]', value)
    value = _KNOWN_SECRET.sub('[credential omitted]', value)
    value = _BASE64.sub('[binary/base64 omitted]', value)
    if len(value) > limit:
        return value[:limit] + '\n[truncated: ' + str(len(value) - limit) + ' chars omitted]'
    return value


def content(value, *, secrets=(), text_limit=TEXT_LIMIT, item_limit=ITEM_LIMIT, _depth=0):
    """Sanitize only explicitly selected content, not arbitrary transport payloads."""
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return clean_text(value, limit=text_limit, secrets=secrets)
    if _depth >= 12:
        return '[truncated: nesting limit]'
    if isinstance(value, (list, tuple)):
        result = [content(v, secrets=secrets, text_limit=text_limit, item_limit=item_limit,
                          _depth=_depth + 1) for v in value[:item_limit]]
        if len(value) > item_limit:
            result.append({'truncated': True, 'omitted_items': len(value) - item_limit})
        return result
    if isinstance(value, dict):
        result = {}
        for k, v in list(value.items())[:item_limit]:
            if not isinstance(k, str):
                continue
            if _SECRET_KEY.fullmatch(k):
                result[k] = '[credential omitted]'
            elif k in ('inlineData', 'inline_data', 'fileData', 'file_data', 'data', 'bytes'):
                result[k] = '[media or binary omitted]'
            else:
                result[clean_text(k, limit=160, secrets=secrets)] = content(
                    v, secrets=secrets, text_limit=text_limit, item_limit=item_limit, _depth=_depth + 1)
        if len(value) > item_limit:
            result['_truncation'] = {'omitted_fields': len(value) - item_limit}
        return result
    return '[unsupported content omitted]'


def _parts(parts, *, secrets=()):
    result = []
    for part in (parts if isinstance(parts, list) else [])[:ITEM_LIMIT]:
        if not isinstance(part, dict):
            continue
        # Provider thoughts/signatures are deliberately excluded; only visible output.
        if part.get('thought') is True:
            continue
        if isinstance(part.get('text'), str):
            result.append({'type': 'text', 'text': clean_text(part['text'], secrets=secrets)})
        for name in ('inlineData', 'fileData'):
            media = part.get(name)
            if isinstance(media, dict):
                result.append({'type': 'text', 'text': '[media omitted]',
                    'mime_type': clean_text(media.get('mimeType'), limit=100),
                    'video_metadata': content(part.get('videoMetadata', {}), secrets=secrets)})
    if isinstance(parts, list) and len(parts) > ITEM_LIMIT:
        result.append({'type': 'text', 'text': '[truncated: ' + str(len(parts) - ITEM_LIMIT) + ' parts omitted]'})
    return result


def gemini_request(data, provenance=None, *, secrets=()):
    """Read exact already-built request, projecting only text/config/media metadata."""
    result = {'content_version': CONTENT_VERSION}
    if isinstance(data, (bytes, str)):
        encoded = data if isinstance(data, bytes) else data.encode()
        result['request_sha256'] = sha256(encoded)
        if len(encoded) > 18 * 1024 * 1024:
            result['capture_status'] = 'omitted_request_above_18_mib'
            return result
        try:
            data = json.loads(data)
        except (ValueError, UnicodeError):
            result['capture_status'] = 'request_not_json'
            return result
    if not isinstance(data, dict):
        result['capture_status'] = 'request_not_object'
        return result
    messages = []
    system = data.get('systemInstruction', {})
    if isinstance(system, dict):
        parts = _parts(system.get('parts'), secrets=secrets)
        if parts:
            messages.append({'role': 'system', 'content': parts})
    for row in data.get('contents', [])[:ITEM_LIMIT]:
        if isinstance(row, dict):
            messages.append({'role': row.get('role') if row.get('role') in ('user', 'model', 'assistant') else 'user',
                             'content': _parts(row.get('parts'), secrets=secrets)})
    result['messages'] = messages
    config = data.get('generationConfig', {})
    result['generation_config'] = content({k: config[k] for k in (
        'responseMimeType', 'maxOutputTokens', 'temperature', 'topP', 'topK', 'seed') if k in config}, secrets=secrets)
    if isinstance(provenance, dict):
        result['prompt_provenance'] = content({k: provenance[k] for k in (
            'prompt_version', 'request_sha256', 'video_sample_fps', 'contract_sha256',
            'input_sha256', 'analysis_id', 'provider_job_id', 'event_id', 'team_event_id') if k in provenance}, secrets=secrets)
    result['capture_status'] = 'text_and_config_captured_media_omitted'
    return result


def gemini_response(raw, *, secrets=()):
    """Capture actual visible candidate text/JSON; do not invent validated results."""
    result = {'content_version': CONTENT_VERSION, 'candidates': []}
    if not isinstance(raw, dict):
        result['capture_status'] = 'response_not_object'
        return result
    for row in raw.get('candidates', [])[:ITEM_LIMIT]:
        if not isinstance(row, dict):
            continue
        parts = _parts((row.get('content') or {}).get('parts'), secrets=secrets)
        entry = {'finish_reason': clean_text(row.get('finishReason'), limit=100),
                 'message': {'role': 'assistant', 'content': parts}}
        texts = ''.join(p['text'] for p in (row.get('content') or {}).get('parts', [])
                        if isinstance(p, dict) and isinstance(p.get('text'), str) and not p.get('thought'))
        if texts and len(texts) <= 256 * 1024:
            try:
                entry['response_json'] = content(json.loads(texts), secrets=secrets)
            except (ValueError, UnicodeError):
                entry['response_json_status'] = 'not_json'
        result['candidates'].append(entry)
    if raw.get('promptFeedback'):
        result['prompt_feedback'] = content(raw['promptFeedback'], secrets=secrets)
    result['capture_status'] = 'visible_response_captured'
    return result


def _shrink_readable(value, *, text_limit, item_limit, key=None, inside=False, omit=False):
    """Only content is eligible: identity, result refs, accounting and timing survive."""
    if key in PROTECTED_FIELDS or (key and key.endswith(('_id', '_sha256', 'TokenCount'))):
        return value
    inside = inside or key in READABLE_FIELDS
    if inside and isinstance(value, str):
        return '[content omitted: trace_record_size_limit]' if omit else clean_text(value, limit=text_limit)
    if isinstance(value, dict):
        return {k: _shrink_readable(v, text_limit=text_limit, item_limit=item_limit,
                                   key=k, inside=inside, omit=omit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        # Metadata arrays (selected/checks/sources) retain their document IDs;
        # only explicitly readable lists such as message parts are shortened.
        rows = value[:item_limit] if inside else value
        result = [_shrink_readable(v, text_limit=text_limit, item_limit=item_limit,
                                  inside=inside, omit=omit) for v in rows]
        if len(rows) < len(value):
            result.append({'truncated': True, 'omitted_items': len(value) - len(rows)})
        return result
    return value


def bounded_record(record, limit=RECORD_LIMIT):
    """Preserve measured span tree/accounting when readable content exceeds the cap."""
    value = deepcopy(record)
    value['content_version'] = CONTENT_VERSION
    def size():
        return len((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode())
    original_size = size()
    if original_size <= limit:
        return value
    for text_limit, item_limit in ((6000, 32), (1500, 16), (400, 8), (100, 4), (0, 1)):
        for row in value['spans']:
            for key in ('inputs', 'outputs'):
                row[key] = _shrink_readable(row[key], text_limit=text_limit, item_limit=item_limit)
        value['content_truncation'] = {'reason': 'trace_record_size_limit',
            'original_bytes': original_size, 'maximum_bytes': limit,
            'text_limit': text_limit, 'item_limit': item_limit}
        if size() <= limit:
            return value
    # Existing metadata-only outboxes fit the cap. Preserve all metadata if a
    # future producer violates that assumption, and explicitly describe overflow.
    for row in value['spans']:
        for key in ('inputs', 'outputs'):
            row[key] = _shrink_readable(row[key], text_limit=0, item_limit=0, omit=True)
    if size() > limit:
        value['content_truncation']['metadata_exceeds_limit'] = True
    return value
