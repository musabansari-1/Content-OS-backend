from typing import List

from app.voice_engine.extractor import VoiceProfileExtractorService
from app.voice_engine.types import VoiceProfile


_extractor_service = VoiceProfileExtractorService()


def extract_voice_profile(samples: List[str]) -> VoiceProfile:
    return _extractor_service.extractVoiceProfile(samples)
