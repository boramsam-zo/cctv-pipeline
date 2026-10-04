-- Additional teammate-contract table. No legacy rows or constraints are changed.
CREATE SCHEMA IF NOT EXISTS cctv_scene;
CREATE TABLE IF NOT EXISTS cctv_scene.team_descriptions (
    analysis_id uuid PRIMARY KEY,
    provider_job_id text NOT NULL,
    event_id text NOT NULL,
    model text NOT NULL,
    result_sha256 text NOT NULL CHECK (result_sha256 ~ '^[a-f0-9]{64}$'),
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (result->>'schema_version' = 'cctv-team-scene-v1'),
    CHECK (result->>'status' = 'completed'),
    CHECK (result->>'analysis_id' = analysis_id::text),
    CHECK (result->>'provider_job_id' = provider_job_id),
    CHECK (result->>'event_id' = event_id),
    CHECK (result->>'model' = model)
);
GRANT USAGE ON SCHEMA cctv_scene TO cctv_app;
GRANT SELECT, INSERT ON cctv_scene.team_descriptions TO cctv_app;
