from __future__ import annotations

import math
import mimetypes
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import HTTPException
from starlette.responses import RedirectResponse

from app.core.config import env
from app.integrations_repository import SocialIntegrationRecord, SocialIntegrationRepository


TIKTOK_CLIENT_KEY = env("TIKTOK_CLIENT_KEY", "") or ""
TIKTOK_CLIENT_SECRET = env("TIKTOK_CLIENT_SECRET", "") or ""
TIKTOK_REDIRECT_URI = env("TIKTOK_REDIRECT_URI", "") or ""
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", "http://localhost:3000") or "http://localhost:3000"
PUBLIC_BASE_URL = (env("PUBLIC_BASE_URL", "http://localhost:8000") or "http://localhost:8000").rstrip("/")

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
TIKTOK_VIDEO_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
TIKTOK_SCOPES = ("user.info.basic", "video.publish")
TIKTOK_PLATFORM = "tiktok"

_OAUTH_STATE_TTL_SECONDS = 600
_TOKEN_REFRESH_SKEW_SECONDS = 120
_MAX_TITLE_LENGTH = 2200
_DEFAULT_UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024
_tiktok_oauth_state_lock = threading.Lock()
_tiktok_oauth_state_store: dict[str, dict[str, int]] = {}
social_integration_repository = SocialIntegrationRepository()


def start_tiktok_auth(*, user_id: int) -> str:
    _require_tiktok_config()
    state = secrets.token_urlsafe(32)
    _store_tiktok_oauth_state(state, user_id)

    params = {
        "client_key": TIKTOK_CLIENT_KEY,
        "response_type": "code",
        "scope": ",".join(TIKTOK_SCOPES),
        "redirect_uri": TIKTOK_REDIRECT_URI,
        "state": state,
    }
    return f"{TIKTOK_AUTH_URL}?{urlencode(params)}"


async def handle_tiktok_callback(
    code: str = None,
    state: str = None,
    error: str = None,
) -> RedirectResponse:
    if error:
        return RedirectResponse(_build_frontend_error_redirect("tiktok", "authorization"), status_code=302)

    if not code or not state:
        return RedirectResponse(_build_frontend_error_redirect("tiktok", "missing_code_or_state"), status_code=302)

    config_error = _tiktok_config_error()
    if config_error:
        return RedirectResponse(_build_frontend_error_redirect("tiktok", config_error), status_code=302)

    user_id = _pop_tiktok_oauth_state(state)
    if user_id is None:
        return RedirectResponse(_build_frontend_error_redirect("tiktok", "expired_state"), status_code=302)

    try:
        token_data = await _exchange_tiktok_code_for_token(code)
        access_token = str(token_data.get("access_token") or "").strip()
        open_id = str(token_data.get("open_id") or "").strip()
        if not access_token or not open_id:
            return RedirectResponse(_build_frontend_error_redirect("tiktok", "missing_token_data"), status_code=302)

        creator_info = await _query_tiktok_creator_info(access_token)
        social_integration_repository.upsert_connection(
            user_id=user_id,
            platform=TIKTOK_PLATFORM,
            platform_user_id=open_id,
            platform_username=creator_info.get("creator_username"),
            access_token=access_token,
            refresh_token=token_data.get("refresh_token"),
            scope=token_data.get("scope"),
            token_type=token_data.get("token_type"),
            expires_in=token_data.get("expires_in"),
        )
        return RedirectResponse(_build_frontend_redirect("tiktok", "connected"), status_code=302)
    except httpx.HTTPStatusError as error:
        return RedirectResponse(
            _build_frontend_error_redirect("tiktok", f"token_{error.response.status_code}"),
            status_code=302,
        )
    except httpx.HTTPError:
        return RedirectResponse(_build_frontend_error_redirect("tiktok", "http_error"), status_code=302)
    except Exception:
        return RedirectResponse(_build_frontend_error_redirect("tiktok", "exception"), status_code=302)


