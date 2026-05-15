from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from ..db.connection import Database

# Sign of each transaction type for asset.quantity (entity_kind='asset')
ASSET_QTY_SIGN: dict[str, int] = {
    "opening_balance": +1,
    "buy": +1,
    "sell": -1,
    "dividend": 0,    # cash effect, not quantity
    "fee": 0,
    "interest": 0,
}

# Sign of each transaction type for cash.amount (entity_kind='cash')
CASH_AMOUNT_SIGN: dict[str, int] = {
    "opening_balance": +1,
    "deposit": +1,
    "withdraw": -1,
    "dividend": +1,
    "interest": +1,
    "fee": -1,
}

# Sign of each transaction type for liability.principal (entity_kind='liability')
LIABILITY_AMOUNT_SIGN: dict[str, int] = {
    "opening_balance": +1,
    "principal_change": +1,    # additional borrowing increases principal
    "repayment": -1,           # paying down decreases principal
    "interest": 0,             # interest accrues but is recorded against cash, not principal
}


@dataclass
class Holdings:
    asset_quantities: dict[str, float]   # {asset_id: net quantity}
    cash_balances: dict[str, float]      # {cash_id: net balance}
    liability_principals: dict[str, float]   # {liability_id: outstanding principal}


class HoldingsService:
    """Derives current state (quantities, balances, principals) from the transactions
    log. State is never stored on entity rows — this service is the single computation
    of truth.

    A `SPLIT` transaction multiplies the running asset quantity by `quantity` (the ratio).
    Asset quantity is the sum of signed quantities for non-split transactions, then for
    each subsequent split the running total is multiplied by the split ratio.
    """

    def __init__(self, db: Database):
        self.db = db

    def at(self, when: datetime | date | None = None) -> Holdings:
        """Compute holdings as of `when` (inclusive). None → as-of now."""
        date_clause = ""
        params: list = []
        if when is not None:
            date_clause = "WHERE transaction_date <= ?"
            params = [when.date() if isinstance(when, datetime) else when]

        # Assets: sum signed quantities. Splits are handled below.
        asset_rows = self.db.fetchall(
            f"""
            SELECT entity_id,
                   SUM(CASE
                       WHEN transaction_type = 'opening_balance' THEN COALESCE(quantity, 0)
                       WHEN transaction_type = 'buy'             THEN COALESCE(quantity, 0)
                       WHEN transaction_type = 'sell'            THEN -COALESCE(quantity, 0)
                       ELSE 0
                   END) AS qty
              FROM transactions
              {date_clause}{' AND' if date_clause else 'WHERE'} entity_kind = 'asset'
                AND transaction_type != 'split'
             GROUP BY entity_id
            """,
            params,
        )
        asset_quantities = {r[0]: float(r[1] or 0) for r in asset_rows}

        # Apply splits in order (multiplicative). quantity holds the split ratio.
        split_rows = self.db.fetchall(
            f"""
            SELECT entity_id, COALESCE(quantity, 1)
              FROM transactions
              {date_clause}{' AND' if date_clause else 'WHERE'} entity_kind = 'asset'
                AND transaction_type = 'split'
             ORDER BY transaction_date ASC, created_at ASC
            """,
            params,
        )
        for asset_id, ratio in split_rows:
            if asset_id in asset_quantities and ratio:
                asset_quantities[asset_id] *= float(ratio)

        # Cash: sum signed amounts.
        cash_rows = self.db.fetchall(
            f"""
            SELECT entity_id,
                   SUM(CASE
                       WHEN transaction_type IN ('opening_balance','deposit','dividend','interest') THEN COALESCE(amount, 0)
                       WHEN transaction_type IN ('withdraw','fee') THEN -COALESCE(amount, 0)
                       ELSE 0
                   END) AS bal
              FROM transactions
              {date_clause}{' AND' if date_clause else 'WHERE'} entity_kind = 'cash'
             GROUP BY entity_id
            """,
            params,
        )
        cash_balances = {r[0]: float(r[1] or 0) for r in cash_rows}

        # Liabilities: opening balance + principal_change - repayment.
        liab_rows = self.db.fetchall(
            f"""
            SELECT entity_id,
                   SUM(CASE
                       WHEN transaction_type IN ('opening_balance','principal_change') THEN COALESCE(amount, 0)
                       WHEN transaction_type = 'repayment' THEN -COALESCE(amount, 0)
                       ELSE 0
                   END) AS principal
              FROM transactions
              {date_clause}{' AND' if date_clause else 'WHERE'} entity_kind = 'liability'
             GROUP BY entity_id
            """,
            params,
        )
        liability_principals = {r[0]: float(r[1] or 0) for r in liab_rows}

        return Holdings(
            asset_quantities=asset_quantities,
            cash_balances=cash_balances,
            liability_principals=liability_principals,
        )

    def asset_quantity(self, asset_id: str, when: datetime | date | None = None) -> float:
        return self.at(when).asset_quantities.get(asset_id, 0.0)

    def cash_balance(self, cash_id: str, when: datetime | date | None = None) -> float:
        return self.at(when).cash_balances.get(cash_id, 0.0)
