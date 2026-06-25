import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import httpx

from app.integrations_repository import SocialIntegrationRecord
from app.services import instagram_service as service


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""
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


if __name__ == "__main__":
    unittest.main()
