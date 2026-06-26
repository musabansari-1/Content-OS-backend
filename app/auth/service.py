import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token

from app.auth.notifications import (
    build_email_verification_url,
    build_password_reset_url,
    send_auth_email,
)
from app.auth.domain import AuthSession, AuthUser
from app.auth.repository import UserRepository
from app.auth.security import (
    EMAIL_VERIFICATION_TTL_SECONDS,
    PASSWORD_RESET_TTL_SECONDS,
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
from app.auth.types import ForgotPasswordRequest, GoogleAuthRequest, LoginRequest, RegisterRequest, ResetPasswordConfirmRequest
from app.core.config import env


logger = logging.getLogger(__name__)
GOOGLE_CLIENT_ID = (env("GOOGLE_CLIENT_ID", "") or "").strip()


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


def _validate_new_password(password: str) -> str:
    normalized = (password or "").strip()
    if len(normalized) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")
    return normalized


class AuthService:
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    def register(
        self,
        request: RegisterRequest,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        email = _normalize_email(request.email)
        _validate_email(email)
        existing_user = self.repository.get_by_email(email)
        if existing_user:
            if not existing_user.is_active:
                raise HTTPException(status_code=403, detail="This account has been disabled.")
            if existing_user.email_verified_at is None:
                verification_preview_url = self._issue_email_verification(existing_user.id, existing_user.email)
                return self._build_registration_response(
                    message="This email is already registered but still unverified. We sent you a fresh verification link.",
                    email_verification_preview_url=verification_preview_url,
                )
            raise HTTPException(status_code=409, detail="Email is already registered.")

        password = _validate_new_password(request.password)

        display_name = (request.display_name or request.email.split("@")[0]).strip()
        user = self.repository.create_user(
            email,
            hash_password(password),
            display_name or "Creator",
        )
        verification_preview_url = self._issue_email_verification(user.id, user.email)
        return self._build_registration_response(
            message="Check your email to verify your account. Once you confirm it, we will sign you in automatically.",
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
        if existing_user.email_verified_at is None:
            verification_preview_url = self._issue_email_verification(existing_user.id, existing_user.email)
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Verify your email before logging in. We sent you a fresh verification link.",
                    "email_verification_required": True,
                    "email_verification_sent": True,
                    "email_verification_preview_url": verification_preview_url,
                },
            )

        user = self.repository.get_by_id(existing_user.id)
        return self._start_session(
            user,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def login_with_google(
        self,
        request: GoogleAuthRequest,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSession:
        if not GOOGLE_CLIENT_ID:
            raise HTTPException(status_code=503, detail="Google sign-in is not configured.")

        try:
            token_payload = google_id_token.verify_oauth2_token(
                request.id_token,
                GoogleRequest(),
                GOOGLE_CLIENT_ID,
            )
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid Google sign-in token.")
        provider_user_id = str(token_payload.get("sub") or "").strip()
        email = _normalize_email(str(token_payload.get("email") or ""))
        email_verified = bool(token_payload.get("email_verified"))
        display_name = str(token_payload.get("name") or token_payload.get("given_name") or email.split("@")[0] or "Creator").strip()

        if not provider_user_id or not email:
            raise HTTPException(status_code=400, detail="Google did not return a valid user profile.")

        identity = self.repository.get_auth_identity(provider="google", provider_user_id=provider_user_id)
        if identity:
            user = self.repository.get_by_id(identity.user_id)
            if not user:
                raise HTTPException(status_code=401, detail="Google account is linked to a missing user.")
            if not user.is_active:
                raise HTTPException(status_code=403, detail="This account has been disabled.")
            return self._start_session(user, ip_address=ip_address, user_agent=user_agent)

        existing_user = self.repository.get_by_email(email)
        if existing_user:
            user = self.repository.get_by_id(existing_user.id)
            if not user or not user.is_active:
                raise HTTPException(status_code=403, detail="This account has been disabled.")
        else:
            placeholder_password = hash_password(generate_opaque_token_secret())
            user = self.repository.create_user(
                email,
                placeholder_password,
                display_name or "Creator",
            )

        self.repository.create_auth_identity(
            user_id=user.id,
            provider="google",
            provider_user_id=provider_user_id,
            email=email,
        )

        if email_verified and not user.email_verified_at:
            user = self.repository.mark_user_email_verified(user.id)

        return self._start_session(user, ip_address=ip_address, user_agent=user_agent)

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

    def verify_email(
        self,
        token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSession:
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
        return self._start_session(user, ip_address=ip_address, user_agent=user_agent)

    def request_password_reset(self, request: ForgotPasswordRequest) -> dict:
        email = _normalize_email(request.email)
        _validate_email(email)
        user = self.repository.get_by_email(email)
        if not user or not user.is_active:
            return {
                "message": "If an account exists for that email, a reset link has been sent.",
                "password_reset_sent": False,
                "password_reset_preview_url": None,
            }

        preview_url = self._issue_password_reset(user.id, user.email)
        return {
            "message": "If an account exists for that email, a reset link has been sent.",
            "password_reset_sent": True,
            "password_reset_preview_url": preview_url,
        }

    def reset_password(self, request: ResetPasswordConfirmRequest) -> AuthUser:
        password = _validate_new_password(request.password)
        parsed = parse_scoped_token(request.token)
        if not parsed:
            raise HTTPException(status_code=400, detail="Invalid password reset token.")
        token_id, token_secret = parsed
        record = self.repository.get_password_reset_token_by_id(token_id)
        if not record or record.used_at or _is_expired(record.expires_at):
            raise HTTPException(status_code=400, detail="Password reset token is invalid or expired.")
        if hash_opaque_token(token_secret) != record.token_hash:
            raise HTTPException(status_code=400, detail="Password reset token is invalid or expired.")
        self.repository.mark_password_reset_token_used(token_id)
        user = self.repository.update_user_password(record.user_id, hash_password(password))
        self.repository.revoke_all_auth_sessions_for_user(record.user_id)
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

    def _build_registration_response(
        self,
        *,
        message: str,
        email_verification_preview_url: str | None,
    ) -> dict:
        return {
            "message": message,
            "email_verification_required": True,
            "email_verification_sent": True,
            "email_verification_preview_url": email_verification_preview_url,
        }

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
        return send_auth_email(
            email=email,
            subject="Verify your ContentOS email",
            action="verify_email",
            action_url=build_email_verification_url(token),
        )

    def _issue_password_reset(self, user_id: int, email: str) -> str | None:
        reset_secret = generate_opaque_token_secret()
        record = self.repository.create_password_reset_token(
            user_id=user_id,
            token_hash=hash_opaque_token(reset_secret),
            expires_at=_now() + timedelta(seconds=PASSWORD_RESET_TTL_SECONDS),
        )
        token = build_scoped_token(record.id, reset_secret)
        return send_auth_email(
            email=email,
            subject="Reset your ContentOS password",
            action="reset_password",
            action_url=build_password_reset_url(token),
        )
