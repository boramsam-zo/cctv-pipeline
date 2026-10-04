-- Additive only: existing scene rows and document vectors remain unchanged.
BEGIN;
CREATE TABLE IF NOT EXISTS cctv_scene.report_runs (
    report_id uuid PRIMARY KEY,
    analysis_id uuid NOT NULL UNIQUE REFERENCES cctv_scene.descriptions(analysis_id),
    worker_job_id text NOT NULL,
    settings jsonb NOT NULL,
    state jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE ON cctv_scene.report_runs TO cctv_app;
COMMIT;
