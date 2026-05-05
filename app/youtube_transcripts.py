import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from fastapi import HTTPException
from yt_dlp import YoutubeDL

from app.transcript_cache_repository import TranscriptCacheRepository
from app.utils.llm import client as groq_client
from app.utils.transcript_conversion import normalize_transcript


try:
    from supadata import Supadata
except ImportError:
    Supadata = None


GROQ_TRANSCRIPTION_MODEL = os.getenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY", "").strip()
SUPADATA_MODE = os.getenv("SUPADATA_MODE", "native")  # 'native', 'auto', or 'generate'
SUPADATA_JOB_POLL_INTERVAL = 2  # seconds
SUPADATA_JOB_POLL_TIMEOUT = 300  # 5 minutes
logger = logging.getLogger(__name__)
transcript_cache_repository = TranscriptCacheRepository()
TRANSCRIPT_FALLBACK_MESSAGE = (
    "We were unable to fetch the transcript for this video. Please paste the transcript manually."
)


def clean_transcript(text):
    """Remove excess whitespace from transcript text to reduce token usage."""
    return re.sub(r'\s+', ' ', text).strip()


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


def _build_ytdlp_option_candidates(output_template: str) -> list[dict]:
    base_options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    user_agent = os.getenv("YTDLP_USER_AGENT", "").strip()
    if user_agent:
        base_options["http_headers"] = {"User-Agent": user_agent}

    candidates: list[dict] = []

    cookie_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    if cookie_file:
        cookie_file_path = Path(cookie_file).expanduser()
        if cookie_file_path.exists():
            candidates.append({**base_options, "cookiefile": str(cookie_file_path)})
        else:
            logger.warning("YTDLP_COOKIES_FILE was set but the file does not exist: %s", cookie_file)

    cookies_from_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if cookies_from_browser:
        browser_parts = tuple(
            part.strip() for part in cookies_from_browser.split(",") if part.strip()
        )
        if browser_parts:
            candidates.append({**base_options, "cookiesfrombrowser": browser_parts})

    candidates.append(base_options)
    return candidates


def _get_supadata_client() -> Supadata:
    """Initialize and return Supadata client"""
    if not Supadata:
        raise RuntimeError(
            "Supadata package is not installed. Install it with: pip install supadata"
        )
    
    if not SUPADATA_API_KEY:
        raise RuntimeError(
            "SUPADATA_API_KEY is not set. Add it to backend/.env or your environment."
        )
    
    return Supadata(api_key=SUPADATA_API_KEY)


def _poll_supadata_job(supadata: Supadata, job_id: str) -> str:
    """Poll Supadata job status until completion"""
    start_time = time.time()
    
    while time.time() - start_time < SUPADATA_JOB_POLL_TIMEOUT:
        try:
            result = supadata.transcript.get_job_status(job_id)
            
            if result.status == "completed":
                content = result.content if hasattr(result, 'content') else str(result)
                return content
            elif result.status == "failed":
                raise RuntimeError(f"Supadata job failed: {result}")
            elif result.status in ["pending", "processing"]:
                logger.debug("Supadata job %s status: %s, waiting...", job_id, result.status)
                time.sleep(SUPADATA_JOB_POLL_INTERVAL)
            else:
                logger.warning("Supadata job %s returned unknown status: %s", job_id, result.status)
                time.sleep(SUPADATA_JOB_POLL_INTERVAL)
        except Exception as error:
            logger.warning("Error polling Supadata job %s: %s", job_id, error, exc_info=True)
            raise RuntimeError(f"Error polling Supadata job status: {error}") from error
    
    raise RuntimeError(f"Supadata job {job_id} polling timeout after {SUPADATA_JOB_POLL_TIMEOUT}s")


