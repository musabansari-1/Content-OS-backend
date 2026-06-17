# import inspect
# import json
# import logging
# import mimetypes
# import os
# from copy import deepcopy
# from pathlib import Path
# from typing import Any, Optional
# from urllib.parse import quote

# import requests
# from fastapi import HTTPException, UploadFile

# from app.agents.execution_agent import run_execution_pipeline
# from app.agents.moment_agent import extract_moments
# from app.agents.strategy_agent import generate_strategy
# from app.api.services import creator_voice_profile_service
# from app.assets import AVAILABLE_TARGET_ASSETS, build_asset_brief, get_asset_catalog, normalize_target_assets
# from app.billing.service import ensure_can_generate_assets, record_generated_assets
# from app.core.config import env
# from app.generation_jobs import generation_job_store
# from app.utils.generate_video_clips import generate_short_clips_from_groq
# from app.voice_engine.types import GenerateContentRequest
# from app.youtube_transcripts import (
#     fetch_video_transcript,
#     resolve_uploaded_video_path,
#     transcribe_uploaded_video,
#     transcribe_uploaded_video_with_artifacts,
# )


# def _resolve_generated_clips_dir() -> Path:
#     configured = (env("GENERATED_CLIPS_DIR", "/tmp/generated_clips") or "/tmp/generated_clips").strip()
#     path = Path(configured).expanduser()
#     if not path.is_absolute():
#         path = (Path.cwd() / path).resolve()
#     path.mkdir(parents=True, exist_ok=True)
#     return path


# GENERATED_CLIPS_DIR = _resolve_generated_clips_dir()

# SHORT_VIDEO_ASSET_TYPES = {
#     asset_type
#     for asset_type, asset in AVAILABLE_TARGET_ASSETS.items()
#     if asset.get("output_type") == "short_video"
# }

# SUPABASE_URL = (env("SUPABASE_URL", "") or "").strip().rstrip("/")
# SUPABASE_SERVICE_ROLE_KEY = (env("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
# SUPABASE_STORAGE_BUCKET = (env("SUPABASE_STORAGE_BUCKET", "clips") or "clips").strip()

# S3_BUCKET = (env("S3_BUCKET", "") or "").strip()
# S3_REGION = (env("AWS_REGION", env("AWS_DEFAULT_REGION", "")) or "").strip()
# S3_ENDPOINT_URL = (env("S3_ENDPOINT_URL", "") or "").strip()
# S3_PUBLIC_BASE_URL = (env("S3_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

# try:
#     import boto3
#     from botocore.exceptions import BotoCoreError, ClientError
# except ImportError:
#     boto3 = None  # type: ignore
#     BotoCoreError = ClientError = Exception  # type: ignore[misc,assignment]


# def list_target_assets() -> dict[str, Any]:
#     return {"target_assets": get_asset_catalog()}


# def generate_from_query(
#     *,
#     video_id: Optional[str],
#     video_url: Optional[str],
#     target_assets: Optional[str],
#     user_id: int,
# ) -> dict[str, Any]:
#     video_input = video_url or video_id

#     if not video_input:
#         raise HTTPException(status_code=400, detail="Provide either video_id or video_url.")

#     try:
#         selected_target_assets = normalize_target_assets(
#             target_assets.split(",") if target_assets else None
#         )
#     except ValueError as error:
#         raise HTTPException(status_code=400, detail=str(error))

#     ensure_can_generate_assets(user_id, len(selected_target_assets))

#     result = _generate_from_video(
#         video_input,
#         user_id,
#         selected_target_assets,
#     )
#     record_generated_assets(user_id, len(result.get("results", [])))
#     return result


# def generate_from_request(
#     *,
#     request: GenerateContentRequest,
#     user_id: int,
#     uploaded_video: Optional[UploadFile] = None,
# ) -> dict[str, Any]:
#     video_input = request.video_url or request.video_id
#     transcript = request.transcript.strip()

#     if not video_input and not transcript and not uploaded_video:
#         raise HTTPException(
#             status_code=400,
#             detail="Provide a YouTube video URL/ID, paste a transcript, or upload a video.",
#         )

#     try:
#         selected_target_assets = normalize_target_assets(request.target_assets)
#     except ValueError as error:
#         raise HTTPException(status_code=400, detail=str(error))

#     ensure_can_generate_assets(user_id, len(selected_target_assets))

#     if transcript:
#         result = _generate_from_transcript(
#             transcript,
#             user_id,
#             selected_target_assets,
#             uploaded_video_path=request.uploaded_video_path,
#             transcription_bundle=request.transcription_bundle,
#         )
#         record_generated_assets(user_id, len(result.get("results", [])))
#         return result

#     if uploaded_video:
#         result = _generate_from_video(
#             None,
#             user_id,
#             selected_target_assets,
#             uploaded_video=uploaded_video,
#             uploaded_video_path=request.uploaded_video_path,
#             transcription_bundle=request.transcription_bundle,
#         )
#         record_generated_assets(user_id, len(result.get("results", [])))
#         return result

#     result = _generate_from_video(
#         video_input,
#         user_id,
#         selected_target_assets,
#         uploaded_video_path=request.uploaded_video_path,
#         transcription_bundle=request.transcription_bundle,
#     )
#     record_generated_assets(user_id, len(result.get("results", [])))
#     return result


# def process_uploaded_video(file: UploadFile, user_id: int) -> dict[str, Any]:
#     logger = logging.getLogger(__name__)
#     logger.info(
#         "Upload-video request received: user_id=%s filename=%s content_type=%s",
#         user_id,
#         file.filename,
#         file.content_type,
#     )

#     file.file.seek(0, 2)
#     file_size = file.file.tell()
#     file.file.seek(0)
#     max_size = 100 * 1024 * 1024
#     if file_size > max_size:
#         logger.warning(
#             "Upload-video rejected for size: filename=%s size_bytes=%s",
#             file.filename,
#             file_size,
#         )
#         raise HTTPException(
#             status_code=400,
#             detail=f"File too large. Maximum size is 100MB. Your file is {file_size / (1024 * 1024):.1f}MB.",
#         )

