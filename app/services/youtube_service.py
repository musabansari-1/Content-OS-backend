from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import HTTPException
from starlette.responses import RedirectResponse

from app.core.config import env
from app.integrations_repository import SocialIntegrationRecord, SocialIntegrationRepository


YOUTUBE_CLIENT_ID = env("YOUTUBE_CLIENT_ID", "") or ""
YOUTUBE_CLIENT_SECRET = env("YOUTUBE_CLIENT_SECRET", "") or ""
YOUTUBE_REDIRECT_URI = env("YOUTUBE_REDIRECT_URI", "") or ""
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", "http://localhost:3000") or "http://localhost:3000"
PUBLIC_BASE_URL = (env("PUBLIC_BASE_URL", "http://localhost:8000") or "http://localhost:8000").rstrip("/")
YOUTUBE_DEFAULT_PRIVACY_STATUS = (env("YOUTUBE_DEFAULT_PRIVACY_STATUS", "private") or "private").strip().lower()

YOUTUBE_PLATFORM = "youtube"
YOUTUBE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
)

_OAUTH_STATE_TTL_SECONDS = 600
_TOKEN_REFRESH_SKEW_SECONDS = 120
_MAX_TITLE_LENGTH = 100
_MAX_DESCRIPTION_LENGTH = 5000
_VALID_PRIVACY_STATUSES = {"private", "public", "unlisted"}
_DEFAULT_CATEGORY_ID = "22"
_youtube_oauth_state_lock = threading.Lock()
_youtube_oauth_state_store: dict[str, dict[str, int]] = {}
social_integration_repository = SocialIntegrationRepository()
logger = logging.getLogger(__name__)


