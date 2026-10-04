"""Append-only teammate observations; legacy descriptions remain unchanged."""
from apps.scene_description import scene, team_scene


def store(connection, result):
    from psycopg.types.json import Jsonb
    from psycopg.rows import tuple_row
    digest = team_scene.validate_result(result)
    with connection.transaction():
        with connection.cursor(row_factory=tuple_row) as cursor:
            cursor.execute('''INSERT INTO cctv_scene.team_descriptions
                (analysis_id,provider_job_id,event_id,model,result_sha256,result)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (analysis_id) DO NOTHING''',
                (result['analysis_id'], result['provider_job_id'], result['event_id'],
                 result['model'], digest, Jsonb(result)))
            inserted = cursor.rowcount == 1
            cursor.execute('''SELECT result_sha256,result FROM cctv_scene.team_descriptions
                              WHERE analysis_id=%s''', (result['analysis_id'],))
            row = cursor.fetchone()
            if row is None or row[0] != digest or scene.encoded(row[1]) != scene.encoded(result):
                raise ValueError('team_analysis_id_conflict_original_preserved')
    return {'analysis_id': result['analysis_id'], 'committed': True, 'inserted': inserted,
            'already_present': not inserted, 'result_sha256': digest,
            'gemini_api_calls': 0}


def show(connection, analysis_id):
    from psycopg.rows import tuple_row
    with connection.cursor(row_factory=tuple_row) as cursor:
        cursor.execute('''SELECT result_sha256,result FROM cctv_scene.team_descriptions
                          WHERE analysis_id=%s''', (analysis_id,))
        row = cursor.fetchone()
    if row is None:
        raise ValueError('team_description_not_stored')
    if team_scene.validate_result(row[1]) != row[0]:
        raise ValueError('team_stored_description_changed')
    return row[1]
