-- Retellis Postgres init.
-- pgvector extension for event-chain embeddings (Phase 3 recall).
CREATE EXTENSION IF NOT EXISTS vector;

-- Separate database for Langfuse (self-hosted observability).
-- The POSTGRES_USER is a superuser, so this runs cleanly at first-boot init.
CREATE DATABASE langfuse;