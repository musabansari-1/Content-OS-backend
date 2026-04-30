# main.py

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.agents.execution_agent import run_execution_pipeline
from app.agents.moment_agent import extract_moments
from app.agents.strategy_agent import generate_strategy
from app.assets import build_asset_brief, get_asset_catalog, normalize_target_assets
from app.auth.dependencies import auth_service, require_current_user
from app.auth.types import AuthResponse, LoginRequest, RegisterRequest, UserResponse
from app.generation_jobs import generation_job_store
from app.youtube_transcripts import (
    fetch_video_transcript,
    fetch_video_transcripts,
)
from app.voice_engine.db import run_migrations
from app.voice_engine.service import CreatorVoiceProfileService
from app.voice_engine.types import (
    CreatorVoiceProfileRecord,
    GenerateContentRequest,
    SaveMyVoiceProfileFromYoutubeRequest,
    SaveMyVoiceProfileRequest,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _load_env_file() -> None:
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _get_allowed_origins() -> list[str]:
    _load_env_file()
    allowed_origins = {
        "http://localhost:5173",
        "http://localhost:3000",
        "https://content-os-frontend.vercel.app",
    }

    configured_origins = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if configured_origins:
        allowed_origins.update(
            origin.strip() for origin in configured_origins.split(",") if origin.strip()
        )

    frontend_url = os.getenv("FRONTEND_URL", "").strip()
    if frontend_url:
        allowed_origins.add(frontend_url)

    uptime_robot_url = os.getenv("UPTIMEROBOT_URL", "").strip()
    if uptime_robot_url:
        allowed_origins.add(uptime_robot_url)

    return sorted(allowed_origins)


app = FastAPI()
creator_voice_profile_service = CreatorVoiceProfileService()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    run_migrations()
    creator_voice_profile_service.repairStoredPreferredDevices()


def _merge_steps(current_steps, updates):
    step_map = {step["key"]: dict(step) for step in current_steps}
    for step_key, status in (updates or {}).items():
        if step_key in step_map:
            step_map[step_key]["status"] = status
    return [step_map[step["key"]] for step in current_steps]


def _build_progress_callback(job_id: str):
    def emit(event):
        current_job = generation_job_store.get_job_snapshot(job_id)
        current_steps = current_job["steps"] if current_job else []
        generation_job_store.update_job(
            job_id,
            status="running",
            stage=event.get("stage", "running"),
            message=event.get("message", "Generation in progress."),
            detail=event.get("detail", ""),
            progress_percent=event.get("progress_percent", 5),
            steps=_merge_steps(current_steps, event.get("steps")),
            asset_progress=event.get("asset_progress", current_job.get("asset_progress", []) if current_job else []),
        )

    return emit


def _run_generation_pipeline(
    source_text: str,
    target_assets: list[str],
    user_id: int,
    progress_callback=None,
):
    if progress_callback:
        progress_callback(
            {
                "stage": "moments",
                "message": "Understanding your input.",
                "detail": "Reviewing the source to prepare the content pack.",
                "progress_percent": 12,
                "steps": {"source": "completed", "moments": "active"},
            }
        )

    moments = extract_moments(source_text)

    if progress_callback:
        progress_callback(
            {
                "stage": "strategy",
                "message": "Preparing your content pack.",
                "detail": "Structuring the requested outputs before creation begins.",
                "progress_percent": 22,
                "steps": {"moments": "completed", "strategy": "active"},
            }
        )

    strategy_output = generate_strategy(
        {
            "transcript": source_text,
            "moments": moments,
            "target_assets": target_assets,
            "asset_catalog": build_asset_brief(target_assets),
        }
    )

    strategy_output = json.loads(strategy_output)
    execution_plan = strategy_output["execution_plan"]

    if progress_callback:
        progress_callback(
            {
                "stage": "execution",
                "message": "Starting creation.",
                "detail": "Moving from setup into the main generation phase.",
                "progress_percent": 30,
                "steps": {"strategy": "completed", "execution": "active"},
            }
        )

    results = run_execution_pipeline(
        execution_plan,
        source_text,
        user_id=user_id,
        creator_voice_profile_service=creator_voice_profile_service,
        progress_callback=progress_callback,
    )

    if progress_callback:
        progress_callback(
            {
                "stage": "finalize",
                "message": "Wrapping things up.",
                "detail": "Final touches are being applied before display.",
                "progress_percent": 96,
                "steps": {"execution": "completed", "finalize": "active"},
            }
        )

    return {
        "strategy": strategy_output,
        "results": results,
    }


def _generate_from_video(
    video_input: str,
    user_id: int,
    target_assets: list[str],
    progress_callback=None,
):
    if progress_callback:
        progress_callback(
            {
                "stage": "source",
                "message": "Getting your source ready.",
                "detail": "Bringing in the source material for generation.",
                "progress_percent": 6,
                "steps": {"source": "active"},
            }
        )

    transcript = fetch_video_transcript(video_input)

    return _run_generation_pipeline(
        transcript,
        target_assets,
        user_id,
        progress_callback=progress_callback,
    )


def _generate_from_transcript(
    transcript: str,
    user_id: int,
    target_assets: list[str],
    progress_callback=None,
):
    normalized_transcript = transcript.strip()
    if not normalized_transcript:
        raise HTTPException(status_code=400, detail="A transcript is required.")

    if progress_callback:
        progress_callback(
            {
                "stage": "source",
                "message": "Getting your source ready.",
                "detail": "Bringing in the source material for generation.",
                "progress_percent": 8,
                "steps": {"source": "active"},
            }
        )

    return _run_generation_pipeline(
        normalized_transcript,
        target_assets,
        user_id,
        progress_callback=progress_callback,
    )


@app.get("/generate")
def generate(
    video_id: Optional[str] = None,
    video_url: Optional[str] = None,
    target_assets: Optional[str] = None,
    current_user: UserResponse = Depends(require_current_user),
):
    video_input = video_url or video_id

    if not video_input:
        raise HTTPException(status_code=400, detail="Provide either video_id or video_url.")

    try:
        selected_target_assets = normalize_target_assets(
            target_assets.split(",") if target_assets else None
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return _generate_from_video(
        video_input,
        current_user.id,
        selected_target_assets,
    )


@app.post("/generate-from-video")
def generate_from_video(
    request: GenerateContentRequest,
    current_user: UserResponse = Depends(require_current_user),
):
    video_input = request.video_url or request.video_id
    transcript = request.transcript.strip()

    if not video_input and not transcript:
        raise HTTPException(
            status_code=400,
            detail="Provide a YouTube video URL/ID or paste a transcript.",
        )

    try:
        selected_target_assets = normalize_target_assets(request.target_assets)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    if transcript:
        return _generate_from_transcript(
            transcript,
            current_user.id,
            selected_target_assets,
        )

    return _generate_from_video(video_input, current_user.id, selected_target_assets)


@app.post("/generation-jobs")
def create_generation_job(
    request: GenerateContentRequest,
    current_user: UserResponse = Depends(require_current_user),
):
    video_input = request.video_url or request.video_id
    transcript = request.transcript.strip()

    if not video_input and not transcript:
        raise HTTPException(
            status_code=400,
            detail="Provide a YouTube video URL/ID or paste a transcript.",
        )

    try:
        selected_target_assets = normalize_target_assets(request.target_assets)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    payload = {
        "video_id": request.video_id,
        "video_url": request.video_url,
        "transcript": transcript,
        "target_assets": selected_target_assets,
    }
    job = generation_job_store.create_job(current_user.id, payload)
    progress_callback = _build_progress_callback(job["id"])

    def runner():
        if transcript:
            return _generate_from_transcript(
                transcript,
                current_user.id,
                selected_target_assets,
                progress_callback=progress_callback,
            )

        return _generate_from_video(
            video_input,
            current_user.id,
            selected_target_assets,
            progress_callback=progress_callback,
        )

    generation_job_store.start_job(job["id"], runner)
    return job


@app.get("/generation-jobs/{job_id}")
def get_generation_job(
    job_id: str,
    current_user: UserResponse = Depends(require_current_user),
):
    job = generation_job_store.get_job(job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found.")
    return job


@app.get("/target-assets")
def get_target_assets():
    return {"target_assets": get_asset_catalog()}


@app.get("/me", response_model=UserResponse)
def get_me(current_user: UserResponse = Depends(require_current_user)):
    return current_user


@app.post("/auth/register", response_model=AuthResponse)
def register(request: RegisterRequest):
    return auth_service.register(request)


@app.post("/auth/login", response_model=AuthResponse)
def login(request: LoginRequest):
    return auth_service.login(request)


@app.get("/me/voice-profile", response_model=CreatorVoiceProfileRecord)
def get_my_default_voice_profile(
    current_user: UserResponse = Depends(require_current_user),
):
    profile = creator_voice_profile_service.getVoiceProfile(current_user.id)

    if not profile:
        raise HTTPException(status_code=404, detail="Creator voice profile not found.")

    return profile


@app.post("/me/voice-profile", response_model=CreatorVoiceProfileRecord)
def create_or_update_my_default_voice_profile(
    request: SaveMyVoiceProfileRequest,
    current_user: UserResponse = Depends(require_current_user),
):
    return creator_voice_profile_service.createOrUpdateVoiceProfile(
        current_user.id,
        request.samples,
    )


@app.post("/me/voice-profile/from-youtube", response_model=CreatorVoiceProfileRecord)
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
