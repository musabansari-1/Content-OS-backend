from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status

from app.billing.service import ensure_can_direct_publish, record_direct_publish
from app.integrations_repository import SocialIntegrationRepository
from app.services.ghost_service import publish_ghost_asset_for_user
from app.scheduling.domain import ScheduledPostRecord
from app.scheduling.repository import ScheduledPostRepository
from app.services.instagram_service import publish_instagram_asset_for_user
from app.services.integration_service import publish_linkedin_post_for_user
from app.services.tiktok_service import publish_tiktok_asset_for_user


VALID_PLATFORMS = {"linkedin", "instagram", "tiktok", "ghost"}
VALID_STATUSES = {"scheduled", "publishing", "published", "failed", "canceled"}
MINIMUM_SCHEDULE_DELAY_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 3
BASE_RETRY_DELAY_SECONDS = 120

scheduled_post_repository = ScheduledPostRepository()
social_integration_repository = SocialIntegrationRepository()


def create_scheduled_post(
    *,
    user_id: int,
    platform: str,
    payload: dict[str, Any],
    scheduled_for: datetime,
    timezone_name: str | None = None,
) -> ScheduledPostRecord:
    normalized_platform = _normalize_platform(platform)
    normalized_scheduled_for = _ensure_utc(scheduled_for)
    minimum_scheduled_for = datetime.now(timezone.utc) + timedelta(seconds=MINIMUM_SCHEDULE_DELAY_SECONDS)
    if normalized_scheduled_for < minimum_scheduled_for:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Schedule posts at least 30 seconds in the future.",
        )

    normalized_payload = _normalize_payload(normalized_platform, payload)
    _ensure_platform_connected(user_id=user_id, platform=normalized_platform)
    client_asset_id = _client_asset_id(normalized_payload)
    if client_asset_id:
        existing = scheduled_post_repository.find_active_for_asset(
            user_id=user_id,
            platform=normalized_platform,
            client_asset_id=client_asset_id,
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This asset is already scheduled for publishing.",
            )
    asset_type = _asset_type(normalized_payload)
    return scheduled_post_repository.create(
        user_id=user_id,
        platform=normalized_platform,
        payload=normalized_payload,
        scheduled_for=normalized_scheduled_for,
        timezone_name=(timezone_name or "UTC").strip() or "UTC",
        asset_type=asset_type,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
    )


def list_scheduled_posts(
    *,
    user_id: int,
    status_filter: str | None = None,
    limit: int = 100,
) -> list[ScheduledPostRecord]:
    if status_filter and status_filter not in VALID_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid scheduled post status.")
    return scheduled_post_repository.list_for_user(user_id=user_id, status=status_filter, limit=limit)


def cancel_scheduled_post(*, user_id: int, post_id: int) -> ScheduledPostRecord:
    existing = scheduled_post_repository.get_for_user(user_id=user_id, post_id=post_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found.")
    if existing.status != "scheduled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only scheduled posts can be canceled. Current status: {existing.status}.",
        )

    canceled = scheduled_post_repository.cancel_for_user(user_id=user_id, post_id=post_id)
    if canceled is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Scheduled post is already being processed.")
    return canceled


async def run_due_scheduled_posts(*, limit: int = 25) -> dict[str, Any]:
    lock_token = secrets.token_urlsafe(24)
    claimed_posts = scheduled_post_repository.claim_due_posts(
        now=datetime.now(timezone.utc),
        limit=limit,
        lock_token=lock_token,
    )

    results = []
    for post in claimed_posts:
        result = await _publish_claimed_post(post)
        if result.get("ok"):
            scheduled_post_repository.mark_published(
                post_id=post.id,
                lock_token=lock_token,
                external_post_id=_external_post_id(result),
                publish_result=result,
            )
        else:
            error_message = str(result.get("message") or result.get("error") or "Scheduled publish failed.")
            scheduled_post_repository.mark_failed(
                post_id=post.id,
                lock_token=lock_token,
                error_message=error_message[:1000],
                retry_at=_next_retry_at(post, result),
            )
        results.append(
            {
                "id": post.id,
                "platform": post.platform,
                "ok": bool(result.get("ok")),
                "message": result.get("message"),
                "error": result.get("error"),
            }
        )

    return {
        "claimed": len(claimed_posts),
        "results": results,
    }


