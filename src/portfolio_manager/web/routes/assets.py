from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...domain.enums import AssetClass, InstrumentType
from ...domain.exceptions import NotFoundError
from ...domain.models import Asset

router = APIRouter()


@router.get("/assets")
def list_assets(request: Request):
    c = request.app.state.container
    items = c.portfolio.list_assets(include_inactive=True)
    state = c.holdings.at()
    overrides = {
        a.asset_id: c.manual_prices_repo.list_for_asset(a.asset_id) for a in items
    }
    return request.app.state.templates.TemplateResponse(
        request,
        "assets.html",
        {
            "request": request,
            "items": items,
            "quantities": state.asset_quantities,
            "overrides": overrides,
            "instrument_types": [t.value for t in InstrumentType],
            "asset_classes": [t.value for t in AssetClass],
        },
    )


@router.post("/assets")
def create_asset(
    request: Request,
    name: str = Form(...),
    symbol: str | None = Form(None),
    instrument_type: str = Form(...),
    asset_class: str = Form(...),
    currency: str = Form(...),
    country: str | None = Form(None),
    sector: str | None = Form(None),
    price_provider: str | None = Form(None),
    notes: str | None = Form(None),
    tags: str | None = Form(None),
):
    c = request.app.state.container
    asset = Asset(
        name=name,
        symbol=symbol or None,
        instrument_type=InstrumentType(instrument_type),
        asset_class=AssetClass(asset_class),
        currency=currency,
        country=country or None,
        sector=sector or None,
        price_provider=price_provider or None,
        notes=notes,
        tags=[t.strip() for t in (tags or "").split(",") if t.strip()],
    )
    c.portfolio.add_asset(asset)
    return RedirectResponse("/assets", status_code=303)


@router.post("/assets/{asset_id}/update")
def update_asset(
    request: Request,
    asset_id: str,
    name: str = Form(...),
    symbol: str | None = Form(None),
    instrument_type: str = Form(...),
    asset_class: str = Form(...),
    currency: str = Form(...),
    country: str | None = Form(None),
    sector: str | None = Form(None),
    price_provider: str | None = Form(None),
    notes: str | None = Form(None),
    tags: str | None = Form(None),
):
    c = request.app.state.container
    try:
        existing = c.portfolio.assets.get(asset_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    existing.name = name
    existing.symbol = symbol or None
    existing.instrument_type = InstrumentType(instrument_type)
    existing.asset_class = AssetClass(asset_class)
    existing.currency = currency
    existing.country = country or None
    existing.sector = sector or None
    existing.price_provider = price_provider or None
    existing.notes = notes
    existing.tags = [t.strip() for t in (tags or "").split(",") if t.strip()]
    c.portfolio.update_asset(existing)
    return RedirectResponse("/assets", status_code=303)


@router.post("/assets/{asset_id}/delete")
def delete_asset(request: Request, asset_id: str, hard: bool = False):
    c = request.app.state.container
    c.portfolio.remove_asset(asset_id, hard=hard)
    return RedirectResponse("/assets", status_code=303)


@router.post("/assets/{asset_id}/reactivate")
def reactivate_asset(request: Request, asset_id: str):
    c = request.app.state.container
    a = c.portfolio.assets.get(asset_id)
    a.is_active = True
    c.portfolio.update_asset(a)
    return RedirectResponse("/assets", status_code=303)
