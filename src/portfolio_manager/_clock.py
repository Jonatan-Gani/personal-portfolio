from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Naive UTC datetime — kept naive so DuckDB TIMESTAMP comparisons stay simple."""
    return datetime.now(UTC).replace(tzinfo=None)
