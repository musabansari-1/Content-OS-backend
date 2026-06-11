from fastapi import APIRouter

from app.services.system_service import (
    check_ffmpeg as check_ffmpeg_service,
    check_ffprobe as check_ffprobe_service,
)


router = APIRouter()


@router.get("/check")
def check():
    return check_ffmpeg_service()


@router.get("/check-ffprobe")
def check_ffprobe():
    return check_ffprobe_service()
