"""User-run additive A/B task schema; existing results remain byte-equivalent."""
import argparse
import json
from pathlib import Path
import subprocess


def sql():
    tables=('cctv_jobs.queue','cctv_jobs.flows','cctv_scene.descriptions',
            'cctv_scene.report_runs','cctv_rag.chunks')
    parts=["BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s';",
           'LOCK TABLE '+', '.join(tables)+' IN SHARE MODE;']
    for i,table in enumerate(tables):
        parts.append(f'CREATE TEMP TABLE p6_before_{i} ON COMMIT DROP AS SELECT to_jsonb(t) AS value FROM {table} t;')
    parts.append(Path(__file__).with_name('selection_schema.sql').read_text().replace('BEGIN;','').replace('COMMIT;',''))
    for i,table in enumerate(tables):
        parts.append(f'''DO $$ BEGIN IF EXISTS (
            (SELECT value FROM p6_before_{i} EXCEPT ALL SELECT to_jsonb(t) FROM {table} t)
            UNION ALL (SELECT to_jsonb(t) FROM {table} t EXCEPT ALL SELECT value FROM p6_before_{i}))
            THEN RAISE EXCEPTION 'p6_existing_data_changed'; END IF; END $$;''')
    parts.append('''SET LOCAL ROLE cctv_app;
        SELECT task_id,worker_job_id,purpose,version,gpu_key,generation_settings,status,paused FROM cctv_jobs.analysis_tasks LIMIT 0;
        SELECT selection_id,input_sha256,selection,purpose,children FROM cctv_jobs.analysis_selections LIMIT 0;
        RESET ROLE;
        SELECT json_build_object('status','analysis_selection_schema_ready',
            'existing_data_unchanged',true,'model_calls',0,
            'rag_chunks',(SELECT count(*) FROM cctv_rag.chunks),
            'queue_jobs',(SELECT count(*) FROM cctv_jobs.queue));
        COMMIT;''')
    return '\n'.join(parts)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose',default='/opt/cctv-stack/compose.yaml')
    args=parser.parse_args()
    result=subprocess.run(['docker','compose','-f',args.compose,'exec','-T','--user','postgres','db',
        'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-d','cctv'],input=sql(),
        text=True,capture_output=True,timeout=90)
    if result.returncode:raise SystemExit('analysis_schema_failed; transaction not confirmed; inspect DB locally')
    print(json.dumps(dict(json.loads(result.stdout.strip()),transaction_committed=True),indent=2))
