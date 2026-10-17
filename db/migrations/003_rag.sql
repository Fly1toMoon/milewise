-- Rules documents for retrieval (design.md §2.2). Raw pages live in S3 / raw/, never in git.
CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE rag.documents (
  doc_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id      text NOT NULL,                    -- id in data/sources.yaml
  airline        char(2) NOT NULL CHECK (airline IN ('NH', 'JL')),
  source_url     text NOT NULL,
  lang           text NOT NULL,
  title          text NOT NULL,
  doc_type       text NOT NULL,                    -- rules | fees | partner | faq | chart_notes
  content_sha256 char(64) NOT NULL,                -- of the extracted text, not the raw HTML
  raw_key        text NOT NULL,                    -- S3 key or local path of the raw page
  fetched_at     timestamptz NOT NULL,
  is_current     boolean NOT NULL DEFAULT true,
  superseded_at  timestamptz,
  UNIQUE (source_url, content_sha256)
);
CREATE UNIQUE INDEX documents_one_current ON rag.documents (source_url) WHERE is_current;

CREATE TABLE rag.chunks (
  chunk_id        char(64) PRIMARY KEY,            -- sha256(doc_id|ord|text): re-ingest is a no-op
  doc_id          uuid NOT NULL REFERENCES rag.documents ON DELETE CASCADE,
  ord             int NOT NULL,
  section_path    text[] NOT NULL,
  text            text NOT NULL,
  body_sha256     char(64) NOT NULL,               -- of the body without the heading path
  char_count      int NOT NULL,
  embedding       vector(1024) NOT NULL,
  embedding_model text NOT NULL,
  lang            text NOT NULL
);
CREATE INDEX chunks_embedding_hnsw ON rag.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_doc ON rag.chunks (doc_id);
CREATE INDEX chunks_body ON rag.chunks (body_sha256);  -- skip boilerplate repeated across pages

CREATE TABLE rag.ingestion_runs (
  raw_key        text NOT NULL,
  raw_etag       text NOT NULL,                    -- S3 ETag, or sha256 of the local file
  status         text NOT NULL CHECK (status IN ('started', 'succeeded', 'skipped_same_content', 'failed')),
  chunks_written int,
  error          text,
  started_at     timestamptz NOT NULL DEFAULT now(),
  finished_at    timestamptz,
  PRIMARY KEY (raw_key, raw_etag)
);
