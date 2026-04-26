# main.py

import json
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from youtube_transcript_api import YouTubeTranscriptApi

from app.agents.execution_agent import run_execution_pipeline
from app.agents.moment_agent import extract_moments
from app.agents.strategy_agent import generate_strategy
from app.voice_engine.db import run_migrations
from app.voice_engine.service import CreatorVoiceProfileService
from app.voice_engine.types import (
    CreateVoiceProfileRequest,
    CreateVoiceProfileFromYoutubeRequest,
    CreatorVoiceProfileRecord,
    GenerateContentRequest,
)

app = FastAPI()
creator_voice_profile_service = CreatorVoiceProfileService()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    run_migrations()
    creator_voice_profile_service.repairStoredPreferredDevices()


def transcript_to_text(transcript):
    return " ".join(snippet.text for snippet in transcript.snippets)


def resolve_youtube_video_id(video_input: str) -> str:
    value = (video_input or "").strip()

    if not value:
        raise HTTPException(status_code=400, detail="A YouTube video ID or URL is required.")

    if "youtube.com" not in value and "youtu.be" not in value:
        return value

    parsed_url = urlparse(value)
    hostname = (parsed_url.netloc or "").lower()

    if "youtu.be" in hostname:
        video_id = parsed_url.path.strip("/").split("/")[0]
        if video_id:
            return video_id

    if "youtube.com" in hostname:
        query_video_id = parse_qs(parsed_url.query).get("v", [])
        if query_video_id and query_video_id[0]:
            return query_video_id[0]

        path_parts = [part for part in parsed_url.path.split("/") if part]
        if len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
            return path_parts[1]

    raise HTTPException(status_code=400, detail="Invalid YouTube URL or unsupported YouTube format.")


def fetch_video_transcript(video_input: str) -> str:
    video_id = resolve_youtube_video_id(video_input)
    ytt_api = YouTubeTranscriptApi()
    transcript = ytt_api.fetch(video_id)
    return transcript_to_text(transcript)


def fetch_video_transcripts(video_inputs):
    return [fetch_video_transcript(video_input) for video_input in video_inputs]


def _generate_from_video(video_input: str, creator_id: Optional[str] = None):
    transcript = fetch_video_transcript(video_input)

    moments = extract_moments(transcript)

    strategy_output = generate_strategy(
        {
            "transcript": transcript,
            "moments": moments,
        }
    )

    print(transcript)
    print(strategy_output)

    strategy_output = json.loads(strategy_output)
    execution_plan = strategy_output["execution_plan"]

    print(execution_plan)

    results = run_execution_pipeline(
        execution_plan,
        transcript,
        creator_id=creator_id,
        creator_voice_profile_service=creator_voice_profile_service,
    )

    return {
        "strategy": strategy_output,
        "results": results,
    }


@app.get("/generate")
def generate(
    video_id: Optional[str] = None,
    video_url: Optional[str] = None,
    creator_id: Optional[str] = None,
):
    video_input = video_url or video_id

    if not video_input:
        raise HTTPException(status_code=400, detail="Provide either video_id or video_url.")

    return _generate_from_video(video_input, creator_id)


@app.post("/generate-from-video")
def generate_from_video(request: GenerateContentRequest):
    video_input = request.video_url or request.video_id

    if not video_input:
        raise HTTPException(status_code=400, detail="Provide either video_id or video_url.")

    return _generate_from_video(video_input, request.creator_id)


@app.post("/creator-voice-profiles", response_model=CreatorVoiceProfileRecord)
def create_or_update_creator_voice_profile(
    request: CreateVoiceProfileRequest,
):
    return creator_voice_profile_service.createOrUpdateVoiceProfile(
        request.creator_id,
        request.samples,
    )


@app.post(
    "/creator-voice-profiles/from-youtube",
    response_model=CreatorVoiceProfileRecord,
)
def create_or_update_creator_voice_profile_from_youtube(
    request: CreateVoiceProfileFromYoutubeRequest,
):
    video_inputs = [
        video_id.strip() for video_id in request.youtube_video_ids if video_id.strip()
    ]
    video_inputs.extend(
        [video_url.strip() for video_url in request.youtube_urls if video_url.strip()]
    )

    if not video_inputs:
        raise HTTPException(
            status_code=400,
            detail="At least one YouTube video ID or URL is required.",
        )

    samples = fetch_video_transcripts(video_inputs)

    return creator_voice_profile_service.createOrUpdateVoiceProfile(
        request.creator_id,
        samples,
    )


@app.get(
    "/creator-voice-profiles/{creator_id}",
    response_model=CreatorVoiceProfileRecord,
)
def get_creator_voice_profile(creator_id: str):
    profile = creator_voice_profile_service.getVoiceProfile(creator_id)

    if not profile:
        raise HTTPException(status_code=404, detail="Creator voice profile not found.")

    return profile
