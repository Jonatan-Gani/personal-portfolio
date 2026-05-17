"""Split a holding's gain into its causes: currency, market, sector, pick.

For one buy — price/rate/market/sector then (`p0,f0,M0,S0`) and now (`p1,f1,M1,S1`),
all per unit, value in base currency:

    total    = p1*f1 - p0*f0
    currency = p0*(f1 - f0)                      gain from the rate moving
    market   = p0*(M1/M0 - 1)*f1                 the share matching the market
    sector   = p0*(S1/S0 - M1/M0)*f1             the share matching the sector,
                                                 beyond the market
    pick     = total - currency - market - sector   how the asset did vs its sector

The four always add up to `total` exactly. When a buy has no frozen market or
sector level (recorded before this was captured, or the provider was offline),
that part folds into `pick` and the result is flagged incomplete.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from .._clock import utcnow

log = logging.getLogger(__name__)


@dataclass
class ReturnSplit:
    asset_id: str
    symbol: str | None
    quantity: float
    total: float
    currency: float
    market: float
    sector: float
    pick: float
    complete: bool          # False if any included lot lacked frozen index data


@dataclass
class _UnitSplit:
    total: float
    currency: float
    market: float
    sector: float
    pick: float
    complete: bool


def split_unit(
    p0: float, f0: float, p1: float, f1: float,
    m0: float | None, m1: float | None,
    s0: float | None, s1: float | None,
) -> _UnitSplit:
    """The per-unit four-way split. Pure — no I/O."""
    total = p1 * f1 - p0 * f0
    currency = p0 * (f1 - f0)
    price_part = total - currency           # == (p1 - p0) * f1

    complete = True
    if m0 and m1 and m0 > 0:
        market = p0 * (m1 / m0 - 1.0) * f1
        if s0 and s1 and s0 > 0:
            sector = p0 * (s1 / s0 - m1 / m0) * f1
        else:
            sector = 0.0
            complete = False
    else:
        market = 0.0
        sector = 0.0
        complete = False

    pick = price_part - market - sector
    return _UnitSplit(total, currency, market, sector, pick, complete)


class ReturnSplitService:
    """Computes the currency / market / sector / pick split for a holding,
    from each open lot's frozen inception values to a chosen end date
    (default: live now)."""

    def __init__(self, cost_basis, fx, price_provider, history, portfolio, base_currency: str):
        self.cost_basis = cost_basis
        self.fx = fx
        self.price_provider = price_provider
        self.history = history
        self.portfolio = portfolio
        self.base_currency = base_currency.upper()

    def for_asset(self, asset, end: date | None = None) -> ReturnSplit | None:
        """Split the open-lot return for one asset. `end` defaults to today
        (live prices); a past date reads the price-history store."""
        cb = self.cost_basis.compute(asset.asset_id, as_of=end)
        lots = [lot for lot in cb.open_lots if lot.qty > 0]
        if not lots:
            return None

        ev = self._end_values(asset, end)
        if ev is None:
            return None
        p1, f1, m1, s1 = ev

        total = currency = market = sector = pick = 0.0
        complete = True
        for lot in lots:
            if lot.fx_to_base is None or not lot.cost_known:
                complete = False
                continue
            u = split_unit(
                p0=lot.unit_cost, f0=lot.fx_to_base, p1=p1, f1=f1,
                m0=lot.market_index_level, m1=m1,
                s0=lot.sector_index_level, s1=s1,
            )
            total += u.total * lot.qty
            currency += u.currency * lot.qty
            market += u.market * lot.qty
            sector += u.sector * lot.qty
            pick += u.pick * lot.qty
            complete = complete and u.complete

        return ReturnSplit(
            asset_id=asset.asset_id, symbol=asset.symbol,
            quantity=sum(lot.qty for lot in lots),
            total=total, currency=currency, market=market, sector=sector,
            pick=pick, complete=complete,
        )

    def for_portfolio(self, end: date | None = None) -> list[ReturnSplit]:
        """The split for every currently held asset, biggest gain first."""
        out: list[ReturnSplit] = []
        for asset in self.portfolio.list_assets(include_inactive=True):
            split = self.for_asset(asset, end=end)
            if split is not None and split.quantity:
                out.append(split)
        out.sort(key=lambda s: s.total, reverse=True)
        return out

    # ------------------------------------------------------------- internals
    def _end_values(
        self, asset, end: date | None
    ) -> tuple[float, float, float | None, float | None] | None:
        """(asset price, FX→base, market index, sector index) at `end`.
        end=None or today → live; a past date → the price-history store."""
        today = utcnow().date()
        live = end is None or end >= today

        price = self._value(asset.symbol, end, live)
        if price is None:
            return None
        fx = self._fx(asset.currency, end if not live else None)
        market = self._value(asset.market_index_symbol, end, live)
        sector = self._value(asset.sector_index_symbol, end, live)
        return price, fx, market, sector

    def _value(self, symbol: str | None, end: date | None, live: bool) -> float | None:
        if not symbol:
            return None
        if live:
            try:
                return self.price_provider.get_price(symbol).price
            except Exception as e:
                log.warning("live price unavailable for %s: %s", symbol, e)
                return None
        row = self.history.get_asof(symbol, end)
        return row.price if row else None

    def _fx(self, currency: str, as_of: date | None) -> float:
        try:
            return self.fx.rate(currency, self.base_currency, as_of)
        except Exception:
            return 1.0
