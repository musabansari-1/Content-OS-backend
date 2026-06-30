from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


ScheduledPostStatus = Literal["scheduled", "publishing", "published", "failed", "canceled"]
ScheduledPostPlatform = Literal["linkedin", "instagram", "tiktok", "ghost", "x", "youtube"]


@dataclass(frozen=True)
class ScheduledPostRecord:
    id: int
    user_id: int
    platform: str
    asset_type: str | None
    payload: dict[str, Any]
    scheduled_for: datetime
    timezone: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    locked_at: datetime | None
    lock_token: str | None
    published_at: datetime | None
    canceled_at: datetime | None
    last_error: str | None
    external_post_id: str | None
    publish_result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
