from __future__ import annotations

from ..db.connection import Database
from .scope import scope_filter_sql, scope_join_sql


class ExposureService:
    """Group-by exposures over the latest (or any) snapshot, in any reporting currency."""

    def __init__(self, db: Database):
        self.db = db

    def by_dimension(
        self,
        dimension: str,
        currency: str,
        snapshot_id: str | None = None,
        kinds: list[str] | None = None,
        account_ids: list[str] | None = None,
    ) -> list[dict]:
        allowed = {"currency", "country", "sector", "instrument_type", "asset_class", "position_kind"}
        if dimension not in allowed:
            raise ValueError(f"dimension must be one of {sorted(allowed)}")
        snap_id = snapshot_id or self._latest_snapshot_id()
        if not snap_id:
            return []
        kind_filter = ""
        params: list = [snap_id, currency.upper()]
        if kinds:
            placeholders = ",".join(["?"] * len(kinds))
            kind_filter = f"AND p.position_kind IN ({placeholders})"
            params.extend(kinds)

        scope_where, scope_params = scope_filter_sql(account_ids)
        joins = scope_join_sql() if account_ids is not None else ""
        params.extend(scope_params)

        rows = self.db.fetchall_dict(
            f"""
            SELECT COALESCE(p.{dimension}, '(none)') AS bucket,
                   SUM(v.value) AS value,
                   COUNT(DISTINCT p.entity_id) AS positions
              FROM snapshot_positions p
              JOIN snapshot_position_values v
                ON v.snapshot_id = p.snapshot_id
               AND v.position_kind = p.position_kind
               AND v.entity_id = p.entity_id
              {joins}
             WHERE p.snapshot_id = ?
               AND v.currency = ?
               {kind_filter}
               {scope_where}
             GROUP BY bucket
             ORDER BY value DESC
            """,
            params,
        )
        total = sum(float(r["value"] or 0) for r in rows)
        for r in rows:
            r["value"] = float(r["value"] or 0)
            r["share"] = (r["value"] / total) if total else 0.0
        return rows

    def by_tag(
        self,
        currency: str,
        snapshot_id: str | None = None,
        account_ids: list[str] | None = None,
    ) -> list[dict]:
        snap_id = snapshot_id or self._latest_snapshot_id()
        if not snap_id:
            return []
        scope_where, scope_params = scope_filter_sql(account_ids)
        joins = scope_join_sql() if account_ids is not None else ""
        rows = self.db.fetchall_dict(
            f"""
            SELECT tag,
                   SUM(v.value) AS value,
                   COUNT(DISTINCT p.entity_id) AS positions
              FROM snapshot_positions p,
                   UNNEST(p.tags) AS t(tag)
              JOIN snapshot_position_values v
                ON v.snapshot_id = p.snapshot_id
               AND v.position_kind = p.position_kind
               AND v.entity_id = p.entity_id
              {joins}
             WHERE p.snapshot_id = ?
               AND v.currency = ?
               {scope_where}
             GROUP BY tag
             ORDER BY value DESC
            """,
            [snap_id, currency.upper(), *scope_params],
        )
        total = sum(float(r["value"] or 0) for r in rows)
        for r in rows:
            r["value"] = float(r["value"] or 0)
            r["share"] = (r["value"] / total) if total else 0.0
        return rows

    def latest_totals(
        self,
        currency: str,
        account_ids: list[str] | None = None,
    ) -> dict:
        """Net worth / assets / cash / liabilities totals for the latest snapshot,
        in `currency`, optionally scoped to a set of accounts.

        Returns {snapshot_id, taken_at, assets, cash, liabilities, net_worth}, or
        an empty dict if no snapshot exists yet."""
        row = self.db.fetchone(
            "SELECT snapshot_id, taken_at FROM snapshots ORDER BY taken_at DESC LIMIT 1"
        )
        if not row:
            return {}
        snap_id, taken_at = row
        scope_where, scope_params = scope_filter_sql(account_ids)
        joins = scope_join_sql() if account_ids is not None else ""
        agg = self.db.fetchone(
            f"""
            SELECT
              COALESCE(SUM(CASE WHEN p.position_kind = 'asset'     THEN v.value ELSE 0 END), 0),
              COALESCE(SUM(CASE WHEN p.position_kind = 'cash'      THEN v.value ELSE 0 END), 0),
              COALESCE(SUM(CASE WHEN p.position_kind = 'liability' THEN v.value ELSE 0 END), 0)
            FROM snapshot_positions p
            JOIN snapshot_position_values v
              ON v.snapshot_id = p.snapshot_id
             AND v.position_kind = p.position_kind
             AND v.entity_id = p.entity_id
            {joins}
            WHERE p.snapshot_id = ?
              AND v.currency = ?
              {scope_where}
            """,
            [snap_id, currency.upper(), *scope_params],
        )
        assets = float(agg[0] or 0)
        cash = float(agg[1] or 0)
        liabilities = float(agg[2] or 0)
        return {
            "snapshot_id": snap_id,
            "taken_at": taken_at,
            "assets": assets,
            "cash": cash,
            "liabilities": liabilities,
            "net_worth": assets + cash - liabilities,
        }

    def _latest_snapshot_id(self) -> str | None:
        row = self.db.fetchone("SELECT snapshot_id FROM snapshots ORDER BY taken_at DESC LIMIT 1")
        return row[0] if row else None