#     try:
#         logger.info("Calling transcribe_uploaded_video_with_artifacts for %s", file.filename)
#         transcript, transcription_path, transcription_bundle, stored_video_path = transcribe_uploaded_video_with_artifacts(file)
#         logger.info("Upload-video transcription completed for %s", file.filename)
#         return {
#             "filename": file.filename,
#             "transcript": transcript,
#             "transcription_path": str(transcription_path),
#             "transcription_bundle": transcription_bundle,
#             "stored_video_path": str(stored_video_path),
#             "stored_video_url": "",
#             "message": "Video processed successfully.",
#         }
#     except HTTPException:
#         raise
#     except Exception as error:
#         logger.exception("Error processing uploaded video: %s", error)
#         raise HTTPException(
#             status_code=500,
#             detail=f"Error processing video: {str(error)}",
#         )


# def create_generation_job(request: GenerateContentRequest, user_id: int) -> dict[str, Any]:
#     video_input = request.video_url or request.video_id
#     transcript = request.transcript.strip()

#     if not video_input and not transcript:
#         raise HTTPException(
#             status_code=400,
#             detail="Provide a YouTube video URL/ID or paste a transcript.",
#         )

#     try:
#         selected_target_assets = normalize_target_assets(request.target_assets)
#     except ValueError as error:
#         raise HTTPException(status_code=400, detail=str(error))

#     ensure_can_generate_assets(user_id, len(selected_target_assets))

#     payload = {
#         "video_id": request.video_id,
#         "video_url": request.video_url,
#         "transcript": transcript,
#         "target_assets": selected_target_assets,
#         "uploaded_video_filename": request.uploaded_video_filename,
#         "uploaded_video_content_type": request.uploaded_video_content_type,
#         "uploaded_video_path": request.uploaded_video_path,
#         "uploaded_video_url": request.uploaded_video_url,
#         "transcription_bundle": request.transcription_bundle,
#     }
#     job = generation_job_store.create_job(user_id, payload)
#     progress_callback = _build_progress_callback(job["id"])

#     def runner():
#         if transcript:
#             result = _generate_from_transcript(
#                 transcript,
#                 user_id,
#                 selected_target_assets,
#                 progress_callback=progress_callback,
#                 uploaded_video_path=request.uploaded_video_path,
#                 transcription_bundle=request.transcription_bundle,
#             )
#             record_generated_assets(user_id, len(result.get("results", [])))
#             return result

#         result = _generate_from_video(
#             video_input,
#             user_id,
#             selected_target_assets,
#             progress_callback=progress_callback,
#             uploaded_video_path=request.uploaded_video_path,
#             transcription_bundle=request.transcription_bundle,
#         )
#         record_generated_assets(user_id, len(result.get("results", [])))
#         return result

#     generation_job_store.start_job(job["id"], runner)
#     return job


# def get_generation_job(job_id: str, user_id: int) -> dict[str, Any]:
#     job = generation_job_store.get_job(job_id, user_id)
#     if not job:
#         raise HTTPException(status_code=404, detail="Generation job not found.")
#     return job


# def _build_supabase_public_url(object_path: str) -> str:
#     if not SUPABASE_URL:
#         raise RuntimeError("SUPABASE_URL is not set.")
#     bucket = SUPABASE_STORAGE_BUCKET.strip("/")
#     return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{quote(object_path, safe='/')}"


# def _build_s3_public_url(object_path: str) -> str:
#     if S3_PUBLIC_BASE_URL:
#         return f"{S3_PUBLIC_BASE_URL}/{quote(object_path, safe='/')}"
#     if not S3_BUCKET:
#         raise RuntimeError("S3_BUCKET is not set.")
#     if not S3_REGION:
#         raise RuntimeError("AWS_REGION or AWS_DEFAULT_REGION is not set.")
#     return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{quote(object_path, safe='/')}"


# def _upload_file_to_s3(local_path: Path, object_path: str, content_type: str | None = None) -> str:
#     if boto3 is None:
#         raise RuntimeError("boto3 is not installed. Add boto3 to requirements to use S3 uploads.")
#     if not S3_BUCKET:
#         raise RuntimeError("S3_BUCKET is not set.")

#     session = boto3.session.Session(region_name=S3_REGION or None)
#     client = session.client("s3", endpoint_url=S3_ENDPOINT_URL or None)
#     extra_args: dict[str, Any] = {}
#     guessed_type = content_type or mimetypes.guess_type(local_path.name)[0]
#     if guessed_type:
#         extra_args["ContentType"] = guessed_type

#     logger = logging.getLogger(__name__)
#     logger.info("Uploading file to S3: local_path=%s object_path=%s bucket=%s", local_path, object_path, S3_BUCKET)
#     try:
#         client.upload_file(str(local_path), S3_BUCKET, object_path, ExtraArgs=extra_args or None)
#     except (BotoCoreError, ClientError) as error:
#         raise RuntimeError(f"S3 upload failed for {local_path.name}: {error}") from error

#     public_url = _build_s3_public_url(object_path)
#     logger.info("Uploaded file to S3: object_path=%s public_url=%s", object_path, public_url)
#     return public_url


# def _upload_file_to_supabase(local_path: Path, object_path: str, content_type: str | None = None) -> str:
#     if not SUPABASE_URL:
#         raise RuntimeError("SUPABASE_URL is not set.")
#     if not SUPABASE_SERVICE_ROLE_KEY:
#         raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set.")

#     bucket = SUPABASE_STORAGE_BUCKET.strip("/")
#     upload_url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{quote(object_path, safe='/')}"
#     guessed_type = content_type or mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"

#     headers = {
#         "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
#         "apikey": SUPABASE_SERVICE_ROLE_KEY,
#         "Content-Type": guessed_type,
#         "x-upsert": "true",
#     }

#     logger = logging.getLogger(__name__)
#     logger.info("Uploading file to Supabase: local_path=%s object_path=%s", local_path, object_path)
#     with local_path.open("rb") as file_obj:
#         response = requests.put(upload_url, headers=headers, data=file_obj, timeout=120)
#     if not response.ok:
#         raise RuntimeError(
#             f"Supabase upload failed for {local_path.name}: {response.status_code} {response.text}"
#         )

#     public_url = _build_supabase_public_url(object_path)
#     logger.info("Uploaded file to Supabase: object_path=%s public_url=%s", object_path, public_url)
#     return public_url


# def _upload_generated_clip(
#     local_path: Path,
#     object_path: str,
#     content_type: str | None = None,
# ) -> str:
#     if S3_BUCKET:
#         return _upload_file_to_s3(local_path, object_path, content_type)
#     return _upload_file_to_supabase(local_path, object_path, content_type)


