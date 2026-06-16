CREATE TABLE IF NOT EXISTS scheduled_posts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    asset_type TEXT,
    payload JSONB NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    status TEXT NOT NULL DEFAULT 'scheduled',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ NOT NULL,
    locked_at TIMESTAMPTZ,
    lock_token TEXT,
    published_at TIMESTAMPTZ,
    canceled_at TIMESTAMPTZ,
    last_error TEXT,
    external_post_id TEXT,
    publish_result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_scheduled_posts_platform CHECK (platform IN ('linkedin', 'instagram', 'tiktok')),
    CONSTRAINT chk_scheduled_posts_status CHECK (status IN ('scheduled', 'publishing', 'published', 'failed', 'canceled')),
    CONSTRAINT chk_scheduled_posts_attempts CHECK (attempt_count >= 0 AND max_attempts > 0)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_posts_due
ON scheduled_posts (status, next_attempt_at, id)
WHERE status = 'scheduled';

CREATE INDEX IF NOT EXISTS idx_scheduled_posts_user_scheduled_for
ON scheduled_posts (user_id, scheduled_for DESC);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_scheduled_posts_updated_at ON scheduled_posts;
CREATE TRIGGER trg_scheduled_posts_updated_at
BEFORE UPDATE ON scheduled_posts
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
