from app.auth.dependencies import auth_service
from app.auth.types import AuthResponse, LoginRequest, RegisterRequest, UserResponse


def get_current_user_profile(current_user: UserResponse) -> UserResponse:
    return current_user


def register_user(request: RegisterRequest) -> AuthResponse:
    return auth_service.register(request)


def login_user(request: LoginRequest) -> AuthResponse:
    return auth_service.login(request)
