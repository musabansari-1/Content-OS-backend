import logging
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.core.config import env


logger = logging.getLogger(__name__)
FRONTEND_BASE_URL = (env("FRONTEND_BASE_URL", "http://localhost:3000") or "http://localhost:3000").rstrip("/")
PUBLIC_BASE_URL = (env("PUBLIC_BASE_URL", "http://localhost:8000") or "http://localhost:8000").rstrip("/")
AUTH_EMAIL_PROVIDER = (env("AUTH_EMAIL_PROVIDER", "resend") or "resend").strip().lower()
AUTH_EMAIL_PREVIEW_ENABLED = (
    (env("AUTH_EMAIL_VERIFY_SHOW_TOKEN", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    if (env("AUTH_EMAIL_VERIFY_SHOW_TOKEN", "") or "").strip()
    else FRONTEND_BASE_URL.startswith("http://localhost") or PUBLIC_BASE_URL.startswith("http://localhost")
)
AUTH_EMAIL_FROM = (env("AUTH_EMAIL_FROM", "") or "").strip()
RESEND_API_KEY = (env("RESEND_API_KEY", "") or "").strip()
RESEND_API_BASE_URL = (env("RESEND_API_BASE_URL", "https://api.resend.com") or "https://api.resend.com").rstrip("/")


def build_email_verification_url(token: str) -> str:
    return f"{FRONTEND_BASE_URL}/verify-email?token={quote(token, safe='')}"


def build_password_reset_url(token: str) -> str:
    return f"{FRONTEND_BASE_URL}/reset-password?token={quote(token, safe='')}"


def _build_email_copy(*, action: str, action_url: str) -> tuple[str, str]:
    if action == "verify_email":
        text = (
            "Verify your ContentOS account.\n\n"
            f"Open this link to verify your email and sign in:\n{action_url}\n\n"
            "If you did not create this account, you can ignore this email."
        )
        html = (
            "<p>Verify your ContentOS account.</p>"
            f"<p><a href=\"{action_url}\">Verify your email and sign in</a></p>"
            "<p>If you did not create this account, you can ignore this email.</p>"
        )
        return text, html

    if action == "reset_password":
        text = (
            "Reset your ContentOS password.\n\n"
            f"Open this link to choose a new password:\n{action_url}\n\n"
            "If you did not request this reset, you can ignore this email."
        )
        html = (
            "<p>Reset your ContentOS password.</p>"
            f"<p><a href=\"{action_url}\">Choose a new password</a></p>"
            "<p>If you did not request this reset, you can ignore this email.</p>"
        )
        return text, html

    text = (
        "Open the following secure link:\n"
        f"{action_url}"
    )
    html = f"<p><a href=\"{action_url}\">Open secure link</a></p>"
    return text, html


def _send_via_resend(*, email: str, subject: str, text: str, html: str) -> None:
    if not RESEND_API_KEY:
        raise HTTPException(status_code=503, detail="Auth email delivery is not configured.")
    if not AUTH_EMAIL_FROM:
        raise HTTPException(status_code=503, detail="Auth email sender address is not configured.")

    payload = {
        "from": AUTH_EMAIL_FROM,
        "to": [email],
        "subject": subject,
        "text": text,
        "html": html,
    }

    try:
        response = httpx.post(
            f"{RESEND_API_BASE_URL}/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        logger.exception("Auth email delivery failed for %s via Resend.", email)
        raise HTTPException(status_code=503, detail="We could not send the email right now. Please try again.")


def send_auth_email(*, email: str, subject: str, action: str, action_url: str) -> str | None:
    logger.info("Preparing auth email '%s' for %s.", action, email)
    if AUTH_EMAIL_PREVIEW_ENABLED:
        logger.info("Auth email preview mode is enabled; returning preview URL for %s.", action)
        return action_url

    text, html = _build_email_copy(action=action, action_url=action_url)
    if AUTH_EMAIL_PROVIDER == "resend":
        _send_via_resend(email=email, subject=subject, text=text, html=html)
        return None

    raise HTTPException(status_code=503, detail=f"Unsupported auth email provider: {AUTH_EMAIL_PROVIDER}.")
