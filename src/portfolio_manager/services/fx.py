from __future__ import annotations

import logging
from datetime import date

from ..domain.exceptions import FXRateUnavailable
from ..domain.models import FXRate, Transaction
from ..providers.base import FXProvider
from ..repositories.prices import FXRateCache

log = logging.getLogger(__name__)


class FXService:
    """Provides cached, base-currency-normalized FX rates. Caches to DuckDB so snapshots are reproducible."""

    def __init__(
        self,
        provider: FXProvider,
        cache: FXRateCache,
        cache_ttl_hours: int = 12,
    ):
        self.provider = provider
        self.cache = cache
        self.cache_ttl_hours = cache_ttl_hours

    def rate(self, base: str, quote: str, as_of: date | None = None) -> float:
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return 1.0
        rate_date = as_of or date.today()
        cached = self.cache.get_fresh(rate_date, base, quote, self.provider.name, self.cache_ttl_hours)
        if cached is not None:
            return cached
        try:
            rate = self.provider.get_rate(base, quote, as_of)
        except FXRateUnavailable:
            log.warning("FX provider failed for %s->%s; checking any-age cache", base, quote)
            stale = self.cache.get_fresh(
                rate_date, base, quote, self.provider.name, ttl_hours=24 * 365 * 10
            )
            if stale is not None:
                return stale
            raise
        self.cache.upsert(
            FXRate(
                rate_date=rate_date,
                base_currency=base,
                quote_currency=quote,
                rate=rate,
                provider=self.provider.name,
            )
        )
        return rate

    def rates_from_base(
        self, base: str, quotes: list[str], as_of: date | None = None
    ) -> dict[str, float]:
        return {q: self.rate(base, q, as_of) for q in quotes}

    def convert(self, amount: float, from_ccy: str, to_ccy: str, as_of: date | None = None) -> float:
        return amount * self.rate(from_ccy, to_ccy, as_of)

    def stamp_transaction(self, tx: Transaction, base_currency: str) -> Transaction:
        """Pin the FX rate at the transaction's inception onto the row, so cost
        basis and returns can be measured against the rate that was true then.

        Mutates and returns `tx`. Never raises: if the provider is unreachable
        the rate is left None — capturing FX must not block recording activity.
        """
        base = base_currency.upper()
        tx.fx_base_currency = base
        try:
            tx.fx_rate_to_base = self.rate(tx.currency, base, tx.transaction_date)
        except FXRateUnavailable:
            log.warning(
                "no FX rate %s->%s at %s; transaction %s recorded without a pinned rate",
                tx.currency, base, tx.transaction_date, tx.transaction_id,
            )
            tx.fx_rate_to_base = None
        return tx
