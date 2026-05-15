from __future__ import annotations

from .._clock import utcnow
from ..db.connection import Database
from ..domain.models import TargetAllocation

VALID_DIMENSIONS = {
    "asset_class",
    "currency",
    "position_kind",
    "instrument_type",
    "country",
    "sector",
}


class TargetAllocationRepository:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, t: TargetAllocation) -> TargetAllocation:
        if t.dimension not in VALID_DIMENSIONS:
            raise ValueError(f"unknown dimension {t.dimension!r}")
        existing = self.db.fetchone(
            "SELECT target_id FROM target_allocations WHERE dimension = ? AND bucket = ?",
            [t.dimension, t.bucket],
        )
        now = utcnow()
        if existing:
            self.db.execute(
                """
                UPDATE target_allocations
                   SET target_weight = ?, notes = ?, updated_at = ?
                 WHERE target_id = ?
                """,
                [t.target_weight, t.notes, now, existing[0]],
            )
            t.target_id = existing[0]
            t.updated_at = now
            return t
        self.db.execute(
            """
            INSERT INTO target_allocations
                (target_id, dimension, bucket, target_weight, notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            [t.target_id, t.dimension, t.bucket, t.target_weight, t.notes, now, now],
        )
        t.created_at = now
        t.updated_at = now
        return t

    def delete(self, target_id: str) -> None:
        self.db.execute("DELETE FROM target_allocations WHERE target_id = ?", [target_id])

    def list_by_dimension(self, dimension: str) -> list[TargetAllocation]:
        rows = self.db.fetchall_dict(
            """
            SELECT * FROM target_allocations
             WHERE dimension = ?
             ORDER BY target_weight DESC, bucket ASC
            """,
            [dimension],
        )
        return [TargetAllocation.model_validate(r) for r in rows]

    def list_all(self) -> list[TargetAllocation]:
        rows = self.db.fetchall_dict(
            "SELECT * FROM target_allocations ORDER BY dimension, target_weight DESC"
        )
        return [TargetAllocation.model_validate(r) for r in rows]
