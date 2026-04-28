-- Initial Postgres schema for creator voice profiles.

CREATE TABLE IF NOT EXISTS creator_voice_profiles (
    id BIGSERIAL PRIMARY KEY,
    creator_id TEXT NOT NULL UNIQUE,
    voice_profile_json TEXT NOT NULL,
    style_summary TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
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

DROP TRIGGER IF EXISTS trg_creator_voice_profiles_updated_at ON creator_voice_profiles;
CREATE TRIGGER trg_creator_voice_profiles_updated_at
BEFORE UPDATE ON creator_voice_profiles
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
