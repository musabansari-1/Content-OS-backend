# tools/youtube.py

from app.youtube_transcripts import fetch_video_transcript


def get_transcript(video_input: str):
    return fetch_video_transcript(video_input)
