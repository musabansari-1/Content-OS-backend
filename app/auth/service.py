import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import HTTPException

from app.auth.domain import AuthSession, AuthUser
from app.auth.repository import UserRepository
from app.auth.security import (
    EMAIL_VERIFICATION_TTL_SECONDS,
    REFRESH_SESSION_TTL_SECONDS,
    build_scoped_token,
    create_access_token,
    decode_access_token,
    generate_opaque_token_secret,
    hash_opaque_token,
    hash_password,
    parse_scoped_token,
    verify_password,
)
from app.auth.types import LoginRequest, RegisterRequest
from app.core.config import env


logger = logging.getLogger(__name__)
FRONTEND_BASE_URL = (env("FRONTEND_BASE_URL", "http://localhost:3000") or "http://localhost:3000").rstrip("/")
PUBLIC_BASE_URL = (env("PUBLIC_BASE_URL", "http://localhost:8000") or "http://localhost:8000").rstrip("/")
EMAIL_VERIFICATION_PREVIEW_ENABLED = (
    (env("AUTH_EMAIL_VERIFY_SHOW_TOKEN", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    if (env("AUTH_EMAIL_VERIFY_SHOW_TOKEN", "") or "").strip()
    else FRONTEND_BASE_URL.startswith("http://localhost") or PUBLIC_BASE_URL.startswith("http://localhost")
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _validate_email(email: str) -> None:
    local, separator, domain = email.partition("@")
    if not separator or not local or not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")


def _is_expired(value: datetime) -> bool:
    return value <= _now()


class AuthService:
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    def register(
        self,
        request: RegisterRequest,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSession:
        email = _normalize_email(request.email)
        _validate_email(email)
        existing_user = self.repository.get_by_email(email)
        if existing_user:
            raise HTTPException(status_code=409, detail="Email is already registered.")

        password = request.password.strip()
        if len(password) < 8:
            raise HTTPException(
                status_code=400,
                detail="Password must be at least 8 characters long.",
            )

        display_name = (request.display_name or request.email.split("@")[0]).strip()
        user = self.repository.create_user(
            email,
            hash_password(password),
            display_name or "Creator",
        )
        verification_preview_url = self._issue_email_verification(user.id, user.email)
        return self._start_session(
            user,
            ip_address=ip_address,
            user_agent=user_agent,
            email_verification_sent=True,
            email_verification_preview_url=verification_preview_url,
        )

    def login(
        self,
        request: LoginRequest,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSession:
        email = _normalize_email(request.email)
        _validate_email(email)
        existing_user = self.repository.get_by_email(email)
        if not existing_user or not verify_password(
            request.password,
            existing_user.password_hash,
        ):
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        if not existing_user.is_active:
            raise HTTPException(status_code=403, detail="This account has been disabled.")

        user = self.repository.get_by_id(existing_user.id)
        return self._start_session(
            user,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def refresh(
        self,
        refresh_token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSession:
        session, user = self._get_refresh_session_and_user(refresh_token)
        new_secret = generate_opaque_token_secret()
        self.repository.rotate_auth_session(
            session_id=session.id,
            refresh_token_hash=hash_opaque_token(new_secret),
            expires_at=_now() + timedelta(seconds=REFRESH_SESSION_TTL_SECONDS),
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return AuthSession(
            session_id=session.id,
            access_token=create_access_token(user.id, user.email, session.id),
            refresh_token=build_scoped_token(session.id, new_secret),
            user=user,
            email_verification_required=user.email_verified_at is None,
        )

    def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        parsed = parse_scoped_token(refresh_token)
        if not parsed:
            return
        session_id, token_secret = parsed
        session = self.repository.get_auth_session_by_id(session_id)
        if not session or session.revoked_at or _is_expired(session.expires_at):
            return
        if hash_opaque_token(token_secret) != session.refresh_token_hash:
            return
        self.repository.revoke_auth_session(session_id)

    def logout_all(self, user_id: int) -> None:
        self.repository.revoke_all_auth_sessions_for_user(user_id)

    def request_email_verification(self, user_id: int) -> dict:
        user = self.repository.get_by_id(user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=404, detail="User not found.")
        if user.email_verified_at:
            return {
                "email_verification_required": False,
                "email_verification_sent": False,
                "email_verification_preview_url": None,
            }
        preview_url = self._issue_email_verification(user.id, user.email)
        return {
            "email_verification_required": True,
            "email_verification_sent": True,
            "email_verification_preview_url": preview_url,
        }

    def verify_email(self, token: str) -> AuthUser:
        parsed = parse_scoped_token(token)
        if not parsed:
            raise HTTPException(status_code=400, detail="Invalid verification token.")
        token_id, token_secret = parsed
        record = self.repository.get_email_verification_token_by_id(token_id)
        if not record or record.used_at or _is_expired(record.expires_at):
            raise HTTPException(status_code=400, detail="Verification token is invalid or expired.")
        if hash_opaque_token(token_secret) != record.token_hash:
            raise HTTPException(status_code=400, detail="Verification token is invalid or expired.")
        self.repository.mark_email_verification_token_used(token_id)
        user = self.repository.mark_user_email_verified(record.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        return user

    def get_current_user(self, token: str) -> AuthUser:
        payload = decode_access_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token.")

        session_id_raw = payload.get("sid")
        try:
            session_id = int(session_id_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=401, detail="Invalid or expired token.")

        session = self.repository.get_auth_session_by_id(session_id)
        if not session or session.revoked_at or _is_expired(session.expires_at):
            raise HTTPException(status_code=401, detail="Invalid or expired token.")

        user = self.repository.get_by_id(int(payload["sub"]))
        if not user:
            raise HTTPException(status_code=401, detail="User no longer exists.")
        if not user.is_active:
            raise HTTPException(status_code=403, detail="This account has been disabled.")
        if session.user_id != user.id:
            raise HTTPException(status_code=401, detail="Invalid or expired token.")

        return user

    def _start_session(
        self,
        user: AuthUser,
        *,
        ip_address: str | None,
        user_agent: str | None,
        email_verification_sent: bool = False,
        email_verification_preview_url: str | None = None,
    ) -> AuthSession:
        refresh_secret = generate_opaque_token_secret()
        session = self.repository.create_auth_session(
            user_id=user.id,
            refresh_token_hash=hash_opaque_token(refresh_secret),
            expires_at=_now() + timedelta(seconds=REFRESH_SESSION_TTL_SECONDS),
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return AuthSession(
            session_id=session.id,
            access_token=create_access_token(user.id, user.email, session.id),
            refresh_token=build_scoped_token(session.id, refresh_secret),
            user=user,
            email_verification_required=user.email_verified_at is None,
            email_verification_sent=email_verification_sent,
            email_verification_preview_url=email_verification_preview_url,
        )

    def _get_refresh_session_and_user(self, refresh_token: str):
        parsed = parse_scoped_token(refresh_token)
        if not parsed:
            raise HTTPException(status_code=401, detail="Invalid refresh token.")
        session_id, token_secret = parsed
        session = self.repository.get_auth_session_by_id(session_id)
        if not session or session.revoked_at or _is_expired(session.expires_at):
            raise HTTPException(status_code=401, detail="Invalid refresh token.")
        if hash_opaque_token(token_secret) != session.refresh_token_hash:
            self.repository.revoke_auth_session(session_id)
            raise HTTPException(status_code=401, detail="Invalid refresh token.")

        user = self.repository.get_by_id(session.user_id)
        if not user or not user.is_active:
            self.repository.revoke_auth_session(session_id)
            raise HTTPException(status_code=401, detail="Invalid refresh token.")
        return session, user

    def _issue_email_verification(self, user_id: int, email: str) -> str | None:
        verification_secret = generate_opaque_token_secret()
        record = self.repository.create_email_verification_token(
            user_id=user_id,
            token_hash=hash_opaque_token(verification_secret),
            expires_at=_now() + timedelta(seconds=EMAIL_VERIFICATION_TTL_SECONDS),
        )
        token = build_scoped_token(record.id, verification_secret)
        preview_url = f"{FRONTEND_BASE_URL}/verify-email?token={quote(token, safe='')}"
        logger.info("Email verification requested for %s. Preview URL: %s", email, preview_url)
        if EMAIL_VERIFICATION_PREVIEW_ENABLED:
            return preview_url
        return None
