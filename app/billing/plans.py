from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanDefinition:
    code: str
    label: str
    assets_per_month: int
    direct_publishes_per_month: int


FREE_PLAN = PlanDefinition(
    code="free",
    label="Free",
    assets_per_month=20,
    direct_publishes_per_month=3,
)

PRO_PLAN = PlanDefinition(
    code="pro",
    label="Pro",
    assets_per_month=150,
    direct_publishes_per_month=40,
)

MAX_PLAN = PlanDefinition(
    code="max",
    label="Max",
    assets_per_month=600,
    direct_publishes_per_month=200,
)

PLAN_DEFINITIONS = {
    FREE_PLAN.code: FREE_PLAN,
    PRO_PLAN.code: PRO_PLAN,
    MAX_PLAN.code: MAX_PLAN,
}

DEFAULT_PLAN_CODE = FREE_PLAN.code


def get_plan_definition(plan_code: str) -> PlanDefinition:
    return PLAN_DEFINITIONS.get(plan_code, FREE_PLAN)

