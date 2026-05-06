CREATE TABLE IF NOT EXISTS devices (
    ip_address TEXT PRIMARY KEY,
    vendor TEXT NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_state (
    ip_address TEXT PRIMARY KEY,
    execution_state TEXT NOT NULL,
    connectivity_status TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    facts_collected JSONB,
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    last_updated TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_evidence (
    id SERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    execution_state TEXT NOT NULL,
    error JSONB,
    raw_payload JSONB NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_validation_evidence_run_id ON validation_evidence (run_id);
CREATE INDEX IF NOT EXISTS idx_validation_evidence_ip ON validation_evidence (ip_address);
