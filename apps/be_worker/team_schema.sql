BEGIN;
CREATE TABLE IF NOT EXISTS cctv_scene.team_report_runs (
    report_id uuid PRIMARY KEY,
    analysis_id uuid NOT NULL UNIQUE REFERENCES cctv_scene.team_descriptions(analysis_id),
    worker_job_id text NOT NULL REFERENCES cctv_jobs.queue(worker_job_id),
    settings jsonb NOT NULL,
    state jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE ON cctv_scene.team_report_runs TO cctv_app;
-- Replace only the legacy purpose/date shape check. Other keys/constraints stay.
DO $$ DECLARE c record; BEGIN
    FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='cctv_jobs.analysis_tasks'::regclass
        AND contype='c' AND pg_get_constraintdef(oid) LIKE '%generation_settings%'
        AND pg_get_constraintdef(oid) LIKE '%reference_date%'
    LOOP EXECUTE format('ALTER TABLE cctv_jobs.analysis_tasks DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE cctv_jobs.analysis_tasks ADD CONSTRAINT analysis_tasks_settings_date_check CHECK (
    (purpose='gpu_comparison' AND generation_settings IS NULL AND reference_date IS NULL) OR
    (purpose='full_report' AND generation_settings IS NOT NULL AND (
        (generation_settings->>'schema_version'='be-generation-settings-team-rag-v1'
            AND version='B' AND reference_date IS NULL) OR
        (generation_settings->>'schema_version' IS DISTINCT FROM 'be-generation-settings-team-rag-v1'
            AND reference_date IS NOT NULL))));
COMMIT;
