from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.db import get_connection


@dataclass(frozen=True)
class SocialIntegrationRecord:
    id: int
    user_id: int
    platform: str
    platform_user_id: str
    platform_username: Optional[str]
    access_token: str
    refresh_token: Optional[str]
    scope: Optional[str]
    token_type: Optional[str]
    token_expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


def _expires_at_from_seconds(expires_in: Optional[int]) -> Optional[datetime]:
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


class SocialIntegrationRepository:
    def upsert_connection(
        self,
        *,
        user_id: int,
        platform: str,
        platform_user_id: str,
        platform_username: Optional[str],
        access_token: str,
        refresh_token: Optional[str],
        scope: Optional[str],
        token_type: Optional[str],
        expires_in: Optional[int],
    ) -> SocialIntegrationRecord:
        token_expires_at = _expires_at_from_seconds(expires_in)
        connection = get_connection()
        try:
            connection.execute(
                """
                INSERT INTO social_integrations (
                    user_id,
                    platform,
                    platform_user_id,
                    platform_username,
                    access_token,
                    refresh_token,
                    scope,
                    token_type,
                    token_expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, platform) DO UPDATE SET
                    platform_user_id = EXCLUDED.platform_user_id,
                    platform_username = EXCLUDED.platform_username,
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    scope = EXCLUDED.scope,
                    token_type = EXCLUDED.token_type,
                    token_expires_at = EXCLUDED.token_expires_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    platform,
                    platform_user_id,
                    platform_username,
                    access_token,
                    refresh_token,
                    scope,
                    token_type,
                    token_expires_at,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        record = self.get_by_user_and_platform(user_id=user_id, platform=platform)
        if record is None:
            raise RuntimeError("Failed to load saved social integration.")
        return record

    def get_by_user_and_platform(
        self,
        *,
        user_id: int,
        platform: str,
    ) -> Optional[SocialIntegrationRecord]:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    platform,
                    platform_user_id,
                    platform_username,
                    access_token,
                    refresh_token,
                    scope,
                    token_type,
                    token_expires_at,
                    created_at,
                    updated_at
                FROM social_integrations
                WHERE user_id = %s AND platform = %s
                """,
                (user_id, platform),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return SocialIntegrationRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            platform=row["platform"],
            platform_user_id=row["platform_user_id"],
            platform_username=row["platform_username"],
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            scope=row["scope"],
            token_type=row["token_type"],
            token_expires_at=row["token_expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
