import logging
from urllib.parse import quote

from app.core.config import env


logger = logging.getLogger(__name__)
FRONTEND_BASE_URL = (env("FRONTEND_BASE_URL", "http://localhost:3000") or "http://localhost:3000").rstrip("/")
PUBLIC_BASE_URL = (env("PUBLIC_BASE_URL", "http://localhost:8000") or "http://localhost:8000").rstrip("/")
AUTH_EMAIL_PREVIEW_ENABLED = (
    (env("AUTH_EMAIL_VERIFY_SHOW_TOKEN", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    if (env("AUTH_EMAIL_VERIFY_SHOW_TOKEN", "") or "").strip()
    else FRONTEND_BASE_URL.startswith("http://localhost") or PUBLIC_BASE_URL.startswith("http://localhost")
)


def build_email_verification_url(token: str) -> str:
    return f"{FRONTEND_BASE_URL}/verify-email?token={quote(token, safe='')}"


def build_password_reset_url(token: str) -> str:
    return f"{FRONTEND_BASE_URL}/reset-password?token={quote(token, safe='')}"


def send_auth_email(*, email: str, subject: str, action: str, action_url: str) -> str | None:
    logger.info("Auth email '%s' for %s (%s): %s", action, email, subject, action_url)
    if AUTH_EMAIL_PREVIEW_ENABLED:
        return action_url
    return None
