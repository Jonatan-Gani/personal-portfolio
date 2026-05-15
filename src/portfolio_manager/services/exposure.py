from __future__ import annotations

from ..db.connection import Database


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
             WHERE p.snapshot_id = ?
               AND v.currency = ?
               {kind_filter}
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

    def by_tag(self, currency: str, snapshot_id: str | None = None) -> list[dict]:
        snap_id = snapshot_id or self._latest_snapshot_id()
        if not snap_id:
            return []
        rows = self.db.fetchall_dict(
            """
            SELECT tag,
                   SUM(v.value) AS value,
                   COUNT(DISTINCT p.entity_id) AS positions
              FROM snapshot_positions p,
                   UNNEST(p.tags) AS t(tag)
              JOIN snapshot_position_values v
                ON v.snapshot_id = p.snapshot_id
               AND v.position_kind = p.position_kind
               AND v.entity_id = p.entity_id
             WHERE p.snapshot_id = ?
               AND v.currency = ?
             GROUP BY tag
             ORDER BY value DESC
            """,
            [snap_id, currency.upper()],
        )
        total = sum(float(r["value"] or 0) for r in rows)
        for r in rows:
            r["value"] = float(r["value"] or 0)
            r["share"] = (r["value"] / total) if total else 0.0
        return rows

    def _latest_snapshot_id(self) -> str | None:
        row = self.db.fetchone("SELECT snapshot_id FROM snapshots ORDER BY taken_at DESC LIMIT 1")
        return row[0] if row else None
