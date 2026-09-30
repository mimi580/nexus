"""Hard budget control. No paid action bypasses this class.

Spend flows: estimate -> reserve -> (act) -> commit or release.
A reservation that would break the monthly ceiling or a category ceiling is
rejected before the action runs, so the ceiling cannot be exceeded by racing.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import HARD_MONTHLY_CEILING_USD, Settings, get_settings
from app.core.errors import BudgetExceeded
from app.core.types import utcnow
from app.database.models import BudgetPeriod, CostEntry

EPSILON = 1e-9


def period_of(moment: datetime | None = None) -> str:
    return (moment or utcnow()).strftime("%Y-%m")


class BudgetController:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ---------------------------------------------------------------- period
    def _period_row(self, period: str) -> BudgetPeriod:
        row = self.session.scalar(select(BudgetPeriod).where(BudgetPeriod.period == period))
        if row is None:
            row = BudgetPeriod(
                period=period,
                limit_usd=min(self.settings.budget_monthly_limit_usd, HARD_MONTHLY_CEILING_USD),
                category_limits=self.settings.category_limits,
            )
            self.session.add(row)
            self.session.flush()
        return row

    def limit(self, period: str | None = None) -> float:
        return min(self._period_row(period or period_of()).limit_usd, HARD_MONTHLY_CEILING_USD)

    def category_limit(self, category: str, period: str | None = None) -> float | None:
        limits = self._period_row(period or period_of()).category_limits or {}
        value = limits.get(category)
        return float(value) if value is not None else None

    # ----------------------------------------------------------------- state
    def _sum(self, period: str, *, category: str | None = None, states: tuple[str, ...]) -> float:
        stmt = select(CostEntry).where(
            CostEntry.period == period, CostEntry.state.in_(states)
        )
        if category:
            stmt = stmt.where(CostEntry.category == category)
        return round(sum(e.amount_usd for e in self.session.scalars(stmt)), 6)

    def committed(self, period: str | None = None, category: str | None = None) -> float:
        return self._sum(period or period_of(), category=category, states=("committed",))

    def reserved(self, period: str | None = None, category: str | None = None) -> float:
        return self._sum(period or period_of(), category=category, states=("reserved",))

    def encumbered(self, period: str | None = None, category: str | None = None) -> float:
        return self._sum(period or period_of(), category=category, states=("committed", "reserved"))

    def remaining(self, period: str | None = None) -> float:
        p = period or period_of()
        return round(self.limit(p) - self.encumbered(p), 6)

    def category_remaining(self, category: str, period: str | None = None) -> float:
        p = period or period_of()
        cap = self.category_limit(category, p)
        overall = self.remaining(p)
        if cap is None:
            return overall
        return round(min(cap - self.encumbered(p, category), overall), 6)

    def hard_stopped(self, period: str | None = None) -> bool:
        return self._period_row(period or period_of()).hard_stopped or self.remaining() <= EPSILON

    def set_hard_stop(self, value: bool, period: str | None = None) -> None:
        self._period_row(period or period_of()).hard_stopped = value
        self.session.flush()

    # --------------------------------------------------------------- actions
    def can_spend(self, category: str, amount: float, period: str | None = None) -> bool:
        p = period or period_of()
        if self._period_row(p).hard_stopped:
            return False
        if amount < 0:
            return False
        return self.category_remaining(category, p) + EPSILON >= amount

    def reserve(self, category: str, amount: float, reason: str = "", period: str | None = None) -> str:
        p = period or period_of()
        amount = round(float(amount), 6)
        if not self.can_spend(category, amount, p):
            raise BudgetExceeded(
                "reservation rejected: would exceed budget",
                category=category,
                amount=amount,
                category_remaining=self.category_remaining(category, p),
                period_remaining=self.remaining(p),
                period=p,
            )
        entry = CostEntry(
            period=p, category=category, amount_usd=amount, state="reserved", reason=reason[:300]
        )
        self.session.add(entry)
        self.session.flush()
        return entry.id

    def commit(self, reservation_id: str, actual_usd: float | None = None) -> float:
        entry = self.session.get(CostEntry, reservation_id)
        if entry is None or entry.state != "reserved":
            raise BudgetExceeded("unknown or non-reserved cost entry", reservation_id=reservation_id)
        if actual_usd is not None:
            actual = round(float(actual_usd), 6)
            delta = actual - entry.amount_usd
            if delta > 0 and not self.can_spend(entry.category, delta, entry.period):
                # Overrun beyond the reservation is clamped, never silently allowed.
                actual = entry.amount_usd + max(self.category_remaining(entry.category, entry.period), 0.0)
            entry.amount_usd = actual
        entry.state = "committed"
        self.session.flush()
        if self.remaining(entry.period) <= EPSILON:
            self.set_hard_stop(True, entry.period)
        return entry.amount_usd

    def release(self, reservation_id: str) -> None:
        entry = self.session.get(CostEntry, reservation_id)
        if entry is not None and entry.state == "reserved":
            entry.state = "released"
            entry.amount_usd = 0.0
            self.session.flush()

    def record_direct(self, category: str, amount: float, reason: str = "") -> str:
        """Spend already incurred externally (e.g. a monthly infra invoice)."""
        rid = self.reserve(category, amount, reason)
        self.commit(rid, amount)
        return rid

    # --------------------------------------------------------------- reports
    def forecast(self, period: str | None = None, now: datetime | None = None) -> float:
        p = period or period_of()
        moment = now or utcnow()
        day = moment.day
        days_in_month = 30
        spent = self.encumbered(p)
        if day <= 0:
            return spent
        return round(min(spent / day * days_in_month, self.limit(p) * 3), 4)

    def snapshot(self, period: str | None = None) -> dict:
        p = period or period_of()
        cats = self._period_row(p).category_limits or {}
        return {
            "period": p,
            "limit_usd": self.limit(p),
            "committed_usd": self.committed(p),
            "reserved_usd": self.reserved(p),
            "remaining_usd": self.remaining(p),
            "forecast_usd": self.forecast(p),
            "hard_stopped": self.hard_stopped(p),
            "categories": {
                c: {
                    "limit": float(limit),
                    "used": self.encumbered(p, c),
                    "remaining": self.category_remaining(c, p),
                }
                for c, limit in cats.items()
            },
        }
