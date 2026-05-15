from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...domain.enums import PositionKind, TransactionType
from ...domain.exceptions import NotFoundError
from ...domain.models import Transaction

router = APIRouter()


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s)


@router.get("/transactions")
def list_transactions(
    request: Request,
    kind: str | None = None,
    entity_id: str | None = None,
    type: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    c = request.app.state.container
    txs = c.transactions_repo.list_all(
        entity_kind=kind or None,
        entity_id=entity_id or None,
        transaction_type=type or None,
        since=_parse_date(since),
        until=_parse_date(until),
    )

    # Build entity name lookup so the table can show a friendly label.
    asset_names = {a.asset_id: a.name for a in c.portfolio.list_assets(include_inactive=True)}
    cash_names = {ca.cash_id: ca.account_name for ca in c.portfolio.list_cash(include_inactive=True)}
    liab_names = {l.liability_id: l.name for l in c.portfolio.list_liabilities(include_inactive=True)}

    return request.app.state.templates.TemplateResponse(
        request,
        "transactions.html",
        {
            "request": request,
            "items": txs,
            "asset_names": asset_names,
            "cash_names": cash_names,
            "liab_names": liab_names,
            "filters": {
                "kind": kind, "entity_id": entity_id, "type": type,
                "since": since, "until": until,
            },
            "transaction_types": [t.value for t in TransactionType],
            "assets": c.portfolio.list_assets(),
            "cash_accounts": c.portfolio.list_cash(),
            "liabilities": c.portfolio.list_liabilities(),
        },
    )


@router.post("/transactions")
def create_transaction(
    request: Request,
    transaction_date: str = Form(...),
    transaction_type: str = Form(...),
    entity_kind: str = Form(...),
    entity_id: str = Form(...),
    quantity: float | None = Form(None),
    price: float | None = Form(None),
    amount: float = Form(0.0),
    currency: str = Form(...),
    fees: float = Form(0.0),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    tx = Transaction(
        transaction_date=date.fromisoformat(transaction_date),
        transaction_type=TransactionType(transaction_type),
        entity_kind=PositionKind(entity_kind),
        entity_id=entity_id,
        quantity=quantity,
        price=price,
        amount=amount,
        currency=currency,
        fees=fees,
        notes=notes,
    )
    c.transactions_repo.insert(tx)
    # Auto-snapshot so the dashboard reflects the new state right away.
    if c.config.auto_snapshot.enabled:
        c.snapshot.take(notes="auto · after transaction")
    return RedirectResponse("/transactions", status_code=303)


@router.post("/transactions/{transaction_id}/update")
def update_transaction(
    request: Request,
    transaction_id: str,
    transaction_date: str = Form(...),
    transaction_type: str = Form(...),
    entity_kind: str = Form(...),
    entity_id: str = Form(...),
    quantity: float | None = Form(None),
    price: float | None = Form(None),
    amount: float = Form(0.0),
    currency: str = Form(...),
    fees: float = Form(0.0),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    try:
        existing = c.transactions_repo.get(transaction_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    existing.transaction_date = date.fromisoformat(transaction_date)
    existing.transaction_type = TransactionType(transaction_type)
    existing.entity_kind = PositionKind(entity_kind)
    existing.entity_id = entity_id
    existing.quantity = quantity
    existing.price = price
    existing.amount = amount
    existing.currency = currency
    existing.fees = fees
    existing.notes = notes
    c.transactions_repo.update(existing)
    if c.config.auto_snapshot.enabled:
        c.snapshot.take(notes="auto · after transaction edit")
    return RedirectResponse("/transactions", status_code=303)


@router.post("/transactions/{transaction_id}/delete")
def delete_transaction(request: Request, transaction_id: str):
    c = request.app.state.container
    c.transactions_repo.delete(transaction_id)
    if c.config.auto_snapshot.enabled:
        c.snapshot.take(notes="auto · after transaction delete")
    return RedirectResponse("/transactions", status_code=303)
