from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/returns")
def returns(request: Request):
    """Compare/returns page: TWR, XIRR, drawdown, risk, monthly cash-flow attribution,
    and the multi-line compare chart. All visualisations are loaded client-side from
    /api/* so the page itself just supplies metadata for pickers."""
    c = request.app.state.container
    snaps = c.snapshots_repo.list_snapshots(limit=200)
    return request.app.state.templates.TemplateResponse(
        request,
        "returns.html",
        {
            "request": request,
            "assets": c.portfolio.list_assets(),
            "benchmarks": c.benchmarks.list_active(),
            "snapshots": snaps,
        },
    )
