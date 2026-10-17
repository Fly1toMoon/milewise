-- Evaluation runs and per-case results (design.md §2.3). Each run is also exported as
-- JSONL (S3 artifacts bucket in the cloud) so history survives `make down`.
CREATE SCHEMA IF NOT EXISTS eval;

CREATE TABLE eval.cases (
  case_id      text NOT NULL,
  case_sha256  char(64) NOT NULL,                  -- of the case definition as run
  category     text NOT NULL CHECK (category IN ('miles', 'rules', 'both', 'abstain')),
  lang         text NOT NULL,
  question     text NOT NULL,
  expected     jsonb NOT NULL,
  gold_urls    text[] NOT NULL DEFAULT '{}',
  verified_at  date,                               -- NULL = not yet checked by a person
  PRIMARY KEY (case_id, case_sha256)
);

CREATE TABLE eval.runs (
  run_id              uuid PRIMARY KEY,
  started_at          timestamptz NOT NULL,
  finished_at         timestamptz,
  git_sha             text NOT NULL,
  trigger             text NOT NULL,               -- local | ci-deploy | manual
  target              text NOT NULL,               -- local | lambda:candidate | ...
  config              jsonb NOT NULL,              -- models, effort, embedding, prompt hash
  n_cases             int,
  n_unverified        int,
  miles_accuracy      numeric,
  rules_accuracy      numeric,
  abstain_accuracy    numeric,
  citation_accuracy   numeric,
  postcheck_fail_rate numeric,
  overall_accuracy    numeric,
  latency_ms_avg      int,
  latency_ms_p95      int,
  cost_usd_avg        numeric(10, 6),
  cost_usd_total      numeric(10, 4),
  thresholds          jsonb NOT NULL,
  passed              boolean
);

CREATE TABLE eval.results (
  run_id          uuid NOT NULL REFERENCES eval.runs ON DELETE CASCADE,
  case_id         text NOT NULL,
  case_sha256     char(64) NOT NULL,
  answer_text     text,
  tool_calls      jsonb,
  cited_urls      text[],
  miles_ok        boolean,                         -- NULL when not applicable
  judge_ok        boolean,
  judge_rationale text,
  citation_ok     boolean,
  postcheck_ok    boolean,
  passed          boolean NOT NULL,
  unstable        boolean NOT NULL DEFAULT false,  -- failed once, passed on retry
  latency_ms      int,
  input_tokens    int,
  output_tokens   int,
  cost_usd        numeric(10, 6),
  error           text,
  PRIMARY KEY (run_id, case_id)
);
