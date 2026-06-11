from typing import Optional

from fastapi import APIRouter, Depends, UploadFile

from app.auth.dependencies import require_current_user
from app.auth.types import UserResponse
from app.services.generation_service import (
    GENERATED_CLIPS_DIR,
    create_generation_job as create_generation_job_service,
    generate_from_query as generate_from_query_service,
    generate_from_request as generate_from_request_service,
    get_generation_job as get_generation_job_service,
    list_target_assets as list_target_assets_service,
    process_uploaded_video as process_uploaded_video_service,
)
from app.voice_engine.types import GenerateContentRequest


router = APIRouter()

@router.get("/generate")
def generate(
    video_id: Optional[str] = None,
    video_url: Optional[str] = None,
    target_assets: Optional[str] = None,
    current_user: UserResponse = Depends(require_current_user),
):
    return generate_from_query_service(
        video_id=video_id,
        video_url=video_url,
        target_assets=target_assets,
        user_id=current_user.id,
    )


@router.post("/generate-from-video")
def generate_from_video(
    request: GenerateContentRequest,
    uploaded_video: Optional[UploadFile] = None,
    current_user: UserResponse = Depends(require_current_user),
):
    return generate_from_request_service(
        request=request,
        user_id=current_user.id,
        uploaded_video=uploaded_video,
    )


@router.post("/upload-video")
async def upload_video(
    file: UploadFile,
    current_user: UserResponse = Depends(require_current_user),
):
    return process_uploaded_video_service(file, current_user.id)


@router.post("/generation-jobs")
def create_generation_job(
    request: GenerateContentRequest,
    current_user: UserResponse = Depends(require_current_user),
):
    return create_generation_job_service(request, current_user.id)


@router.get("/generation-jobs/{job_id}")
def get_generation_job(
    job_id: str,
    current_user: UserResponse = Depends(require_current_user),
):
    return get_generation_job_service(job_id, current_user.id)


@router.get("/target-assets")
def get_target_assets():
    return list_target_assets_service()
