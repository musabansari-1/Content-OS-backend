from __future__ import annotations

import secrets
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from starlette.responses import RedirectResponse

from app.core.config import env
from app.integrations_repository import SocialIntegrationRecord, SocialIntegrationRepository
from app.services.generation_service import GENERATED_CLIPS_DIR


def _first_env(*names: str) -> str:
    for name in names:
        value = env(name, "") or ""
        if value.strip():
            return value.strip()
    return ""


INSTAGRAM_APP_ID = _first_env("INSTAGRAM_APP_ID", "INSTAGRAM_CLIENT_ID")
INSTAGRAM_APP_SECRET = _first_env("INSTAGRAM_APP_SECRET", "INSTAGRAM_CLIENT_SECRET")
INSTAGRAM_REDIRECT_URI = env("INSTAGRAM_REDIRECT_URI", "") or ""
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", "http://localhost:3000") or "http://localhost:3000"
PUBLIC_BASE_URL = (env("PUBLIC_BASE_URL", "http://localhost:8000") or "http://localhost:8000").rstrip("/")

INSTAGRAM_PLATFORM = "instagram"
INSTAGRAM_API_VERSION = "v25.0"
INSTAGRAM_AUTH_URL = "https://www.instagram.com/oauth/authorize"
INSTAGRAM_SHORT_LIVED_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
INSTAGRAM_LONG_LIVED_TOKEN_URL = "https://graph.instagram.com/access_token"
INSTAGRAM_REFRESH_TOKEN_URL = "https://graph.instagram.com/refresh_access_token"
INSTAGRAM_GRAPH_URL = f"https://graph.instagram.com/{INSTAGRAM_API_VERSION}"
INSTAGRAM_SCOPES = (
    "instagram_business_basic",
    "instagram_business_content_publish",
)

_OAUTH_STATE_TTL_SECONDS = 600
_TOKEN_REFRESH_MIN_AGE = timedelta(hours=24)
_TOKEN_REFRESH_SKEW = timedelta(days=7)
_instagram_oauth_state_lock = threading.Lock()
_instagram_oauth_state_store: dict[str, dict[str, int]] = {}
social_integration_repository = SocialIntegrationRepository()


def start_instagram_auth(*, user_id: int) -> str:
    _require_instagram_config()
    state = secrets.token_urlsafe(32)
    _store_instagram_oauth_state(state, user_id)

    params = {
        "client_id": INSTAGRAM_APP_ID,
        "redirect_uri": INSTAGRAM_REDIRECT_URI,
        "response_type": "code",
        "scope": ",".join(INSTAGRAM_SCOPES),
        "state": state,
    }
    return f"{INSTAGRAM_AUTH_URL}?{urlencode(params)}"


async def handle_instagram_callback(
    code: str = None,
    state: str = None,
    error: str = None,
    error_reason: str = None,
    error_description: str = None,
) -> RedirectResponse:
    if error:
        reason = str(error_reason or error or "authorization").strip().lower().replace(" ", "_")
        return RedirectResponse(_build_frontend_error_redirect(INSTAGRAM_PLATFORM, reason), status_code=302)

    if not code or not state:
        return RedirectResponse(_build_frontend_error_redirect(INSTAGRAM_PLATFORM, "missing_code_or_state"), status_code=302)

    config_error = _instagram_config_error()
    if config_error:
        return RedirectResponse(_build_frontend_error_redirect(INSTAGRAM_PLATFORM, config_error), status_code=302)

    user_id = _pop_instagram_oauth_state(state)
    if user_id is None:
        return RedirectResponse(_build_frontend_error_redirect(INSTAGRAM_PLATFORM, "expired_state"), status_code=302)

    try:
        token_data = await _exchange_instagram_code_for_short_lived_token(code)
        short_lived_access_token = _extract_short_lived_access_token(token_data)
        if not short_lived_access_token:
            return RedirectResponse(
                _build_frontend_error_redirect(INSTAGRAM_PLATFORM, "missing_access_token"),
                status_code=302,
            )

        long_lived_token_data = await _exchange_instagram_token_for_long_lived_token(short_lived_access_token)
        access_token = str(long_lived_token_data.get("access_token") or "").strip()
        if not access_token:
            return RedirectResponse(
                _build_frontend_error_redirect(INSTAGRAM_PLATFORM, "missing_long_lived_access_token"),
                status_code=302,
            )

        account_data = await _fetch_instagram_account_profile(access_token)
        social_integration_repository.upsert_connection(
            user_id=user_id,
            platform=INSTAGRAM_PLATFORM,
            platform_user_id=account_data["instagram_user_id"],
            platform_username=account_data["username"],
            access_token=access_token,
            refresh_token=None,
            scope=_extract_granted_permissions(token_data) or ",".join(INSTAGRAM_SCOPES),
            token_type=str(long_lived_token_data.get("token_type") or "bearer").strip() or "bearer",
            expires_in=long_lived_token_data.get("expires_in"),
        )

        return RedirectResponse(
            _build_frontend_redirect(INSTAGRAM_PLATFORM, "connected"),
            status_code=302,
        )
    except httpx.HTTPStatusError as error:
        return RedirectResponse(
            _build_frontend_error_redirect(INSTAGRAM_PLATFORM, f"token_{error.response.status_code}"),
            status_code=302,
        )
    except httpx.HTTPError:
        return RedirectResponse(_build_frontend_error_redirect(INSTAGRAM_PLATFORM, "http_error"), status_code=302)
    except Exception:
        return RedirectResponse(_build_frontend_error_redirect(INSTAGRAM_PLATFORM, "exception"), status_code=302)


