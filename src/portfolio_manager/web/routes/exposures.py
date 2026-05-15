from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/exposures")
def exposures(
    request: Request,
    snapshot_id: str | None = None,
    currency: str | None = None,
    kind: str | None = None,
):
    c = request.app.state.container
    snaps = c.snapshots_repo.list_snapshots(limit=200)
    selected_snap_id = snapshot_id or (snaps[0].snapshot_id if snaps else None)
    show_ccy = (currency or c.config.reporting.base_currency).upper()
    kinds = [kind] if kind in ("asset", "liability", "cash") else None

    rows = {}
    if selected_snap_id:
        for dim in ("asset_class", "instrument_type", "currency", "country", "sector", "position_kind"):
            rows[dim] = c.exposure.by_dimension(dim, show_ccy, selected_snap_id, kinds)
        rows["tag"] = c.exposure.by_tag(show_ccy, selected_snap_id)

    return request.app.state.templates.TemplateResponse(
        request,
        "exposures.html",
        {
            "request": request,
            "snapshots": snaps,
            "selected_snapshot_id": selected_snap_id,
            "currency": show_ccy,
            "kind": kind,
            "rows": rows,
        },
    )
