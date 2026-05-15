from __future__ import annotations

from datetime import date
from typing import Any

from ..domain.exceptions import FXRateUnavailable, PriceUnavailable
from .base import FXProvider, PriceProvider, PriceQuote
from .registry import register_fx, register_price


class MockFxProvider(FXProvider):
    """Deterministic FX provider for tests / offline use. USD-based by convention."""

    name = "mock"

    DEFAULT_USD_RATES: dict[str, float] = {
        "USD": 1.0,
        "EUR": 0.92,
        "GBP": 0.79,
        "SEK": 10.5,
        "ILS": 3.7,
        "JPY": 150.0,
    }

    def __init__(self, usd_rates: dict[str, float] | None = None):
        self.usd_rates = {**self.DEFAULT_USD_RATES, **(usd_rates or {})}

    def get_rate(self, base: str, quote: str, as_of: date | None = None) -> float:
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return 1.0
        if base not in self.usd_rates or quote not in self.usd_rates:
            raise FXRateUnavailable(f"mock has no rate for {base}->{quote}")
        return self.usd_rates[quote] / self.usd_rates[base]


class MockPriceProvider(PriceProvider):
    name = "mock"

    def __init__(self, prices: dict[str, tuple[float, str]] | None = None):
        self.prices = prices or {}

    def get_price(self, symbol: str, as_of: date | None = None) -> PriceQuote:
        if symbol not in self.prices:
            raise PriceUnavailable(f"mock has no price for {symbol}")
        price, ccy = self.prices[symbol]
        return PriceQuote(symbol=symbol, price=price, currency=ccy, as_of=as_of or date.today())

    def get_history(self, symbol: str, start: date, end: date | None = None) -> list[PriceQuote]:
        if symbol not in self.prices:
            return []
        from datetime import timedelta
        end = end or date.today()
        price, ccy = self.prices[symbol]
        out: list[PriceQuote] = []
        d = start
        while d <= end:
            out.append(PriceQuote(symbol=symbol, price=price, currency=ccy, as_of=d))
            d = d + timedelta(days=1)
        return out


@register_fx("mock")
def _fx_factory(opts: dict[str, Any]) -> FXProvider:
    return MockFxProvider(usd_rates=opts.get("usd_rates"))


@register_price("mock")
def _price_factory(opts: dict[str, Any]) -> PriceProvider:
    return MockPriceProvider(prices=opts.get("prices"))
