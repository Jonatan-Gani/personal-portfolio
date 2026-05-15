from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import threading
from datetime import date, datetime
from typing import Any

from .._clock import utcnow
from ..domain.exceptions import PriceUnavailable
from .base import PriceProvider, PriceQuote
from .registry import register_price

log = logging.getLogger(__name__)


class IBKRPriceProvider(PriceProvider):
    """Prices from a running Interactive Brokers TWS or IB Gateway, via ib_insync.

    Requires the `ibkr` extra (`pip install -e ".[ibkr]"`) and a TWS / IB Gateway
    instance listening on the configured socket with API access enabled
    (TWS → Global Configuration → API → Settings → "Enable ActiveX and Socket
    Clients").

    A symbol is either a bare ticker (`AAPL`) or `TICKER:EXCHANGE:CURRENCY`
    (`VOD:LSE:GBP`) when the exchange/currency differ from the configured
    defaults — IBKR needs those to resolve non-US instruments unambiguously.

    The socket connection is opened lazily on first use and reused. Any error
    drops the connection so the next call reconnects cleanly.
    """

    name = "ibkr"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 17,
        timeout_seconds: int = 15,
        exchange: str = "SMART",
        currency: str = "USD",
        market_data_type: int = 3,  # 1 live, 2 frozen, 3 delayed, 4 delayed-frozen
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout_seconds = timeout_seconds
        self.exchange = exchange
        self.currency = currency
        self.market_data_type = market_data_type
        self._ib: Any = None
        self._lock = threading.RLock()

    # ----------------------------------------------------------------- conn
    def _connect(self) -> Any:
        try:
            from ib_insync import IB
        except ImportError as e:
            raise PriceUnavailable(
                'ib_insync not installed — run: pip install -e ".[ibkr]"'
            ) from e
        # ib_insync needs an event loop; worker threads (FastAPI threadpool) have none.
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        ib = IB()
        try:
            ib.connect(
                self.host, self.port, clientId=self.client_id,
                timeout=self.timeout_seconds, readonly=True,
            )
        except Exception as e:
            raise PriceUnavailable(
                f"cannot reach IB Gateway/TWS at {self.host}:{self.port}: {e}"
            ) from e
        ib.reqMarketDataType(self.market_data_type)
        return ib

    def _ib_conn(self) -> Any:
        if self._ib is None or not self._ib.isConnected():
            self._ib = self._connect()
        return self._ib

    def _reset(self) -> None:
        if self._ib is not None:
            with contextlib.suppress(Exception):
                self._ib.disconnect()
        self._ib = None

    def _contract(self, symbol: str) -> Any:
        from ib_insync import Stock

        parts = symbol.split(":")
        ticker = parts[0].strip().upper()
        exch = parts[1].strip().upper() if len(parts) > 1 and parts[1].strip() else self.exchange
        ccy = parts[2].strip().upper() if len(parts) > 2 and parts[2].strip() else self.currency
        return Stock(ticker, exch, ccy)

    # --------------------------------------------------------------- prices
    def get_price(self, symbol: str, as_of: date | None = None) -> PriceQuote:
        with self._lock:
            if as_of is not None and as_of < utcnow().date():
                return self._historical_price(symbol, as_of)
            return self._live_price(symbol)

    def _live_price(self, symbol: str) -> PriceQuote:
        try:
            ib = self._ib_conn()
            contract = self._contract(symbol)
            if not ib.qualifyContracts(contract):
                raise PriceUnavailable(f"IBKR could not resolve a contract for {symbol!r}")
            ticker = ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
            price = math.nan
            waited = 0.0
            while waited < self.timeout_seconds:
                ib.sleep(0.25)
                waited += 0.25
                for cand in (ticker.marketPrice(), ticker.last, ticker.close):
                    if cand is not None and not math.isnan(cand) and cand > 0:
                        price = cand
                        break
                if not math.isnan(price):
                    break
            ib.cancelMktData(contract)
            if math.isnan(price):
                raise PriceUnavailable(f"no IBKR market data for {symbol!r}")
            return PriceQuote(
                symbol=symbol,
                price=float(price),
                currency=(contract.currency or self.currency).upper(),
                as_of=utcnow().date(),
            )
        except PriceUnavailable:
            raise
        except Exception as e:
            self._reset()
            raise PriceUnavailable(f"IBKR error for {symbol!r}: {e}") from e

    def _historical_price(self, symbol: str, as_of: date) -> PriceQuote:
        try:
            ib = self._ib_conn()
            contract = self._contract(symbol)
            if not ib.qualifyContracts(contract):
                raise PriceUnavailable(f"IBKR could not resolve a contract for {symbol!r}")
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59),
                durationStr="5 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            if not bars:
                raise PriceUnavailable(f"no IBKR history for {symbol!r} at {as_of}")
            last = bars[-1]
            return PriceQuote(
                symbol=symbol,
                price=float(last.close),
                currency=(contract.currency or self.currency).upper(),
                as_of=last.date if isinstance(last.date, date) else as_of,
            )
        except PriceUnavailable:
            raise
        except Exception as e:
            self._reset()
            raise PriceUnavailable(f"IBKR history error for {symbol!r}: {e}") from e

    def get_history(self, symbol: str, start: date, end: date | None = None) -> list[PriceQuote]:
        with self._lock:
            end = end or utcnow().date()
            try:
                ib = self._ib_conn()
                contract = self._contract(symbol)
                if not ib.qualifyContracts(contract):
                    return []
                span_days = max((end - start).days + 1, 1)
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime=datetime(end.year, end.month, end.day, 23, 59, 59),
                    durationStr=f"{span_days} D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=1,
                )
            except Exception as e:
                self._reset()
                log.warning("IBKR history failed for %s: %s", symbol, e)
                return []
            ccy = (contract.currency or self.currency).upper()
            out: list[PriceQuote] = []
            for b in bars or []:
                bd = b.date if isinstance(b.date, date) else None
                if bd is None or bd < start or bd > end:
                    continue
                out.append(PriceQuote(symbol=symbol, price=float(b.close), currency=ccy, as_of=bd))
            return out


@register_price("ibkr")
def _factory(opts: dict[str, Any]) -> PriceProvider:
    return IBKRPriceProvider(
        host=str(opts.get("host", "127.0.0.1")),
        port=int(opts.get("port", 7497)),
        client_id=int(opts.get("client_id", 17)),
        timeout_seconds=int(opts.get("timeout_seconds", 15)),
        exchange=str(opts.get("exchange", "SMART")),
        currency=str(opts.get("currency", "USD")),
        market_data_type=int(opts.get("market_data_type", 3)),
    )
