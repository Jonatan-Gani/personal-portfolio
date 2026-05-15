from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from ..db.connection import Database
from .cost_basis import CostBasisService
from .fx import FXService


@dataclass
class IncomeRow:
    entity_kind: str         # 'asset' | 'cash'
    entity_id: str
    name: str
    currency: str            # native currency for the income stream
    ttm_dividend: float      # in entity's native currency
    ttm_interest: float
    ttm_total: float
    ttm_in_report: float     # in reporting currency
    current_value_report: float
    cost_basis_report: float | None
    yield_on_value: float | None        # ttm_in_report / current_value_report
    yield_on_cost: float | None         # ttm_in_report / cost_basis_report
    payments: int


@dataclass
class IncomeReport:
    report_currency: str
    as_of: date
    rows: list[IncomeRow]
    ttm_total_report: float
    forward_annual_report: float        # = ttm_total_report (run-rate; trivial projection)
    portfolio_yield: float | None       # ttm_total / total_value
    monthly_series: list[dict]          # [{month, dividends, interest, total}]


class IncomeService:
    """Trailing-12-month dividend & interest income, per asset and total. Yields
    computed against latest snapshot value (yield-on-value) and FIFO cost basis
    (yield-on-cost, assets only)."""

    def __init__(self, db: Database, fx: FXService, cost_basis: CostBasisService, base_currency: str):
        self.db = db
        self.fx = fx
        self.cost_basis = cost_basis
        self.base_currency = base_currency.upper()

    def report(self, report_currency: str, as_of: date | None = None) -> IncomeReport:
        ccy = report_currency.upper()
        as_of = as_of or date.today()
        since = as_of - timedelta(days=365)

        rows = self.db.fetchall_dict(
            """
            SELECT t.entity_kind, t.entity_id, t.transaction_type, t.transaction_date,
                   t.amount, t.currency
              FROM transactions t
             WHERE t.transaction_type IN ('dividend', 'interest')
               AND t.transaction_date >= ?
               AND t.transaction_date <= ?
            """,
            [since, as_of],
        )

        # Names for entities
        asset_names = {r[0]: r[1] for r in self.db.fetchall("SELECT asset_id, name FROM assets")}
        cash_names = {r[0]: r[1] for r in self.db.fetchall("SELECT cash_id, account_name FROM cash_holdings")}

        # current values (latest snapshot, in report ccy)
        current_value_per: dict[tuple[str, str], float] = {}
        latest_snap = self.db.fetchone("SELECT snapshot_id FROM snapshots ORDER BY taken_at DESC LIMIT 1")
        if latest_snap:
            for r in self.db.fetchall(
                """
                SELECT p.position_kind, p.entity_id, COALESCE(v.value, 0)
                  FROM snapshot_positions p
                  LEFT JOIN snapshot_position_values v
                    ON v.snapshot_id = p.snapshot_id
                   AND v.position_kind = p.position_kind
                   AND v.entity_id = p.entity_id
                   AND v.currency = ?
                 WHERE p.snapshot_id = ?
                """,
                [ccy, latest_snap[0]],
            ):
                current_value_per[(r[0], r[1])] = float(r[2] or 0)

        # Per-entity bucketing
        per: dict[tuple[str, str], dict] = defaultdict(lambda: {
            "dividend_native": 0.0, "interest_native": 0.0, "currency": None, "payments": 0,
        })
        # Monthly aggregation in report currency
        monthly: dict[str, dict[str, float]] = defaultdict(lambda: {"dividends": 0.0, "interest": 0.0})

        for r in rows:
            key = (r["entity_kind"], r["entity_id"])
            amt = float(r["amount"] or 0)
            cur = (r["currency"] or "USD").upper()
            per[key]["currency"] = per[key]["currency"] or cur
            per[key]["payments"] += 1
            if r["transaction_type"] == "dividend":
                per[key]["dividend_native"] += amt
            else:
                per[key]["interest_native"] += amt
            try:
                amt_r = self.fx.convert(amt, cur, ccy)
            except Exception:
                amt_r = amt
            mkey = f"{r['transaction_date'].year:04d}-{r['transaction_date'].month:02d}"
            if r["transaction_type"] == "dividend":
                monthly[mkey]["dividends"] += amt_r
            else:
                monthly[mkey]["interest"] += amt_r

        out_rows: list[IncomeRow] = []
        ttm_total = 0.0
        for (kind, eid), agg in per.items():
            ttm_native = agg["dividend_native"] + agg["interest_native"]
            cur = agg["currency"] or ccy
            try:
                ttm_in_report = self.fx.convert(ttm_native, cur, ccy)
            except Exception:
                ttm_in_report = ttm_native
            ttm_total += ttm_in_report

            cv = current_value_per.get((kind, eid), 0.0)
            cost_basis_report: float | None = None
            yield_on_cost: float | None = None
            if kind == "asset":
                try:
                    cb = self.cost_basis.compute(eid, as_of=as_of)
                    if cb.total_cost_basis > 0:
                        # cost basis is in asset's local currency
                        asset_ccy_row = self.db.fetchone("SELECT currency FROM assets WHERE asset_id = ?", [eid])
                        asset_ccy = (asset_ccy_row[0] if asset_ccy_row else cur).upper()
                        try:
                            cost_basis_report = self.fx.convert(cb.total_cost_basis, asset_ccy, ccy)
                        except Exception:
                            cost_basis_report = cb.total_cost_basis
                        if cost_basis_report and cost_basis_report > 0:
                            yield_on_cost = ttm_in_report / cost_basis_report
                except Exception:
                    pass

            yield_on_value = (ttm_in_report / cv) if cv > 0 else None

            out_rows.append(IncomeRow(
                entity_kind=kind,
                entity_id=eid,
                name=(asset_names.get(eid) if kind == "asset" else cash_names.get(eid)) or eid[:8],
                currency=cur,
                ttm_dividend=agg["dividend_native"],
                ttm_interest=agg["interest_native"],
                ttm_total=ttm_native,
                ttm_in_report=ttm_in_report,
                current_value_report=cv,
                cost_basis_report=cost_basis_report,
                yield_on_value=yield_on_value,
                yield_on_cost=yield_on_cost,
                payments=agg["payments"],
            ))

        out_rows.sort(key=lambda r: r.ttm_in_report, reverse=True)

        # Portfolio total value (assets + cash) in report ccy from latest snapshot
        total_pv = 0.0
        if latest_snap:
            row = self.db.fetchone(
                """
                SELECT COALESCE(SUM(v.value), 0)
                  FROM snapshot_positions p
                  JOIN snapshot_position_values v
                    ON v.snapshot_id = p.snapshot_id
                   AND v.position_kind = p.position_kind
                   AND v.entity_id = p.entity_id
                 WHERE p.snapshot_id = ?
                   AND v.currency = ?
                   AND p.position_kind IN ('asset','cash')
                """,
                [latest_snap[0], ccy],
            )
            total_pv = float(row[0] or 0) if row else 0.0

        # Sort monthly chronologically
        monthly_series = [{"month": m, **monthly[m], "total": monthly[m]["dividends"] + monthly[m]["interest"]}
                          for m in sorted(monthly.keys())]

        return IncomeReport(
            report_currency=ccy,
            as_of=as_of,
            rows=out_rows,
            ttm_total_report=ttm_total,
            forward_annual_report=ttm_total,
            portfolio_yield=(ttm_total / total_pv) if total_pv > 0 else None,
            monthly_series=monthly_series,
        )