async def get_tiktok_creator_info_for_user(*, user_id: int) -> dict:
    connection = social_integration_repository.get_by_user_and_platform(user_id=user_id, platform=TIKTOK_PLATFORM)
    if connection is None:
        return {
            "ok": False,
            "error": "tiktok_not_connected",
            "message": "Connect TikTok before publishing.",
        }

    try:
        access_token = await _access_token_for_connection(connection)
        creator_info = await _query_tiktok_creator_info(access_token)
        return {
            "ok": True,
            "platform": TIKTOK_PLATFORM,
            "creator_info": creator_info,
        }
    except HTTPException as error:
        return _http_exception_result(error, default_error="tiktok_creator_info_failed")
    except httpx.HTTPStatusError as error:
        return _tiktok_http_error_result(error, default_message="TikTok creator info request failed.")
    except Exception:
        return {
            "ok": False,
            "error": "tiktok_creator_info_failed",
            "message": "TikTok creator info request failed unexpectedly.",
        }


async def get_tiktok_publish_status_for_user(*, user_id: int, publish_id: str) -> dict:
    connection = social_integration_repository.get_by_user_and_platform(user_id=user_id, platform=TIKTOK_PLATFORM)
    if connection is None:
        return {
            "ok": False,
            "error": "tiktok_not_connected",
            "message": "Connect TikTok before checking publish status.",
        }

    try:
        access_token = await _access_token_for_connection(connection)
        status_payload = await _fetch_tiktok_publish_status(access_token, publish_id)
        return {
            "ok": True,
            "platform": TIKTOK_PLATFORM,
            "publish_id": publish_id,
            "status": status_payload,
        }
    except HTTPException as error:
        return _http_exception_result(error, default_error="tiktok_status_failed")
    except httpx.HTTPStatusError as error:
        return _tiktok_http_error_result(error, default_message="TikTok status request failed.")
    except Exception:
        return {
            "ok": False,
            "error": "tiktok_status_failed",
            "message": "TikTok status request failed unexpectedly.",
        }


async def publish_tiktok_asset_for_user(
    *,
    user_id: int,
    asset: dict,
    privacy_level: str | None = None,
    disable_comment: bool | None = None,
    disable_duet: bool | None = None,
    disable_stitch: bool | None = None,
    video_cover_timestamp_ms: int | None = None,
) -> dict:
    config_error = _tiktok_config_error()
    if config_error:
        return {
            "ok": False,
            "error": "tiktok_not_configured",
            "message": config_error,
        }

    asset_type = _asset_type(asset)
    if asset_type not in {"tiktok_clip", "instagram_reel"}:
        return {
            "ok": False,
            "error": "tiktok_unsupported_asset",
            "message": "Only TikTok clip and short-video assets can be published directly to TikTok.",
        }

    connection = social_integration_repository.get_by_user_and_platform(user_id=user_id, platform=TIKTOK_PLATFORM)
    if connection is None:
        return {
            "ok": False,
            "error": "tiktok_not_connected",
            "message": "Connect TikTok before publishing.",
        }

    if not connection.access_token or not connection.platform_user_id:
        return {
            "ok": False,
            "error": "tiktok_connection_incomplete",
            "message": "Your TikTok connection is missing token data.",
        }

    try:
        access_token = await _access_token_for_connection(connection)
        creator_info = await _query_tiktok_creator_info(access_token)
        post_info = _build_tiktok_post_info(
            asset,
            creator_info=creator_info,
            privacy_level=privacy_level,
            disable_comment=disable_comment,
            disable_duet=disable_duet,
            disable_stitch=disable_stitch,
            video_cover_timestamp_ms=video_cover_timestamp_ms,
        )
        source_info, upload_file_path, content_type = _build_tiktok_source_info(asset)
        init_result = await _init_tiktok_video_publish(access_token, post_info, source_info)
        publish_id = str(init_result.get("publish_id") or "").strip()
        upload_url = str(init_result.get("upload_url") or "").strip()
        if upload_file_path and upload_url:
            await _upload_video_to_tiktok(upload_url, upload_file_path, content_type)

        return {
            "ok": True,
            "platform": TIKTOK_PLATFORM,
            "asset_type": asset_type,
            "tiktok_user_id": connection.platform_user_id,
            "tiktok_username": creator_info.get("creator_username") or connection.platform_username,
            "publish_id": publish_id,
            "privacy_level": post_info["privacy_level"],
            "source": source_info["source"],
        }
    except HTTPException as error:
        return {
            "ok": False,
            "error": "tiktok_publish_failed",
            "message": error.detail if isinstance(error.detail, str) else "TikTok publish failed.",
            "status_code": error.status_code,
        }
    except httpx.HTTPStatusError as error:
        return _tiktok_http_error_result(error, default_message="TikTok rejected the publish request.")
    except Exception:
        return {
            "ok": False,
            "error": "tiktok_publish_failed",
            "message": "TikTok publish failed unexpectedly.",
        }


