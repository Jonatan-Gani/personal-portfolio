"""Market-watch service: fetches current and prior-day prices for a configurable
list of tickers, used by the dashboard's "Markets" widget.

Cached aggressively in `price_cache` (the existing table — symbols here just
live alongside user-asset prices). On failure, returns whatever's in the cache
even if stale, so the dashboard never breaks because the network blipped."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Optional

from .._clock import utcnow
from ..domain.exceptions import PriceUnavailable
from ..domain.models import Price
from ..providers.base import PriceProvider
from ..repositories.prices import PriceCache

log = logging.getLogger(__name__)


# Default watchlist. Friendlier labels than the raw Yahoo symbols.
DEFAULT_WATCHLIST: list[dict[str, str]] = [
    {"symbol": "^GSPC",     "label": "S&P 500"},
    {"symbol": "^IXIC",     "label": "Nasdaq Composite"},
    {"symbol": "^DJI",      "label": "Dow Jones"},
    {"symbol": "^STOXX50E", "label": "Euro Stoxx 50"},
    {"symbol": "BTC-USD",   "label": "Bitcoin"},
    {"symbol": "ETH-USD",   "label": "Ethereum"},
    {"symbol": "GC=F",      "label": "Gold (futures)"},
    {"symbol": "CL=F",      "label": "Crude oil"},
    {"symbol": "EURUSD=X",  "label": "EUR / USD"},
    {"symbol": "^TNX",      "label": "US 10-year yield"},
]


@dataclass
class MarketQuote:
    symbol: str
    label: str
    price: Optional[float]
    currency: Optional[str]
    prev_price: Optional[float]
    change_abs: Optional[float]
    change_pct: Optional[float]
    as_of: Optional[str]
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class MarketsService:
    """Snapshots a few headline tickers. Uses the same PriceProvider abstraction
    as everything else, so swapping providers transparently changes the source."""

    def __init__(self, provider: PriceProvider, cache: PriceCache, cache_ttl_minutes: int = 60):
        self.provider = provider
        self.cache = cache
        self.cache_ttl_minutes = cache_ttl_minutes

    def quote(self, symbol: str, label: str | None = None) -> MarketQuote:
        # Try cache first
        today = utcnow().date()
        cache_ttl_hours = max(1, self.cache_ttl_minutes // 60)
        cached_today = self.cache.get_fresh(today, symbol, self.provider.name, ttl_hours=cache_ttl_hours)

        # We want price + prev-day price. Either grab history (1 call) or two cache lookups.
        price: float | None = None
        currency: str | None = None
        prev_price: float | None = None
        as_of_str: str | None = None
        err: str | None = None

        if cached_today:
            price, currency = cached_today
            as_of_str = today.isoformat()
            # Look for a prior day price in cache: walk back up to 7 days.
            for back in range(1, 8):
                d = today - timedelta(days=back)
                hit = self.cache.get_fresh(d, symbol, self.provider.name, ttl_hours=24 * 30)
                if hit:
                    prev_price = hit[0]
                    break

        if price is None or prev_price is None:
            # Try the live provider for a small history window (gives us today + prev close).
            try:
                hist = self.provider.get_history(symbol, today - timedelta(days=10), today)
                if hist:
                    last = hist[-1]
                    price = last.price
                    currency = last.currency
                    as_of_str = last.as_of.isoformat() if last.as_of else None
                    # Persist for next time
                    for q in hist:
                        self.cache.upsert(Price(
                            price_date=q.as_of, symbol=q.symbol, currency=q.currency,
                            price=q.price, provider=self.provider.name,
                        ))
                    if len(hist) >= 2:
                        prev_price = hist[-2].price
                elif price is None:
                    err = "no data"
            except PriceUnavailable as e:
                err = str(e)
            except Exception as e:  # noqa: BLE001
                log.warning("market quote %s failed: %s", symbol, e)
                err = str(e)

        change_abs = (price - prev_price) if (price is not None and prev_price is not None) else None
        change_pct = (change_abs / prev_price) if (change_abs is not None and prev_price) else None

        return MarketQuote(
            symbol=symbol,
            label=label or symbol,
            price=price,
            currency=currency,
            prev_price=prev_price,
            change_abs=change_abs,
            change_pct=change_pct,
            as_of=as_of_str,
            error=err,
        )

    def watchlist(self, items: list[dict[str, str]] | None = None) -> list[MarketQuote]:
        items = items or DEFAULT_WATCHLIST
        out: list[MarketQuote] = []
        for it in items:
            sym = it.get("symbol")
            if not sym:
                continue
            out.append(self.quote(sym, label=it.get("label")))
        return out
