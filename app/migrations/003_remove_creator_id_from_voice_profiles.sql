ALTER TABLE creator_voice_profiles RENAME TO creator_voice_profiles_legacy_v2;

CREATE TABLE creator_voice_profiles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    voice_profile_json TEXT NOT NULL,
    style_summary TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

INSERT INTO creator_voice_profiles (
    user_id,
    voice_profile_json,
    style_summary,
    version,
    created_at,
    updated_at
)
SELECT
    user_id,
    voice_profile_json,
    style_summary,
    version,
    created_at,
    updated_at
FROM creator_voice_profiles_legacy_v2
WHERE user_id IS NOT NULL;

DROP TABLE creator_voice_profiles_legacy_v2;

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
