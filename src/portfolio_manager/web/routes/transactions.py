from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...domain.enums import (
    AssetClass,
    InstrumentType,
    LiabilityType,
    PositionKind,
    TransactionType,
)
from ...domain.exceptions import NotFoundError
from ...domain.models import Asset, CashHolding, Liability, Transaction

router = APIRouter()


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s)


def _kind_from_type(ttype: TransactionType, kind_override: str | None) -> PositionKind:
    """Most transaction types determine their entity kind. Only opening_balance is
    ambiguous and needs the caller to supply one."""
    if ttype in (TransactionType.BUY, TransactionType.SELL, TransactionType.SPLIT):
        return PositionKind.ASSET
    if ttype in (
        TransactionType.DEPOSIT, TransactionType.WITHDRAW,
        TransactionType.DIVIDEND, TransactionType.INTEREST, TransactionType.FEE,
    ):
        return PositionKind.CASH
    if ttype in (TransactionType.REPAYMENT, TransactionType.PRINCIPAL_CHANGE):
        return PositionKind.LIABILITY
    # opening_balance
    if not kind_override:
        raise HTTPException(400, "kind is required for opening_balance transactions")
    try:
        return PositionKind(kind_override)
    except ValueError as e:
        raise HTTPException(400, f"unknown position kind {kind_override!r}") from e


