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


def _split_entity(entity: str) -> tuple[PositionKind, str]:
    """Parse the friendly entity picker value `<kind>:<id>` back into our enum + id."""
    if ":" not in entity:
        raise HTTPException(400, "entity must be in the form '<kind>:<id>'")
    kind_str, _, eid = entity.partition(":")
    try:
        kind = PositionKind(kind_str)
    except ValueError as e:
        raise HTTPException(400, f"unknown entity kind {kind_str!r}") from e
    if not eid:
        raise HTTPException(400, "entity id missing")
    return kind, eid


def _entity_index(c) -> dict[str, dict]:
    """Build a {kind:id → {name, currency, account_id, broker}} index so we can
    decorate transaction rows without N+1 lookups."""
    out: dict[str, dict] = {}
    accounts_by_id = {a.account_id: a for a in c.accounts_repo.list_all()}
    for a in c.portfolio.list_assets(include_inactive=True):
        acct = accounts_by_id.get(a.account_id) if a.account_id else None
        out[f"asset:{a.asset_id}"] = {
            "name": a.name, "currency": a.currency, "symbol": a.symbol,
            "isin": a.isin, "account_id": a.account_id,
            "account_name": acct.name if acct else None,
            "account_broker": acct.broker if acct else None,
            "is_active": a.is_active,
        }
    for ca in c.portfolio.list_cash(include_inactive=True):
        acct = accounts_by_id.get(ca.account_id) if ca.account_id else None
        out[f"cash:{ca.cash_id}"] = {
            "name": ca.account_name, "currency": ca.currency, "symbol": None,
            "isin": None, "account_id": ca.account_id,
            "account_name": acct.name if acct else None,
            "account_broker": acct.broker if acct else None,
            "is_active": ca.is_active,
        }
    for l in c.portfolio.list_liabilities(include_inactive=True):
        acct = accounts_by_id.get(l.account_id) if l.account_id else None
        out[f"liability:{l.liability_id}"] = {
            "name": l.name, "currency": l.currency, "symbol": None,
            "isin": None, "account_id": l.account_id,
            "account_name": acct.name if acct else None,
            "account_broker": acct.broker if acct else None,
            "is_active": l.is_active,
        }
    return out


@router.get("/transactions")
def list_transactions(
    request: Request,
    kind: str | None = None,
    entity_id: str | None = None,
    type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    account: str | None = None,
):
    c = request.app.state.container
    txs = c.transactions_repo.list_all(
        entity_kind=kind or None,
        entity_id=entity_id or None,
        transaction_type=type or None,
        since=_parse_date(since),
        until=_parse_date(until),
    )

    entity_idx = _entity_index(c)

    # Optional account filter: keep only transactions whose entity belongs to that account.
    if account:
        txs = [
            t for t in txs
            if (entity_idx.get(f"{t.entity_kind.value}:{t.entity_id}") or {}).get("account_id") == account
        ]

    assets = c.portfolio.list_assets()
    cash_accounts = c.portfolio.list_cash()
    liabilities = c.portfolio.list_liabilities()
    accounts = c.accounts_repo.list_active()

    return request.app.state.templates.TemplateResponse(
        request,
        "transactions.html",
        {
            "request": request,
            "items": txs,
            "entity_idx": entity_idx,
            "filters": {
                "kind": kind, "entity_id": entity_id, "type": type,
                "since": since, "until": until, "account": account,
            },
            "transaction_types": [t.value for t in TransactionType],
            "assets": assets,
            "cash_accounts": cash_accounts,
            "liabilities": liabilities,
            "accounts": accounts,
            "import_count": request.query_params.get("import_count"),
        },
    )


# ---------------------------------------------------------------- create

@router.post("/transactions")
def create_transaction(
    request: Request,
    transaction_date: str = Form(...),
    transaction_type: str = Form(...),
    entity: str = Form(...),                       # friendly: '<kind>:<id>'
    quantity: float | None = Form(None),
    price: float | None = Form(None),
    amount: float | None = Form(None),             # may be derived server-side
    currency: str | None = Form(None),             # may be inherited from entity
    fees: float = Form(0.0),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    kind, eid = _split_entity(entity)
    ttype = TransactionType(transaction_type)

    # Auto-inherit currency from the entity if the form didn't supply one.
    if not currency:
        idx = _entity_index(c)
        e = idx.get(f"{kind.value}:{eid}")
        if not e:
            raise HTTPException(400, f"unknown entity {entity!r}")
        currency = e["currency"]

    # Derive amount for trade-style transactions if the user didn't override it.
    # Buy/Sell: |amount| = |qty * price| + fees. Sell stays positive (it's the
    # cash inflow from selling); Buy stays positive (it's the cash outflow).
    if amount is None or amount == 0:
        if ttype in (TransactionType.BUY, TransactionType.SELL) and quantity and price:
            amount = abs(quantity * price)
            if ttype == TransactionType.BUY:
                amount = amount + fees
            else:  # SELL — fees reduce proceeds
                amount = amount - fees
    if amount is None:
        amount = 0.0

    tx = Transaction(
        transaction_date=date.fromisoformat(transaction_date),
        transaction_type=ttype,
        entity_kind=kind,
        entity_id=eid,
        quantity=quantity,
        price=price,
        amount=amount,
        currency=currency,
        fees=fees,
        notes=notes,
    )
    c.transactions_repo.insert(tx)
    if c.config.auto_snapshot.enabled:
        c.snapshot.take(notes="auto · after transaction")
    return RedirectResponse("/transactions", status_code=303)


# ---------------------------------------------------------------- update

@router.post("/transactions/{transaction_id}/update")
def update_transaction(
    request: Request,
    transaction_id: str,
    transaction_date: str = Form(...),
    transaction_type: str = Form(...),
    entity: str = Form(...),
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
    kind, eid = _split_entity(entity)
    existing.transaction_date = date.fromisoformat(transaction_date)
    existing.transaction_type = TransactionType(transaction_type)
    existing.entity_kind = kind
    existing.entity_id = eid
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
