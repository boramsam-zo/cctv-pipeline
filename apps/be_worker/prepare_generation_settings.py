"""User-run additive P5 schema change; preserve all pre-existing row values."""
import argparse
import json
import subprocess


def sql():
    tables = ('cctv_jobs.queue', 'cctv_jobs.flows', 'cctv_scene.descriptions',
              'cctv_scene.report_runs', 'cctv_rag.chunks')
    parts = ["BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s';",
             'LOCK TABLE ' + ', '.join(tables) + ' IN SHARE MODE;']
    for i, table in enumerate(tables):
        value = "to_jsonb(t)-'generation_settings'" if table == 'cctv_jobs.flows' else 'to_jsonb(t)'
        parts.append(f'CREATE TEMP TABLE p5_before_{i} ON COMMIT DROP AS SELECT {value} AS value FROM {table} t;')
    parts.append('ALTER TABLE cctv_jobs.flows ADD COLUMN IF NOT EXISTS generation_settings jsonb;')
    for i, table in enumerate(tables):
        value = "to_jsonb(t)-'generation_settings'" if table == 'cctv_jobs.flows' else 'to_jsonb(t)'
        parts.append(f'''DO $$ BEGIN IF EXISTS (
            (SELECT value FROM p5_before_{i} EXCEPT ALL SELECT {value} FROM {table} t)
            UNION ALL (SELECT {value} FROM {table} t EXCEPT ALL SELECT value FROM p5_before_{i}))
            THEN RAISE EXCEPTION 'p5_existing_data_changed'; END IF; END $$;''')
    parts.append('''DO $$ BEGIN
        IF (SELECT atttypid FROM pg_attribute WHERE attrelid='cctv_jobs.flows'::regclass
            AND attname='generation_settings' AND NOT attisdropped) <> 'jsonb'::regtype
        THEN RAISE EXCEPTION 'p5_wrong_settings_column_type'; END IF;
        IF NOT has_column_privilege('cctv_app','cctv_jobs.flows','generation_settings','INSERT')
            OR NOT has_column_privilege('cctv_app','cctv_jobs.flows','generation_settings','UPDATE')
        THEN RAISE EXCEPTION 'p5_settings_privileges_missing'; END IF;
        END $$;
        SET LOCAL ROLE cctv_app;
        SELECT generation_settings FROM cctv_jobs.flows LIMIT 0;
        RESET ROLE;
        SELECT json_build_object('status','generation_settings_schema_ready',
            'existing_data_unchanged',true, 'model_calls',0,
            'rag_chunks',(SELECT count(*) FROM cctv_rag.chunks),
            'flows',(SELECT count(*) FROM cctv_jobs.flows));
        COMMIT;''')
    return '\n'.join(parts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose', default='/opt/cctv-stack/compose.yaml')
    args = parser.parse_args()
    result = subprocess.run(['docker', 'compose', '-f', args.compose, 'exec', '-T',
        '--user', 'postgres', 'db', 'psql', '-X', '-q', '-A', '-t', '-v',
        'ON_ERROR_STOP=1', '-d', 'cctv'], input=sql(), text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise SystemExit('generation_settings_schema_failed; transaction not confirmed; inspect DB locally')
    receipt = json.loads(result.stdout.strip())
    print(json.dumps(dict(receipt, transaction_committed=True), indent=2))
