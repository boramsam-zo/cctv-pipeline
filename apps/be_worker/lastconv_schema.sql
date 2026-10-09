-- Optional third classifier. Apply after mlops_schema.sql during LC deployment.
-- Existing jobs, saved settings, quotas and results are left intact.
BEGIN;
ALTER TABLE cctv_jobs.analysis_tasks DROP CONSTRAINT IF EXISTS analysis_tasks_version_check;
ALTER TABLE cctv_jobs.analysis_tasks ADD CONSTRAINT analysis_tasks_version_check
    CHECK (version IN ('A','B','R2','LC'));
ALTER TABLE cctv_jobs.analysis_selections DROP CONSTRAINT IF EXISTS analysis_selections_selection_check;
ALTER TABLE cctv_jobs.analysis_selections ADD CONSTRAINT analysis_selections_selection_check
    CHECK (selection IN ('A','B','A+B','R2','LC'));
ALTER TABLE cctv_jobs.analysis_tasks DROP CONSTRAINT IF EXISTS analysis_tasks_settings_date_check;
ALTER TABLE cctv_jobs.analysis_tasks ADD CONSTRAINT analysis_tasks_settings_date_check CHECK (
    (purpose='gpu_comparison' AND generation_settings IS NULL AND reference_date IS NULL) OR
    (purpose='full_report' AND generation_settings IS NOT NULL AND (
        (generation_settings->>'schema_version'='be-generation-settings-team-rag-v1'
            AND version IN ('B','R2','LC') AND reference_date IS NULL) OR
        (generation_settings->>'schema_version' IS DISTINCT FROM 'be-generation-settings-team-rag-v1'
            AND reference_date IS NOT NULL))));
COMMIT;
