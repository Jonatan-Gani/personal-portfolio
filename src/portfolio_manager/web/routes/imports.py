from __future__ import annotations

import csv
import io
import json
from datetime import date

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from ...domain.enums import PositionKind, TransactionType
from ...domain.models import Transaction

router = APIRouter()


REQUIRED = {"date", "type", "entity_kind", "entity_id", "amount", "currency"}
OPTIONAL = {"quantity", "price", "fees", "notes"}


@router.get("/import")
def import_page(request: Request):
    c = request.app.state.container
    return request.app.state.templates.TemplateResponse(
        request,
        "import.html",
        {
            "request": request,
            "transaction_types": [t.value for t in TransactionType],
            "entity_kinds": [k.value for k in PositionKind],
            "assets": c.portfolio.list_assets(include_inactive=True),
            "cash_accounts": c.portfolio.list_cash(include_inactive=True),
            "liabilities": c.portfolio.list_liabilities(include_inactive=True),
            "preview": None,
            "errors": [],
        },
    )


@router.post("/import/preview")
async def import_preview(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008 — FastAPI dependency injection
    column_map: str = Form("{}"),  # JSON: {csv_col: target_field}
    has_header: str = Form("on"),
):
    """Parse the uploaded CSV with the user's column map; return rows as a preview.
    Doesn't insert anything yet — the user reviews and submits to /import/commit."""
    c = request.app.state.container
    raw = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(raw))
    rows = list(reader)
    if not rows:
        return _render_preview(request, [], ["empty file"], raw, column_map, has_header == "on", c)

    header = rows[0] if has_header == "on" else [f"col_{i}" for i in range(len(rows[0]))]
    body = rows[1:] if has_header == "on" else rows

    try:
        cmap: dict[str, str] = json.loads(column_map) if column_map.strip() else {}
    except json.JSONDecodeError as e:
        return _render_preview(request, [], [f"column map JSON: {e}"], raw, column_map, has_header == "on", c)

    parsed: list[dict] = []
    errors: list[str] = []
    for i, r in enumerate(body, start=(2 if has_header == "on" else 1)):
        rec: dict = {}
        for col_idx, col_name in enumerate(header):
            target = cmap.get(col_name)
            if not target or col_idx >= len(r):
                continue
            rec[target] = r[col_idx].strip()
        # Validate required
        missing = REQUIRED - rec.keys()
        if missing:
            errors.append(f"row {i}: missing fields {sorted(missing)}")
            continue
        try:
            d = date.fromisoformat(rec["date"])
            ttype = TransactionType(rec["type"])
            ekind = PositionKind(rec["entity_kind"])
            tx = Transaction(
                transaction_date=d,
                transaction_type=ttype,
                entity_kind=ekind,
                entity_id=rec["entity_id"],
                quantity=float(rec["quantity"]) if rec.get("quantity") else None,
                price=float(rec["price"]) if rec.get("price") else None,
                amount=float(rec["amount"]),
                currency=rec["currency"],
                fees=float(rec["fees"]) if rec.get("fees") else 0.0,
                notes=rec.get("notes") or None,
            )
            parsed.append(tx.model_dump())
        except Exception as e:
            errors.append(f"row {i}: {type(e).__name__}: {e}")
    return _render_preview(request, parsed, errors, raw, column_map, has_header == "on", c, header=header)


@router.post("/import/commit")
async def import_commit(
    request: Request,
    payload: str = Form(...),  # JSON-encoded list of validated tx dicts
):
    c = request.app.state.container
    txs = json.loads(payload)
    n = 0
    for d in txs:
        # Re-validate via Pydantic to be safe; date/enum strings are reparsed.
        tx = Transaction.model_validate(d)
        c.transactions_repo.insert(tx)
        n += 1
    return RedirectResponse(f"/transactions?import_count={n}", status_code=303)


def _render_preview(request, parsed, errors, raw, column_map, has_header, c, header=None):
    return request.app.state.templates.TemplateResponse(
        request,
        "import.html",
        {
            "request": request,
            "transaction_types": [t.value for t in TransactionType],
            "entity_kinds": [k.value for k in PositionKind],
            "assets": c.portfolio.list_assets(include_inactive=True),
            "cash_accounts": c.portfolio.list_cash(include_inactive=True),
            "liabilities": c.portfolio.list_liabilities(include_inactive=True),
            "preview": parsed,
            "errors": errors,
            "raw": raw,
            "column_map": column_map,
            "has_header": has_header,
            "header": header or [],
            "fields": sorted(REQUIRED | OPTIONAL),
            "preview_payload": json.dumps(parsed, default=str),
        },
    )
