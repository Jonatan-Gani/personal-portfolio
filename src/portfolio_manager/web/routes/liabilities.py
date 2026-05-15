from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...domain.enums import LiabilityType, PositionKind, TransactionType
from ...domain.exceptions import NotFoundError
from ...domain.models import Liability, Transaction

router = APIRouter()


@router.get("/liabilities")
def list_liabilities(request: Request):
    c = request.app.state.container
    items = c.portfolio.list_liabilities(include_inactive=True)
    base = c.config.reporting.base_currency

    # Outstanding principal per liability — derived from transaction log.
    state = c.holdings.at()
    principals = state.liability_principals

    latest = c.snapshots_repo.latest()
    debts_total = 0.0
    if latest is not None:
        row = c.db.fetchone(
            """
            SELECT COALESCE(SUM(v.value), 0)
              FROM snapshot_positions p
              JOIN snapshot_position_values v
                ON v.snapshot_id = p.snapshot_id
               AND v.position_kind = p.position_kind
               AND v.entity_id = p.entity_id
               AND v.currency = ?
             WHERE p.snapshot_id = ? AND p.position_kind = 'liability'
            """,
            [base, latest.snapshot_id],
        )
        if row:
            debts_total = float(row[0] or 0)
    return request.app.state.templates.TemplateResponse(
        request,
        "liabilities.html",
        {
            "request": request,
            "items": items,
            "principals": principals,
            "liability_types": [t.value for t in LiabilityType],
            "debts_total": debts_total,
        },
    )


@router.post("/liabilities")
def create_liability(
    request: Request,
    name: str = Form(...),
    liability_type: str = Form(...),
    currency: str = Form(...),
    opening_balance: float = Form(...),
    interest_rate: float | None = Form(None),
    interest_rate_pct: float | None = Form(None),
    notes: str | None = Form(None),
    tags: str | None = Form(None),
):
    c = request.app.state.container
    # Accept either the legacy decimal field or the new APR-percent field.
    if interest_rate_pct is not None:
        interest_rate = interest_rate_pct / 100.0
    liab = Liability(
        name=name,
        liability_type=LiabilityType(liability_type),
        currency=currency,
        interest_rate=interest_rate,
        notes=notes,
        tags=[t.strip() for t in (tags or "").split(",") if t.strip()],
    )
    c.portfolio.add_liability(liab)
    if opening_balance and opening_balance != 0:
        c.transactions_repo.insert(Transaction(
            transaction_date=date.today(),
            transaction_type=TransactionType.OPENING_BALANCE,
            entity_kind=PositionKind.LIABILITY,
            entity_id=liab.liability_id,
            amount=opening_balance,
            currency=currency,
            notes="opening balance",
        ))
    return RedirectResponse("/liabilities", status_code=303)


@router.post("/liabilities/{liability_id}/update")
def update_liability(
    request: Request,
    liability_id: str,
    name: str = Form(...),
    liability_type: str = Form(...),
    currency: str = Form(...),
    interest_rate: float | None = Form(None),
    interest_rate_pct: float | None = Form(None),
    notes: str | None = Form(None),
    tags: str | None = Form(None),
):
    c = request.app.state.container
    try:
        existing = c.portfolio.liabilities.get(liability_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    if interest_rate_pct is not None:
        interest_rate = interest_rate_pct / 100.0
    existing.name = name
    existing.liability_type = LiabilityType(liability_type)
    existing.currency = currency
    existing.interest_rate = interest_rate
    existing.notes = notes
    existing.tags = [t.strip() for t in (tags or "").split(",") if t.strip()]
    c.portfolio.update_liability(existing)
    return RedirectResponse("/liabilities", status_code=303)


@router.post("/liabilities/{liability_id}/delete")
def delete_liability(request: Request, liability_id: str, hard: bool = False):
    c = request.app.state.container
    c.portfolio.remove_liability(liability_id, hard=hard)
    return RedirectResponse("/liabilities", status_code=303)
