from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

from fastapi import HTTPException

from app.billing.domain import BillingSummary, BillingSubscription, BillingUsageCounter
from app.billing.plans import DEFAULT_PLAN_CODE, PLAN_DEFINITIONS, get_plan_definition
from app.billing.repository import BillingRepository
from app.core.config import env


billing_repository = BillingRepository()


def _resolve_frontend_base_url() -> str:
    configured_url = (
        env("FRONTEND_BASE_URL", None)
        or env("FRONTEND_URL", None)
        or "http://localhost:5173"
    )
    return configured_url.rstrip("/")


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


FRONTEND_BASE_URL = _resolve_frontend_base_url()
CREEM_API_KEY = env("CREEM_API_KEY", "") or ""
CREEM_TEST_MODE = _is_truthy(env("CREEM_TEST_MODE", "")) or CREEM_API_KEY.startswith("creem_test_")
CREEM_API_BASE_URL = (
    env(
        "CREEM_API_BASE_URL",
        "https://test-api.creem.io" if CREEM_TEST_MODE else "https://api.creem.io",
    )
    or ("https://test-api.creem.io" if CREEM_TEST_MODE else "https://api.creem.io")
).rstrip("/")
CREEM_CHECKOUT_PATH = (env("CREEM_CHECKOUT_PATH", "/v1/checkouts") or "/v1/checkouts").strip() or "/v1/checkouts"
CREEM_WEBHOOK_SECRET = env("CREEM_WEBHOOK_SECRET", "") or ""
CREEM_PRODUCT_ID_PRO = env("CREEM_PRODUCT_ID_PRO", "") or ""
CREEM_PRODUCT_ID_MAX = env("CREEM_PRODUCT_ID_MAX", "") or ""
CREEM_LOCAL_TEST_MODE = (not CREEM_API_KEY) and (
    FRONTEND_BASE_URL.startswith("http://localhost")
    or FRONTEND_BASE_URL.startswith("http://127.0.0.1")
)

PLAN_PRODUCT_IDS = {
    "pro": CREEM_PRODUCT_ID_PRO,
    "max": CREEM_PRODUCT_ID_MAX,
}


def list_billing_plans() -> list[dict]:
    plans = []
    for plan_code in ("free", "pro", "max"):
        plan = PLAN_DEFINITIONS[plan_code]
        plans.append(
            {
                "code": plan.code,
                "label": plan.label,
                "assets_per_month": plan.assets_per_month,
                "direct_publishes_per_month": plan.direct_publishes_per_month,
                "checkout_enabled": (
                    plan.code in ("pro", "max")
                    and (
                        (bool(CREEM_API_KEY) and bool(PLAN_PRODUCT_IDS.get(plan.code)))
                        or CREEM_LOCAL_TEST_MODE
                    )
                ),
                "checkout_url": None,
            }
        )
    return plans


def get_billing_summary(user_id: int) -> BillingSummary:
    subscription = _get_or_create_subscription(user_id)
    usage = _get_or_create_usage_counter(subscription)
    plan = get_plan_definition(subscription.plan_code)
    return BillingSummary(subscription=subscription, usage=usage, plan=plan)


def ensure_can_generate_assets(user_id: int, requested_assets: int) -> BillingSummary:
    summary = get_billing_summary(user_id)
    remaining_assets = summary.plan.assets_per_month - summary.usage.assets_generated
    if requested_assets > remaining_assets:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Your {summary.plan.label} plan has {remaining_assets} asset generations left "
                f"for this billing period."
            ),
        )
    return summary


def record_generated_assets(user_id: int, generated_assets: int) -> BillingUsageCounter:
    summary = get_billing_summary(user_id)
    return billing_repository.increment_usage(
        user_id=user_id,
        period_start=summary.subscription.current_period_start,
        period_end=summary.subscription.current_period_end,
        assets_generated=max(0, generated_assets),
    )


def ensure_can_direct_publish(user_id: int, requested_publishes: int = 1) -> BillingSummary:
    summary = get_billing_summary(user_id)
    remaining_publishes = summary.plan.direct_publishes_per_month - summary.usage.direct_publishes
    if requested_publishes > remaining_publishes:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Your {summary.plan.label} plan has {remaining_publishes} direct publishes left "
                f"for this billing period."
            ),
        )
    return summary


def record_direct_publish(user_id: int, publish_count: int = 1) -> BillingUsageCounter:
    summary = get_billing_summary(user_id)
    return billing_repository.increment_usage(
        user_id=user_id,
        period_start=summary.subscription.current_period_start,
        period_end=summary.subscription.current_period_end,
        direct_publishes=max(0, publish_count),
    )


