"""Permanent end-of-day price history.

The rest of the app talks only to the `PriceHistoryStore` interface — never to a
concrete database. Swapping the backend (for example to your own locally-run
database) means writing one new `PriceHistoryStore` subclass and selecting it by
name in config; no other code changes.

This is distinct from the short-lived quote cache (`PriceCache`): that holds
recent live quotes with a TTL, this is a growing, permanent daily series used to
value any holding on any past date.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from ..db.connection import Database


@dataclass
class EodPrice:
    symbol: str
    price_date: date
    price: float
    currency: str
    kind: str = "asset"          # 'asset' | 'index'
    source: str | None = None


class PriceHistoryStore(ABC):
    """Append/upsert daily prices and read them back as-of a date or as a range."""

    name: str

    @abstractmethod
    def record(self, price: EodPrice) -> None:
        """Insert or replace one symbol/date price."""

    def record_many(self, prices: list[EodPrice]) -> None:
        for p in prices:
            self.record(p)

    @abstractmethod
    def get_asof(self, symbol: str, on_or_before: date) -> EodPrice | None:
        """Most recent stored price for `symbol` on or before the given date."""

    @abstractmethod
    def series(self, symbol: str, start: date, end: date) -> list[EodPrice]:
        """All stored prices for `symbol` within [start, end], oldest first."""

    @abstractmethod
    def latest(self, symbol: str) -> EodPrice | None:
        """The newest stored price for `symbol`."""

    @abstractmethod
    def has(self, symbol: str, price_date: date) -> bool:
        """Whether a price is already stored for this symbol/date."""


def _row_to_eod(row) -> EodPrice | None:
    if not row:
        return None
    return EodPrice(
        symbol=row[0], price_date=row[1], price=float(row[2]),
        currency=row[3], kind=row[4], source=row[5],
    )


class DuckDBPriceHistoryStore(PriceHistoryStore):
    """Default backend — the local DuckDB `price_history` table."""

    name = "duckdb"

    _COLS = "symbol, price_date, price, currency, kind, source"

    def __init__(self, db: Database):
        self.db = db

    def record(self, price: EodPrice) -> None:
        self.db.execute(
            """
            INSERT INTO price_history (symbol, price_date, price, currency, kind, source)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT (symbol, price_date) DO UPDATE SET
                price = EXCLUDED.price,
                currency = EXCLUDED.currency,
                kind = EXCLUDED.kind,
                source = EXCLUDED.source
            """,
            [price.symbol.upper(), price.price_date, float(price.price),
             price.currency.upper(), price.kind, price.source],
        )

    def get_asof(self, symbol: str, on_or_before: date) -> EodPrice | None:
        return _row_to_eod(self.db.fetchone(
            f"SELECT {self._COLS} FROM price_history "
            "WHERE symbol = ? AND price_date <= ? ORDER BY price_date DESC LIMIT 1",
            [symbol.upper(), on_or_before],
        ))

    def series(self, symbol: str, start: date, end: date) -> list[EodPrice]:
        rows = self.db.fetchall(
            f"SELECT {self._COLS} FROM price_history "
            "WHERE symbol = ? AND price_date >= ? AND price_date <= ? "
            "ORDER BY price_date ASC",
            [symbol.upper(), start, end],
        )
        return [_row_to_eod(r) for r in rows]

    def latest(self, symbol: str) -> EodPrice | None:
        return _row_to_eod(self.db.fetchone(
            f"SELECT {self._COLS} FROM price_history "
            "WHERE symbol = ? ORDER BY price_date DESC LIMIT 1",
            [symbol.upper()],
        ))

    def has(self, symbol: str, price_date: date) -> bool:
        return self.db.fetchone(
            "SELECT 1 FROM price_history WHERE symbol = ? AND price_date = ?",
            [symbol.upper(), price_date],
        ) is not None


# Backend registry — add a class here and select it via config.history.backend.
_BACKENDS: dict[str, type[PriceHistoryStore]] = {
    "duckdb": DuckDBPriceHistoryStore,
}


def build_price_history_store(backend: str, db: Database) -> PriceHistoryStore:
    impl = _BACKENDS.get(backend)
    if impl is None:
        from ..domain.exceptions import ConfigError
        raise ConfigError(
            f"unknown price-history backend {backend!r}; known: {sorted(_BACKENDS)}"
        )
    return impl(db)
