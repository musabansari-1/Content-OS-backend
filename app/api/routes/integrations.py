from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.dependencies import require_current_user
from app.auth.domain import AuthUser
from app.billing.service import ensure_can_direct_publish, record_direct_publish
from app.services.instagram_service import (
    handle_instagram_callback,
    publish_instagram_asset_for_user,
    start_instagram_auth,
)
from app.services.integration_service import (
    get_connected_platforms_for_user,
    handle_linkedin_callback,
    handle_x_callback,
    publish_linkedin_post_for_user,
    start_linkedin_auth,
    start_x_auth,
)
from app.services.tiktok_service import (
    get_tiktok_creator_info_for_user,
    get_tiktok_publish_status_for_user,
    handle_tiktok_callback,
    publish_tiktok_asset_for_user,
    start_tiktok_auth,
)


router = APIRouter()


class LinkedInPublishRequest(BaseModel):
    text: str = Field(..., description="The LinkedIn post text to publish.")


class InstagramPublishRequest(BaseModel):
    asset: dict = Field(..., description="The Instagram asset payload to publish.")


class TikTokPublishRequest(BaseModel):
    asset: dict = Field(..., description="The TikTok clip asset payload to publish.")
    privacy_level: str | None = Field(None, description="One of the creator's current TikTok privacy options.")
    disable_comment: bool | None = None
    disable_duet: bool | None = None
    disable_stitch: bool | None = None
    video_cover_timestamp_ms: int | None = None


class TikTokStatusRequest(BaseModel):
    publish_id: str = Field(..., description="The TikTok publish id returned by /tiktok/publish.")


@router.get("/status")
def get_status(current_user: AuthUser = Depends(require_current_user)):
    return get_connected_platforms_for_user(user_id=current_user.id)


@router.get("/auth/linkedin")
def auth_linkedin(current_user: AuthUser = Depends(require_current_user)):
    return {"auth_url": start_linkedin_auth(user_id=current_user.id)}


@router.get("/auth/linkedin/callback")
async def auth_linkedin_callback(code: str = None, state: str = None, error: str = None):
    return await handle_linkedin_callback(code=code, state=state, error=error)


@router.get("/auth/x")
def auth_x(current_user: AuthUser = Depends(require_current_user)):
    return {"auth_url": start_x_auth(user_id=current_user.id)}


@router.get("/auth/x/callback")
async def auth_x_callback(code: str = None, state: str = None, error: str = None):
    return await handle_x_callback(code=code, state=state, error=error)


@router.get("/auth/instagram")
def auth_instagram(current_user: AuthUser = Depends(require_current_user)):
    return {"auth_url": start_instagram_auth(user_id=current_user.id)}


@router.get("/auth/instagram/callback")
async def auth_instagram_callback(code: str = None, state: str = None, error: str = None):
    return await handle_instagram_callback(code=code, state=state, error=error)


@router.get("/auth/tiktok")
def auth_tiktok(current_user: AuthUser = Depends(require_current_user)):
    return {"auth_url": start_tiktok_auth(user_id=current_user.id)}


@router.get("/auth/tiktok/callback")
async def auth_tiktok_callback(code: str = None, state: str = None, error: str = None):
    return await handle_tiktok_callback(code=code, state=state, error=error)


@router.post("/linkedin/publish")
async def publish_linkedin(
    request: LinkedInPublishRequest,
    current_user: AuthUser = Depends(require_current_user),
):
    text = request.text.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LinkedIn post text is required.",
        )

    ensure_can_direct_publish(current_user.id, 1)
    result = await publish_linkedin_post_for_user(user_id=current_user.id, text=text)
    if not result.get("ok"):
        error = result.get("error")
        if error == "linkedin_not_connected":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["message"])
        if error == "linkedin_connection_incomplete":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["message"])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("message", "LinkedIn publish failed."),
        )

    record_direct_publish(current_user.id, 1)
    return {
        "message": "LinkedIn post published.",
        "platform": result["platform"],
        "linkedin_post_id": result.get("linkedin_post_id"),
        "status_code": result.get("status_code"),
    }


@router.post("/instagram/publish")
async def publish_instagram(
    request: InstagramPublishRequest,
    current_user: AuthUser = Depends(require_current_user),
):
    ensure_can_direct_publish(current_user.id, 1)

    asset = request.asset if isinstance(request.asset, dict) else {}
    result = await publish_instagram_asset_for_user(user_id=current_user.id, asset=asset)
    if not result.get("ok"):
        error = result.get("error")
        if error == "instagram_not_connected":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["message"])
        if error == "instagram_connection_incomplete":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["message"])
        if error in {"instagram_unsupported_asset", "instagram_reel_missing_video", "instagram_carousel_missing_slides"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("message", "Instagram publish failed."),
        )

    record_direct_publish(current_user.id, 1)
    return {
        "message": "Instagram post published.",
        "platform": result["platform"],
        "asset_type": result.get("asset_type"),
        "instagram_post_id": result.get("instagram_post_id"),
        "creation_id": result.get("creation_id"),
    }


@router.get("/tiktok/creator-info")
async def get_tiktok_creator_info(current_user: AuthUser = Depends(require_current_user)):
    result = await get_tiktok_creator_info_for_user(user_id=current_user.id)
    if not result.get("ok"):
        error = result.get("error")
        if error == "tiktok_not_connected":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["message"])
        raise HTTPException(
            status_code=result.get("status_code") or status.HTTP_502_BAD_GATEWAY,
            detail=result.get("message", "TikTok creator info request failed."),
        )
    return result


@router.post("/tiktok/publish")
async def publish_tiktok(
    request: TikTokPublishRequest,
    current_user: AuthUser = Depends(require_current_user),
):
    ensure_can_direct_publish(current_user.id, 1)

    asset = request.asset if isinstance(request.asset, dict) else {}
    result = await publish_tiktok_asset_for_user(
        user_id=current_user.id,
        asset=asset,
        privacy_level=request.privacy_level,
        disable_comment=request.disable_comment,
        disable_duet=request.disable_duet,
        disable_stitch=request.disable_stitch,
        video_cover_timestamp_ms=request.video_cover_timestamp_ms,
    )
    if not result.get("ok"):
        error = result.get("error")
        if error == "tiktok_not_connected":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["message"])
        if error == "tiktok_connection_incomplete":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["message"])
        if error in {"tiktok_not_configured", "tiktok_unsupported_asset"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
        raise HTTPException(
            status_code=result.get("status_code") or status.HTTP_502_BAD_GATEWAY,
            detail=result.get("message", "TikTok publish failed."),
        )

    record_direct_publish(current_user.id, 1)
    return {
        "message": "TikTok post initialized.",
        "platform": result["platform"],
        "asset_type": result.get("asset_type"),
        "publish_id": result.get("publish_id"),
        "privacy_level": result.get("privacy_level"),
        "source": result.get("source"),
        "tiktok_username": result.get("tiktok_username"),
    }


@router.post("/tiktok/status")
async def get_tiktok_publish_status(
    request: TikTokStatusRequest,
    current_user: AuthUser = Depends(require_current_user),
):
    publish_id = request.publish_id.strip()
    if not publish_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TikTok publish_id is required.")

    result = await get_tiktok_publish_status_for_user(user_id=current_user.id, publish_id=publish_id)
    if not result.get("ok"):
        error = result.get("error")
        if error == "tiktok_not_connected":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["message"])
        raise HTTPException(
            status_code=result.get("status_code") or status.HTTP_502_BAD_GATEWAY,
            detail=result.get("message", "TikTok status request failed."),
        )
    return result
