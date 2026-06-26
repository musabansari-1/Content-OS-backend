from app.auth.domain import AuthSession, AuthUser
from app.auth.dependencies import auth_service
from app.auth.types import (
    AuthResponse,
    ForgotPasswordRequest,
    GoogleAuthRequest,
    LoginRequest,
    PasswordResetRequestResponse,
    RegisterResponse,
    RegisterRequest,
    ResetPasswordConfirmRequest,
    UserResponse,
    VerifyEmailRequestResponse,
)


def _to_user_response(user: AuthUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
        email_verified=bool(user.email_verified_at),
        email_verified_at=user.email_verified_at,
    )


def _to_auth_response(session: AuthSession) -> AuthResponse:
    return AuthResponse(
        access_token=session.access_token,
        user=_to_user_response(session.user),
        email_verification_required=session.email_verification_required,
        email_verification_sent=session.email_verification_sent,
        email_verification_preview_url=session.email_verification_preview_url,
    )


def get_current_user_profile(current_user: AuthUser) -> UserResponse:
    return _to_user_response(current_user)


def register_user(
    request: RegisterRequest,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> RegisterResponse:
    return RegisterResponse(**auth_service.register(request, ip_address=ip_address, user_agent=user_agent))


def login_user(
    request: LoginRequest,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuthSession:
    return auth_service.login(request, ip_address=ip_address, user_agent=user_agent)


def login_with_google_user(
    request: GoogleAuthRequest,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuthSession:
    return auth_service.login_with_google(request, ip_address=ip_address, user_agent=user_agent)


def refresh_user(
    refresh_token: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuthSession:
    return auth_service.refresh(refresh_token, ip_address=ip_address, user_agent=user_agent)


def to_auth_response(session: AuthSession) -> AuthResponse:
    return _to_auth_response(session)


def request_email_verification(current_user: AuthUser) -> VerifyEmailRequestResponse:
    payload = auth_service.request_email_verification(current_user.id)
    return VerifyEmailRequestResponse(**payload)


def confirm_email_verification(
    token: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuthSession:
    return auth_service.verify_email(token, ip_address=ip_address, user_agent=user_agent)


def request_password_reset(request: ForgotPasswordRequest) -> PasswordResetRequestResponse:
    payload = auth_service.request_password_reset(request)
    return PasswordResetRequestResponse(**payload)


def confirm_password_reset(request: ResetPasswordConfirmRequest) -> UserResponse:
    return _to_user_response(auth_service.reset_password(request))
