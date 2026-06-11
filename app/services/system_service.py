import subprocess


def check_ffmpeg() -> dict[str, str]:
    ffmpeg = subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]
    return {"ffmpeg": ffmpeg}


def check_ffprobe() -> dict[str, str]:
    out = subprocess.check_output(["ffprobe", "-version"], text=True)
    return {"ffprobe": out.splitlines()[0]}
