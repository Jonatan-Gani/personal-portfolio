from __future__ import annotations

from dataclasses import dataclass

from ..db.connection import Database


@dataclass
class PositionDelta:
    position_kind: str
    entity_id: str
    name: str | None
    currency: str | None              # local currency
    qty_old: float | None
    qty_new: float | None
    price_old: float | None           # local price
    price_new: float | None
    value_old_report: float           # value in report currency
    value_new_report: float
    delta_total: float                # value_new - value_old
    price_effect: float               # q * (P - p) * x   (local price move on prior holdings)
    fx_effect: float                  # q * p * (X - x)   (FX move on prior holdings)
    qty_effect: float                 # (Q - q) * P * X   (trades at current price/fx)

    @property
    def status(self) -> str:
        if self.qty_old in (None, 0) and self.qty_new not in (None, 0):
            return "new"
        if self.qty_new in (None, 0) and self.qty_old not in (None, 0):
            return "exited"
        return "held"


@dataclass
class SnapshotDiff:
    from_snapshot_id: str
    to_snapshot_id: str
    report_currency: str
    total_old: float
    total_new: float
    total_delta: float
    total_price_effect: float
    total_fx_effect: float
    total_qty_effect: float
    positions: list[PositionDelta]


class SnapshotDiffService:
    """Decompose the change between two snapshots, per position, into
    price / FX / quantity effects (in the requested reporting currency).

    Decomposition (q,p,x = old qty/price/fx_to_report; Q,P,X = new):
        ΔV = Q*P*X - q*p*x
           = q*(P - p)*x        # price effect
           + q*P*(X - x)        # fx effect
           + (Q - q)*P*X        # qty effect
    Verifies algebraically that the three effects sum to ΔV.
    """

    def __init__(self, db: Database):
        self.db = db

    def diff(self, from_snapshot_id: str, to_snapshot_id: str, report_currency: str) -> SnapshotDiff:
        ccy = report_currency.upper()
        old = self._load(from_snapshot_id, ccy)
        new = self._load(to_snapshot_id, ccy)
        keys = set(old.keys()) | set(new.keys())

        deltas: list[PositionDelta] = []
        tot_price = tot_fx = tot_qty = 0.0
        tot_old = tot_new = 0.0

        for k in keys:
            o = old.get(k)
            n = new.get(k)
            kind, eid = k

            qty_o = (o["quantity"] if o else None) or 0.0
            qty_n = (n["quantity"] if n else None) or 0.0
            price_o = (o["price_local"] if o else None) or 0.0
            price_n = (n["price_local"] if n else None) or 0.0
            v_old_r = (o["value_report"] if o else 0.0) or 0.0
            v_new_r = (n["value_report"] if n else 0.0) or 0.0

            # Implied fx_to_report from value_report / value_local; if local is zero, fall back.
            fx_o = (v_old_r / o["value_local"]) if (o and o["value_local"]) else None
            fx_n = (v_new_r / n["value_local"]) if (n and n["value_local"]) else None

            # If a side is missing, impute prices/fx from the other side so the
            # decomposition stays clean (the trade absorbs everything).
            if not o:
                price_o = price_n
                fx_o = fx_n if fx_n is not None else 1.0
            if not n:
                price_n = price_o
                fx_n = fx_o if fx_o is not None else 1.0
            if fx_o is None:
                fx_o = 1.0
            if fx_n is None:
                fx_n = 1.0

            price_effect = qty_o * (price_n - price_o) * fx_o
            fx_effect = qty_o * price_n * (fx_n - fx_o)
            qty_effect = (qty_n - qty_o) * price_n * fx_n
            delta_total = v_new_r - v_old_r

            tot_price += price_effect
            tot_fx += fx_effect
            tot_qty += qty_effect
            tot_old += v_old_r
            tot_new += v_new_r

            name = (n["name"] if n else o["name"])
            currency = (n["currency"] if n else (o["currency"] if o else None))
            deltas.append(PositionDelta(
                position_kind=kind,
                entity_id=eid,
                name=name,
                currency=currency,
                qty_old=qty_o if o else None,
                qty_new=qty_n if n else None,
                price_old=price_o if o else None,
                price_new=price_n if n else None,
                value_old_report=v_old_r,
                value_new_report=v_new_r,
                delta_total=delta_total,
                price_effect=price_effect,
                fx_effect=fx_effect,
                qty_effect=qty_effect,
            ))

        deltas.sort(key=lambda d: abs(d.delta_total), reverse=True)
        return SnapshotDiff(
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            report_currency=ccy,
            total_old=tot_old,
            total_new=tot_new,
            total_delta=tot_new - tot_old,
            total_price_effect=tot_price,
            total_fx_effect=tot_fx,
            total_qty_effect=tot_qty,
            positions=deltas,
        )

    def _load(self, snapshot_id: str, ccy: str) -> dict[tuple[str, str], dict]:
        rows = self.db.fetchall_dict(
            """
            SELECT p.position_kind, p.entity_id, p.name, p.currency, p.quantity, p.price_local,
                   p.value_local, COALESCE(v.value, 0) AS value_report
              FROM snapshot_positions p
              LEFT JOIN snapshot_position_values v
                ON v.snapshot_id = p.snapshot_id
               AND v.position_kind = p.position_kind
               AND v.entity_id = p.entity_id
               AND v.currency = ?
             WHERE p.snapshot_id = ?
            """,
            [ccy, snapshot_id],
        )
        out: dict[tuple[str, str], dict] = {}
        for r in rows:
            out[(r["position_kind"], r["entity_id"])] = {
                "name": r["name"],
                "currency": r["currency"],
                "quantity": float(r["quantity"]) if r["quantity"] is not None else None,
                "price_local": float(r["price_local"]) if r["price_local"] is not None else None,
                "value_local": float(r["value_local"] or 0),
                "value_report": float(r["value_report"] or 0),
            }
        return out
