from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ...domain.models import TargetAllocation
from ...repositories.targets import VALID_DIMENSIONS

router = APIRouter()


@router.get("/targets")
def targets_page(request: Request, dim: str = "asset_class"):
    c = request.app.state.container
    if dim not in VALID_DIMENSIONS:
        dim = "asset_class"
    base = c.config.reporting.base_currency
    report = c.drift.report(dim, base)
    all_targets = c.targets_repo.list_all()
    # Suggest buckets the user might want to set targets for: things present in the
    # latest snapshot for the chosen dimension.
    suggestions = sorted({(r.bucket or "(none)") for r in report.rows} - {t.bucket for t in c.targets_repo.list_by_dimension(dim)})
    return request.app.state.templates.TemplateResponse(
        request,
        "targets.html",
        {
            "request": request,
            "dim": dim,
            "dimensions": sorted(VALID_DIMENSIONS),
            "report": report,
            "all_targets": all_targets,
            "suggestions": suggestions,
        },
    )


@router.post("/targets")
def upsert_target(
    request: Request,
    dimension: str = Form(...),
    bucket: str = Form(...),
    target_weight_pct: float = Form(...),  # entered as percent for human-friendliness
    notes: str | None = Form(None),
):
    c = request.app.state.container
    t = TargetAllocation(
        dimension=dimension,
        bucket=bucket.strip(),
        target_weight=max(0.0, min(1.0, target_weight_pct / 100.0)),
        notes=notes,
    )
    c.targets_repo.upsert(t)
    return RedirectResponse(f"/targets?dim={dimension}", status_code=303)


@router.post("/targets/{target_id}/delete")
def delete_target(request: Request, target_id: str, dim: str = "asset_class"):
    c = request.app.state.container
    c.targets_repo.delete(target_id)
    return RedirectResponse(f"/targets?dim={dim}", status_code=303)
