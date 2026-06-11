from fastapi import APIRouter

from app.services.integration_service import handle_linkedin_callback, start_linkedin_auth


router = APIRouter()


@router.get("/auth/linkedin")
def auth_linkedin():
    return start_linkedin_auth()


@router.get("/auth/linkedin/callback")
async def auth_linkedin_callback(code: str = None, state: str = None, error: str = None):
    return await handle_linkedin_callback(code=code, state=state, error=error)
