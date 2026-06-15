from datetime import datetime

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from app.auth.dependencies import require_current_user
from app.auth.domain import AuthUser
from app.billing.service import (
    get_billing_summary,
    get_checkout_settings,
    list_billing_plans,
    process_paddle_webhook,
    verify_paddle_webhook_signature,
)


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


class BillingCheckoutRequest(BaseModel):
    plan_code: str = Field(..., description="Target paid plan code.")


class BillingCheckoutResponse(BaseModel):
    plan_code: str
    price_id: str
    paddle_environment: str
    paddle_client_token: str
    customer_email: str
    success_url: str
    cancel_url: str
    custom_data: dict


class BillingPlanResponse(BaseModel):
    code: str
    label: str
    assets_per_month: int
    direct_publishes_per_month: int
    checkout_enabled: bool
    price_id: str | None = None


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


@router.get("/billing/plans", response_model=list[BillingPlanResponse])
def get_billing_plans() -> list[BillingPlanResponse]:
    return [BillingPlanResponse(**plan) for plan in list_billing_plans()]


@router.post("/billing/checkout", response_model=BillingCheckoutResponse)
def create_billing_checkout(
    request: BillingCheckoutRequest,
    current_user: AuthUser = Depends(require_current_user),
) -> BillingCheckoutResponse:
    settings = get_checkout_settings(
        user_id=current_user.id,
        user_email=current_user.email,
        plan_code=request.plan_code,
    )
    return BillingCheckoutResponse(**settings)


@router.post("/billing/webhooks/paddle")
async def handle_paddle_webhook(
    request: Request,
    paddle_signature: str | None = Header(default=None, alias="Paddle-Signature"),
):
    raw_body = await request.body()
    verify_paddle_webhook_signature(raw_body, paddle_signature)
    return process_paddle_webhook(raw_body)
