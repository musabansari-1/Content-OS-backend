from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuthUser:
    id: int
    email: str
    display_name: str
    created_at: datetime


@dataclass(frozen=True)
class AuthUserCredentials:
    id: int
    email: str
    password_hash: str
    display_name: str
    created_at: datetime


@dataclass(frozen=True)
class AuthSession:
    access_token: str
    user: AuthUser
