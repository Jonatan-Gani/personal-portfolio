from __future__ import annotations

from fastapi import APIRouter, Request

from ...domain.enums import AssetClass, InstrumentType

router = APIRouter()


@router.get("/holdings")
def holdings(request: Request):
    c = request.app.state.container
    assets = c.portfolio.list_assets(include_inactive=True)
    cash_accounts = c.portfolio.list_cash(include_inactive=True)
    state = c.holdings.at()
    overrides = {a.asset_id: c.manual_prices_repo.list_for_asset(a.asset_id) for a in assets}

    # Per-asset cost basis & realized P&L (FIFO, in asset's local currency).
    cost = {a.asset_id: c.cost_basis.compute(a.asset_id) for a in assets}

    # Subtotals from the latest snapshot in the base currency. Snapshots already store
    # per-currency values, so this is one indexed lookup, not a recompute.
    latest = c.snapshots_repo.latest()
    base = c.config.reporting.base_currency
    investments_total = 0.0
    cash_total = 0.0
    if latest is not None:
        row = c.db.fetchone(
            """
            SELECT
                COALESCE(SUM(CASE WHEN p.position_kind = 'asset' THEN v.value ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.position_kind = 'cash'  THEN v.value ELSE 0 END), 0)
            FROM snapshot_positions p
            JOIN snapshot_position_values v
              ON v.snapshot_id = p.snapshot_id
             AND v.position_kind = p.position_kind
             AND v.entity_id = p.entity_id
             AND v.currency = ?
            WHERE p.snapshot_id = ?
            """,
            [base, latest.snapshot_id],
        )
        if row:
            investments_total = float(row[0] or 0)
            cash_total = float(row[1] or 0)

    return request.app.state.templates.TemplateResponse(
        request,
        "holdings.html",
        {
            "request": request,
            "assets": assets,
            "cash_accounts": cash_accounts,
            "quantities": state.asset_quantities,
            "balances": state.cash_balances,
            "overrides": overrides,
            "instrument_types": [t.value for t in InstrumentType],
            "asset_classes": [t.value for t in AssetClass],
            "investments_total": investments_total,
            "cash_total": cash_total,
            "total_assets": investments_total + cash_total,
            "cost": cost,
        },
    )
