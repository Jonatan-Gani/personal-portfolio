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
    fx_to_base: float | None = None  # FX rate (asset ccy → base) pinned at acquisition


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
    # Cost basis converted to the base currency at the FX rate pinned on each
    # lot's purchase transaction — not today's rate.
    total_cost_basis_base: float = 0.0
    avg_cost_base: float | None = None
    incomplete_fx: bool = False          # at least one open lot has no pinned FX rate


@dataclass
class CurrencyAttribution:
    """Unrealized return split into its price and FX components, in base currency."""
    cost_base: float
    value_base: float
    unrealized_base: float
    price_effect_base: float   # return attributable to the asset's price move
    fx_effect_base: float      # return attributable to FX moves (incl. cross term)
    complete: bool             # False if some open lot has no pinned FX rate


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
            SELECT transaction_date, transaction_type, quantity, price, amount, fees,
                   fx_rate_to_base
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
                fx = r.get("fx_rate_to_base")
                lots.append(Lot(
                    acquired=t_date, qty=qty, unit_cost=unit_cost, cost_known=cost_known,
                    fx_to_base=float(fx) if fx is not None else None,
                ))

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

        # Base-currency cost basis: each lot at the FX rate pinned on its
        # purchase. A lot with no pinned rate is skipped and flagged.
        total_cost_base = sum(
            lot.qty * lot.unit_cost * lot.fx_to_base
            for lot in lots if lot.fx_to_base is not None
        )
        incomplete_fx = any(lot.fx_to_base is None for lot in lots)
        avg_cost_base = (total_cost_base / total_qty) if total_qty > 0 and not incomplete_fx else None

        return CostBasisResult(
            asset_id=asset_id,
            quantity=total_qty,
            avg_cost=avg_cost,
            total_cost_basis=total_cost,
            realized_pnl=realized_total,
            realized_events=realized,
            open_lots=list(lots),
            incomplete_cost_basis=incomplete,
            total_cost_basis_base=total_cost_base,
            avg_cost_base=avg_cost_base,
            incomplete_fx=incomplete_fx,
        )

    def attribute_currency(
        self,
        asset_id: str,
        current_price: float,
        current_fx_to_base: float,
        as_of: date | datetime | None = None,
    ) -> CurrencyAttribution | None:
        """Decompose a position's unrealized return into the price move and the
        FX move, in the base currency, using the FX rate pinned on each lot.

        For one lot bought at price p0 / rate fx0, now worth p1 / fx1:
          price effect = qty * (p1 - p0) * fx0   (return with FX held at buy)
          fx effect    = the remainder           (absorbs the price/FX cross term)
        so price_effect + fx_effect == unrealized_base exactly.

        Returns None when there are no open lots.
        """
        result = self.compute(asset_id, as_of=as_of)
        lots = [lot for lot in result.open_lots if lot.qty > 0]
        if not lots:
            return None

        cost_base = 0.0
        value_base = 0.0
        price_effect = 0.0
        priced_lots = [lot for lot in lots if lot.fx_to_base is not None]
        for lot in priced_lots:
            fx0 = lot.fx_to_base
            cost_base += lot.qty * lot.unit_cost * fx0
            value_base += lot.qty * current_price * current_fx_to_base
            price_effect += lot.qty * (current_price - lot.unit_cost) * fx0

        unrealized_base = value_base - cost_base
        return CurrencyAttribution(
            cost_base=cost_base,
            value_base=value_base,
            unrealized_base=unrealized_base,
            price_effect_base=price_effect,
            fx_effect_base=unrealized_base - price_effect,
            complete=all(lot.fx_to_base is not None for lot in lots),
        )

    def compute_all(self, as_of: date | datetime | None = None) -> dict[str, CostBasisResult]:
        ids = [r[0] for r in self.db.fetchall(
            "SELECT DISTINCT entity_id FROM transactions WHERE entity_kind = 'asset'"
        )]
        return {aid: self.compute(aid, as_of=as_of) for aid in ids}
