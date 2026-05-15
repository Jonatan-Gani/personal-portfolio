from __future__ import annotations

import logging
import threading
from pathlib import Path

import duckdb

from .migrations import apply_migrations

log = logging.getLogger(__name__)


class Database:
    """Thin wrapper around a single DuckDB connection. DuckDB connections are not thread-safe;
    we serialize access with a lock so the FastAPI app can share one DB.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = duckdb.connect(self.path)
        log.info("opened DuckDB at %s", self.path)

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def conn(self) -> duckdb.DuckDBPyConnection:
        return self._con

    def execute(self, sql: str, params: list | tuple | None = None):
        with self._lock:
            return self._con.execute(sql, params or [])

    def executemany(self, sql: str, seq_of_params):
        with self._lock:
            return self._con.executemany(sql, seq_of_params)

    def fetchone(self, sql: str, params: list | tuple | None = None):
        with self._lock:
            return self._con.execute(sql, params or []).fetchone()

    def fetchall(self, sql: str, params: list | tuple | None = None):
        with self._lock:
            return self._con.execute(sql, params or []).fetchall()

    def fetchall_dict(self, sql: str, params: list | tuple | None = None) -> list[dict]:
        with self._lock:
            cur = self._con.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def begin(self):
        return _Transaction(self)


class _Transaction:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db._lock.acquire()
        self.db._con.execute("BEGIN")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db._con.execute("COMMIT")
            else:
                self.db._con.execute("ROLLBACK")
        finally:
            self.db._lock.release()


_singleton: Database | None = None


def get_database(path: str | Path | None = None) -> Database:
    global _singleton
    if _singleton is None:
        if path is None:
            raise RuntimeError("first call to get_database() must pass a path")
        _singleton = Database(path)
        apply_migrations(_singleton)
    return _singleton


def reset_database_singleton() -> None:
    global _singleton
    if _singleton is not None:
        _singleton.close()
    _singleton = None
