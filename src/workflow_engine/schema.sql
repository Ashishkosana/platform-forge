-- Durable step runner. Source of truth for V1.
-- All lease comparisons use PostgreSQL now(), never worker wall clocks.

CREATE TABLE IF NOT EXISTS jobs (
  id                uuid PRIMARY KEY,
  workflow_name     text NOT NULL,
  idempotency_key   text NOT NULL,
  status            text NOT NULL,
  input             jsonb NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT jobs_status_chk CHECK (
    status IN ('queued', 'running', 'succeeded', 'dead_lettered', 'cancelled')
  ),
  CONSTRAINT jobs_idempotency_key_len CHECK (
    char_length(idempotency_key) BETWEEN 1 AND 200
  ),
  UNIQUE (workflow_name, idempotency_key)
);

CREATE TABLE IF NOT EXISTS steps (
  id              uuid PRIMARY KEY,
  job_id          uuid NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
  seq             int  NOT NULL,
  name            text NOT NULL,
  status          text NOT NULL,
  run_after       timestamptz NOT NULL DEFAULT now(),
  leased_until    timestamptz,
  worker_id       text,
  fencing_token   bigint NOT NULL DEFAULT 0,
  attempt_count   int NOT NULL DEFAULT 0,
  max_attempts    int NOT NULL,
  timeout_seconds int NOT NULL,
  output          jsonb,
  last_error      text,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT steps_status_chk CHECK (
    status IN ('pending', 'blocked', 'running', 'succeeded', 'dead_lettered', 'cancelled')
  ),
  CONSTRAINT steps_seq_chk CHECK (seq >= 1),
  CONSTRAINT steps_attempts_chk CHECK (max_attempts >= 1 AND attempt_count >= 0),
  UNIQUE (job_id, seq),
  UNIQUE (job_id, name)
);

CREATE TABLE IF NOT EXISTS attempts (
  id             bigserial PRIMARY KEY,
  step_id        uuid NOT NULL REFERENCES steps (id) ON DELETE CASCADE,
  fencing_token  bigint NOT NULL,
  worker_id      text NOT NULL,
  started_at     timestamptz NOT NULL DEFAULT now(),
  finished_at    timestamptz,
  outcome        text NOT NULL,
  error          text,
  CONSTRAINT attempts_outcome_chk CHECK (
    outcome IN (
      'started',
      'succeeded',
      'failed',
      'rejected_fence',
      'cancelled',
      'timeout'
    )
  ),
  UNIQUE (step_id, fencing_token)
);

-- Laboratory side-effect ledger. Not a product store. Used to MEASURE
-- duplicate effects after crashes. write_count > 1 is a finding, not a bug
-- in the engine when the handler is naive.
CREATE TABLE IF NOT EXISTS lab_effects (
  effect_key     text PRIMARY KEY,
  write_count    int NOT NULL,
  first_token    bigint NOT NULL,
  last_token     bigint NOT NULL,
  last_worker_id text NOT NULL,
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS steps_claimable
  ON steps (run_after, id)
  WHERE status IN ('pending', 'running');

CREATE INDEX IF NOT EXISTS steps_job ON steps (job_id);

CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, created_at);

CREATE INDEX IF NOT EXISTS attempts_step ON attempts (step_id, started_at);
