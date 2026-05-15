from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime

from ..db.connection import Database


@dataclass
class Lot:
    acquired: date
    qty: float
    unit_cost: float          # in the asset's currency
    cost_known: bool = True   # False for opening_balance with no recorded price


@dataclass
class RealizedEvent:
    sold_at: date
    qty: float
    proceeds: float           # gross of fees consumed (we subtract them in realized_pnl)
    cost_basis_consumed: float
    fees: float
    pnl: float                # proceeds - cost - fees


@dataclass
class CostBasisResult:
    asset_id: str
    quantity: float
    avg_cost: float | None              # in asset currency, weighted by remaining lot qty
    total_cost_basis: float             # sum of remaining lots' (qty * unit_cost)
    realized_pnl: float                 # cumulative
    realized_events: list[RealizedEvent] = field(default_factory=list)
    open_lots: list[Lot] = field(default_factory=list)
    incomplete_cost_basis: bool = False  # at least one lot has no recorded price


class CostBasisService:
    """Compute per-asset cost basis and realized P&L from the transaction log
    using FIFO. Splits scale lot quantities up and unit costs down (preserving
    total cost). All numbers are in the asset's local currency — convert to
    reporting currencies at the call site if needed."""

    def __init__(self, db: Database):
        self.db = db

    def compute(self, asset_id: str, as_of: date | datetime | None = None) -> CostBasisResult:
        clauses = ["entity_kind = 'asset'", "entity_id = ?"]
        params: list = [asset_id]
        if as_of is not None:
            clauses.append("transaction_date <= ?")
            params.append(as_of.date() if isinstance(as_of, datetime) else as_of)

        rows = self.db.fetchall_dict(
            f"""
            SELECT transaction_date, transaction_type, quantity, price, amount, fees
              FROM transactions
             WHERE {' AND '.join(clauses)}
             ORDER BY transaction_date ASC, created_at ASC
            """,
            params,
        )

        lots: deque[Lot] = deque()
        realized: list[RealizedEvent] = []
        realized_total = 0.0
        incomplete = False

        for r in rows:
            t_date: date = r["transaction_date"]
            t_type: str = r["transaction_type"]
            qty = float(r["quantity"] or 0)
            price = float(r["price"]) if r["price"] is not None else None
            amount = float(r["amount"] or 0)
            fees = float(r["fees"] or 0)

            if t_type in ("opening_balance", "buy"):
                if qty <= 0:
                    continue
                # Prefer explicit price; else derive from amount; else mark unknown.
                if price is not None and price != 0:
                    unit_cost = price + (fees / qty if qty else 0.0)
                    cost_known = True
                elif amount and qty:
                    unit_cost = amount / qty + (fees / qty if qty else 0.0)
                    cost_known = True
                else:
                    unit_cost = 0.0
                    cost_known = False
                if not cost_known:
                    incomplete = True
                lots.append(Lot(acquired=t_date, qty=qty, unit_cost=unit_cost, cost_known=cost_known))

            elif t_type == "sell":
                if qty <= 0:
                    continue
                remaining = qty
                cost_consumed = 0.0
                while remaining > 1e-12 and lots:
                    lot = lots[0]
                    take = min(lot.qty, remaining)
                    cost_consumed += take * lot.unit_cost
                    lot.qty -= take
                    remaining -= take
                    if lot.qty <= 1e-12:
                        lots.popleft()
                # sale_proceeds: prefer price * qty, else amount.
                if price is not None:
                    proceeds = price * qty
                elif amount:
                    proceeds = amount
                else:
                    proceeds = 0.0
                pnl = proceeds - cost_consumed - fees
                realized_total += pnl
                realized.append(RealizedEvent(
                    sold_at=t_date,
                    qty=qty,
                    proceeds=proceeds,
                    cost_basis_consumed=cost_consumed,
                    fees=fees,
                    pnl=pnl,
                ))

            elif t_type == "split":
                ratio = float(r["quantity"]) if r["quantity"] else 1.0
                if ratio and ratio != 1.0:
                    for lot in lots:
                        lot.qty *= ratio
                        if ratio != 0:
                            lot.unit_cost /= ratio

            # other transaction types (dividend, fee, interest) don't move lots

        total_qty = sum(lot.qty for lot in lots)
        total_cost = sum(lot.qty * lot.unit_cost for lot in lots)
        avg_cost = (total_cost / total_qty) if total_qty > 0 else None

        return CostBasisResult(
            asset_id=asset_id,
            quantity=total_qty,
            avg_cost=avg_cost,
            total_cost_basis=total_cost,
            realized_pnl=realized_total,
            realized_events=realized,
            open_lots=list(lots),
            incomplete_cost_basis=incomplete,
        )

    def compute_all(self, as_of: date | datetime | None = None) -> dict[str, CostBasisResult]:
        ids = [r[0] for r in self.db.fetchall(
            "SELECT DISTINCT entity_id FROM transactions WHERE entity_kind = 'asset'"
        )]
        return {aid: self.compute(aid, as_of=as_of) for aid in ids}
