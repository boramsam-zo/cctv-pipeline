-- Apply after schema.sql as the DB administrator. Only data insert/search privileges for this CLI.
BEGIN;
GRANT USAGE ON SCHEMA cctv_rag TO cctv_app;
GRANT SELECT, INSERT ON cctv_rag.chunks TO cctv_app;
COMMIT;
