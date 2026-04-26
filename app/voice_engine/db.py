import sqlite3
from pathlib import Path
from threading import Lock


PROJECT_BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_BACKEND_DIR / "data"
DATABASE_PATH = DATA_DIR / "contentos.db"
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

_migration_lock = Lock()


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(DATABASE_PATH), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA foreign_keys=ON;")
    return connection


def run_migrations() -> None:
    with _migration_lock:
        connection = get_connection()

        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            applied_versions = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }

            for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if migration_path.name in applied_versions:
                    continue

                sql = migration_path.read_text(encoding="utf-8")
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)",
                    (migration_path.name,),
                )

            connection.commit()
        finally:
            connection.close()