async def publish_instagram_asset_for_user(*, user_id: int, asset: dict) -> dict:
    config_error = _instagram_config_error()
    if config_error:
        return {
            "ok": False,
            "error": "instagram_not_configured",
            "message": config_error,
        }

    asset_type = _asset_type(asset)
    if asset_type not in {"instagram_reel", "instagram_carousel"}:
        return {
            "ok": False,
            "error": "instagram_unsupported_asset",
            "message": "Only Instagram reel and carousel assets can be published directly.",
        }

    connection = social_integration_repository.get_by_user_and_platform(user_id=user_id, platform=INSTAGRAM_PLATFORM)
    if connection is None:
        return {
            "ok": False,
            "error": "instagram_not_connected",
            "message": "Connect Instagram before publishing.",
        }

    if not connection.access_token or not connection.platform_user_id:
        return {
            "ok": False,
            "error": "instagram_connection_incomplete",
            "message": "Your Instagram connection is missing token data.",
        }

    try:
        access_token = await _access_token_for_connection(connection)
        if asset_type == "instagram_reel":
            return await _publish_instagram_reel(access_token, connection.platform_user_id, asset)
        return await _publish_instagram_carousel(access_token, connection.platform_user_id, asset)
    except HTTPException as error:
        return {
            "ok": False,
            "error": "instagram_publish_failed",
            "message": error.detail if isinstance(error.detail, str) else "Instagram publish failed.",
        }
    except httpx.HTTPStatusError as error:
        response = error.response
        return {
            "ok": False,
            "error": "instagram_publish_failed",
            "message": "Instagram rejected the post request.",
            "status_code": response.status_code,
            "response_text": response.text,
        }
    except Exception:
        return {
            "ok": False,
            "error": "instagram_publish_failed",
            "message": "Instagram publish failed unexpectedly.",
        }


def _store_instagram_oauth_state(state: str, user_id: int) -> None:
    expires_at = int(time.time()) + _OAUTH_STATE_TTL_SECONDS
    with _instagram_oauth_state_lock:
        _instagram_oauth_state_store[state] = {
            "user_id": user_id,
            "expires_at": expires_at,
        }


def _pop_instagram_oauth_state(state: str | None) -> int | None:
    if not state:
        return None

    with _instagram_oauth_state_lock:
        payload = _instagram_oauth_state_store.pop(state, None)

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


async def _exchange_instagram_code_for_short_lived_token(code: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            INSTAGRAM_SHORT_LIVED_TOKEN_URL,
            data={
                "client_id": INSTAGRAM_APP_ID,
                "client_secret": INSTAGRAM_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": INSTAGRAM_REDIRECT_URI,
                "code": code,
            },
        )
        response.raise_for_status()
    return response.json()


