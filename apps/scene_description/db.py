"""Store an existing verified VLM result. No Gemini/S3 calls and no new embedding."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote
import uuid

if __package__:
    from .scene import encoded, finite, parse, read_limited, sha, validate_description, JSON_LIMIT
else:
    from scene import encoded, finite, parse, read_limited, sha, validate_description, JSON_LIMIT


def validate_result(result):
    if result.get('schema_version') not in ('cctv-scene-v1', 'cctv-scene-v2') or result.get('status') != 'completed':
        raise ValueError('Only completed scene descriptions can be stored')
    if str(uuid.UUID(result['analysis_id'])) != result['analysis_id']:
        raise ValueError('Invalid analysis ID')
    job, event = result['provider_job_id'], result['event_id']
    if not re.fullmatch(r'[a-f0-9]{32}', job) or not re.fullmatch(r'event_[0-9]+', event):
        raise ValueError('Invalid evidence identity')
    source = result['source']
    start, end = finite(source['source_start_s']), finite(source['source_end_s'])
    if not 0 <= start < end or end-start > 30:
        raise ValueError('Invalid source video interval')
    if source['manifest_key'] != f'cctv-evidence/{job}/manifest.json':
        raise ValueError('Result manifest belongs to another job')
    is_ab = source.get('evidence_version') == 'runpod-evidence-v2'
    if not is_ab and ('analysis_profile' in source or 'gpu_key' in source or 'evidence_version' in source):
        raise ValueError('Unknown or downgraded analysis evidence contract')
    if is_ab:
        from apps.scene_description import ab_scene
        ab_scene.validate_result_assets(result)
    for kind, ext in ([] if is_ab else [('clip', 'mp4'), ('frame', 'png')]):
        asset = source['assets'][kind]
        if (asset['key'] != f'cctv-evidence/{job}/evidence/{event}/{kind}.{ext}'
                or asset['asset_id'] != f'{job}/{event}/{kind}'):
            raise ValueError('Result media belongs to another event')
    description = result['description']
    raw = dict(description, observations=[{k: obs[k] for k in ['text', 'clip_times_seconds', 'asset_refs']}
                                        for obs in description['observations']])
    normalized = validate_description(raw, source, result['schema_version'])
    for observation, expected in zip(description['observations'], normalized['observations']):
        if observation['source_times_seconds'] != expected['source_times_seconds']:
            raise ValueError('Observation source time changed')
        if observation['evidence_asset_ids'] != [source['assets'][ref]['asset_id'] for ref in expected['asset_refs']]:
            raise ValueError('Observation evidence reference changed')
    return sha(encoded(result))


def store(connection, result):
    from psycopg.types.json import Jsonb
    from psycopg.rows import tuple_row
    digest = validate_result(result)
    with connection.transaction():
        with connection.cursor(row_factory=tuple_row) as cursor:
            cursor.execute('''INSERT INTO cctv_scene.descriptions
                (analysis_id, provider_job_id, event_id, model, assessment, result_sha256, payload,
                 accident_suspicion, incident_types)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (analysis_id) DO NOTHING''',
                (result['analysis_id'], result['provider_job_id'], result['event_id'], result['model'],
                 result['description'].get('assessment'), digest, Jsonb(result),
                 result['description'].get('accident_suspicion'),
                 Jsonb(result['description']['incident_types']) if 'incident_types' in result['description'] else None))
            inserted = cursor.rowcount == 1
            cursor.execute('SELECT result_sha256 FROM cctv_scene.descriptions WHERE analysis_id=%s',
                           (result['analysis_id'],))
            if cursor.fetchone() != (digest,):
                raise ValueError('Analysis ID already exists with different content; original preserved')
    return {'analysis_id': result['analysis_id'], 'committed': True, 'inserted': inserted,
            'already_present': not inserted, 'result_sha256': digest, 'gemini_api_calls': 0}


def connect():
    import psycopg
    url = os.environ.get('SCENE_DATABASE_URL')
    if not url:
        password = Path('/run/secrets/db_app_password').read_text().strip()
        if not re.fullmatch('[a-f0-9]{64}', password):
            raise ValueError('Invalid DB password file')
        url = f'postgresql://cctv_app:{quote(password, safe="")}@db:5432/cctv'
    return psycopg.connect(url, connect_timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['load', 'show'])
    parser.add_argument('--input', type=Path, default=Path('/result.json'))
    args = parser.parse_args()
    try:
        result = parse(read_limited(args.input, JSON_LIMIT))
        validate_result(result)
        with connect() as conn:
            if args.command == 'load':
                answer = store(conn, result)
            else:
                with conn.cursor() as cur:
                    cur.execute('SELECT payload FROM cctv_scene.descriptions WHERE analysis_id=%s', (result['analysis_id'],))
                    row = cur.fetchone()
                if row is None:
                    raise ValueError('Description has not been stored')
                answer = {'database_loaded': True, 'result': row[0]}
        print(json.dumps(answer, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f'{type(exc).__name__}: DB operation failed; original result preserved; no Gemini call', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
