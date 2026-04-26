ALTER TABLE creator_voice_profiles RENAME TO creator_voice_profiles_legacy_v2;

CREATE TABLE creator_voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    voice_profile_json TEXT NOT NULL,
    style_summary TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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

CREATE TRIGGER IF NOT EXISTS trg_creator_voice_profiles_updated_at
AFTER UPDATE ON creator_voice_profiles
FOR EACH ROW
BEGIN
    UPDATE creator_voice_profiles
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.id;
END;
