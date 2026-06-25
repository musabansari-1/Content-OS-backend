from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: str
    created_at: datetime
    email_verified: bool
    email_verified_at: Optional[datetime] = None


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    email_verification_required: bool = False
    email_verification_sent: bool = False
    email_verification_preview_url: Optional[str] = None


class VerifyEmailConfirmRequest(BaseModel):
    token: str


class VerifyEmailRequestResponse(BaseModel):
    email_verification_required: bool
    email_verification_sent: bool
    email_verification_preview_url: Optional[str] = None


class MessageResponse(BaseModel):
    message: str
