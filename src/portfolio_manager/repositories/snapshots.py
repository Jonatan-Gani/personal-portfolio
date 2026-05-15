from __future__ import annotations

from ..db.connection import Database
from ..domain.exceptions import NotFoundError
from ..domain.models import SnapshotMeta, SnapshotPosition


class SnapshotRepository:
    def __init__(self, db: Database):
        self.db = db

    def insert_snapshot(
        self,
        meta: SnapshotMeta,
        positions: list[SnapshotPosition],
        fx_rates_from_base: dict[str, float],
    ) -> SnapshotMeta:
        with self.db.begin() as txn:
            txn.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id, taken_at, base_currency, reporting_currencies,
                    total_assets_base, total_liabilities_base, total_cash_base, net_worth_base, notes
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                [
                    meta.snapshot_id, meta.taken_at, meta.base_currency, meta.reporting_currencies,
                    meta.total_assets_base, meta.total_liabilities_base, meta.total_cash_base,
                    meta.net_worth_base, meta.notes,
                ],
            )
            for p in positions:
                txn.execute(
                    """
                    INSERT INTO snapshot_positions (
                        snapshot_id, position_kind, entity_id, name, instrument_type, asset_class,
                        currency, country, sector, quantity, price_local, value_local, tags
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        p.snapshot_id, p.position_kind.value, p.entity_id, p.name,
                        p.instrument_type, p.asset_class, p.currency, p.country, p.sector,
                        p.quantity, p.price_local, p.value_local, p.tags,
                    ],
                )
                for ccy, value in p.values_by_currency.items():
                    txn.execute(
                        """
                        INSERT INTO snapshot_position_values (
                            snapshot_id, position_kind, entity_id, currency, value, fx_rate_from_base
                        ) VALUES (?,?,?,?,?,?)
                        """,
                        [
                            p.snapshot_id, p.position_kind.value, p.entity_id,
                            ccy, value, fx_rates_from_base.get(ccy, 1.0),
                        ],
                    )
        return meta

    def list_snapshots(self, limit: int = 100) -> list[SnapshotMeta]:
        rows = self.db.fetchall_dict(
            "SELECT * FROM snapshots ORDER BY taken_at DESC LIMIT ?", [limit]
        )
        return [SnapshotMeta.model_validate(r) for r in rows]

    def get_meta(self, snapshot_id: str) -> SnapshotMeta:
        rows = self.db.fetchall_dict("SELECT * FROM snapshots WHERE snapshot_id = ?", [snapshot_id])
        if not rows:
            raise NotFoundError(f"snapshot {snapshot_id!r} not found")
        return SnapshotMeta.model_validate(rows[0])

    def latest(self) -> SnapshotMeta | None:
        rows = self.db.fetchall_dict("SELECT * FROM snapshots ORDER BY taken_at DESC LIMIT 1")
        return SnapshotMeta.model_validate(rows[0]) if rows else None

    def positions_with_values(self, snapshot_id: str) -> list[dict]:
        positions = self.db.fetchall_dict(
            """
            SELECT *
              FROM snapshot_positions
             WHERE snapshot_id = ?
             ORDER BY position_kind, name
            """,
            [snapshot_id],
        )
        values = self.db.fetchall_dict(
            """
            SELECT position_kind, entity_id, currency, value
              FROM snapshot_position_values
             WHERE snapshot_id = ?
            """,
            [snapshot_id],
        )
        by_key: dict[tuple[str, str], dict[str, float]] = {}
        for v in values:
            by_key.setdefault((v["position_kind"], v["entity_id"]), {})[v["currency"]] = float(v["value"])
        for p in positions:
            p["values_by_currency"] = by_key.get((p["position_kind"], p["entity_id"]), {})
        return positions
