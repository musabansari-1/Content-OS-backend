import unittest
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

import app.auth.notifications as notifications_module
import app.auth.service as service_module
from app.auth.domain import (
    AuthIdentityRecord,
    AuthSessionRecord,
    AuthUser,
    AuthUserCredentials,
    EmailVerificationTokenRecord,
    PasswordResetTokenRecord,
)
from app.auth.security import hash_password
from app.auth.service import AuthService
from app.auth.types import ForgotPasswordRequest, GoogleAuthRequest, LoginRequest, RegisterRequest, ResetPasswordConfirmRequest


class FakeRepository:
    def __init__(self) -> None:
        self._users: dict[int, AuthUser] = {}
        self._credentials_by_email: dict[str, AuthUserCredentials] = {}
        self._sessions: dict[int, AuthSessionRecord] = {}
        self._identities: dict[tuple[str, str], AuthIdentityRecord] = {}
        self._verification_tokens: dict[int, EmailVerificationTokenRecord] = {}
        self._password_reset_tokens: dict[int, PasswordResetTokenRecord] = {}
        self._next_user_id = 1
        self._next_session_id = 1
        self._next_identity_id = 1
        self._next_verification_token_id = 1
        self._next_password_reset_token_id = 1

    def create_user(self, email: str, password_hash: str, display_name: str) -> AuthUser:
        user = AuthUser(
            id=self._next_user_id,
            email=email,
            display_name=display_name,
            created_at=datetime.now(timezone.utc),
            email_verified_at=None,
            is_active=True,
        )
        credentials = AuthUserCredentials(
            id=user.id,
            email=user.email,
            password_hash=password_hash,
            display_name=user.display_name,
            created_at=user.created_at,
            email_verified_at=None,
            is_active=True,
        )
        self._users[user.id] = user
        self._credentials_by_email[email] = credentials
        self._next_user_id += 1
        return user

    def get_by_email(self, email: str):
        return self._credentials_by_email.get(email)

    def get_by_id(self, user_id: int):
        return self._users.get(user_id)

    def create_auth_identity(self, *, user_id: int, provider: str, provider_user_id: str, email: str | None):
        existing = self._identities.get((provider, provider_user_id))
        if existing:
            updated = replace(existing, user_id=user_id, email=email)
            self._identities[(provider, provider_user_id)] = updated
            return updated
        record = AuthIdentityRecord(
            id=self._next_identity_id,
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            email=email,
            created_at=datetime.now(timezone.utc),
        )
        self._identities[(provider, provider_user_id)] = record
        self._next_identity_id += 1
        return record

    def get_auth_identity(self, *, provider: str, provider_user_id: str):
        return self._identities.get((provider, provider_user_id))

    def get_auth_identity_by_email(self, *, provider: str, email: str):
        for record in self._identities.values():
            if record.provider == provider and record.email == email:
                return record
        return None

    def create_auth_session(self, *, user_id: int, refresh_token_hash: str, expires_at, user_agent, ip_address):
        record = AuthSessionRecord(
            id=self._next_session_id,
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            revoked_at=None,
            last_used_at=None,
            user_agent=user_agent,
            ip_address=ip_address,
            created_at=datetime.now(timezone.utc),
        )
        self._sessions[record.id] = record
        self._next_session_id += 1
        return record

    def get_auth_session_by_id(self, session_id: int):
        return self._sessions.get(session_id)

    def rotate_auth_session(self, *, session_id: int, refresh_token_hash: str, expires_at, user_agent, ip_address):
        record = self._sessions[session_id]
        self._sessions[session_id] = replace(
            record,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            last_used_at=datetime.now(timezone.utc),
            user_agent=user_agent or record.user_agent,
            ip_address=ip_address or record.ip_address,
        )

    def revoke_auth_session(self, session_id: int) -> None:
        record = self._sessions.get(session_id)
        if record:
            self._sessions[session_id] = replace(record, revoked_at=record.revoked_at or datetime.now(timezone.utc))

    def revoke_all_auth_sessions_for_user(self, user_id: int) -> None:
        for session_id, record in list(self._sessions.items()):
            if record.user_id == user_id and not record.revoked_at:
                self._sessions[session_id] = replace(record, revoked_at=datetime.now(timezone.utc))

    def create_email_verification_token(self, *, user_id: int, token_hash: str, expires_at):
        for token_id, record in list(self._verification_tokens.items()):
            if record.user_id == user_id and record.used_at is None:
                self._verification_tokens[token_id] = replace(record, used_at=datetime.now(timezone.utc))
        record = EmailVerificationTokenRecord(
            id=self._next_verification_token_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=None,
            created_at=datetime.now(timezone.utc),
        )
        self._verification_tokens[record.id] = record
        self._next_verification_token_id += 1
        return record

    def get_email_verification_token_by_id(self, token_id: int):
        return self._verification_tokens.get(token_id)

    def mark_email_verification_token_used(self, token_id: int) -> None:
        record = self._verification_tokens[token_id]
        self._verification_tokens[token_id] = replace(record, used_at=record.used_at or datetime.now(timezone.utc))

    def mark_user_email_verified(self, user_id: int):
        user = self._users[user_id]
        verified_at = user.email_verified_at or datetime.now(timezone.utc)
        updated_user = replace(user, email_verified_at=verified_at)
        self._users[user_id] = updated_user
        credentials = self._credentials_by_email[user.email]
        self._credentials_by_email[user.email] = replace(credentials, email_verified_at=verified_at)
        return updated_user

    def create_password_reset_token(self, *, user_id: int, token_hash: str, expires_at):
        for token_id, record in list(self._password_reset_tokens.items()):
            if record.user_id == user_id and record.used_at is None:
                self._password_reset_tokens[token_id] = replace(record, used_at=datetime.now(timezone.utc))
        record = PasswordResetTokenRecord(
            id=self._next_password_reset_token_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=None,
            created_at=datetime.now(timezone.utc),
        )
        self._password_reset_tokens[record.id] = record
        self._next_password_reset_token_id += 1
        return record

    def get_password_reset_token_by_id(self, token_id: int):
        return self._password_reset_tokens.get(token_id)

    def mark_password_reset_token_used(self, token_id: int) -> None:
        record = self._password_reset_tokens[token_id]
        self._password_reset_tokens[token_id] = replace(record, used_at=record.used_at or datetime.now(timezone.utc))

    def update_user_password(self, user_id: int, password_hash: str) -> AuthUser | None:
        user = self._users[user_id]
        credentials = self._credentials_by_email[user.email]
        self._credentials_by_email[user.email] = replace(credentials, password_hash=password_hash)
        return self._users[user_id]


class AuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.service = AuthService(repository=self.repository)
        self._old_preview_flag = notifications_module.AUTH_EMAIL_PREVIEW_ENABLED
        self._old_frontend = notifications_module.FRONTEND_BASE_URL
        self._old_provider = notifications_module.AUTH_EMAIL_PROVIDER
        notifications_module.AUTH_EMAIL_PREVIEW_ENABLED = True
        notifications_module.AUTH_EMAIL_PROVIDER = "resend"
        notifications_module.FRONTEND_BASE_URL = "http://localhost:3000"
        self._old_google_client_id = service_module.GOOGLE_CLIENT_ID
        service_module.GOOGLE_CLIENT_ID = "google-client-id.apps.googleusercontent.com"

    def tearDown(self) -> None:
        notifications_module.AUTH_EMAIL_PREVIEW_ENABLED = self._old_preview_flag
        notifications_module.FRONTEND_BASE_URL = self._old_frontend
        notifications_module.AUTH_EMAIL_PROVIDER = self._old_provider
        service_module.GOOGLE_CLIENT_ID = self._old_google_client_id

    def _register_and_extract_token(self, email: str = "user@example.com") -> tuple[dict, str]:
        payload = self.service.register(
            RegisterRequest(email=email, password="password123", display_name="User"),
        )
        parsed = urlparse(payload["email_verification_preview_url"] or "")
        token = parse_qs(parsed.query)["token"][0]
        return payload, token

    def _verify_registered_user(self, email: str = "user@example.com"):
        _, token = self._register_and_extract_token(email)
        return self.service.verify_email(token)

    def test_register_requires_verification_and_returns_preview(self) -> None:
        payload = self.service.register(
            RegisterRequest(email="  User@Example.com ", password="password123", display_name="User"),
            ip_address="127.0.0.1",
            user_agent="unittest",
        )

        self.assertEqual(self.repository.get_by_email("user@example.com").email, "user@example.com")
        self.assertEqual(
            payload["message"],
            "Check your email to verify your account. Once you confirm it, we will sign you in automatically.",
        )
        self.assertTrue(payload["email_verification_required"])
        self.assertTrue(payload["email_verification_sent"])
        self.assertIn("verify-email?token=", payload["email_verification_preview_url"] or "")

    def test_register_existing_unverified_email_resends_verification(self) -> None:
        first_payload = self.service.register(
            RegisterRequest(email="user@example.com", password="password123", display_name="User"),
        )

        second_payload = self.service.register(
            RegisterRequest(email="user@example.com", password="password123", display_name="User"),
        )

        self.assertNotEqual(
            first_payload["email_verification_preview_url"],
            second_payload["email_verification_preview_url"],
        )
        self.assertIn("fresh verification link", second_payload["message"])

    def test_login_rejects_invalid_password(self) -> None:
        self.repository.create_user("user@example.com", hash_password("password123"), "User")

        with self.assertRaises(HTTPException) as exc:
            self.service.login(LoginRequest(email="user@example.com", password="nope"))

        self.assertEqual(exc.exception.status_code, 401)

    def test_login_rejects_unverified_user_and_resends_verification(self) -> None:
        self.service.register(
            RegisterRequest(email="user@example.com", password="password123", display_name="User"),
        )

        with self.assertRaises(HTTPException) as exc:
            self.service.login(LoginRequest(email="user@example.com", password="password123"))

        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(exc.exception.detail["email_verification_required"], True)
        self.assertEqual(exc.exception.detail["email_verification_sent"], True)
        self.assertIn("verify-email?token=", exc.exception.detail["email_verification_preview_url"] or "")

    def test_refresh_rotates_refresh_token_and_invalidates_old_one(self) -> None:
        session = self._verify_registered_user()

        refreshed = self.service.refresh(session.refresh_token, ip_address="127.0.0.1", user_agent="refreshed")

        self.assertNotEqual(refreshed.refresh_token, session.refresh_token)
        self.assertEqual(refreshed.session_id, session.session_id)
        with self.assertRaises(HTTPException) as exc:
            self.service.refresh(session.refresh_token)
        self.assertEqual(exc.exception.status_code, 401)

    def test_logout_revokes_session_and_access_token(self) -> None:
        session = self._verify_registered_user()

        self.service.logout(session.refresh_token)

        with self.assertRaises(HTTPException) as exc:
            self.service.get_current_user(session.access_token)
        self.assertEqual(exc.exception.status_code, 401)

    def test_verify_email_marks_user_verified_and_starts_session(self) -> None:
        _, token = self._register_and_extract_token()

        session = self.service.verify_email(token, ip_address="127.0.0.1", user_agent="verify")

        user = session.user
        self.assertIsNotNone(user.email_verified_at)
        self.assertTrue(session.access_token)
        self.assertTrue(session.refresh_token)
        payload = self.service.request_email_verification(user.id)
        self.assertFalse(payload["email_verification_required"])
        self.assertFalse(payload["email_verification_sent"])

    def test_request_password_reset_issues_preview_link(self) -> None:
        self._verify_registered_user()

        payload = self.service.request_password_reset(ForgotPasswordRequest(email="user@example.com"))

        self.assertTrue(payload["password_reset_sent"])
        self.assertIn("reset-password?token=", payload["password_reset_preview_url"] or "")

    def test_reset_password_updates_credentials_and_revokes_sessions(self) -> None:
        session = self._verify_registered_user()
        payload = self.service.request_password_reset(ForgotPasswordRequest(email="user@example.com"))
        parsed = urlparse(payload["password_reset_preview_url"] or "")
        token = parse_qs(parsed.query)["token"][0]

        user = self.service.reset_password(ResetPasswordConfirmRequest(token=token, password="newpassword123"))

        self.assertEqual(user.email, "user@example.com")
        with self.assertRaises(HTTPException):
            self.service.refresh(session.refresh_token)
        new_session = self.service.login(LoginRequest(email="user@example.com", password="newpassword123"))
        self.assertTrue(new_session.access_token)

    def test_google_login_creates_user_and_marks_verified(self) -> None:
        original_verify = service_module.google_id_token.verify_oauth2_token
        original_request = service_module.GoogleRequest
        try:
            service_module.GoogleRequest = lambda: object()
            service_module.google_id_token.verify_oauth2_token = lambda token, req, aud: {
                "sub": "google-sub-123",
                "email": "googleuser@example.com",
                "email_verified": True,
                "name": "Google User",
            }

            session = self.service.login_with_google(GoogleAuthRequest(id_token="fake-google-token"))
        finally:
            service_module.google_id_token.verify_oauth2_token = original_verify
            service_module.GoogleRequest = original_request

        self.assertEqual(session.user.email, "googleuser@example.com")
        self.assertIsNotNone(session.user.email_verified_at)
        self.assertIsNotNone(self.repository.get_auth_identity(provider="google", provider_user_id="google-sub-123"))

    def test_google_login_links_existing_email_user(self) -> None:
        existing_user = self.repository.create_user("user@example.com", hash_password("password123"), "User")
        original_verify = service_module.google_id_token.verify_oauth2_token
        original_request = service_module.GoogleRequest
        try:
            service_module.GoogleRequest = lambda: object()
            service_module.google_id_token.verify_oauth2_token = lambda token, req, aud: {
                "sub": "google-sub-456",
                "email": "user@example.com",
                "email_verified": True,
                "name": "User",
            }

            session = self.service.login_with_google(GoogleAuthRequest(id_token="fake-google-token"))
        finally:
            service_module.google_id_token.verify_oauth2_token = original_verify
            service_module.GoogleRequest = original_request

        self.assertEqual(session.user.id, existing_user.id)


if __name__ == "__main__":
    unittest.main()