# def _merge_steps(current_steps, updates):
#     step_map = {step["key"]: dict(step) for step in current_steps}
#     for step_key, status in (updates or {}).items():
#         if step_key in step_map:
#             step_map[step_key]["status"] = status
#     return [step_map[step["key"]] for step in current_steps]


# def _build_progress_callback(job_id: str):
#     def emit(event):
#         current_job = generation_job_store.get_job_snapshot(job_id)
#         current_steps = current_job["steps"] if current_job else []
#         generation_job_store.update_job(
#             job_id,
#             status="running",
#             stage=event.get("stage", "running"),
#             message=event.get("message", "Generation in progress."),
#             detail=event.get("detail", ""),
#             progress_percent=event.get("progress_percent", 5),
#             steps=_merge_steps(current_steps, event.get("steps")),
#             asset_progress=event.get("asset_progress", current_job.get("asset_progress", []) if current_job else []),
#         )

#     return emit


# def _run_generation_pipeline(
#     source_text: str,
#     target_assets: list[str],
#     user_id: int,
#     progress_callback=None,
#     skip_text_asset_types: set[str] | None = None,
# ):
#     logger = logging.getLogger(__name__)
#     logger.info(
#         "run_generation_pipeline started: user_id=%s target_assets=%s source_chars=%s skip_text_asset_types=%s",
#         user_id,
#         target_assets,
#         len(source_text or ""),
#         sorted(skip_text_asset_types) if skip_text_asset_types else [],
#     )
#     if progress_callback:
#         progress_callback(
#             {
#                 "stage": "moments",
#                 "message": "Understanding your input.",
#                 "detail": "Reviewing the source to prepare the content pack.",
#                 "progress_percent": 12,
#                 "steps": {"source": "completed", "moments": "active"},
#             }
#         )

#     moments = extract_moments(source_text)

#     if progress_callback:
#         progress_callback(
#             {
#                 "stage": "strategy",
#                 "message": "Preparing your content pack.",
#                 "detail": "Structuring the requested outputs before creation begins.",
#                 "progress_percent": 22,
#                 "steps": {"moments": "completed", "strategy": "active"},
#             }
#         )

#     strategy_output = generate_strategy(
#         {
#             "transcript": source_text,
#             "moments": moments,
#             "target_assets": target_assets,
#             "asset_catalog": build_asset_brief(target_assets),
#         }
#     )

#     strategy_output = json.loads(strategy_output)
#     execution_plan = strategy_output["execution_plan"]

#     if progress_callback:
#         progress_callback(
#             {
#                 "stage": "execution",
#                 "message": "Starting creation.",
#                 "detail": "Moving from setup into the main generation phase.",
#                 "progress_percent": 30,
#                 "steps": {"strategy": "completed", "execution": "active"},
#             }
#         )

#     execution_kwargs = {
#         "user_id": user_id,
#         "creator_voice_profile_service": creator_voice_profile_service,
#         "progress_callback": progress_callback,
#     }
#     if "skip_text_asset_types" in inspect.signature(run_execution_pipeline).parameters:
#         execution_kwargs["skip_text_asset_types"] = skip_text_asset_types

#     results = run_execution_pipeline(
#         execution_plan,
#         source_text,
#         **execution_kwargs,
#     )
#     logger.info("run_execution_pipeline completed: user_id=%s results=%s", user_id, len(results))

#     if progress_callback:
#         progress_callback(
#             {
#                 "stage": "finalize",
#                 "message": "Wrapping things up.",
#                 "detail": "Final touches are being applied before display.",
#                 "progress_percent": 96,
#                 "steps": {"execution": "completed", "finalize": "active"},
#             }
#         )

#     return {
#         "strategy": strategy_output,
#         "results": results,
#     }


# def _build_generated_clip_media(
#     run_id: str,
#     clip_payload: dict[str, Any],
#     video_url: str | None = None,
#     subtitle_url: str | None = None,
# ) -> dict[str, Any]:
#     clip_details = clip_payload.get("clip", {})
#     video_path = Path(clip_payload["video_path"])
#     media = {
#         "kind": "video",
#         "label": clip_details.get("title") or "Generated clip",
#         "video_path": str(video_path),
#         "video_url": video_url or f"/generated-clips/{run_id}/clips/{video_path.name}",
#         "clip_id": clip_details.get("clip_id"),
#         "start": clip_details.get("start"),
#         "end": clip_details.get("end"),
#         "duration": clip_details.get("duration"),
#         "score": clip_details.get("score"),
#         "rationale": clip_details.get("rationale"),
#         "transcript_text": clip_details.get("transcript_text"),
#         "target_asset_type": clip_payload.get("target_asset_type") or clip_details.get("target_asset_type"),
#         "platform_profile": clip_payload.get("platform_profile") or clip_details.get("platform_profile"),
#     }

#     subtitle_path = clip_payload.get("subtitle_path")
#     if subtitle_path:
#         subtitle_file = Path(subtitle_path)
#         media["subtitle_path"] = str(subtitle_file)
#         media["subtitle_url"] = subtitle_url or f"/generated-clips/{run_id}/subtitles/{subtitle_file.name}"

#     return media


# def _attach_generated_clips_to_results(
#     pipeline_result: dict[str, Any],
#     uploaded_video_path: str | None,
#     transcription_bundle: dict[str, Any] | None,
#     target_assets: list[str],
#     progress_callback=None,
# ) -> dict[str, Any]:
#     logger = logging.getLogger(__name__)
#     if not uploaded_video_path:
#         logger.info("Skipping clip attachment because no uploaded_video_path was provided.")
#         return pipeline_result

#     requested_short_assets = [
#         asset_type for asset_type in target_assets if asset_type in SHORT_VIDEO_ASSET_TYPES
#     ]
#     if not requested_short_assets:
#         logger.info(
#             "Skipping clip attachment because no short-video assets were requested. target_assets=%s",
#             target_assets,
#         )
#         return pipeline_result

#     if not transcription_bundle:
#         logger.warning(
#             "Skipping clip attachment because transcription_bundle is missing. uploaded_video_path=%s",
#             uploaded_video_path,
#         )
#         return pipeline_result

#     logger.info(
#         "Preparing clip attachment: uploaded_video_path=%s requested_short_assets=%s transcription_keys=%s",
#         uploaded_video_path,
#         requested_short_assets,
#         sorted(transcription_bundle.keys()),
#     )

