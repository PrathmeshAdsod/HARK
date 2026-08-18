SET CLUSTER SETTING feature.vector_index.enabled = true;

CREATE SCHEMA IF NOT EXISTS hark;
CREATE SCHEMA IF NOT EXISTS hark_demo;

CREATE TABLE IF NOT EXISTS hark.demos (
    id STRING PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS hark.runs (
    id UUID PRIMARY KEY,
    demo_id STRING NOT NULL REFERENCES hark.demos(id),
    skill_id STRING NOT NULL,
    skill_source STRING NOT NULL,
    environment_id STRING NOT NULL,
    workflow STRING NOT NULL,
    task STRING NOT NULL,
    status STRING NOT NULL CHECK (status IN ('running','succeeded','failed')),
    diagnosis STRING NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::JSONB,
    error_code STRING NULL,
    error_message STRING NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ NULL,
    INDEX runs_demo_created_idx (demo_id, created_at),
    INDEX runs_created_idx (created_at)
);

CREATE TABLE IF NOT EXISTS hark.run_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES hark.runs(id),
    sequence INT NOT NULL,
    event_type STRING NOT NULL,
    title STRING NOT NULL,
    detail STRING NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence)
);

CREATE TABLE IF NOT EXISTS hark.experiences (
    id UUID PRIMARY KEY,
    demo_id STRING NOT NULL REFERENCES hark.demos(id),
    source_run_id UUID NOT NULL REFERENCES hark.runs(id),
    skill_id STRING NOT NULL,
    environment_id STRING NOT NULL,
    workflow STRING NOT NULL,
    original_task STRING NOT NULL,
    experience_brief STRING NOT NULL,
    what_happened STRING NOT NULL,
    failure_fingerprint STRING NULL,
    failure_detail JSONB NOT NULL DEFAULT '{}'::JSONB,
    recovery STRING NOT NULL,
    paths_to_avoid STRING NOT NULL,
    outcome STRING NOT NULL,
    confidence DECIMAL(4,3) NOT NULL,
    status STRING NOT NULL CHECK (status IN ('succeeded','failed')),
    embedding VECTOR(256) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    invalidated_at TIMESTAMPTZ NULL,
    invalidation_reason STRING NULL,
    INDEX experiences_scope_idx (demo_id, skill_id, environment_id, workflow, status, created_at)
);

CREATE VECTOR INDEX IF NOT EXISTS experiences_memory_vector_idx
ON hark.experiences (demo_id, skill_id, environment_id, workflow, embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS hark.failure_recoveries (
    demo_id STRING NOT NULL REFERENCES hark.demos(id),
    failure_fingerprint STRING NOT NULL,
    skill_id STRING NOT NULL,
    environment_id STRING NOT NULL,
    tool_name STRING NOT NULL,
    sqlstate STRING NULL,
    failure_category STRING NOT NULL,
    recovery STRING NOT NULL,
    experience_id UUID NOT NULL REFERENCES hark.experiences(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (demo_id, failure_fingerprint)
);

CREATE TABLE IF NOT EXISTS hark.run_experience_links (
    run_id UUID NOT NULL REFERENCES hark.runs(id),
    experience_id UUID NOT NULL REFERENCES hark.experiences(id),
    use_type STRING NOT NULL CHECK (use_type IN ('proactive','reactive')),
    similarity DECIMAL(9,8) NULL,
    brief_snapshot STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, experience_id, use_type)
);

CREATE TABLE IF NOT EXISTS hark.usage_guard (
    id INT PRIMARY KEY CHECK (id = 1)
);
UPSERT INTO hark.usage_guard (id) VALUES (1);

CREATE TABLE IF NOT EXISTS hark.execution_leases (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL UNIQUE REFERENCES hark.runs(id),
    expires_at TIMESTAMPTZ NOT NULL,
    INDEX execution_leases_expiry_idx (expires_at)
);

CREATE TABLE IF NOT EXISTS hark_demo.customers (
    id UUID PRIMARY KEY,
    email STRING NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hark_demo.orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES hark_demo.customers(id),
    status STRING NOT NULL,
    total_cents INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    INDEX orders_created_idx (created_at DESC)
);

UPSERT INTO hark_demo.customers (id, email) VALUES
  ('11111111-1111-4111-8111-111111111111', 'alex@example.test'),
  ('22222222-2222-4222-8222-222222222222', 'bea@example.test'),
  ('33333333-3333-4333-8333-333333333333', 'chen@example.test'),
  ('44444444-4444-4444-8444-444444444444', 'dara@example.test');

INSERT INTO hark_demo.orders (customer_id, status, total_cents, created_at)
SELECT
  CASE i % 4
    WHEN 0 THEN '11111111-1111-4111-8111-111111111111'::UUID
    WHEN 1 THEN '22222222-2222-4222-8222-222222222222'::UUID
    WHEN 2 THEN '33333333-3333-4333-8333-333333333333'::UUID
    ELSE '44444444-4444-4444-8444-444444444444'::UUID
  END,
  CASE i % 5 WHEN 0 THEN 'pending' WHEN 1 THEN 'refunded' ELSE 'paid' END,
  500 + (i * 37) % 25000,
  now() - (i || ' minutes')::INTERVAL
FROM generate_series(1, 5000) AS g(i)
WHERE NOT EXISTS (SELECT 1 FROM hark_demo.orders LIMIT 1);
