from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...domain.exceptions import NotFoundError
from ...domain.models import Account, AccountGroup

router = APIRouter()


ACCOUNT_TYPES = [
    "taxable", "ira", "roth", "k401", "hsa",
    "checking", "savings", "brokerage", "mortgage", "loan", "credit_card", "other",
]
GROUP_KINDS = ["household", "person", "institution", "strategy", "other"]


def _holding_counts_by_account(db) -> dict[str | None, dict[str, int]]:
    """Count active holdings per account_id, split by kind. NULL means Unassigned."""
    rows = db.fetchall(
        """
        SELECT account_id, 'asset'     AS kind, COUNT(*) FROM assets        WHERE is_active = TRUE GROUP BY account_id
        UNION ALL
        SELECT account_id, 'cash'      AS kind, COUNT(*) FROM cash_holdings WHERE is_active = TRUE GROUP BY account_id
        UNION ALL
        SELECT account_id, 'liability' AS kind, COUNT(*) FROM liabilities   WHERE is_active = TRUE GROUP BY account_id
        """
    )
    out: dict[str | None, dict[str, int]] = {}
    for account_id, kind, n in rows:
        bucket = out.setdefault(account_id, {"asset": 0, "cash": 0, "liability": 0})
        bucket[kind] = n
    return out


def _holding_values_by_account(db, base_ccy: str) -> dict[str | None, float]:
    """Sum the latest snapshot's positions in base currency, grouped by the
    holding entity's account_id. Returns {account_id_or_None: net_value_base}.
    Liabilities are subtracted from net worth."""
    row = db.fetchone("SELECT snapshot_id FROM snapshots ORDER BY taken_at DESC LIMIT 1")
    if not row:
        return {}
    snap_id = row[0]
    rows = db.fetchall(
        """
        WITH pos AS (
          SELECT p.position_kind, p.entity_id, v.value
            FROM snapshot_positions p
            JOIN snapshot_position_values v
              ON v.snapshot_id = p.snapshot_id
             AND v.position_kind = p.position_kind
             AND v.entity_id = p.entity_id
             AND v.currency = ?
           WHERE p.snapshot_id = ?
        ),
        mapped AS (
          SELECT a.account_id AS account_id, pos.value AS value
            FROM pos JOIN assets        a ON a.asset_id     = pos.entity_id AND pos.position_kind = 'asset'
          UNION ALL
          SELECT c.account_id, pos.value
            FROM pos JOIN cash_holdings c ON c.cash_id      = pos.entity_id AND pos.position_kind = 'cash'
          UNION ALL
          SELECT l.account_id, -1 * pos.value
            FROM pos JOIN liabilities   l ON l.liability_id = pos.entity_id AND pos.position_kind = 'liability'
        )
        SELECT account_id, SUM(value) FROM mapped GROUP BY account_id
        """,
        [base_ccy, snap_id],
    )
    return {acc: float(v or 0) for acc, v in rows}


@router.get("/accounts")
def accounts_page(request: Request):
    c = request.app.state.container
    base = c.config.reporting.base_currency
    groups = c.account_groups_repo.list_all()
    accounts = c.accounts_repo.list_all()
    counts = _holding_counts_by_account(c.db)
    values = _holding_values_by_account(c.db, base)

    # Group accounts by their group_id for the tree view.
    by_group: dict[str | None, list] = {}
    for a in accounts:
        by_group.setdefault(a.group_id, []).append(a)

    # Totals at the group level (sum across all accounts in the group + any
    # NULL-account holdings tagged with that group are not represented; only
    # account-bound holdings roll up here).
    group_totals: dict[str | None, dict] = {}
    for g_id, items in by_group.items():
        total = sum(values.get(a.account_id, 0.0) for a in items)
        n_accts = sum(1 for a in items if a.is_active)
        group_totals[g_id] = {"value": total, "n_accounts": n_accts}

    unassigned_value = values.get(None, 0.0)
    unassigned_counts = counts.get(None, {"asset": 0, "cash": 0, "liability": 0})

    return request.app.state.templates.TemplateResponse(
        request,
        "accounts.html",
        {
            "request": request,
            "base_currency": base,
            "groups": groups,
            "by_group": by_group,
            "group_totals": group_totals,
            "counts": counts,
            "values": values,
            "unassigned_value": unassigned_value,
            "unassigned_counts": unassigned_counts,
            "account_types": ACCOUNT_TYPES,
            "group_kinds": GROUP_KINDS,
        },
    )


