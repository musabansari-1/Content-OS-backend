from __future__ import annotations

from pathlib import Path
from threading import Lock

from psycopg import Connection, connect
from psycopg.rows import dict_row

from app.core.config import get_database_url


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
_migration_lock = Lock()


def get_connection() -> Connection:
    return connect(
        get_database_url(),
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
