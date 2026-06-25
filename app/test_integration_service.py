import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx

from app.integrations_repository import SocialIntegrationRecord
from app.services import integration_service as service


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.content = b"{}"
        self.request = httpx.Request("POST", "https://api.x.com/2/tweets")

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

    async def post(
        self,
        url: str,
        *,
        data: dict | None = None,
        json: dict | None = None,
        headers: dict | None = None,
        auth=None,
        **kwargs,
    ):
        del kwargs
        self.calls.append(
            (
                "POST",
                url,
                {
                    "data": data or {},
                    "json": json or {},
                    "headers": headers or {},
                    "auth": auth,
                },
            )
        )
        return self.responses.pop(0)


class XIntegrationServiceTests(unittest.IsolatedAsyncioTestCase):
    def _x_connection(self) -> SocialIntegrationRecord:
        return SocialIntegrationRecord(
            id=1,
            user_id=17,
            platform="x",
            platform_user_id="12345",
            platform_username="creator_handle",
            access_token="stored-token",
            refresh_token="refresh-token",
            scope="tweet.read tweet.write users.read offline.access",
            token_type="bearer",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
            updated_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

    async def test_publish_x_asset_for_user_publishes_single_post(self) -> None:
        fake_client_calls: list[tuple[str, str, dict]] = []
        FakeAsyncClient.calls = fake_client_calls
        FakeAsyncClient.responses = [
            FakeResponse({"data": {"id": "post-1", "text": "Ship the sharper take."}}, text='{"data":{"id":"post-1"}}'),
        ]

        with (
            patch.object(service.social_integration_repository, "get_by_user_and_platform", return_value=self._x_connection()),
            patch.object(service, "_access_token_for_x_connection", AsyncMock(return_value="fresh-token")),
            patch("app.services.integration_service.httpx.AsyncClient", FakeAsyncClient),
        ):
            result = await service.publish_x_asset_for_user(
                user_id=17,
                asset={"asset_type": "x_post", "post": "Ship the sharper take."},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["asset_type"], "x_post")
        self.assertEqual(result["x_post_id"], "post-1")
        self.assertEqual(result["x_post_ids"], ["post-1"])
        self.assertEqual(result["published_count"], 1)
        self.assertEqual(len(fake_client_calls), 1)
        method, url, payload = fake_client_calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, service.X_POSTS_URL)
        self.assertEqual(payload["json"], {"text": "Ship the sharper take."})
        self.assertEqual(payload["headers"]["Authorization"], "Bearer fresh-token")

    async def test_publish_x_asset_for_user_publishes_thread_as_reply_chain(self) -> None:
        fake_client_calls: list[tuple[str, str, dict]] = []
        FakeAsyncClient.calls = fake_client_calls
        FakeAsyncClient.responses = [
            FakeResponse({"data": {"id": "post-1", "text": "Hook"}}),
            FakeResponse({"data": {"id": "post-2", "text": "Layer 2"}}),
            FakeResponse({"data": {"id": "post-3", "text": "CTA"}}),
        ]

        with (
            patch.object(service.social_integration_repository, "get_by_user_and_platform", return_value=self._x_connection()),
            patch.object(service, "_access_token_for_x_connection", AsyncMock(return_value="fresh-token")),
            patch("app.services.integration_service.httpx.AsyncClient", FakeAsyncClient),
        ):
            result = await service.publish_x_asset_for_user(
                user_id=17,
                asset={
                    "assetType": "twitter_thread",
                    "tweets": ["Hook", "Layer 2", "CTA"],
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["asset_type"], "twitter_thread")
        self.assertEqual(result["x_post_id"], "post-1")
        self.assertEqual(result["x_post_ids"], ["post-1", "post-2", "post-3"])
        self.assertEqual(result["published_count"], 3)
        self.assertEqual(len(fake_client_calls), 3)
        self.assertEqual(fake_client_calls[0][2]["json"], {"text": "Hook"})
        self.assertEqual(
            fake_client_calls[1][2]["json"],
            {"text": "Layer 2", "reply": {"in_reply_to_tweet_id": "post-1"}},
        )
        self.assertEqual(
            fake_client_calls[2][2]["json"],
            {"text": "CTA", "reply": {"in_reply_to_tweet_id": "post-2"}},
        )

    async def test_publish_x_asset_for_user_rejects_overlong_post(self) -> None:
        with patch.object(
            service.social_integration_repository,
            "get_by_user_and_platform",
            return_value=self._x_connection(),
        ):
            result = await service.publish_x_asset_for_user(
                user_id=17,
                asset={"asset_type": "x_post", "post": "x" * 281},
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "x_invalid_asset")
        self.assertEqual(result["status_code"], 400)

    async def test_access_token_for_x_connection_refreshes_near_expiry(self) -> None:
        connection = SocialIntegrationRecord(
            id=1,
            user_id=17,
            platform="x",
            platform_user_id="12345",
            platform_username="creator_handle",
            access_token="stored-token",
            refresh_token="refresh-token",
            scope="tweet.read tweet.write users.read offline.access",
            token_type="bearer",
            token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
            updated_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        with patch.object(service, "_refresh_x_access_token", AsyncMock(return_value="refreshed-token")) as mock_refresh:
            token = await service._access_token_for_x_connection(connection)

        self.assertEqual(token, "refreshed-token")
        mock_refresh.assert_awaited_once_with(connection)


if __name__ == "__main__":
    unittest.main()
