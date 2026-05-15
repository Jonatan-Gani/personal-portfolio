from __future__ import annotations

from ..db.connection import Database


class ReturnsService:
    """Period returns between two snapshots, in any reporting currency.

    Two interpretations are supported:
      - "USD return on EUR assets": value EUR-currency assets in USD at each snapshot, compare.
      - "ILS return on ILS assets": value ILS-currency assets in ILS at each snapshot, compare.
    The `asset_currency` filter (optional) controls the *which positions* part; the `report_currency`
    parameter controls the *valued in what* part.
    """

    def __init__(self, db: Database):
        self.db = db

    def total_value(
        self,
        snapshot_id: str,
        report_currency: str,
        asset_currency: str | None = None,
        kinds: list[str] | None = None,
    ) -> float:
        params: list = [snapshot_id, report_currency.upper()]
        ccy_filter = ""
        if asset_currency:
            ccy_filter = "AND p.currency = ?"
            params.append(asset_currency.upper())
        kind_filter = ""
        if kinds:
            placeholders = ",".join(["?"] * len(kinds))
            kind_filter = f"AND p.position_kind IN ({placeholders})"
            params.extend(kinds)
        row = self.db.fetchone(
            f"""
            SELECT COALESCE(SUM(v.value), 0)
              FROM snapshot_positions p
              JOIN snapshot_position_values v
                ON v.snapshot_id = p.snapshot_id
               AND v.position_kind = p.position_kind
               AND v.entity_id = p.entity_id
             WHERE p.snapshot_id = ?
               AND v.currency = ?
               {ccy_filter}
               {kind_filter}
            """,
            params,
        )
        return float(row[0]) if row else 0.0

    def period_return(
        self,
        from_snapshot_id: str,
        to_snapshot_id: str,
        report_currency: str,
        asset_currency: str | None = None,
        kinds: list[str] | None = None,
    ) -> dict:
        v0 = self.total_value(from_snapshot_id, report_currency, asset_currency, kinds)
        v1 = self.total_value(to_snapshot_id, report_currency, asset_currency, kinds)
        abs_change = v1 - v0
        pct = (abs_change / v0) if v0 else None
        return {
            "from_snapshot_id": from_snapshot_id,
            "to_snapshot_id": to_snapshot_id,
            "report_currency": report_currency.upper(),
            "asset_currency": asset_currency.upper() if asset_currency else None,
            "kinds": kinds,
            "from_value": v0,
            "to_value": v1,
            "abs_change": abs_change,
            "pct_change": pct,
        }
