import json
from typing import Optional

from app.core.db import get_connection
from app.voice_engine.types import CreatorVoiceProfileRecord, VoiceProfile


def _voice_profile_to_dict(voice_profile: VoiceProfile) -> dict:
    if hasattr(voice_profile, "model_dump"):
        return voice_profile.model_dump()
    return voice_profile.dict()


class CreatorVoiceProfileRepository:
    def create_or_update(
        self,
        user_id: Optional[int],
        voice_profile: VoiceProfile,
    ) -> CreatorVoiceProfileRecord:
        payload = json.dumps(_voice_profile_to_dict(voice_profile), ensure_ascii=True)
        connection = get_connection()

        try:
            existing = connection.execute(
                """
                SELECT id, version
                FROM creator_voice_profiles
                WHERE user_id = %s
                """,
                (user_id,),
            ).fetchone()

            if existing:
                next_version = int(existing["version"]) + 1
                connection.execute(
                    """
                    UPDATE creator_voice_profiles
                    SET voice_profile_json = %s,
                        style_summary = %s,
                        version = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                    """,
                    (
                        payload,
                        voice_profile.style_summary,
                        next_version,
                        user_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO creator_voice_profiles (
                        user_id,
                        voice_profile_json,
                        style_summary,
                        version
                    )
                    VALUES (%s, %s, %s, 1)
                    """,
                    (
                        user_id,
                        payload,
                        voice_profile.style_summary,
                    ),
                )

            connection.commit()
            return self.get_by_user_id(user_id)
        finally:
            connection.close()

    def get_by_user_id(
        self,
        user_id: Optional[int],
    ) -> Optional[CreatorVoiceProfileRecord]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    voice_profile_json,
                    style_summary,
                    version,
                    created_at,
                    updated_at
                FROM creator_voice_profiles
                WHERE user_id = %s
                """,
                (user_id,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        profile_payload = json.loads(row["voice_profile_json"])
        return CreatorVoiceProfileRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]) if row["user_id"] is not None else None,
            voice_profile_json=VoiceProfile.parse_obj(profile_payload),
            style_summary=row["style_summary"],
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_all(self, user_id: Optional[int] = None) -> list[CreatorVoiceProfileRecord]:
        connection = get_connection()

        try:
            if user_id is None:
                rows = connection.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        voice_profile_json,
                        style_summary,
                        version,
                        created_at,
                        updated_at
                    FROM creator_voice_profiles
                    ORDER BY id ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        voice_profile_json,
                        style_summary,
                        version,
                        created_at,
                        updated_at
                    FROM creator_voice_profiles
                    WHERE user_id = %s
                    ORDER BY id ASC
                    """,
                    (user_id,),
                ).fetchall()
        finally:
            connection.close()

        records = []
        for row in rows:
            profile_payload = json.loads(row["voice_profile_json"])
            records.append(
                CreatorVoiceProfileRecord(
                    id=int(row["id"]),
                    user_id=int(row["user_id"]) if row["user_id"] is not None else None,
                    voice_profile_json=VoiceProfile.parse_obj(profile_payload),
                    style_summary=row["style_summary"],
                    version=int(row["version"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )

        return records
