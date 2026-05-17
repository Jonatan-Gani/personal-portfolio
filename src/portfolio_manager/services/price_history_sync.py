"""Accumulate end-of-day prices into the PriceHistoryStore.

`record_eod_prices` stores today's price for everything currently held and every
index in use. `backfill_price_history` pulls whatever daily history the price
provider will give for the dates before the store was populated. Together they
build a local price history with no permanent dependence on an outside service —
each date is fetched once and then kept.
"""
from __future__ import annotations

import logging
from datetime import date

from ..repositories.price_history import EodPrice

log = logging.getLogger(__name__)


def _tracked_symbols(c) -> list[tuple[str, str]]:
    """(symbol, kind) pairs worth recording — held assets and the indices they
    are measured against. Kind is 'asset' or 'index'."""
    assets = c.portfolio.list_assets(include_inactive=True)
    asset_syms = {a.symbol.upper() for a in assets if a.symbol}
    index_syms: set[str] = set()
    for a in assets:
        if a.market_index_symbol:
            index_syms.add(a.market_index_symbol.upper())
        if a.sector_index_symbol:
            index_syms.add(a.sector_index_symbol.upper())
    return ([(s, "asset") for s in sorted(asset_syms)]
            + [(s, "index") for s in sorted(index_syms)])


def record_eod_prices(c) -> int:
    """Record today's price for every held asset and index. Returns how many
    prices were stored. Best-effort — an unreachable symbol is skipped."""
    provider = c.snapshot.price_provider
    stored = 0
    for sym, kind in _tracked_symbols(c):
        try:
            q = provider.get_price(sym)
        except Exception as e:
            log.warning("end-of-day price unavailable for %s: %s", sym, e)
            continue
        if q.price and q.price > 0:
            c.price_history.record(EodPrice(
                symbol=sym, price_date=q.as_of, price=q.price,
                currency=q.currency, kind=kind, source=provider.name,
            ))
            stored += 1
    return stored


def backfill_price_history(c, since: date | None = None) -> dict:
    """Pull daily history for every held asset and index and store it. `since`
    defaults to the earliest transaction date. Best-effort."""
    provider = c.snapshot.price_provider
    if since is None:
        row = c.db.fetchone("SELECT MIN(transaction_date) FROM transactions")
        since = row[0] if row and row[0] else date.today()
    end = date.today()

    symbols = _tracked_symbols(c)
    stored = 0
    failed = 0
    for sym, kind in symbols:
        try:
            quotes = provider.get_history(sym, since, end)
        except Exception as e:
            log.warning("price history unavailable for %s: %s", sym, e)
            failed += 1
            continue
        for q in quotes:
            if q.price and q.price > 0:
                c.price_history.record(EodPrice(
                    symbol=sym, price_date=q.as_of, price=q.price,
                    currency=q.currency, kind=kind, source=provider.name,
                ))
                stored += 1
    return {"symbols": len(symbols), "symbols_failed": failed, "prices_stored": stored}
