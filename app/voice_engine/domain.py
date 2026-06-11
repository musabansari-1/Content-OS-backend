from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.voice_engine.types import VoiceProfile


@dataclass(frozen=True)
class VoiceProfileRecord:
    id: int
    user_id: Optional[int]
    voice_profile: VoiceProfile
    style_summary: str
    version: int
    created_at: datetime
    updated_at: datetime