async def _publish_claimed_post(post: ScheduledPostRecord) -> dict[str, Any]:
    try:
        ensure_can_direct_publish(post.user_id, 1)
    except HTTPException as error:
        return {
            "ok": False,
            "error": "billing_limit_reached",
            "message": error.detail if isinstance(error.detail, str) else "Direct publish limit reached.",
            "retryable": False,
        }

    if post.platform == "linkedin":
        result = await publish_linkedin_post_for_user(user_id=post.user_id, text=str(post.payload["text"]))
    elif post.platform == "instagram":
        result = await publish_instagram_asset_for_user(user_id=post.user_id, asset=post.payload["asset"])
    elif post.platform == "tiktok":
        result = await publish_tiktok_asset_for_user(
            user_id=post.user_id,
            asset=post.payload["asset"],
            privacy_level=post.payload.get("privacy_level"),
            disable_comment=post.payload.get("disable_comment"),
            disable_duet=post.payload.get("disable_duet"),
            disable_stitch=post.payload.get("disable_stitch"),
            video_cover_timestamp_ms=post.payload.get("video_cover_timestamp_ms"),
        )
    elif post.platform == "ghost":
        result = await publish_ghost_asset_for_user(
            user_id=post.user_id,
            asset=post.payload["asset"],
            newsletter_slug=post.payload.get("newsletter_slug"),
        )
    else:
        result = {
            "ok": False,
            "error": "unsupported_platform",
            "message": f"Unsupported scheduled platform: {post.platform}.",
            "retryable": False,
        }

    if result.get("ok"):
        record_direct_publish(post.user_id, 1)
    return result


def _ensure_platform_connected(*, user_id: int, platform: str) -> None:
    connection = social_integration_repository.get_by_user_and_platform(
        user_id=user_id,
        platform=platform,
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Connect {_platform_label(platform)} before scheduling this asset.",
        )


def _normalize_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized not in VALID_PLATFORMS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported scheduling platform.")
    return normalized


def _platform_label(platform: str) -> str:
    labels = {
        "linkedin": "LinkedIn",
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "ghost": "Ghost",
    }
    return labels.get(platform, platform.title())


def _normalize_payload(platform: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Scheduled post payload must be an object.")

    metadata = _normalize_metadata(payload.get("metadata"))

    if platform == "linkedin":
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="LinkedIn scheduled posts require text.")
        normalized_payload: dict[str, Any] = {"text": text}
        if metadata:
            normalized_payload["metadata"] = metadata
        return normalized_payload

    if platform in {"instagram", "tiktok", "ghost"}:
        asset = payload.get("asset")
        if not isinstance(asset, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{platform.title()} scheduled posts require an asset.")
        normalized_payload: dict[str, Any] = {"asset": asset}
        if metadata:
            normalized_payload["metadata"] = metadata
        if platform == "tiktok":
            for key in (
                "privacy_level",
                "disable_comment",
                "disable_duet",
                "disable_stitch",
                "video_cover_timestamp_ms",
            ):
                if key in payload:
                    normalized_payload[key] = payload[key]
        if platform == "ghost":
            newsletter_slug = str(payload.get("newsletter_slug") or "").strip()
            if newsletter_slug:
                normalized_payload["newsletter_slug"] = newsletter_slug
        return normalized_payload

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported scheduling platform.")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _asset_type(payload: dict[str, Any]) -> str | None:
    asset = payload.get("asset")
    if not isinstance(asset, dict):
        return None
    asset_type = str(asset.get("assetType") or asset.get("asset_type") or "").strip().lower()
    return asset_type or None


def _normalize_metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, Any] = {}
    client_asset_id = str(value.get("asset_id") or value.get("assetId") or "").strip()
    if client_asset_id:
        normalized["asset_id"] = client_asset_id

    title = str(value.get("title") or "").strip()
    if title:
        normalized["title"] = title[:200]

    return normalized


def _client_asset_id(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        asset_id = str(metadata.get("asset_id") or "").strip()
        if asset_id:
            return asset_id

    asset = payload.get("asset")
    if isinstance(asset, dict):
        return str(asset.get("id") or "").strip()

    return ""


def _external_post_id(result: dict[str, Any]) -> str | None:
    for key in ("linkedin_post_id", "instagram_post_id", "publish_id", "ghost_post_id"):
        value = result.get(key)
        if value:
            return str(value)
    return None


def _next_retry_at(post: ScheduledPostRecord, result: dict[str, Any]) -> datetime | None:
    if result.get("retryable") is False:
        return None
    if post.attempt_count >= post.max_attempts:
        return None
    delay_seconds = BASE_RETRY_DELAY_SECONDS * (2 ** max(post.attempt_count - 1, 0))
    return datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
