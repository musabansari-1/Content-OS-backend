import os
import json
import tempfile
import logging
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastapi import HTTPException
from yt_dlp import YoutubeDL

from app.utils.llm import client as groq_client


GROQ_TRANSCRIPTION_MODEL = os.getenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
YOUTUBE_TRANSCRIPT_API_URL = os.getenv(
    "YOUTUBE_TRANSCRIPT_API_URL",
    "https://youtube-transcript-api-tau-one.vercel.app/transcript",
)
logger = logging.getLogger(__name__)


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

        cookie_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
        if cookie_file:
            options["cookiefile"] = cookie_file

        cookies_from_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
        if cookies_from_browser:
            browser_parts = tuple(
                part.strip() for part in cookies_from_browser.split(",") if part.strip()
            )
            if browser_parts:
                options["cookiesfrombrowser"] = browser_parts

        user_agent = os.getenv("YTDLP_USER_AGENT", "").strip()
        if user_agent:
            options["http_headers"] = {"User-Agent": user_agent}

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


def _normalize_remote_transcript_response(payload) -> str:
    if isinstance(payload, str):
        return payload.strip()

    if isinstance(payload, dict):
        for key in ("transcript", "text", "caption", "captions"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                text = " ".join(
                    str(item.get("text", "")).strip()
                    for item in value
                    if isinstance(item, dict)
                ).strip()
                if text:
                    return text

    if isinstance(payload, list):
        text = " ".join(
            str(item.get("text", "")).strip()
            for item in payload
            if isinstance(item, dict)
        ).strip()
        if text:
            return text

    return ""


def _fetch_transcript_from_hosted_api(video_input: str) -> str:
    video_id = resolve_youtube_video_id(video_input)
    request_variants = [
        {"video_url": video_input},
        {"video": video_input},
        {"video_url": video_id},
        {"video": video_id},
    ]

    last_error: Exception | None = None
    for payload in request_variants:
        request = Request(
            YOUTUBE_TRANSCRIPT_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=90) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as error:
            last_error = error
            if error.code == 422:
                continue
            raise RuntimeError(
                f"Hosted YouTube transcript API returned HTTP {error.code}."
            ) from error

        try:
            data = json.loads(response_body)
        except json.JSONDecodeError as error:
            last_error = error
            continue

        transcript_text = _normalize_remote_transcript_response(data)
        if transcript_text:
            return transcript_text

        last_error = RuntimeError("Hosted YouTube transcript API returned an empty transcript.")

    raise RuntimeError(
        f"Hosted YouTube transcript API could not process the request. Last error: {last_error}"
    )


def _fetch_transcript_with_audio_fallback(video_id: str) -> str:
    source_url = f"https://www.youtube.com/watch?v={video_id}"
    audio_path = _download_audio_file(video_id, source_url)
    return _transcribe_audio_with_groq(audio_path)


def fetch_video_transcript(video_input: str) -> str:
    return fetch_video_transcript_with_fallback(video_input)


def fetch_video_transcript_with_fallback(video_input: str) -> str:
    video_id = resolve_youtube_video_id(video_input)
    source_url = f"https://www.youtube.com/watch?v={video_id}"
    primary_error: Exception | None = None

    try:
        return _fetch_transcript_from_hosted_api(source_url)
    except HTTPException:
        raise
    except Exception as error:
        primary_error = error
        logger.warning(
            "Hosted YouTube transcript API failed for %s; trying audio fallback.",
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
    return [fetch_video_transcript_with_fallback(video_input) for video_input in video_inputs]


def fetch_video_transcript_from_api(video_input: str) -> str:
    video_id = resolve_youtube_video_id(video_input)
    source_url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        return _fetch_transcript_from_hosted_api(source_url)
    except Exception as error:
        detail = (
            f"Unable to get a transcript for YouTube video {video_id} from the hosted transcript API. "
            f"Transcript API error: {error}"
        )
        raise HTTPException(status_code=502, detail=detail) from error


def fetch_video_transcripts_from_api(video_inputs: Iterable[str]) -> list[str]:
    return [fetch_video_transcript_from_api(video_input) for video_input in video_inputs]
