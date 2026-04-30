import re
from typing import Any, Dict, List


def clean_segment_text(text: str) -> str:
    """
    Light cleanup for timestamped transcript segments.
    Keeps text close to source while removing formatting noise.
    Safe for subtitles / timestamps / clipping.
    """
    if not text:
        return ""

    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)                 # collapse repeated whitespace
    text = re.sub(r"\s+([,.;!?])", r"\1", text)      # remove spaces before punctuation
    return text.strip()


def clean_full_transcript(text: str) -> str:
    """
    Stronger cleanup for flattened transcript used by LLMs.
    Optimized for readability and downstream prompting.
    """
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;!?])", r"\1", text)      # remove spaces before punctuation
    text = re.sub(r"([,.;!?]){2,}", r"\1", text)     # collapse repeated punctuation
    return text.strip()


def normalize_transcript(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize transcript API response into internal transcript format.

    Input format:
    {
        "video_id": "...",
        "language": "English",
        "language_code": "en",
        "is_generated": false,
        "snippets": [
            {
                "text": "...",
                "start": 0.229,
                "duration": 7.751
            }
        ]
    }

    Output format:
    {
        "video_id": "...",
        "language": "en",
        "source": "transcript_api_v2",
        "is_generated": False,
        "full_text": "...",
        "segments": [
            {
                "text": "...",
                "start": 0.229,
                "duration": 7.751
            }
        ]
    }
    """
    snippets: List[Dict[str, Any]] = payload.get("snippets", [])
    segments: List[Dict[str, Any]] = []

    for snippet in snippets:
        cleaned_text = clean_segment_text(snippet.get("text", ""))

        if not cleaned_text:
            continue

        segments.append({
            "text": cleaned_text,
            "start": float(snippet.get("start", 0.0)),
            "duration": float(snippet.get("duration", 0.0)),
        })

    full_text = " ".join(segment["text"] for segment in segments)
    full_text = clean_full_transcript(full_text)

    is_generated = payload.get("is_generated", False)
    if isinstance(is_generated, str):
        is_generated = is_generated.strip().lower() in {"true", "1", "yes", "y"}

    return {
        "video_id": payload.get("video_id"),
        "language": payload.get("language_code") or payload.get("language"),
        "source": payload.get("source", "transcript_api_v2"),
        "is_generated": is_generated,
        "full_text": full_text,
        "segments": segments,
    }

