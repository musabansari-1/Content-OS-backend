from typing import Optional

from app.auth.domain import (
    AuthSessionRecord,
    AuthUser,
    AuthUserCredentials,
    EmailVerificationTokenRecord,
    PasswordResetTokenRecord,
)
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
                SELECT id, email, password_hash, display_name, created_at, email_verified_at, is_active
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
            email_verified_at=row["email_verified_at"],
            is_active=bool(row["is_active"]),
        )

    def get_by_id(self, user_id: int) -> Optional[AuthUser]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT id, email, display_name, created_at, email_verified_at, is_active
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
            email_verified_at=row["email_verified_at"],
            is_active=bool(row["is_active"]),
        )

    def create_auth_session(
        self,
        *,
        user_id: int,
        refresh_token_hash: str,
        expires_at,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> AuthSessionRecord:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                INSERT INTO auth_sessions (
                    user_id,
                    refresh_token_hash,
                    expires_at,
                    user_agent,
                    ip_address
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING
                    id,
                    user_id,
                    refresh_token_hash,
                    expires_at,
                    revoked_at,
                    last_used_at,
                    user_agent,
                    ip_address,
                    created_at
                """,
                (user_id, refresh_token_hash, expires_at, user_agent, ip_address),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        return AuthSessionRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            refresh_token_hash=row["refresh_token_hash"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            last_used_at=row["last_used_at"],
            user_agent=row["user_agent"],
            ip_address=row["ip_address"],
            created_at=row["created_at"],
        )

    def get_auth_session_by_id(self, session_id: int) -> Optional[AuthSessionRecord]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    refresh_token_hash,
                    expires_at,
                    revoked_at,
                    last_used_at,
                    user_agent,
                    ip_address,
                    created_at
                FROM auth_sessions
                WHERE id = %s
                """,
                (session_id,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return AuthSessionRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            refresh_token_hash=row["refresh_token_hash"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            last_used_at=row["last_used_at"],
            user_agent=row["user_agent"],
            ip_address=row["ip_address"],
            created_at=row["created_at"],
        )

    def rotate_auth_session(
        self,
        *,
        session_id: int,
        refresh_token_hash: str,
        expires_at,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> None:
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE auth_sessions
                SET refresh_token_hash = %s,
                    expires_at = %s,
                    last_used_at = CURRENT_TIMESTAMP,
                    user_agent = COALESCE(%s, user_agent),
                    ip_address = COALESCE(%s, ip_address)
                WHERE id = %s
                """,
                (refresh_token_hash, expires_at, user_agent, ip_address, session_id),
            )
            connection.commit()
        finally:
            connection.close()

    def revoke_auth_session(self, session_id: int) -> None:
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                WHERE id = %s
                """,
                (session_id,),
            )
            connection.commit()
        finally:
            connection.close()

    def revoke_all_auth_sessions_for_user(self, user_id: int) -> None:
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                WHERE user_id = %s AND revoked_at IS NULL
                """,
                (user_id,),
            )
            connection.commit()
        finally:
            connection.close()

    def create_email_verification_token(self, *, user_id: int, token_hash: str, expires_at) -> EmailVerificationTokenRecord:
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE email_verification_tokens
                SET used_at = CURRENT_TIMESTAMP
                WHERE user_id = %s AND used_at IS NULL
                """,
                (user_id,),
            )
            row = connection.execute(
                """
                INSERT INTO email_verification_tokens (
                    user_id,
                    token_hash,
                    expires_at
                )
                VALUES (%s, %s, %s)
                RETURNING
                    id,
                    user_id,
                    token_hash,
                    expires_at,
                    used_at,
                    created_at
                """,
                (user_id, token_hash, expires_at),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        return EmailVerificationTokenRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            token_hash=row["token_hash"],
            expires_at=row["expires_at"],
            used_at=row["used_at"],
            created_at=row["created_at"],
        )

    def get_email_verification_token_by_id(self, token_id: int) -> Optional[EmailVerificationTokenRecord]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    token_hash,
                    expires_at,
                    used_at,
                    created_at
                FROM email_verification_tokens
                WHERE id = %s
                """,
                (token_id,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return EmailVerificationTokenRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            token_hash=row["token_hash"],
            expires_at=row["expires_at"],
            used_at=row["used_at"],
            created_at=row["created_at"],
        )

    def mark_email_verification_token_used(self, token_id: int) -> None:
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE email_verification_tokens
                SET used_at = COALESCE(used_at, CURRENT_TIMESTAMP)
                WHERE id = %s
                """,
                (token_id,),
            )
            connection.commit()
        finally:
            connection.close()

    def mark_user_email_verified(self, user_id: int):
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE users
                SET email_verified_at = COALESCE(email_verified_at, CURRENT_TIMESTAMP)
                WHERE id = %s
                """,
                (user_id,),
            )
            connection.commit()
        finally:
            connection.close()

        return self.get_by_id(user_id)

    def create_password_reset_token(self, *, user_id: int, token_hash: str, expires_at) -> PasswordResetTokenRecord:
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = CURRENT_TIMESTAMP
                WHERE user_id = %s AND used_at IS NULL
                """,
                (user_id,),
            )
            row = connection.execute(
                """
                INSERT INTO password_reset_tokens (
                    user_id,
                    token_hash,
                    expires_at
                )
                VALUES (%s, %s, %s)
                RETURNING
                    id,
                    user_id,
                    token_hash,
                    expires_at,
                    used_at,
                    created_at
                """,
                (user_id, token_hash, expires_at),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        return PasswordResetTokenRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            token_hash=row["token_hash"],
            expires_at=row["expires_at"],
            used_at=row["used_at"],
            created_at=row["created_at"],
        )

    def get_password_reset_token_by_id(self, token_id: int) -> Optional[PasswordResetTokenRecord]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    token_hash,
                    expires_at,
                    used_at,
                    created_at
                FROM password_reset_tokens
                WHERE id = %s
                """,
                (token_id,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return PasswordResetTokenRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            token_hash=row["token_hash"],
            expires_at=row["expires_at"],
            used_at=row["used_at"],
            created_at=row["created_at"],
        )

    def mark_password_reset_token_used(self, token_id: int) -> None:
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = COALESCE(used_at, CURRENT_TIMESTAMP)
                WHERE id = %s
                """,
                (token_id,),
            )
            connection.commit()
        finally:
            connection.close()

    def update_user_password(self, user_id: int, password_hash: str) -> AuthUser | None:
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE users
                SET password_hash = %s
                WHERE id = %s
                """,
                (password_hash, user_id),
            )
            connection.commit()
        finally:
            connection.close()

        return self.get_by_id(user_id)
