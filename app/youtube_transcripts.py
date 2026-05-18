import json
import logging
import os
import re
import tempfile
import time
from uuid import uuid4
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from fastapi import HTTPException, UploadFile
from openai import OpenAI
from yt_dlp import YoutubeDL

from app.transcript_cache_repository import TranscriptCacheRepository
from app.utils.transcript_conversion import normalize_transcript


try:
    from supadata import Supadata
except ImportError:
    Supadata = None


GROQ_TRANSCRIPTION_MODEL = os.getenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip()
SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY", "").strip()
SUPADATA_MODE = os.getenv("SUPADATA_MODE", "native")  # 'native', 'auto', or 'generate'
SUPADATA_JOB_POLL_INTERVAL = 2  # seconds
SUPADATA_JOB_POLL_TIMEOUT = 300  # 5 minutes
logger = logging.getLogger(__name__)
transcript_cache_repository = TranscriptCacheRepository()
UPLOADED_VIDEO_STORE_DIR = Path(tempfile.gettempdir()) / "contentos-uploaded-videos"
UPLOADED_VIDEO_STORE_DIR.mkdir(parents=True, exist_ok=True)
UPLOADED_TRANSCRIPTION_STORE_DIR = Path(tempfile.gettempdir()) / "contentos-uploaded-transcriptions"
UPLOADED_TRANSCRIPTION_STORE_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPT_FALLBACK_MESSAGE = (
    "We were unable to fetch the transcript for this video. Please paste the transcript manually."
)
_GROQ_TRANSCRIPTION_RETRYABLE_TYPES = {
    "APIConnectionError",
    "APITimeoutError",
    "ReadTimeout",
    "RemoteProtocolError",
    "TimeoutError",
}
_groq_transcription_client: OpenAI | None = None


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()


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


def _get_groq_transcription_client() -> OpenAI:
    global _groq_transcription_client

    if _groq_transcription_client is not None:
        return _groq_transcription_client

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to backend/.env or your environment."
        )

    _groq_transcription_client = OpenAI(
        api_key=api_key,
        base_url=GROQ_BASE_URL or "https://api.groq.com/openai/v1",
    )
    return _groq_transcription_client


def _is_retryable_transcription_error(error: Exception) -> bool:
    error_name = type(error).__name__
    if error_name in _GROQ_TRANSCRIPTION_RETRYABLE_TYPES:
        return True

    message = str(error).lower()
    retryable_markers = (
        "incomplete chunked read",
        "peer closed connection",
        "connection reset",
        "connection aborted",
        "temporary failure",
        "timed out",
        "timeout",
        "read error",
    )
    return any(marker in message for marker in retryable_markers)


def _transcribe_media_with_groq_bundle_once(media_path: Path) -> dict[str, Any]:
    transcription_client = _get_groq_transcription_client()
    with media_path.open("rb") as media_file:
        transcription = transcription_client.audio.transcriptions.create(
            model=GROQ_TRANSCRIPTION_MODEL,
            file=media_file,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
            temperature=0,
        )

    if hasattr(transcription, "model_dump"):
        data = transcription.model_dump()
    elif isinstance(transcription, dict):
        data = transcription
    else:
        data = {
            "text": getattr(transcription, "text", ""),
            "segments": getattr(transcription, "segments", []),
            "words": getattr(transcription, "words", []),
        }

    text = str(data.get("text") or "").strip()
    if not text:
        raise RuntimeError("Groq Whisper returned an empty transcript.")

    segments = []
    for seg in data.get("segments", []) or []:
        if isinstance(seg, dict):
            segments.append(
                {
                    "id": seg.get("id"),
                    "start": float(seg.get("start", 0) or 0),
                    "end": float(seg.get("end", 0) or 0),
                    "text": str(seg.get("text", "")).strip(),
                }
            )
        else:
            segments.append(
                {
                    "id": getattr(seg, "id", None),
                    "start": float(getattr(seg, "start", 0) or 0),
                    "end": float(getattr(seg, "end", 0) or 0),
                    "text": str(getattr(seg, "text", "")).strip(),
                }
            )

    words = []
    for word in data.get("words", []) or []:
        if isinstance(word, dict):
            token = str(word.get("word", "")).strip()
            if token:
                words.append(
                    {
                        "word": token,
                        "start": float(word.get("start", 0) or 0),
                        "end": float(word.get("end", 0) or 0),
                    }
                )
        else:
            token = str(getattr(word, "word", "")).strip()
            if token:
                words.append(
                    {
                        "word": token,
                        "start": float(getattr(word, "start", 0) or 0),
                        "end": float(getattr(word, "end", 0) or 0),
                    }
                )

    return {
        "text": text,
        "segments": segments,
        "words": words,
        "raw": data,
    }


def _transcribe_media_with_groq_bundle(media_path: Path) -> dict[str, Any]:
    last_error: Exception | None = None
    attempts = 3

    for attempt in range(1, attempts + 1):
        try:
            return _transcribe_media_with_groq_bundle_once(media_path)
        except Exception as error:
            last_error = error
            if attempt < attempts and _is_retryable_transcription_error(error):
                delay_seconds = float(attempt)
                logger.warning(
                    "Groq transcription attempt %s/%s failed for %s with a retryable error; retrying in %.1fs.",
                    attempt,
                    attempts,
                    media_path.name,
                    delay_seconds,
                    exc_info=True,
                )
                time.sleep(delay_seconds)
                continue
            break

    raise RuntimeError(f"Groq transcription failed: {last_error}") from last_error


