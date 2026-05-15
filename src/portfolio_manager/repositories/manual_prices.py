from __future__ import annotations

from datetime import datetime

from ..db.connection import Database
from ..domain.exceptions import NotFoundError
from ..domain.models import ManualPriceOverride


class ManualPriceOverrideRepository:
    """Stores user-entered prices for assets that lack a live-quotable symbol.
    The latest observation before a snapshot's taken_at is what the snapshot uses."""

    def __init__(self, db: Database):
        self.db = db

    def insert(self, override: ManualPriceOverride) -> ManualPriceOverride:
        self.db.execute(
            """
            INSERT INTO manual_price_overrides (
                override_id, asset_id, observed_at, price, currency, notes, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            [
                override.override_id, override.asset_id, override.observed_at,
                override.price, override.currency, override.notes, override.created_at,
            ],
        )
        return override

    def update(self, override: ManualPriceOverride) -> ManualPriceOverride:
        self.db.execute(
            """
            UPDATE manual_price_overrides SET
                asset_id = ?, observed_at = ?, price = ?, currency = ?, notes = ?
            WHERE override_id = ?
            """,
            [
                override.asset_id, override.observed_at, override.price,
                override.currency, override.notes, override.override_id,
            ],
        )
        return override

    def delete(self, override_id: str) -> None:
        self.db.execute("DELETE FROM manual_price_overrides WHERE override_id = ?", [override_id])

    def get(self, override_id: str) -> ManualPriceOverride:
        rows = self.db.fetchall_dict(
            "SELECT * FROM manual_price_overrides WHERE override_id = ?", [override_id]
        )
        if not rows:
            raise NotFoundError(f"manual price override {override_id!r} not found")
        return ManualPriceOverride.model_validate(rows[0])

    def list_for_asset(self, asset_id: str) -> list[ManualPriceOverride]:
        rows = self.db.fetchall_dict(
            """
            SELECT * FROM manual_price_overrides
             WHERE asset_id = ?
             ORDER BY observed_at DESC
            """,
            [asset_id],
        )
        return [ManualPriceOverride.model_validate(r) for r in rows]

    def latest_before(self, asset_id: str, when: datetime) -> ManualPriceOverride | None:
        rows = self.db.fetchall_dict(
            """
            SELECT * FROM manual_price_overrides
             WHERE asset_id = ? AND observed_at <= ?
             ORDER BY observed_at DESC
             LIMIT 1
            """,
            [asset_id, when],
        )
        return ManualPriceOverride.model_validate(rows[0]) if rows else None
