CREATE TABLE IF NOT EXISTS youtube_transcript_cache (
    id BIGSERIAL PRIMARY KEY,
    video_id TEXT NOT NULL UNIQUE,
    video_url TEXT NOT NULL,
    transcript_json TEXT NOT NULL,
    full_text TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    source TEXT NOT NULL,
    is_generated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_youtube_transcript_cache_updated_at ON youtube_transcript_cache;
CREATE TRIGGER trg_youtube_transcript_cache_updated_at
BEFORE UPDATE ON youtube_transcript_cache
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
