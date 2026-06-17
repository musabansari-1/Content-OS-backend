from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import time
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx

from app.integrations_repository import SocialIntegrationRecord, SocialIntegrationRepository


GHOST_PLATFORM = "ghost"
GHOST_ACCEPT_VERSION = "v5.0"
_GHOST_REQUEST_TIMEOUT = 30.0

social_integration_repository = SocialIntegrationRepository()


async def connect_ghost_site_for_user(
    *,
    user_id: int,
    admin_api_url: str,
    admin_api_key: str,
    default_newsletter_slug: str | None = None,
) -> dict[str, Any]:
    normalized_admin_api_url = _normalize_admin_api_url(admin_api_url)
    normalized_admin_api_key = _normalize_admin_api_key(admin_api_key)
    requested_newsletter_slug = (default_newsletter_slug or "").strip()

    newsletters = await _fetch_ghost_newsletters(
        normalized_admin_api_url,
        normalized_admin_api_key,
    )
    active_newsletters = [item for item in newsletters if item.get("status") == "active"]
    resolved_newsletter_slug = _resolve_newsletter_slug_from_list(
        newsletters=active_newsletters,
        requested_newsletter_slug=requested_newsletter_slug,
    )

    site_title = _ghost_host_label(normalized_admin_api_url)
    try:
        site = await _fetch_ghost_site(normalized_admin_api_url, normalized_admin_api_key)
        site_title = str(site.get("title") or "").strip() or site_title
    except httpx.HTTPError:
        pass

    social_integration_repository.upsert_connection(
        user_id=user_id,
        platform=GHOST_PLATFORM,
        platform_user_id=normalized_admin_api_url,
        platform_username=site_title,
        access_token=normalized_admin_api_key,
        refresh_token=resolved_newsletter_slug or None,
        scope=None,
        token_type="ghost_admin_api_key",
        expires_in=None,
    )

    return {
        "ok": True,
        "platform": GHOST_PLATFORM,
        "site_title": site_title,
        "admin_api_url": normalized_admin_api_url,
        "default_newsletter_slug": resolved_newsletter_slug,
        "newsletters": active_newsletters,
    }


async def list_ghost_newsletters_for_user(*, user_id: int) -> dict[str, Any]:
    connection = social_integration_repository.get_by_user_and_platform(
        user_id=user_id,
        platform=GHOST_PLATFORM,
    )
    if connection is None:
        return {
            "ok": False,
            "error": "ghost_not_connected",
            "message": "Connect Ghost before loading newsletters.",
        }

    newsletters = await _fetch_ghost_newsletters(
        connection.platform_user_id,
        connection.access_token,
    )
    active_newsletters = [item for item in newsletters if item.get("status") == "active"]
    return {
        "ok": True,
        "platform": GHOST_PLATFORM,
        "site_title": connection.platform_username or _ghost_host_label(connection.platform_user_id),
        "default_newsletter_slug": _default_newsletter_slug(connection),
        "newsletters": active_newsletters,
    }


async def publish_ghost_asset_for_user(
    *,
    user_id: int,
    asset: dict[str, Any],
    newsletter_slug: str | None = None,
) -> dict[str, Any]:
    connection = social_integration_repository.get_by_user_and_platform(
        user_id=user_id,
        platform=GHOST_PLATFORM,
    )
    if connection is None:
        return {
            "ok": False,
            "error": "ghost_not_connected",
            "message": "Connect Ghost before publishing.",
        }

    if not connection.access_token or not connection.platform_user_id:
        return {
            "ok": False,
            "error": "ghost_connection_incomplete",
            "message": "Your Ghost connection is missing the API URL or admin key.",
        }

    asset_type = _asset_type(asset)
    if asset_type == "blog_post":
        return await _publish_ghost_blog_post(connection, asset)
    if asset_type == "newsletter":
        return await _publish_ghost_newsletter(
            connection,
            asset,
            newsletter_slug=newsletter_slug,
        )

    return {
        "ok": False,
        "error": "ghost_unsupported_asset",
        "message": "Only blog posts and newsletters can be published directly to Ghost.",
    }


