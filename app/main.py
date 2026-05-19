import json
import inspect
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional
import subprocess


from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agents.execution_agent import run_execution_pipeline
from app.agents.moment_agent import extract_moments
from app.agents.strategy_agent import generate_strategy
from app.assets import AVAILABLE_TARGET_ASSETS, build_asset_brief, get_asset_catalog, normalize_target_assets
from app.auth.dependencies import auth_service, require_current_user
from app.auth.types import AuthResponse, LoginRequest, RegisterRequest, UserResponse
from app.generation_jobs import generation_job_store
from app.utils.generate_video_clips import generate_short_clips_from_groq
from app.youtube_transcripts import (
    fetch_video_transcript,
    fetch_video_transcripts,
    resolve_uploaded_video_path,
    transcribe_uploaded_video,
    transcribe_uploaded_video_with_artifacts,
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
GENERATED_CLIPS_DIR = BACKEND_DIR / "generated_clips"
GENERATED_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
SHORT_VIDEO_ASSET_TYPES = {
    asset_type
    for asset_type, asset in AVAILABLE_TARGET_ASSETS.items()
    if asset.get("output_type") == "short_video"
}


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


def _get_allowed_origin_regex() -> str | None:
    return r"^https://.*\.vercel\.app$|^https://.*\.onrender\.com$|^http://localhost:\d+$"


app = FastAPI()
creator_voice_profile_service = CreatorVoiceProfileService()
app.mount("/generated-clips", StaticFiles(directory=str(GENERATED_CLIPS_DIR)), name="generated-clips")

logging.getLogger(__name__).warning("CORS middleware is configured to allow all origins.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=None,
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
    skip_text_asset_types: set[str] | None = None,
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

    execution_kwargs = {
        "user_id": user_id,
        "creator_voice_profile_service": creator_voice_profile_service,
        "progress_callback": progress_callback,
    }
    if "skip_text_asset_types" in inspect.signature(run_execution_pipeline).parameters:
        execution_kwargs["skip_text_asset_types"] = skip_text_asset_types

    results = run_execution_pipeline(
        execution_plan,
        source_text,
        **execution_kwargs,
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


def _parse_result_output(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return deepcopy(output)

    if isinstance(output, str):
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"raw": output}

    return {"raw": output}


def _build_generated_clip_media(run_id: str, clip_payload: dict[str, Any]) -> dict[str, Any]:
    clip_details = clip_payload.get("clip", {})
    video_path = Path(clip_payload["video_path"])
    media = {
        "kind": "video",
        "label": clip_details.get("title") or "Generated clip",
        "video_path": str(video_path),
        "video_url": f"/generated-clips/{run_id}/clips/{video_path.name}",
        "clip_id": clip_details.get("clip_id"),
        "start": clip_details.get("start"),
        "end": clip_details.get("end"),
        "duration": clip_details.get("duration"),
        "score": clip_details.get("score"),
        "rationale": clip_details.get("rationale"),
        "transcript_text": clip_details.get("transcript_text"),
    }

    subtitle_path = clip_payload.get("subtitle_path")
    if subtitle_path:
        subtitle_file = Path(subtitle_path)
        media["subtitle_path"] = str(subtitle_file)
        media["subtitle_url"] = f"/generated-clips/{run_id}/subtitles/{subtitle_file.name}"

    return media


def _attach_generated_clips_to_results(
    pipeline_result: dict[str, Any],
    uploaded_video_path: str | None,
    transcription_bundle: dict[str, Any] | None,
    target_assets: list[str],
    progress_callback=None,
) -> dict[str, Any]:
    if not uploaded_video_path:
        return pipeline_result

    requested_short_assets = [
        asset_type for asset_type in target_assets if asset_type in SHORT_VIDEO_ASSET_TYPES
    ]
    if not requested_short_assets:
        return pipeline_result

    if not transcription_bundle:
        return pipeline_result

    logger = logging.getLogger(__name__)

    try:
        source_video_path = str(resolve_uploaded_video_path(uploaded_video_path))
    except Exception:
        logger.exception(
            "Failed to resolve uploaded video path for clip rendering: uploaded_video_path=%s",
            uploaded_video_path,
        )
        raise

    if progress_callback:
        progress_callback(
            {
                "stage": "execution_video",
                "message": "Rendering short-form clips.",
                "detail": "Turning the uploaded source video into playable short clips.",
                "progress_percent": 88,
                "steps": {"execution": "completed", "finalize": "active"},
            }
        )

    try:
        clip_result = generate_short_clips_from_groq(
            source_video_path=source_video_path,
            transcription=transcription_bundle,
            clip_count=len(requested_short_assets),
            output_dir=str(GENERATED_CLIPS_DIR),
            create_blur_background=True,
            debug=True,
        )
    except Exception:
        logger.exception(
            "Clip rendering failed: source_video_path=%s target_assets=%s",
            source_video_path,
            requested_short_assets,
        )
        raise

    selected_clips = clip_result.get("selected_clips", [])
    if not selected_clips:
        return pipeline_result

    results = []
    short_asset_index = 0
    for result in pipeline_result["results"]:
        next_result = dict(result)
        if result.get("asset_type") in requested_short_assets and short_asset_index < len(selected_clips):
            clip_payload = selected_clips[short_asset_index]
            output_payload = {
                "generated_clip": {
                    "title": clip_payload.get("clip", {}).get("title"),
                    "start": clip_payload.get("clip", {}).get("start"),
                    "end": clip_payload.get("clip", {}).get("end"),
                    "duration": clip_payload.get("clip", {}).get("duration"),
                    "score": clip_payload.get("clip", {}).get("score"),
                    "rationale": clip_payload.get("clip", {}).get("rationale"),
                }
            }
            next_result["output"] = json.dumps(output_payload)
            next_result["media"] = _build_generated_clip_media(clip_result["run_id"], clip_payload)
            short_asset_index += 1

        results.append(next_result)

    return {
        **pipeline_result,
        "results": results,
        "generated_clips": clip_result,
    }


def _generate_from_video(
    video_input: Optional[str],
    user_id: int,
    target_assets: list[str],
    progress_callback=None,
    uploaded_video: Optional[UploadFile] = None,
    uploaded_video_path: Optional[str] = None,
    transcription_bundle: Optional[dict[str, Any]] = None,
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

    if uploaded_video:
        # Transcribe uploaded video
        transcript = transcribe_uploaded_video(uploaded_video)
    else:
        # Fetch from YouTube URL
        transcript = fetch_video_transcript(video_input)  # type: ignore[arg-type]

    pipeline_result = _run_generation_pipeline(
        transcript,
        target_assets,
        user_id,
        progress_callback=progress_callback,
        skip_text_asset_types=set(SHORT_VIDEO_ASSET_TYPES) if uploaded_video_path else None,
    )

    return _attach_generated_clips_to_results(
        pipeline_result,
        uploaded_video_path=uploaded_video_path,
        transcription_bundle=transcription_bundle,
        target_assets=target_assets,
        progress_callback=progress_callback,
    )


def _generate_from_transcript(
    transcript: str,
    user_id: int,
    target_assets: list[str],
    progress_callback=None,
    uploaded_video_path: Optional[str] = None,
    transcription_bundle: Optional[dict[str, Any]] = None,
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

    pipeline_result = _run_generation_pipeline(
        normalized_transcript,
        target_assets,
        user_id,
        progress_callback=progress_callback,
        skip_text_asset_types=set(SHORT_VIDEO_ASSET_TYPES) if uploaded_video_path else None,
    )

    return _attach_generated_clips_to_results(
        pipeline_result,
        uploaded_video_path=uploaded_video_path,
        transcription_bundle=transcription_bundle,
        target_assets=target_assets,
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
    uploaded_video: Optional[UploadFile] = None,
    current_user: UserResponse = Depends(require_current_user),
):
    video_input = request.video_url or request.video_id
    transcript = request.transcript.strip()

    # Check for valid input: YouTube URL/ID, transcript, or uploaded video
    if not video_input and not transcript and not uploaded_video:
        raise HTTPException(
            status_code=400,
            detail="Provide a YouTube video URL/ID, paste a transcript, or upload a video.",
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
            uploaded_video_path=request.uploaded_video_path,
            transcription_bundle=request.transcription_bundle,
        )

    if uploaded_video:
        # Generate from uploaded video
        return _generate_from_video(
            None,
            current_user.id,
            selected_target_assets,
            uploaded_video=uploaded_video,
            uploaded_video_path=request.uploaded_video_path,
            transcription_bundle=request.transcription_bundle,
        )

    return _generate_from_video(
        video_input,
        current_user.id,
        selected_target_assets,
        uploaded_video_path=request.uploaded_video_path,
        transcription_bundle=request.transcription_bundle,
    )


@app.post("/upload-video")
async def upload_video(
    file: UploadFile,
    current_user: UserResponse = Depends(require_current_user),
):
    """
    Upload a video file for transcript generation.
    Returns the transcript of the uploaded video.
    """
    logger = logging.getLogger(__name__)
    logger.info(
        "Upload-video request received: user_id=%s filename=%s content_type=%s",
        current_user.id,
        file.filename,
        file.content_type,
    )
    # Validate file size (limit to 100MB)
    file_size = len(file.file.read())
    file.file.seek(0)  # Reset file pointer
    max_size = 100 * 1024 * 1024  # 100MB
    if file_size > max_size:
        logger.warning(
            "Upload-video rejected for size: filename=%s size_bytes=%s",
            file.filename,
            file_size,
        )
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is 100MB. Your file is {file_size / (1024 * 1024):.1f}MB.",
        )

    try:
        logger.info("Calling transcribe_uploaded_video_with_artifacts for %s", file.filename)
        transcript, transcription_path, transcription_bundle, stored_video_path = transcribe_uploaded_video_with_artifacts(file)
        logger.info("Upload-video transcription completed for %s", file.filename)
        return {
            "filename": file.filename,
            "transcript": transcript,
            "transcription_path": str(transcription_path),
            "transcription_bundle": transcription_bundle,
            "stored_video_path": str(stored_video_path),
            "stored_video_url": "",
            "message": "Video processed successfully.",
        }
    except HTTPException:
        raise
    except Exception as error:
        logger = logging.getLogger(__name__)
        logger.exception("Error processing uploaded video: %s", error)
        raise HTTPException(
            status_code=500,
            detail=f"Error processing video: {str(error)}",
        )


@app.post("/generation-jobs")
def create_generation_job(
    request: GenerateContentRequest,
    current_user: UserResponse = Depends(require_current_user),
):
    video_input = request.video_url or request.video_id
    transcript = request.transcript.strip()

    # Check for valid input: YouTube URL/ID or transcript
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
        "uploaded_video_filename": request.uploaded_video_filename,
        "uploaded_video_content_type": request.uploaded_video_content_type,
        "uploaded_video_path": request.uploaded_video_path,
        "uploaded_video_url": request.uploaded_video_url,
        "transcription_bundle": request.transcription_bundle,
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
                uploaded_video_path=request.uploaded_video_path,
                transcription_bundle=request.transcription_bundle,
            )

        return _generate_from_video(
            video_input,
            current_user.id,
            selected_target_assets,
            progress_callback=progress_callback,
            uploaded_video_path=request.uploaded_video_path,
            transcription_bundle=request.transcription_bundle,
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
    
    
@app.get("/check")
def check():
    ffmpeg = subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]
    return {"ffmpeg": ffmpeg}

@app.get("/check-ffprobe")
def check_ffprobe():
    out = subprocess.check_output(["ffprobe", "-version"], text=True)
    return {"ffprobe": out.splitlines()[0]}