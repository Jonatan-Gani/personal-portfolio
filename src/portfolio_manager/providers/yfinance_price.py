from __future__ import annotations

import logging
from datetime import date

from .._clock import utcnow
from typing import Any

from ..domain.exceptions import PriceUnavailable
from .base import PriceProvider, PriceQuote
from .registry import register_price

log = logging.getLogger(__name__)


class YFinancePriceProvider(PriceProvider):
    name = "yfinance"

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds

    def get_price(self, symbol: str, as_of: date | None = None) -> PriceQuote:
        try:
            import yfinance as yf
        except ImportError as e:
            raise PriceUnavailable("yfinance not installed") from e
        try:
            ticker = yf.Ticker(symbol)
            if as_of is None:
                hist = ticker.history(period="5d", auto_adjust=False)
            else:
                start = as_of.isoformat()
                hist = ticker.history(start=start, period="5d", auto_adjust=False)
            if hist is None or hist.empty:
                raise PriceUnavailable(f"no yfinance data for {symbol}")
            row = hist.iloc[-1]
            currency = (ticker.fast_info.get("currency") if hasattr(ticker, "fast_info") else None) or "USD"
            ts = hist.index[-1]
            quote_date = ts.date() if hasattr(ts, "date") else utcnow().date()
            return PriceQuote(
                symbol=symbol,
                price=float(row["Close"]),
                currency=str(currency).upper(),
                as_of=quote_date,
            )
        except PriceUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise PriceUnavailable(f"yfinance error for {symbol}: {e}") from e

    def get_history(self, symbol: str, start: date, end: date | None = None) -> list[PriceQuote]:
        try:
            import yfinance as yf
        except ImportError:
            return []
        try:
            ticker = yf.Ticker(symbol)
            kwargs: dict[str, Any] = {"start": start.isoformat(), "auto_adjust": False}
            if end is not None:
                kwargs["end"] = end.isoformat()
            hist = ticker.history(**kwargs)
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance history failed for %s: %s", symbol, e)
            return []
        if hist is None or hist.empty:
            return []
        currency = (ticker.fast_info.get("currency") if hasattr(ticker, "fast_info") else None) or "USD"
        currency = str(currency).upper()
        out: list[PriceQuote] = []
        for ts, row in hist.iterrows():
            qd = ts.date() if hasattr(ts, "date") else None
            if qd is None:
                continue
            out.append(PriceQuote(symbol=symbol, price=float(row["Close"]), currency=currency, as_of=qd))
        return out


@register_price("yfinance")
def _factory(opts: dict[str, Any]) -> PriceProvider:
    return YFinancePriceProvider(timeout_seconds=int(opts.get("timeout_seconds", 15)))
