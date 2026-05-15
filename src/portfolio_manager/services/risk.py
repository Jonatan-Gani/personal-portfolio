from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ..db.connection import Database
from .performance import PerformanceService


@dataclass
class RiskMetrics:
    n_periods: int
    period: str                          # 'monthly'
    annualized_volatility: float | None
    annualized_sharpe: float | None
    risk_free_rate: float                # decimal APR used
    beta: float | None
    alpha_annual: float | None           # CAPM intercept, annualized
    correlation: float | None
    benchmark_id: str | None
    benchmark_name: str | None


class RiskService:
    """Volatility, Sharpe, beta vs a chosen benchmark — all from monthly portfolio
    returns derived from snapshots, paired with monthly benchmark price returns
    from the price cache."""

    def __init__(self, db: Database, performance: PerformanceService):
        self.db = db
        self.perf = performance

    def metrics(
        self,
        report_currency: str,
        risk_free_rate: float = 0.0,
        benchmark_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> RiskMetrics:
        port_returns = self._monthly_portfolio_returns(report_currency, since, until)
        if len(port_returns) < 2:
            return RiskMetrics(
                n_periods=len(port_returns), period="monthly",
                annualized_volatility=None, annualized_sharpe=None,
                risk_free_rate=risk_free_rate,
                beta=None, alpha_annual=None, correlation=None,
                benchmark_id=benchmark_id, benchmark_name=None,
            )

        rf_monthly = (1 + risk_free_rate) ** (1.0 / 12) - 1.0
        excess = [r - rf_monthly for _, r in port_returns]
        vol_m = _stdev(excess)
        vol_a = vol_m * math.sqrt(12) if vol_m is not None else None
        mean_excess = sum(excess) / len(excess)
        sharpe_a = ((mean_excess * 12) / vol_a) if vol_a and vol_a > 0 else None

        beta = corr = alpha_annual = None
        bench_name = None
        if benchmark_id:
            bench_returns_map = self._monthly_benchmark_returns(benchmark_id, since, until)
            if bench_returns_map:
                bench_row = self.db.fetchone("SELECT name FROM benchmarks WHERE benchmark_id = ?", [benchmark_id])
                bench_name = bench_row[0] if bench_row else None
                pairs = [(r, bench_returns_map[m]) for m, r in port_returns if m in bench_returns_map]
                if len(pairs) >= 3:
                    p_ret = [p for p, _ in pairs]
                    b_ret = [b for _, b in pairs]
                    beta = _beta(p_ret, b_ret)
                    corr = _corr(p_ret, b_ret)
                    if beta is not None:
                        # alpha (monthly) = mean(p) - (rf_m + beta * (mean(b) - rf_m))
                        mean_p = sum(p_ret) / len(p_ret)
                        mean_b = sum(b_ret) / len(b_ret)
                        alpha_m = mean_p - (rf_monthly + beta * (mean_b - rf_monthly))
                        alpha_annual = (1 + alpha_m) ** 12 - 1.0

        return RiskMetrics(
            n_periods=len(port_returns), period="monthly",
            annualized_volatility=vol_a, annualized_sharpe=sharpe_a,
            risk_free_rate=risk_free_rate,
            beta=beta, alpha_annual=alpha_annual, correlation=corr,
            benchmark_id=benchmark_id, benchmark_name=bench_name,
        )

    def _monthly_portfolio_returns(
        self, ccy: str, since: datetime | None, until: datetime | None
    ) -> list[tuple[str, float]]:
        navs = self.perf.nav_series(ccy, since=since, until=until)
        if not navs:
            return []
        # last nav per (year, month)
        by_month: dict[str, float] = {}
        for p in navs:
            key = f"{p.when.year:04d}-{p.when.month:02d}"
            by_month[key] = p.nav
        ordered = sorted(by_month.items())
        out: list[tuple[str, float]] = []
        for i in range(1, len(ordered)):
            prev = ordered[i - 1][1]
            cur = ordered[i][1]
            if prev > 0:
                out.append((ordered[i][0], (cur - prev) / prev))
        return out

    def _monthly_benchmark_returns(
        self, benchmark_id: str, since: datetime | None, until: datetime | None
    ) -> dict[str, float]:
        bench = self.db.fetchone(
            "SELECT symbol FROM benchmarks WHERE benchmark_id = ?", [benchmark_id],
        )
        if not bench:
            return {}
        rows = self.db.fetchall(
            """
            SELECT price_date, price
              FROM price_cache
             WHERE symbol = ?
               AND (? IS NULL OR price_date >= ?)
               AND (? IS NULL OR price_date <= ?)
             ORDER BY price_date ASC
            """,
            [bench[0], since.date() if since else None, since.date() if since else None,
             until.date() if until else None, until.date() if until else None],
        )
        if not rows:
            return {}
        by_month: dict[str, float] = {}
        for d, p in rows:
            key = f"{d.year:04d}-{d.month:02d}"
            by_month[key] = float(p)  # last write wins → end-of-month
        ordered = sorted(by_month.items())
        out: dict[str, float] = {}
        for i in range(1, len(ordered)):
            prev = ordered[i - 1][1]
            cur = ordered[i][1]
            if prev > 0:
                out[ordered[i][0]] = (cur - prev) / prev
        return out


# ───────────────────────────────────────────────────── tiny numeric helpers
def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _corr(xs: list[float], ys: list[float]) -> float | None:
    sx = _stdev(xs)
    sy = _stdev(ys)
    cov = _cov(xs, ys)
    if cov is None or not sx or not sy:
        return None
    return cov / (sx * sy)


def _cov(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx, my = _mean(xs), _mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (len(xs) - 1)


def _beta(xs: list[float], ys: list[float]) -> float | None:
    cov = _cov(xs, ys)
    var_y = _cov(ys, ys)
    if cov is None or var_y in (None, 0):
        return None
    return cov / var_y


