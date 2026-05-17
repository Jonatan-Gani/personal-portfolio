from __future__ import annotations

from fastapi import APIRouter, Request

from ..._clock import utcnow
from ...domain.enums import AssetClass, InstrumentType
from ...services.scope import parse_scope

router = APIRouter()


def _in_scope(account_id: str | None, account_ids: list[str] | None) -> bool:
    """Whether a position's account passes the current scope filter."""
    if account_ids is None:
        return True
    if account_ids == ["__unassigned__"]:
        return account_id is None
    return account_id in account_ids


def _asset_price(c, asset, snap_price: dict[str, float]) -> float | None:
    """Best-effort current price: last snapshot price, else a live quote, else
    the latest manual override, else None (the quantity is still shown)."""
    if asset.asset_id in snap_price:
        return snap_price[asset.asset_id]
    if asset.symbol:
        try:
            return c.snapshot.price_provider.get_price(asset.symbol).price
        except Exception:
            pass
    ov = c.manual_prices_repo.latest_before(asset.asset_id, utcnow())
    return ov.price if ov else None


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

    Positions are derived live from the transaction log (HoldingsService) — a
    holding appears the moment its transaction is recorded. Prices are
    best-effort; a holding with no available price still shows its quantity.
    """
    state = c.holdings.at()
    assets = {a.asset_id: a for a in c.portfolio.list_assets(include_inactive=True)}
    cash = {x.cash_id: x for x in c.portfolio.list_cash(include_inactive=True)}
    liabs = {x.liability_id: x for x in c.portfolio.list_liabilities(include_inactive=True)}

    # Last known price per asset, from the most recent snapshot.
    snap_price: dict[str, float] = {}
    latest = c.snapshots_repo.latest()
    if latest is not None:
        for row in c.db.fetchall_dict(
            "SELECT entity_id, price_local FROM snapshot_positions "
            "WHERE snapshot_id = ? AND position_kind = 'asset'",
            [latest.snapshot_id],
        ):
            if row["price_local"] is not None:
                snap_price[row["entity_id"]] = float(row["price_local"])

    def _fx(ccy: str) -> float:
        try:
            return c.fx.rate(ccy, base_ccy)
        except Exception:
            return 1.0

    rows: list[dict] = []

    for aid, qty in state.asset_quantities.items():
        a = assets.get(aid)
        if abs(qty) < 1e-9 or a is None or not _in_scope(a.account_id, account_ids):
            continue
        price = _asset_price(c, a, snap_price)
        priced = price is not None
        value_local = qty * price if priced else 0.0
        rows.append({
            "position_kind": "asset", "entity_id": aid, "name": a.name,
            "currency": a.currency, "country": a.country, "quantity": qty,
            "price_local": price, "priced": priced, "value_local": value_local,
            "value_base": value_local * _fx(a.currency), "tags": a.tags,
            "instrument_type": a.instrument_type.value,
            "asset_class": a.asset_class.value, "account_id": a.account_id,
        })

    for cid, bal in state.cash_balances.items():
        x = cash.get(cid)
        if abs(bal) < 1e-9 or x is None or not _in_scope(x.account_id, account_ids):
            continue
        rows.append({
            "position_kind": "cash", "entity_id": cid, "name": x.account_name,
            "currency": x.currency, "country": x.country, "quantity": None,
            "price_local": None, "priced": True, "value_local": bal,
            "value_base": bal * _fx(x.currency), "tags": x.tags,
            "instrument_type": "cash", "asset_class": "cash",
            "account_id": x.account_id,
        })

    for lid, principal in state.liability_principals.items():
        x = liabs.get(lid)
        if abs(principal) < 1e-9 or x is None or not _in_scope(x.account_id, account_ids):
            continue
        rows.append({
            "position_kind": "liability", "entity_id": lid, "name": x.name,
            "currency": x.currency, "country": None, "quantity": None,
            "price_local": None, "priced": True, "value_local": principal,
            "value_base": principal * _fx(x.currency), "tags": x.tags,
            "instrument_type": x.liability_type.value, "asset_class": "other",
            "account_id": x.account_id,
        })

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
            "priced": r.get("priced", True),
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
        for cls in ab["classes"].values():
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
