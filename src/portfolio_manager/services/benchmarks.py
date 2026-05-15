from __future__ import annotations

import logging
from datetime import date, timedelta

from ..domain.exceptions import PriceUnavailable
from ..domain.models import Benchmark, Price
from ..providers.base import PriceProvider
from ..providers.registry import build_price_provider
from ..repositories.benchmarks import BenchmarkRepository
from ..repositories.prices import PriceCache

log = logging.getLogger(__name__)


class BenchmarkService:
    """Benchmark CRUD + price-history maintenance.

    Each benchmark is a named pointer to a market index/ETF symbol. Daily prices are
    stored in `price_cache` (keyed by symbol+provider+date), so chart endpoints can
    pull a clean time series with one indexed query.
    """

    def __init__(
        self,
        repo: BenchmarkRepository,
        price_cache: PriceCache,
        default_price_provider: PriceProvider,
    ):
        self.repo = repo
        self.price_cache = price_cache
        self.default_price_provider = default_price_provider

    # -------------------------------------------------------------- CRUD
    def list_active(self) -> list[Benchmark]:
        return self.repo.list_active()

    def list_all(self) -> list[Benchmark]:
        return self.repo.list_all()

    def get(self, benchmark_id: str) -> Benchmark:
        return self.repo.get(benchmark_id)

    def add(self, b: Benchmark, *, backfill_days: int = 365) -> Benchmark:
        self.repo.upsert(b)
        if backfill_days > 0:
            try:
                self.backfill_history(b, days=backfill_days)
            except Exception as e:
                log.warning("backfill failed for %s: %s", b.symbol, e)
        return b

    def update(self, b: Benchmark) -> Benchmark:
        return self.repo.upsert(b)

    def deactivate(self, benchmark_id: str) -> None:
        self.repo.deactivate(benchmark_id)

    def delete(self, benchmark_id: str) -> None:
        self.repo.delete(benchmark_id)

    # -------------------------------------------------------------- price history
    def _provider_for(self, b: Benchmark) -> PriceProvider:
        if b.price_provider:
            return build_price_provider(b.price_provider, {})
        return self.default_price_provider

    def backfill_history(self, b: Benchmark, days: int = 365) -> int:
        """Fetch ~`days` days of daily history for this benchmark and persist to price_cache.
        Returns the number of rows written."""
        provider = self._provider_for(b)
        start = date.today() - timedelta(days=days)
        quotes = provider.get_history(b.symbol, start)
        for q in quotes:
            self.price_cache.upsert(
                Price(
                    price_date=q.as_of,
                    symbol=b.symbol,
                    currency=(q.currency or b.currency).upper(),
                    price=q.price,
                    provider=provider.name,
                )
            )
        log.info("benchmark %s: backfilled %d quotes", b.symbol, len(quotes))
        return len(quotes)

    def record_today(self, b: Benchmark) -> Price | None:
        """Fetch today's price for this benchmark and persist. Used by SnapshotService."""
        provider = self._provider_for(b)
        try:
            quote = provider.get_price(b.symbol)
        except PriceUnavailable as e:
            log.warning("benchmark %s: cannot record today's price: %s", b.symbol, e)
            return None
        price = Price(
            price_date=quote.as_of,
            symbol=b.symbol,
            currency=(quote.currency or b.currency).upper(),
            price=quote.price,
            provider=provider.name,
        )
        self.price_cache.upsert(price)
        return price

    def record_today_for_all(self) -> int:
        n = 0
        for b in self.list_active():
            if self.record_today(b) is not None:
                n += 1
        return n

    def history(self, b: Benchmark, since: date | None = None) -> list[tuple[date, float, str]]:
        return self.price_cache.history(b.symbol, since=since)

    # -------------------------------------------------------------- seeding
    def seed_defaults_if_empty(self, *, backfill: bool = True) -> Benchmark | None:
        """Create the S&P 500 as a default benchmark if no benchmarks exist yet."""
        if self.repo.list_all():
            return None
        b = Benchmark(
            name="S&P 500",
            symbol="^GSPC",
            currency="USD",
            country="US",
            notes="Default benchmark — seeded by init-db.",
        )
        self.repo.upsert(b)
        if backfill:
            try:
                self.backfill_history(b, days=365)
            except Exception as e:
                log.warning("seed backfill of %s failed: %s", b.symbol, e)
        return b