def _resolve_or_create_entity(
    c,
    *,
    kind: PositionKind,
    symbol: str | None,
    name: str | None,
    account_id: str | None,
    currency: str | None,
) -> tuple[str, str]:
    """Find an existing entity matching (account, symbol-or-name) for this kind, or
    create a new one. Returns (entity_id, entity_currency).

    Match rule:
      - asset: same account_id AND (symbol matches when provided, else name matches)
      - cash/liability: same account_id AND name matches (case-insensitive)
    """
    sym = (symbol or "").strip().upper() or None
    nm = (name or "").strip() or None
    acct = (account_id or "").strip() or None
    ccy = (currency or "").strip().upper() or None

    if kind is PositionKind.ASSET:
        if not sym and not nm:
            raise HTTPException(400, "asset transactions need a symbol or name")
        for a in c.portfolio.list_assets(include_inactive=True):
            if a.account_id != acct:
                continue
            if sym and a.symbol and a.symbol.upper() == sym:
                return a.asset_id, a.currency
            if not sym and nm and a.name.strip().lower() == nm.lower():
                return a.asset_id, a.currency
        if not ccy:
            raise HTTPException(400, "currency is required for a new asset")
        created = c.portfolio.add_asset(Asset(
            name=nm or sym or "Unnamed", symbol=sym, currency=ccy, account_id=acct,
            instrument_type=InstrumentType.OTHER, asset_class=AssetClass.OTHER,
        ))
        return created.asset_id, created.currency

    if kind is PositionKind.CASH:
        if not nm:
            raise HTTPException(400, "cash transactions need a cash-account name")
        for ca in c.portfolio.list_cash(include_inactive=True):
            if ca.account_id == acct and ca.account_name.strip().lower() == nm.lower():
                return ca.cash_id, ca.currency
        if not ccy:
            raise HTTPException(400, "currency is required for a new cash account")
        created = c.portfolio.add_cash(CashHolding(
            account_name=nm, currency=ccy, account_id=acct,
        ))
        return created.cash_id, created.currency

    if kind is PositionKind.LIABILITY:
        if not nm:
            raise HTTPException(400, "liability transactions need a name")
        for li in c.portfolio.list_liabilities(include_inactive=True):
            if li.account_id == acct and li.name.strip().lower() == nm.lower():
                return li.liability_id, li.currency
        if not ccy:
            raise HTTPException(400, "currency is required for a new liability")
        created = c.portfolio.add_liability(Liability(
            name=nm, currency=ccy, account_id=acct,
            liability_type=LiabilityType.OTHER,
        ))
        return created.liability_id, created.currency

    raise HTTPException(400, f"cannot resolve entity for kind {kind!r}")


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

    # Known positions for the form's auto-fill (symbol/name → currency, account).
    known_positions = (
        [{"kind": "asset", "symbol": a.symbol or "", "name": a.name,
          "currency": a.currency, "account_id": a.account_id or ""} for a in assets]
        + [{"kind": "cash", "symbol": "", "name": ca.account_name,
            "currency": ca.currency, "account_id": ca.account_id or ""} for ca in cash_accounts]
        + [{"kind": "liability", "symbol": "", "name": li.name,
            "currency": li.currency, "account_id": li.account_id or ""} for li in liabilities]
    )

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
            "known_positions": known_positions,
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
    symbol: str | None = Form(None),
    name: str | None = Form(None),
    account_id: str | None = Form(None),
    kind: str | None = Form(None),                 # only used for opening_balance
    quantity: float | None = Form(None),
    price: float | None = Form(None),
    amount: float | None = Form(None),
    currency: str | None = Form(None),
    fees: float = Form(0.0),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    ttype = TransactionType(transaction_type)
    ekind = _kind_from_type(ttype, kind)
    eid, entity_currency = _resolve_or_create_entity(
        c, kind=ekind, symbol=symbol, name=name,
        account_id=account_id, currency=currency,
    )
    if not currency:
        currency = entity_currency

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
        entity_kind=ekind,
        entity_id=eid,
        quantity=quantity,
        price=price,
        amount=amount,
        currency=currency,
        fees=fees,
        notes=notes,
    )
    c.inception.stamp(tx)
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
    quantity: float | None = Form(None),
    price: float | None = Form(None),
    amount: float = Form(0.0),
    currency: str = Form(...),
    fees: float = Form(0.0),
    notes: str | None = Form(None),
):
    """Edit a transaction in place. The position it belongs to is fixed — to
    move a transaction to a different position, delete and re-record it."""
    c = request.app.state.container
    try:
        existing = c.transactions_repo.get(transaction_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    existing.transaction_date = date.fromisoformat(transaction_date)
    existing.transaction_type = TransactionType(transaction_type)
    existing.quantity = quantity
    existing.price = price
    existing.amount = amount
    existing.currency = currency
    existing.fees = fees
    existing.notes = notes
    # Currency or date may have changed — re-pin the inception FX rate.
    c.inception.stamp(existing)
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


# ---------------------------------------------------------------- position builder

def _parse_float(s: str | None) -> float | None:
    if s is None or str(s).strip() == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


@router.get("/position-builder")
def position_builder(request: Request):
    c = request.app.state.container
    return request.app.state.templates.TemplateResponse(
        request,
        "position_builder.html",
        {
            "request": request,
            "accounts": c.accounts_repo.list_active(),
            "today": date.today().isoformat(),
        },
    )


@router.post("/position-builder")
async def position_builder_submit(request: Request):
    """Bulk-create holdings as opening_balance transactions. Each submitted row
    is one position; rows with neither a symbol nor a name are skipped."""
    c = request.app.state.container
    form = await request.form()
    as_of = _parse_date(form.get("as_of")) or date.today()

    kinds = form.getlist("kind")
    symbols = form.getlist("symbol")
    names = form.getlist("name")
    account_ids = form.getlist("account_id")
    quantities = form.getlist("quantity")
    unit_costs = form.getlist("unit_cost")
    amounts = form.getlist("amount")
    currencies = form.getlist("currency")

    def cell(seq: list, i: int) -> str:
        return str(seq[i]).strip() if i < len(seq) and seq[i] is not None else ""

    created = 0
    for i in range(len(kinds)):
        sym, nm = cell(symbols, i), cell(names, i)
        if not sym and not nm:
            continue  # blank row
        try:
            kind = PositionKind(cell(kinds, i))
        except ValueError:
            continue

        ccy = cell(currencies, i).upper() or None
        eid, entity_ccy = _resolve_or_create_entity(
            c, kind=kind, symbol=sym or None, name=nm or None,
            account_id=cell(account_ids, i) or None, currency=ccy,
        )
        ccy = ccy or entity_ccy

        qty = _parse_float(cell(quantities, i))
        cost = _parse_float(cell(unit_costs, i))
        amt = _parse_float(cell(amounts, i))

        if kind is PositionKind.ASSET:
            if not qty:
                continue
            price = cost
            amount = qty * cost if (qty and cost) else (amt or 0.0)
        else:
            qty = price = None
            amount = amt or 0.0
            if not amount:
                continue

        tx = Transaction(
            transaction_date=as_of, transaction_type=TransactionType.OPENING_BALANCE,
            entity_kind=kind, entity_id=eid, quantity=qty, price=price,
            amount=amount, currency=ccy, notes="position builder",
        )
        c.inception.stamp(tx)
        c.transactions_repo.insert(tx)
        created += 1

    if created and c.config.auto_snapshot.enabled:
        c.snapshot.take(notes="auto · after position builder")
    return RedirectResponse(f"/holdings?builder={created}", status_code=303)