async def _publish_ghost_blog_post(
    connection: SocialIntegrationRecord,
    asset: dict[str, Any],
) -> dict[str, Any]:
    title, custom_excerpt, post_html = _build_blog_post_content(asset)
    if not title or not post_html:
        return {
            "ok": False,
            "error": "ghost_invalid_asset",
            "message": "This blog post asset is missing the title or article content required by Ghost.",
        }

    payload = {
        "posts": [
            {
                "title": title,
                "custom_excerpt": custom_excerpt or None,
                "html": post_html,
                "status": "published",
            }
        ]
    }

    try:
        created = await _ghost_request(
            connection.platform_user_id,
            connection.access_token,
            "POST",
            "posts/?source=html",
            json_body=payload,
        )
        post = _first_resource(created, "posts")
        return _ghost_publish_result(
            post=post,
            asset_type="blog_post",
            newsletter_slug=None,
            email_only=False,
        )
    except httpx.HTTPStatusError as error:
        return _ghost_http_error_result(error, default_message="Ghost rejected the blog post publish request.")
    except Exception:
        return {
            "ok": False,
            "error": "ghost_publish_failed",
            "message": "Ghost publish failed unexpectedly.",
        }


async def _publish_ghost_newsletter(
    connection: SocialIntegrationRecord,
    asset: dict[str, Any],
    *,
    newsletter_slug: str | None,
) -> dict[str, Any]:
    subject_line, preview_text, body_html = _build_newsletter_content(asset)
    if not subject_line or not body_html:
        return {
            "ok": False,
            "error": "ghost_invalid_asset",
            "message": "This newsletter asset is missing the subject line or body required by Ghost.",
        }

    try:
        newsletters = await _fetch_ghost_newsletters(
            connection.platform_user_id,
            connection.access_token,
        )
        active_newsletters = [item for item in newsletters if item.get("status") == "active"]
        resolved_newsletter_slug = _resolve_newsletter_slug_for_publish(
            connection=connection,
            newsletters=active_newsletters,
            requested_newsletter_slug=newsletter_slug,
        )
    except ValueError as error:
        return {
            "ok": False,
            "error": "ghost_newsletter_missing",
            "message": str(error),
        }

    create_payload = {
        "posts": [
            {
                "title": subject_line,
                "custom_excerpt": preview_text or None,
                "html": body_html,
                "status": "draft",
            }
        ]
    }

    try:
        created = await _ghost_request(
            connection.platform_user_id,
            connection.access_token,
            "POST",
            "posts/?source=html",
            json_body=create_payload,
        )
        created_post = _first_resource(created, "posts")
        post_id = str(created_post.get("id") or "").strip()
        updated_at = created_post.get("updated_at")
        if not post_id or not updated_at:
            return {
                "ok": False,
                "error": "ghost_publish_failed",
                "message": "Ghost created the draft newsletter post but did not return the required publish metadata.",
            }

        publish_payload = {
            "posts": [
                {
                    "updated_at": updated_at,
                    "status": "published",
                    "email_only": True,
                }
            ]
        }
        published = await _ghost_request(
            connection.platform_user_id,
            connection.access_token,
            "PUT",
            f"posts/{quote(post_id)}/?newsletter={quote(resolved_newsletter_slug)}",
            json_body=publish_payload,
        )
        post = _first_resource(published, "posts")
        return _ghost_publish_result(
            post=post,
            asset_type="newsletter",
            newsletter_slug=resolved_newsletter_slug,
            email_only=True,
        )
    except httpx.HTTPStatusError as error:
        return _ghost_http_error_result(error, default_message="Ghost rejected the newsletter publish request.")
    except Exception:
        return {
            "ok": False,
            "error": "ghost_publish_failed",
            "message": "Ghost publish failed unexpectedly.",
        }


async def _fetch_ghost_site(admin_api_url: str, admin_api_key: str) -> dict[str, Any]:
    payload = await _ghost_request(
        admin_api_url,
        admin_api_key,
        "GET",
        "site/",
    )
    if not isinstance(payload, dict):
        return {}
    return payload


async def _fetch_ghost_newsletters(admin_api_url: str, admin_api_key: str) -> list[dict[str, Any]]:
    payload = await _ghost_request(
        admin_api_url,
        admin_api_key,
        "GET",
        "newsletters/?limit=50",
    )
    newsletters = payload.get("newsletters") if isinstance(payload, dict) else None
    if not isinstance(newsletters, list):
        return []
    return [item for item in newsletters if isinstance(item, dict)]