def schedule_subscription_cancellation(user_id: int) -> BillingSummary:
    subscription = _get_or_create_subscription(user_id)
    normalized_provider = str(subscription.provider or "").strip().lower()

    if subscription.plan_code == DEFAULT_PLAN_CODE or normalized_provider == "internal":
        raise HTTPException(status_code=400, detail="There is no paid subscription to cancel.")

    if subscription.cancel_at_period_end or subscription.subscription_status == "scheduled_cancel":
        return get_billing_summary(user_id)

    if normalized_provider == "creem_test":
        _persist_subscription_state(
            subscription,
            subscription_status="scheduled_cancel",
            cancel_at_period_end=True,
        )
        return get_billing_summary(user_id)

    if normalized_provider != "creem":
        raise HTTPException(status_code=400, detail="Subscription cancellation is only supported for Creem billing.")

    provider_subscription_id = str(subscription.provider_subscription_id or "").strip()
    if not provider_subscription_id:
        raise HTTPException(status_code=400, detail="This subscription is missing a Creem subscription ID.")

    _creem_api_request(
        "POST",
        f"/v1/subscriptions/{provider_subscription_id}/cancel",
        {"mode": "scheduled"},
    )
    _persist_subscription_state(
        subscription,
        subscription_status="scheduled_cancel",
        cancel_at_period_end=True,
    )
    return get_billing_summary(user_id)


def get_checkout_settings(user_id: int, user_email: str, plan_code: str) -> dict:
    normalized_plan = (plan_code or "").strip().lower()
    if normalized_plan not in ("pro", "max"):
        raise HTTPException(status_code=400, detail="Only paid plans can be purchased through checkout.")

    if not CREEM_API_KEY:
        if not CREEM_LOCAL_TEST_MODE:
            raise HTTPException(status_code=500, detail="CREEM_API_KEY is not configured.")
        return _create_local_test_checkout(user_id=user_id, user_email=user_email, plan_code=normalized_plan)

    product_id = PLAN_PRODUCT_IDS.get(normalized_plan, "").strip()
    if not product_id:
        raise HTTPException(
            status_code=500,
            detail=f"CREEM_PRODUCT_ID_{normalized_plan.upper()} is not configured.",
        )

    checkout_session = _create_creem_checkout_session(
        user_id=user_id,
        user_email=user_email,
        plan_code=normalized_plan,
        product_id=product_id,
    )

    return {
        "provider": "creem",
        "plan_code": normalized_plan,
        "checkout_url": checkout_session["checkout_url"],
        "checkout_mode": "redirect",
        "creem_mode": "test" if CREEM_TEST_MODE else "live",
        "provider_checkout_id": checkout_session.get("provider_checkout_id"),
        "customer_email": user_email,
        "success_url": f"{FRONTEND_BASE_URL}/billing?checkout=success",
        "cancel_url": f"{FRONTEND_BASE_URL}/billing?checkout=canceled",
        "metadata": {
            "user_id": user_id,
            "plan_code": normalized_plan,
            "product_id": product_id,
        },
    }


def _create_local_test_checkout(user_id: int, user_email: str, plan_code: str) -> dict:
    period_start, period_end = _get_current_period_bounds()
    billing_repository.upsert_subscription(
        user_id=user_id,
        plan_code=plan_code,
        provider="creem_test",
        subscription_status="active",
        current_period_start=period_start,
        current_period_end=period_end,
        provider_customer_id=f"test-customer-{user_id}",
        provider_subscription_id=f"test-subscription-{user_id}-{plan_code}",
        cancel_at_period_end=False,
    )

    return {
        "provider": "creem_test",
        "plan_code": plan_code,
        "checkout_url": f"{FRONTEND_BASE_URL}/billing?checkout=success&mode=local-test&plan={plan_code}",
        "checkout_mode": "redirect",
        "creem_mode": "local-test",
        "provider_checkout_id": f"local-test-{user_id}-{plan_code}",
        "customer_email": user_email,
        "success_url": f"{FRONTEND_BASE_URL}/billing?checkout=success&mode=local-test&plan={plan_code}",
        "cancel_url": f"{FRONTEND_BASE_URL}/billing?checkout=canceled&mode=local-test&plan={plan_code}",
        "metadata": {
            "user_id": user_id,
            "plan_code": plan_code,
            "mode": "local-test",
        },
    }


def verify_creem_webhook_signature(raw_body: bytes, signature_header: str | None) -> None:
    if not CREEM_WEBHOOK_SECRET:
        return

    if not signature_header:
        raise HTTPException(status_code=400, detail="Missing creem-signature header.")

    computed_signature = hmac.new(
        CREEM_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_signature, signature_header.strip()):
        raise HTTPException(status_code=400, detail="Invalid Creem webhook signature.")


