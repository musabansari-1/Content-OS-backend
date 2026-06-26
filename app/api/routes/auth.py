from fastapi import APIRouter, Depends, Request, Response

from app.auth.domain import AuthUser
from app.auth.dependencies import auth_service, require_current_user
from app.auth.notifications import FRONTEND_BASE_URL
from app.auth.security import REFRESH_SESSION_TTL_SECONDS
from app.auth.types import (
    AuthResponse,
    ForgotPasswordRequest,
    GoogleAuthRequest,
    LoginRequest,
    MessageResponse,
    PasswordResetRequestResponse,
    RegisterRequest,
    ResetPasswordConfirmRequest,
    UserResponse,
    VerifyEmailConfirmRequest,
    VerifyEmailRequestResponse,
)
from app.services.auth_workflows import (
    confirm_password_reset,
    confirm_email_verification,
    get_current_user_profile,
    login_with_google_user,
    login_user,
    request_password_reset,
    refresh_user,
    register_user,
    request_email_verification,
    to_auth_response,
)


router = APIRouter()
REFRESH_COOKIE_NAME = "contentos_refresh"
REFRESH_COOKIE_SECURE = not FRONTEND_BASE_URL.startswith("http://localhost") and not FRONTEND_BASE_URL.startswith("http://127.0.0.1")


def _apply_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=REFRESH_COOKIE_SECURE,
        samesite="lax",
        max_age=REFRESH_SESSION_TTL_SECONDS,
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        httponly=True,
        secure=REFRESH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/me", response_model=UserResponse)
def get_me(current_user: AuthUser = Depends(require_current_user)):
    return get_current_user_profile(current_user)


@router.post("/auth/register", response_model=AuthResponse)
def register(request: RegisterRequest, response: Response, raw_request: Request):
    session = register_user(
        request,
        ip_address=_client_ip(raw_request),
        user_agent=raw_request.headers.get("user-agent"),
    )
    _apply_refresh_cookie(response, session.refresh_token)
    return to_auth_response(session)


@router.post("/auth/login", response_model=AuthResponse)
def login(request: LoginRequest, response: Response, raw_request: Request):
    session = login_user(
        request,
        ip_address=_client_ip(raw_request),
        user_agent=raw_request.headers.get("user-agent"),
    )
    _apply_refresh_cookie(response, session.refresh_token)
    return to_auth_response(session)


@router.post("/auth/google", response_model=AuthResponse)
def login_with_google(request: GoogleAuthRequest, response: Response, raw_request: Request):
    session = login_with_google_user(
        request,
        ip_address=_client_ip(raw_request),
        user_agent=raw_request.headers.get("user-agent"),
    )
    _apply_refresh_cookie(response, session.refresh_token)
    return to_auth_response(session)


@router.post("/auth/refresh", response_model=AuthResponse)
def refresh(response: Response, raw_request: Request):
    refresh_token = raw_request.cookies.get(REFRESH_COOKIE_NAME)
    session = refresh_user(
        refresh_token or "",
        ip_address=_client_ip(raw_request),
        user_agent=raw_request.headers.get("user-agent"),
    )
    _apply_refresh_cookie(response, session.refresh_token)
    return to_auth_response(session)


@router.post("/auth/logout", response_model=MessageResponse)
def logout(response: Response, raw_request: Request):
    auth_service.logout(raw_request.cookies.get(REFRESH_COOKIE_NAME))
    _clear_refresh_cookie(response)
    return MessageResponse(message="Logged out.")


@router.post("/auth/logout-all", response_model=MessageResponse)
def logout_all(
    response: Response,
    current_user: AuthUser = Depends(require_current_user),
):
    auth_service.logout_all(current_user.id)
    _clear_refresh_cookie(response)
    return MessageResponse(message="Logged out of all sessions.")


@router.post("/auth/verify-email/request", response_model=VerifyEmailRequestResponse)
def resend_email_verification(current_user: AuthUser = Depends(require_current_user)):
    return request_email_verification(current_user)


@router.post("/auth/verify-email/confirm", response_model=UserResponse)
def verify_email_confirm(request: VerifyEmailConfirmRequest):
    return confirm_email_verification(request.token)


@router.post("/auth/password/forgot", response_model=PasswordResetRequestResponse)
def forgot_password(request: ForgotPasswordRequest):
    return request_password_reset(request)


@router.post("/auth/password/reset", response_model=UserResponse)
def reset_password(request: ResetPasswordConfirmRequest):
    return confirm_password_reset(request)
