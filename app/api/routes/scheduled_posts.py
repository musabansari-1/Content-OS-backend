from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.dependencies import require_current_user
from app.auth.domain import AuthUser
from app.core.config import env
from app.scheduling.domain import ScheduledPostRecord
from app.scheduling.service import (
    cancel_scheduled_post,
    create_scheduled_post,
    list_scheduled_posts,
    run_due_scheduled_posts,
)


router = APIRouter(prefix="/scheduled-posts", tags=["scheduled-posts"])


class ScheduledPostCreateRequest(BaseModel):
    platform: str = Field(..., description="One of linkedin, instagram, tiktok, or ghost.")
    payload: dict[str, Any] = Field(..., description="Platform publish payload snapshot.")
    scheduled_for: datetime = Field(..., description="Scheduled publish time. Timezone-aware values are recommended.")
    timezone: str | None = Field("UTC", description="User-facing timezone label for display.")


class ScheduledPostResponse(BaseModel):
    id: int
    platform: str
    asset_type: str | None
    payload: dict[str, Any]
    scheduled_for: datetime
    timezone: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    published_at: datetime | None
    canceled_at: datetime | None
    last_error: str | None
    external_post_id: str | None
    created_at: datetime
    updated_at: datetime


@router.post("", response_model=ScheduledPostResponse)
def schedule_post(
    request: ScheduledPostCreateRequest,
    current_user: AuthUser = Depends(require_current_user),
):
    record = create_scheduled_post(
        user_id=current_user.id,
        platform=request.platform,
        payload=request.payload,
        scheduled_for=request.scheduled_for,
        timezone_name=request.timezone,
    )
    return _response_from_record(record)


@router.get("", response_model=list[ScheduledPostResponse])
def list_posts(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = 100,
    current_user: AuthUser = Depends(require_current_user),
):
    records = list_scheduled_posts(
        user_id=current_user.id,
        status_filter=status_filter,
        limit=limit,
    )
    return [_response_from_record(record) for record in records]


@router.post("/{post_id}/cancel", response_model=ScheduledPostResponse)
def cancel_post(
    post_id: int,
    current_user: AuthUser = Depends(require_current_user),
):
    return _response_from_record(cancel_scheduled_post(user_id=current_user.id, post_id=post_id))


@router.post("/run-due")
async def run_due_posts(
    limit: int = 25,
    x_scheduled_runner_token: str | None = Header(None),
):
    _require_runner_token(x_scheduled_runner_token)
    return await run_due_scheduled_posts(limit=limit)


def _require_runner_token(provided_token: str | None) -> None:
    expected_token = (env("SCHEDULED_POST_RUNNER_TOKEN", "") or "").strip()
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduled post runner token is not configured.",
        )
    if not provided_token or not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid scheduled post runner token.")


def _response_from_record(record: ScheduledPostRecord) -> ScheduledPostResponse:
    return ScheduledPostResponse(
        id=record.id,
        platform=record.platform,
        asset_type=record.asset_type,
        payload=record.payload,
        scheduled_for=record.scheduled_for,
        timezone=record.timezone,
        status=record.status,
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        next_attempt_at=record.next_attempt_at,
        published_at=record.published_at,
        canceled_at=record.canceled_at,
        last_error=record.last_error,
        external_post_id=record.external_post_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