def _store_tiktok_oauth_state(state: str, user_id: int) -> None:
    expires_at = int(time.time()) + _OAUTH_STATE_TTL_SECONDS
    with _tiktok_oauth_state_lock:
        _tiktok_oauth_state_store[state] = {
            "user_id": user_id,
            "expires_at": expires_at,
        }


def _pop_tiktok_oauth_state(state: str | None) -> int | None:
    if not state:
        return None

    with _tiktok_oauth_state_lock:
        payload = _tiktok_oauth_state_store.pop(state, None)

    if not payload:
        return None

    expires_at = int(payload["expires_at"])
    if expires_at < int(time.time()):
        return None

    return int(payload["user_id"])


def _build_frontend_redirect(platform: str, status: str) -> str:
    return f"{FRONTEND_BASE_URL}/integrations?{platform}={status}"


def _build_frontend_error_redirect(platform: str, reason: str) -> str:
    return f"{FRONTEND_BASE_URL}/integrations?{platform}=error&reason={reason}"


async def _exchange_tiktok_code_for_token(code: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TIKTOK_TOKEN_URL,
            data={
                "client_key": TIKTOK_CLIENT_KEY,
                "client_secret": TIKTOK_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": TIKTOK_REDIRECT_URI,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cache-Control": "no-cache",
            },
        )
        response.raise_for_status()
    return response.json()


