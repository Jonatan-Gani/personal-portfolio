from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/snapshots")
def list_snapshots(request: Request):
    c = request.app.state.container
    snaps = c.snapshots_repo.list_snapshots(limit=200)
    # Each row's "compare to previous" link points at the snapshot taken just
    # before it (chronologically). The list is descending, so the next row
    # is the prior snapshot.
    rows = []
    for idx, s in enumerate(snaps):
        prev_id = snaps[idx + 1].snapshot_id if idx + 1 < len(snaps) else None
        rows.append({"meta": s, "prev_id": prev_id})
    return request.app.state.templates.TemplateResponse(
        request,
        "snapshots.html",
        {"request": request, "rows": rows},
    )


@router.post("/snapshots")
def create_snapshot(request: Request, notes: str | None = Form(None)):
    c = request.app.state.container
    meta = c.snapshot.take(notes=notes)
    return RedirectResponse(f"/snapshots/{meta.snapshot_id}", status_code=303)


@router.get("/snapshots/{snapshot_id}")
def snapshot_detail(request: Request, snapshot_id: str, currency: str | None = None):
    c = request.app.state.container
    try:
        meta = c.snapshots_repo.get_meta(snapshot_id)
    except Exception as e:
        raise HTTPException(404, str(e)) from e
    show_ccy = (currency or c.config.reporting.base_currency).upper()
    positions = c.snapshots_repo.positions_with_values(snapshot_id)

    # Find the immediately preceding snapshot for the "compare to previous" link.
    prev = c.db.fetchone(
        """
        SELECT snapshot_id FROM snapshots
         WHERE taken_at < (SELECT taken_at FROM snapshots WHERE snapshot_id = ?)
         ORDER BY taken_at DESC LIMIT 1
        """,
        [snapshot_id],
    )
    prev_id = prev[0] if prev else None
    return request.app.state.templates.TemplateResponse(
        request,
        "snapshot_detail.html",
        {
            "request": request,
            "meta": meta,
            "positions": positions,
            "show_currency": show_ccy,
            "currencies": meta.reporting_currencies,
            "prev_snapshot_id": prev_id,
        },
    )
