from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .schema import MIGRATIONS

if TYPE_CHECKING:
    from .connection import Database

log = logging.getLogger(__name__)


def _current_version(db: Database) -> int:
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    row = db.fetchone("SELECT COALESCE(MAX(version), 0) FROM schema_version")
    return int(row[0]) if row else 0


def apply_migrations(db: Database) -> None:
    current = _current_version(db)
    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        log.debug("schema up to date at v%d", current)
        return
    for version, sql in pending:
        log.info("applying migration v%d", version)
        with db.begin() as txn:
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                txn.execute(stmt)
            txn.execute("INSERT INTO schema_version (version) VALUES (?)", [version])
    log.info("schema now at v%d", MIGRATIONS[-1][0])
