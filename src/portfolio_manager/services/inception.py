"""Freezes market context onto a transaction at the moment it is recorded.

Every transaction gets its FX rate pinned. Asset buys / sells / opening balances
also get the levels of the asset's market and sector indices pinned — so the
transaction's return can later be split into currency, market, sector and pick
without needing historical index data. The fetched index levels are also written
to the price-history store, so they are never fetched twice for the same date.
"""
from __future__ import annotations

import logging
from datetime import date

from ..domain.enums import PositionKind, TransactionType
from ..domain.models import Transaction
from ..repositories.price_history import EodPrice, PriceHistoryStore

log = logging.getLogger(__name__)

# Asset transaction types worth freezing market/sector context for.
_PRICED_TYPES = {
    TransactionType.BUY,
    TransactionType.SELL,
    TransactionType.OPENING_BALANCE,
}


class InceptionService:
    def __init__(self, fx, price_provider, assets, history: PriceHistoryStore, base_currency: str):
        self.fx = fx
        self.price_provider = price_provider
        self.assets = assets
        self.history = history
        self.base_currency = base_currency

    def stamp(self, tx: Transaction) -> Transaction:
        """Pin FX (and, for asset trades, index levels) onto `tx`. Mutates and
        returns it. Never raises — missing data leaves the field None."""
        self.fx.stamp_transaction(tx, self.base_currency)
        if tx.entity_kind is PositionKind.ASSET and tx.transaction_type in _PRICED_TYPES:
            try:
                asset = self.assets.get(tx.entity_id)
            except Exception:  # asset row missing — nothing to pin against
                return tx
            tx.market_index_level = self._level(asset.market_index_symbol, tx.transaction_date)
            tx.sector_index_level = self._level(asset.sector_index_symbol, tx.transaction_date)
        return tx

    def _level(self, symbol: str | None, on: date) -> float | None:
        if not symbol:
            return None
        # Reuse a stored level for that exact date if we already have it.
        cached = self.history.get_asof(symbol, on)
        if cached is not None and cached.price_date == on:
            return cached.price
        try:
            quote = self.price_provider.get_price(symbol, as_of=on)
        except Exception as e:
            log.warning("index level unavailable for %s at %s: %s", symbol, on, e)
            return None
        if quote.price and quote.price > 0:
            self.history.record(EodPrice(
                symbol=symbol, price_date=quote.as_of, price=quote.price,
                currency=quote.currency, kind="index", source=self.price_provider.name,
            ))
            return quote.price
        return None
