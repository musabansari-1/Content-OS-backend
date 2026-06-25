import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

import app.auth.service as service_module
from app.auth.domain import AuthSessionRecord, AuthUser, AuthUserCredentials, EmailVerificationTokenRecord
from app.auth.security import hash_password
from app.auth.service import AuthService
from app.auth.types import LoginRequest, RegisterRequest


class FakeRepository:
    def __init__(self) -> None:
        self._users: dict[int, AuthUser] = {}
        self._credentials_by_email: dict[str, AuthUserCredentials] = {}
        self._sessions: dict[int, AuthSessionRecord] = {}
        self._verification_tokens: dict[int, EmailVerificationTokenRecord] = {}
        self._next_user_id = 1
        self._next_session_id = 1
        self._next_verification_token_id = 1

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


class AuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.service = AuthService(repository=self.repository)
        self._old_preview_flag = service_module.EMAIL_VERIFICATION_PREVIEW_ENABLED
        self._old_frontend = service_module.FRONTEND_BASE_URL
        service_module.EMAIL_VERIFICATION_PREVIEW_ENABLED = True
        service_module.FRONTEND_BASE_URL = "http://localhost:3000"

    def tearDown(self) -> None:
        service_module.EMAIL_VERIFICATION_PREVIEW_ENABLED = self._old_preview_flag
        service_module.FRONTEND_BASE_URL = self._old_frontend

    def test_register_creates_session_and_verification_preview(self) -> None:
        session = self.service.register(
            RegisterRequest(email="  User@Example.com ", password="password123", display_name="User"),
            ip_address="127.0.0.1",
            user_agent="unittest",
        )

        self.assertEqual(session.user.email, "user@example.com")
        self.assertTrue(session.access_token)
        self.assertTrue(session.refresh_token)
        self.assertTrue(session.email_verification_required)
        self.assertTrue(session.email_verification_sent)
        self.assertIn("verify-email?token=", session.email_verification_preview_url or "")

    def test_login_rejects_invalid_password(self) -> None:
        self.repository.create_user("user@example.com", hash_password("password123"), "User")

        with self.assertRaises(HTTPException) as exc:
            self.service.login(LoginRequest(email="user@example.com", password="nope"))

        self.assertEqual(exc.exception.status_code, 401)

    def test_refresh_rotates_refresh_token_and_invalidates_old_one(self) -> None:
        session = self.service.register(RegisterRequest(email="user@example.com", password="password123", display_name="User"))

        refreshed = self.service.refresh(session.refresh_token, ip_address="127.0.0.1", user_agent="refreshed")

        self.assertNotEqual(refreshed.refresh_token, session.refresh_token)
        self.assertEqual(refreshed.session_id, session.session_id)
        with self.assertRaises(HTTPException) as exc:
            self.service.refresh(session.refresh_token)
        self.assertEqual(exc.exception.status_code, 401)

    def test_logout_revokes_session_and_access_token(self) -> None:
        session = self.service.register(RegisterRequest(email="user@example.com", password="password123", display_name="User"))

        self.service.logout(session.refresh_token)

        with self.assertRaises(HTTPException) as exc:
            self.service.get_current_user(session.access_token)
        self.assertEqual(exc.exception.status_code, 401)

    def test_verify_email_marks_user_verified(self) -> None:
        session = self.service.register(RegisterRequest(email="user@example.com", password="password123", display_name="User"))
        parsed = urlparse(session.email_verification_preview_url or "")
        token = parse_qs(parsed.query)["token"][0]

        user = self.service.verify_email(token)

        self.assertIsNotNone(user.email_verified_at)
        payload = self.service.request_email_verification(user.id)
        self.assertFalse(payload["email_verification_required"])
        self.assertFalse(payload["email_verification_sent"])


if __name__ == "__main__":
    unittest.main()
