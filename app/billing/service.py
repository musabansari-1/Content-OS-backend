from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.billing.domain import BillingSummary, BillingSubscription, BillingUsageCounter
from app.billing.plans import DEFAULT_PLAN_CODE, PLAN_DEFINITIONS, get_plan_definition
from app.billing.repository import BillingRepository
from app.core.config import env, require_env


billing_repository = BillingRepository()
FRONTEND_BASE_URL = (env("FRONTEND_BASE_URL", "http://localhost:3000") or "http://localhost:3000").rstrip("/")
PADDLE_ENVIRONMENT = (env("PADDLE_ENVIRONMENT", "sandbox") or "sandbox").strip().lower()
PADDLE_CLIENT_TOKEN = env("PADDLE_CLIENT_TOKEN", "") or ""
PADDLE_WEBHOOK_SECRET = env("PADDLE_WEBHOOK_SECRET", "") or ""
PADDLE_PRICE_ID_PRO = env("PADDLE_PRICE_ID_PRO", "") or ""
PADDLE_PRICE_ID_MAX = env("PADDLE_PRICE_ID_MAX", "") or ""

PLAN_PRICE_IDS = {
    "pro": PADDLE_PRICE_ID_PRO,
    "max": PADDLE_PRICE_ID_MAX,
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
                "checkout_enabled": bool(PLAN_PRICE_IDS.get(plan.code)),
                "price_id": PLAN_PRICE_IDS.get(plan.code) or None,
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


def get_checkout_settings(user_id: int, user_email: str, plan_code: str) -> dict:
    normalized_plan = (plan_code or "").strip().lower()
    if normalized_plan not in ("pro", "max"):
        raise HTTPException(status_code=400, detail="Only paid plans can be purchased through checkout.")

    price_id = PLAN_PRICE_IDS.get(normalized_plan, "")
    if not price_id:
        raise HTTPException(
            status_code=500,
            detail=f"Paddle price ID is not configured for the {normalized_plan} plan.",
        )

    if not PADDLE_CLIENT_TOKEN:
        raise HTTPException(status_code=500, detail="PADDLE_CLIENT_TOKEN is not configured.")

    return {
        "plan_code": normalized_plan,
        "price_id": price_id,
        "paddle_environment": PADDLE_ENVIRONMENT,
        "paddle_client_token": PADDLE_CLIENT_TOKEN,
        "customer_email": user_email,
        "success_url": f"{FRONTEND_BASE_URL}/billing?checkout=success",
        "custom_data": {
            "user_id": user_id,
            "plan_code": normalized_plan,
        },
    }


def verify_paddle_webhook_signature(raw_body: bytes, signature_header: str | None) -> None:
    if not PADDLE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="PADDLE_WEBHOOK_SECRET is not configured.")

    if not signature_header:
        raise HTTPException(status_code=400, detail="Missing Paddle-Signature header.")

    parsed_header = _parse_paddle_signature_header(signature_header)
    timestamp = parsed_header.get("ts", "")
    expected_signature = parsed_header.get("h1", "")

    if not timestamp or not expected_signature:
        raise HTTPException(status_code=400, detail="Malformed Paddle-Signature header.")

    signed_payload = timestamp.encode("utf-8") + b":" + raw_body
    computed_signature = hmac.new(
        PADDLE_WEBHOOK_SECRET.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_signature, expected_signature):
        raise HTTPException(status_code=400, detail="Invalid Paddle webhook signature.")


def process_paddle_webhook(raw_body: bytes) -> dict:
    payload = json.loads(raw_body.decode("utf-8"))
    event_id = str(
        payload.get("event_id")
        or payload.get("notification_id")
        or payload.get("id")
        or ""
    ).strip()
    event_type = str(payload.get("event_type") or payload.get("type") or "").strip()

    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Webhook payload is missing event metadata.")

    is_new_event = billing_repository.record_webhook_event(
        provider="paddle",
        event_id=event_id,
        event_type=event_type,
        payload=raw_body.decode("utf-8"),
        processing_status="received",
    )
    if not is_new_event:
        return {"ok": True, "duplicate": True, "event_id": event_id, "event_type": event_type}

    try:
        data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
        if event_type.startswith("subscription."):
            _sync_subscription_from_paddle_event(event_type, data)

        billing_repository.update_webhook_event_status(
            provider="paddle",
            event_id=event_id,
            processing_status="processed",
            processed_at=datetime.now(timezone.utc),
        )
        return {"ok": True, "duplicate": False, "event_id": event_id, "event_type": event_type}
    except Exception:
        billing_repository.update_webhook_event_status(
            provider="paddle",
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


def _parse_paddle_signature_header(signature_header: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_part in signature_header.split(";"):
        part = raw_part.strip()
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _sync_subscription_from_paddle_event(event_type: str, data: dict) -> None:
    provider_subscription_id = str(data.get("id") or "").strip()
    custom_data = data.get("custom_data", {}) if isinstance(data.get("custom_data"), dict) else {}
    user_id = _extract_user_id_from_paddle_payload(custom_data, provider_subscription_id)
    if user_id is None:
        return

    plan_code = _resolve_plan_code_from_paddle_payload(custom_data, data)
    subscription_status = str(data.get("status") or "active").strip() or "active"
    current_period = data.get("current_billing_period", {}) if isinstance(data.get("current_billing_period"), dict) else {}
    current_period_start = _parse_paddle_datetime(current_period.get("starts_at")) or _parse_paddle_datetime(
        data.get("started_at")
    ) or datetime.now(timezone.utc)
    current_period_end = _parse_paddle_datetime(current_period.get("ends_at")) or _get_current_period_bounds()[1]
    cancel_at_period_end = bool(data.get("scheduled_change"))
    if event_type == "subscription.canceled":
        cancel_at_period_end = True

    billing_repository.upsert_subscription(
        user_id=user_id,
        plan_code=plan_code,
        provider="paddle",
        provider_customer_id=str(data.get("customer_id") or "") or None,
        provider_subscription_id=provider_subscription_id or None,
        subscription_status=subscription_status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        cancel_at_period_end=cancel_at_period_end,
    )


def _extract_user_id_from_paddle_payload(
    custom_data: dict,
    provider_subscription_id: str,
) -> int | None:
    custom_user_id = custom_data.get("user_id")
    if custom_user_id is not None:
        try:
            return int(custom_user_id)
        except (TypeError, ValueError):
            return None

    if provider_subscription_id:
        existing_subscription = billing_repository.get_subscription_by_provider_subscription_id(
            provider_subscription_id
        )
        if existing_subscription is not None:
            return existing_subscription.user_id

    return None


def _resolve_plan_code_from_paddle_payload(custom_data: dict, data: dict) -> str:
    custom_plan_code = str(custom_data.get("plan_code") or "").strip().lower()
    if custom_plan_code in ("free", "pro", "max"):
        return custom_plan_code

    items = data.get("items", [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            price = item.get("price", {}) if isinstance(item.get("price"), dict) else {}
            price_id = str(price.get("id") or item.get("price_id") or "").strip()
            for plan_code, configured_price_id in PLAN_PRICE_IDS.items():
                if configured_price_id and price_id == configured_price_id:
                    return plan_code

    return DEFAULT_PLAN_CODE


def _parse_paddle_datetime(value: str | None) -> datetime | None:
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
