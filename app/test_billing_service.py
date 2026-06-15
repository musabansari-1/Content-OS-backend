import hashlib
import hmac
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.billing import service


class BillingServiceTests(unittest.TestCase):
    def test_resolve_frontend_base_url_prefers_frontend_base_url(self) -> None:
        with patch("app.billing.service.env") as mock_env:
            mock_env.side_effect = lambda name, default=None: {
                "FRONTEND_BASE_URL": "https://app.contentos.ai/",
                "FRONTEND_URL": "https://fallback.contentos.ai",
            }.get(name, default)
            self.assertEqual(service._resolve_frontend_base_url(), "https://app.contentos.ai")

    def test_resolve_frontend_base_url_falls_back_to_frontend_url(self) -> None:
        with patch("app.billing.service.env") as mock_env:
            mock_env.side_effect = lambda name, default=None: {
                "FRONTEND_URL": "https://contentos.app/",
            }.get(name, default)
            self.assertEqual(service._resolve_frontend_base_url(), "https://contentos.app")

    def test_get_checkout_settings_includes_cancel_url(self) -> None:
        with (
            patch.object(service, "FRONTEND_BASE_URL", "https://app.contentos.ai"),
            patch.object(service, "PADDLE_CLIENT_TOKEN", "test_client_token"),
            patch.object(service, "PADDLE_ENVIRONMENT", "sandbox"),
            patch.dict(service.PLAN_PRICE_IDS, {"pro": "pri_pro_123", "max": "pri_max_456"}, clear=True),
        ):
            settings = service.get_checkout_settings(
                user_id=17,
                user_email="owner@example.com",
                plan_code="pro",
            )

        self.assertEqual(settings["success_url"], "https://app.contentos.ai/billing?checkout=success")
        self.assertEqual(settings["cancel_url"], "https://app.contentos.ai/billing?checkout=canceled")
        self.assertEqual(settings["custom_data"], {"user_id": 17, "plan_code": "pro"})

    def test_verify_paddle_webhook_signature_accepts_valid_signature(self) -> None:
        raw_body = json.dumps({"event_id": "evt_123", "event_type": "subscription.updated"}).encode("utf-8")
        timestamp = "1718400000"
        secret = "whsec_test_secret"
        signature = hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("utf-8") + b":" + raw_body,
            hashlib.sha256,
        ).hexdigest()

        with patch.object(service, "PADDLE_WEBHOOK_SECRET", secret):
            service.verify_paddle_webhook_signature(raw_body, f"ts={timestamp};h1={signature}")

    def test_verify_paddle_webhook_signature_rejects_invalid_signature(self) -> None:
        raw_body = b'{"event_id":"evt_123"}'

        with patch.object(service, "PADDLE_WEBHOOK_SECRET", "whsec_test_secret"):
            with self.assertRaises(HTTPException) as context:
                service.verify_paddle_webhook_signature(raw_body, "ts=1718400000;h1=bad_signature")

        self.assertEqual(context.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
