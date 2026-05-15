from __future__ import annotations

import pytest

from portfolio_manager.config import AppConfig
from portfolio_manager.db.connection import Database
from portfolio_manager.db.migrations import apply_migrations
from portfolio_manager.providers.mock import MockFxProvider, MockPriceProvider
from portfolio_manager.repositories import (
    AssetRepository,
    CashRepository,
    FXRateCache,
    LiabilityRepository,
    ManualPriceOverrideRepository,
    SnapshotRepository,
    TransactionRepository,
)
from portfolio_manager.services import (
    ExposureService,
    FXService,
    HoldingsService,
    PortfolioService,
    ReturnsService,
    SnapshotService,
)


@pytest.fixture
def db(tmp_path) -> Database:
    db = Database(tmp_path / "test.duckdb")
    apply_migrations(db)
    yield db
    db.close()


@pytest.fixture
def config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def services(db, config):
    fx_provider = MockFxProvider()
    price_provider = MockPriceProvider({
        "AAPL": (200.0, "USD"),
        "VWCE": (110.0, "EUR"),
        "BUND": (100.0, "EUR"),
    })
    fx_cache = FXRateCache(db)
    fx = FXService(fx_provider, fx_cache, cache_ttl_hours=1)

    portfolio = PortfolioService(AssetRepository(db), LiabilityRepository(db), CashRepository(db))
    snap_repo = SnapshotRepository(db)
    tx_repo = TransactionRepository(db)
    mp_repo = ManualPriceOverrideRepository(db)
    holdings = HoldingsService(db)
    snap = SnapshotService(
        portfolio=portfolio,
        fx=fx,
        price_provider=price_provider,
        snapshots=snap_repo,
        holdings=holdings,
        manual_overrides=mp_repo,
        base_currency="USD",
        reporting_currencies=["USD", "EUR", "GBP", "SEK", "ILS"],
    )
    return {
        "portfolio": portfolio,
        "snap": snap,
        "snap_repo": snap_repo,
        "tx_repo": tx_repo,
        "mp_repo": mp_repo,
        "holdings": holdings,
        "exposure": ExposureService(db),
        "returns": ReturnsService(db),
        "fx": fx,
    }
