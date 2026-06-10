from fastapi import APIRouter, Depends, HTTPException

from app.api.services import creator_voice_profile_service
from app.auth.dependencies import require_current_user
from app.auth.types import UserResponse
from app.voice_engine.types import (
    CreatorVoiceProfileRecord,
    SaveMyVoiceProfileFromYoutubeRequest,
    SaveMyVoiceProfileRequest,
)
from app.youtube_transcripts import fetch_video_transcripts


router = APIRouter()


@router.get("/me/voice-profile", response_model=CreatorVoiceProfileRecord)
def get_my_default_voice_profile(
    current_user: UserResponse = Depends(require_current_user),
):
    profile = creator_voice_profile_service.getVoiceProfile(current_user.id)

    if not profile:
        raise HTTPException(status_code=404, detail="Creator voice profile not found.")

    return profile


@router.post("/me/voice-profile", response_model=CreatorVoiceProfileRecord)
def create_or_update_my_default_voice_profile(
    request: SaveMyVoiceProfileRequest,
    current_user: UserResponse = Depends(require_current_user),
):
    return creator_voice_profile_service.createOrUpdateVoiceProfile(
        current_user.id,
        request.samples,
    )


@router.post("/me/voice-profile/from-youtube", response_model=CreatorVoiceProfileRecord)
def create_or_update_my_default_voice_profile_from_youtube(
    request: SaveMyVoiceProfileFromYoutubeRequest,
    current_user: UserResponse = Depends(require_current_user),
):
    video_inputs = [
        video_id.strip() for video_id in request.youtube_video_ids if video_id.strip()
    ]
    video_inputs.extend(
        [video_url.strip() for video_url in request.youtube_urls if video_url.strip()]
    )

    transcripts = [transcript.strip() for transcript in request.transcripts if transcript.strip()]

    if not video_inputs and not transcripts:
        raise HTTPException(
            status_code=400,
            detail="At least one YouTube video ID, URL, or transcript is required.",
        )

    samples = list(transcripts)
    if video_inputs:
        samples.extend(fetch_video_transcripts(video_inputs))

    return creator_voice_profile_service.createOrUpdateVoiceProfile(
        current_user.id,
        samples,
    )
