from typing import Iterable, List, Optional

from app.voice_engine.domain import VoiceProfileRecord
from app.voice_engine.extractor import VoiceProfileExtractorService
from app.voice_engine.repository import CreatorVoiceProfileRepository
from app.voice_engine.types import VoiceProfile


class CreatorVoiceProfileService:
    def __init__(
        self,
        extractor: Optional[VoiceProfileExtractorService] = None,
        repository: Optional[CreatorVoiceProfileRepository] = None,
    ) -> None:
        self.extractor = extractor or VoiceProfileExtractorService()
        self.repository = repository or CreatorVoiceProfileRepository()

    def createOrUpdateVoiceProfile(
        self,
        userId: Optional[int],
        samples: List[str],
    ) -> VoiceProfileRecord:
        existing_record = self.repository.get_by_user_id(userId)
        existing_profile = existing_record.voice_profile if existing_record else None
        extracted_profile = self.extractor.extractVoiceProfile(samples, existing_profile)
        merged_profile = self._merge_voice_profiles(existing_profile, extracted_profile)
        return self.repository.create_or_update(userId, merged_profile)

    def getVoiceProfile(
        self,
        userId: Optional[int],
    ) -> Optional[VoiceProfileRecord]:
        return self.repository.get_by_user_id(userId)

    def listVoiceProfiles(self, userId: int) -> list[VoiceProfileRecord]:
        return self.repository.list_all(userId)

    def repairStoredPreferredDevices(self) -> list[VoiceProfileRecord]:
        repaired_records = []

        for record in self.repository.list_all():
            normalized_profile = VoiceProfile.parse_obj(
                self._voice_profile_to_payload(record.voice_profile)
            )

            if normalized_profile.preferred_devices == record.voice_profile.preferred_devices:
                continue

            repaired_records.append(
                self.repository.create_or_update(
                    record.user_id,
                    normalized_profile,
                )
            )

        return repaired_records

    def _voice_profile_to_payload(self, voice_profile: VoiceProfile) -> dict:
        if hasattr(voice_profile, "model_dump"):
            return voice_profile.model_dump()
        return voice_profile.dict()

    def _merge_voice_profiles(
        self,
        existing_profile: Optional[VoiceProfile],
        refined_profile: VoiceProfile,
    ) -> VoiceProfile:
        if not existing_profile:
            return refined_profile

        merged_payload = {
            "sample_count": self._merge_sample_count(
                existing_profile.sample_count,
                refined_profile.sample_count,
            ),
            "field_confidence": self._merge_field_confidence(
                existing_profile.field_confidence,
                refined_profile.field_confidence,
            ),
            "evidence": self._merge_evidence(
                existing_profile.evidence,
                refined_profile.evidence,
            ),
            "tone": self._merge_lists(refined_profile.tone, existing_profile.tone, limit=6),
            "sentence_rhythm": self._prefer_text(
                refined_profile.sentence_rhythm,
                existing_profile.sentence_rhythm,
            ),
            "hook_style": self._merge_lists(
                refined_profile.hook_style,
                existing_profile.hook_style,
                limit=6,
            ),
            "cta_style": self._merge_lists(
                refined_profile.cta_style,
                existing_profile.cta_style,
                limit=5,
            ),
            "humor_style": self._prefer_text(
                refined_profile.humor_style,
                existing_profile.humor_style,
            ),
            "emotional_intensity": self._prefer_text(
                refined_profile.emotional_intensity,
                existing_profile.emotional_intensity,
            ),
            "emoji_usage": self._prefer_text(
                refined_profile.emoji_usage,
                existing_profile.emoji_usage,
            ),
            "punctuation_style": self._prefer_text(
                refined_profile.punctuation_style,
                existing_profile.punctuation_style,
            ),
            "preferred_devices": self._merge_lists(
                refined_profile.preferred_devices,
                existing_profile.preferred_devices,
                limit=8,
            ),
            "banned_phrases": self._merge_lists(
                refined_profile.banned_phrases,
                existing_profile.banned_phrases,
                limit=8,
            ),
            "preferred_phrases": self._merge_lists(
                refined_profile.preferred_phrases,
                existing_profile.preferred_phrases,
                limit=10,
            ),
            "narrative_behavior": {
                "opening_pattern": self._prefer_text(
                    refined_profile.narrative_behavior.opening_pattern,
                    existing_profile.narrative_behavior.opening_pattern,
                ),
                "idea_progression": self._merge_lists(
                    refined_profile.narrative_behavior.idea_progression,
                    existing_profile.narrative_behavior.idea_progression,
                    limit=7,
                ),
                "tension_pattern": self._prefer_text(
                    refined_profile.narrative_behavior.tension_pattern,
                    existing_profile.narrative_behavior.tension_pattern,
                ),
                "teaching_pattern": self._prefer_text(
                    refined_profile.narrative_behavior.teaching_pattern,
                    existing_profile.narrative_behavior.teaching_pattern,
                ),
                "authority_pattern": self._prefer_text(
                    refined_profile.narrative_behavior.authority_pattern,
                    existing_profile.narrative_behavior.authority_pattern,
                ),
                "closing_pattern": self._prefer_text(
                    refined_profile.narrative_behavior.closing_pattern,
                    existing_profile.narrative_behavior.closing_pattern,
                ),
            },
            "cognitive_style": {
                "reasoning_style": self._merge_lists(
                    refined_profile.cognitive_style.reasoning_style,
                    existing_profile.cognitive_style.reasoning_style,
                    limit=6,
                ),
                "decision_lens": self._merge_lists(
                    refined_profile.cognitive_style.decision_lens,
                    existing_profile.cognitive_style.decision_lens,
                    limit=6,
                ),
                "abstraction_pattern": self._prefer_text(
                    refined_profile.cognitive_style.abstraction_pattern,
                    existing_profile.cognitive_style.abstraction_pattern,
                ),
                "problem_solving_style": self._prefer_text(
                    refined_profile.cognitive_style.problem_solving_style,
                    existing_profile.cognitive_style.problem_solving_style,
                ),
                "common_reframes": self._merge_lists(
                    refined_profile.cognitive_style.common_reframes,
                    existing_profile.cognitive_style.common_reframes,
                    limit=8,
                ),
            },
            "constraint_profile": {
                "avoids": self._merge_lists(
                    refined_profile.constraint_profile.avoids,
                    existing_profile.constraint_profile.avoids,
                    limit=8,
                ),
                "never_does": self._merge_lists(
                    refined_profile.constraint_profile.never_does,
                    existing_profile.constraint_profile.never_does,
                    limit=8,
                ),
                "overuse_risks": self._merge_lists(
                    refined_profile.constraint_profile.overuse_risks,
                    existing_profile.constraint_profile.overuse_risks,
                    limit=8,
                ),
            },
            "voice_anchors": self._merge_lists(
                refined_profile.voice_anchors,
                existing_profile.voice_anchors,
                limit=7,
            ),
            "style_summary": self._prefer_text(
                refined_profile.style_summary,
                existing_profile.style_summary,
            ),
        }

        return VoiceProfile.parse_obj(merged_payload)

    def _merge_sample_count(self, existing: int, refined: int) -> int:
        existing_count = max(int(existing or 0), 0)
        refined_count = max(int(refined or 0), 0)
        return max(existing_count, refined_count)

    def _merge_field_confidence(
        self,
        existing: dict[str, float],
        refined: dict[str, float],
    ) -> dict[str, float]:
        merged: dict[str, float] = {}

        for source in (existing or {}, refined or {}):
            for key, value in source.items():
                try:
                    score = float(value)
                except (TypeError, ValueError):
                    continue

                score = max(0.0, min(score, 1.0))
                if key not in merged or score > merged[key]:
                    merged[key] = score

        return merged

    def _merge_evidence(
        self,
        existing: dict[str, list[str]],
        refined: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        merged: dict[str, list[str]] = {}

        for source in (existing or {}, refined or {}):
            for key, values in source.items():
                merged[key] = self._merge_lists(values, merged.get(key, []), limit=4)

        return merged

    def _prefer_text(self, primary: str, fallback: str) -> str:
        value = (primary or "").strip()
        if value:
            return value
        return (fallback or "").strip()

    def _merge_lists(
        self,
        primary: Iterable[str],
        secondary: Iterable[str],
        limit: int,
    ) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()

        for raw_value in list(primary or []) + list(secondary or []):
            value = (raw_value or "").strip()
            if not value:
                continue

            key = value.casefold()
            if key in seen:
                continue

            seen.add(key)
            merged.append(value)

            if len(merged) >= limit:
                break

        return merged
