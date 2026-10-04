"""User-run EC2 schema preparation, preserving the existing RAG corpus.

No image build, secret setup, external API, or model call. Both existing schema
scripts and the verification commit together, or roll back together.
"""
import argparse
import json
from pathlib import Path
import subprocess


def schema_body(path):
    lines = path.read_text().splitlines()
    if lines.count('BEGIN;') != 1 or lines.count('COMMIT;') != 1:
        raise ValueError('schema_transaction_layout_changed')
    return '\n'.join(line for line in lines if line not in ('BEGIN;', 'COMMIT;'))


def sql():
    root = Path(__file__).resolve().parent
    before = '''
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
-- A missing corpus is an error, never a reason to create an empty replacement.
LOCK TABLE cctv_rag.chunks IN SHARE MODE;
CREATE TEMP TABLE stage1_rag_before ON COMMIT DROP AS
SELECT chunk_id, embedding_profile, input_sha256, to_jsonb(c) AS original
FROM cctv_rag.chunks AS c;
'''
    after = '''
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_extension WHERE extname = 'vector') THEN
    RAISE EXCEPTION 'pgvector_missing';
  END IF;
  IF (SELECT count(*) FROM stage1_rag_before) = 0 THEN
    RAISE EXCEPTION 'existing_rag_corpus_empty';
  END IF;
  IF EXISTS (
    SELECT FROM stage1_rag_before b FULL JOIN cctv_rag.chunks c
      USING (chunk_id, embedding_profile, input_sha256)
    WHERE b.original IS DISTINCT FROM to_jsonb(c)
  ) THEN
    RAISE EXCEPTION 'rag_corpus_changed';
  END IF;
  IF NOT has_sequence_privilege('cctv_app', 'cctv_jobs.queue_sequence_seq', 'USAGE')
     OR NOT has_table_privilege('cctv_app', 'cctv_jobs.queue', 'INSERT')
     OR NOT has_table_privilege('cctv_app', 'cctv_jobs.queue', 'UPDATE')
     OR NOT has_table_privilege('cctv_app', 'cctv_scene.descriptions', 'INSERT') THEN
    RAISE EXCEPTION 'be_write_privileges_missing';
  END IF;
END $$;
-- Resolve required columns and read permissions as the actual BE database role.
SET LOCAL ROLE cctv_app;
SELECT sequence, worker_job_id, request_key, directory_id, state, handoff,
       submit_attempts, retry_after, created_at, updated_at
FROM cctv_jobs.queue LIMIT 0;
SELECT analysis_id, provider_job_id, event_id, model, assessment,
       accident_suspicion, incident_types, result_sha256, payload, created_at
FROM cctv_scene.descriptions LIMIT 0;
SELECT chunk_id, embedding_profile, input_sha256, document_id, content,
       metadata, vector_dims(embedding), indexed_at FROM cctv_rag.chunks LIMIT 0;
RESET ROLE;
SELECT json_build_object(
  'stage', 1, 'status', 'passed', 'rag_unchanged', true,
  'rag_chunk_count', (SELECT count(*) FROM cctv_rag.chunks),
  'queue_count', (SELECT count(*) FROM cctv_jobs.queue),
  'description_count', (SELECT count(*) FROM cctv_scene.descriptions),
  'required_columns_and_privileges', true,
  'model_calls', 0,
  'next_stage', 'be_container_runpod_auth_and_s3_read');
COMMIT;
'''
    return before + schema_body(root / 'queue_schema.sql') + '\n' + schema_body(
        root.parent / 'scene_description/schema.sql') + '\n' + after


def prepare(compose, runner=subprocess.run):
    result = runner(['docker', 'compose', '-f', str(compose), 'exec', '-T',
                     '--user', 'postgres', 'db', 'psql', '-X', '-q', '-A', '-t',
                     '-v', 'ON_ERROR_STOP=1', '-d', 'cctv'],
                    input=sql(), text=True, capture_output=True, timeout=90)
    if result.returncode:
        # No success receipt on a failed transaction. Avoid exposing connection
        # details; an operator can inspect the database locally when necessary.
        raise RuntimeError('stage1_failed_transaction_not_confirmed; inspect EC2 DB locally')
    receipt = json.loads(result.stdout.strip())
    if receipt.get('stage') != 1 or receipt.get('status') != 'passed' or receipt.get('rag_unchanged') is not True:
        raise ValueError('stage1_invalid_receipt')
    return dict(receipt, transaction_committed=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--compose', type=Path, default=Path('/opt/cctv-stack/compose.yaml'))
    args = parser.parse_args()
    print(json.dumps(prepare(args.compose), ensure_ascii=False, indent=2))