#     try:
#         source_video_path = str(resolve_uploaded_video_path(uploaded_video_path))
#     except Exception:
#         logger.exception(
#             "Failed to resolve uploaded video path for clip rendering: uploaded_video_path=%s",
#             uploaded_video_path,
#         )
#         raise

#     if progress_callback:
#         progress_callback(
#             {
#                 "stage": "execution_video",
#                 "message": "Rendering short-form clips.",
#                 "detail": "Turning the uploaded source video into playable short clips.",
#                 "progress_percent": 88,
#                 "steps": {"execution": "completed", "finalize": "active"},
#             }
#         )

#     logger.info(
#         "Starting generate_short_clips_from_groq: source_video_path=%s clip_count=%s output_dir=%s",
#         source_video_path,
#         len(requested_short_assets),
#         str(GENERATED_CLIPS_DIR),
#     )

#     try:
#         clip_result = generate_short_clips_from_groq(
#             source_video_path=source_video_path,
#             transcription=transcription_bundle,
#             clip_count=len(requested_short_assets),
#             output_dir=str(GENERATED_CLIPS_DIR),
#             create_blur_background=True,
#             debug=True,
#             target_asset_types=requested_short_assets,
#         )
#     except Exception:
#         logger.exception(
#             "Clip rendering failed: source_video_path=%s target_assets=%s",
#             source_video_path,
#             requested_short_assets,
#         )
#         raise

#     logger.info(
#         "Clip rendering finished: run_id=%s selected_clips=%s output_dir=%s",
#         clip_result.get("run_id"),
#         len(clip_result.get("selected_clips", [])),
#         clip_result.get("output_dir"),
#     )

#     selected_clips = clip_result.get("selected_clips", [])
#     if not selected_clips:
#         logger.warning("Clip rendering returned no selected_clips; returning original pipeline result.")
#         return pipeline_result

#     clips_by_asset_type = {
#         clip.get("target_asset_type") or clip.get("clip", {}).get("target_asset_type"): clip
#         for clip in selected_clips
#         if clip.get("target_asset_type") or clip.get("clip", {}).get("target_asset_type")
#     }

#     results = []
#     short_asset_index = 0
#     for result in pipeline_result["results"]:
#         next_result = dict(result)
#         asset_type = result.get("asset_type")
#         if asset_type in requested_short_assets:
#             clip_payload = clips_by_asset_type.get(asset_type)
#             if clip_payload is None and short_asset_index < len(selected_clips):
#                 clip_payload = selected_clips[short_asset_index]
#             if clip_payload is None:
#                 results.append(next_result)
#                 continue

#             clip_video_path = Path(clip_payload["video_path"])
#             clip_subtitle_path = clip_payload.get("subtitle_path")
#             try:
#                 clip_public_url = _upload_generated_clip(
#                     clip_video_path,
#                     f"clips/{clip_result.get('run_id', 'run')}/{clip_video_path.name}",
#                     "video/mp4",
#                 )
#                 subtitle_public_url = None
#                 if clip_subtitle_path:
#                     subtitle_file = Path(clip_subtitle_path)
#                     subtitle_public_url = _upload_generated_clip(
#                         subtitle_file,
#                         f"subtitles/{clip_result.get('run_id', 'run')}/{subtitle_file.name}",
#                         "text/plain",
#                     )
#             except Exception:
#                 logger.exception(
#                     "Failed to upload rendered clip: clip_path=%s subtitle_path=%s",
#                     clip_payload.get("video_path"),
#                     clip_subtitle_path,
#                 )
#                 raise

#             logger.info(
#                 "Mapping rendered clip to asset_type=%s clip_id=%s platform_profile=%s video_path=%s clip_url=%s",
#                 asset_type,
#                 clip_payload.get("clip", {}).get("clip_id"),
#                 clip_payload.get("platform_profile") or clip_payload.get("clip", {}).get("platform_profile"),
#                 clip_payload.get("video_path"),
#                 clip_public_url,
#             )
#             output_payload = {
#                 "generated_clip": {
#                     "title": clip_payload.get("clip", {}).get("title"),
#                     "start": clip_payload.get("clip", {}).get("start"),
#                     "end": clip_payload.get("clip", {}).get("end"),
#                     "duration": clip_payload.get("clip", {}).get("duration"),
#                     "score": clip_payload.get("clip", {}).get("score"),
#                     "rationale": clip_payload.get("clip", {}).get("rationale"),
#                     "platform_profile": clip_payload.get("platform_profile") or clip_payload.get("clip", {}).get("platform_profile"),
#                 }
#             }
#             next_result["output"] = json.dumps(output_payload)
#             next_result["media"] = _build_generated_clip_media(
#                 clip_result["run_id"],
#                 clip_payload,
#                 video_url=clip_public_url,
#                 subtitle_url=subtitle_public_url,
#             )
#             short_asset_index += 1

#         results.append(next_result)

#     return {
#         **pipeline_result,
#         "results": results,
#         "generated_clips": clip_result,
#     }


# def _generate_from_video(
#     video_input: Optional[str],
#     user_id: int,
#     target_assets: list[str],
#     progress_callback=None,
#     uploaded_video: Optional[UploadFile] = None,
#     uploaded_video_path: Optional[str] = None,
#     transcription_bundle: Optional[dict[str, Any]] = None,
# ):
#     logger = logging.getLogger(__name__)
#     if progress_callback:
#         progress_callback(
#             {
#                 "stage": "source",
#                 "message": "Getting your source ready.",
#                 "detail": "Bringing in the source material for generation.",
#                 "progress_percent": 6,
#                 "steps": {"source": "active"},
#             }
#         )

#     if uploaded_video:
#         logger.info(
#             "_generate_from_video using uploaded video: filename=%s uploaded_video_path=%s transcription_bundle_present=%s",
#             getattr(uploaded_video, "filename", None),
#             uploaded_video_path,
#             bool(transcription_bundle),
#         )
#         transcript = transcribe_uploaded_video(uploaded_video)
#     else:
#         logger.info("_generate_from_video using video_input=%s", video_input)
#         transcript = fetch_video_transcript(video_input)  # type: ignore[arg-type]

#     pipeline_result = _run_generation_pipeline(
#         transcript,
#         target_assets,
#         user_id,
#         progress_callback=progress_callback,
#         skip_text_asset_types=set(SHORT_VIDEO_ASSET_TYPES) if uploaded_video_path else None,
#     )

#     return _attach_generated_clips_to_results(
#         pipeline_result,
#         uploaded_video_path=uploaded_video_path,
#         transcription_bundle=transcription_bundle,
#         target_assets=target_assets,
#         progress_callback=progress_callback,
#     )


