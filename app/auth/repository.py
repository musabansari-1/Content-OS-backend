from typing import Optional

from app.auth.types import UserResponse
from app.voice_engine.db import get_connection, run_migrations


class UserRepository:
    def __init__(self) -> None:
        run_migrations()

    def create_user(self, email: str, password_hash: str, display_name: str) -> UserResponse:
        connection = get_connection()

        try:
            cursor = connection.execute(
                """
                INSERT INTO users (email, password_hash, display_name)
                VALUES (?, ?, ?)
                """,
                (email, password_hash, display_name),
            )
            connection.commit()
            return self.get_by_id(int(cursor.lastrowid))
        finally:
            connection.close()

    def get_by_email(self, email: str) -> Optional[dict]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT id, email, password_hash, display_name, created_at
                FROM users
                WHERE email = ?
                """,
                (email,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return {
            "id": int(row["id"]),
            "email": row["email"],
            "password_hash": row["password_hash"],
            "display_name": row["display_name"],
            "created_at": row["created_at"],
        }

    def get_by_id(self, user_id: int) -> Optional[UserResponse]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT id, email, display_name, created_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return UserResponse(
            id=int(row["id"]),
            email=row["email"],
            display_name=row["display_name"],
            created_at=row["created_at"],
        )
