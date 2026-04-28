import os
from pathlib import Path
from threading import Lock

from psycopg import Connection, connect
from psycopg.rows import dict_row


PROJECT_BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

_migration_lock = Lock()


def _load_env_file() -> None:
    env_path = PROJECT_BACKEND_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _get_database_url() -> str:
    _load_env_file()
    database_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add your Neon Postgres connection string to backend/.env."
        )

    return database_url


def get_connection() -> Connection:
    return connect(
        _get_database_url(),
        row_factory=dict_row,
    )


def run_migrations() -> None:
    with _migration_lock:
        with get_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES (%s)",
                    (migration_path.name,),
                )
