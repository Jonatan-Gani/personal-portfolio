from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Request

from ..._clock import utcnow

router = APIRouter()
log = logging.getLogger(__name__)


def _maybe_auto_snapshot(c, force: bool) -> tuple[bool, str | None]:
    """Take a fresh snapshot if config says so and the latest is stale (or none exists).
    Returns (took_snapshot, reason)."""
    cfg = c.config.auto_snapshot
    if not (cfg.enabled or force):
        return False, None
    latest = c.snapshots_repo.latest()
    if latest is None:
        c.snapshot.take(notes="auto · first snapshot")
        return True, "first snapshot"
    age = utcnow() - latest.taken_at
    if force or age > timedelta(minutes=cfg.stale_after_minutes):
        c.snapshot.take(notes=f"auto · stale by {int(age.total_seconds() // 60)}m" if not force else "manual · refresh")
        return True, "stale"
    return False, None


@router.get("/")
def dashboard(request: Request, refresh: bool = False):
    c = request.app.state.container
    templates = request.app.state.templates

    auto_taken, _ = _maybe_auto_snapshot(c, force=refresh)

    latest = c.snapshots_repo.latest()
    base = c.config.reporting.base_currency

    summary = None
    by_class = []
    by_currency = []
    by_country = []
    by_kind = []

    if latest:
        summary = {
            "snapshot_id": latest.snapshot_id,
            "taken_at": latest.taken_at,
            "assets": latest.total_assets_base,
            "cash": latest.total_cash_base,
            "liabilities": latest.total_liabilities_base,
            "net_worth": latest.net_worth_base,
        }
        by_class    = c.exposure.by_dimension("asset_class",  base, latest.snapshot_id, kinds=["asset"])
        by_currency = c.exposure.by_dimension("currency",     base, latest.snapshot_id, kinds=["asset", "cash"])
        by_country  = c.exposure.by_dimension("country",      base, latest.snapshot_id, kinds=["asset", "cash"])
        by_kind     = c.exposure.by_dimension("position_kind", base, latest.snapshot_id)

    benchmarks = c.benchmarks.list_active()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "summary": summary,
            "by_class": by_class,
            "by_currency": by_currency,
            "by_country": by_country,
            "by_kind": by_kind,
            "benchmarks": benchmarks,
            "auto_taken": auto_taken,
            "stale_after_minutes": c.config.auto_snapshot.stale_after_minutes,
        },
    )
