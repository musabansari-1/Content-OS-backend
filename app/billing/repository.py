from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.billing.domain import BillingSubscription, BillingUsageCounter
from app.core.db import get_connection


class BillingRepository:
    def get_subscription_by_user_id(self, user_id: int) -> Optional[BillingSubscription]:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    plan_code,
                    provider,
                    provider_customer_id,
                    provider_subscription_id,
                    subscription_status,
                    current_period_start,
                    current_period_end,
                    cancel_at_period_end,
                    created_at,
                    updated_at
                FROM billing_subscriptions
                WHERE user_id = %s
                """,
                (user_id,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return BillingSubscription(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            plan_code=row["plan_code"],
            provider=row["provider"],
            provider_customer_id=row["provider_customer_id"],
            provider_subscription_id=row["provider_subscription_id"],
            subscription_status=row["subscription_status"],
            current_period_start=row["current_period_start"],
            current_period_end=row["current_period_end"],
            cancel_at_period_end=bool(row["cancel_at_period_end"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_subscription(
        self,
        *,
        user_id: int,
        plan_code: str,
        provider: str,
        subscription_status: str,
        current_period_start: datetime,
        current_period_end: datetime,
        provider_customer_id: str | None = None,
        provider_subscription_id: str | None = None,
        cancel_at_period_end: bool = False,
    ) -> BillingSubscription:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                INSERT INTO billing_subscriptions (
                    user_id,
                    plan_code,
                    provider,
                    provider_customer_id,
                    provider_subscription_id,
                    subscription_status,
                    current_period_start,
                    current_period_end,
                    cancel_at_period_end
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    user_id,
                    plan_code,
                    provider,
                    provider_customer_id,
                    provider_subscription_id,
                    subscription_status,
                    current_period_start,
                    current_period_end,
                    cancel_at_period_end,
                ),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        return self.get_subscription_by_user_id(user_id)  # type: ignore[return-value]

    def update_subscription_period(
        self,
        *,
        subscription_id: int,
        current_period_start: datetime,
        current_period_end: datetime,
    ) -> BillingSubscription:
        connection = get_connection()
        try:
            connection.execute(
                """
                UPDATE billing_subscriptions
                SET current_period_start = %s,
                    current_period_end = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (current_period_start, current_period_end, subscription_id),
            )
            connection.commit()
        finally:
            connection.close()

        return self.get_subscription_by_id(subscription_id)  # type: ignore[return-value]

    def get_subscription_by_id(self, subscription_id: int) -> Optional[BillingSubscription]:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    plan_code,
                    provider,
                    provider_customer_id,
                    provider_subscription_id,
                    subscription_status,
                    current_period_start,
                    current_period_end,
                    cancel_at_period_end,
                    created_at,
                    updated_at
                FROM billing_subscriptions
                WHERE id = %s
                """,
                (subscription_id,),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return BillingSubscription(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            plan_code=row["plan_code"],
            provider=row["provider"],
            provider_customer_id=row["provider_customer_id"],
            provider_subscription_id=row["provider_subscription_id"],
            subscription_status=row["subscription_status"],
            current_period_start=row["current_period_start"],
            current_period_end=row["current_period_end"],
            cancel_at_period_end=bool(row["cancel_at_period_end"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_usage_counter(
        self,
        *,
        user_id: int,
        period_start: datetime,
    ) -> Optional[BillingUsageCounter]:
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    period_start,
                    period_end,
                    assets_generated,
                    direct_publishes,
                    created_at,
                    updated_at
                FROM billing_usage_counters
                WHERE user_id = %s AND period_start = %s
                """,
                (user_id, period_start),
            ).fetchone()
        finally:
            connection.close()

        if not row:
            return None

        return BillingUsageCounter(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            period_start=row["period_start"],
            period_end=row["period_end"],
            assets_generated=int(row["assets_generated"]),
            direct_publishes=int(row["direct_publishes"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_usage_counter(
        self,
        *,
        user_id: int,
        period_start: datetime,
        period_end: datetime,
    ) -> BillingUsageCounter:
        connection = get_connection()
        try:
            connection.execute(
                """
                INSERT INTO billing_usage_counters (
                    user_id,
                    period_start,
                    period_end,
                    assets_generated,
                    direct_publishes
                )
                VALUES (%s, %s, %s, 0, 0)
                ON CONFLICT (user_id, period_start) DO NOTHING
                """,
                (user_id, period_start, period_end),
            )
            connection.commit()
        finally:
            connection.close()

        return self.get_usage_counter(user_id=user_id, period_start=period_start)  # type: ignore[return-value]

    def increment_usage(
        self,
        *,
        user_id: int,
        period_start: datetime,
        period_end: datetime,
        assets_generated: int = 0,
        direct_publishes: int = 0,
    ) -> BillingUsageCounter:
        connection = get_connection()
        try:
            connection.execute(
                """
                INSERT INTO billing_usage_counters (
                    user_id,
                    period_start,
                    period_end,
                    assets_generated,
                    direct_publishes
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, period_start) DO UPDATE SET
                    period_end = EXCLUDED.period_end,
                    assets_generated = billing_usage_counters.assets_generated + EXCLUDED.assets_generated,
                    direct_publishes = billing_usage_counters.direct_publishes + EXCLUDED.direct_publishes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    period_start,
                    period_end,
                    assets_generated,
                    direct_publishes,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        return self.get_usage_counter(user_id=user_id, period_start=period_start)  # type: ignore[return-value]

