import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx
from starlette.responses import RedirectResponse

from app.core.config import env, require_env
from app.integrations_repository import SocialIntegrationRepository


LINKEDIN_CLIENT_ID = require_env("LINKEDIN_CLIENT_ID")
LINKEDIN_REDIRECT_URI = require_env("LINKEDIN_REDIRECT_URI")
LINKEDIN_CLIENT_SECRET = require_env("LINKEDIN_CLIENT_SECRET")
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", "http://localhost:3000") or "http://localhost:3000"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_POST_URL = "https://api.linkedin.com/v2/ugcPosts"
X_CLIENT_ID = require_env("X_CLIENT_ID")
X_REDIRECT_URI = require_env("X_REDIRECT_URI")
X_CLIENT_SECRET = env("X_CLIENT_SECRET")
X_AUTH_URL = "https://x.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_ME_URL = "https://api.x.com/2/users/me"
X_SCOPES = ("tweet.read", "tweet.write", "users.read", "offline.access")
_OAUTH_STATE_TTL_SECONDS = 600
_x_oauth_state_lock = threading.Lock()
_x_oauth_state_store: dict[str, dict[str, str | int]] = {}
_linkedin_oauth_state_lock = threading.Lock()
_linkedin_oauth_state_store: dict[str, dict[str, int]] = {}
social_integration_repository = SocialIntegrationRepository()


def start_linkedin_auth(*, user_id: int) -> str:
    state = secrets.token_urlsafe(32)
    _store_linkedin_oauth_state(state, user_id)

    params = {
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "state": state,
        "scope": "openid profile email w_member_social",
    }
    return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(params)


def _build_frontend_redirect(platform: str, status: str) -> str:
    return f"{FRONTEND_BASE_URL}/integrations?{platform}={status}"


def _build_frontend_error_redirect(platform: str, reason: str) -> str:
    return f"{FRONTEND_BASE_URL}/integrations?{platform}=error&reason={reason}"


def _generate_code_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")


def _generate_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _store_linkedin_oauth_state(state: str, user_id: int) -> None:
    expires_at = int(time.time()) + _OAUTH_STATE_TTL_SECONDS
    with _linkedin_oauth_state_lock:
        _linkedin_oauth_state_store[state] = {
            "user_id": user_id,
            "expires_at": expires_at,
        }


def _pop_linkedin_oauth_state(state: str | None) -> int | None:
    if not state:
        return None

    with _linkedin_oauth_state_lock:
        payload = _linkedin_oauth_state_store.pop(state, None)

    if not payload:
        return None

    expires_at = int(payload["expires_at"])
    if expires_at < int(time.time()):
        return None

    return int(payload["user_id"])


def _store_x_oauth_payload(state: str, *, code_verifier: str, user_id: int) -> None:
    expires_at = int(time.time()) + _OAUTH_STATE_TTL_SECONDS
    with _x_oauth_state_lock:
        _x_oauth_state_store[state] = {
            "code_verifier": code_verifier,
            "user_id": user_id,
            "expires_at": expires_at,
        }


def _pop_x_oauth_payload(state: str | None) -> tuple[str, int] | tuple[None, None]:
    if not state:
        return None, None

    with _x_oauth_state_lock:
        payload = _x_oauth_state_store.pop(state, None)

    if not payload:
        return None, None

    expires_at = int(payload["expires_at"])
    if expires_at < int(time.time()):
        return None, None

    return str(payload["code_verifier"]), int(payload["user_id"])


def start_x_auth(*, user_id: int) -> str:
    state = secrets.token_urlsafe(32)
    code_verifier = _generate_code_verifier()
    code_challenge = _generate_code_challenge(code_verifier)
    _store_x_oauth_payload(state, code_verifier=code_verifier, user_id=user_id)

    params = {
        "response_type": "code",
        "client_id": X_CLIENT_ID,
        "redirect_uri": X_REDIRECT_URI,
        "scope": " ".join(X_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    return f"{X_AUTH_URL}?{urlencode(params)}"


async def handle_linkedin_callback(
    code: str = None,
    state: str = None,
    error: str = None,
) -> RedirectResponse:
    if error:
        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
            status_code=302,
        )

    user_id = _pop_linkedin_oauth_state(state)
    if not code or user_id is None:
        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
            status_code=302,
        )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                LINKEDIN_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": LINKEDIN_REDIRECT_URI,
                    "client_id": LINKEDIN_CLIENT_ID,
                    "client_secret": LINKEDIN_CLIENT_SECRET,
                },
            )

        if resp.status_code >= 400:
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
                status_code=302,
            )

        data = resp.json()
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        scope = data.get("scope")
        token_type = data.get("token_type")
        expires_in = data.get("expires_in")

        if not access_token:
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
                status_code=302,
            )

        linkedin_user_id = await get_linkedin_user_id(access_token)
        social_integration_repository.upsert_connection(
            user_id=user_id,
            platform="linkedin",
            platform_user_id=linkedin_user_id,
            platform_username=None,
            access_token=access_token,
            refresh_token=refresh_token,
            scope=scope,
            token_type=token_type,
            expires_in=expires_in,
        )

        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/integrations?linkedin=connected",
            status_code=302,
        )

    except Exception:
        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/integrations?linkedin=error",
            status_code=302,
        )


