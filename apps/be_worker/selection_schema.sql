BEGIN;
CREATE TABLE IF NOT EXISTS cctv_jobs.analysis_tasks (
    task_id text PRIMARY KEY CHECK (task_id ~ '^[0-9a-f]{32}$'),
    worker_job_id text NOT NULL REFERENCES cctv_jobs.queue(worker_job_id),
    purpose text NOT NULL CHECK (purpose IN ('gpu_comparison','full_report')),
    version text NOT NULL CHECK (version IN ('A','B')),
    gpu_key text NOT NULL,
    reference_date date,
    generation_settings jsonb,
    status text NOT NULL DEFAULT 'queued',
    paused boolean NOT NULL DEFAULT false,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(worker_job_id, purpose),
    CHECK ((purpose='gpu_comparison' AND generation_settings IS NULL AND reference_date IS NULL)
        OR (purpose='full_report' AND generation_settings IS NOT NULL AND reference_date IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS cctv_jobs.analysis_selections (
    selection_id text PRIMARY KEY CHECK (selection_id ~ '^[0-9a-f]{32}$'),
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    selection text NOT NULL CHECK (selection IN ('A','B','A+B')),
    purpose text NOT NULL CHECK (purpose IN ('gpu_comparison','full_report')),
    children jsonb NOT NULL CHECK (jsonb_typeof(children)='array'),
    created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON cctv_jobs.analysis_selections TO cctv_app;
GRANT SELECT, INSERT, UPDATE ON cctv_jobs.analysis_tasks TO cctv_app;
COMMIT;
