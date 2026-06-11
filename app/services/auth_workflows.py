from app.auth.domain import AuthSession, AuthUser
from app.auth.dependencies import auth_service
from app.auth.types import AuthResponse, LoginRequest, RegisterRequest, UserResponse


def _to_user_response(user: AuthUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
    )


def _to_auth_response(session: AuthSession) -> AuthResponse:
    return AuthResponse(
        access_token=session.access_token,
        user=_to_user_response(session.user),
    )


def get_current_user_profile(current_user: AuthUser) -> UserResponse:
    return _to_user_response(current_user)


def register_user(request: RegisterRequest) -> AuthResponse:
    return _to_auth_response(auth_service.register(request))


def login_user(request: LoginRequest) -> AuthResponse:
    return _to_auth_response(auth_service.login(request))
