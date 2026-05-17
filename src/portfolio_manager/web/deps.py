from __future__ import annotations

from dataclasses import dataclass

from ..config import AppConfig
from ..db.connection import Database
from ..providers.registry import build_fx_provider, build_price_provider
from ..repositories import (
    AccountGroupRepository,
    AccountRepository,
    AppSettingsRepository,
    AssetRepository,
    BenchmarkRepository,
    CashRepository,
    FXRateCache,
    LiabilityRepository,
    ManualPriceOverrideRepository,
    PriceCache,
    SnapshotRepository,
    TargetAllocationRepository,
    TransactionRepository,
)
from ..repositories.price_history import PriceHistoryStore, build_price_history_store
from ..services import (
    AccrualService,
    AssetLookupService,
    BenchmarkService,
    CostBasisService,
    DriftService,
    ExposureService,
    FXService,
    HoldingsService,
    InceptionService,
    IncomeService,
    MarketsService,
    PerformanceService,
    PortfolioService,
    ReturnsService,
    RiskService,
    SnapshotDiffService,
    SnapshotService,
)


@dataclass
class Container:
    config: AppConfig
    db: Database
    portfolio: PortfolioService
    snapshot: SnapshotService
    holdings: HoldingsService
    exposure: ExposureService
    returns: ReturnsService
    fx: FXService
    benchmarks: BenchmarkService
    cost_basis: CostBasisService
    performance: PerformanceService
    snapshot_diff: SnapshotDiffService
    drift: DriftService
    income: IncomeService
    accrual: AccrualService
    inception: InceptionService
    risk: RiskService
    markets: MarketsService
    asset_lookup: AssetLookupService
    snapshots_repo: SnapshotRepository
    transactions_repo: TransactionRepository
    benchmarks_repo: BenchmarkRepository
    manual_prices_repo: ManualPriceOverrideRepository
    targets_repo: TargetAllocationRepository
    accounts_repo: AccountRepository
    account_groups_repo: AccountGroupRepository
    app_settings_repo: AppSettingsRepository
    price_cache: PriceCache
    price_history: PriceHistoryStore


def build_container(config: AppConfig, db: Database) -> Container:
    fx_provider = build_fx_provider(config.providers.fx.name, config.providers.fx.options)
    price_provider = build_price_provider(config.providers.price.name, config.providers.price.options)

    asset_repo = AssetRepository(db)
    liab_repo = LiabilityRepository(db)
    cash_repo = CashRepository(db)
    snap_repo = SnapshotRepository(db)
    tx_repo = TransactionRepository(db)
    bench_repo = BenchmarkRepository(db)
    mp_repo = ManualPriceOverrideRepository(db)
    targets_repo = TargetAllocationRepository(db)
    accounts_repo = AccountRepository(db)
    account_groups_repo = AccountGroupRepository(db)
    app_settings_repo = AppSettingsRepository(db)
    fx_cache = FXRateCache(db)
    price_cache = PriceCache(db)
    price_history = build_price_history_store(config.history.backend, db)

    fx_ttl = int(config.providers.fx.options.get("cache_ttl_hours", 12))
    fx_service = FXService(provider=fx_provider, cache=fx_cache, cache_ttl_hours=fx_ttl)

    portfolio = PortfolioService(asset_repo, liab_repo, cash_repo)
    holdings = HoldingsService(db)
    benchmarks = BenchmarkService(
        repo=bench_repo,
        price_cache=price_cache,
        default_price_provider=price_provider,
    )
    accrual = AccrualService(
        db=db, liabilities=liab_repo, transactions=tx_repo,
        fx=fx_service, base_currency=config.reporting.base_currency,
    )
    snapshot_service = SnapshotService(
        portfolio=portfolio,
        fx=fx_service,
        price_provider=price_provider,
        snapshots=snap_repo,
        holdings=holdings,
        manual_overrides=mp_repo,
        base_currency=config.reporting.base_currency,
        reporting_currencies=config.reporting.reporting_currencies,
        benchmarks=benchmarks,
        accrual=accrual,
    )
    exposure_service = ExposureService(db)
    returns_service = ReturnsService(db)
    cost_basis_service = CostBasisService(db)
    performance_service = PerformanceService(db, fx_service, config.reporting.base_currency)
    snapshot_diff_service = SnapshotDiffService(db)
    drift_service = DriftService(db, targets_repo, exposure_service)
    income_service = IncomeService(db, fx_service, cost_basis_service, config.reporting.base_currency)
    risk_service = RiskService(db, performance_service)
    inception_service = InceptionService(
        fx=fx_service, price_provider=price_provider, assets=asset_repo,
        history=price_history, base_currency=config.reporting.base_currency,
    )
    markets_service = MarketsService(provider=price_provider, cache=price_cache, cache_ttl_minutes=60)
    asset_lookup_service = AssetLookupService()

    return Container(
        config=config,
        db=db,
        portfolio=portfolio,
        snapshot=snapshot_service,
        holdings=holdings,
        exposure=exposure_service,
        returns=returns_service,
        fx=fx_service,
        benchmarks=benchmarks,
        cost_basis=cost_basis_service,
        performance=performance_service,
        snapshot_diff=snapshot_diff_service,
        drift=drift_service,
        income=income_service,
        accrual=accrual,
        inception=inception_service,
        risk=risk_service,
        markets=markets_service,
        asset_lookup=asset_lookup_service,
        snapshots_repo=snap_repo,
        transactions_repo=tx_repo,
        benchmarks_repo=bench_repo,
        manual_prices_repo=mp_repo,
        targets_repo=targets_repo,
        accounts_repo=accounts_repo,
        account_groups_repo=account_groups_repo,
        app_settings_repo=app_settings_repo,
        price_cache=price_cache,
        price_history=price_history,
    )
