from __future__ import annotations

from .._clock import utcnow
from ..db.connection import Database
from ..domain.exceptions import NotFoundError
from ..domain.models import CashHolding


class CashRepository:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, cash: CashHolding) -> CashHolding:
        cash.updated_at = utcnow()
        self.db.execute(
            """
            INSERT INTO cash_holdings (
                cash_id, account_name, currency, country, notes, tags,
                created_at, updated_at, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT (cash_id) DO UPDATE SET
                account_name = EXCLUDED.account_name,
                currency = EXCLUDED.currency,
                country = EXCLUDED.country,
                notes = EXCLUDED.notes,
                tags = EXCLUDED.tags,
                updated_at = EXCLUDED.updated_at,
                is_active = EXCLUDED.is_active
            """,
            [
                cash.cash_id, cash.account_name, cash.currency, cash.country,
                cash.notes, cash.tags, cash.created_at, cash.updated_at, cash.is_active,
            ],
        )
        return cash

    def get(self, cash_id: str) -> CashHolding:
        row = self.db.fetchall_dict("SELECT * FROM cash_holdings WHERE cash_id = ?", [cash_id])
        if not row:
            raise NotFoundError(f"cash holding {cash_id!r} not found")
        return CashHolding.model_validate(row[0])

    def list_active(self) -> list[CashHolding]:
        rows = self.db.fetchall_dict(
            "SELECT * FROM cash_holdings WHERE is_active = TRUE ORDER BY account_name"
        )
        return [CashHolding.model_validate(r) for r in rows]

    def list_all(self) -> list[CashHolding]:
        rows = self.db.fetchall_dict(
            "SELECT * FROM cash_holdings ORDER BY is_active DESC, account_name"
        )
        return [CashHolding.model_validate(r) for r in rows]

    def deactivate(self, cash_id: str) -> None:
        self.db.execute(
            "UPDATE cash_holdings SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE cash_id = ?",
            [cash_id],
        )

    def delete(self, cash_id: str) -> None:
        self.db.execute("DELETE FROM cash_holdings WHERE cash_id = ?", [cash_id])
