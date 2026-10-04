BEGIN;
CREATE TABLE IF NOT EXISTS cctv_jobs.flows (
    worker_job_id text PRIMARY KEY REFERENCES cctv_jobs.queue(worker_job_id),
    reference_date date NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    paused boolean NOT NULL DEFAULT false,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
-- Additive upgrade: NULL identifies rows admitted before settings were pinned.
ALTER TABLE cctv_jobs.flows ADD COLUMN IF NOT EXISTS generation_settings jsonb;
CREATE TABLE IF NOT EXISTS cctv_jobs.service_state (
    name text PRIMARY KEY,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE ON cctv_jobs.flows, cctv_jobs.service_state TO cctv_app;
COMMIT;
