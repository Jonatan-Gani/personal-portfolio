from __future__ import annotations

from datetime import date, timedelta

from .._clock import utcnow

from ..db.connection import Database
from ..domain.models import FXRate, Price


class FXRateCache:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, rate: FXRate) -> None:
        self.db.execute(
            """
            INSERT INTO fx_rates_cache (rate_date, base_currency, quote_currency, rate, provider, fetched_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT (rate_date, base_currency, quote_currency, provider) DO UPDATE SET
                rate = EXCLUDED.rate,
                fetched_at = EXCLUDED.fetched_at
            """,
            [rate.rate_date, rate.base_currency, rate.quote_currency, rate.rate, rate.provider, utcnow()],
        )

    def get_fresh(
        self,
        rate_date: date,
        base: str,
        quote: str,
        provider: str,
        ttl_hours: int = 12,
    ) -> float | None:
        cutoff = utcnow() - timedelta(hours=ttl_hours)
        row = self.db.fetchone(
            """
            SELECT rate FROM fx_rates_cache
            WHERE rate_date = ? AND base_currency = ? AND quote_currency = ? AND provider = ? AND fetched_at >= ?
            """,
            [rate_date, base.upper(), quote.upper(), provider, cutoff],
        )
        return float(row[0]) if row else None


class PriceCache:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, price: Price) -> None:
        self.db.execute(
            """
            INSERT INTO price_cache (price_date, symbol, currency, price, provider, fetched_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT (price_date, symbol, provider) DO UPDATE SET
                price = EXCLUDED.price,
                currency = EXCLUDED.currency,
                fetched_at = EXCLUDED.fetched_at
            """,
            [price.price_date, price.symbol, price.currency, price.price, price.provider, utcnow()],
        )

    def get_fresh(
        self,
        price_date: date,
        symbol: str,
        provider: str,
        ttl_hours: int = 12,
    ) -> tuple[float, str] | None:
        cutoff = utcnow() - timedelta(hours=ttl_hours)
        row = self.db.fetchone(
            """
            SELECT price, currency FROM price_cache
            WHERE price_date = ? AND symbol = ? AND provider = ? AND fetched_at >= ?
            """,
            [price_date, symbol, provider, cutoff],
        )
        return (float(row[0]), row[1]) if row else None

    def history(
        self,
        symbol: str,
        since: date | None = None,
        until: date | None = None,
    ) -> list[tuple[date, float, str]]:
        """Return (date, price, currency) ordered ascending. No provider filter — uses
        the most recent observation per date across providers if duplicates exist."""
        clauses = ["symbol = ?"]
        params: list = [symbol]
        if since is not None:
            clauses.append("price_date >= ?")
            params.append(since)
        if until is not None:
            clauses.append("price_date <= ?")
            params.append(until)
        rows = self.db.fetchall(
            f"""
            SELECT price_date, price, currency
              FROM (
                SELECT price_date, price, currency,
                       ROW_NUMBER() OVER (PARTITION BY price_date ORDER BY fetched_at DESC) AS rn
                  FROM price_cache
                 WHERE {' AND '.join(clauses)}
              )
             WHERE rn = 1
             ORDER BY price_date ASC
            """,
            params,
        )
        return [(r[0], float(r[1]), r[2]) for r in rows]
