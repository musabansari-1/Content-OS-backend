from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class AuthUser:
    id: int
    email: str
    display_name: str
    created_at: datetime
    email_verified_at: Optional[datetime]
    is_active: bool


@dataclass(frozen=True)
class AuthUserCredentials:
    id: int
    email: str
    password_hash: str
    display_name: str
    created_at: datetime
    email_verified_at: Optional[datetime]
    is_active: bool


@dataclass(frozen=True)
class AuthSession:
    session_id: int
    access_token: str
    refresh_token: str
    user: AuthUser
    email_verification_required: bool = False
    email_verification_sent: bool = False
    email_verification_preview_url: Optional[str] = None


@dataclass(frozen=True)
class AuthSessionRecord:
    id: int
    user_id: int
    refresh_token_hash: str
    expires_at: datetime
    revoked_at: Optional[datetime]
    last_used_at: Optional[datetime]
    user_agent: Optional[str]
    ip_address: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class EmailVerificationTokenRecord:
    id: int
    user_id: int
    token_hash: str
    expires_at: datetime
    used_at: Optional[datetime]
    created_at: datetime


@dataclass(frozen=True)
class PasswordResetTokenRecord:
    id: int
    user_id: int
    token_hash: str
    expires_at: datetime
    used_at: Optional[datetime]
    created_at: datetime