def _extract_short_lived_access_token(payload: dict) -> str:
    data = _extract_single_data_object(payload)
    access_token = ""
    if isinstance(data, dict):
        access_token = str(data.get("access_token") or "").strip()
    if access_token:
        return access_token
    return str(payload.get("access_token") or "").strip()


def _extract_granted_permissions(payload: dict) -> str:
    data = _extract_single_data_object(payload)
    if isinstance(data, dict):
        permissions = data.get("permissions")
        if isinstance(permissions, list):
            normalized = [str(permission).strip() for permission in permissions if str(permission).strip()]
            return ",".join(normalized)
        return str(permissions or "").strip()
    permissions = payload.get("permissions")
    if isinstance(permissions, list):
        normalized = [str(permission).strip() for permission in permissions if str(permission).strip()]
        return ",".join(normalized)
    return str(permissions or "").strip()


def _extract_single_data_object(payload: dict) -> dict | None:
    data = payload.get("data")
    if isinstance(data, list) and data:
        first_item = data[0]
        if isinstance(first_item, dict):
            return first_item
    if isinstance(data, dict):
        return data
    return payload if isinstance(payload, dict) else None


async def _exchange_instagram_token_for_long_lived_token(short_lived_access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            INSTAGRAM_LONG_LIVED_TOKEN_URL,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": INSTAGRAM_APP_SECRET,
                "access_token": short_lived_access_token,
            },
        )
        response.raise_for_status()
    return response.json()


