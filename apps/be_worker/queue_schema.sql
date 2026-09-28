-- Apply as the DB administrator. No change to RAG/scene tables.
BEGIN;
CREATE SCHEMA IF NOT EXISTS cctv_jobs;
CREATE TABLE IF NOT EXISTS cctv_jobs.queue (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    worker_job_id text NOT NULL UNIQUE CHECK (worker_job_id ~ '^[a-f0-9]{32}$'),
    request_key text NOT NULL UNIQUE CHECK (length(request_key) BETWEEN 1 AND 120),
    directory_id text NOT NULL UNIQUE CHECK (directory_id ~ '^[a-f0-9]{32}$'),
    state jsonb NOT NULL,
    handoff jsonb,
    submit_attempts integer NOT NULL DEFAULT 0 CHECK (submit_attempts >= 0),
    retry_after timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state->>'worker_job_id' = worker_job_id),
    CHECK (state->>'schema_version' = 'be-runpod-v1'),
    CHECK (state->>'status' IN ('prepared','submitting','waiting','submission_unknown',
      'submission_rejected','provider_failed','evidence_ready','storage_not_ready',
      'verification_failed','handoff_ready','no_candidates'))
);
CREATE INDEX IF NOT EXISTS queue_status_idx ON cctv_jobs.queue ((state->>'status'), sequence);
CREATE UNIQUE INDEX IF NOT EXISTS queue_provider_identity_idx ON cctv_jobs.queue
    ((state->'config'->>'runpod_url'), (state->>'provider_job_id'))
    WHERE state->>'provider_job_id' IS NOT NULL;
GRANT USAGE ON SCHEMA cctv_jobs TO cctv_app;
GRANT SELECT, INSERT, UPDATE ON cctv_jobs.queue TO cctv_app;
GRANT USAGE, SELECT ON SEQUENCE cctv_jobs.queue_sequence_seq TO cctv_app;
COMMIT;