# def _generate_from_transcript(
#     transcript: str,
#     user_id: int,
#     target_assets: list[str],
#     progress_callback=None,
#     uploaded_video_path: Optional[str] = None,
#     transcription_bundle: Optional[dict[str, Any]] = None,
# ):
#     logger = logging.getLogger(__name__)
#     normalized_transcript = transcript.strip()
#     if not normalized_transcript:
#         raise HTTPException(status_code=400, detail="A transcript is required.")

#     if progress_callback:
#         progress_callback(
#             {
#                 "stage": "source",
#                 "message": "Getting your source ready.",
#                 "detail": "Bringing in the source material for generation.",
#                 "progress_percent": 8,
#                 "steps": {"source": "active"},
#             }
#         )

#     pipeline_result = _run_generation_pipeline(
#         normalized_transcript,
#         target_assets,
#         user_id,
#         progress_callback=progress_callback,
#         skip_text_asset_types=set(SHORT_VIDEO_ASSET_TYPES) if uploaded_video_path else None,
#     )
#     logger.info(
#         "_generate_from_transcript completed: user_id=%s target_assets=%s uploaded_video_path=%s transcription_bundle_present=%s",
#         user_id,
#         target_assets,
#         uploaded_video_path,
#         bool(transcription_bundle),
#     )

#     return _attach_generated_clips_to_results(
#         pipeline_result,
#         uploaded_video_path=uploaded_video_path,
#         transcription_bundle=transcription_bundle,
#         target_assets=target_assets,
#         progress_callback=progress_callback,
#     )


import inspect
import json
import logging
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests
from fastapi import HTTPException, UploadFile

from app.agents.execution_agent import run_execution_pipeline
from app.agents.moment_agent import extract_moments
from app.agents.strategy_agent import generate_strategy
from app.api.services import creator_voice_profile_service
from app.assets import (
    AVAILABLE_TARGET_ASSETS,
    build_asset_brief,
    get_asset_catalog,
    normalize_target_assets,
)
from app.billing.service import ensure_can_generate_assets, record_generated_assets
from app.core.config import env
from app.generation_jobs import generation_job_store
from app.utils.generate_video_clips import generate_short_clips_from_groq
from app.voice_engine.types import GenerateContentRequest
from app.youtube_transcripts import (
    fetch_video_transcript,
    resolve_uploaded_video_path,
    transcribe_uploaded_video,
    transcribe_uploaded_video_with_artifacts,
)


try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None  # type: ignore
    Config = None  # type: ignore
    BotoCoreError = ClientError = Exception  # type: ignore[misc,assignment]


logger = logging.getLogger(__name__)


def _resolve_generated_clips_dir() -> Path:
    configured = (env("GENERATED_CLIPS_DIR", "/tmp/generated_clips") or "/tmp/generated_clips").strip()
    path = Path(configured).expanduser()

    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    path.mkdir(parents=True, exist_ok=True)
    return path


GENERATED_CLIPS_DIR = _resolve_generated_clips_dir()

SHORT_VIDEO_ASSET_TYPES = {
    asset_type
    for asset_type, asset in AVAILABLE_TARGET_ASSETS.items()
    if asset.get("output_type") == "short_video"
}


# ---------------------------------------------------------------------
# Storage config
# ---------------------------------------------------------------------

