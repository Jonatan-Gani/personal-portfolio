from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...domain.enums import PositionKind, TransactionType
from ...domain.exceptions import NotFoundError
from ...domain.models import CashHolding, Transaction

router = APIRouter()


@router.get("/cash")
def list_cash(request: Request):
    c = request.app.state.container
    items = c.portfolio.list_cash(include_inactive=True)
    state = c.holdings.at()
    accounts = c.accounts_repo.list_active()
    return request.app.state.templates.TemplateResponse(
        request,
        "cash.html",
        {"request": request, "items": items, "balances": state.cash_balances, "accounts": accounts},
    )


@router.post("/cash")
def create_cash(
    request: Request,
    account_name: str = Form(...),
    currency: str = Form(...),
    opening_balance: float | None = Form(None),
    country: str | None = Form(None),
    account_id: str | None = Form(None),
    notes: str | None = Form(None),
    tags: str | None = Form(None),
):
    c = request.app.state.container
    cash = CashHolding(
        account_name=account_name,
        currency=currency,
        country=country or None,
        account_id=account_id or None,
        notes=notes,
        tags=[t.strip() for t in (tags or "").split(",") if t.strip()],
    )
    c.portfolio.add_cash(cash)
    if opening_balance is not None and opening_balance != 0:
        tx = Transaction(
            transaction_date=date.today(),
            transaction_type=TransactionType.OPENING_BALANCE,
            entity_kind=PositionKind.CASH,
            entity_id=cash.cash_id,
            amount=opening_balance,
            currency=currency,
            notes="opening balance set on account creation",
        )
        c.fx.stamp_transaction(tx, c.config.reporting.base_currency)
        c.transactions_repo.insert(tx)
        if c.config.auto_snapshot.enabled:
            c.snapshot.take(notes="auto · after cash account created")
    return RedirectResponse("/cash", status_code=303)


@router.post("/cash/{cash_id}/update")
def update_cash(
    request: Request,
    cash_id: str,
    account_name: str = Form(...),
    currency: str = Form(...),
    country: str | None = Form(None),
    account_id: str | None = Form(None),
    notes: str | None = Form(None),
    tags: str | None = Form(None),
):
    c = request.app.state.container
    try:
        existing = c.portfolio.cash.get(cash_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    existing.account_name = account_name
    existing.currency = currency
    existing.country = country or None
    existing.account_id = account_id or None
    existing.notes = notes
    existing.tags = [t.strip() for t in (tags or "").split(",") if t.strip()]
    c.portfolio.update_cash(existing)
    return RedirectResponse("/cash", status_code=303)


@router.post("/cash/{cash_id}/delete")
def delete_cash(request: Request, cash_id: str, hard: bool = False):
    c = request.app.state.container
    c.portfolio.remove_cash(cash_id, hard=hard)
    return RedirectResponse("/cash", status_code=303)
