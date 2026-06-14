from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator


INVALID_DEVICE_TERMS = {
    "youtube",
    "twitter",
    "x",
    "linkedin",
    "instagram",
    "tiktok",
    "facebook",
    "threads",
    "newsletter",
    "email",
    "blog",
    "podcast",
    "discord",
    "reddit",
    "telegram",
    "whatsapp",
    "video platform",
    "social media",
    "platform",
    "channel",
}


def _normalize_preferred_devices(values: List[str]) -> List[str]:
    normalized = []

    for raw_value in values or []:
        value = (raw_value or "").strip()
        if not value:
            continue

        lowered = value.lower()

        if any(term in lowered for term in INVALID_DEVICE_TERMS):
            continue

        normalized.append(value)

    return list(dict.fromkeys(normalized))


class VoiceProfile(BaseModel):
    sample_count: int = 0
    field_confidence: Dict[str, float] = Field(default_factory=dict)
    evidence: Dict[str, List[str]] = Field(default_factory=dict)
    tone: List[str] = Field(default_factory=list)
    sentence_rhythm: str = ""
    hook_style: List[str] = Field(default_factory=list)
    cta_style: List[str] = Field(default_factory=list)
    humor_style: str = ""
    emotional_intensity: str = ""
    emoji_usage: str = ""
    punctuation_style: str = ""
    preferred_devices: List[str] = Field(default_factory=list)
    banned_phrases: List[str] = Field(default_factory=list)
    preferred_phrases: List[str] = Field(default_factory=list)
    narrative_behavior: "NarrativeBehavior" = Field(default_factory=lambda: NarrativeBehavior())
    cognitive_style: "CognitiveStyle" = Field(default_factory=lambda: CognitiveStyle())
    constraint_profile: "ConstraintProfile" = Field(default_factory=lambda: ConstraintProfile())
    voice_anchors: List[str] = Field(default_factory=list)
    style_summary: str = ""

    @validator("sample_count", pre=True, always=True)
    def validate_sample_count(cls, value):
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0

    @validator("field_confidence", pre=True, always=True)
    def validate_field_confidence(cls, value):
        if not isinstance(value, dict):
            return {}

        normalized = {}
        for key, raw_value in value.items():
            try:
                normalized[str(key)] = min(max(float(raw_value), 0.0), 1.0)
            except (TypeError, ValueError):
                continue

        return normalized

    @validator("evidence", pre=True, always=True)
    def validate_evidence(cls, value):
        if not isinstance(value, dict):
            return {}

        normalized = {}
        for key, raw_values in value.items():
            if isinstance(raw_values, str):
                raw_values = [raw_values]

            if not isinstance(raw_values, list):
                continue

            cleaned = []
            for raw_value in raw_values:
                text = (raw_value or "").strip()
                if text:
                    cleaned.append(text)

            if cleaned:
                normalized[str(key)] = cleaned

        return normalized

    @validator("preferred_devices", pre=True, always=True)
    def validate_preferred_devices(cls, value):
        if value is None:
            return []

        if isinstance(value, str):
            value = [value]

        return _normalize_preferred_devices(value)


class NarrativeBehavior(BaseModel):
    opening_pattern: str = ""
    idea_progression: List[str] = Field(default_factory=list)
    tension_pattern: str = ""
    teaching_pattern: str = ""
    authority_pattern: str = ""
    closing_pattern: str = ""


class CognitiveStyle(BaseModel):
    reasoning_style: List[str] = Field(default_factory=list)
    decision_lens: List[str] = Field(default_factory=list)
    abstraction_pattern: str = ""
    problem_solving_style: str = ""
    common_reframes: List[str] = Field(default_factory=list)


class ConstraintProfile(BaseModel):
    avoids: List[str] = Field(default_factory=list)
    never_does: List[str] = Field(default_factory=list)
    overuse_risks: List[str] = Field(default_factory=list)


class CreatorVoiceProfileRecord(BaseModel):
    id: int
    user_id: Optional[int] = None
    voice_profile_json: VoiceProfile
    style_summary: str
    version: int
    created_at: datetime
    updated_at: datetime


class SaveMyVoiceProfileRequest(BaseModel):
    samples: List[str] = Field(default_factory=list)


class SaveMyVoiceProfileFromYoutubeRequest(BaseModel):
    youtube_video_ids: List[str] = Field(default_factory=list)
    youtube_urls: List[str] = Field(default_factory=list)
    transcripts: List[str] = Field(default_factory=list)


class GenerateContentRequest(BaseModel):
    video_id: Optional[str] = None
    video_url: Optional[str] = None
    transcript: str = ""
    target_assets: List[str] = Field(default_factory=list)
    uploaded_video_filename: Optional[str] = None
    uploaded_video_content_type: Optional[str] = None
    uploaded_video_path: Optional[str] = None
    uploaded_video_url: Optional[str] = None
    transcription_bundle: Dict[str, Any] = Field(default_factory=dict)


VoiceProfile.update_forward_refs()