SUPABASE_URL = (env("SUPABASE_URL", "") or "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = (env("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
SUPABASE_STORAGE_BUCKET = (env("SUPABASE_STORAGE_BUCKET", "clips") or "clips").strip()

S3_BUCKET = (env("S3_BUCKET", "") or "").strip()
S3_REGION = (env("AWS_REGION", env("AWS_DEFAULT_REGION", "")) or "").strip()
S3_PROFILE = (env("AWS_PROFILE", "") or "").strip()
S3_ENDPOINT_URL = (env("S3_ENDPOINT_URL", "") or "").strip() or None

# Optional CDN/public base URL. Keep empty for private S3 signed URLs.
S3_PUBLIC_BASE_URL = (env("S3_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

# Default: private bucket + signed URLs.
S3_USE_PRESIGNED_URLS = (
    (env("S3_USE_PRESIGNED_URLS", "true") or "true").strip().lower()
    not in {"false", "0", "no"}
)

S3_PRESIGNED_URL_EXPIRES_SECONDS = int(
    env("S3_PRESIGNED_URL_EXPIRES_SECONDS", "3600") or "3600"
)

S3_SERVER_SIDE_ENCRYPTION = (
    env("S3_SERVER_SIDE_ENCRYPTION", "AES256") or "AES256"
).strip()

S3_VIDEO_CACHE_CONTROL = (
    env("S3_VIDEO_CACHE_CONTROL", "private, max-age=3600") or "private, max-age=3600"
).strip()

S3_TEXT_CACHE_CONTROL = (
    env("S3_TEXT_CACHE_CONTROL", "private, max-age=3600") or "private, max-age=3600"
).strip()


@dataclass(frozen=True)
class StorageUploadResult:
    provider: str
    bucket: str
    object_key: str
    url: str
    content_type: str
    expires_in: Optional[int] = None
    is_signed_url: bool = False


def _normalize_object_path(object_path: str) -> str:
    cleaned = object_path.replace("\\", "/").strip().lstrip("/")

    if not cleaned:
        raise RuntimeError("Storage object path cannot be empty.")

    if ".." in cleaned.split("/"):
        raise RuntimeError(f"Invalid storage object path: {object_path}")

    return cleaned


def _guess_content_type(local_path: Path, explicit_content_type: str | None = None) -> str:
    if explicit_content_type:
        return explicit_content_type

    guessed_type = mimetypes.guess_type(local_path.name)[0]
    return guessed_type or "application/octet-stream"


def _validate_local_file(local_path: Path) -> None:
    if not local_path.exists():
        raise RuntimeError(f"File does not exist: {local_path}")

    if not local_path.is_file():
        raise RuntimeError(f"Path is not a file: {local_path}")

    if local_path.stat().st_size <= 0:
        raise RuntimeError(f"File is empty: {local_path}")


@lru_cache(maxsize=1)
def _get_s3_client():
    if boto3 is None:
        raise RuntimeError("boto3 is not installed. Add boto3 to requirements.txt.")

    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET is not set.")

    session_kwargs: dict[str, Any] = {}

    if S3_PROFILE:
        session_kwargs["profile_name"] = S3_PROFILE

    if S3_REGION:
        session_kwargs["region_name"] = S3_REGION

    session = boto3.session.Session(**session_kwargs)

    client_kwargs: dict[str, Any] = {}

    if S3_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = S3_ENDPOINT_URL

    if Config is not None:
        client_kwargs["config"] = Config(
            signature_version="s3v4",
            retries={
                "max_attempts": 3,
                "mode": "standard",
            },
        )

    return session.client("s3", **client_kwargs)


def _build_s3_public_url(object_path: str) -> str:
    object_key = _normalize_object_path(object_path)

    if S3_PUBLIC_BASE_URL:
        return f"{S3_PUBLIC_BASE_URL}/{quote(object_key, safe='/')}"

    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET is not set.")

    if not S3_REGION:
        raise RuntimeError("AWS_REGION or AWS_DEFAULT_REGION is not set.")

    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{quote(object_key, safe='/')}"


def _build_s3_signed_url(object_path: str, expires_in: int | None = None) -> str:
    object_key = _normalize_object_path(object_path)
    client = _get_s3_client()

    return client.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": S3_BUCKET,
            "Key": object_key,
        },
        ExpiresIn=expires_in or S3_PRESIGNED_URL_EXPIRES_SECONDS,
    )


def _upload_file_to_s3(
    local_path: Path,
    object_path: str,
    content_type: str | None = None,
) -> StorageUploadResult:
    _validate_local_file(local_path)

    object_key = _normalize_object_path(object_path)
    resolved_content_type = _guess_content_type(local_path, content_type)
    client = _get_s3_client()

    extra_args: dict[str, Any] = {
        "ContentType": resolved_content_type,
    }

    if resolved_content_type.startswith("video/"):
        extra_args["CacheControl"] = S3_VIDEO_CACHE_CONTROL
    elif resolved_content_type.startswith("text/"):
        extra_args["CacheControl"] = S3_TEXT_CACHE_CONTROL

    # Good default for production private files.
    # You can disable by setting S3_SERVER_SIDE_ENCRYPTION="".
    if S3_SERVER_SIDE_ENCRYPTION:
        extra_args["ServerSideEncryption"] = S3_SERVER_SIDE_ENCRYPTION

    logger.info(
        "Uploading file to S3: local_path=%s object_key=%s bucket=%s content_type=%s size_bytes=%s",
        local_path,
        object_key,
        S3_BUCKET,
        resolved_content_type,
        local_path.stat().st_size,
    )

    try:
        client.upload_file(
            Filename=str(local_path),
            Bucket=S3_BUCKET,
            Key=object_key,
            ExtraArgs=extra_args,
        )
    except (BotoCoreError, ClientError) as error:
        logger.exception(
            "S3 upload failed: local_path=%s object_key=%s bucket=%s",
            local_path,
            object_key,
            S3_BUCKET,
        )
        raise RuntimeError(f"S3 upload failed for {local_path.name}: {error}") from error

    if S3_USE_PRESIGNED_URLS:
        playback_url = _build_s3_signed_url(object_key)
        expires_in = S3_PRESIGNED_URL_EXPIRES_SECONDS
        is_signed_url = True
    else:
        playback_url = _build_s3_public_url(object_key)
        expires_in = None
        is_signed_url = False

    logger.info(
        "Uploaded file to S3: object_key=%s signed_url=%s expires_in=%s",
        object_key,
        is_signed_url,
        expires_in,
    )

    return StorageUploadResult(
        provider="s3",
        bucket=S3_BUCKET,
        object_key=object_key,
        url=playback_url,
        content_type=resolved_content_type,
        expires_in=expires_in,
        is_signed_url=is_signed_url,
    )


def _build_supabase_public_url(object_path: str) -> str:
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is not set.")

    object_key = _normalize_object_path(object_path)
    bucket = SUPABASE_STORAGE_BUCKET.strip("/")

    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{quote(object_key, safe='/')}"


def _upload_file_to_supabase(
    local_path: Path,
    object_path: str,
    content_type: str | None = None,
) -> StorageUploadResult:
    _validate_local_file(local_path)

    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is not set.")

    if not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set.")

    object_key = _normalize_object_path(object_path)
    bucket = SUPABASE_STORAGE_BUCKET.strip("/")
    resolved_content_type = _guess_content_type(local_path, content_type)

    upload_url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{quote(object_key, safe='/')}"

    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": resolved_content_type,
        "x-upsert": "true",
    }

    logger.info(
        "Uploading file to Supabase: local_path=%s object_key=%s bucket=%s content_type=%s size_bytes=%s",
        local_path,
        object_key,
        bucket,
        resolved_content_type,
        local_path.stat().st_size,
    )

    with local_path.open("rb") as file_obj:
        response = requests.put(upload_url, headers=headers, data=file_obj, timeout=120)

    if not response.ok:
        logger.error(
            "Supabase upload failed: object_key=%s status=%s response=%s",
            object_key,
            response.status_code,
            response.text,
        )
        raise RuntimeError(
            f"Supabase upload failed for {local_path.name}: {response.status_code} {response.text}"
        )

    public_url = _build_supabase_public_url(object_key)

    logger.info(
        "Uploaded file to Supabase: object_key=%s public_url_created=true",
        object_key,
    )

    return StorageUploadResult(
        provider="supabase",
        bucket=bucket,
        object_key=object_key,
        url=public_url,
        content_type=resolved_content_type,
        expires_in=None,
        is_signed_url=False,
    )


def _upload_generated_clip(
    local_path: Path,
    object_path: str,
    content_type: str | None = None,
) -> StorageUploadResult:
    if S3_BUCKET:
        return _upload_file_to_s3(local_path, object_path, content_type)

    return _upload_file_to_supabase(local_path, object_path, content_type)


# ---------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------

def list_target_assets() -> dict[str, Any]:
    return {"target_assets": get_asset_catalog()}


