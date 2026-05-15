from __future__ import annotations

import logging

from .._clock import utcnow
from ..domain.enums import PositionKind
from ..domain.exceptions import PriceUnavailable
from ..domain.models import Asset, CashHolding, Liability, SnapshotMeta, SnapshotPosition
from ..providers.base import PriceProvider
from ..repositories.manual_prices import ManualPriceOverrideRepository
from ..repositories.snapshots import SnapshotRepository
from .fx import FXService
from .holdings import HoldingsService
from .portfolio import PortfolioService

log = logging.getLogger(__name__)


class SnapshotService:
    """Take a snapshot of the portfolio: derive quantities/balances from the transaction
    log, value each non-zero position, and persist the materialised state."""

    def __init__(
        self,
        portfolio: PortfolioService,
        fx: FXService,
        price_provider: PriceProvider,
        snapshots: SnapshotRepository,
        holdings: HoldingsService,
        manual_overrides: ManualPriceOverrideRepository,
        base_currency: str,
        reporting_currencies: list[str],
        benchmarks=None,  # BenchmarkService | None — declared post-hoc to avoid import cycle
        accrual=None,     # AccrualService | None — same reason
    ):
        self.portfolio = portfolio
        self.fx = fx
        self.price_provider = price_provider
        self.snapshots = snapshots
        self.holdings = holdings
        self.manual_overrides = manual_overrides
        self.benchmarks = benchmarks
        self.accrual = accrual
        self.base_currency = base_currency.upper()
        self.reporting_currencies = [c.upper() for c in reporting_currencies]
        if self.base_currency not in self.reporting_currencies:
            self.reporting_currencies = [self.base_currency, *self.reporting_currencies]

    def take(self, notes: str | None = None) -> SnapshotMeta:
        taken_at = utcnow()
        log.info("taking snapshot at %s base=%s", taken_at.isoformat(), self.base_currency)

        # Apply interest accrual on liabilities BEFORE reading holdings — accrual
        # writes a principal_change transaction so the snapshot reflects current debt.
        if self.accrual is not None:
            try:
                accrued = self.accrual.accrue_all(as_of=taken_at.date())
                if accrued:
                    log.info("accrued interest on %d liabilities", len(accrued))
            except Exception as e:
                log.warning("liability accrual failed: %s", e)

        fx_from_base = self.fx.rates_from_base(self.base_currency, self.reporting_currencies)
        state = self.holdings.at(taken_at)

        positions: list[SnapshotPosition] = []
        meta = SnapshotMeta(
            taken_at=taken_at,
            base_currency=self.base_currency,
            reporting_currencies=self.reporting_currencies,
            notes=notes,
        )

        total_assets_base = 0.0
        total_liab_base = 0.0
        total_cash_base = 0.0

        for asset in self.portfolio.list_assets():
            qty = state.asset_quantities.get(asset.asset_id, 0.0)
            if qty == 0:
                continue
            try:
                pos = self._asset_to_position(asset, qty, taken_at, meta.snapshot_id, fx_from_base)
            except PriceUnavailable as e:
                log.warning("skipping %s — %s", asset.name, e)
                continue
            positions.append(pos)
            total_assets_base += pos.values_by_currency[self.base_currency]

        for cash in self.portfolio.list_cash():
            bal = state.cash_balances.get(cash.cash_id, 0.0)
            if bal == 0:
                continue
            pos = self._cash_to_position(cash, bal, meta.snapshot_id, fx_from_base)
            positions.append(pos)
            total_cash_base += pos.values_by_currency[self.base_currency]

        for liab in self.portfolio.list_liabilities():
            principal = state.liability_principals.get(liab.liability_id, 0.0)
            if principal == 0:
                continue
            pos = self._liability_to_position(liab, principal, meta.snapshot_id, fx_from_base)
            positions.append(pos)
            total_liab_base += pos.values_by_currency[self.base_currency]

        meta.total_assets_base = total_assets_base
        meta.total_liabilities_base = total_liab_base
        meta.total_cash_base = total_cash_base
        meta.net_worth_base = total_assets_base + total_cash_base - total_liab_base

        self.snapshots.insert_snapshot(meta, positions, fx_from_base)
        log.info(
            "snapshot %s saved: assets=%.2f cash=%.2f liab=%.2f net=%.2f %s",
            meta.snapshot_id, total_assets_base, total_cash_base, total_liab_base,
            meta.net_worth_base, self.base_currency,
        )

        if self.benchmarks is not None:
            try:
                n = self.benchmarks.record_today_for_all()
                log.info("benchmarks: recorded today's price for %d", n)
            except Exception as e:
                log.warning("benchmark price recording failed: %s", e)
        return meta

    # -------------------------------------------------------------- asset
    def _asset_to_position(
        self,
        asset: Asset,
        quantity: float,
        taken_at,
        snapshot_id: str,
        fx_from_base: dict[str, float],
    ) -> SnapshotPosition:
        price_local, price_ccy = self._resolve_price(asset, taken_at)
        if price_ccy != asset.currency:
            converted = self.fx.convert(price_local, price_ccy, asset.currency)
            log.debug("price ccy %s != asset ccy %s, converted %s -> %s", price_ccy, asset.currency, price_local, converted)
            price_local = converted
        value_local = price_local * quantity
        values_by_ccy = self._project_values(value_local, asset.currency, fx_from_base)
        return SnapshotPosition(
            snapshot_id=snapshot_id,
            position_kind=PositionKind.ASSET,
            entity_id=asset.asset_id,
            name=asset.name,
            instrument_type=asset.instrument_type.value,
            asset_class=asset.asset_class.value,
            currency=asset.currency,
            country=asset.country,
            sector=asset.sector,
            quantity=quantity,
            price_local=price_local,
            value_local=value_local,
            tags=asset.tags,
            values_by_currency=values_by_ccy,
        )

    # -------------------------------------------------------------- liability
    def _liability_to_position(
        self,
        liab: Liability,
        principal: float,
        snapshot_id: str,
        fx_from_base: dict[str, float],
    ) -> SnapshotPosition:
        value_local = principal
        values_by_ccy = self._project_values(value_local, liab.currency, fx_from_base)
        return SnapshotPosition(
            snapshot_id=snapshot_id,
            position_kind=PositionKind.LIABILITY,
            entity_id=liab.liability_id,
            name=liab.name,
            instrument_type=liab.liability_type.value,
            asset_class="liability",
            currency=liab.currency,
            country=None,
            sector=None,
            quantity=None,
            price_local=None,
            value_local=value_local,
            tags=liab.tags,
            values_by_currency=values_by_ccy,
        )

    # -------------------------------------------------------------- cash
    def _cash_to_position(
        self,
        cash: CashHolding,
        balance: float,
        snapshot_id: str,
        fx_from_base: dict[str, float],
    ) -> SnapshotPosition:
        values_by_ccy = self._project_values(balance, cash.currency, fx_from_base)
        return SnapshotPosition(
            snapshot_id=snapshot_id,
            position_kind=PositionKind.CASH,
            entity_id=cash.cash_id,
            name=cash.account_name,
            instrument_type="cash",
            asset_class="cash",
            currency=cash.currency,
            country=cash.country,
            sector=None,
            quantity=None,
            price_local=1.0,
            value_local=balance,
            tags=cash.tags,
            values_by_currency=values_by_ccy,
        )

    # -------------------------------------------------------------- price resolution
    def _resolve_price(self, asset: Asset, when) -> tuple[float, str]:
        # Manual override wins if present (most recent on/before `when`).
        override = self.manual_overrides.latest_before(asset.asset_id, when)
        if override is not None:
            return override.price, override.currency
        if asset.symbol:
            provider = self.price_provider
            if asset.price_provider:
                from ..providers.registry import build_price_provider
                provider = build_price_provider(asset.price_provider, {})
            quote = provider.get_price(asset.symbol)
            return quote.price, (quote.currency or asset.currency).upper()
        raise PriceUnavailable(
            f"asset {asset.name!r} has no symbol and no manual price override — "
            f"add a manual price entry or set a symbol so prices can be fetched"
        )

    def _project_values(
        self,
        value_local: float,
        local_currency: str,
        fx_from_base: dict[str, float],
    ) -> dict[str, float]:
        local_currency = local_currency.upper()
        rate_base_to_local = self.fx.rate(self.base_currency, local_currency)
        if rate_base_to_local == 0:
            raise ZeroDivisionError(f"FX rate {self.base_currency}->{local_currency} is zero")
        value_in_base = value_local / rate_base_to_local
        values: dict[str, float] = {}
        for ccy in self.reporting_currencies:
            values[ccy] = value_in_base * fx_from_base[ccy]
        return values
