from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ...domain.enums import AssetClass, InstrumentType
from ...domain.exceptions import NotFoundError
from ...domain.models import Asset
from ...services.asset_lookup import is_valid_isin_format

router = APIRouter()


@router.get("/api/asset-lookup")
def asset_lookup(request: Request, symbol: str | None = None, isin: str | None = None):
    """Verify a ticker/ISIN against the external lookup provider and return
    enriched metadata. Always returns 200 — callers should branch on `ok`."""
    c = request.app.state.container
    result = c.asset_lookup.lookup(symbol=symbol, isin=isin)
    return JSONResponse(result.to_dict())


@router.get("/api/isin-check")
def isin_check(isin: str):
    """Cheap structural ISIN validity check (no network)."""
    return {"isin": isin.strip().upper(), "valid": is_valid_isin_format(isin)}


@router.get("/api/markets/watchlist")
def markets_watchlist(request: Request):
    """Current + prior-close prices for the dashboard market widget.
    Uses the configured watchlist from app_settings if set, else built-in defaults."""
    c = request.app.state.container
    custom = c.app_settings_repo.get("ui.market_watchlist")
    if isinstance(custom, list) and custom:
        quotes = c.markets.watchlist(custom)
    else:
        quotes = c.markets.watchlist()
    return {"quotes": [q.to_dict() for q in quotes]}


@router.get("/assets")
def list_assets(request: Request):
    c = request.app.state.container
    items = c.portfolio.list_assets(include_inactive=True)
    state = c.holdings.at()
    overrides = {
        a.asset_id: c.manual_prices_repo.list_for_asset(a.asset_id) for a in items
    }
    accounts = c.accounts_repo.list_active()
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
            "accounts": accounts,
        },
    )


@router.post("/assets")
def create_asset(
    request: Request,
    name: str = Form(...),
    symbol: str | None = Form(None),
    isin: str | None = Form(None),
    instrument_type: str = Form(...),
    asset_class: str = Form(...),
    currency: str = Form(...),
    country: str | None = Form(None),
    sector: str | None = Form(None),
    price_provider: str | None = Form(None),
    account_id: str | None = Form(None),
    notes: str | None = Form(None),
    tags: str | None = Form(None),
):
    c = request.app.state.container
    isin_clean = (isin or "").strip().upper() or None
    asset = Asset(
        name=name,
        symbol=(symbol or "").strip().upper() or None,
        isin=isin_clean,
        instrument_type=InstrumentType(instrument_type),
        asset_class=AssetClass(asset_class),
        currency=currency,
        country=country or None,
        sector=sector or None,
        price_provider=price_provider or None,
        account_id=account_id or None,
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
    isin: str | None = Form(None),
    instrument_type: str = Form(...),
    asset_class: str = Form(...),
    currency: str = Form(...),
    country: str | None = Form(None),
    sector: str | None = Form(None),
    price_provider: str | None = Form(None),
    account_id: str | None = Form(None),
    notes: str | None = Form(None),
    tags: str | None = Form(None),
):
    c = request.app.state.container
    try:
        existing = c.portfolio.assets.get(asset_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    existing.name = name
    existing.symbol = (symbol or "").strip().upper() or None
    existing.isin = (isin or "").strip().upper() or None
    existing.instrument_type = InstrumentType(instrument_type)
    existing.asset_class = AssetClass(asset_class)
    existing.currency = currency
    existing.country = country or None
    existing.sector = sector or None
    existing.price_provider = price_provider or None
    existing.account_id = account_id or None
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
