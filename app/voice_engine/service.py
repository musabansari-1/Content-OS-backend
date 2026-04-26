from typing import List, Optional

from app.voice_engine.extractor import VoiceProfileExtractorService
from app.voice_engine.repository import CreatorVoiceProfileRepository
from app.voice_engine.types import CreatorVoiceProfileRecord, VoiceProfile


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
    ) -> CreatorVoiceProfileRecord:
        voice_profile = self.extractor.extractVoiceProfile(samples)
        return self.repository.create_or_update(userId, voice_profile)

    def getVoiceProfile(
        self,
        userId: Optional[int],
    ) -> Optional[CreatorVoiceProfileRecord]:
        return self.repository.get_by_user_id(userId)

    def listVoiceProfiles(self, userId: int) -> list[CreatorVoiceProfileRecord]:
        return self.repository.list_all(userId)

    def repairStoredPreferredDevices(self) -> list[CreatorVoiceProfileRecord]:
        repaired_records = []

        for record in self.repository.list_all():
            normalized_profile = VoiceProfile.parse_obj(
                self._voice_profile_to_payload(record.voice_profile_json)
            )

            if normalized_profile.preferred_devices == record.voice_profile_json.preferred_devices:
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