async def fetch_x_me(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            X_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def handle_x_callback(
    code: str = None,
    state: str = None,
    error: str = None,
) -> RedirectResponse:
    if error:
        return RedirectResponse(_build_frontend_error_redirect("x", "authorization"), status_code=302)

    if not code or not state:
        return RedirectResponse(_build_frontend_error_redirect("x", "missing_code_or_state"), status_code=302)

    code_verifier, user_id = _pop_x_oauth_payload(state)
    if not code_verifier or user_id is None:
        return RedirectResponse(_build_frontend_error_redirect("x", "expired_state"), status_code=302)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            token_request_kwargs = {}
            if X_CLIENT_SECRET:
                token_request_kwargs["auth"] = (X_CLIENT_ID, X_CLIENT_SECRET)
            token_resp = await client.post(
                X_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": X_REDIRECT_URI,
                    "client_id": X_CLIENT_ID,
                    "code_verifier": code_verifier,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                **token_request_kwargs,
            )

        if token_resp.status_code >= 400:
            return RedirectResponse(
                _build_frontend_error_redirect("x", f"token_{token_resp.status_code}"),
                status_code=302,
            )

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")
        scope = token_data.get("scope")
        token_type = token_data.get("token_type")
        if not access_token:
            return RedirectResponse(_build_frontend_error_redirect("x", "missing_access_token"), status_code=302)

        me_data = await fetch_x_me(access_token)
        x_user = me_data.get("data", {}) if isinstance(me_data, dict) else {}
        if not x_user.get("id"):
            return RedirectResponse(_build_frontend_error_redirect("x", "missing_x_user"), status_code=302)
        social_integration_repository.upsert_connection(
            user_id=user_id,
            platform="x",
            platform_user_id=str(x_user.get("id", "")),
            platform_username=x_user.get("username"),
            access_token=access_token,
            refresh_token=refresh_token,
            scope=scope,
            token_type=token_type,
            expires_in=expires_in,
        )
        return RedirectResponse(_build_frontend_redirect("x", "connected"), status_code=302)
    except httpx.HTTPError:
        return RedirectResponse(_build_frontend_error_redirect("x", "http_error"), status_code=302)
    except Exception:
        return RedirectResponse(_build_frontend_error_redirect("x", "exception"), status_code=302)


async def get_linkedin_user_id(access_token: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            LINKEDIN_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    profile = resp.json()
    return profile["sub"]


async def publish_linkedin_post(access_token: str, member_id: str, text: str):
    payload = {
        "author": f"urn:li:person:{member_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            LINKEDIN_POST_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        resp.raise_for_status()
    return resp.status_code, resp.text, resp.headers.get("x-restli-id") or resp.headers.get("location")


async def publish_linkedin_post_for_user(*, user_id: int, text: str) -> dict:
    connection = social_integration_repository.get_linkedin_connection(user_id=user_id)
    if connection is None:
        return {
            "ok": False,
            "error": "linkedin_not_connected",
            "message": "Connect LinkedIn before publishing.",
        }

    if not connection.access_token or not connection.platform_user_id:
        return {
            "ok": False,
            "error": "linkedin_connection_incomplete",
            "message": "Your LinkedIn connection is missing token data.",
        }

    try:
        status_code, response_text, linkedin_post_id = await publish_linkedin_post(
            connection.access_token,
            connection.platform_user_id,
            text,
        )
        return {
            "ok": True,
            "platform": "linkedin",
            "status_code": status_code,
            "linkedin_user_id": connection.platform_user_id,
            "linkedin_post_id": linkedin_post_id,
            "response_text": response_text,
        }
    except httpx.HTTPStatusError as error:
        response = error.response
        return {
            "ok": False,
            "error": "linkedin_publish_failed",
            "message": "LinkedIn rejected the post request.",
            "status_code": response.status_code,
            "response_text": response.text,
        }
    except Exception:
        return {
            "ok": False,
            "error": "linkedin_publish_failed",
            "message": "LinkedIn publish failed unexpectedly.",
        }