def _save_groq_transcription_bundle(bundle: dict[str, Any], source_path: Path) -> Path:
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_path.stem or "upload")
    output_path = UPLOADED_TRANSCRIPTION_STORE_DIR / f"{uuid4().hex}_{safe_stem}.json"
    output_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def _transcribe_media_with_groq(media_path: Path) -> str:
    bundle = _transcribe_media_with_groq_bundle(media_path)
    return bundle["text"]


def _transcribe_audio_with_groq_bundle(audio_path: Path) -> dict[str, Any]:
    try:
        return _transcribe_media_with_groq_bundle(audio_path)
    finally:
        audio_path.unlink(missing_ok=True)


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
        return _transcribe_media_with_groq(audio_path)
    finally:
        audio_path.unlink(missing_ok=True)


def fetch_video_transcript(video_input: str) -> str:
    return fetch_video_transcript_bundle(video_input)["full_text"]


def fetch_video_transcript_with_fallback(video_input: str) -> str:
    return fetch_video_transcript(video_input)


def transcribe_uploaded_video(video_file: UploadFile) -> str:
    text, _, _, _ = transcribe_uploaded_video_with_artifacts(video_file)
    return text


def transcribe_uploaded_video_with_artifacts(video_file: UploadFile) -> tuple[str, Path, dict[str, Any], Path]:
    """
    Transcribe an uploaded video file using Groq Whisper.
    The uploaded video is first sent directly to Whisper. If that fails, we
    fall back to extracting audio from the local file and retrying.
    """
    # Validate file type
    content_type = video_file.content_type or ""
    if not content_type.startswith("video/"):
        logger.warning(
            "Uploaded video rejected: filename=%s content_type=%s",
            video_file.filename,
            content_type,
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Please upload a video file (MP4, MOV, AVI, etc.).",
        )

    logger.info(
        "Uploaded video transcription started: filename=%s content_type=%s",
        video_file.filename,
        content_type,
    )
    stored_path = _store_uploaded_video(video_file)
    logger.info("Uploaded video stored at %s", stored_path)

    try:
        logger.info("Trying direct Whisper transcription for uploaded video: %s", stored_path.name)
        bundle = _transcribe_media_with_groq_bundle(stored_path)
        saved_bundle_path = _save_groq_transcription_bundle(bundle, stored_path)
        logger.info("Saved Groq transcription bundle to %s", saved_bundle_path)
        return bundle["text"], saved_bundle_path, bundle, stored_path
    except Exception as direct_error:
        logger.warning(
            "Direct transcription of uploaded video failed for %s; trying audio extraction fallback.",
            stored_path.name,
            exc_info=True,
        )
        try:
            logger.info("Trying audio extraction fallback for uploaded video: %s", stored_path.name)
            audio_path = _extract_audio_from_video(stored_path, stored_path.parent)
            logger.info("Audio extracted for uploaded video: %s", audio_path)
            bundle = _transcribe_audio_with_groq_bundle(audio_path)
            bundle["source"] = "audio_fallback"
            saved_bundle_path = _save_groq_transcription_bundle(bundle, stored_path)
            logger.info("Saved fallback transcription bundle to %s", saved_bundle_path)
            return bundle["text"], saved_bundle_path, bundle, stored_path
        except Exception as fallback_error:
            logger.exception(
                "Uploaded video transcription failed after both paths: stored_path=%s",
                stored_path,
            )
            raise RuntimeError(
                f"Could not transcribe uploaded video. Direct transcription error: {direct_error}. "
                f"Audio extraction error: {fallback_error}"
            ) from fallback_error


def _store_uploaded_video(video_file: UploadFile) -> Path:
    safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(video_file.filename or "upload.mp4").name)
    stored_name = f"{uuid4().hex}_{safe_filename}"
    stored_path = UPLOADED_VIDEO_STORE_DIR / stored_name
    logger.info("Persisting uploaded video as %s", stored_path.name)
    video_content = video_file.file.read()
    stored_path.write_bytes(video_content)
    return stored_path


def resolve_uploaded_video_path(stored_path: str) -> Path:
    candidate = Path(stored_path).expanduser().resolve(strict=False)
    store_root = UPLOADED_VIDEO_STORE_DIR.resolve(strict=False)

    try:
        candidate.relative_to(store_root)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid uploaded video reference.") from error

    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Uploaded video file not found.")

    return candidate


def _extract_audio_from_video(video_path: Path, temp_dir: Path) -> Path:
    """
    Extract audio from a local uploaded video file using yt-dlp.
    Returns the path to the extracted audio file.
    """
    output_template = str(temp_dir / "audio.%(ext)s")
    source_url = video_path.resolve().as_uri()

    options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "enable_file_urls": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        logger.info("Starting yt-dlp audio extraction for uploaded file: %s", video_path)
        with YoutubeDL(options) as ydl:
            ydl.extract_info(source_url, download=False)
            ydl.download([source_url])

            # Find the extracted audio file
            for file_path in temp_dir.glob("audio.*"):
                if file_path.exists():
                    # Create a copy in a temp file for processing
                    file_descriptor, temp_copy_path = tempfile.mkstemp(
                        prefix="audio-",
                        suffix=file_path.suffix,
                    )
                    os.close(file_descriptor)
                    temp_copy = Path(temp_copy_path)
                    temp_copy.write_bytes(file_path.read_bytes())
                    logger.info("yt-dlp audio extraction succeeded: source=%s output=%s", video_path, temp_copy)
                    return temp_copy

        raise RuntimeError("Failed to extract audio from video")
    except Exception as error:
        logger.exception("yt-dlp audio extraction failed for uploaded file: %s", video_path)
        raise RuntimeError(f"Error extracting audio from video: {error}") from error


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
