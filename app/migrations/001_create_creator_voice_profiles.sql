-- v1 uses SQLite in WAL mode because it is the cheapest operational option.
-- The repository/service boundary keeps the storage swappable so v2 can move
-- voice_profile_json to a native Postgres JSONB column without touching agents.

CREATE TABLE IF NOT EXISTS creator_voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id TEXT NOT NULL UNIQUE,
    voice_profile_json TEXT NOT NULL,
    style_summary TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS trg_creator_voice_profiles_updated_at
AFTER UPDATE ON creator_voice_profiles
FOR EACH ROW
BEGIN
    UPDATE creator_voice_profiles
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.id;
END;
