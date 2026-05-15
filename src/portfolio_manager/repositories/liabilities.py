from __future__ import annotations

from .._clock import utcnow
from ..db.connection import Database
from ..domain.exceptions import NotFoundError
from ..domain.models import Liability


class LiabilityRepository:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, liability: Liability) -> Liability:
        liability.updated_at = utcnow()
        self.db.execute(
            """
            INSERT INTO liabilities (
                liability_id, name, liability_type, currency, interest_rate,
                notes, tags, created_at, updated_at, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (liability_id) DO UPDATE SET
                name = EXCLUDED.name,
                liability_type = EXCLUDED.liability_type,
                currency = EXCLUDED.currency,
                interest_rate = EXCLUDED.interest_rate,
                notes = EXCLUDED.notes,
                tags = EXCLUDED.tags,
                updated_at = EXCLUDED.updated_at,
                is_active = EXCLUDED.is_active
            """,
            [
                liability.liability_id, liability.name, liability.liability_type.value,
                liability.currency, liability.interest_rate,
                liability.notes, liability.tags, liability.created_at, liability.updated_at,
                liability.is_active,
            ],
        )
        return liability

    def get(self, liability_id: str) -> Liability:
        row = self.db.fetchall_dict("SELECT * FROM liabilities WHERE liability_id = ?", [liability_id])
        if not row:
            raise NotFoundError(f"liability {liability_id!r} not found")
        return Liability.model_validate(row[0])

    def list_active(self) -> list[Liability]:
        rows = self.db.fetchall_dict("SELECT * FROM liabilities WHERE is_active = TRUE ORDER BY name")
        return [Liability.model_validate(r) for r in rows]

    def list_all(self) -> list[Liability]:
        rows = self.db.fetchall_dict("SELECT * FROM liabilities ORDER BY is_active DESC, name")
        return [Liability.model_validate(r) for r in rows]

    def deactivate(self, liability_id: str) -> None:
        self.db.execute(
            "UPDATE liabilities SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE liability_id = ?",
            [liability_id],
        )

    def delete(self, liability_id: str) -> None:
        self.db.execute("DELETE FROM liabilities WHERE liability_id = ?", [liability_id])
