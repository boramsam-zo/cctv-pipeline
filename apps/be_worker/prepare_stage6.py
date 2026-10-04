"""User-run additive report table preparation; no model or vector mutation."""
import argparse
import json
from pathlib import Path
import subprocess
from prepare_stage1 import schema_body


def sql():
    return '''BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
LOCK TABLE cctv_rag.chunks, cctv_scene.descriptions IN SHARE MODE;
CREATE TEMP TABLE before_rag ON COMMIT DROP AS SELECT to_jsonb(c) AS original FROM cctv_rag.chunks c;
CREATE TEMP TABLE before_scene ON COMMIT DROP AS SELECT to_jsonb(c) AS original FROM cctv_scene.descriptions c;
''' + schema_body(Path(__file__).with_name('flow_schema.sql')) + '''
DO $$ BEGIN
IF EXISTS ((SELECT original FROM before_rag EXCEPT ALL SELECT to_jsonb(c) FROM cctv_rag.chunks c)
    UNION ALL (SELECT to_jsonb(c) FROM cctv_rag.chunks c EXCEPT ALL SELECT original FROM before_rag))
OR EXISTS ((SELECT original FROM before_scene EXCEPT ALL SELECT to_jsonb(c) FROM cctv_scene.descriptions c)
    UNION ALL (SELECT to_jsonb(c) FROM cctv_scene.descriptions c EXCEPT ALL SELECT original FROM before_scene))
THEN RAISE EXCEPTION 'existing_data_changed'; END IF;
IF NOT has_table_privilege('cctv_app', 'cctv_jobs.flows', 'INSERT')
OR NOT has_table_privilege('cctv_app', 'cctv_jobs.flows', 'UPDATE')
THEN RAISE EXCEPTION 'flow_permissions_missing'; END IF;
END $$;
SET LOCAL ROLE cctv_app;
SELECT worker_job_id, reference_date, status, paused, error_code FROM cctv_jobs.flows LIMIT 0;
SELECT name, payload, updated_at FROM cctv_jobs.service_state LIMIT 0;
RESET ROLE;
SELECT json_build_object('status', 'flow_schema_ready', 'existing_data_unchanged', true,
    'rag_chunks', (SELECT count(*) FROM cctv_rag.chunks),
    'scene_descriptions', (SELECT count(*) FROM cctv_scene.descriptions), 'model_calls', 0);
COMMIT;
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--compose', default='/opt/cctv-stack/compose.yaml')
    args = parser.parse_args()
    result = subprocess.run(['docker', 'compose', '-f', args.compose, 'exec', '-T', '--user', 'postgres',
        'db', 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-d', 'cctv'],
        input=sql(), text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise SystemExit('flow_schema_failed; transaction not confirmed; inspect DB locally')
    receipt = json.loads(result.stdout.strip())
    print(json.dumps(dict(receipt, transaction_committed=True), indent=2))
