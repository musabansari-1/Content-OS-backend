# tools/youtube.py

from youtube_transcript_api import YouTubeTranscriptApi

def get_transcript(video_id: str):
    transcript = YouTubeTranscriptApi.get_transcript(video_id)
    return " ".join([t["text"] for t in transcript])