def generate_from_query(
    *,
    video_id: Optional[str],
    video_url: Optional[str],
    target_assets: Optional[str],
    user_id: int,
) -> dict[str, Any]:
    video_input = video_url or video_id

    if not video_input:
        raise HTTPException(status_code=400, detail="Provide either video_id or video_url.")

    try:
        selected_target_assets = normalize_target_assets(
            target_assets.split(",") if target_assets else None
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    ensure_can_generate_assets(user_id, len(selected_target_assets))

    result = _generate_from_video(
        video_input,
        user_id,
        selected_target_assets,
    )

    record_generated_assets(user_id, len(result.get("results", [])))
    return result


def generate_from_request(
    *,
    request: GenerateContentRequest,
    user_id: int,
    uploaded_video: Optional[UploadFile] = None,
) -> dict[str, Any]:
    video_input = request.video_url or request.video_id
    transcript = request.transcript.strip()

    if not video_input and not transcript and not uploaded_video:
        raise HTTPException(
            status_code=400,
            detail="Provide a YouTube video URL/ID, paste a transcript, or upload a video.",
        )

    try:
        selected_target_assets = normalize_target_assets(request.target_assets)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    ensure_can_generate_assets(user_id, len(selected_target_assets))

    if transcript:
        result = _generate_from_transcript(
            transcript,
            user_id,
            selected_target_assets,
            uploaded_video_path=request.uploaded_video_path,
            transcription_bundle=request.transcription_bundle,
        )
        record_generated_assets(user_id, len(result.get("results", [])))
        return result

    if uploaded_video:
        result = _generate_from_video(
            None,
            user_id,
            selected_target_assets,
            uploaded_video=uploaded_video,
            uploaded_video_path=request.uploaded_video_path,
            transcription_bundle=request.transcription_bundle,
        )
        record_generated_assets(user_id, len(result.get("results", [])))
        return result

    result = _generate_from_video(
        video_input,
        user_id,
        selected_target_assets,
        uploaded_video_path=request.uploaded_video_path,
        transcription_bundle=request.transcription_bundle,
    )

    record_generated_assets(user_id, len(result.get("results", [])))
    return result


def process_uploaded_video(file: UploadFile, user_id: int) -> dict[str, Any]:
    logger.info(
        "Upload-video request received: user_id=%s filename=%s content_type=%s",
        user_id,
        file.filename,
        file.content_type,
    )

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    max_size = 100 * 1024 * 1024

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

        transcript, transcription_path, transcription_bundle, stored_video_path = (
            transcribe_uploaded_video_with_artifacts(file)
        )

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
        logger.exception("Error processing uploaded video: %s", error)
        raise HTTPException(
            status_code=500,
            detail=f"Error processing video: {str(error)}",
        ) from error


def create_generation_job(request: GenerateContentRequest, user_id: int) -> dict[str, Any]:
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

    ensure_can_generate_assets(user_id, len(selected_target_assets))

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

    job = generation_job_store.create_job(user_id, payload)
    progress_callback = _build_progress_callback(job["id"])

    def runner():
        if transcript:
            result = _generate_from_transcript(
                transcript,
                user_id,
                selected_target_assets,
                progress_callback=progress_callback,
                uploaded_video_path=request.uploaded_video_path,
                transcription_bundle=request.transcription_bundle,
            )
            record_generated_assets(user_id, len(result.get("results", [])))
            return result

        result = _generate_from_video(
            video_input,
            user_id,
            selected_target_assets,
            progress_callback=progress_callback,
            uploaded_video_path=request.uploaded_video_path,
            transcription_bundle=request.transcription_bundle,
        )
        record_generated_assets(user_id, len(result.get("results", [])))
        return result

    generation_job_store.start_job(job["id"], runner)
    return job


def get_generation_job(job_id: str, user_id: int) -> dict[str, Any]:
    job = generation_job_store.get_job(job_id, user_id)

    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found.")

    return job


# ---------------------------------------------------------------------
# Progress + pipeline
# ---------------------------------------------------------------------

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
            asset_progress=event.get(
                "asset_progress",
                current_job.get("asset_progress", []) if current_job else [],
            ),
        )

    return emit


