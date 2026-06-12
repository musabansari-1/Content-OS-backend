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


router = APIRouter()


class LinkedInPublishRequest(BaseModel):
    text: str = Field(..., description="The LinkedIn post text to publish.")


class InstagramPublishRequest(BaseModel):
    asset: dict = Field(..., description="The Instagram asset payload to publish.")


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