def process_creem_webhook(raw_body: bytes) -> dict:
    payload = json.loads(raw_body.decode("utf-8"))
    event_id = str(payload.get("id") or "").strip()
    event_type = str(payload.get("eventType") or payload.get("event_type") or payload.get("type") or "").strip()

    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Webhook payload is missing event metadata.")

    is_new_event = billing_repository.record_webhook_event(
        provider="creem",
        event_id=event_id,
        event_type=event_type,
        payload=raw_body.decode("utf-8"),
        processing_status="received",
    )
    if not is_new_event:
        return {"ok": True, "duplicate": True, "event_id": event_id, "event_type": event_type}

    try:
        data = payload.get("object", {}) if isinstance(payload.get("object"), dict) else {}
        if _is_creem_subscription_event(event_type):
            _sync_subscription_from_creem_event(event_type, data)

        billing_repository.update_webhook_event_status(
            provider="creem",
            event_id=event_id,
            processing_status="processed",
            processed_at=datetime.now(timezone.utc),
        )
        return {"ok": True, "duplicate": False, "event_id": event_id, "event_type": event_type}
    except Exception:
        billing_repository.update_webhook_event_status(
            provider="creem",
            event_id=event_id,
            processing_status="failed",
            processed_at=datetime.now(timezone.utc),
        )
        raise


def _get_or_create_subscription(user_id: int) -> BillingSubscription:
    subscription = billing_repository.get_subscription_by_user_id(user_id)
    if subscription is None:
        period_start, period_end = _get_current_period_bounds()
        return billing_repository.create_subscription(
            user_id=user_id,
            plan_code=DEFAULT_PLAN_CODE,
            provider="internal",
            subscription_status="active",
            current_period_start=period_start,
            current_period_end=period_end,
        )

    return _refresh_period_if_needed(subscription)


def _refresh_period_if_needed(subscription: BillingSubscription) -> BillingSubscription:
    now = datetime.now(timezone.utc)
    if now < subscription.current_period_end:
        return subscription

    period_start, period_end = _get_current_period_bounds()
    return billing_repository.update_subscription_period(
        subscription_id=subscription.id,
        current_period_start=period_start,
        current_period_end=period_end,
    )


def _get_or_create_usage_counter(subscription: BillingSubscription) -> BillingUsageCounter:
    usage = billing_repository.get_usage_counter(
        user_id=subscription.user_id,
        period_start=subscription.current_period_start,
    )
    if usage is not None:
        return usage

    return billing_repository.create_usage_counter(
        user_id=subscription.user_id,
        period_start=subscription.current_period_start,
        period_end=subscription.current_period_end,
    )


def _get_current_period_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    period_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        period_end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        period_end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return period_start, period_end


def _sync_subscription_from_creem_event(event_type: str, data: dict) -> None:
    subscription_data, metadata = _extract_creem_subscription_payload(data)
    provider_subscription_id = str(subscription_data.get("id") or "").strip()
    user_id = _extract_user_id_from_creem_payload(metadata, provider_subscription_id)
    if user_id is None:
        return

    plan_code = _resolve_plan_code_from_creem_payload(metadata, data, subscription_data)
    subscription_status = str(subscription_data.get("status") or data.get("status") or "active").strip() or "active"
    current_period_start = (
        _parse_provider_datetime(subscription_data.get("current_period_start_date"))
        or _parse_provider_datetime(subscription_data.get("created_at"))
        or _parse_provider_datetime(data.get("created_at"))
        or datetime.now(timezone.utc)
    )
    current_period_end = (
        _parse_provider_datetime(subscription_data.get("current_period_end_date"))
        or _parse_provider_datetime(subscription_data.get("next_transaction_date"))
        or _get_current_period_bounds()[1]
    )
    cancel_at_period_end = event_type == "subscription.scheduled_cancel" or subscription_status == "scheduled_cancel"

    billing_repository.upsert_subscription(
        user_id=user_id,
        plan_code=plan_code,
        provider="creem",
        provider_customer_id=_extract_creem_customer_id(data, subscription_data),
        provider_subscription_id=provider_subscription_id or None,
        subscription_status=subscription_status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        cancel_at_period_end=cancel_at_period_end,
    )


def _extract_creem_subscription_payload(data: dict) -> tuple[dict, dict]:
    if isinstance(data.get("subscription"), dict):
        subscription_data = data["subscription"]
        metadata = subscription_data.get("metadata", {}) if isinstance(subscription_data.get("metadata"), dict) else {}
        if not metadata and isinstance(data.get("metadata"), dict):
            metadata = data["metadata"]
        return subscription_data, metadata

    metadata = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
    return data, metadata


