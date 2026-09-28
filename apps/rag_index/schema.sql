-- Apply only to the agreed PostgreSQL database. pgvector must be installed on the server.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS cctv_rag;
CREATE TABLE IF NOT EXISTS cctv_rag.chunks (
    chunk_id text NOT NULL,
    embedding_profile text NOT NULL CHECK (embedding_profile = 'gemini-001-768-retrieval-v1'),
    input_sha256 text NOT NULL,
    document_id text NOT NULL,
    content text NOT NULL,
    metadata jsonb NOT NULL,
    embedding vector(768) NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, embedding_profile, input_sha256)
);
COMMIT;
-- Two chunks: exact cosine search is sufficient; no approximate index is needed yet.
