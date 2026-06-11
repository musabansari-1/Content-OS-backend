from fastapi import APIRouter, Depends

from app.auth.dependencies import require_current_user
from app.auth.domain import AuthUser
from app.services.integration_service import (
    handle_linkedin_callback,
    handle_x_callback,
    start_linkedin_auth,
    start_x_auth,
)


router = APIRouter()


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
