from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ...domain.exceptions import FXRateUnavailable, NotFoundError
from ...services.scope import parse_scope, scope_filter_sql, scope_join_sql

router = APIRouter(prefix="/api")


def _dc(obj):
    """Serialize dataclasses recursively to plain dicts (FastAPI handles dicts well)."""
    if is_dataclass(obj):
        return {k: _dc(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_dc(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dc(v) for k, v in obj.items()}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


_PERIOD_DELTAS: dict[str, timedelta | None] = {
    "1d": timedelta(days=1),
    "1w": timedelta(days=7),
    "1m": timedelta(days=31),
    "3m": timedelta(days=92),
    "6m": timedelta(days=183),
    "1y": timedelta(days=366),
    "5y": timedelta(days=366 * 5),
    "max": None,
}


def _resolve_window(period: str, start: str | None, end: str | None) -> tuple[date | None, date | None]:
    today = date.today()
    if period == "custom":
        s = date.fromisoformat(start) if start else None
        e = date.fromisoformat(end) if end else today
        return s, e
    if period == "ytd":
        return date(today.year, 1, 1), today
    if period == "max":
        return None, today
    delta = _PERIOD_DELTAS.get(period)
    if delta is None:
        return None, today
    return today - delta, today


def _rebase(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points:
        return points
    base = None
    for p in points:
        if p["y"] not in (None, 0):
            base = p["y"]
            break
    if base is None or base == 0:
        return points
    return [{"x": p["x"], "y": (p["y"] / base) * 100.0 if p["y"] is not None else None} for p in points]


@router.get("/timeseries/networth")
def networth_timeseries(request: Request, currency: str | None = None, scope: str = "all"):
    """Return [{taken_at, snapshot_id, net_worth, assets, cash, liabilities}] in `currency`,
    valued at the FX rates each snapshot recorded. Currency defaults to base.

    `scope` filters by account/group ('all' | 'unassigned' | 'group:<id>' | 'account:<id>').
    Scope filtering uses each holding's CURRENT account assignment, applied to every
    historical snapshot — this is intentional, so you can look at "this account's
    history" even if you only assigned the account later."""
    c = request.app.state.container
    ccy = (currency or c.config.reporting.base_currency).upper()
    db = c.db
    _, account_ids, _ = parse_scope(scope, c)
    scope_where, scope_params = scope_filter_sql(account_ids)
    joins = scope_join_sql() if account_ids is not None else ""
    rows = db.fetchall_dict(
        f"""
        SELECT s.snapshot_id, s.taken_at, s.notes, s.base_currency,
               COALESCE(SUM(CASE WHEN p.position_kind = 'asset'     THEN v.value ELSE 0 END), 0) AS assets,
               COALESCE(SUM(CASE WHEN p.position_kind = 'cash'      THEN v.value ELSE 0 END), 0) AS cash,
               COALESCE(SUM(CASE WHEN p.position_kind = 'liability' THEN v.value ELSE 0 END), 0) AS liabilities
          FROM snapshots s
          LEFT JOIN snapshot_positions p
            ON p.snapshot_id = s.snapshot_id
          LEFT JOIN snapshot_position_values v
            ON v.snapshot_id = p.snapshot_id
           AND v.position_kind = p.position_kind
           AND v.entity_id = p.entity_id
           AND v.currency = ?
          {joins}
         WHERE 1=1
           {scope_where}
         GROUP BY s.snapshot_id, s.taken_at, s.notes, s.base_currency
         ORDER BY s.taken_at ASC
        """,
        [ccy, *scope_params],
    )
    series = [
        {
            "snapshot_id": r["snapshot_id"],
            "taken_at": r["taken_at"].isoformat() if r["taken_at"] else None,
            "notes": r["notes"],
            "assets": float(r["assets"] or 0),
            "cash": float(r["cash"] or 0),
            "liabilities": float(r["liabilities"] or 0),
            "net_worth": float((r["assets"] or 0) + (r["cash"] or 0) - (r["liabilities"] or 0)),
        }
        for r in rows
    ]
    return {"currency": ccy, "points": series}


@router.get("/timeseries/benchmark/{benchmark_id}")
def benchmark_timeseries(request: Request, benchmark_id: str):
    c = request.app.state.container
    try:
        b = c.benchmarks.get(benchmark_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    history = c.benchmarks.history(b)
    return {
        "benchmark_id": b.benchmark_id,
        "name": b.name,
        "symbol": b.symbol,
        "currency": b.currency,
        "points": [
            {"date": d.isoformat(), "price": float(p), "currency": ccy}
            for (d, p, ccy) in history
        ],
    }


@router.get("/allocation/latest")
def latest_allocation(
    request: Request,
    dim: str = "asset_class",
    currency: str | None = None,
    kind: str | None = None,
    kinds: str | None = None,
    scope: str = "all",
):
    """Allocation breakdown for the most recent snapshot.

    `kind` is a single kind ("asset"|"cash"|"liability"). `kinds` is a comma-separated
    list — e.g. "asset,cash". If neither is provided, only non-liability positions
    are included so the chart isn't dominated by debt.

    `scope` filters by account/group (see /api/timeseries/networth).
    """
    c = request.app.state.container
    latest = c.snapshots_repo.latest()
    if latest is None:
        return {"snapshot_id": None, "dim": dim, "currency": None, "rows": []}
    ccy = (currency or c.config.reporting.base_currency).upper()

    valid = {"asset", "cash", "liability"}
    selected: list[str] | None
    if kinds:
        selected = [k for k in (s.strip() for s in kinds.split(",")) if k in valid] or None
    elif kind in valid:
        selected = [kind]
    else:
        selected = ["asset", "cash"]  # default: don't include liabilities

    _, account_ids, _ = parse_scope(scope, c)

    if dim == "tag":
        raw = c.exposure.by_tag(ccy, latest.snapshot_id, account_ids=account_ids)
        rows = [
            {"label": r["tag"], "value": float(r["value"]), "share": float(r["share"]), "positions": int(r["positions"])}
            for r in raw
        ]
    else:
        raw = c.exposure.by_dimension(dim, ccy, latest.snapshot_id, selected, account_ids=account_ids)
        rows = [
            {"label": r["bucket"], "value": float(r["value"]), "share": float(r["share"]), "positions": int(r["positions"])}
            for r in raw
        ]
    return {"snapshot_id": latest.snapshot_id, "dim": dim, "currency": ccy, "rows": rows, "kinds": selected}


# ----------------------------------------------------------------- /api/series
# Returns multi-series time data for the comparison chart. A series id is one of:
#   "portfolio"           — total net worth from the snapshots table
#   "asset:<asset_id>"    — that asset's snapshot-time value
#   "benchmark:<id>"      — daily prices from price_cache for that benchmark's symbol
#
# Currency rules:
#   - if currency=="native" (or "_native_"), no FX projection happens. Portfolio uses
#     the snapshot's base currency; assets use their value_local; benchmarks use their
#     own currency. Useful with rebase to compare %-moves across currencies cleanly.
#   - otherwise the target currency is applied. Snapshot positions already carry
#     pre-computed values in every reporting currency. Benchmarks are scaled by the
#     current FX rate (good enough for relative comparisons; for absolute amounts
#     in mixed currencies, prefer rebase=true).

def _portfolio_series(c, ccy: str, since: date | None, until: date | None, native: bool) -> dict:
    if native:
        rows = c.db.fetchall(
            """
            SELECT snapshot_id, taken_at, net_worth_base, base_currency
              FROM snapshots
             WHERE (? IS NULL OR taken_at >= ?)
               AND (? IS NULL OR taken_at <= ?)
             ORDER BY taken_at ASC
            """,
            [since, since, until, until],
        )
        if not rows:
            return {"id": "portfolio", "name": "Portfolio (net worth)", "currency": None, "points": []}
        return {
            "id": "portfolio",
            "name": "Portfolio (net worth)",
            "currency": rows[0][3],
            "points": [{"x": r[1].isoformat(), "y": float(r[2])} for r in rows],
        }

    rows = c.db.fetchall(
        """
        SELECT s.snapshot_id, s.taken_at,
               COALESCE(SUM(CASE WHEN p.position_kind = 'asset'     THEN v.value ELSE 0 END), 0)
             + COALESCE(SUM(CASE WHEN p.position_kind = 'cash'      THEN v.value ELSE 0 END), 0)
             - COALESCE(SUM(CASE WHEN p.position_kind = 'liability' THEN v.value ELSE 0 END), 0) AS net
          FROM snapshots s
          LEFT JOIN snapshot_positions p ON p.snapshot_id = s.snapshot_id
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
    return {
        "id": "portfolio",
        "name": "Portfolio (net worth)",
        "currency": ccy,
        "points": [{"x": r[1].isoformat(), "y": float(r[2] or 0)} for r in rows],
    }


def _asset_series(c, asset_id: str, ccy: str, since: date | None, until: date | None, native: bool) -> dict:
    asset = c.portfolio.assets.get(asset_id)
    if native:
        rows = c.db.fetchall(
            """
            SELECT s.taken_at, p.value_local
              FROM snapshots s
              JOIN snapshot_positions p
                ON p.snapshot_id = s.snapshot_id
               AND p.entity_id = ?
               AND p.position_kind = 'asset'
             WHERE (? IS NULL OR s.taken_at >= ?)
               AND (? IS NULL OR s.taken_at <= ?)
             ORDER BY s.taken_at ASC
            """,
            [asset_id, since, since, until, until],
        )
        return {
            "id": f"asset:{asset_id}",
            "name": f"{asset.name} ({asset.symbol or asset.currency})",
            "currency": asset.currency,
            "points": [{"x": r[0].isoformat(), "y": float(r[1])} for r in rows],
        }

    rows = c.db.fetchall(
        """
        SELECT s.taken_at, v.value
          FROM snapshots s
          JOIN snapshot_position_values v
            ON v.snapshot_id = s.snapshot_id
           AND v.entity_id = ?
           AND v.position_kind = 'asset'
           AND v.currency = ?
         WHERE (? IS NULL OR s.taken_at >= ?)
           AND (? IS NULL OR s.taken_at <= ?)
         ORDER BY s.taken_at ASC
        """,
        [asset_id, ccy, since, since, until, until],
    )
    return {
        "id": f"asset:{asset_id}",
        "name": f"{asset.name} ({asset.symbol or asset.currency})",
        "currency": ccy,
        "points": [{"x": r[0].isoformat(), "y": float(r[1])} for r in rows],
    }


def _benchmark_series(c, benchmark_id: str, ccy: str, since: date | None, until: date | None, native: bool) -> dict:
    b = c.benchmarks.get(benchmark_id)
    rows = c.price_cache.history(b.symbol, since=since, until=until)
    # Each row is (date, price, currency_at_observation). Use observation currency
    # as the native one. If target ccy differs, scale by current FX rate.
    points: list[dict] = []
    if native or not rows:
        points = [{"x": d.isoformat(), "y": float(p)} for (d, p, _ccy) in rows]
        return {
            "id": f"benchmark:{benchmark_id}",
            "name": b.name,
            "currency": b.currency,
            "points": points,
        }

    obs_ccy = rows[0][2] or b.currency
    try:
        rate = c.fx.rate(obs_ccy, ccy) if obs_ccy.upper() != ccy.upper() else 1.0
    except FXRateUnavailable:
        rate = 1.0
    points = [{"x": d.isoformat(), "y": float(p) * rate} for (d, p, _ccy) in rows]
    return {
        "id": f"benchmark:{benchmark_id}",
        "name": b.name,
        "currency": ccy,
        "points": points,
    }


@router.get("/series")
def series_endpoint(
    request: Request,
    series: list[str] = Query(default=[]),
    currency: str = "USD",
    period: str = "1y",
    start: str | None = None,
    end: str | None = None,
    rebase: bool = False,
):
    c = request.app.state.container
    native = currency.lower() in ("native", "_native_")
    ccy = c.config.reporting.base_currency if native else currency.upper()
    since, until = _resolve_window(period, start, end)
    until_dt = datetime.combine(until or date.today(), datetime.max.time())
    since_dt = datetime.combine(since, datetime.min.time()) if since else None

    out_series: list[dict[str, Any]] = []
    errors: list[str] = []

    for sid in series:
        try:
            if sid == "portfolio":
                s = _portfolio_series(c, ccy, since_dt, until_dt, native)
            elif sid.startswith("asset:"):
                s = _asset_series(c, sid.split(":", 1)[1], ccy, since_dt, until_dt, native)
            elif sid.startswith("benchmark:"):
                s = _benchmark_series(c, sid.split(":", 1)[1], ccy, since, until, native)
            else:
                errors.append(f"unknown series id {sid!r}")
                continue
            if rebase:
                s["points"] = _rebase(s["points"])
                s["rebased"] = True
            out_series.append(s)
        except NotFoundError as e:
            errors.append(str(e))
        except Exception as e:
            errors.append(f"{sid}: {e!s}")

    return {
        "currency": "native" if native else ccy,
        "period": period,
        "from": since.isoformat() if since else None,
        "to": (until or date.today()).isoformat(),
        "rebase": rebase,
        "series": out_series,
        "errors": errors,
    }


# ──────────────────────────────────────────────────────────── /api/performance
@router.get("/performance")
def performance(
    request: Request,
    currency: str | None = None,
    period: str = "1y",
    start: str | None = None,
    end: str | None = None,
    risk_free: float = 0.0,
    benchmark_id: str | None = None,
):
    """Return TWR, XIRR, drawdown stats, and risk metrics for the requested
    window — the high-level numbers that headline the /performance page."""
    c = request.app.state.container
    ccy = (currency or c.config.reporting.base_currency).upper()
    since, until = _resolve_window(period, start, end)
    since_dt = datetime.combine(since, datetime.min.time()) if since else None
    until_dt = datetime.combine(until or date.today(), datetime.max.time())

    twr = c.performance.twr(ccy, since=since_dt, until=until_dt)
    xirr = c.performance.xirr(ccy, since=since_dt, until=until_dt)
    dd = c.performance.drawdown_series(ccy, since=since_dt, until=until_dt)
    risk = c.risk.metrics(ccy, risk_free_rate=risk_free, benchmark_id=benchmark_id,
                          since=since_dt, until=until_dt)
    return {
        "currency": ccy,
        "period": period,
        "from": since.isoformat() if since else None,
        "to": (until or date.today()).isoformat(),
        "twr": twr,
        "xirr": xirr,
        "drawdown": {
            "max_drawdown": dd["max_drawdown"],
            "max_drawdown_at": dd["max_drawdown_at"],
            "current_drawdown": dd["current_drawdown"],
        },
        "risk": _dc(risk),
    }


@router.get("/timeseries/drawdown")
def drawdown_timeseries(
    request: Request,
    currency: str | None = None,
    period: str = "max",
    start: str | None = None,
    end: str | None = None,
):
    c = request.app.state.container
    ccy = (currency or c.config.reporting.base_currency).upper()
    since, until = _resolve_window(period, start, end)
    since_dt = datetime.combine(since, datetime.min.time()) if since else None
    until_dt = datetime.combine(until or date.today(), datetime.max.time())
    out = c.performance.drawdown_series(ccy, since=since_dt, until=until_dt)
    out["currency"] = ccy
    return out


@router.get("/cashflow/monthly")
def monthly_cashflow(
    request: Request,
    currency: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    c = request.app.state.container
    ccy = (currency or c.config.reporting.base_currency).upper()
    s = date.fromisoformat(since) if since else None
    u = date.fromisoformat(until) if until else None
    rows = c.performance.monthly_attribution(ccy, since=s, until=u)
    return {"currency": ccy, "rows": [_dc(r) for r in rows]}


# ──────────────────────────────────────────────────────────── /api/snapshot-diff
@router.get("/snapshots/{from_id}/diff/{to_id}")
def snapshot_diff(
    request: Request,
    from_id: str,
    to_id: str,
    currency: str | None = None,
):
    c = request.app.state.container
    ccy = (currency or c.config.reporting.base_currency).upper()
    diff = c.snapshot_diff.diff(from_id, to_id, ccy)
    return _dc(diff)


# ──────────────────────────────────────────────────────────── /api/exposures (treemap)
@router.get("/exposures/hierarchical")
def hierarchical_exposure(
    request: Request,
    currency: str | None = None,
    snapshot_id: str | None = None,
    kinds: str | None = None,
):
    """Two-level hierarchy: asset_class → individual position. Returns rows shaped
    for Chart.js treemap (groups + values)."""
    c = request.app.state.container
    ccy = (currency or c.config.reporting.base_currency).upper()
    snap_id = snapshot_id or (c.snapshots_repo.latest().snapshot_id if c.snapshots_repo.latest() else None)
    if not snap_id:
        return {"snapshot_id": None, "rows": []}
    valid = {"asset", "cash", "liability"}
    selected: list[str]
    if kinds:
        selected = [k for k in (s.strip() for s in kinds.split(",")) if k in valid] or ["asset", "cash"]
    else:
        selected = ["asset", "cash"]
    placeholders = ",".join(["?"] * len(selected))
    rows = c.db.fetchall_dict(
        f"""
        SELECT COALESCE(p.asset_class, p.position_kind) AS group_key,
               p.name,
               p.position_kind,
               p.entity_id,
               COALESCE(v.value, 0) AS value
          FROM snapshot_positions p
          JOIN snapshot_position_values v
            ON v.snapshot_id = p.snapshot_id
           AND v.position_kind = p.position_kind
           AND v.entity_id = p.entity_id
         WHERE p.snapshot_id = ?
           AND v.currency = ?
           AND p.position_kind IN ({placeholders})
         ORDER BY value DESC
        """,
        [snap_id, ccy, *selected],
    )
    return {
        "snapshot_id": snap_id,
        "currency": ccy,
        "rows": [
            {
                "group": r["group_key"] or "(none)",
                "name": r["name"] or "(unnamed)",
                "kind": r["position_kind"],
                "entity_id": r["entity_id"],
                "value": float(r["value"] or 0),
            }
            for r in rows if (r["value"] or 0) > 0
        ],
    }


# ──────────────────────────────────────────────────────────── /api/holdings/positions
@router.get("/holdings/positions")
def holdings_positions(request: Request, currency: str | None = None):
    """Per-position cost basis + unrealized + realized P&L, in report currency.
    Used by the holdings page to show a richer table than just quantity."""
    c = request.app.state.container
    base = c.config.reporting.base_currency.upper()
    ccy = (currency or base).upper()
    latest = c.snapshots_repo.latest()
    snap_id = latest.snapshot_id if latest else None
    out = []
    for asset in c.portfolio.list_assets(include_inactive=True):
        cb = c.cost_basis.compute(asset.asset_id)
        # current value in report currency from latest snapshot
        cur_val_report = 0.0
        cur_price_local = None
        if snap_id:
            row = c.db.fetchone(
                """
                SELECT p.price_local, COALESCE(v.value, 0)
                  FROM snapshot_positions p
                  LEFT JOIN snapshot_position_values v
                    ON v.snapshot_id = p.snapshot_id
                   AND v.position_kind = p.position_kind
                   AND v.entity_id = p.entity_id
                   AND v.currency = ?
                 WHERE p.snapshot_id = ? AND p.entity_id = ? AND p.position_kind = 'asset'
                """,
                [ccy, snap_id, asset.asset_id],
            )
            if row:
                cur_price_local = float(row[0]) if row[0] is not None else None
                cur_val_report = float(row[1] or 0)
        # cost basis in asset currency → convert to report ccy
        try:
            cost_in_report = c.fx.convert(cb.total_cost_basis, asset.currency, ccy)
            realized_in_report = c.fx.convert(cb.realized_pnl, asset.currency, ccy)
        except Exception:
            cost_in_report = cb.total_cost_basis
            realized_in_report = cb.realized_pnl
        unrealized_report = cur_val_report - cost_in_report
        unrealized_pct = (unrealized_report / cost_in_report) if cost_in_report else None

        # Currency attribution (always in base currency): split the unrealized
        # return into the price move vs the FX move, using the rate pinned on
        # each purchase rather than today's rate.
        attribution = None
        if cur_price_local is not None:
            try:
                fx_now = c.fx.rate(asset.currency, base)
                attribution = c.cost_basis.attribute_currency(
                    asset.asset_id, cur_price_local, fx_now
                )
            except Exception:
                attribution = None

        out.append({
            "asset_id": asset.asset_id,
            "name": asset.name,
            "symbol": asset.symbol,
            "currency": asset.currency,
            "is_active": asset.is_active,
            "quantity": cb.quantity,
            "avg_cost_local": cb.avg_cost,
            "current_price_local": cur_price_local,
            "cost_basis_report": cost_in_report,
            "current_value_report": cur_val_report,
            "unrealized_report": unrealized_report,
            "unrealized_pct": unrealized_pct,
            "realized_report": realized_in_report,
            "incomplete_cost_basis": cb.incomplete_cost_basis,
            # Base-currency figures from FX rates pinned at purchase.
            "base_currency": base,
            "cost_basis_base": cb.total_cost_basis_base,
            "incomplete_fx": cb.incomplete_fx,
            "unrealized_base": attribution.unrealized_base if attribution else None,
            "price_effect_base": attribution.price_effect_base if attribution else None,
            "fx_effect_base": attribution.fx_effect_base if attribution else None,
        })
    out.sort(key=lambda r: r["current_value_report"], reverse=True)
    return {"currency": ccy, "snapshot_id": snap_id, "rows": out}


# ──────────────────────────────────────────────────────── /api/holdings/return-split
@router.get("/holdings/return-split")
def holdings_return_split(request: Request):
    """Per-holding gain split into currency / market / sector / pick, in the
    base currency, from each lot's frozen inception values to live prices."""
    c = request.app.state.container
    splits = c.return_split.for_portfolio()
    rows = [
        {
            "asset_id": s.asset_id, "symbol": s.symbol, "quantity": s.quantity,
            "total": s.total, "currency": s.currency, "market": s.market,
            "sector": s.sector, "pick": s.pick, "complete": s.complete,
        }
        for s in splits
    ]
    return {"base_currency": c.config.reporting.base_currency, "rows": rows}


# ──────────────────────────────────────────────────────────── /api/holdings/lots
@router.get("/holdings/lots/{asset_id}")
def holdings_lots(request: Request, asset_id: str):
    """Open FIFO lots and realized-sale events for one asset — the cost-basis
    detail behind the holdings/attribution figures."""
    c = request.app.state.container
    cb = c.cost_basis.compute(asset_id)
    return {
        "asset_id": asset_id,
        "realized_pnl": cb.realized_pnl,
        "open_lots": [
            {
                "acquired": lot.acquired.isoformat(),
                "qty": lot.qty,
                "unit_cost": lot.unit_cost,
                "cost_known": lot.cost_known,
                "cost_local": lot.qty * lot.unit_cost,
                "fx_to_base": lot.fx_to_base,
                "cost_base": (
                    lot.qty * lot.unit_cost * lot.fx_to_base
                    if lot.fx_to_base is not None else None
                ),
            }
            for lot in cb.open_lots
        ],
        "realized_events": [
            {
                "sold_at": ev.sold_at.isoformat(),
                "qty": ev.qty,
                "proceeds": ev.proceeds,
                "cost_basis_consumed": ev.cost_basis_consumed,
                "fees": ev.fees,
                "pnl": ev.pnl,
            }
            for ev in cb.realized_events
        ],
    }
