from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...domain.exceptions import NotFoundError
from ...domain.models import ManualPriceOverride

router = APIRouter()


def _parse_dt(s: str) -> datetime:
    # accept either "YYYY-MM-DD" or full ISO datetime
    if len(s) == 10:
        return datetime.fromisoformat(s + "T00:00:00")
    return datetime.fromisoformat(s)


@router.post("/manual-prices")
def create_override(
    request: Request,
    asset_id: str = Form(...),
    observed_at: str = Form(...),
    price: float = Form(...),
    currency: str = Form(...),
    notes: str | None = Form(None),
    return_to: str = Form("/assets"),
):
    c = request.app.state.container
    override = ManualPriceOverride(
        asset_id=asset_id,
        observed_at=_parse_dt(observed_at),
        price=price,
        currency=currency,
        notes=notes,
    )
    c.manual_prices_repo.insert(override)
    if c.config.auto_snapshot.enabled:
        c.snapshot.take(notes="auto · after manual price update")
    return RedirectResponse(return_to, status_code=303)


@router.post("/manual-prices/{override_id}/delete")
def delete_override(request: Request, override_id: str, return_to: str = Form("/assets")):
    c = request.app.state.container
    try:
        c.manual_prices_repo.get(override_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    c.manual_prices_repo.delete(override_id)
    if c.config.auto_snapshot.enabled:
        c.snapshot.take(notes="auto · after manual price delete")
    return RedirectResponse(return_to, status_code=303)