async def _refresh_tiktok_access_token(connection: SocialIntegrationRecord) -> str:
    if not connection.refresh_token:
        raise HTTPException(status_code=409, detail="Your TikTok connection needs to be reconnected.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TIKTOK_TOKEN_URL,
            data={
                "client_key": TIKTOK_CLIENT_KEY,
                "client_secret": TIKTOK_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": connection.refresh_token,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cache-Control": "no-cache",
            },
        )
        response.raise_for_status()

    token_data = response.json()
    access_token = str(token_data.get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(status_code=502, detail="TikTok did not return a refreshed access token.")

    social_integration_repository.upsert_connection(
        user_id=connection.user_id,
        platform=TIKTOK_PLATFORM,
        platform_user_id=str(token_data.get("open_id") or connection.platform_user_id),
        platform_username=connection.platform_username,
        access_token=access_token,
        refresh_token=token_data.get("refresh_token") or connection.refresh_token,
        scope=token_data.get("scope") or connection.scope,
        token_type=token_data.get("token_type") or connection.token_type,
        expires_in=token_data.get("expires_in"),
    )
    return access_token


async def _access_token_for_connection(connection: SocialIntegrationRecord) -> str:
    if not connection.access_token:
        raise HTTPException(status_code=409, detail="Your TikTok connection is missing token data.")

    expires_at = _ensure_aware(connection.token_expires_at)
    refresh_after = datetime.now(timezone.utc) + timedelta(seconds=_TOKEN_REFRESH_SKEW_SECONDS)
    if expires_at and expires_at <= refresh_after:
        return await _refresh_tiktok_access_token(connection)

    return connection.access_token


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _query_tiktok_creator_info(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TIKTOK_CREATOR_INFO_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        response.raise_for_status()

    payload = response.json()
    _raise_for_tiktok_error(payload, "TikTok creator info request failed.")
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


async def _fetch_tiktok_publish_status(access_token: str, publish_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TIKTOK_STATUS_URL,
            json={"publish_id": publish_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        response.raise_for_status()

    payload = response.json()
    _raise_for_tiktok_error(payload, "TikTok status request failed.")
    return payload


async def _init_tiktok_video_publish(access_token: str, post_info: dict, source_info: dict) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            TIKTOK_VIDEO_INIT_URL,
            json={
                "post_info": post_info,
                "source_info": source_info,
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        response.raise_for_status()

    payload = response.json()
    _raise_for_tiktok_error(payload, "TikTok publish initialization failed.")
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("publish_id"):
        raise HTTPException(status_code=502, detail="TikTok did not return a publish id.")
    return data


async def _upload_video_to_tiktok(upload_url: str, file_path: Path, content_type: str) -> None:
    file_size = file_path.stat().st_size
    if file_size <= 0:
        raise HTTPException(status_code=400, detail="The TikTok video file is empty.")

    headers = {
        "Content-Type": content_type,
        "Content-Length": str(file_size),
        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        with file_path.open("rb") as video_file:
            response = await client.put(upload_url, content=video_file, headers=headers)
        response.raise_for_status()


def _build_tiktok_post_info(
    asset: dict,
    *,
    creator_info: dict,
    privacy_level: str | None,
    disable_comment: bool | None,
    disable_duet: bool | None,
    disable_stitch: bool | None,
    video_cover_timestamp_ms: int | None,
) -> dict:
    selected_privacy_level = _select_privacy_level(creator_info, privacy_level)
    title = _build_title_from_asset(asset)
    post_info = {
        "title": title,
        "privacy_level": selected_privacy_level,
        "disable_comment": _effective_interaction_flag(disable_comment, creator_info.get("comment_disabled")),
        "disable_duet": _effective_interaction_flag(disable_duet, creator_info.get("duet_disabled")),
        "disable_stitch": _effective_interaction_flag(disable_stitch, creator_info.get("stitch_disabled")),
    }
    if video_cover_timestamp_ms is not None:
        post_info["video_cover_timestamp_ms"] = max(0, int(video_cover_timestamp_ms))
    return post_info


def _build_tiktok_source_info(asset: dict) -> tuple[dict, Path | None, str]:
    media = asset.get("media") if isinstance(asset.get("media"), dict) else {}
    file_path = _extract_existing_video_path(media)
    if file_path:
        file_size = file_path.stat().st_size
        chunk_size = min(_DEFAULT_UPLOAD_CHUNK_SIZE, file_size)
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": chunk_size,
            "total_chunk_count": max(1, math.ceil(file_size / chunk_size)),
        }
        return source_info, file_path, _video_content_type(media, file_path)

    video_url = _extract_video_url(media)
    if not video_url:
        raise HTTPException(
            status_code=400,
            detail="This TikTok asset does not include a video file path or public video URL to publish.",
        )

    return {
        "source": "PULL_FROM_URL",
        "video_url": video_url,
    }, None, _video_content_type(media, None)


def _select_privacy_level(creator_info: dict, requested_privacy_level: str | None) -> str:
    options = [
        str(option).strip()
        for option in creator_info.get("privacy_level_options", [])
        if str(option).strip()
    ]
    requested = str(requested_privacy_level or "").strip()
    if requested:
        if requested not in options:
            raise HTTPException(
                status_code=400,
                detail="TikTok privacy_level must match one of the creator's current privacy options.",
            )
        return requested

    for conservative_option in ("SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"):
        if conservative_option in options:
            return conservative_option

    raise HTTPException(status_code=400, detail="TikTok did not return any available privacy options.")


def _effective_interaction_flag(requested: bool | None, forced_disabled: object) -> bool:
    if forced_disabled is True:
        return True
    return bool(requested) if requested is not None else False


def _asset_type(asset: dict) -> str:
    return str(asset.get("assetType") or asset.get("asset_type") or "").strip().lower()


def _asset_blocks(asset: dict) -> list[dict]:
    blocks = asset.get("blocks")
    return blocks if isinstance(blocks, list) else []


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("title", "body", "content", "text", "summary", "caption", "hook", "script", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        parts = [_normalize_text(candidate) for candidate in value.values()]
        return "\n".join(part for part in parts if part)
    if isinstance(value, list):
        parts = [_normalize_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    return str(value).strip()


def _build_title_from_asset(asset: dict) -> str:
    explicit_caption = _normalize_text(asset.get("caption") or asset.get("description"))
    if explicit_caption:
        return explicit_caption[:_MAX_TITLE_LENGTH]

    chunks = [_normalize_text(asset.get("title"))]
    for block in _asset_blocks(asset):
        value = _normalize_text(block.get("value"))
        if value:
            chunks.append(value)

    title = "\n\n".join(chunk for chunk in chunks if chunk).strip()
    return (title or "Generated TikTok clip")[:_MAX_TITLE_LENGTH]


def _extract_existing_video_path(media: dict) -> Path | None:
    candidates = (
        media.get("video_path"),
        media.get("videoPath"),
        media.get("path"),
        media.get("file_path"),
        media.get("filePath"),
    )
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(str(candidate)).expanduser()
        if path.exists() and path.is_file():
            return path
    return None


def _extract_video_url(media: dict) -> str:
    video_url = str(media.get("videoUrl") or media.get("video_url") or "").strip()
    if not video_url:
        return ""
    parsed = urlparse(video_url)
    if parsed.scheme in {"http", "https"}:
        return video_url
    if video_url.startswith("/"):
        return f"{PUBLIC_BASE_URL}{video_url}"
    return video_url


def _video_content_type(media: dict, file_path: Path | None) -> str:
    content_type = str(media.get("video_content_type") or media.get("content_type") or "").strip()
    if content_type:
        return content_type
    if file_path:
        guessed_type, _ = mimetypes.guess_type(file_path.name)
        if guessed_type:
            return guessed_type
    return "video/mp4"


def _raise_for_tiktok_error(payload: dict, fallback_message: str) -> None:
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return

    code = str(error.get("code") or "").strip()
    if not code or code == "ok":
        return

    message = str(error.get("message") or "").strip() or fallback_message
    raise HTTPException(status_code=502, detail=f"{message} ({code})")


def _tiktok_http_error_result(error: httpx.HTTPStatusError, *, default_message: str) -> dict:
    response = error.response
    message = default_message
    try:
        payload = response.json()
        error_payload = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error_payload, dict) and error_payload.get("message"):
            message = str(error_payload["message"])
        elif isinstance(payload, dict) and payload.get("error_description"):
            message = str(payload["error_description"])
    except ValueError:
        pass

    return {
        "ok": False,
        "error": "tiktok_request_failed",
        "message": message,
        "status_code": response.status_code,
        "response_text": response.text,
    }


def _http_exception_result(error: HTTPException, *, default_error: str) -> dict:
    return {
        "ok": False,
        "error": default_error,
        "message": error.detail if isinstance(error.detail, str) else "TikTok request failed.",
        "status_code": error.status_code,
    }


def _tiktok_config_error() -> str:
    missing = []
    if not TIKTOK_CLIENT_KEY:
        missing.append("TIKTOK_CLIENT_KEY")
    if not TIKTOK_CLIENT_SECRET:
        missing.append("TIKTOK_CLIENT_SECRET")
    if not TIKTOK_REDIRECT_URI:
        missing.append("TIKTOK_REDIRECT_URI")
    if missing:
        return f"TikTok is not configured yet. Missing: {', '.join(missing)}."
    return ""


def _require_tiktok_config() -> None:
    error = _tiktok_config_error()
    if error:
        raise HTTPException(status_code=500, detail=error)
