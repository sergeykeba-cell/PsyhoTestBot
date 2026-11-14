-- PsyhoTestBot — PostgreSQL schema
-- Idempotent: safe to run on an empty database and on an existing one (it adds the columns
-- that older versions of this file were missing).
--
-- Run as the database owner:
--   psql -U <db_user> -d <db_name> -f schema.sql
--
-- Create the role and the database first, as the postgres superuser:
--   CREATE USER <db_user> WITH PASSWORD '<set a strong password of your own>';
--   CREATE DATABASE <db_name> OWNER <db_user>;
--   REVOKE CONNECT ON DATABASE <db_name> FROM PUBLIC;

CREATE TABLE IF NOT EXISTS public.doctors (
    id          BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    email       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- tokens.status: 'pending' (created) -> 'processing' (claimed by the workflow) -> 'completed'
CREATE TABLE IF NOT EXISTS public.tokens (
    token        TEXT NOT NULL PRIMARY KEY,
    full_name    TEXT NOT NULL,
    test_type    TEXT NOT NULL,
    used         BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    doctor_id    BIGINT,
    test_version VARCHAR NOT NULL DEFAULT '1.0',
    status       VARCHAR NOT NULL DEFAULT 'pending',
    opened_at    TIMESTAMPTZ,
    used_at      TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '90 days'),
    sex          CHAR(1),
    CONSTRAINT fk_tokens_doctor FOREIGN KEY (doctor_id) REFERENCES public.doctors(id)
);

CREATE TABLE IF NOT EXISTS public.patients (
    id          BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT,
    full_name   TEXT NOT NULL,
    doctor_id   BIGINT REFERENCES public.doctors(id),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.results (
    id                BIGSERIAL PRIMARY KEY,
    submission_id     UUID UNIQUE,
    token             TEXT REFERENCES public.tokens(token),
    full_name         TEXT NOT NULL,
    test_type         TEXT NOT NULL,
    score             INTEGER,
    severity          TEXT,
    answers           JSONB,
    status            VARCHAR NOT NULL DEFAULT 'notified',
    ai_interpretation TEXT,
    scoring_time_ms   INTEGER,
    ai_time_ms        INTEGER,
    n8n_execution_id  TEXT,
    completed_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW(),
    details           JSONB
);

CREATE TABLE IF NOT EXISTS public.error_logs (
    id               BIGSERIAL PRIMARY KEY,
    n8n_execution_id TEXT,
    workflow_name    TEXT,
    node_name        TEXT,
    error_message    TEXT NOT NULL,
    error_stack      TEXT,
    input_data       JSONB,
    session_token    TEXT,
    submission_id    UUID,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Upgrade path for databases created from older versions of this file
ALTER TABLE public.tokens  ADD COLUMN IF NOT EXISTS sex CHAR(1);
ALTER TABLE public.results ADD COLUMN IF NOT EXISTS details JSONB;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tokens_sex_check') THEN
        ALTER TABLE public.tokens ADD CONSTRAINT tokens_sex_check CHECK (sex IN ('M', 'F'));
    END IF;
END
$$;

-- Indexes (results.submission_id is already indexed by its UNIQUE constraint)
CREATE INDEX IF NOT EXISTS idx_tokens_doctor_id ON public.tokens(doctor_id);
CREATE INDEX IF NOT EXISTS idx_tokens_status    ON public.tokens(status);
CREATE INDEX IF NOT EXISTS idx_results_token    ON public.results(token);
