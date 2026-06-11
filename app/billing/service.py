from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from app.billing.domain import BillingSummary, BillingSubscription, BillingUsageCounter
from app.billing.plans import DEFAULT_PLAN_CODE, get_plan_definition
from app.billing.repository import BillingRepository


billing_repository = BillingRepository()


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