def _extract_user_id_from_creem_payload(metadata: dict, provider_subscription_id: str) -> int | None:
    custom_user_id = metadata.get("user_id") or metadata.get("referenceId")
    if custom_user_id is not None:
        try:
            return int(custom_user_id)
        except (TypeError, ValueError):
            pass

    if provider_subscription_id:
        existing_subscription = billing_repository.get_subscription_by_provider_subscription_id(
            provider_subscription_id
        )
        if existing_subscription is not None:
            return existing_subscription.user_id

    return None


def _resolve_plan_code_from_creem_payload(metadata: dict, data: dict, subscription_data: dict) -> str:
    custom_plan_code = str(metadata.get("plan_code") or "").strip().lower()
    if custom_plan_code in ("free", "pro", "max"):
        return custom_plan_code

    product_id = _extract_creem_product_id(data, subscription_data)
    for plan_code, configured_product_id in PLAN_PRODUCT_IDS.items():
        if configured_product_id and product_id == configured_product_id:
            return plan_code

    product_name = _extract_creem_product_name(data, subscription_data)
    if "max" in product_name:
        return "max"
    if "pro" in product_name:
        return "pro"

    return DEFAULT_PLAN_CODE


def _extract_creem_product_id(data: dict, subscription_data: dict) -> str:
    candidates = [
        subscription_data.get("product"),
        data.get("product_id"),
    ]
    product_data = data.get("product")
    if isinstance(product_data, dict):
        candidates.append(product_data.get("id"))

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _extract_creem_product_name(data: dict, subscription_data: dict) -> str:
    candidates = []
    product_data = data.get("product")
    if isinstance(product_data, dict):
        candidates.append(product_data.get("name"))
    subscription_product = subscription_data.get("product")
    if isinstance(subscription_product, dict):
        candidates.append(subscription_product.get("name"))

    for candidate in candidates:
        value = str(candidate or "").strip().lower()
        if value:
            return value
    return ""


def _extract_creem_customer_id(data: dict, subscription_data: dict) -> str | None:
    customer_data = data.get("customer")
    if isinstance(customer_data, dict):
        customer_id = str(customer_data.get("id") or "").strip()
        if customer_id:
            return customer_id

    customer_id = str(subscription_data.get("customer") or "").strip()
    return customer_id or None


def _parse_provider_datetime(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None

    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _is_valid_checkout_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_creem_subscription_event(event_type: str) -> bool:
    normalized = event_type.strip().lower()
    return normalized == "checkout.completed" or normalized.startswith("subscription.")


def _create_creem_checkout_session(
    *,
    user_id: int,
    user_email: str,
    plan_code: str,
    product_id: str,
) -> dict[str, str]:
    payload = {
        "product_id": product_id,
        "success_url": f"{FRONTEND_BASE_URL}/billing?checkout=success",
        # This metadata field is inferred from Creem webhook payload examples so
        # the subscription can be linked back to the app user during sync.
        "metadata": {
            "user_id": user_id,
            "plan_code": plan_code,
            "email": user_email,
        },
    }
    response = _creem_api_request("POST", CREEM_CHECKOUT_PATH, payload)
    checkout_url = str(response.get("checkout_url") or "").strip()
    if not _is_valid_checkout_url(checkout_url):
        raise HTTPException(status_code=502, detail="Creem did not return a valid checkout URL.")

    provider_checkout_id = str(response.get("id") or "").strip()
    return {
        "checkout_url": checkout_url,
        "provider_checkout_id": provider_checkout_id,
    }


def _creem_api_request(method: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    url = f"{CREEM_API_BASE_URL}{path if path.startswith('/') else f'/{path}'}"
    request = urllib_request.Request(
        url,
        data=body,
        method=method.upper(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ContentOS/1.0 (+https://contentos.ai)",
            "x-api-key": CREEM_API_KEY,
        },
    )

    try:
        with urllib_request.urlopen(request, timeout=20) as response:
            raw_response = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(
            status_code=502,
            detail=f"Creem checkout request failed with HTTP {exc.code}. {raw_error}".strip(),
        ) from exc
    except urllib_error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Creem API: {exc.reason}") from exc

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Creem returned a non-JSON response.") from exc

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Creem returned an unexpected checkout response.")
    return parsed


def _persist_subscription_state(
    subscription: BillingSubscription,
    *,
    subscription_status: str,
    cancel_at_period_end: bool,
) -> BillingSubscription:
    return billing_repository.upsert_subscription(
        user_id=subscription.user_id,
        plan_code=subscription.plan_code,
        provider=subscription.provider,
        provider_customer_id=subscription.provider_customer_id,
        provider_subscription_id=subscription.provider_subscription_id,
        subscription_status=subscription_status,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=cancel_at_period_end,
    )
