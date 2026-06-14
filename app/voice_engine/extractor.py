import json
import re
from typing import List, Optional

from app.prompts.voice_profiling_prompt import (
    VOICE_PROFILE_EXTRACTION_PROMPT,
    VOICE_PROFILE_SYNTHESIS_PROMPT,
)
from app.utils.llm import call_llm
from app.voice_engine.types import VoiceProfile


class VoiceProfileExtractorService:
    _batch_size = 4

    def extractVoiceProfile(
        self,
        samples: List[str],
        existing_profile: Optional[VoiceProfile] = None,
    ) -> VoiceProfile:
        cleaned_samples = [sample.strip() for sample in samples if sample and sample.strip()]

        if not cleaned_samples:
            raise ValueError("At least one non-empty writing sample is required.")

        if len(cleaned_samples) <= self._batch_size and not existing_profile:
            return self._extract_from_samples(cleaned_samples)

        batch_profiles = []
        for batch in self._chunk_samples(cleaned_samples, self._batch_size):
            batch_profiles.append(self._extract_from_samples(batch))

        return self._synthesize_profiles(batch_profiles, existing_profile, len(cleaned_samples))

    def _voice_profile_to_payload(self, voice_profile: VoiceProfile) -> dict:
        if hasattr(voice_profile, "model_dump"):
            return voice_profile.model_dump()
        return voice_profile.dict()

    def _extract_from_samples(self, samples: List[str]) -> VoiceProfile:
        payload = {
            "samples": samples,
            "existing_profile": None,
        }

        response = call_llm(
            VOICE_PROFILE_EXTRACTION_PROMPT,
            json.dumps(payload, ensure_ascii=False),
            temperature=0.2,
        )

        return VoiceProfile.parse_obj(self._load_json_object(response))

    def _synthesize_profiles(
        self,
        candidate_profiles: List[VoiceProfile],
        existing_profile: Optional[VoiceProfile],
        sample_count: int,
    ) -> VoiceProfile:
        payload = {
            "candidate_profiles": [self._voice_profile_to_payload(profile) for profile in candidate_profiles],
            "existing_profile": self._voice_profile_to_payload(existing_profile)
            if existing_profile
            else None,
            "sample_count": sample_count,
        }

        response = call_llm(
            VOICE_PROFILE_SYNTHESIS_PROMPT,
            json.dumps(payload, ensure_ascii=False),
            temperature=0.15,
        )

        profile = VoiceProfile.parse_obj(self._load_json_object(response))
        if profile.sample_count <= 0:
            profile.sample_count = sample_count
        return profile

    def _chunk_samples(self, samples: List[str], size: int) -> List[List[str]]:
        return [samples[index:index + size] for index in range(0, len(samples), size)]

    def _load_json_object(self, response: str) -> dict:
        text = (response or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
