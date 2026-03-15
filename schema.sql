-- PsyhoTestBot — PostgreSQL Schema
-- Run as: psql -U psycho_user -d psycho_db < database/schema.sql

-- Create user and database (run as postgres superuser):
-- CREATE USER psycho_user WITH PASSWORD 'your_password';
-- CREATE DATABASE psycho_db OWNER psycho_user;
-- GRANT ALL PRIVILEGES ON DATABASE psycho_db TO psycho_user;

CREATE TABLE IF NOT EXISTS public.doctors (
    id          BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    email       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.tokens (
    token        TEXT NOT NULL PRIMARY KEY,
    full_name    TEXT NOT NULL,
    test_type    TEXT NOT NULL,
    used         BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    doctor_id    INTEGER REFERENCES public.doctors(id),
    test_version VARCHAR NOT NULL DEFAULT '1.0',
    status       VARCHAR NOT NULL DEFAULT 'pending',
    opened_at    TIMESTAMPTZ,
    used_at      TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '90 days'),
    CONSTRAINT fk_tokens_doctor FOREIGN KEY (doctor_id) REFERENCES public.doctors(id)
);

CREATE TABLE IF NOT EXISTS public.patients (
    id          BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT,
    full_name   TEXT NOT NULL,
    doctor_id   INTEGER REFERENCES public.doctors(id),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.results (
    id                 BIGSERIAL PRIMARY KEY,
    submission_id      UUID UNIQUE,
    token              TEXT REFERENCES public.tokens(token),
    full_name          TEXT NOT NULL,
    test_type          TEXT NOT NULL,
    score              INTEGER,
    severity           TEXT,
    answers            JSONB,
    status             VARCHAR NOT NULL DEFAULT 'notified',
    ai_interpretation  TEXT,
    scoring_time_ms    INTEGER,
    ai_time_ms         INTEGER,
    n8n_execution_id   TEXT,
    completed_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.error_logs (
    id                BIGSERIAL PRIMARY KEY,
    n8n_execution_id  TEXT,
    workflow_name     TEXT,
    node_name         TEXT,
    error_message     TEXT NOT NULL,
    error_stack       TEXT,
    input_data        JSONB,
    session_token     TEXT,
    submission_id     UUID,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tokens_doctor_id       ON public.tokens(doctor_id);
CREATE INDEX IF NOT EXISTS idx_tokens_status           ON public.tokens(status);
CREATE INDEX IF NOT EXISTS idx_results_token           ON public.results(token);
CREATE INDEX IF NOT EXISTS idx_results_submission_id   ON public.results(submission_id);
