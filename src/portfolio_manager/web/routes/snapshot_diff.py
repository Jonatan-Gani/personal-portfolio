from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/snapshots/{from_id}/diff/{to_id}")
def diff_page(request: Request, from_id: str, to_id: str, currency: str | None = None):
    c = request.app.state.container
    ccy = (currency or c.config.reporting.base_currency).upper()
    try:
        from_meta = c.snapshots_repo.get_meta(from_id)
        to_meta = c.snapshots_repo.get_meta(to_id)
    except Exception as e:
        raise HTTPException(404, str(e)) from e
    diff = c.snapshot_diff.diff(from_id, to_id, ccy)
    return request.app.state.templates.TemplateResponse(
        request,
        "snapshot_diff.html",
        {
            "request": request,
            "from_meta": from_meta,
            "to_meta": to_meta,
            "diff": diff,
            "ccy": ccy,
            "currencies": from_meta.reporting_currencies,
        },
    )
