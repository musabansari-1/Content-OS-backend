from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.billing.plans import PlanDefinition


@dataclass(frozen=True)
class BillingSubscription:
    id: int
    user_id: int
    plan_code: str
    provider: str
    provider_customer_id: str | None
    provider_subscription_id: str | None
    subscription_status: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BillingUsageCounter:
    id: int
    user_id: int
    period_start: datetime
    period_end: datetime
    assets_generated: int
    direct_publishes: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BillingSummary:
    subscription: BillingSubscription
    usage: BillingUsageCounter
    plan: PlanDefinition

