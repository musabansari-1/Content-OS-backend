from fastapi import HTTPException

from app.api.services import creator_voice_profile_service
from app.voice_engine.types import (
    CreatorVoiceProfileRecord,
    SaveMyVoiceProfileFromYoutubeRequest,
    SaveMyVoiceProfileRequest,
)
from app.youtube_transcripts import fetch_video_transcripts


def get_my_voice_profile(user_id: int) -> CreatorVoiceProfileRecord:
    profile = creator_voice_profile_service.getVoiceProfile(user_id)

    if not profile:
        raise HTTPException(status_code=404, detail="Creator voice profile not found.")

    return profile


def save_my_voice_profile(
    request: SaveMyVoiceProfileRequest,
    user_id: int,
) -> CreatorVoiceProfileRecord:
    return creator_voice_profile_service.createOrUpdateVoiceProfile(
        user_id,
        request.samples,
    )


def save_my_voice_profile_from_youtube(
    request: SaveMyVoiceProfileFromYoutubeRequest,
    user_id: int,
) -> CreatorVoiceProfileRecord:
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
        user_id,
        samples,
    )
