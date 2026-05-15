from __future__ import annotations

import json
from typing import Any

from ..db.connection import Database


class AppSettingsRepository:
    """Tiny key/value store for user-editable runtime settings. Values are
    JSON-encoded so callers can store strings, numbers, lists, dicts uniformly.
    Keys are namespaced by convention (e.g. 'reporting.base_currency')."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.fetchone("SELECT value FROM app_settings WHERE key = ?", [key])
        if not row:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value: Any) -> None:
        encoded = json.dumps(value)
        self.db.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = EXCLUDED.updated_at
            """,
            [key, encoded],
        )

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM app_settings WHERE key = ?", [key])

    def all(self) -> dict[str, Any]:
        rows = self.db.fetchall("SELECT key, value FROM app_settings")
        out: dict[str, Any] = {}
        for key, value in rows:
            try:
                out[key] = json.loads(value)
            except (TypeError, ValueError):
                out[key] = None
        return out
