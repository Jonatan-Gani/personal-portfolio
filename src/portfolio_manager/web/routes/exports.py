from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/export")


def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    if rows:
        # union of keys across rows in stable insertion order
        cols: list[str] = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k); cols.append(k)
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _stringify(r.get(k)) for k in cols})
    buf.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers=headers)


def _stringify(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        # tabular CSV doesn't represent dicts well — emit a JSON-like compact form
        return "{" + "; ".join(f"{k}={vv}" for k, vv in v.items()) + "}"
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    return v


def _query_dicts(db, sql: str, params=None) -> list[dict]:
    return db.fetchall_dict(sql, params or [])


@router.get("/assets.csv")
def export_assets(request: Request):
    c = request.app.state.container
    rows = _query_dicts(c.db, "SELECT * FROM assets ORDER BY is_active DESC, name")
    return _csv_response(rows, "assets.csv")


@router.get("/cash.csv")
def export_cash(request: Request):
    c = request.app.state.container
    rows = _query_dicts(c.db, "SELECT * FROM cash_holdings ORDER BY is_active DESC, account_name")
    return _csv_response(rows, "cash.csv")


@router.get("/liabilities.csv")
def export_liabilities(request: Request):
    c = request.app.state.container
    rows = _query_dicts(c.db, "SELECT * FROM liabilities ORDER BY is_active DESC, name")
    return _csv_response(rows, "liabilities.csv")


@router.get("/transactions.csv")
def export_transactions(request: Request):
    c = request.app.state.container
    rows = _query_dicts(
        c.db,
        "SELECT * FROM transactions ORDER BY transaction_date DESC, created_at DESC",
    )
    return _csv_response(rows, "transactions.csv")


@router.get("/benchmarks.csv")
def export_benchmarks(request: Request):
    c = request.app.state.container
    rows = _query_dicts(c.db, "SELECT * FROM benchmarks ORDER BY is_active DESC, name")
    return _csv_response(rows, "benchmarks.csv")


@router.get("/snapshots.csv")
def export_snapshots(request: Request):
    c = request.app.state.container
    rows = _query_dicts(c.db, "SELECT * FROM snapshots ORDER BY taken_at ASC")
    return _csv_response(rows, "snapshots.csv")


@router.get("/manual-prices.csv")
def export_manual_prices(request: Request):
    c = request.app.state.container
    rows = _query_dicts(c.db, "SELECT * FROM manual_price_overrides ORDER BY observed_at ASC")
    return _csv_response(rows, "manual_prices.csv")


@router.get("/snapshot/{snapshot_id}/positions.csv")
def export_snapshot_positions(request: Request, snapshot_id: str):
    c = request.app.state.container
    positions = c.snapshots_repo.positions_with_values(snapshot_id)
    if not positions:
        raise HTTPException(404, f"snapshot {snapshot_id!r} has no positions or doesn't exist")
    return _csv_response(positions, f"snapshot-{snapshot_id[:8]}-positions.csv")


@router.get("/benchmark/{benchmark_id}/history.csv")
def export_benchmark_history(request: Request, benchmark_id: str):
    c = request.app.state.container
    b = c.benchmarks.get(benchmark_id)
    history = c.benchmarks.history(b)
    rows = [{"date": d.isoformat(), "price": p, "currency": ccy} for (d, p, ccy) in history]
    return _csv_response(rows, f"benchmark-{b.symbol.replace('^','')}-history.csv")


@router.get("/all.zip")
def export_all_zip(request: Request):
    """One-shot ZIP of every table as CSV, plus per-snapshot positions and benchmark histories."""
    c = request.app.state.container
    buf = io.BytesIO()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, sql in [
            ("assets.csv",        "SELECT * FROM assets"),
            ("cash.csv",          "SELECT * FROM cash_holdings"),
            ("liabilities.csv",   "SELECT * FROM liabilities"),
            ("transactions.csv",  "SELECT * FROM transactions ORDER BY transaction_date, created_at"),
            ("benchmarks.csv",    "SELECT * FROM benchmarks"),
            ("snapshots.csv",     "SELECT * FROM snapshots ORDER BY taken_at"),
            ("snapshot_positions.csv",        "SELECT * FROM snapshot_positions"),
            ("snapshot_position_values.csv",  "SELECT * FROM snapshot_position_values"),
            ("manual_prices.csv", "SELECT * FROM manual_price_overrides ORDER BY observed_at"),
            ("price_cache.csv",   "SELECT * FROM price_cache ORDER BY symbol, price_date"),
            ("fx_rates_cache.csv","SELECT * FROM fx_rates_cache ORDER BY rate_date"),
        ]:
            try:
                rows = _query_dicts(c.db, sql)
            except Exception:  # noqa: BLE001 — table may not exist on older DBs
                continue
            csv_buf = io.StringIO()
            if rows:
                cols, seen = [], set()
                for r in rows:
                    for k in r:
                        if k not in seen:
                            seen.add(k); cols.append(k)
                w = csv.DictWriter(csv_buf, fieldnames=cols)
                w.writeheader()
                for r in rows:
                    w.writerow({k: _stringify(r.get(k)) for k in cols})
            zf.writestr(name, csv_buf.getvalue())
    buf.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="portfolio-export-{ts}.zip"'}
    return StreamingResponse(iter([buf.getvalue()]), media_type="application/zip", headers=headers)
