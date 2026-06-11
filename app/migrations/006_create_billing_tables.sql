CREATE TABLE IF NOT EXISTS billing_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_code TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'internal',
    provider_customer_id TEXT,
    provider_subscription_id TEXT,
    subscription_status TEXT NOT NULL DEFAULT 'active',
    current_period_start TIMESTAMPTZ NOT NULL,
    current_period_end TIMESTAMPTZ NOT NULL,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id)
);

CREATE TABLE IF NOT EXISTS billing_usage_counters (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    assets_generated INTEGER NOT NULL DEFAULT 0,
    direct_publishes INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, period_start)
);

CREATE TABLE IF NOT EXISTS billing_webhook_events (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'received',
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, event_id)
);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_billing_subscriptions_updated_at ON billing_subscriptions;
CREATE TRIGGER trg_billing_subscriptions_updated_at
BEFORE UPDATE ON billing_subscriptions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_billing_usage_counters_updated_at ON billing_usage_counters;
CREATE TRIGGER trg_billing_usage_counters_updated_at
BEFORE UPDATE ON billing_usage_counters
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_billing_webhook_events_updated_at ON billing_webhook_events;
CREATE TRIGGER trg_billing_webhook_events_updated_at
BEFORE UPDATE ON billing_webhook_events
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
