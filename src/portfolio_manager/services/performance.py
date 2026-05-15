from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from ..db.connection import Database
from .fx import FXService

# Transaction types that move money in/out of the wealth pool from outside.
# Everything else (BUY, SELL, DIVIDEND, INTEREST, FEE, REPAYMENT, PRINCIPAL_CHANGE,
# SPLIT, OPENING_BALANCE) is internal — it shuffles value between asset/cash/liability
# rows but doesn't change net worth from an external standpoint.
EXTERNAL_FLOW_TYPES = {"deposit", "withdraw"}


@dataclass
class NavPoint:
    when: datetime
    nav: float
    snapshot_id: str


@dataclass
class CashFlow:
    when: date
    amount: float            # +ve = money in (deposit), -ve = money out (withdraw)
    currency: str
    note: str = ""


@dataclass
class CashFlowMonth:
    month: str               # 'YYYY-MM'
    deposits: float
    withdrawals: float
    dividends: float
    interest: float
    fees: float
    net_external: float      # deposits - withdrawals
    market_pnl: float        # Δnet_worth - net_external (gross of dividends; dividends/fees are internal)


class PerformanceService:
    """Time-Weighted Return, Money-Weighted Return (XIRR), drawdown, and cash-flow
    attribution. All numbers are reported in the requested currency, computed off the
    snapshot history (NAV) and the transaction log (cash flows)."""

    def __init__(self, db: Database, fx: FXService, base_currency: str):
        self.db = db
        self.fx = fx
        self.base_currency = base_currency.upper()

    # ────────────────────────────────────────────────────────── NAV time series
    def nav_series(
        self,
        report_currency: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[NavPoint]:
        ccy = report_currency.upper()
        rows = self.db.fetchall(
            """
            SELECT s.snapshot_id, s.taken_at,
                   COALESCE(SUM(CASE WHEN p.position_kind = 'asset'     THEN v.value ELSE 0 END), 0)
                 + COALESCE(SUM(CASE WHEN p.position_kind = 'cash'      THEN v.value ELSE 0 END), 0)
                 - COALESCE(SUM(CASE WHEN p.position_kind = 'liability' THEN v.value ELSE 0 END), 0) AS nav
              FROM snapshots s
              LEFT JOIN snapshot_positions p
                ON p.snapshot_id = s.snapshot_id
              LEFT JOIN snapshot_position_values v
                ON v.snapshot_id = p.snapshot_id
               AND v.position_kind = p.position_kind
               AND v.entity_id = p.entity_id
               AND v.currency = ?
             WHERE (? IS NULL OR s.taken_at >= ?)
               AND (? IS NULL OR s.taken_at <= ?)
             GROUP BY s.snapshot_id, s.taken_at
             ORDER BY s.taken_at ASC
            """,
            [ccy, since, since, until, until],
        )
        return [NavPoint(when=r[1], nav=float(r[2] or 0), snapshot_id=r[0]) for r in rows]

    # ────────────────────────────────────────────────────────── external cash flows
    def external_flows(
        self,
        report_currency: str,
        since: date | None = None,
        until: date | None = None,
    ) -> list[CashFlow]:
        ccy = report_currency.upper()
        clauses = ["transaction_type IN ('deposit','withdraw')"]
        params: list = []
        if since is not None:
            clauses.append("transaction_date >= ?")
            params.append(since)
        if until is not None:
            clauses.append("transaction_date <= ?")
            params.append(until)
        rows = self.db.fetchall_dict(
            f"""
            SELECT transaction_date, transaction_type, amount, currency, notes
              FROM transactions
             WHERE {' AND '.join(clauses)}
             ORDER BY transaction_date ASC, created_at ASC
            """,
            params,
        )
        flows: list[CashFlow] = []
        for r in rows:
            amt_native = float(r["amount"] or 0)
            sign = +1 if r["transaction_type"] == "deposit" else -1
            try:
                amt_in_ccy = self.fx.convert(amt_native, r["currency"], ccy)
            except Exception:
                amt_in_ccy = amt_native  # fail-soft: assume same currency
            flows.append(CashFlow(
                when=r["transaction_date"],
                amount=sign * amt_in_ccy,
                currency=ccy,
                note=r["notes"] or "",
            ))
        return flows

    # ────────────────────────────────────────────────────────── TWR (Modified Dietz, chained)
    def twr(self, report_currency: str, since: datetime | None, until: datetime | None) -> dict:
        """Chain-link sub-period returns between successive snapshots, isolating
        external flows that fell inside each interval. Each sub-period uses the
        Modified-Dietz approximation (cash flow assumed at end of period, weight=0).
        For monthly-ish snapshots this is accurate to a few bp."""
        navs = self.nav_series(report_currency, since=since, until=until)
        if len(navs) < 2:
            return {
                "twr": None, "annualized": None,
                "sub_periods": 0,
                "from": navs[0].when.isoformat() if navs else None,
                "to": navs[-1].when.isoformat() if navs else None,
            }

        flows = self.external_flows(
            report_currency,
            since=navs[0].when.date(),
            until=navs[-1].when.date(),
        )
        # Bucket flows into intervals (start_date, end_date]
        bucketed: list[float] = [0.0] * (len(navs) - 1)
        for f in flows:
            for i in range(len(navs) - 1):
                start = navs[i].when.date()
                end = navs[i + 1].when.date()
                if start < f.when <= end:
                    bucketed[i] += f.amount
                    break
                if i == 0 and f.when <= start:
                    bucketed[0] += f.amount
                    break

        product = 1.0
        sub_returns: list[dict] = []
        for i in range(len(navs) - 1):
            start_v = navs[i].nav
            end_v = navs[i + 1].nav
            cf = bucketed[i]
            # Modified-Dietz with end-of-period weighting (simplest stable form):
            #   R = (end - cf) / start - 1
            denom = start_v
            if denom <= 0:
                continue
            r = (end_v - cf) / denom - 1.0
            product *= 1.0 + r
            sub_returns.append({
                "from": navs[i].when.isoformat(),
                "to": navs[i + 1].when.isoformat(),
                "start_nav": start_v,
                "end_nav": end_v,
                "cash_flow": cf,
                "return": r,
            })

        twr = product - 1.0
        days = max((navs[-1].when - navs[0].when).days, 1)
        years = days / 365.25
        annualized = ((1.0 + twr) ** (1.0 / years) - 1.0) if years > 0 and (1.0 + twr) > 0 else None
        return {
            "twr": twr,
            "annualized": annualized,
            "sub_periods": len(sub_returns),
            "from": navs[0].when.isoformat(),
            "to": navs[-1].when.isoformat(),
            "start_nav": navs[0].nav,
            "end_nav": navs[-1].nav,
            "sub_returns": sub_returns,
        }

    # ────────────────────────────────────────────────────────── XIRR
    def xirr(self, report_currency: str, since: datetime | None, until: datetime | None) -> dict:
        """Money-weighted return: solve sum(CF_i / (1+r)^(d_i/365)) = 0.
        Treats the starting NAV as a CF in (negative — money you "had"), external
        flows as their signed amounts (deposit negative, withdraw positive — same
        sign convention: outgoing = negative from your wallet's view), and the
        ending NAV as a CF out (positive)."""
        navs = self.nav_series(report_currency, since=since, until=until)
        if len(navs) < 2:
            return {"xirr": None, "from": None, "to": None, "n_flows": 0}

        flows = self.external_flows(
            report_currency,
            since=navs[0].when.date(),
            until=navs[-1].when.date(),
        )
        # XIRR convention: CF you paid in is negative, CF you received is positive.
        # Our external_flows: deposit = +ve (money in to your wallet from outside).
        # Reframe from the "investor → portfolio" perspective: deposit becomes -ve
        # (you handed money to the portfolio); withdraw becomes +ve.
        # Anchor: start NAV is what you "had invested" already → -start_nav at t0;
        # end NAV is +end_nav at t_end (you take it back).
        cashflows: list[tuple[date, float]] = []
        cashflows.append((navs[0].when.date(), -navs[0].nav))
        for f in flows:
            cashflows.append((f.when, -f.amount))  # flip sign
        cashflows.append((navs[-1].when.date(), navs[-1].nav))

        # Filter zero-only or single-sign series — XIRR undefined.
        if not (any(c[1] > 0 for c in cashflows) and any(c[1] < 0 for c in cashflows)):
            return {"xirr": None, "from": navs[0].when.isoformat(), "to": navs[-1].when.isoformat(), "n_flows": len(cashflows)}

        rate = _xirr_solve(cashflows)
        return {
            "xirr": rate,
            "from": navs[0].when.isoformat(),
            "to": navs[-1].when.isoformat(),
            "n_flows": len(cashflows),
        }

    # ────────────────────────────────────────────────────────── drawdown series
    def drawdown_series(self, report_currency: str, since: datetime | None, until: datetime | None) -> dict:
        """Returns running peak and drawdown (negative %) at each snapshot, plus max
        drawdown stat."""
        navs = self.nav_series(report_currency, since=since, until=until)
        if not navs:
            return {
                "points": [], "max_drawdown": None, "max_drawdown_at": None,
                "current_drawdown": None, "current_peak": None, "current_peak_at": None,
            }
        peak = -math.inf
        points: list[dict] = []
        max_dd = 0.0
        max_dd_when: datetime | None = None
        peak_when: datetime | None = None
        for p in navs:
            if p.nav > peak:
                peak = p.nav
                peak_when = p.when
            dd = (p.nav - peak) / peak if peak > 0 else 0.0
            points.append({
                "when": p.when.isoformat(),
                "nav": p.nav,
                "peak": peak,
                "drawdown": dd,
            })
            if dd < max_dd:
                max_dd = dd
                max_dd_when = p.when
        return {
            "points": points,
            "max_drawdown": max_dd,
            "max_drawdown_at": max_dd_when.isoformat() if max_dd_when else None,
            "current_drawdown": points[-1]["drawdown"] if points else None,
            "current_peak": peak,
            "current_peak_at": peak_when.isoformat() if peak_when else None,
        }

    # ────────────────────────────────────────────────────────── monthly cash-flow attribution
    def monthly_attribution(
        self,
        report_currency: str,
        since: date | None = None,
        until: date | None = None,
    ) -> list[CashFlowMonth]:
        """Per-calendar-month breakdown of:
            - external flows (deposits/withdrawals)
            - income (dividends/interest)
            - fees
            - market P&L = Δnet_worth - net_external
        Net worth deltas come from the snapshot stream, projected to `report_currency`.
        """
        ccy = report_currency.upper()
        # transactions grouped by month
        clauses = ["transaction_type IN ('deposit','withdraw','dividend','interest','fee')"]
        params: list = []
        if since is not None:
            clauses.append("transaction_date >= ?")
            params.append(since)
        if until is not None:
            clauses.append("transaction_date <= ?")
            params.append(until)
        tx_rows = self.db.fetchall_dict(
            f"""
            SELECT transaction_date, transaction_type, amount, currency
              FROM transactions
             WHERE {' AND '.join(clauses)}
             ORDER BY transaction_date ASC
            """,
            params,
        )
        from collections import defaultdict
        per_month: dict[str, dict[str, float]] = defaultdict(
            lambda: {"deposits": 0.0, "withdrawals": 0.0, "dividends": 0.0, "interest": 0.0, "fees": 0.0}
        )
        for r in tx_rows:
            d: date = r["transaction_date"]
            month_key = f"{d.year:04d}-{d.month:02d}"
            try:
                amt = self.fx.convert(float(r["amount"] or 0), r["currency"], ccy)
            except Exception:
                amt = float(r["amount"] or 0)
            t = r["transaction_type"]
            bucket = {
                "deposit": "deposits", "withdraw": "withdrawals",
                "dividend": "dividends", "interest": "interest", "fee": "fees",
            }.get(t)
            if bucket:
                per_month[month_key][bucket] += amt

        # Snapshot navs grouped to month-end. Use the latest snapshot in each month.
        navs = self.nav_series(ccy, since=datetime.combine(since, datetime.min.time()) if since else None,
                               until=datetime.combine(until, datetime.max.time()) if until else None)
        nav_by_month: dict[str, float] = {}
        for p in navs:
            month_key = f"{p.when.year:04d}-{p.when.month:02d}"
            nav_by_month[month_key] = p.nav  # last write wins → end-of-month value

        all_months = sorted(set(per_month.keys()) | set(nav_by_month.keys()))
        out: list[CashFlowMonth] = []
        prev_nav: float | None = None
        for m in all_months:
            stats = per_month.get(m, {"deposits": 0.0, "withdrawals": 0.0, "dividends": 0.0, "interest": 0.0, "fees": 0.0})
            net_ext = stats["deposits"] - stats["withdrawals"]
            cur_nav = nav_by_month.get(m)
            market = 0.0
            if cur_nav is not None and prev_nav is not None:
                market = (cur_nav - prev_nav) - net_ext
            if cur_nav is not None:
                prev_nav = cur_nav
            out.append(CashFlowMonth(
                month=m,
                deposits=stats["deposits"],
                withdrawals=stats["withdrawals"],
                dividends=stats["dividends"],
                interest=stats["interest"],
                fees=stats["fees"],
                net_external=net_ext,
                market_pnl=market,
            ))
        return out


# ──────────────────────────────────────────────────────────────── XIRR solver
def _xnpv(rate: float, flows: list[tuple[date, float]]) -> float:
    t0 = flows[0][0]
    s = 0.0
    for d, cf in flows:
        days = (d - t0).days
        s += cf / ((1.0 + rate) ** (days / 365.0))
    return s


def _xnpv_deriv(rate: float, flows: list[tuple[date, float]]) -> float:
    t0 = flows[0][0]
    s = 0.0
    for d, cf in flows:
        days = (d - t0).days
        if days == 0:
            continue
        s += -cf * (days / 365.0) / ((1.0 + rate) ** (days / 365.0 + 1))
    return s


def _xirr_solve(flows: list[tuple[date, float]], guess: float = 0.10) -> float | None:
    """Newton-Raphson with bracketing fallback. Returns annualized rate or None."""
    if not flows:
        return None
    flows = sorted(flows, key=lambda x: x[0])
    rate = guess
    for _ in range(80):
        v = _xnpv(rate, flows)
        if abs(v) < 1e-7:
            return rate
        d = _xnpv_deriv(rate, flows)
        if d == 0:
            break
        new_rate = rate - v / d
        # keep rate above -0.999 (prevent (1+r)<=0)
        if new_rate <= -0.9999:
            new_rate = (rate - 0.9999) / 2
        if abs(new_rate - rate) < 1e-9:
            return new_rate
        rate = new_rate
    # fallback: bisection on a wide bracket
    lo, hi = -0.9999, 10.0
    flo, fhi = _xnpv(lo, flows), _xnpv(hi, flows)
    if flo * fhi > 0:
        return None
    for _ in range(120):
        mid = (lo + hi) / 2
        fm = _xnpv(mid, flows)
        if abs(fm) < 1e-7:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2
