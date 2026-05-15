from __future__ import annotations

from fastapi import APIRouter, Request

from ...domain.enums import AssetClass, InstrumentType
from ...services.scope import parse_scope, scope_filter_sql, scope_join_sql

router = APIRouter()


def _build_position_tree(c, base_ccy: str, account_ids: list[str] | None) -> dict:
    """Return holdings grouped as:
        accounts: [
          {
            account_id, name, broker, account_type, group_name, color, total,
            classes: [
              {key, label, total,
               types: [
                 {key, label, total,
                  rows: [{kind, entity_id, name, symbol, currency, country,
                          quantity, price_local, value_local, value_base, tags}, ...]
                 }, ...]
              }, ...]
          }, ...
        ]
        totals: {assets, cash, liabilities, net_worth}

    Cash and liabilities are also placed under their account, into synthetic
    "Cash" and "Liabilities" class buckets. Holdings with NULL account_id land
    under a synthetic "Unassigned" account.
    """
    latest = c.snapshots_repo.latest()
    if latest is None:
        return {"accounts": [], "totals": {"assets": 0.0, "cash": 0.0, "liabilities": 0.0, "net_worth": 0.0}}
    snap_id = latest.snapshot_id

    scope_where, scope_params = scope_filter_sql(account_ids)
    joins = scope_join_sql() if account_ids is not None else ""
    extra_select = (
        ", COALESCE(_a.account_id, _c.account_id, _l.account_id) AS account_id"
        if account_ids is not None else
        ", NULL AS account_id"
    )

    # We always need the per-row account_id for the tree, even when scope is "all".
    rows = c.db.fetchall_dict(
        f"""
        SELECT p.position_kind, p.entity_id, p.name, p.instrument_type, p.asset_class,
               p.currency, p.country, p.quantity, p.price_local, p.value_local, p.tags,
               COALESCE(v.value, 0) AS value_base,
               COALESCE(_a.account_id, _c.account_id, _l.account_id) AS account_id
          FROM snapshot_positions p
          LEFT JOIN snapshot_position_values v
            ON v.snapshot_id = p.snapshot_id
           AND v.position_kind = p.position_kind
           AND v.entity_id = p.entity_id
           AND v.currency = ?
          LEFT JOIN assets        _a ON _a.asset_id     = p.entity_id AND p.position_kind = 'asset'
          LEFT JOIN cash_holdings _c ON _c.cash_id      = p.entity_id AND p.position_kind = 'cash'
          LEFT JOIN liabilities   _l ON _l.liability_id = p.entity_id AND p.position_kind = 'liability'
         WHERE p.snapshot_id = ?
           {scope_where}
         ORDER BY p.position_kind, value_base DESC
        """,
        [base_ccy, snap_id, *scope_params],
    )

    # Account metadata (resolve names, brokers, groups, colours).
    acct_meta: dict[str | None, dict] = {}
    groups_by_id = {g.group_id: g for g in c.account_groups_repo.list_all()}
    for a in c.accounts_repo.list_all():
        grp = groups_by_id.get(a.group_id) if a.group_id else None
        acct_meta[a.account_id] = {
            "account_id": a.account_id,
            "name": a.name,
            "broker": a.broker,
            "account_type": a.account_type,
            "group_name": grp.name if grp else None,
            "color": grp.color if grp else None,
            "sort_key": (grp.sort_order if grp else 9999, grp.name if grp else "~", a.sort_order, a.name),
        }
    acct_meta[None] = {
        "account_id": None,
        "name": "Unassigned",
        "broker": None,
        "account_type": None,
        "group_name": None,
        "color": None,
        "sort_key": (99999, "~~", 0, "Unassigned"),
    }

    # 2-level grouping: account_id -> class_key -> type_key -> rows
    tree: dict = {}
    for r in rows:
        aid = r["account_id"]
        if r["position_kind"] == "cash":
            class_key = "_cash"
            class_label = "Cash"
            type_key = (r.get("currency") or "—").upper()
            type_label = f"Cash · {type_key}"
        elif r["position_kind"] == "liability":
            class_key = "_liability"
            class_label = "Liabilities"
            type_key = r.get("instrument_type") or "other"
            type_label = (r.get("instrument_type") or "other").replace("_", " ").capitalize()
        else:
            class_key = r.get("asset_class") or "other"
            class_label = (r.get("asset_class") or "other").replace("_", " ").capitalize()
            type_key = r.get("instrument_type") or "other"
            type_label = (r.get("instrument_type") or "other").replace("_", " ").capitalize()

        acct_bucket = tree.setdefault(aid, {"classes": {}, "total": 0.0})
        cls_bucket = acct_bucket["classes"].setdefault(class_key, {
            "key": class_key, "label": class_label, "total": 0.0, "types": {}
        })
        type_bucket = cls_bucket["types"].setdefault(type_key, {
            "key": type_key, "label": type_label, "total": 0.0, "rows": []
        })
        type_bucket["rows"].append({
            "kind": r["position_kind"],
            "entity_id": r["entity_id"],
            "name": r["name"],
            "currency": r["currency"],
            "country": r["country"],
            "quantity": float(r["quantity"]) if r["quantity"] is not None else None,
            "price_local": float(r["price_local"]) if r["price_local"] is not None else None,
            "value_local": float(r["value_local"] or 0),
            "value_base": float(r["value_base"] or 0),
            "tags": r["tags"] or [],
            "instrument_type": r["instrument_type"],
            "asset_class": r["asset_class"],
        })
        # Liabilities subtract from net worth — keep their value positive in the tree
        # (so users see the actual amount owed) but track sign in totals.
        signed = -float(r["value_base"] or 0) if r["position_kind"] == "liability" else float(r["value_base"] or 0)
        type_bucket["total"] += float(r["value_base"] or 0)
        cls_bucket["total"] += float(r["value_base"] or 0)
        acct_bucket["total"] += signed

    # Flatten + sort: accounts → classes → types
    out_accounts: list[dict] = []
    for aid, ab in tree.items():
        meta = acct_meta.get(aid, acct_meta[None])
        classes_out = []
        for cls_key, cls in ab["classes"].items():
            types_out = sorted(cls["types"].values(), key=lambda t: -t["total"])
            classes_out.append({
                "key": cls["key"], "label": cls["label"],
                "total": cls["total"], "types": types_out,
            })
        # Order: liabilities last, cash near the end, asset classes by total desc.
        def _cls_order(c):
            if c["key"] == "_liability":
                return (2, -c["total"])
            if c["key"] == "_cash":
                return (1, -c["total"])
            return (0, -c["total"])
        classes_out.sort(key=_cls_order)
        out_accounts.append({**meta, "total": ab["total"], "classes": classes_out})

    out_accounts.sort(key=lambda a: a["sort_key"])

    # Totals across the whole tree
    assets = sum(
        t["total"]
        for a in out_accounts for cls in a["classes"]
        if cls["key"] not in ("_cash", "_liability")
        for t in cls["types"]
    )
    cash = sum(
        t["total"]
        for a in out_accounts for cls in a["classes"] if cls["key"] == "_cash"
        for t in cls["types"]
    )
    liabilities = sum(
        t["total"]
        for a in out_accounts for cls in a["classes"] if cls["key"] == "_liability"
        for t in cls["types"]
    )

    return {
        "accounts": out_accounts,
        "totals": {
            "assets": assets,
            "cash": cash,
            "liabilities": liabilities,
            "net_worth": assets + cash - liabilities,
        },
    }


@router.get("/holdings")
def holdings(request: Request, scope: str = "all"):
    c = request.app.state.container
    base = c.config.reporting.base_currency
    scope_label, account_ids, scope_kind = parse_scope(scope, c)

    tree = _build_position_tree(c, base, account_ids)

    # Forms still need lookups for the add-asset / add-cash drawers.
    assets_list = c.portfolio.list_assets(include_inactive=True)
    cash_list = c.portfolio.list_cash(include_inactive=True)
    accounts = c.accounts_repo.list_active()
    groups = c.account_groups_repo.list_active()

    return request.app.state.templates.TemplateResponse(
        request,
        "holdings.html",
        {
            "request": request,
            "tree": tree,
            "assets": assets_list,
            "cash_accounts": cash_list,
            "accounts": accounts,
            "groups": groups,
            "instrument_types": [t.value for t in InstrumentType],
            "asset_classes": [t.value for t in AssetClass],
            "scope": scope,
            "scope_label": scope_label,
            "scope_kind": scope_kind,
        },
    )