async def _refresh_instagram_access_token(connection: SocialIntegrationRecord) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            INSTAGRAM_REFRESH_TOKEN_URL,
            params={
                "grant_type": "ig_refresh_token",
                "access_token": connection.access_token,
            },
        )
        response.raise_for_status()

    token_data = response.json()
    access_token = str(token_data.get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(status_code=502, detail="Instagram did not return a refreshed access token.")

    social_integration_repository.upsert_connection(
        user_id=connection.user_id,
        platform=INSTAGRAM_PLATFORM,
        platform_user_id=connection.platform_user_id,
        platform_username=connection.platform_username,
        access_token=access_token,
        refresh_token=None,
        scope=connection.scope,
        token_type=str(token_data.get("token_type") or connection.token_type or "bearer").strip() or "bearer",
        expires_in=token_data.get("expires_in"),
    )
    return access_token


async def _access_token_for_connection(connection: SocialIntegrationRecord) -> str:
    if not connection.access_token:
        raise HTTPException(status_code=409, detail="Your Instagram connection is missing token data.")

    expires_at = _ensure_aware(connection.token_expires_at)
    updated_at = _ensure_aware(connection.updated_at)
    now = datetime.now(timezone.utc)

    if expires_at and expires_at <= now:
        raise HTTPException(status_code=409, detail="Your Instagram connection has expired. Reconnect Instagram.")

    if (
        expires_at
        and updated_at
        and expires_at <= now + _TOKEN_REFRESH_SKEW
        and updated_at <= now - _TOKEN_REFRESH_MIN_AGE
    ):
        return await _refresh_instagram_access_token(connection)

    return connection.access_token


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _asset_type(asset: dict) -> str:
    return str(asset.get("assetType") or asset.get("asset_type") or "").strip().lower()


def _asset_title(asset: dict) -> str:
    title = str(asset.get("title") or "").strip()
    return title or "Instagram post"


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
        for key in ("title", "body", "content", "text", "summary", "caption", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        parts = [_normalize_text(candidate) for candidate in value.values()]
        return "\n".join(part for part in parts if part)
    if isinstance(value, list):
        parts = [_normalize_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    return str(value).strip()


def _build_caption_from_asset(asset: dict) -> str:
    title = _asset_title(asset)
    blocks = _asset_blocks(asset)
    chunks: list[str] = [title]

    for block in blocks:
        block_label = str(block.get("label") or block.get("key") or "").strip()
        block_value = _normalize_text(block.get("value"))
        if not block_value:
            continue
        if block_label.lower() in {"slides", "slide", "carousel"}:
            continue
        if block_label:
            chunks.append(f"{block_label}: {block_value}")
        else:
            chunks.append(block_value)

    caption = "\n\n".join(chunk for chunk in chunks if chunk).strip()
    return caption[:2200] if len(caption) > 2200 else caption


def _extract_carousel_slides(asset: dict) -> list[str]:
    blocks = _asset_blocks(asset)
    slide_candidates: list[str] = []

    for block in blocks:
        key = str(block.get("key") or "").lower()
        value = block.get("value")
        if isinstance(value, list) and (key.startswith("slide") or "carousel" in key or key == "slides"):
            slide_candidates = [_normalize_text(item) for item in value]
            break

    if not slide_candidates:
        for block in blocks:
            value = block.get("value")
            if isinstance(value, list) and len(value) >= 2:
                normalized_items = [_normalize_text(item) for item in value]
                if sum(1 for item in normalized_items if item) >= 2:
                    slide_candidates = normalized_items
                    break

    if not slide_candidates:
        slide_candidates = [
            _normalize_text(block.get("value"))
            for block in blocks
            if _normalize_text(block.get("value"))
        ]

    return [slide for slide in slide_candidates if slide][:10]


def _wrap_text_to_width(text: str, *, max_chars: int) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    lines = textwrap.wrap(normalized, width=max_chars)
    return lines or [normalized]


def _render_carousel_slide(slide_text: str, *, index: int, total: int, title: str) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise HTTPException(
            status_code=500,
            detail="Pillow is required to render Instagram carousel slides.",
        ) from exc

    canvas_width = 1080
    canvas_height = 1350
    bg_color = (18, 18, 22)
    panel_color = (28, 28, 36)
    accent_color = (242, 116, 86)
    text_color = (248, 248, 250)
    muted_color = (176, 176, 190)

    image = Image.new("RGB", (canvas_width, canvas_height), bg_color)
    draw = ImageDraw.Draw(image)

    margin = 88
    draw.rounded_rectangle(
        (40, 40, canvas_width - 40, canvas_height - 40),
        radius=48,
        fill=panel_color,
    )
    draw.rectangle((margin, 120, canvas_width - margin, 126), fill=accent_color)

    try:
        title_font = ImageFont.truetype("arial.ttf", 56)
        body_font = ImageFont.truetype("arial.ttf", 40)
        meta_font = ImageFont.truetype("arial.ttf", 30)
    except Exception:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        meta_font = ImageFont.load_default()

    draw.text((margin, 160), title[:56], fill=muted_color, font=meta_font)
    draw.text((margin, 220), f"{index + 1:02d}", fill=accent_color, font=title_font)

    wrapped_lines = _wrap_text_to_width(slide_text, max_chars=24)
    y = 380
    for line in wrapped_lines[:12]:
        draw.text((margin, y), line, fill=text_color, font=body_font)
        y += 64 if body_font != ImageFont.load_default() else 20

    footer = f"Slide {index + 1} of {total}"
    footer_box = draw.textbbox((0, 0), footer, font=meta_font)
    footer_width = footer_box[2] - footer_box[0]
    draw.text(
        (canvas_width - margin - footer_width, canvas_height - margin - 24),
        footer,
        fill=muted_color,
        font=meta_font,
    )

    safe_title = "".join(ch for ch in title.lower() if ch.isalnum() or ch in ("-", "_", " ")).strip()
    safe_title = safe_title.replace(" ", "-")[:36] or "instagram-carousel"
    file_name = f"{safe_title}-{secrets.token_hex(4)}-{index + 1}.png"
    file_path = GENERATED_CLIPS_DIR / "instagram" / file_name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(file_path, format="PNG")
    return file_path


def _public_media_url(file_path: Path) -> str:
    relative_path = file_path.relative_to(GENERATED_CLIPS_DIR).as_posix()
    return f"{PUBLIC_BASE_URL}/generated-clips/{relative_path}"


async def _fetch_instagram_account_profile(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{INSTAGRAM_GRAPH_URL}/me",
            params={
                "fields": "user_id,username",
                "access_token": access_token,
            },
        )
        response.raise_for_status()

    payload = response.json()
    profile = _extract_single_data_object(payload) or {}
    instagram_user_id = str(profile.get("user_id") or "").strip()
    username = str(profile.get("username") or "").strip()
    if not instagram_user_id or not username:
        raise HTTPException(status_code=502, detail="Instagram did not return the professional account id and username.")

    return {
        "instagram_user_id": instagram_user_id,
        "username": username,
    }


def _instagram_config_error() -> str:
    missing = []
    if not INSTAGRAM_APP_ID:
        missing.append("INSTAGRAM_APP_ID or INSTAGRAM_CLIENT_ID")
    if not INSTAGRAM_APP_SECRET:
        missing.append("INSTAGRAM_APP_SECRET or INSTAGRAM_CLIENT_SECRET")
    if not INSTAGRAM_REDIRECT_URI:
        missing.append("INSTAGRAM_REDIRECT_URI")
    if missing:
        return f"Instagram is not configured yet. Missing: {', '.join(missing)}."
    return ""


def _require_instagram_config() -> None:
    error = _instagram_config_error()
    if error:
        raise HTTPException(status_code=500, detail=error)


async def _create_instagram_media_container(
    *,
    access_token: str,
    instagram_user_id: str,
    payload: dict,
) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{INSTAGRAM_GRAPH_URL}/{instagram_user_id}/media",
            data={**payload, "access_token": access_token},
        )
        response.raise_for_status()

    data = response.json()
    creation_id = str(data.get("id") or "").strip()
    if not creation_id:
        raise HTTPException(status_code=502, detail="Instagram did not return a media container id.")
    return creation_id


async def _publish_instagram_container(
    *,
    access_token: str,
    instagram_user_id: str,
    creation_id: str,
) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{INSTAGRAM_GRAPH_URL}/{instagram_user_id}/media_publish",
            data={
                "creation_id": creation_id,
                "access_token": access_token,
            },
        )
        response.raise_for_status()
    return response.json() if response.content else {}


async def _publish_instagram_reel(access_token: str, instagram_user_id: str, asset: dict) -> dict:
    media = asset.get("media") if isinstance(asset.get("media"), dict) else {}
    video_url = str(media.get("videoUrl") or media.get("video_url") or "").strip()
    if not video_url:
        return {
            "ok": False,
            "error": "instagram_reel_missing_video",
            "message": "This Instagram reel does not include a video URL to publish.",
        }

    caption = _build_caption_from_asset(asset)
    creation_id = await _create_instagram_media_container(
        access_token=access_token,
        instagram_user_id=instagram_user_id,
        payload={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
        },
    )
    publish_result = await _publish_instagram_container(
        access_token=access_token,
        instagram_user_id=instagram_user_id,
        creation_id=creation_id,
    )
    return {
        "ok": True,
        "platform": "instagram",
        "asset_type": "instagram_reel",
        "instagram_user_id": instagram_user_id,
        "instagram_post_id": publish_result.get("id") or creation_id,
        "creation_id": creation_id,
    }


async def _publish_instagram_carousel(access_token: str, instagram_user_id: str, asset: dict) -> dict:
    slides = _extract_carousel_slides(asset)
    if len(slides) < 2:
        return {
            "ok": False,
            "error": "instagram_carousel_missing_slides",
            "message": "This Instagram carousel does not have enough slides to publish.",
        }

    caption = _build_caption_from_asset(asset)
    title = _asset_title(asset)
    child_ids: list[str] = []
    slide_paths: list[Path] = []
    for index, slide_text in enumerate(slides):
        file_path = _render_carousel_slide(slide_text, index=index, total=len(slides), title=title)
        slide_paths.append(file_path)
        child_id = await _create_instagram_media_container(
            access_token=access_token,
            instagram_user_id=instagram_user_id,
            payload={
                "image_url": _public_media_url(file_path),
                "is_carousel_item": "true",
            },
        )
        child_ids.append(child_id)

    creation_id = await _create_instagram_media_container(
        access_token=access_token,
        instagram_user_id=instagram_user_id,
        payload={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
        },
    )
    publish_result = await _publish_instagram_container(
        access_token=access_token,
        instagram_user_id=instagram_user_id,
        creation_id=creation_id,
    )

    return {
        "ok": True,
        "platform": "instagram",
        "asset_type": "instagram_carousel",
        "instagram_user_id": instagram_user_id,
        "instagram_post_id": publish_result.get("id") or creation_id,
        "creation_id": creation_id,
        "rendered_slide_paths": [str(path) for path in slide_paths],
    }
