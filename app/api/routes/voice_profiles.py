from fastapi import APIRouter, Depends

from app.auth.domain import AuthUser
from app.auth.dependencies import require_current_user
from app.services.voice_profile_workflows import (
    get_my_voice_profile,
    save_my_voice_profile,
    save_my_voice_profile_from_youtube,
)
from app.voice_engine.types import (
    CreatorVoiceProfileRecord,
    SaveMyVoiceProfileFromYoutubeRequest,
    SaveMyVoiceProfileRequest,
)


router = APIRouter()


@router.get("/me/voice-profile", response_model=CreatorVoiceProfileRecord)
def get_my_default_voice_profile(
    current_user: AuthUser = Depends(require_current_user),
):
    return get_my_voice_profile(current_user.id)


@router.post("/me/voice-profile", response_model=CreatorVoiceProfileRecord)
def create_or_update_my_default_voice_profile(
    request: SaveMyVoiceProfileRequest,
    current_user: AuthUser = Depends(require_current_user),
):
    return save_my_voice_profile(request, current_user.id)


@router.post("/me/voice-profile/from-youtube", response_model=CreatorVoiceProfileRecord)
def create_or_update_my_default_voice_profile_from_youtube(
    request: SaveMyVoiceProfileFromYoutubeRequest,
    current_user: AuthUser = Depends(require_current_user),
):
    return save_my_voice_profile_from_youtube(request, current_user.id)
