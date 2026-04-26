CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE creator_voice_profiles RENAME TO creator_voice_profiles_legacy;

CREATE TABLE creator_voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    creator_id TEXT NOT NULL,
    voice_profile_json TEXT NOT NULL,
    style_summary TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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

CREATE TRIGGER IF NOT EXISTS trg_creator_voice_profiles_updated_at
AFTER UPDATE ON creator_voice_profiles
FOR EACH ROW
BEGIN
    UPDATE creator_voice_profiles
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.id;
END;
