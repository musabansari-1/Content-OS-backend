import subprocess

from fastapi import APIRouter


router = APIRouter()


@router.get("/check")
def check():
    ffmpeg = subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]
    return {"ffmpeg": ffmpeg}


@router.get("/check-ffprobe")
def check_ffprobe():
    out = subprocess.check_output(["ffprobe", "-version"], text=True)
    return {"ffprobe": out.splitlines()[0]}
