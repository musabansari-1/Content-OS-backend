import json
from typing import List

from app.prompts.voice_profiling_prompt import VOICE_PROFILE_EXTRACTION_PROMPT
from app.utils.llm import call_llm
from app.voice_engine.types import VoiceProfile


class VoiceProfileExtractorService:
    def extractVoiceProfile(self, samples: List[str]) -> VoiceProfile:
        cleaned_samples = [sample.strip() for sample in samples if sample and sample.strip()]

        if not cleaned_samples:
            raise ValueError("At least one non-empty writing sample is required.")

        payload = {
            "samples": cleaned_samples,
        }

        response = call_llm(
            VOICE_PROFILE_EXTRACTION_PROMPT,
            json.dumps(payload, ensure_ascii=False),
        )

        voice_profile_json = json.loads(response)
        return VoiceProfile.parse_obj(voice_profile_json)