def _fetch_transcript_from_supadata(video_input: str) -> dict:
    """Fetch transcript from Supadata API with job polling if async"""
    video_id = resolve_youtube_video_id(video_input)
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    
    try:
        supadata = _get_supadata_client()
        
        # Request transcript from Supadata
        transcript_result = supadata.transcript(
            url=video_url,
            text=True,
            mode=SUPADATA_MODE
        )
        
        print("Supadata transcript result: ", transcript_result, sep="\n")
        
        # Check if result has job_id (async) or direct content
        if hasattr(transcript_result, 'job_id'):
            logger.info("Supadata transcript request is async, polling job %s", transcript_result.job_id)
            # Poll job status until completion or timeout
            full_text = _poll_supadata_job(supadata, transcript_result.job_id)
        else:
            # Direct result - extract content
            full_text = transcript_result.content if hasattr(transcript_result, 'content') else str(transcript_result)
        
        if not full_text or not str(full_text).strip():
            raise RuntimeError("Supadata returned an empty transcript.")
        
        # Clean excess whitespace to reduce token usage
        cleaned_text = clean_transcript(str(full_text))
        print("Cleaned transcript text: ", cleaned_text, sep="\n")
        
        return {
            "video_id": video_id,
            "video_url": video_url,
            "language": "en",
            "source": "supadata",
            "is_generated": SUPADATA_MODE in ["auto", "generate"],
            "full_text": cleaned_text,
            "segments": [],
        }
    except Exception as error:
        logger.warning(
            "Supadata transcript request failed for %s.",
            video_id,
            exc_info=True,
        )
        raise RuntimeError(f"Supadata could not process the request. Last error: {error}") from error


def _bundle_from_whisper(video_id: str) -> dict:
    source_url = f"https://www.youtube.com/watch?v={video_id}"
    audio_path = _download_audio_file(video_id, source_url)
    full_text = _transcribe_audio_with_groq(audio_path)
    return {
        "video_id": video_id,
        "video_url": source_url,
        "language": "en",
        "source": "whisper_fallback",
        "is_generated": True,
        "full_text": full_text,
        "segments": [],
    }


def _download_audio_file(video_id: str, source_url: str) -> Path:
    with tempfile.TemporaryDirectory(prefix="youtube-audio-") as temp_dir:
        output_template = str(Path(temp_dir) / f"{video_id}.%(ext)s")
        last_error: Exception | None = None

        for options in _build_ytdlp_option_candidates(output_template):
            try:
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
            except Exception as error:
                last_error = error
                error_text = str(error).lower()
                if "cookies database" in error_text or "cookies-from-browser" in error_text:
                    logger.warning(
                        "yt-dlp browser cookie lookup failed for %s; trying next option.",
                        video_id,
                        exc_info=True,
                    )
                    continue

                logger.warning(
                    "yt-dlp audio download failed for %s with a candidate option; trying next option.",
                    video_id,
                    exc_info=True,
                )
                continue

    raise RuntimeError(f"yt-dlp finished without producing a downloadable audio file. Last error: {last_error}")


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


def fetch_video_transcript(video_input: str) -> str:
    return fetch_video_transcript_bundle(video_input)["full_text"]


def fetch_video_transcript_with_fallback(video_input: str) -> str:
    return fetch_video_transcript(video_input)


def fetch_video_transcripts(video_inputs: Iterable[str]) -> list[str]:
    return [fetch_video_transcript_with_fallback(video_input) for video_input in video_inputs]


def _raise_transcript_fetch_error(video_id: str, primary_error: Exception, fallback_error: Exception) -> None:
    logger.exception(
        "Transcript fetch failed for %s. Primary error: %s. Fallback error: %s",
        video_id,
        primary_error,
        fallback_error,
    )
    raise HTTPException(status_code=502, detail=TRANSCRIPT_FALLBACK_MESSAGE) from fallback_error


def fetch_video_transcript_bundle(video_input: str) -> dict:
    video_id = resolve_youtube_video_id(video_input)
    cached_bundle = transcript_cache_repository.get_by_video_id(video_id)
    if cached_bundle:
        return cached_bundle

    primary_error: Exception | None = None

    try:
        transcript_bundle = _fetch_transcript_from_supadata(video_input)
    except Exception as error:
        primary_error = error
        logger.warning(
            "Supadata transcript API failed for %s; trying audio fallback.",
            video_id,
            exc_info=True,
        )

        try:
            transcript_bundle = _bundle_from_whisper(video_id)
        except Exception as fallback_error:
            _raise_transcript_fetch_error(video_id, primary_error, fallback_error)

    transcript_bundle["video_id"] = transcript_bundle.get("video_id") or video_id
    transcript_bundle["video_url"] = transcript_bundle.get("video_url") or f"https://www.youtube.com/watch?v={video_id}"
    transcript_bundle["full_text"] = transcript_bundle.get("full_text", "").strip()
    transcript_bundle["segments"] = transcript_bundle.get("segments", [])
    transcript_bundle.setdefault("language", "en")
    transcript_bundle.setdefault("source", "supadata")
    transcript_bundle.setdefault("is_generated", False)

    return transcript_cache_repository.upsert(transcript_bundle)
