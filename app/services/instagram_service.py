from __future__ import annotations

import asyncio
import logging
import secrets
import textwrap
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
from app.services.generation_service import GENERATED_CLIPS_DIR, _upload_generated_clip


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
_INSTAGRAM_PUBLISH_RETRY_ATTEMPTS = 5
_INSTAGRAM_PUBLISH_RETRY_DELAY_SECONDS = 3
_instagram_oauth_state_lock = threading.Lock()
_instagram_oauth_state_store: dict[str, dict[str, int]] = {}
social_integration_repository = SocialIntegrationRepository()
logger = logging.getLogger(__name__)


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
        error_key = (
            "instagram_invalid_asset"
            if error.status_code == 400
            else "instagram_connection_incomplete"
            if error.status_code == 409
            else "instagram_publish_failed"
        )
        return {
            "ok": False,
            "error": error_key,
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


def _is_structured_slide_candidate(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(
            _normalize_text(value.get(key))
            for key in (
                "title",
                "hook",
                "heading",
                "headline",
                "body",
                "content",
                "text",
                "description",
                "caption",
                "quote",
                "cta",
                "call_to_action",
            )
        )
    return False


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


def _extract_carousel_slide_payloads(asset: dict) -> list[object]:
    blocks = _asset_blocks(asset)
    slide_candidates: list[object] = []

    for block in blocks:
        key = str(block.get("key") or "").lower()
        value = block.get("value")
        if isinstance(value, list) and (key.startswith("slide") or "carousel" in key or key == "slides"):
            slide_candidates = [item for item in value if _is_structured_slide_candidate(item)]
            break

    if not slide_candidates:
        for block in blocks:
            value = block.get("value")
            if isinstance(value, list) and len(value) >= 2:
                normalized_items = [item for item in value if _is_structured_slide_candidate(item)]
                if len(normalized_items) >= 2:
                    slide_candidates = normalized_items
                    break

    if not slide_candidates:
        slide_candidates = [
            _normalize_text(block.get("value"))
            for block in blocks
            if _normalize_text(block.get("value"))
        ]

    return [slide for slide in slide_candidates if _is_structured_slide_candidate(slide)][:10]


def _split_slide_text(value: object) -> dict[str, object]:
    text = _normalize_text(value)
    if not text:
        return {"title": "", "body": "", "items": []}

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullet_lines = [line.replace("- ", "", 1).replace("* ", "", 1).strip() for line in lines]
    bullet_lines = [line for line in bullet_lines if line]

    if len(bullet_lines) >= 3:
        return {
            "title": bullet_lines[0],
            "body": "",
            "items": bullet_lines[1:5],
        }

    if len(lines) >= 2:
        return {
            "title": lines[0],
            "body": " ".join(lines[1:]),
            "items": [],
        }

    if len(text) > 110:
        sentence_break = -1
        for marker in (". ", "! ", "? "):
            candidate = text.find(marker)
            if candidate > sentence_break:
                sentence_break = candidate + 1
        if sentence_break > 30:
            return {
                "title": text[: sentence_break + 1].strip(),
                "body": text[sentence_break + 1 :].strip(),
                "items": [],
            }
        chunk = text[:72]
        split_at = chunk.rfind(" ")
        if split_at <= 24:
            split_at = 72
        return {
            "title": text[:split_at].strip(),
            "body": text[split_at:].strip(),
            "items": [],
        }

    return {"title": text, "body": "", "items": []}


def _normalize_carousel_slide(raw: object, *, index: int, total: int) -> dict[str, object]:
    if isinstance(raw, str):
        parsed = _split_slide_text(raw)
        return {
            "type": "hook" if index == 0 else "cta" if index == total - 1 else "content",
            "title": parsed["title"],
            "body": parsed["body"],
            "items": parsed["items"],
            "quote": parsed["title"],
            "cta": parsed["body"] or parsed["title"],
            "eyebrow": "",
        }

    if isinstance(raw, dict):
        normalized = {
            "type": raw.get("type") or ("hook" if index == 0 else "cta" if index == total - 1 else "content"),
            "title": _normalize_text(raw.get("title") or raw.get("hook") or raw.get("heading") or raw.get("headline")),
            "body": _normalize_text(
                raw.get("body") or raw.get("content") or raw.get("text") or raw.get("description") or raw.get("caption")
            ),
            "items": (
                raw.get("items")
                if isinstance(raw.get("items"), list)
                else raw.get("points")
                if isinstance(raw.get("points"), list)
                else raw.get("tips")
                if isinstance(raw.get("tips"), list)
                else []
            ),
            "quote": _normalize_text(raw.get("quote") or raw.get("insight") or raw.get("title")),
            "cta": _normalize_text(raw.get("cta") or raw.get("call_to_action") or raw.get("action") or raw.get("title")),
            "eyebrow": _normalize_text(
                raw.get("eyebrow") or raw.get("label") or raw.get("meta") or raw.get("kicker") or raw.get("category")
            ),
        }
        if not normalized["body"] and not normalized["items"] and len(str(normalized["title"])) > 110:
            parsed = _split_slide_text(normalized["title"])
            normalized["title"] = parsed["title"]
            normalized["body"] = parsed["body"]
            normalized["items"] = parsed["items"]
        return normalized

    return _normalize_carousel_slide(_normalize_text(raw), index=index, total=total)


def _wrap_text_to_width(text: str, *, max_chars: int) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    lines = textwrap.wrap(normalized, width=max_chars)
    return lines or [normalized]


_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "arial.ttf": ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf"),
    "arialbd.ttf": ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"),
    "georgiab.ttf": ("georgiab.ttf", "Georgia Bold.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSans-Bold.ttf"),
}


def _load_font(name: str, size: int):
    from PIL import ImageFont

    candidates = _FONT_CANDIDATES.get(name, (name, "DejaVuSans.ttf"))
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return None


def _font_bundle():
    try:
        from PIL import ImageFont
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise HTTPException(
            status_code=500,
            detail="Pillow is required to render Instagram carousel slides.",
        ) from exc

    regular = (
        _load_font("arial.ttf", 40)
        or ImageFont.load_default()
    )
    bold = (
        _load_font("arialbd.ttf", 40)
        or regular
    )
    return regular, bold


def _fit_text(draw, text: str, *, font_name: str, max_size: int, min_size: int, max_width: int, max_height: int):
    from PIL import ImageFont

    words = text.split()
    if not words:
        return ImageFont.load_default(), []

    def wrap_for_font(font):
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if current and bbox[2] - bbox[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    for size in range(max_size, min_size - 1, -2):
        font = _load_font(font_name, size)
        if font is None:
            continue
        lines = wrap_for_font(font)
        line_box = draw.textbbox((0, 0), "Ag", font=font)
        line_height = line_box[3] - line_box[1] + max(8, size // 5)
        total_height = line_height * len(lines)
        if total_height <= max_height:
            return font, lines

    fallback = _load_font(font_name, min_size) or ImageFont.load_default()
    return fallback, wrap_for_font(fallback)


def _draw_footer(draw, *, width: int, height: int, footer_left: str, index: int, total: int, font, color):
    slide_num = f"{index + 1:02d}"
    draw.text((84, height - 96), footer_left, fill=color, font=font)
    num_box = draw.textbbox((0, 0), slide_num, font=font)
    draw.text((width - 84 - (num_box[2] - num_box[0]), height - 96), slide_num, fill=color, font=font)


def _render_hook_or_cta_slide(draw, *, width: int, height: int, slide: dict[str, object], index: int, footer: str, is_cta: bool):
    regular_font, bold_font = _font_bundle()
    bg = (245, 244, 240) if not is_cta else (255, 246, 236)
    draw.rectangle((0, 0, width, height), fill=bg)
    draw.text((84, 84), str(slide.get("eyebrow") or ("Final slide" if is_cta else "Carousel")).upper(), fill=(55, 65, 81), font=_load_font("arialbd.ttf", 20) or bold_font)
    draw.rounded_rectangle((84, 138, 164, 146), radius=4, fill=(249, 115, 22))

    title_font, title_lines = _fit_text(
        draw,
        str(slide.get("title") or "Hook goes here"),
        font_name="arialbd.ttf",
        max_size=88,
        min_size=42,
        max_width=width - 168,
        max_height=430,
    )
    y = 192
    title_line_height = draw.textbbox((0, 0), "Ag", font=title_font)[3] + max(10, getattr(title_font, "size", 42) // 5)
    for line in title_lines[:5]:
        draw.text((84, y), line, fill=(15, 23, 42), font=title_font)
        y += title_line_height

    body = str(slide.get("body") or "").strip()
    if body:
        body_font, body_lines = _fit_text(
            draw,
            body,
            font_name="arial.ttf",
            max_size=38,
            min_size=22,
            max_width=width - 168,
            max_height=220 if not is_cta else 180,
        )
        y += 18
        body_line_height = draw.textbbox((0, 0), "Ag", font=body_font)[3] + 10
        for line in body_lines[:6]:
            draw.text((84, y), line, fill=(75, 85, 99), font=body_font)
            y += body_line_height

    if is_cta:
        cta_text = str(slide.get("cta") or slide.get("title") or "Follow for more")
        box_top = height - 300
        draw.rounded_rectangle((84, box_top, width - 84, height - 104), radius=34, fill=(255, 237, 213))
        cta_font, cta_lines = _fit_text(
            draw,
            cta_text,
            font_name="arialbd.ttf",
            max_size=42,
            min_size=24,
            max_width=width - 220,
            max_height=120,
        )
        cy = box_top + 34
        cta_line_height = draw.textbbox((0, 0), "Ag", font=cta_font)[3] + 10
        for line in cta_lines[:3]:
            draw.text((118, cy), line, fill=(124, 45, 18), font=cta_font)
            cy += cta_line_height
        pill_font = _load_font("arialbd.ttf", 24) or bold_font
        pill_text = "Save this post"
        pill_box = draw.textbbox((0, 0), pill_text, font=pill_font)
        pill_width = (pill_box[2] - pill_box[0]) + 44
        pill_height = (pill_box[3] - pill_box[1]) + 24
        pill_left = 118
        pill_top = height - 170
        draw.rounded_rectangle((pill_left, pill_top, pill_left + pill_width, pill_top + pill_height), radius=999, fill=(249, 115, 22))
        draw.text((pill_left + 22, pill_top + 12), pill_text, fill=(255, 255, 255), font=pill_font)
    else:
        _draw_footer(draw, width=width, height=height, footer_left=footer, index=index, total=0, font=_load_font("arialbd.ttf", 22) or bold_font, color=(55, 65, 81))

    if is_cta:
        _draw_footer(draw, width=width, height=height, footer_left="Save this", index=index, total=0, font=_load_font("arialbd.ttf", 22) or bold_font, color=(124, 45, 18))


def _render_content_slide(draw, *, width: int, height: int, slide: dict[str, object], index: int):
    regular_font, bold_font = _font_bundle()
    draw.rectangle((0, 0, width, height), fill=(15, 23, 42))
    meta_font = _load_font("arialbd.ttf", 20) or bold_font
    draw.text((84, 84), str(slide.get("eyebrow") or f"Slide {index + 1}").upper(), fill=(148, 163, 184), font=meta_font)

    title = str(slide.get("title") or "").strip()
    y = 136
    if title:
        title_font, title_lines = _fit_text(
            draw,
            title,
            font_name="arialbd.ttf",
            max_size=58,
            min_size=28,
            max_width=width - 168,
            max_height=180,
        )
        line_height = draw.textbbox((0, 0), "Ag", font=title_font)[3] + 10
        for line in title_lines[:4]:
            draw.text((84, y), line, fill=(248, 250, 252), font=title_font)
            y += line_height
        y += 20

    items = slide.get("items") if isinstance(slide.get("items"), list) else []
    if items:
        row_top = y
        for item_index, item in enumerate(items[:4]):
            draw.line((84, row_top, width - 84, row_top), fill=(51, 65, 85), width=2)
            row_y = row_top + 18
            draw.ellipse((84, row_y, 132, row_y + 48), fill=(61, 33, 19), outline=(251, 146, 60))
            num_font = _load_font("arialbd.ttf", 22) or bold_font
            draw.text((102, row_y + 11), str(item_index + 1), fill=(251, 146, 60), font=num_font)

            if isinstance(item, dict):
                item_title = _normalize_text(item.get("title") or item.get("text") or item.get("point") or item.get("value"))
                item_body = _normalize_text(item.get("body") or item.get("description"))
            else:
                item_title = _normalize_text(item)
                item_body = ""

            item_title_font, item_title_lines = _fit_text(
                draw,
                item_title,
                font_name="arialbd.ttf",
                max_size=28,
                min_size=20,
                max_width=width - 240,
                max_height=70,
            )
            text_x = 156
            ty = row_y
            title_line_height = draw.textbbox((0, 0), "Ag", font=item_title_font)[3] + 6
            for line in item_title_lines[:2]:
                draw.text((text_x, ty), line, fill=(248, 250, 252), font=item_title_font)
                ty += title_line_height
            if item_body:
                item_body_font, item_body_lines = _fit_text(
                    draw,
                    item_body,
                    font_name="arial.ttf",
                    max_size=20,
                    min_size=16,
                    max_width=width - 240,
                    max_height=42,
                )
                for line in item_body_lines[:2]:
                    draw.text((text_x, ty), line, fill=(148, 163, 184), font=item_body_font)
                    ty += draw.textbbox((0, 0), "Ag", font=item_body_font)[3] + 4
            row_top += 170
    else:
        body = str(slide.get("body") or "").strip()
        body_font, body_lines = _fit_text(
            draw,
            body,
            font_name="arial.ttf",
            max_size=34,
            min_size=20,
            max_width=width - 168,
            max_height=height - y - 140,
        )
        body_line_height = draw.textbbox((0, 0), "Ag", font=body_font)[3] + 10
        for line in body_lines[:12]:
            draw.text((84, y), line, fill=(148, 163, 184), font=body_font)
            y += body_line_height

    _draw_footer(draw, width=width, height=height, footer_left=str(slide.get("eyebrow") or "Key insight"), index=index, total=0, font=_load_font("arialbd.ttf", 22) or bold_font, color=(241, 245, 249))


def _render_quote_slide(draw, *, width: int, height: int, slide: dict[str, object], index: int):
    regular_font, bold_font = _font_bundle()
    draw.rectangle((0, 0, width, height), fill=(17, 24, 39))
    meta_font = _load_font("arialbd.ttf", 20) or bold_font
    draw.text((84, 84), str(slide.get("eyebrow") or "Insight").upper(), fill=(156, 163, 175), font=meta_font)
    quote_mark_font = _load_font("georgiab.ttf", 180) or _load_font("arialbd.ttf", 180) or bold_font
    draw.text((72, 130), '"', fill=(249, 115, 22), font=quote_mark_font)

    quote_text = str(slide.get("quote") or slide.get("title") or "Key insight goes here.")
    quote_font, quote_lines = _fit_text(
        draw,
        quote_text,
        font_name="arialbd.ttf",
        max_size=60,
        min_size=28,
        max_width=width - 168,
        max_height=420,
    )
    y = 270
    line_height = draw.textbbox((0, 0), "Ag", font=quote_font)[3] + 12
    for line in quote_lines[:7]:
        draw.text((84, y), line, fill=(248, 250, 252), font=quote_font)
        y += line_height

    body = str(slide.get("body") or "").strip()
    if body:
        body_font, body_lines = _fit_text(
            draw,
            body,
            font_name="arial.ttf",
            max_size=24,
            min_size=18,
            max_width=width - 168,
            max_height=140,
        )
        y += 18
        for line in body_lines[:4]:
            draw.text((84, y), line, fill=(209, 213, 219), font=body_font)
            y += draw.textbbox((0, 0), "Ag", font=body_font)[3] + 6

    _draw_footer(draw, width=width, height=height, footer_left="Save this", index=index, total=0, font=meta_font, color=(156, 163, 175))


def _render_breakdown_slide(draw, *, width: int, height: int, slide: dict[str, object], index: int):
    regular_font, bold_font = _font_bundle()
    draw.rectangle((0, 0, width, height), fill=(241, 245, 249))
    meta_font = _load_font("arialbd.ttf", 20) or bold_font
    draw.text((84, 84), str(slide.get("eyebrow") or "Breakdown").upper(), fill=(71, 85, 105), font=meta_font)
    title = str(slide.get("title") or "").strip()
    y = 126
    if title:
        title_font, title_lines = _fit_text(
            draw,
            title,
            font_name="arialbd.ttf",
            max_size=44,
            min_size=26,
            max_width=width - 168,
            max_height=130,
        )
        for line in title_lines[:3]:
            draw.text((84, y), line, fill=(15, 23, 42), font=title_font)
            y += draw.textbbox((0, 0), "Ag", font=title_font)[3] + 8
        y += 16

    cells = slide.get("items") if isinstance(slide.get("items"), list) and slide.get("items") else []
    if not cells and slide.get("body"):
        cells = [{"label": "Key point", "value": slide.get("body")}]

    grid_top = y
    card_w = (width - 84 * 2 - 20) // 2
    card_h = 210
    for cell_index, cell in enumerate(cells[:4]):
        row = cell_index // 2
        col = cell_index % 2
        left = 84 + col * (card_w + 20)
        top = grid_top + row * (card_h + 20)
        right = left + card_w
        bottom = top + card_h
        draw.rounded_rectangle((left, top, right, bottom), radius=28, fill=(255, 255, 255), outline=(226, 232, 240))

        if isinstance(cell, dict):
            label = _normalize_text(cell.get("label") or cell.get("kicker")) or f"Point {cell_index + 1}"
            value = _normalize_text(cell.get("value") or cell.get("title") or cell.get("text"))
        else:
            label = f"Point {cell_index + 1}"
            value = _normalize_text(cell)

        draw.text((left + 24, top + 24), label.upper(), fill=(249, 115, 22), font=meta_font)
        value_font, value_lines = _fit_text(
            draw,
            value,
            font_name="arialbd.ttf",
            max_size=28,
            min_size=18,
            max_width=card_w - 48,
            max_height=120,
        )
        vy = top + 66
        for line in value_lines[:5]:
            draw.text((left + 24, vy), line, fill=(15, 23, 42), font=value_font)
            vy += draw.textbbox((0, 0), "Ag", font=value_font)[3] + 6

    _draw_footer(draw, width=width, height=height, footer_left="Framework", index=index, total=0, font=meta_font, color=(71, 85, 105))


def _render_carousel_slide(slide_payload: object, *, index: int, total: int, title: str) -> Path:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise HTTPException(
            status_code=500,
            detail="Pillow is required to render Instagram carousel slides.",
        ) from exc

    canvas_width = 1080
    canvas_height = 1350
    slide = _normalize_carousel_slide(slide_payload, index=index, total=total)
    slide_type = str(slide.get("type") or "content").lower()
    image = Image.new("RGB", (canvas_width, canvas_height), (15, 23, 42))
    draw = ImageDraw.Draw(image)

    if slide_type in {"hook"}:
        _render_hook_or_cta_slide(draw, width=canvas_width, height=canvas_height, slide=slide, index=index, footer="Swipe to read", is_cta=False)
    elif slide_type in {"quote", "insight"}:
        _render_quote_slide(draw, width=canvas_width, height=canvas_height, slide=slide, index=index)
    elif slide_type in {"breakdown", "framework"}:
        _render_breakdown_slide(draw, width=canvas_width, height=canvas_height, slide=slide, index=index)
    elif slide_type in {"cta", "outro"}:
        _render_hook_or_cta_slide(draw, width=canvas_width, height=canvas_height, slide=slide, index=index, footer="Save this", is_cta=True)
    else:
        _render_content_slide(draw, width=canvas_width, height=canvas_height, slide=slide, index=index)

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


def _is_loopback_host(hostname: str) -> bool:
    normalized = hostname.strip().lower().strip("[]")
    return normalized in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _validate_instagram_media_url(url: str, *, media_label: str) -> str:
    candidate = str(url or "").strip()
    if not candidate:
        raise HTTPException(status_code=400, detail=f"This Instagram {media_label} is missing a media URL.")

    parsed = urlparse(candidate)
    if parsed.scheme.lower() != "https":
        raise HTTPException(
            status_code=400,
            detail=(
                f"This Instagram {media_label} must use a public HTTPS URL. "
                "Local files and plain HTTP URLs cannot be fetched by Instagram."
            ),
        )

    hostname = parsed.hostname or ""
    if not hostname or _is_loopback_host(hostname) or hostname.endswith(".local"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"This Instagram {media_label} must use a publicly reachable host. "
                "Localhost and private local domains will not work."
            ),
        )

    return candidate


def _build_instagram_slide_storage_key(file_path: Path) -> str:
    date_prefix = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    return f"instagram/carousels/{date_prefix}/{file_path.name}"


def _resolve_instagram_carousel_slide_url(file_path: Path) -> str:
    try:
        upload = _upload_generated_clip(
            file_path,
            _build_instagram_slide_storage_key(file_path),
            "image/png",
        )
        return _validate_instagram_media_url(upload.url, media_label="carousel slide")
    except Exception as error:
        fallback_url = _public_media_url(file_path)
        parsed_fallback = urlparse(fallback_url)
        if parsed_fallback.scheme.lower() == "https" and parsed_fallback.hostname and not _is_loopback_host(parsed_fallback.hostname):
            logger.warning(
                "Instagram carousel slide upload failed; falling back to PUBLIC_BASE_URL media URL. file_path=%s error=%s",
                file_path,
                error,
            )
            return fallback_url
        raise HTTPException(
            status_code=400,
            detail=(
                "Instagram carousel publishing needs public HTTPS image URLs for each rendered slide. "
                "Configure working S3/Supabase uploads or set PUBLIC_BASE_URL to a public HTTPS backend host."
            ),
        ) from error


def _is_retryable_instagram_publish_error(error: httpx.HTTPStatusError) -> bool:
    response = error.response
    if response.status_code not in {400, 409}:
        return False
    message = response.text.lower()
    return any(snippet in message for snippet in ("not ready", "not finished", "please wait", "processing"))


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


async def _publish_instagram_container_with_retry(
    *,
    access_token: str,
    instagram_user_id: str,
    creation_id: str,
) -> dict:
    for attempt in range(1, _INSTAGRAM_PUBLISH_RETRY_ATTEMPTS + 1):
        try:
            return await _publish_instagram_container(
                access_token=access_token,
                instagram_user_id=instagram_user_id,
                creation_id=creation_id,
            )
        except httpx.HTTPStatusError as error:
            if attempt >= _INSTAGRAM_PUBLISH_RETRY_ATTEMPTS or not _is_retryable_instagram_publish_error(error):
                raise
            await asyncio.sleep(_INSTAGRAM_PUBLISH_RETRY_DELAY_SECONDS)


async def _publish_instagram_reel(access_token: str, instagram_user_id: str, asset: dict) -> dict:
    media = asset.get("media") if isinstance(asset.get("media"), dict) else {}
    video_url = str(media.get("videoUrl") or media.get("video_url") or "").strip()
    if not video_url:
        return {
            "ok": False,
            "error": "instagram_reel_missing_video",
            "message": "This Instagram reel does not include a video URL to publish.",
        }

    publishable_video_url = _validate_instagram_media_url(video_url, media_label="reel video")
    caption = _build_caption_from_asset(asset)
    creation_id = await _create_instagram_media_container(
        access_token=access_token,
        instagram_user_id=instagram_user_id,
        payload={
            "media_type": "REELS",
            "video_url": publishable_video_url,
            "caption": caption,
            "share_to_feed": "true",
        },
    )
    publish_result = await _publish_instagram_container_with_retry(
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
    slides = _extract_carousel_slide_payloads(asset)
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
                "image_url": _resolve_instagram_carousel_slide_url(file_path),
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
    publish_result = await _publish_instagram_container_with_retry(
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
