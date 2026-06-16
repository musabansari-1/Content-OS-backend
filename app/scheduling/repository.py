from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.core.db import get_connection
from app.scheduling.domain import ScheduledPostRecord


class ScheduledPostRepository:
    def find_active_for_asset(
        self,
        *,
        user_id: int,
        platform: str,
        client_asset_id: str,
    ) -> ScheduledPostRecord | None:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT *
                FROM scheduled_posts
                WHERE user_id = %s
                  AND platform = %s
                  AND status IN ('scheduled', 'publishing')
                  AND (
                    payload #>> '{metadata,asset_id}' = %s
                    OR payload #>> '{asset,id}' = %s
                  )
                ORDER BY scheduled_for ASC, id ASC
                LIMIT 1
                """,
                (user_id, platform, client_asset_id, client_asset_id),
            ).fetchone()
        finally:
            connection.close()

        return _record_from_row(row) if row else None

    def create(
        self,
        *,
        user_id: int,
        platform: str,
        payload: dict[str, Any],
        scheduled_for: datetime,
        timezone_name: str,
        asset_type: str | None,
        max_attempts: int = 3,
    ) -> ScheduledPostRecord:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                INSERT INTO scheduled_posts (
                    user_id,
                    platform,
                    asset_type,
                    payload,
                    scheduled_for,
                    timezone,
                    max_attempts,
                    next_attempt_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    user_id,
                    platform,
                    asset_type,
                    Jsonb(payload),
                    scheduled_for,
                    timezone_name,
                    max_attempts,
                    scheduled_for,
                ),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        return _record_from_row(row)

    def get_for_user(self, *, user_id: int, post_id: int) -> ScheduledPostRecord | None:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT *
                FROM scheduled_posts
                WHERE user_id = %s AND id = %s
                """,
                (user_id, post_id),
            ).fetchone()
        finally:
            connection.close()

        return _record_from_row(row) if row else None

    def list_for_user(
        self,
        *,
        user_id: int,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ScheduledPostRecord]:
        normalized_limit = min(max(limit, 1), 200)
        params: list[Any] = [user_id]
        status_filter = ""
        if status:
            status_filter = "AND status = %s"
            params.append(status)
        params.append(normalized_limit)

        connection = get_connection()
        try:
            rows = connection.execute(
                f"""
                SELECT *
                FROM scheduled_posts
                WHERE user_id = %s
                {status_filter}
                ORDER BY scheduled_for DESC, id DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
        finally:
            connection.close()

        return [_record_from_row(row) for row in rows]

    def cancel_for_user(self, *, user_id: int, post_id: int) -> ScheduledPostRecord | None:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                UPDATE scheduled_posts
                SET status = 'canceled',
                    canceled_at = CURRENT_TIMESTAMP,
                    lock_token = NULL,
                    locked_at = NULL
                WHERE user_id = %s
                  AND id = %s
                  AND status = 'scheduled'
                RETURNING *
                """,
                (user_id, post_id),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        return _record_from_row(row) if row else None

    def claim_due_posts(
        self,
        *,
        now: datetime,
        limit: int,
        lock_token: str,
    ) -> list[ScheduledPostRecord]:
        normalized_limit = min(max(limit, 1), 50)
        connection = get_connection()
        try:
            rows = connection.execute(
                """
                WITH due_posts AS (
                    SELECT id
                    FROM scheduled_posts
                    WHERE status = 'scheduled'
                      AND next_attempt_at <= %s
                    ORDER BY next_attempt_at ASC, id ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE scheduled_posts
                SET status = 'publishing',
                    attempt_count = attempt_count + 1,
                    locked_at = CURRENT_TIMESTAMP,
                    lock_token = %s,
                    last_error = NULL
                FROM due_posts
                WHERE scheduled_posts.id = due_posts.id
                RETURNING scheduled_posts.*
                """,
                (now, normalized_limit, lock_token),
            ).fetchall()
            connection.commit()
        finally:
            connection.close()

        return [_record_from_row(row) for row in rows]

    def mark_published(
        self,
        *,
        post_id: int,
        lock_token: str,
        external_post_id: str | None,
        publish_result: dict[str, Any],
    ) -> ScheduledPostRecord | None:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                UPDATE scheduled_posts
                SET status = 'published',
                    published_at = CURRENT_TIMESTAMP,
                    external_post_id = %s,
                    publish_result = %s,
                    lock_token = NULL,
                    locked_at = NULL,
                    last_error = NULL
                WHERE id = %s
                  AND status = 'publishing'
                  AND lock_token = %s
                RETURNING *
                """,
                (external_post_id, Jsonb(publish_result), post_id, lock_token),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        return _record_from_row(row) if row else None

    def mark_failed(
        self,
        *,
        post_id: int,
        lock_token: str,
        error_message: str,
        retry_at: datetime | None,
    ) -> ScheduledPostRecord | None:
        next_status = "scheduled" if retry_at else "failed"
        connection = get_connection()
        try:
            row = connection.execute(
                """
                UPDATE scheduled_posts
                SET status = %s,
                    next_attempt_at = COALESCE(%s, next_attempt_at),
                    last_error = %s,
                    lock_token = NULL,
                    locked_at = NULL
                WHERE id = %s
                  AND status = 'publishing'
                  AND lock_token = %s
                RETURNING *
                """,
                (next_status, retry_at, error_message, post_id, lock_token),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        return _record_from_row(row) if row else None


def _record_from_row(row: dict[str, Any]) -> ScheduledPostRecord:
    payload = row["payload"] if isinstance(row["payload"], dict) else {}
    publish_result = row["publish_result"] if isinstance(row["publish_result"], dict) else None
    return ScheduledPostRecord(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        platform=str(row["platform"]),
        asset_type=row["asset_type"],
        payload=payload,
        scheduled_for=row["scheduled_for"],
        timezone=str(row["timezone"]),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        next_attempt_at=row["next_attempt_at"],
        locked_at=row["locked_at"],
        lock_token=row["lock_token"],
        published_at=row["published_at"],
        canceled_at=row["canceled_at"],
        last_error=row["last_error"],
        external_post_id=row["external_post_id"],
        publish_result=publish_result,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
