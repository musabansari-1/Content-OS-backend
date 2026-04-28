CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE creator_voice_profiles RENAME TO creator_voice_profiles_legacy;

CREATE TABLE creator_voice_profiles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    creator_id TEXT NOT NULL,
    voice_profile_json TEXT NOT NULL,
    style_summary TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(user_id, creator_id)
);

INSERT INTO creator_voice_profiles (
    id,
    user_id,
    creator_id,
    voice_profile_json,
    style_summary,
    version,
    created_at,
    updated_at
)
SELECT
    id,
    NULL,
    creator_id,
    voice_profile_json,
    style_summary,
    version,
    created_at,
    updated_at
FROM creator_voice_profiles_legacy;

DROP TABLE creator_voice_profiles_legacy;

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
