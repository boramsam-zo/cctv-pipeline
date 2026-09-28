-- One VLM description attempt; separate from document vectors and final reports.
BEGIN;
CREATE SCHEMA IF NOT EXISTS cctv_scene;
CREATE TABLE IF NOT EXISTS cctv_scene.descriptions (
    analysis_id uuid PRIMARY KEY,
    provider_job_id text NOT NULL CHECK (provider_job_id ~ '^[a-f0-9]{32}$'),
    event_id text NOT NULL CHECK (event_id ~ '^event_[0-9]+$'),
    model text NOT NULL,
    assessment text NOT NULL CHECK (assessment IN ('collision_suspected', 'no_visible_collision', 'uncertain')),
    result_sha256 text NOT NULL CHECK (result_sha256 ~ '^[a-f0-9]{64}$'),
    payload jsonb NOT NULL CHECK (payload->>'schema_version' = 'cctv-scene-v1' AND payload->>'status' = 'completed'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (payload->>'analysis_id' = analysis_id::text),
    CHECK (payload->>'provider_job_id' = provider_job_id AND payload->>'event_id' = event_id)
);
CREATE INDEX IF NOT EXISTS descriptions_event_idx ON cctv_scene.descriptions (provider_job_id, event_id, created_at DESC);
-- Additive migration: keep historical v1 collision-only results unchanged.
ALTER TABLE cctv_scene.descriptions ADD COLUMN IF NOT EXISTS accident_suspicion text;
ALTER TABLE cctv_scene.descriptions ADD COLUMN IF NOT EXISTS incident_types jsonb;
ALTER TABLE cctv_scene.descriptions ALTER COLUMN assessment DROP NOT NULL;
ALTER TABLE cctv_scene.descriptions DROP CONSTRAINT IF EXISTS descriptions_payload_check;
ALTER TABLE cctv_scene.descriptions DROP CONSTRAINT IF EXISTS descriptions_version_contract;
ALTER TABLE cctv_scene.descriptions ADD CONSTRAINT descriptions_version_contract CHECK ((
    payload->>'status' = 'completed' AND (
      (payload->>'schema_version' = 'cctv-scene-v1'
       AND assessment IS NOT NULL AND assessment = payload->'description'->>'assessment'
       AND accident_suspicion IS NULL AND incident_types IS NULL)
      OR
      (payload->>'schema_version' = 'cctv-scene-v2' AND assessment IS NULL
       AND accident_suspicion IN ('suspected', 'not_observed', 'uncertain')
       AND accident_suspicion = payload->'description'->>'accident_suspicion'
       AND jsonb_typeof(incident_types) = 'array'
       AND incident_types = payload->'description'->'incident_types')
    )
) IS TRUE);
GRANT USAGE ON SCHEMA cctv_scene TO cctv_app;
GRANT SELECT, INSERT ON cctv_scene.descriptions TO cctv_app;
COMMIT;
