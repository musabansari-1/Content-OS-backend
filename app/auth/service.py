from fastapi import HTTPException

from app.auth.domain import AuthSession, AuthUser
from app.auth.repository import UserRepository
from app.auth.security import create_access_token, decode_access_token, hash_password, verify_password
from app.auth.types import LoginRequest, RegisterRequest


class AuthService:
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    def register(self, request: RegisterRequest) -> AuthSession:
        existing_user = self.repository.get_by_email(request.email)
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
            request.email,
            hash_password(password),
            display_name or "Creator",
        )

        return AuthSession(
            access_token=create_access_token(user.id, user.email),
            user=user,
        )

    def login(self, request: LoginRequest) -> AuthSession:
        existing_user = self.repository.get_by_email(request.email)
        if not existing_user or not verify_password(
            request.password,
            existing_user.password_hash,
        ):
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        user = self.repository.get_by_id(existing_user.id)
        return AuthSession(
            access_token=create_access_token(user.id, user.email),
            user=user,
        )

    def get_current_user(self, token: str) -> AuthUser:
        payload = decode_access_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token.")

        user = self.repository.get_by_id(int(payload["sub"]))
        if not user:
            raise HTTPException(status_code=401, detail="User no longer exists.")

        return user
