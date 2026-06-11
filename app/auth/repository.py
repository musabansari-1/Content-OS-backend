from typing import Optional

from app.auth.domain import AuthUser, AuthUserCredentials
from app.core.db import get_connection


class UserRepository:
    def create_user(self, email: str, password_hash: str, display_name: str) -> AuthUser:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                INSERT INTO users (email, password_hash, display_name)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (email, password_hash, display_name),
            ).fetchone()
            connection.commit()
            return self.get_by_id(int(row["id"]))
        finally:
            connection.close()

    def get_by_email(self, email: str) -> Optional[AuthUserCredentials]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT id, email, password_hash, display_name, created_at
                FROM users
                WHERE email = %s
                """,
                (email,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return AuthUserCredentials(
            id=int(row["id"]),
            email=row["email"],
            password_hash=row["password_hash"],
            display_name=row["display_name"],
            created_at=row["created_at"],
        )

    def get_by_id(self, user_id: int) -> Optional[AuthUser]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT id, email, display_name, created_at
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return AuthUser(
            id=int(row["id"]),
            email=row["email"],
            display_name=row["display_name"],
            created_at=row["created_at"],
        )
