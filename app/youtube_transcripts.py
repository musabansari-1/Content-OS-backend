import os
import tempfile
import logging
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import InvalidVideoId
from yt_dlp import YoutubeDL

from app.utils.llm import client as groq_client


GROQ_TRANSCRIPTION_MODEL = os.getenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
logger = logging.getLogger(__name__)


def transcript_to_text(transcript) -> str:
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


def _download_audio_file(video_id: str, source_url: str) -> Path:
    with tempfile.TemporaryDirectory(prefix="youtube-audio-") as temp_dir:
        output_template = str(Path(temp_dir) / f"{video_id}.%(ext)s")
        options = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(source_url, download=True)
            prepared_path = Path(downloader.prepare_filename(info))

        if prepared_path.exists():
            file_descriptor, temp_path = tempfile.mkstemp(
                prefix=f"{video_id}-",
                suffix=prepared_path.suffix,
            )
            os.close(file_descriptor)
            temp_copy = Path(temp_path)
            temp_copy.write_bytes(prepared_path.read_bytes())
            return temp_copy

        matches = sorted(Path(temp_dir).glob(f"{video_id}.*"))
        if matches:
            matched_path = matches[0]
            file_descriptor, temp_path = tempfile.mkstemp(
                prefix=f"{video_id}-",
                suffix=matched_path.suffix,
            )
            os.close(file_descriptor)
            temp_copy = Path(temp_path)
            temp_copy.write_bytes(matched_path.read_bytes())
            return temp_copy

    raise RuntimeError("yt-dlp finished without producing a downloadable audio file.")


def _transcribe_audio_with_groq(audio_path: Path) -> str:
    try:
        with audio_path.open("rb") as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                model=GROQ_TRANSCRIPTION_MODEL,
                file=audio_file,
                response_format="json",
                temperature=0,
            )
    finally:
        audio_path.unlink(missing_ok=True)

    text = getattr(transcription, "text", None)
    if text is None and isinstance(transcription, dict):
        text = transcription.get("text", "")

    text = str(text or "").strip()
    if not text:
        raise RuntimeError("Groq Whisper returned an empty transcript.")

    return text


def _fetch_transcript_from_youtube_api(video_id: str) -> str:
    ytt_api = YouTubeTranscriptApi()
    transcript = ytt_api.fetch(video_id)
    return transcript_to_text(transcript)


def _fetch_transcript_with_audio_fallback(video_id: str) -> str:
    source_url = f"https://www.youtube.com/watch?v={video_id}"
    audio_path = _download_audio_file(video_id, source_url)
    return _transcribe_audio_with_groq(audio_path)


def fetch_video_transcript(video_input: str) -> str:
    video_id = resolve_youtube_video_id(video_input)
    primary_error: Exception | None = None

    try:
        return _fetch_transcript_from_youtube_api(video_id)
    except InvalidVideoId as error:
        raise HTTPException(status_code=400, detail=f"Invalid YouTube video ID: {video_id}") from error
    except HTTPException:
        raise
    except Exception as error:
        primary_error = error
        logger.warning(
            "YouTube transcript API failed for %s; trying audio fallback.",
            video_id,
            exc_info=True,
        )

    try:
        return _fetch_transcript_with_audio_fallback(video_id)
    except Exception as fallback_error:
        detail = (
            f"Unable to get a transcript for YouTube video {video_id}. "
            f"Transcript API error: {primary_error}. "
            f"Audio transcription fallback error: {fallback_error}"
        )
        logger.exception("YouTube transcript fallback failed for %s.", video_id)
        raise HTTPException(status_code=502, detail=detail) from fallback_error


def fetch_video_transcripts(video_inputs: Iterable[str]) -> list[str]:
    return [fetch_video_transcript(video_input) for video_input in video_inputs]
