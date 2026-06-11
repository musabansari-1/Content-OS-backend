from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import require_current_user
from app.auth.domain import AuthUser
from app.billing.service import get_billing_summary


router = APIRouter()


class BillingUsageResponse(BaseModel):
    assets_generated: int
    direct_publishes: int


class BillingLimitsResponse(BaseModel):
    assets_per_month: int
    direct_publishes_per_month: int


class BillingRemainingResponse(BaseModel):
    assets_remaining: int
    direct_publishes_remaining: int


class BillingSummaryResponse(BaseModel):
    plan_code: str
    plan_label: str
    provider: str
    subscription_status: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    usage: BillingUsageResponse
    limits: BillingLimitsResponse
    remaining: BillingRemainingResponse


@router.get("/billing/me", response_model=BillingSummaryResponse)
def get_my_billing(current_user: AuthUser = Depends(require_current_user)) -> BillingSummaryResponse:
    summary = get_billing_summary(current_user.id)
    return BillingSummaryResponse(
        plan_code=summary.plan.code,
        plan_label=summary.plan.label,
        provider=summary.subscription.provider,
        subscription_status=summary.subscription.subscription_status,
        current_period_start=summary.subscription.current_period_start,
        current_period_end=summary.subscription.current_period_end,
        cancel_at_period_end=summary.subscription.cancel_at_period_end,
        usage=BillingUsageResponse(
            assets_generated=summary.usage.assets_generated,
            direct_publishes=summary.usage.direct_publishes,
        ),
        limits=BillingLimitsResponse(
            assets_per_month=summary.plan.assets_per_month,
            direct_publishes_per_month=summary.plan.direct_publishes_per_month,
        ),
        remaining=BillingRemainingResponse(
            assets_remaining=max(0, summary.plan.assets_per_month - summary.usage.assets_generated),
            direct_publishes_remaining=max(
                0,
                summary.plan.direct_publishes_per_month - summary.usage.direct_publishes,
            ),
        ),
    )

