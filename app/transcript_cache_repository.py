import json
from typing import Any, Optional

from app.core.db import get_connection, run_migrations


class TranscriptCacheRepository:
    def __init__(self) -> None:
        run_migrations()

    def get_by_video_id(self, video_id: str) -> Optional[dict[str, Any]]:
        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT
                    video_id,
                    video_url,
                    transcript_json,
                    full_text,
                    segments_json,
                    language,
                    source,
                    is_generated,
                    created_at,
                    updated_at
                FROM youtube_transcript_cache
                WHERE video_id = %s
                """,
                (video_id,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        transcript_bundle = json.loads(row["transcript_json"])
        transcript_bundle["video_id"] = row["video_id"]
        transcript_bundle["video_url"] = row["video_url"]
        transcript_bundle["full_text"] = row["full_text"]
        transcript_bundle["segments"] = json.loads(row["segments_json"])
        transcript_bundle["language"] = row["language"]
        transcript_bundle["source"] = row["source"]
        transcript_bundle["is_generated"] = bool(row["is_generated"])
        transcript_bundle["created_at"] = row["created_at"]
        transcript_bundle["updated_at"] = row["updated_at"]

        return {
            **transcript_bundle,
        }

    def upsert(self, transcript_bundle: dict[str, Any]) -> dict[str, Any]:
        video_id = transcript_bundle["video_id"]
        video_url = transcript_bundle.get("video_url") or f"https://www.youtube.com/watch?v={video_id}"
        transcript_json = json.dumps(transcript_bundle, ensure_ascii=False)
        full_text = transcript_bundle.get("full_text", "")
        segments_json = json.dumps(transcript_bundle.get("segments", []), ensure_ascii=False)
        language = transcript_bundle.get("language") or "en"
        source = transcript_bundle.get("source") or "transcriptyt"
        is_generated = bool(transcript_bundle.get("is_generated", False))

        connection = get_connection()
        try:
            connection.execute(
                """
                INSERT INTO youtube_transcript_cache (
                    video_id,
                    video_url,
                    transcript_json,
                    full_text,
                    segments_json,
                    language,
                    source,
                    is_generated
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (video_id) DO UPDATE SET
                    video_url = EXCLUDED.video_url,
                    transcript_json = EXCLUDED.transcript_json,
                    full_text = EXCLUDED.full_text,
                    segments_json = EXCLUDED.segments_json,
                    language = EXCLUDED.language,
                    source = EXCLUDED.source,
                    is_generated = EXCLUDED.is_generated,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    video_id,
                    video_url,
                    transcript_json,
                    full_text,
                    segments_json,
                    language,
                    source,
                    is_generated,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        cached = self.get_by_video_id(video_id)
        if cached is None:
            return transcript_bundle
        return cached
