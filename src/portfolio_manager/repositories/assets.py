from __future__ import annotations

from .._clock import utcnow
from ..db.connection import Database
from ..domain.exceptions import NotFoundError
from ..domain.models import Asset


class AssetRepository:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, asset: Asset) -> Asset:
        asset.updated_at = utcnow()
        self.db.execute(
            """
            INSERT INTO assets (
                asset_id, symbol, name, instrument_type, asset_class, currency, country, sector,
                price_provider, notes, tags, created_at, updated_at, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (asset_id) DO UPDATE SET
                symbol = EXCLUDED.symbol,
                name = EXCLUDED.name,
                instrument_type = EXCLUDED.instrument_type,
                asset_class = EXCLUDED.asset_class,
                currency = EXCLUDED.currency,
                country = EXCLUDED.country,
                sector = EXCLUDED.sector,
                price_provider = EXCLUDED.price_provider,
                notes = EXCLUDED.notes,
                tags = EXCLUDED.tags,
                updated_at = EXCLUDED.updated_at,
                is_active = EXCLUDED.is_active
            """,
            [
                asset.asset_id, asset.symbol, asset.name, asset.instrument_type.value,
                asset.asset_class.value, asset.currency, asset.country, asset.sector,
                asset.price_provider, asset.notes, asset.tags,
                asset.created_at, asset.updated_at, asset.is_active,
            ],
        )
        return asset

    def get(self, asset_id: str) -> Asset:
        row = self.db.fetchall_dict("SELECT * FROM assets WHERE asset_id = ?", [asset_id])
        if not row:
            raise NotFoundError(f"asset {asset_id!r} not found")
        return Asset.model_validate(row[0])

    def list_active(self) -> list[Asset]:
        rows = self.db.fetchall_dict("SELECT * FROM assets WHERE is_active = TRUE ORDER BY name")
        return [Asset.model_validate(r) for r in rows]

    def list_all(self) -> list[Asset]:
        rows = self.db.fetchall_dict("SELECT * FROM assets ORDER BY is_active DESC, name")
        return [Asset.model_validate(r) for r in rows]

    def deactivate(self, asset_id: str) -> None:
        self.db.execute(
            "UPDATE assets SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE asset_id = ?",
            [asset_id],
        )

    def delete(self, asset_id: str) -> None:
        self.db.execute("DELETE FROM assets WHERE asset_id = ?", [asset_id])