# ---------------- Groups ----------------

@router.post("/accounts/groups")
def create_group(
    request: Request,
    name: str = Form(...),
    kind: str = Form("household"),
    color: str | None = Form(None),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    group = AccountGroup(
        name=name.strip(),
        kind=kind,
        color=(color.strip() or None) if color else None,
        notes=notes,
    )
    c.account_groups_repo.upsert(group)
    return RedirectResponse("/accounts", status_code=303)


@router.post("/accounts/groups/{group_id}/update")
def update_group(
    request: Request,
    group_id: str,
    name: str = Form(...),
    kind: str = Form("household"),
    color: str | None = Form(None),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    try:
        g = c.account_groups_repo.get(group_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    g.name = name.strip()
    g.kind = kind
    g.color = (color.strip() or None) if color else None
    g.notes = notes
    c.account_groups_repo.upsert(g)
    return RedirectResponse("/accounts", status_code=303)


@router.post("/accounts/groups/{group_id}/delete")
def delete_group(request: Request, group_id: str, hard: bool = False):
    c = request.app.state.container
    if hard:
        c.account_groups_repo.delete(group_id)
    else:
        c.account_groups_repo.deactivate(group_id)
    return RedirectResponse("/accounts", status_code=303)


# ---------------- Accounts ----------------

@router.post("/accounts")
def create_account(
    request: Request,
    name: str = Form(...),
    group_id: str | None = Form(None),
    broker: str | None = Form(None),
    account_type: str = Form("other"),
    currency: str | None = Form(None),
    country: str | None = Form(None),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    acc = Account(
        name=name.strip(),
        group_id=(group_id or None) or None,
        broker=(broker.strip() or None) if broker else None,
        account_type=account_type,
        currency=(currency.strip().upper() or None) if currency else None,
        country=(country.strip() or None) if country else None,
        notes=notes,
    )
    c.accounts_repo.upsert(acc)
    return RedirectResponse("/accounts", status_code=303)


@router.post("/accounts/{account_id}/update")
def update_account(
    request: Request,
    account_id: str,
    name: str = Form(...),
    group_id: str | None = Form(None),
    broker: str | None = Form(None),
    account_type: str = Form("other"),
    currency: str | None = Form(None),
    country: str | None = Form(None),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    try:
        a = c.accounts_repo.get(account_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    a.name = name.strip()
    a.group_id = group_id or None
    a.broker = (broker.strip() or None) if broker else None
    a.account_type = account_type
    a.currency = (currency.strip().upper() or None) if currency else None
    a.country = (country.strip() or None) if country else None
    a.notes = notes
    c.accounts_repo.upsert(a)
    return RedirectResponse("/accounts", status_code=303)


@router.post("/accounts/{account_id}/delete")
def delete_account(request: Request, account_id: str, hard: bool = False):
    c = request.app.state.container
    if hard:
        c.accounts_repo.delete(account_id)
    else:
        c.accounts_repo.deactivate(account_id)
    return RedirectResponse("/accounts", status_code=303)


@router.post("/accounts/{account_id}/reactivate")
def reactivate_account(request: Request, account_id: str):
    c = request.app.state.container
    try:
        a = c.accounts_repo.get(account_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    a.is_active = True
    c.accounts_repo.upsert(a)
    return RedirectResponse("/accounts", status_code=303)