def start_youtube_auth(*, user_id: int) -> str:
    _require_youtube_config()
    state = secrets.token_urlsafe(32)
    _store_youtube_oauth_state(state, user_id)

    params = {
        "client_id": YOUTUBE_CLIENT_ID,
        "redirect_uri": YOUTUBE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "state": state,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    return f"{YOUTUBE_AUTH_URL}?{urlencode(params)}"


async def handle_youtube_callback(
    code: str = None,
    state: str = None,
    error: str = None,
) -> RedirectResponse:
    if error:
        return RedirectResponse(_build_frontend_error_redirect(YOUTUBE_PLATFORM, "authorization"), status_code=302)

    if not code or not state:
        return RedirectResponse(_build_frontend_error_redirect(YOUTUBE_PLATFORM, "missing_code_or_state"), status_code=302)

    config_error = _youtube_config_error()
    if config_error:
        return RedirectResponse(_build_frontend_error_redirect(YOUTUBE_PLATFORM, config_error), status_code=302)

    user_id = _pop_youtube_oauth_state(state)
    if user_id is None:
        return RedirectResponse(_build_frontend_error_redirect(YOUTUBE_PLATFORM, "expired_state"), status_code=302)

    try:
        token_data = await _exchange_youtube_code_for_token(code)
        access_token = str(token_data.get("access_token") or "").strip()
        if not access_token:
            return RedirectResponse(_build_frontend_error_redirect(YOUTUBE_PLATFORM, "missing_access_token"), status_code=302)

        channel_profile = await _fetch_youtube_channel_profile_best_effort(access_token)
        social_integration_repository.upsert_connection(
            user_id=user_id,
            platform=YOUTUBE_PLATFORM,
            platform_user_id=channel_profile.get("channel_id") or f"youtube-user-{user_id}",
            platform_username=channel_profile.get("channel_title"),
            access_token=access_token,
            refresh_token=token_data.get("refresh_token"),
            scope=token_data.get("scope") or " ".join(YOUTUBE_SCOPES),
            token_type=token_data.get("token_type"),
            expires_in=token_data.get("expires_in"),
        )
        return RedirectResponse(_build_frontend_redirect(YOUTUBE_PLATFORM, "connected"), status_code=302)
    except httpx.HTTPStatusError as error:
        return RedirectResponse(
            _build_frontend_error_redirect(YOUTUBE_PLATFORM, f"token_{error.response.status_code}"),
            status_code=302,
        )
    except httpx.HTTPError:
        return RedirectResponse(_build_frontend_error_redirect(YOUTUBE_PLATFORM, "http_error"), status_code=302)
    except Exception:
        return RedirectResponse(_build_frontend_error_redirect(YOUTUBE_PLATFORM, "exception"), status_code=302)


async def get_youtube_channel_for_user(*, user_id: int) -> dict[str, Any]:
    connection = social_integration_repository.get_by_user_and_platform(user_id=user_id, platform=YOUTUBE_PLATFORM)
    if connection is None:
        return {
            "ok": False,
            "error": "youtube_not_connected",
            "message": "Connect YouTube before publishing.",
        }

    try:
        access_token = await _access_token_for_connection(connection)
        channel_profile = await _fetch_youtube_channel_profile(access_token)
        return {
            "ok": True,
            "platform": YOUTUBE_PLATFORM,
            "channel": channel_profile,
        }
    except HTTPException as error:
        return _http_exception_result(error, default_error="youtube_channel_failed")
    except httpx.HTTPStatusError as error:
        return _youtube_http_error_result(error, default_message="YouTube channel lookup failed.")
    except Exception:
        return {
            "ok": False,
            "error": "youtube_channel_failed",
            "message": "YouTube channel lookup failed unexpectedly.",
        }


async def publish_youtube_asset_for_user(
    *,
    user_id: int,
    asset: dict[str, Any],
    privacy_status: str | None = None,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    category_id: str | None = None,
    notify_subscribers: bool | None = None,
    self_declared_made_for_kids: bool | None = None,
    contains_synthetic_media: bool | None = None,
) -> dict[str, Any]:
    config_error = _youtube_config_error()
    if config_error:
        return {
            "ok": False,
            "error": "youtube_not_configured",
            "message": config_error,
        }

    asset_type = _asset_type(asset)
    if asset_type not in {"youtube_shorts", "instagram_reel", "tiktok_clip"}:
        return {
            "ok": False,
            "error": "youtube_unsupported_asset",
            "message": "Only YouTube Shorts and generated short-video assets can be published directly to YouTube.",
        }

    connection = social_integration_repository.get_by_user_and_platform(user_id=user_id, platform=YOUTUBE_PLATFORM)
    if connection is None:
        return {
            "ok": False,
            "error": "youtube_not_connected",
            "message": "Connect YouTube before publishing.",
        }

    if not connection.access_token:
        return {
            "ok": False,
            "error": "youtube_connection_incomplete",
            "message": "Your YouTube connection is missing token data.",
        }

    downloaded_path: Path | None = None
    try:
        access_token = await _access_token_for_connection(connection)
        video_path, downloaded_path, content_type = await _resolve_youtube_video_source(asset)
        video_resource = _build_youtube_video_resource(
            asset,
            privacy_status=privacy_status,
            title=title,
            description=description,
            tags=tags,
            category_id=category_id,
            self_declared_made_for_kids=self_declared_made_for_kids,
            contains_synthetic_media=contains_synthetic_media,
        )
        upload_result = await _upload_youtube_video(
            access_token=access_token,
            video_path=video_path,
            content_type=content_type,
            video_resource=video_resource,
            notify_subscribers=notify_subscribers,
        )
        youtube_video_id = str(upload_result.get("id") or "").strip()
        if not youtube_video_id:
            raise HTTPException(status_code=502, detail="YouTube did not return a video id.")

        return {
            "ok": True,
            "platform": YOUTUBE_PLATFORM,
            "asset_type": asset_type,
            "youtube_channel_id": connection.platform_user_id,
            "youtube_channel_title": connection.platform_username,
            "youtube_video_id": youtube_video_id,
            "youtube_video_url": f"https://www.youtube.com/watch?v={youtube_video_id}",
            "privacy_status": video_resource["status"]["privacyStatus"],
            "response": upload_result,
        }
    except HTTPException as error:
        return {
            "ok": False,
            "error": "youtube_publish_failed" if error.status_code >= 500 else "youtube_invalid_asset",
            "message": error.detail if isinstance(error.detail, str) else "YouTube publish failed.",
            "status_code": error.status_code,
        }
    except httpx.HTTPStatusError as error:
        return _youtube_http_error_result(error, default_message="YouTube rejected the upload request.")
    except httpx.HTTPError as error:
        logger.exception("YouTube publish network failure.")
        return {
            "ok": False,
            "error": "youtube_network_failed",
            "message": _safe_exception_message(error, fallback="YouTube publish could not reach the required video or Google upload endpoint."),
            "retryable": True,
        }
    except Exception as error:
        logger.exception("YouTube publish failed unexpectedly.")
        return {
            "ok": False,
            "error": "youtube_publish_failed",
            "message": _safe_exception_message(error, fallback="YouTube publish failed unexpectedly."),
        }
    finally:
        if downloaded_path:
            try:
                downloaded_path.unlink(missing_ok=True)
            except Exception:
                pass


def _store_youtube_oauth_state(state: str, user_id: int) -> None:
    expires_at = int(time.time()) + _OAUTH_STATE_TTL_SECONDS
    with _youtube_oauth_state_lock:
        _youtube_oauth_state_store[state] = {
            "user_id": user_id,
            "expires_at": expires_at,
        }


def _pop_youtube_oauth_state(state: str | None) -> int | None:
    if not state:
        return None

    with _youtube_oauth_state_lock:
        payload = _youtube_oauth_state_store.pop(state, None)

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


async def _exchange_youtube_code_for_token(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            YOUTUBE_TOKEN_URL,
            data={
                "client_id": YOUTUBE_CLIENT_ID,
                "client_secret": YOUTUBE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": YOUTUBE_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
    return response.json()


async def _refresh_youtube_access_token(connection: SocialIntegrationRecord) -> str:
    if not connection.refresh_token:
        raise HTTPException(status_code=409, detail="Your YouTube connection needs to be reconnected.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            YOUTUBE_TOKEN_URL,
            data={
                "client_id": YOUTUBE_CLIENT_ID,
                "client_secret": YOUTUBE_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": connection.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()

    token_data = response.json()
    access_token = str(token_data.get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(status_code=502, detail="YouTube did not return a refreshed access token.")

    social_integration_repository.upsert_connection(
        user_id=connection.user_id,
        platform=YOUTUBE_PLATFORM,
        platform_user_id=connection.platform_user_id,
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
        raise HTTPException(status_code=409, detail="Your YouTube connection is missing token data.")

    expires_at = _ensure_aware(connection.token_expires_at)
    refresh_after = datetime.now(timezone.utc) + timedelta(seconds=_TOKEN_REFRESH_SKEW_SECONDS)
    if expires_at and expires_at <= refresh_after:
        return await _refresh_youtube_access_token(connection)

    return connection.access_token


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _fetch_youtube_channel_profile(access_token: str) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            YOUTUBE_CHANNELS_URL,
            params={"part": "snippet", "mine": "true", "maxResults": 1},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()

    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    first = items[0] if isinstance(items, list) and items else {}
    snippet = first.get("snippet") if isinstance(first, dict) else {}
    channel_id = str(first.get("id") or "").strip() if isinstance(first, dict) else ""
    channel_title = str(snippet.get("title") or "").strip() if isinstance(snippet, dict) else ""
    return {
        "channel_id": channel_id,
        "channel_title": channel_title,
    }


async def _fetch_youtube_channel_profile_best_effort(access_token: str) -> dict[str, str]:
    try:
        return await _fetch_youtube_channel_profile(access_token)
    except Exception:
        return {
            "channel_id": "",
            "channel_title": "",
        }


async def _upload_youtube_video(
    *,
    access_token: str,
    video_path: Path,
    content_type: str,
    video_resource: dict[str, Any],
    notify_subscribers: bool | None,
) -> dict[str, Any]:
    file_size = video_path.stat().st_size
    if file_size <= 0:
        raise HTTPException(status_code=400, detail="The YouTube video file is empty.")

    params: dict[str, Any] = {
        "uploadType": "resumable",
        "part": "snippet,status",
    }
    if notify_subscribers is not None:
        params["notifySubscribers"] = "true" if notify_subscribers else "false"

    async with httpx.AsyncClient(timeout=60.0) as client:
        init_response = await client.post(
            YOUTUBE_UPLOAD_URL,
            params=params,
            json=video_resource,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(file_size),
                "X-Upload-Content-Type": content_type,
            },
        )
        init_response.raise_for_status()
        upload_url = init_response.headers.get("location")
        if not upload_url:
            raise HTTPException(status_code=502, detail="YouTube did not return a resumable upload URL.")

        with video_path.open("rb") as video_file:
            upload_response = await client.put(
                upload_url,
                content=video_file,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Length": str(file_size),
                    "Content-Type": content_type,
                },
                timeout=300.0,
            )
        upload_response.raise_for_status()

    if not upload_response.content:
        return {}
    try:
        payload = upload_response.json()
    except ValueError as error:
        raise HTTPException(
            status_code=502,
            detail="YouTube returned a non-JSON upload response.",
        ) from error
    return payload if isinstance(payload, dict) else {}


async def _resolve_youtube_video_source(asset: dict[str, Any]) -> tuple[Path, Path | None, str]:
    media = asset.get("media") if isinstance(asset.get("media"), dict) else {}
    file_path = _extract_existing_video_path(media)
    if file_path:
        return file_path, None, _video_content_type(media, file_path)

    video_url = _extract_video_url(media)
    if not video_url:
        raise HTTPException(
            status_code=400,
            detail="This YouTube asset does not include a video file path or video URL to upload.",
        )

    content_type = _video_content_type(media, None)
    downloaded_path = await _download_video_to_temp(video_url, content_type=content_type)
    return downloaded_path, downloaded_path, content_type


def _extract_existing_video_path(media: dict[str, Any]) -> Path | None:
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


def _extract_video_url(media: dict[str, Any]) -> str:
    video_url = str(media.get("videoUrl") or media.get("video_url") or "").strip()
    if not video_url:
        return ""
    parsed = urlparse(video_url)
    if parsed.scheme in {"http", "https"}:
        return video_url
    if video_url.startswith("/"):
        return f"{PUBLIC_BASE_URL}{video_url}"
    return ""


async def _download_video_to_temp(video_url: str, *, content_type: str) -> Path:
    suffix = mimetypes.guess_extension(content_type) or ".mp4"
    temp_file = tempfile.NamedTemporaryFile(prefix="contentos-youtube-", suffix=suffix, delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            async with client.stream("GET", video_url) as response:
                response.raise_for_status()
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            handle.write(chunk)
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _video_content_type(media: dict[str, Any], file_path: Path | None) -> str:
    content_type = str(media.get("video_content_type") or media.get("content_type") or "").strip()
    if content_type:
        return content_type
    if file_path:
        guessed_type, _ = mimetypes.guess_type(file_path.name)
        if guessed_type:
            return guessed_type
    return "video/mp4"


def _build_youtube_video_resource(
    asset: dict[str, Any],
    *,
    privacy_status: str | None,
    title: str | None,
    description: str | None,
    tags: list[str] | None,
    category_id: str | None,
    self_declared_made_for_kids: bool | None,
    contains_synthetic_media: bool | None,
) -> dict[str, Any]:
    selected_privacy_status = _normalize_privacy_status(privacy_status)
    selected_title = _build_title_from_asset(asset, explicit_title=title)
    selected_description = _build_description_from_asset(asset, explicit_description=description)
    selected_tags = _build_tags_from_asset(asset, explicit_tags=tags)

    snippet: dict[str, Any] = {
        "title": selected_title,
        "description": selected_description,
        "categoryId": str(category_id or _DEFAULT_CATEGORY_ID).strip() or _DEFAULT_CATEGORY_ID,
    }
    if selected_tags:
        snippet["tags"] = selected_tags

    status: dict[str, Any] = {
        "privacyStatus": selected_privacy_status,
        "selfDeclaredMadeForKids": bool(self_declared_made_for_kids) if self_declared_made_for_kids is not None else False,
    }
    if contains_synthetic_media is not None:
        status["containsSyntheticMedia"] = bool(contains_synthetic_media)

    return {
        "snippet": snippet,
        "status": status,
    }


def _normalize_privacy_status(privacy_status: str | None) -> str:
    selected = str(privacy_status or YOUTUBE_DEFAULT_PRIVACY_STATUS or "private").strip().lower()
    if selected not in _VALID_PRIVACY_STATUSES:
        raise HTTPException(status_code=400, detail="YouTube privacy_status must be one of public, private, or unlisted.")
    return selected


def _asset_type(asset: dict[str, Any]) -> str:
    return str(asset.get("assetType") or asset.get("asset_type") or "").strip().lower()


def _asset_blocks(asset: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = asset.get("blocks")
    return [block for block in blocks if isinstance(block, dict)] if isinstance(blocks, list) else []


def _asset_output(asset: dict[str, Any]) -> dict[str, Any]:
    output = asset.get("output")
    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _block_map(asset: dict[str, Any]) -> dict[str, Any]:
    block_map: dict[str, Any] = {}
    for block in _asset_blocks(asset):
        key = str(block.get("key") or block.get("label") or "").strip().lower()
        if key:
            block_map[key] = block.get("value")
    return block_map


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("title", "description", "body", "content", "text", "summary", "caption", "hook", "script", "shorts_script", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        parts = [_normalize_text(candidate) for candidate in value.values()]
        return "\n".join(part for part in parts if part)
    if isinstance(value, list):
        parts = [_normalize_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    return str(value).strip()


def _build_title_from_asset(asset: dict[str, Any], *, explicit_title: str | None) -> str:
    output = _asset_output(asset)
    media = asset.get("media") if isinstance(asset.get("media"), dict) else {}
    generated_clip = asset.get("generated_clip") if isinstance(asset.get("generated_clip"), dict) else {}
    for candidate in (
        explicit_title,
        asset.get("title"),
        output.get("title"),
        generated_clip.get("title"),
        media.get("label"),
        _block_map(asset).get("title"),
        _block_map(asset).get("hook"),
    ):
        text = _normalize_text(candidate)
        if text:
            return text[:_MAX_TITLE_LENGTH]
    return "Generated YouTube Short"


def _build_description_from_asset(asset: dict[str, Any], *, explicit_description: str | None) -> str:
    output = _asset_output(asset)
    block_map = _block_map(asset)
    chunks: list[str] = []
    for candidate in (
        explicit_description,
        asset.get("description"),
        asset.get("caption"),
        output.get("description"),
        output.get("shorts_script"),
        output.get("cta"),
        block_map.get("description"),
        block_map.get("caption"),
        block_map.get("script"),
        block_map.get("shorts_script"),
    ):
        text = _normalize_text(candidate)
        if text and text not in chunks:
            chunks.append(text)

    hashtags = _build_hashtags(asset)
    if hashtags:
        chunks.append(" ".join(hashtags))

    description = "\n\n".join(chunks).strip() or "Generated with ContentOS."
    return description[:_MAX_DESCRIPTION_LENGTH]


def _build_tags_from_asset(asset: dict[str, Any], *, explicit_tags: list[str] | None) -> list[str]:
    raw_tags: list[Any] = []
    if explicit_tags:
        raw_tags.extend(explicit_tags)

    output = _asset_output(asset)
    for source in (asset.get("tags"), asset.get("hashtags"), output.get("tags"), output.get("hashtags")):
        if isinstance(source, list):
            raw_tags.extend(source)
        elif isinstance(source, str):
            raw_tags.extend(part.strip() for part in source.split(","))

    normalized: list[str] = []
    for tag in raw_tags:
        text = str(tag or "").strip().lstrip("#")
        if text and text.lower() not in {existing.lower() for existing in normalized}:
            normalized.append(text[:30])
    return normalized[:15]


def _build_hashtags(asset: dict[str, Any]) -> list[str]:
    output = _asset_output(asset)
    candidates: list[Any] = []
    for source in (asset.get("hashtags"), output.get("hashtags")):
        if isinstance(source, list):
            candidates.extend(source)
        elif isinstance(source, str):
            candidates.extend(part.strip() for part in source.replace(",", " ").split())

    hashtags: list[str] = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        if not text.startswith("#"):
            text = f"#{text.lstrip('#')}"
        if text.lower() not in {existing.lower() for existing in hashtags}:
            hashtags.append(text)
    return hashtags[:8]


def _youtube_config_error() -> str:
    missing = []
    if not YOUTUBE_CLIENT_ID:
        missing.append("YOUTUBE_CLIENT_ID")
    if not YOUTUBE_CLIENT_SECRET:
        missing.append("YOUTUBE_CLIENT_SECRET")
    if not YOUTUBE_REDIRECT_URI:
        missing.append("YOUTUBE_REDIRECT_URI")
    if missing:
        return f"YouTube is not configured yet. Missing: {', '.join(missing)}."
    return ""


def _require_youtube_config() -> None:
    error = _youtube_config_error()
    if error:
        raise HTTPException(status_code=500, detail=error)


def _youtube_http_error_result(error: httpx.HTTPStatusError, *, default_message: str) -> dict[str, Any]:
    response = error.response
    message = default_message
    try:
        payload = response.json()
        error_payload = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error_payload, dict):
            candidate_message = str(error_payload.get("message") or "").strip()
            if candidate_message:
                message = candidate_message
            errors = error_payload.get("errors")
            if isinstance(errors, list) and errors:
                reason = str(errors[0].get("reason") or "").strip() if isinstance(errors[0], dict) else ""
                if reason:
                    message = f"{message} ({reason})"
    except ValueError:
        pass

    return {
        "ok": False,
        "error": "youtube_request_failed",
        "message": message,
        "status_code": response.status_code,
        "response_text": response.text,
    }


def _http_exception_result(error: HTTPException, *, default_error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": default_error,
        "message": error.detail if isinstance(error.detail, str) else "YouTube request failed.",
        "status_code": error.status_code,
    }


def _safe_exception_message(error: Exception, *, fallback: str) -> str:
    detail = str(error).strip()
    if not detail:
        return fallback
    detail = detail.replace(YOUTUBE_CLIENT_SECRET, "[redacted]") if YOUTUBE_CLIENT_SECRET else detail
    if len(detail) > 220:
        detail = detail[:217].rstrip() + "..."
    return f"{fallback}: {type(error).__name__}: {detail}"
