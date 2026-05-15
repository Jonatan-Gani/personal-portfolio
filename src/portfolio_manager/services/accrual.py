from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from .._clock import utcnow
from ..db.connection import Database
from ..domain.enums import PositionKind, TransactionType
from ..domain.models import Liability, Transaction
from ..repositories.liabilities import LiabilityRepository
from ..repositories.transactions import TransactionRepository

log = logging.getLogger(__name__)

ACCRUAL_NOTE_PREFIX = "auto-accrual"


@dataclass
class AccrualResult:
    liability_id: str
    name: str
    days: int
    rate_apr: float
    starting_principal: float
    accrued: float
    new_principal: float


class AccrualService:
    """Accrue interest on each liability since its most recent principal-affecting
    transaction. Records the accrual as a `principal_change` transaction so the
    HoldingsService keeps reflecting the current principal naturally.

    Convention: `Liability.interest_rate` is APR as a decimal (e.g. 0.045 for 4.5%).
    Daily simple accrual: principal x (rate/365) x days. Compounded by virtue of
    successive accrual events bumping the principal.
    """

    def __init__(
        self,
        db: Database,
        liabilities: LiabilityRepository,
        transactions: TransactionRepository,
    ):
        self.db = db
        self.liabilities = liabilities
        self.transactions = transactions

    def accrue_all(self, as_of: date | None = None) -> list[AccrualResult]:
        as_of = as_of or utcnow().date()
        results: list[AccrualResult] = []
        for liab in self.liabilities.list_active():
            if not liab.interest_rate or liab.interest_rate <= 0:
                continue
            res = self._accrue_one(liab, as_of)
            if res is not None:
                results.append(res)
        return results

    def _accrue_one(self, liab: Liability, as_of: date) -> AccrualResult | None:
        # Find the most recent transaction that touched this liability's principal.
        last_row = self.db.fetchone(
            """
            SELECT MAX(transaction_date) FROM transactions
             WHERE entity_kind = 'liability' AND entity_id = ?
               AND transaction_type IN ('opening_balance','principal_change','repayment')
            """,
            [liab.liability_id],
        )
        last_date = last_row[0] if last_row and last_row[0] else liab.created_at.date()
        if isinstance(last_date, datetime):
            last_date = last_date.date()
        if last_date >= as_of:
            return None  # already accrued through today (or no time elapsed)

        # Current outstanding principal — opening_balance + principal_change - repayment.
        bal_row = self.db.fetchone(
            """
            SELECT COALESCE(SUM(CASE
                       WHEN transaction_type IN ('opening_balance','principal_change') THEN amount
                       WHEN transaction_type = 'repayment' THEN -amount
                       ELSE 0 END), 0)
              FROM transactions
             WHERE entity_kind = 'liability' AND entity_id = ?
            """,
            [liab.liability_id],
        )
        current_principal = float(bal_row[0]) if bal_row else 0.0
        if current_principal <= 0:
            return None

        days = (as_of - last_date).days
        if days <= 0:
            return None

        accrued = current_principal * (liab.interest_rate / 365.0) * days
        if accrued <= 0:
            return None

        tx = Transaction(
            transaction_date=as_of,
            transaction_type=TransactionType.PRINCIPAL_CHANGE,
            entity_kind=PositionKind.LIABILITY,
            entity_id=liab.liability_id,
            quantity=None,
            price=None,
            amount=accrued,
            currency=liab.currency,
            fees=0.0,
            notes=f"{ACCRUAL_NOTE_PREFIX} · {days}d @ {liab.interest_rate * 100:.3f}% APR",
        )
        self.transactions.insert(tx)
        log.info(
            "accrued %.2f %s on liability %s over %d days",
            accrued, liab.currency, liab.name, days,
        )
        return AccrualResult(
            liability_id=liab.liability_id,
            name=liab.name,
            days=days,
            rate_apr=liab.interest_rate,
            starting_principal=current_principal,
            accrued=accrued,
            new_principal=current_principal + accrued,
        )