def _run_generation_pipeline(
    source_text: str,
    target_assets: list[str],
    user_id: int,
    progress_callback=None,
    skip_text_asset_types: set[str] | None = None,
):
    logger.info(
        "run_generation_pipeline started: user_id=%s target_assets=%s source_chars=%s skip_text_asset_types=%s",
        user_id,
        target_assets,
        len(source_text or ""),
        sorted(skip_text_asset_types) if skip_text_asset_types else [],
    )

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

    logger.info(
        "run_execution_pipeline completed: user_id=%s results=%s",
        user_id,
        len(results),
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


def _build_generated_clip_media(
    run_id: str,
    clip_payload: dict[str, Any],
    video_storage: StorageUploadResult | None = None,
    subtitle_storage: StorageUploadResult | None = None,
) -> dict[str, Any]:
    clip_details = clip_payload.get("clip", {})
    video_path = Path(clip_payload["video_path"])

    media = {
        "kind": "video",
        "label": clip_details.get("title") or "Generated clip",
        "video_path": str(video_path),

        # Frontend should use this directly in <video src={media.video_url} controls />
        "video_url": (
            video_storage.url
            if video_storage
            else f"/generated-clips/{run_id}/clips/{video_path.name}"
        ),

        # Permanent storage fields. Store these in DB later.
        "video_storage_provider": video_storage.provider if video_storage else "local",
        "video_storage_bucket": video_storage.bucket if video_storage else None,
        "video_storage_key": video_storage.object_key if video_storage else None,
        "video_content_type": video_storage.content_type if video_storage else "video/mp4",
        "video_url_is_signed": video_storage.is_signed_url if video_storage else False,
        "video_url_expires_in": video_storage.expires_in if video_storage else None,

        "clip_id": clip_details.get("clip_id"),
        "start": clip_details.get("start"),
        "end": clip_details.get("end"),
        "duration": clip_details.get("duration"),
        "score": clip_details.get("score"),
        "rationale": clip_details.get("rationale"),
        "transcript_text": clip_details.get("transcript_text"),
        "target_asset_type": clip_payload.get("target_asset_type") or clip_details.get("target_asset_type"),
        "platform_profile": clip_payload.get("platform_profile") or clip_details.get("platform_profile"),
    }

    subtitle_path = clip_payload.get("subtitle_path")

    if subtitle_path:
        subtitle_file = Path(subtitle_path)

        media["subtitle_path"] = str(subtitle_file)
        media["subtitle_url"] = (
            subtitle_storage.url
            if subtitle_storage
            else f"/generated-clips/{run_id}/subtitles/{subtitle_file.name}"
        )
        media["subtitle_storage_provider"] = subtitle_storage.provider if subtitle_storage else "local"
        media["subtitle_storage_bucket"] = subtitle_storage.bucket if subtitle_storage else None
        media["subtitle_storage_key"] = subtitle_storage.object_key if subtitle_storage else None
        media["subtitle_content_type"] = subtitle_storage.content_type if subtitle_storage else "text/plain"
        media["subtitle_url_is_signed"] = subtitle_storage.is_signed_url if subtitle_storage else False
        media["subtitle_url_expires_in"] = subtitle_storage.expires_in if subtitle_storage else None

    return media


def _attach_generated_clips_to_results(
    pipeline_result: dict[str, Any],
    uploaded_video_path: str | None,
    transcription_bundle: dict[str, Any] | None,
    target_assets: list[str],
    progress_callback=None,
) -> dict[str, Any]:
    if not uploaded_video_path:
        logger.info("Skipping clip attachment because no uploaded_video_path was provided.")
        return pipeline_result

    requested_short_assets = [
        asset_type for asset_type in target_assets if asset_type in SHORT_VIDEO_ASSET_TYPES
    ]

    if not requested_short_assets:
        logger.info(
            "Skipping clip attachment because no short-video assets were requested. target_assets=%s",
            target_assets,
        )
        return pipeline_result

    if not transcription_bundle:
        logger.warning(
            "Skipping clip attachment because transcription_bundle is missing. uploaded_video_path=%s",
            uploaded_video_path,
        )
        return pipeline_result

    logger.info(
        "Preparing clip attachment: uploaded_video_path=%s requested_short_assets=%s transcription_keys=%s",
        uploaded_video_path,
        requested_short_assets,
        sorted(transcription_bundle.keys()),
    )

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

    logger.info(
        "Starting generate_short_clips_from_groq: source_video_path=%s clip_count=%s output_dir=%s",
        source_video_path,
        len(requested_short_assets),
        str(GENERATED_CLIPS_DIR),
    )

    try:
        clip_result = generate_short_clips_from_groq(
            source_video_path=source_video_path,
            transcription=transcription_bundle,
            clip_count=len(requested_short_assets),
            output_dir=str(GENERATED_CLIPS_DIR),
            create_blur_background=True,
            debug=True,
            target_asset_types=requested_short_assets,
        )
    except Exception:
        logger.exception(
            "Clip rendering failed: source_video_path=%s target_assets=%s",
            source_video_path,
            requested_short_assets,
        )
        raise

    logger.info(
        "Clip rendering finished: run_id=%s selected_clips=%s output_dir=%s",
        clip_result.get("run_id"),
        len(clip_result.get("selected_clips", [])),
        clip_result.get("output_dir"),
    )

    selected_clips = clip_result.get("selected_clips", [])

    if not selected_clips:
        logger.warning("Clip rendering returned no selected_clips; returning original pipeline result.")
        return pipeline_result

    clips_by_asset_type = {
        clip.get("target_asset_type") or clip.get("clip", {}).get("target_asset_type"): clip
        for clip in selected_clips
        if clip.get("target_asset_type") or clip.get("clip", {}).get("target_asset_type")
    }

    run_id = clip_result.get("run_id") or datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")

    results = []
    short_asset_index = 0

    for result in pipeline_result["results"]:
        next_result = dict(result)
        asset_type = result.get("asset_type")

        if asset_type in requested_short_assets:
            clip_payload = clips_by_asset_type.get(asset_type)

            if clip_payload is None and short_asset_index < len(selected_clips):
                clip_payload = selected_clips[short_asset_index]

            if clip_payload is None:
                results.append(next_result)
                continue

            clip_video_path = Path(clip_payload["video_path"])
            clip_subtitle_path = clip_payload.get("subtitle_path")

            try:
                video_storage = _upload_generated_clip(
                    clip_video_path,
                    f"clips/{run_id}/{clip_video_path.name}",
                    "video/mp4",
                )

                subtitle_storage = None

                if clip_subtitle_path:
                    subtitle_file = Path(clip_subtitle_path)
                    subtitle_storage = _upload_generated_clip(
                        subtitle_file,
                        f"subtitles/{run_id}/{subtitle_file.name}",
                        "text/plain",
                    )

            except Exception:
                logger.exception(
                    "Failed to upload rendered clip: clip_path=%s subtitle_path=%s",
                    clip_payload.get("video_path"),
                    clip_subtitle_path,
                )
                raise

            logger.info(
                "Mapping rendered clip to asset_type=%s clip_id=%s platform_profile=%s video_storage_key=%s",
                asset_type,
                clip_payload.get("clip", {}).get("clip_id"),
                clip_payload.get("platform_profile") or clip_payload.get("clip", {}).get("platform_profile"),
                video_storage.object_key,
            )

            output_payload = {
                "generated_clip": {
                    "title": clip_payload.get("clip", {}).get("title"),
                    "start": clip_payload.get("clip", {}).get("start"),
                    "end": clip_payload.get("clip", {}).get("end"),
                    "duration": clip_payload.get("clip", {}).get("duration"),
                    "score": clip_payload.get("clip", {}).get("score"),
                    "rationale": clip_payload.get("clip", {}).get("rationale"),
                    "platform_profile": (
                        clip_payload.get("platform_profile")
                        or clip_payload.get("clip", {}).get("platform_profile")
                    ),
                    "video_storage_key": video_storage.object_key,
                    "video_storage_provider": video_storage.provider,
                    "subtitle_storage_key": subtitle_storage.object_key if subtitle_storage else None,
                }
            }

            next_result["output"] = json.dumps(output_payload)
            next_result["media"] = _build_generated_clip_media(
                run_id,
                clip_payload,
                video_storage=video_storage,
                subtitle_storage=subtitle_storage,
            )

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
        logger.info(
            "_generate_from_video using uploaded video: filename=%s uploaded_video_path=%s transcription_bundle_present=%s",
            getattr(uploaded_video, "filename", None),
            uploaded_video_path,
            bool(transcription_bundle),
        )
        transcript = transcribe_uploaded_video(uploaded_video)
    else:
        logger.info("_generate_from_video using video_input=%s", video_input)
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

    logger.info(
        "_generate_from_transcript completed: user_id=%s target_assets=%s uploaded_video_path=%s transcription_bundle_present=%s",
        user_id,
        target_assets,
        uploaded_video_path,
        bool(transcription_bundle),
    )

    return _attach_generated_clips_to_results(
        pipeline_result,
        uploaded_video_path=uploaded_video_path,
        transcription_bundle=transcription_bundle,
        target_assets=target_assets,
        progress_callback=progress_callback,
    )
