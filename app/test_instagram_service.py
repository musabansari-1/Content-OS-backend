import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import HTTPException

from app.integrations_repository import SocialIntegrationRecord
from app.services import instagram_service as service


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.content = b"{}"
        self.request = httpx.Request("GET", "https://example.com")

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=self,
            )


class FakeAsyncClient:
    calls: list[tuple[str, str, dict]]
    responses: list[FakeResponse]

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False

    async def get(self, url: str, *, params: dict | None = None, **kwargs):
        del kwargs
        self.calls.append(("GET", url, params or {}))
        return self.responses.pop(0)

    async def post(self, url: str, *, data: dict | None = None, **kwargs):
        del kwargs
        self.calls.append(("POST", url, data or {}))
        return self.responses.pop(0)


class InstagramServiceTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        service._instagram_oauth_state_store.clear()

    def test_start_instagram_auth_uses_instagram_login_authorize_url_and_business_scopes(self) -> None:
        with (
            patch.object(service, "INSTAGRAM_APP_ID", "1234567890"),
            patch.object(service, "INSTAGRAM_REDIRECT_URI", "https://app.example.com/auth/instagram/callback"),
            patch.object(service, "INSTAGRAM_APP_SECRET", "secret"),
            patch("app.services.instagram_service.secrets.token_urlsafe", return_value="state-123"),
        ):
            auth_url = service.start_instagram_auth(user_id=17)

        parsed = urlparse(auth_url)
        query = parse_qs(parsed.query)

        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", service.INSTAGRAM_AUTH_URL)
        self.assertEqual(query["client_id"], ["1234567890"])
        self.assertEqual(query["redirect_uri"], ["https://app.example.com/auth/instagram/callback"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(
            query["scope"],
            [",".join(service.INSTAGRAM_SCOPES)],
        )
        self.assertEqual(query["state"], ["state-123"])

    async def test_handle_instagram_callback_stores_professional_account_id_from_me_lookup(self) -> None:
        mock_upsert = Mock()
        with (
            patch.object(service, "INSTAGRAM_APP_ID", "1234567890"),
            patch.object(service, "INSTAGRAM_APP_SECRET", "secret"),
            patch.object(service, "INSTAGRAM_REDIRECT_URI", "https://app.example.com/auth/instagram/callback"),
            patch.object(service, "_pop_instagram_oauth_state", return_value=17),
            patch.object(
                service,
                "_exchange_instagram_code_for_short_lived_token",
                AsyncMock(
                    return_value={
                        "data": [
                            {
                                "access_token": "short-token",
                                "user_id": "1789",
                                "permissions": "instagram_business_basic,instagram_business_content_publish",
                            }
                        ]
                    }
                ),
            ),
            patch.object(
                service,
                "_exchange_instagram_token_for_long_lived_token",
                AsyncMock(
                    return_value={
                        "access_token": "long-token",
                        "token_type": "bearer",
                        "expires_in": 5183944,
                    }
                ),
            ),
            patch.object(
                service,
                "_fetch_instagram_account_profile",
                AsyncMock(
                    return_value={
                        "instagram_user_id": "90010177253934",
                        "username": "creator_handle",
                    }
                ),
            ),
            patch.object(service.social_integration_repository, "upsert_connection", mock_upsert),
            patch.object(service, "FRONTEND_BASE_URL", "https://app.example.com"),
        ):
            response = await service.handle_instagram_callback(code="auth-code", state="oauth-state")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "https://app.example.com/integrations?instagram=connected")
        mock_upsert.assert_called_once()
        _, kwargs = mock_upsert.call_args
        self.assertEqual(kwargs["user_id"], 17)
        self.assertEqual(kwargs["platform"], "instagram")
        self.assertEqual(kwargs["platform_user_id"], "90010177253934")
        self.assertEqual(kwargs["platform_username"], "creator_handle")
        self.assertEqual(kwargs["access_token"], "long-token")
        self.assertEqual(kwargs["scope"], "instagram_business_basic,instagram_business_content_publish")
        self.assertEqual(kwargs["token_type"], "bearer")
        self.assertEqual(kwargs["expires_in"], 5183944)

    async def test_create_instagram_media_container_uses_graph_instagram_host(self) -> None:
        fake_client_calls: list[tuple[str, str, dict]] = []
        FakeAsyncClient.calls = fake_client_calls
        FakeAsyncClient.responses = [FakeResponse({"id": "container-123"})]

        with patch("app.services.instagram_service.httpx.AsyncClient", FakeAsyncClient):
            creation_id = await service._create_instagram_media_container(
                access_token="token-123",
                instagram_user_id="90010177253934",
                payload={"image_url": "https://cdn.example.com/slide-1.png"},
            )

        self.assertEqual(creation_id, "container-123")
        self.assertEqual(len(fake_client_calls), 1)
        method, url, data = fake_client_calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, f"{service.INSTAGRAM_GRAPH_URL}/90010177253934/media")
        self.assertEqual(data["access_token"], "token-123")
        self.assertEqual(data["image_url"], "https://cdn.example.com/slide-1.png")

    async def test_access_token_for_connection_refreshes_near_expiry_long_lived_tokens(self) -> None:
        connection = SocialIntegrationRecord(
            id=1,
            user_id=17,
            platform="instagram",
            platform_user_id="90010177253934",
            platform_username="creator_handle",
            access_token="existing-token",
            refresh_token=None,
            scope="instagram_business_basic,instagram_business_content_publish",
            token_type="bearer",
            token_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
            updated_at=datetime.now(timezone.utc) - timedelta(days=2),
        )

        with patch.object(service, "_refresh_instagram_access_token", AsyncMock(return_value="refreshed-token")) as mock_refresh:
            token = await service._access_token_for_connection(connection)

        self.assertEqual(token, "refreshed-token")
        mock_refresh.assert_awaited_once_with(connection)

    async def test_publish_instagram_reel_rejects_localhost_video_url(self) -> None:
        asset = {
            "assetType": "instagram_reel",
            "media": {
                "videoUrl": "http://localhost:8000/generated-clips/run-1/reel.mp4",
            },
        }

        with self.assertRaises(HTTPException) as error_context:
            await service._publish_instagram_reel("token-123", "90010177253934", asset)

        self.assertEqual(error_context.exception.status_code, 400)
        self.assertIn("public HTTPS URL", str(error_context.exception.detail))

    def test_resolve_instagram_carousel_slide_url_falls_back_to_public_base_url(self) -> None:
        file_path = Path("D:/generated/instagram/slide-1.png")

        with (
            patch.object(service, "GENERATED_CLIPS_DIR", Path("D:/generated")),
            patch.object(service, "PUBLIC_BASE_URL", "https://api.contentburst.app"),
            patch.object(service, "_upload_generated_clip", side_effect=RuntimeError("upload unavailable")),
        ):
            resolved_url = service._resolve_instagram_carousel_slide_url(file_path)

        self.assertEqual(resolved_url, "https://api.contentburst.app/generated-clips/instagram/slide-1.png")

    def test_resolve_instagram_carousel_slide_url_requires_public_https_media(self) -> None:
        file_path = Path("D:/generated/instagram/slide-1.png")

        with (
            patch.object(service, "GENERATED_CLIPS_DIR", Path("D:/generated")),
            patch.object(service, "PUBLIC_BASE_URL", "http://localhost:8000"),
            patch.object(service, "_upload_generated_clip", side_effect=RuntimeError("upload unavailable")),
        ):
            with self.assertRaises(HTTPException) as error_context:
                service._resolve_instagram_carousel_slide_url(file_path)

        self.assertEqual(error_context.exception.status_code, 400)
        self.assertIn("public HTTPS image URLs", str(error_context.exception.detail))

    async def test_publish_instagram_container_with_retry_retries_not_ready_response(self) -> None:
        not_ready_response = FakeResponse(
            {},
            status_code=400,
            text="The media is not ready to be published. Please wait.",
        )
        not_ready_response.request = httpx.Request("POST", "https://graph.instagram.com/v25.0/123/media_publish")
        not_ready_error = httpx.HTTPStatusError(
            "HTTP 400",
            request=not_ready_response.request,
            response=not_ready_response,
        )

        with (
            patch.object(
                service,
                "_publish_instagram_container",
                AsyncMock(side_effect=[not_ready_error, {"id": "published-123"}]),
            ) as mock_publish,
            patch("app.services.instagram_service.asyncio.sleep", AsyncMock()) as mock_sleep,
        ):
            result = await service._publish_instagram_container_with_retry(
                access_token="token-123",
                instagram_user_id="90010177253934",
                creation_id="creation-123",
            )

        self.assertEqual(result, {"id": "published-123"})
        self.assertEqual(mock_publish.await_count, 2)
        mock_sleep.assert_awaited_once()

    async def test_publish_instagram_carousel_uses_resolved_slide_urls(self) -> None:
        asset = {
            "assetType": "instagram_carousel",
            "title": "My carousel",
            "blocks": [{"key": "slides", "value": ["Slide 1", "Slide 2"]}],
        }

        render_paths = [
            Path("D:/generated/instagram/slide-1.png"),
            Path("D:/generated/instagram/slide-2.png"),
        ]

        with (
            patch.object(service, "_render_carousel_slide", side_effect=render_paths),
            patch.object(
                service,
                "_resolve_instagram_carousel_slide_url",
                side_effect=[
                    "https://cdn.example.com/slide-1.png",
                    "https://cdn.example.com/slide-2.png",
                ],
            ),
            patch.object(
                service,
                "_create_instagram_media_container",
                AsyncMock(side_effect=["child-1", "child-2", "carousel-1"]),
            ) as mock_create,
            patch.object(
                service,
                "_publish_instagram_container_with_retry",
                AsyncMock(return_value={"id": "post-123"}),
            ),
        ):
            result = await service._publish_instagram_carousel("token-123", "90010177253934", asset)

        self.assertTrue(result["ok"])
        self.assertEqual(result["instagram_post_id"], "post-123")
        create_calls = mock_create.await_args_list
        self.assertEqual(create_calls[0].kwargs["payload"]["image_url"], "https://cdn.example.com/slide-1.png")
        self.assertEqual(create_calls[1].kwargs["payload"]["image_url"], "https://cdn.example.com/slide-2.png")
        self.assertEqual(create_calls[2].kwargs["payload"]["children"], "child-1,child-2")

    def test_normalize_carousel_slide_preserves_structured_template_fields(self) -> None:
        normalized = service._normalize_carousel_slide(
            {
                "type": "quote",
                "quote": "Tiny preview cards beat giant text posters.",
                "body": "Make the published asset match the preview layout.",
                "eyebrow": "Insight",
            },
            index=1,
            total=5,
        )

        self.assertEqual(normalized["type"], "quote")
        self.assertEqual(normalized["quote"], "Tiny preview cards beat giant text posters.")
        self.assertEqual(normalized["body"], "Make the published asset match the preview layout.")
        self.assertEqual(normalized["eyebrow"], "Insight")

    def test_build_caption_from_asset_uses_caption_and_cta_without_labels(self) -> None:
        asset = {
            "title": "Manual releases are a nightmare",
            "blocks": [
                {"key": "slides", "value": ["slide 1", "slide 2"]},
                {"key": "caption", "value": "Manual releases are a nightmare. CI/CD turns that into a one-click pipeline."},
                {"key": "cta", "value": "Watch the full video for the full Jenkinsfile template."},
            ],
        }

        caption = service._build_caption_from_asset(asset)

        self.assertEqual(
            caption,
            "Manual releases are a nightmare. CI/CD turns that into a one-click pipeline.\n\n"
            "Watch the full video for the full Jenkinsfile template.",
        )
        self.assertNotIn("caption:", caption.lower())
        self.assertNotIn("cta:", caption.lower())


if __name__ == "__main__":
    unittest.main()
