from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass
class PriceQuote:
    symbol: str
    price: float
    currency: str
    as_of: date


class PriceProvider(ABC):
    """Source of last-traded prices for an instrument symbol."""

    name: str

    @abstractmethod
    def get_price(self, symbol: str, as_of: date | None = None) -> PriceQuote: ...

    def get_prices(self, symbols: list[str], as_of: date | None = None) -> dict[str, PriceQuote]:
        out: dict[str, PriceQuote] = {}
        for s in symbols:
            try:
                out[s] = self.get_price(s, as_of=as_of)
            except Exception:  # noqa: BLE001
                continue
        return out

    def get_history(
        self, symbol: str, start: date, end: date | None = None
    ) -> list[PriceQuote]:
        """Return daily quotes from `start` to `end` (inclusive). Default returns empty —
        providers that can do bulk history (e.g. yfinance) override this."""
        return []


class FXProvider(ABC):
    """Source of FX rates expressed as 1 base = X quote, for any base currency the caller chooses."""

    name: str

    @abstractmethod
    def get_rate(self, base: str, quote: str, as_of: date | None = None) -> float: ...

    def get_rates(self, base: str, quotes: list[str], as_of: date | None = None) -> dict[str, float]:
        out: dict[str, float] = {}
        for q in quotes:
            out[q] = self.get_rate(base, q, as_of=as_of)
        return out
