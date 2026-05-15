from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter()


def _snapshot_position_breakdown(db, snapshot_id: str, base_ccy: str) -> dict:
    """Return totals + per-class breakdown for one snapshot, scoped by account_id list."""
    raise NotImplementedError  # used via _scope_breakdown below


def _scope_breakdown(c, account_ids: list[str], base: str) -> dict:
    """Aggregate the latest snapshot's value for a set of accounts.
    Returns: {net_value, assets, cash, liabilities, by_class:[(label,value)], top:[(name,kind,value)]}.
    If `account_ids` is empty, scopes to "all"; if it contains the sentinel "__unassigned__",
    only NULL-account holdings are included.
    """
    row = c.db.fetchone("SELECT snapshot_id FROM snapshots ORDER BY taken_at DESC LIMIT 1")
    if not row:
        return {"net_value": 0.0, "assets": 0.0, "cash": 0.0, "liabilities": 0.0,
                "by_class": [], "top": []}
    snap_id = row[0]

    # Build the account filter. Empty -> all. Otherwise filter on assets.account_id /
    # cash_holdings.account_id / liabilities.account_id IN (...).
    if not account_ids:
        acct_filter_assets = "1=1"
        acct_filter_cash = "1=1"
        acct_filter_liab = "1=1"
        acct_params: list = []
    elif account_ids == ["__unassigned__"]:
        acct_filter_assets = "a.account_id IS NULL"
        acct_filter_cash = "ch.account_id IS NULL"
        acct_filter_liab = "l.account_id IS NULL"
        acct_params = []
    else:
        placeholders = ",".join(["?"] * len(account_ids))
        acct_filter_assets = f"a.account_id IN ({placeholders})"
        acct_filter_cash = f"ch.account_id IN ({placeholders})"
        acct_filter_liab = f"l.account_id IN ({placeholders})"
        acct_params = account_ids

    base_params: list = [base, snap_id]

    def _q(joins: str, filt: str, kind: str, sign: int = 1) -> list[tuple[str, str, float]]:
        rows = c.db.fetchall(
            f"""
            SELECT p.name, p.asset_class, p.instrument_type, v.value
              FROM snapshot_positions p
              JOIN snapshot_position_values v
                ON v.snapshot_id = p.snapshot_id
               AND v.position_kind = p.position_kind
               AND v.entity_id = p.entity_id
               AND v.currency = ?
              {joins}
             WHERE p.snapshot_id = ? AND p.position_kind = '{kind}'
               AND {filt}
            """,
            base_params + acct_params,
        )
        return [(name or "—", cls or "—", inst or "—", float(val or 0) * sign) for name, cls, inst, val in rows]

    asset_rows = _q("JOIN assets a ON a.asset_id = p.entity_id", acct_filter_assets, "asset")
    cash_rows = _q("JOIN cash_holdings ch ON ch.cash_id = p.entity_id", acct_filter_cash, "cash")
    liab_rows = _q("JOIN liabilities l ON l.liability_id = p.entity_id", acct_filter_liab, "liability", sign=-1)

    assets_total = sum(v for _, _, _, v in asset_rows)
    cash_total = sum(v for _, _, _, v in cash_rows)
    liab_total = -sum(v for _, _, _, v in liab_rows)  # report positive magnitude

    # by-asset-class breakdown (assets + cash combined)
    cls_totals: dict[str, float] = {}
    for _, cls, _, v in asset_rows:
        cls_totals[cls] = cls_totals.get(cls, 0.0) + v
    for _, _, _, v in cash_rows:
        cls_totals["cash"] = cls_totals.get("cash", 0.0) + v
    by_class = sorted(cls_totals.items(), key=lambda x: -x[1])

    # top positions by absolute value
    all_rows = [(n, "asset", v) for n, _, _, v in asset_rows] \
        + [(n, "cash", v) for n, _, _, v in cash_rows] \
        + [(n, "liability", v) for n, _, _, v in liab_rows]
    top = sorted(all_rows, key=lambda x: -abs(x[2]))[:8]

    return {
        "net_value": assets_total + cash_total - liab_total,
        "assets": assets_total,
        "cash": cash_total,
        "liabilities": liab_total,
        "by_class": by_class,
        "top": top,
    }


@router.get("/compare")
def compare_page(
    request: Request,
    scope: list[str] = Query(default=[]),
):
    """Compare up to N scopes side-by-side. Each scope value is one of:
      - 'all'                — entire portfolio
      - 'unassigned'         — only holdings with no account_id
      - 'group:<group_id>'   — sum of accounts in that group
      - 'account:<acct_id>'  — a single account
    """
    c = request.app.state.container
    base = c.config.reporting.base_currency
    groups = c.account_groups_repo.list_active()
    accounts = c.accounts_repo.list_active()
    acct_by_group: dict[str | None, list] = {}
    for a in accounts:
        acct_by_group.setdefault(a.group_id, []).append(a)

    panels = []
    for s in scope:
        label = "All holdings"
        acct_ids: list[str] = []
        if s == "all":
            acct_ids = []
        elif s == "unassigned":
            label = "Unassigned"
            acct_ids = ["__unassigned__"]
        elif s.startswith("group:"):
            gid = s.split(":", 1)[1]
            try:
                g = c.account_groups_repo.get(gid)
                label = f"Group · {g.name}"
            except Exception:  # noqa: BLE001
                label = "Group · ?"
            acct_ids = [a.account_id for a in acct_by_group.get(gid, [])]
            if not acct_ids:
                acct_ids = ["__no_match__"]  # ensure empty result, not "all"
        elif s.startswith("account:"):
            aid = s.split(":", 1)[1]
            try:
                a = c.accounts_repo.get(aid)
                label = f"{a.name}" + (f" · {a.broker}" if a.broker else "")
            except Exception:  # noqa: BLE001
                label = "Account · ?"
            acct_ids = [aid]
        else:
            continue
        bd = _scope_breakdown(c, acct_ids, base)
        panels.append({"key": s, "label": label, **bd})

    return request.app.state.templates.TemplateResponse(
        request,
        "compare.html",
        {
            "request": request,
            "base_currency": base,
            "groups": groups,
            "accounts": accounts,
            "acct_by_group": acct_by_group,
            "scope": scope,
            "panels": panels,
        },
    )
