import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from app.integrations_repository import SocialIntegrationRecord
from app.services import youtube_service as service


class YouTubeServiceTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        service._youtube_oauth_state_store.clear()

    def _youtube_connection(self) -> SocialIntegrationRecord:
        return SocialIntegrationRecord(
            id=1,
            user_id=17,
            platform="youtube",
            platform_user_id="UC123",
            platform_username="Creator Channel",
            access_token="stored-token",
            refresh_token="refresh-token",
            scope="https://www.googleapis.com/auth/youtube.upload",
            token_type="Bearer",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
            updated_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

    def test_start_youtube_auth_requests_upload_scope_and_offline_access(self) -> None:
        with (
            patch.object(service, "YOUTUBE_CLIENT_ID", "client-id.apps.googleusercontent.com"),
            patch.object(service, "YOUTUBE_CLIENT_SECRET", "secret"),
            patch.object(service, "YOUTUBE_REDIRECT_URI", "https://api.example.com/auth/youtube/callback"),
            patch("app.services.youtube_service.secrets.token_urlsafe", return_value="state-123"),
        ):
            auth_url = service.start_youtube_auth(user_id=17)

        parsed = urlparse(auth_url)
        query = parse_qs(parsed.query)

        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", service.YOUTUBE_AUTH_URL)
        self.assertEqual(query["client_id"], ["client-id.apps.googleusercontent.com"])
        self.assertEqual(query["redirect_uri"], ["https://api.example.com/auth/youtube/callback"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["scope"], [" ".join(service.YOUTUBE_SCOPES)])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["include_granted_scopes"], ["true"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["state"], ["state-123"])

    def test_build_youtube_video_resource_uses_asset_output_and_private_default(self) -> None:
        asset = {
            "assetType": "youtube_shorts",
            "output": {
                "title": "The Jenkins deploy trap",
                "description": "A short explanation of why manual deploys break.",
                "hashtags": ["#devops", "jenkins"],
            },
        }

        with patch.object(service, "YOUTUBE_DEFAULT_PRIVACY_STATUS", "private"):
            resource = service._build_youtube_video_resource(
                asset,
                privacy_status=None,
                title=None,
                description=None,
                tags=None,
                category_id=None,
                self_declared_made_for_kids=None,
                contains_synthetic_media=True,
            )

        self.assertEqual(resource["snippet"]["title"], "The Jenkins deploy trap")
        self.assertEqual(resource["snippet"]["categoryId"], "22")
        self.assertIn("manual deploys break", resource["snippet"]["description"])
        self.assertIn("#devops #jenkins", resource["snippet"]["description"])
        self.assertEqual(resource["snippet"]["tags"], ["devops", "jenkins"])
        self.assertEqual(resource["status"]["privacyStatus"], "private")
        self.assertFalse(resource["status"]["selfDeclaredMadeForKids"])
        self.assertTrue(resource["status"]["containsSyntheticMedia"])

    def test_normalize_privacy_status_rejects_invalid_value(self) -> None:
        with self.assertRaises(HTTPException) as error_context:
            service._normalize_privacy_status("friends")

        self.assertEqual(error_context.exception.status_code, 400)
        self.assertIn("privacy_status", str(error_context.exception.detail))

    async def test_access_token_for_connection_refreshes_near_expiry(self) -> None:
        connection = SocialIntegrationRecord(
            id=1,
            user_id=17,
            platform="youtube",
            platform_user_id="UC123",
            platform_username="Creator Channel",
            access_token="stored-token",
            refresh_token="refresh-token",
            scope="https://www.googleapis.com/auth/youtube.upload",
            token_type="Bearer",
            token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
            updated_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        with patch.object(service, "_refresh_youtube_access_token", AsyncMock(return_value="refreshed-token")) as mock_refresh:
            token = await service._access_token_for_connection(connection)

        self.assertEqual(token, "refreshed-token")
        mock_refresh.assert_awaited_once_with(connection)

    async def test_publish_youtube_asset_for_user_uploads_youtube_shorts_asset(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_video:
            temp_video.write(b"video-bytes")
            video_path = Path(temp_video.name)

        upload_result = {"id": "yt-video-123"}
        mock_upsert = Mock()
        try:
            with (
                patch.object(service.social_integration_repository, "get_by_user_and_platform", return_value=self._youtube_connection()),
                patch.object(service.social_integration_repository, "upsert_connection", mock_upsert),
                patch.object(service, "_access_token_for_connection", AsyncMock(return_value="fresh-token")),
                patch.object(service, "_upload_youtube_video", AsyncMock(return_value=upload_result)) as mock_upload,
            ):
                result = await service.publish_youtube_asset_for_user(
                    user_id=17,
                    asset={
                        "assetType": "youtube_shorts",
                        "title": "Short title",
                        "media": {"video_path": str(video_path), "video_content_type": "video/mp4"},
                    },
                    privacy_status="unlisted",
                )
        finally:
            video_path.unlink(missing_ok=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["youtube_video_id"], "yt-video-123")
        self.assertEqual(result["youtube_video_url"], "https://www.youtube.com/watch?v=yt-video-123")
        self.assertEqual(result["privacy_status"], "unlisted")
        mock_upload.assert_awaited_once()
        _, kwargs = mock_upload.await_args
        self.assertEqual(kwargs["access_token"], "fresh-token")
        self.assertEqual(kwargs["video_path"], video_path)
        self.assertEqual(kwargs["content_type"], "video/mp4")
        self.assertEqual(kwargs["video_resource"]["snippet"]["title"], "Short title")
        self.assertEqual(kwargs["video_resource"]["status"]["privacyStatus"], "unlisted")


if __name__ == "__main__":
    unittest.main()