async def _ghost_request(
    admin_api_url: str,
    admin_api_key: str,
    method: str,
    resource_path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = _build_ghost_endpoint(admin_api_url, resource_path)
    headers = {
        "Authorization": f"Ghost {_build_ghost_jwt(admin_api_key)}",
        "Accept-Version": GHOST_ACCEPT_VERSION,
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    async with httpx.AsyncClient(timeout=_GHOST_REQUEST_TIMEOUT) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            json=json_body,
        )
        response.raise_for_status()
        return response.json()


def _build_ghost_endpoint(admin_api_url: str, resource_path: str) -> str:
    return f"{admin_api_url.rstrip('/')}/{resource_path.lstrip('/')}"


def _build_ghost_jwt(admin_api_key: str) -> str:
    key_id, key_secret = _split_ghost_admin_key(admin_api_key)
    now = int(time.time())
    header = {"alg": "HS256", "kid": key_id, "typ": "JWT"}
    payload = {"iat": now, "exp": now + 300, "aud": "/admin/"}
    encoded_header = _jwt_b64(header)
    encoded_payload = _jwt_b64(payload)
    signing_input = f"{encoded_header}.{encoded_payload}"
    signature = hmac.new(
        bytes.fromhex(key_secret),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    return f"{signing_input}.{encoded_signature}"


def _jwt_b64(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _split_ghost_admin_key(admin_api_key: str) -> tuple[str, str]:
    parts = admin_api_key.split(":", 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError("Ghost admin API keys must be in the form <id>:<secret>.")
    key_id = parts[0].strip()
    key_secret = parts[1].strip()
    bytes.fromhex(key_secret)
    return key_id, key_secret


def _normalize_admin_api_key(admin_api_key: str) -> str:
    normalized = (admin_api_key or "").strip()
    if not normalized:
        raise ValueError("Ghost admin_api_key is required.")
    _split_ghost_admin_key(normalized)
    return normalized


def _normalize_admin_api_url(admin_api_url: str) -> str:
    raw_value = (admin_api_url or "").strip()
    if not raw_value:
        raise ValueError("Ghost admin_api_url is required.")

    parsed = urlparse(raw_value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Ghost admin_api_url must be a valid absolute URL.")

    path = parsed.path.rstrip("/")
    if path.endswith("/ghost/api/admin"):
        normalized_path = path
    elif path.endswith("/ghost"):
        normalized_path = f"{path}/api/admin"
    else:
        normalized_path = f"{path}/ghost/api/admin" if path else "/ghost/api/admin"

    return urlunparse((parsed.scheme, parsed.netloc, normalized_path.rstrip("/") + "/", "", "", ""))


def _ghost_host_label(admin_api_url: str) -> str:
    parsed = urlparse(admin_api_url)
    return parsed.netloc or "Ghost site"


def _asset_type(asset: dict[str, Any]) -> str:
    return str(asset.get("assetType") or asset.get("asset_type") or "").strip().lower()


def _block_map(asset: dict[str, Any]) -> dict[str, Any]:
    block_map: dict[str, Any] = {}
    blocks = asset.get("blocks")
    if not isinstance(blocks, list):
        return block_map

    for block in blocks:
        if not isinstance(block, dict):
            continue
        key = str(block.get("key") or "").strip().lower()
        if not key:
            continue
        block_map[key] = block.get("value")
    return block_map


def _build_blog_post_content(asset: dict[str, Any]) -> tuple[str, str, str]:
    block_map = _block_map(asset)
    title = _string_value(block_map.get("title"))
    subtitle = _string_value(block_map.get("subtitle"))
    cta = _string_value(block_map.get("cta"))
    section_items = _list_value(block_map.get("sections"))

    html_parts: list[str] = []
    if subtitle:
        html_parts.append(f"<p><em>{_escape_inline(subtitle)}</em></p>")

    for item in section_items:
        heading, body = _split_heading_and_body(item)
        if heading:
            html_parts.append(f"<h2>{_escape_inline(heading)}</h2>")
        for paragraph in _paragraphs_from_text(body):
            html_parts.append(f"<p>{_escape_inline(paragraph)}</p>")

    if cta:
        html_parts.append(f"<p><strong>{_escape_inline(cta)}</strong></p>")

    return title, subtitle, "\n".join(html_parts).strip()


def _build_newsletter_content(asset: dict[str, Any]) -> tuple[str, str, str]:
    block_map = _block_map(asset)
    subject_line = _string_value(block_map.get("subject_line"))
    preview_text = _string_value(block_map.get("preview_text"))
    body = _string_value(block_map.get("body"))
    cta = _string_value(block_map.get("cta"))

    paragraphs = _paragraphs_from_text(body)
    html_parts = [f"<p>{_escape_inline(paragraph)}</p>" for paragraph in paragraphs]
    if cta:
        html_parts.append(f"<p><strong>{_escape_inline(cta)}</strong></p>")

    return subject_line, preview_text, "\n".join(html_parts).strip()


def _string_value(value: Any) -> str:
    if isinstance(value, list):
        return "\n\n".join(_string_value(item) for item in value if _string_value(item)).strip()
    if value is None:
        return ""
    return str(value).strip()


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _string_value(value)
    return [text] if text else []


def _split_heading_and_body(value: str) -> tuple[str, str]:
    normalized = str(value or "").strip()
    if not normalized:
        return "", ""
    if "\n" not in normalized:
        return "", normalized

    first_line, remainder = normalized.split("\n", 1)
    heading = first_line.strip()
    body = remainder.strip()
    if not body:
        return "", normalized
    return heading, body


def _paragraphs_from_text(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split("\n\n") if part.strip()]


def _escape_inline(value: str) -> str:
    return html.escape(value, quote=False).replace("\n", "<br>")


def _default_newsletter_slug(connection: SocialIntegrationRecord) -> str:
    return str(connection.refresh_token or "").strip()


def _resolve_newsletter_slug_from_list(
    *,
    newsletters: list[dict[str, Any]],
    requested_newsletter_slug: str,
) -> str:
    normalized_requested_slug = requested_newsletter_slug.strip()
    if normalized_requested_slug:
        for item in newsletters:
            if str(item.get("slug") or "").strip() == normalized_requested_slug:
                return normalized_requested_slug
        raise ValueError(f"Ghost newsletter '{normalized_requested_slug}' was not found among active newsletters.")

    if len(newsletters) == 1:
        return str(newsletters[0].get("slug") or "").strip()
    return ""


def _resolve_newsletter_slug_for_publish(
    *,
    connection: SocialIntegrationRecord,
    newsletters: list[dict[str, Any]],
    requested_newsletter_slug: str | None,
) -> str:
    normalized_requested_slug = (requested_newsletter_slug or "").strip()
    if normalized_requested_slug:
        return _resolve_newsletter_slug_from_list(
            newsletters=newsletters,
            requested_newsletter_slug=normalized_requested_slug,
        )

    connection_default = _default_newsletter_slug(connection)
    if connection_default:
        return _resolve_newsletter_slug_from_list(
            newsletters=newsletters,
            requested_newsletter_slug=connection_default,
        )

    if len(newsletters) == 1:
        return str(newsletters[0].get("slug") or "").strip()

    raise ValueError(
        "This Ghost site has multiple active newsletters. Provide a newsletter_slug or save a default one when connecting Ghost."
    )


def _first_resource(payload: dict[str, Any], resource_name: str) -> dict[str, Any]:
    resources = payload.get(resource_name)
    if isinstance(resources, list) and resources and isinstance(resources[0], dict):
        return resources[0]
    return {}


def _ghost_publish_result(
    *,
    post: dict[str, Any],
    asset_type: str,
    newsletter_slug: str | None,
    email_only: bool,
) -> dict[str, Any]:
    email_resource = post.get("email") if isinstance(post.get("email"), dict) else {}
    return {
        "ok": True,
        "platform": GHOST_PLATFORM,
        "asset_type": asset_type,
        "ghost_post_id": post.get("id"),
        "ghost_post_uuid": post.get("uuid"),
        "ghost_post_url": post.get("url"),
        "ghost_post_status": post.get("status"),
        "newsletter_slug": newsletter_slug,
        "email_only": email_only,
        "ghost_email_status": email_resource.get("status"),
    }


def _ghost_http_error_result(error: httpx.HTTPStatusError, *, default_message: str) -> dict[str, Any]:
    response = error.response
    message = default_message
    try:
        payload = response.json()
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors:
            candidate = errors[0]
            if isinstance(candidate, dict):
                candidate_message = str(candidate.get("message") or "").strip()
                if candidate_message:
                    message = candidate_message
    except Exception:
        pass

    return {
        "ok": False,
        "error": "ghost_publish_failed",
        "message": message,
        "status_code": response.status_code,
        "response_text": response.text,
    }
