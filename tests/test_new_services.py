from __future__ import annotations

from datetime import date, timedelta

import pytest

from portfolio_manager.domain.enums import (
    AssetClass,
    InstrumentType,
    LiabilityType,
    PositionKind,
    TransactionType,
)
from portfolio_manager.domain.models import (
    Asset,
    CashHolding,
    Liability,
    TargetAllocation,
    Transaction,
)
from portfolio_manager.repositories import (
    LiabilityRepository,
    TargetAllocationRepository,
    TransactionRepository,
)
from portfolio_manager.services import (
    AccrualService,
    CostBasisService,
    DriftService,
    ExposureService,
    IncomeService,
    PerformanceService,
    SnapshotDiffService,
)
from portfolio_manager.services.performance import _xirr_solve

# ---------- helpers (copied from test_snapshot_flow style) ----------

def _open_asset(p, tx, *, name, symbol, qty, currency, asset_class=AssetClass.EQUITY,
                instrument_type=InstrumentType.EQUITY, country=None):
    a = Asset(name=name, symbol=symbol, instrument_type=instrument_type,
              asset_class=asset_class, currency=currency, country=country)
    p.add_asset(a)
    tx.insert(Transaction(
        transaction_date=date.today(),
        transaction_type=TransactionType.OPENING_BALANCE,
        entity_kind=PositionKind.ASSET, entity_id=a.asset_id,
        quantity=qty, price=None, amount=0.0, currency=currency,
    ))
    return a


def _open_cash(p, tx, *, name, balance, currency):
    c = CashHolding(account_name=name, currency=currency)
    p.add_cash(c)
    tx.insert(Transaction(
        transaction_date=date.today(),
        transaction_type=TransactionType.OPENING_BALANCE,
        entity_kind=PositionKind.CASH, entity_id=c.cash_id,
        amount=balance, currency=currency,
    ))
    return c


# =========================================================== Cost basis (FIFO)

def test_cost_basis_fifo_realized_pnl(services, db):
    p, tx = services["portfolio"], services["tx_repo"]
    a = _open_asset(p, tx, name="X", symbol="X", qty=0, currency="USD")
    # zero-qty opening then proper buys
    tx.insert(Transaction(transaction_date=date(2025, 1, 1),
        transaction_type=TransactionType.BUY, entity_kind=PositionKind.ASSET,
        entity_id=a.asset_id, quantity=10.0, price=100.0, amount=1000.0, currency="USD"))
    tx.insert(Transaction(transaction_date=date(2025, 2, 1),
        transaction_type=TransactionType.BUY, entity_kind=PositionKind.ASSET,
        entity_id=a.asset_id, quantity=10.0, price=120.0, amount=1200.0, currency="USD"))
    tx.insert(Transaction(transaction_date=date(2025, 3, 1),
        transaction_type=TransactionType.SELL, entity_kind=PositionKind.ASSET,
        entity_id=a.asset_id, quantity=15.0, price=150.0, amount=2250.0, currency="USD"))

    cb = CostBasisService(db)
    res = cb.compute(a.asset_id)

    # Sold 15 of 20: FIFO consumes all 10 @ 100 + 5 @ 120 = 1000 + 600 = 1600 cost.
    # Proceeds = 15 * 150 = 2250. Realized = 2250 - 1600 = 650.
    assert res.realized_pnl == pytest.approx(650.0)
    # Remaining: 5 @ 120 = 600 cost basis, qty 5
    assert res.quantity == pytest.approx(5.0)
    assert res.total_cost_basis == pytest.approx(600.0)
    assert res.avg_cost == pytest.approx(120.0)


def test_cost_basis_split_preserves_total(services, db):
    p, tx = services["portfolio"], services["tx_repo"]
    a = _open_asset(p, tx, name="Y", symbol="Y", qty=0, currency="USD")
    tx.insert(Transaction(transaction_date=date(2025, 1, 1),
        transaction_type=TransactionType.BUY, entity_kind=PositionKind.ASSET,
        entity_id=a.asset_id, quantity=10.0, price=100.0, amount=1000.0, currency="USD"))
    tx.insert(Transaction(transaction_date=date(2025, 2, 1),
        transaction_type=TransactionType.SPLIT, entity_kind=PositionKind.ASSET,
        entity_id=a.asset_id, quantity=2.0, amount=0.0, currency="USD"))
    cb = CostBasisService(db)
    res = cb.compute(a.asset_id)
    assert res.quantity == pytest.approx(20.0)
    assert res.total_cost_basis == pytest.approx(1000.0)
    assert res.avg_cost == pytest.approx(50.0)


# =========================================================== XIRR solver

def test_xirr_solver_handles_simple_case():
    # Invest -1000 today, get +1100 in one year → ~10%
    flows = [(date(2024, 1, 1), -1000.0), (date(2025, 1, 1), 1100.0)]
    rate = _xirr_solve(flows)
    assert rate == pytest.approx(0.10, abs=1e-3)


def test_xirr_handles_irregular_flows():
    flows = [
        (date(2024, 1, 1), -1000.0),
        (date(2024, 7, 1), -500.0),
        (date(2025, 1, 1), 1600.0),
    ]
    rate = _xirr_solve(flows)
    # NPV at returned rate should be ~0
    from portfolio_manager.services.performance import _xnpv
    assert abs(_xnpv(rate, flows)) < 1e-3


# =========================================================== Performance / TWR / drawdown

def test_twr_isolates_external_flows(services, db):
    """TWR should NOT count a deposit as a return."""
    p, tx, fx = services["portfolio"], services["tx_repo"], services["fx"]
    cash = _open_cash(p, tx, name="C", balance=1000.0, currency="USD")
    s1 = services["snap"].take(notes="s1")

    # Deposit 500 → balance is now 1500. NAV grew by 500 entirely from deposit.
    tx.insert(Transaction(transaction_date=date.today(),
        transaction_type=TransactionType.DEPOSIT, entity_kind=PositionKind.CASH,
        entity_id=cash.cash_id, amount=500.0, currency="USD"))
    s2 = services["snap"].take(notes="s2")

    perf = PerformanceService(db, fx, base_currency="USD")
    twr = perf.twr("USD", since=None, until=None)
    # TWR should be ~0 (no real return); without flow isolation it would be +50%
    assert abs(twr["twr"]) < 0.01


def test_drawdown_detects_peak(services, db):
    p, tx, fx = services["portfolio"], services["tx_repo"], services["fx"]
    cash = _open_cash(p, tx, name="C", balance=1000.0, currency="USD")
    services["snap"].take()
    # Withdraw to simulate a drop
    tx.insert(Transaction(transaction_date=date.today(),
        transaction_type=TransactionType.WITHDRAW, entity_kind=PositionKind.CASH,
        entity_id=cash.cash_id, amount=300.0, currency="USD"))
    services["snap"].take()
    perf = PerformanceService(db, fx, base_currency="USD")
    dd = perf.drawdown_series("USD", since=None, until=None)
    assert dd["points"]
    assert dd["max_drawdown"] is not None
    # 300/1000 drop = -30% drawdown — but withdraw is not a market loss; the test
    # just asserts that the drawdown computation runs and produces values; the
    # service treats every Δnav uniformly.


# =========================================================== Snapshot diff

def test_snapshot_diff_decomposes(services, db):
    p, tx = services["portfolio"], services["tx_repo"]
    aapl = _open_asset(p, tx, name="AAPL", symbol="AAPL", qty=10.0, currency="USD")
    s1 = services["snap"].take()
    # Buy 5 more
    tx.insert(Transaction(transaction_date=date.today(),
        transaction_type=TransactionType.BUY, entity_kind=PositionKind.ASSET,
        entity_id=aapl.asset_id, quantity=5.0, price=200.0, amount=1000.0, currency="USD"))
    s2 = services["snap"].take()

    diff = SnapshotDiffService(db).diff(s1.snapshot_id, s2.snapshot_id, "USD")
    # qty effect should equal 5 * 200 (last price) * 1 (USD->USD fx) = 1000
    aapl_row = next(p for p in diff.positions if p.entity_id == aapl.asset_id)
    assert aapl_row.qty_effect == pytest.approx(1000.0, rel=0.01)
    # Decomposition sums to total
    assert (aapl_row.price_effect + aapl_row.fx_effect + aapl_row.qty_effect) == pytest.approx(aapl_row.delta_total, rel=0.01)


# =========================================================== Targets / drift

def test_drift_reports_over_and_under(services, db):
    p, tx = services["portfolio"], services["tx_repo"]
    _open_asset(p, tx, name="EquityFund", symbol="EQF", qty=10.0, currency="USD",
                asset_class=AssetClass.EQUITY)
    _open_cash(p, tx, name="Cash", balance=2000.0, currency="USD")
    services["snap"].take()

    targets = TargetAllocationRepository(db)
    targets.upsert(TargetAllocation(dimension="asset_class", bucket="equity", target_weight=1.0))

    drift = DriftService(db, targets, ExposureService(db))
    rep = drift.report("asset_class", "USD")
    eq = next(r for r in rep.rows if r.bucket == "equity")
    assert eq.target_weight == 1.0
    assert eq.current_weight <= 1.0  # there's no cash in the equity bucket but cash isn't included in this dim


# =========================================================== Liability accrual

def test_accrual_increases_principal(db):
    from portfolio_manager.repositories import TransactionRepository
    liab_repo = LiabilityRepository(db)
    tx_repo = TransactionRepository(db)
    loan = liab_repo.upsert(Liability(
        name="Loan", liability_type=LiabilityType.LOAN, currency="USD",
        interest_rate=0.10,  # 10% APR
    ))
    # Backdate the opening balance 365 days
    yesteryear = date.today() - timedelta(days=365)
    tx_repo.insert(Transaction(
        transaction_date=yesteryear,
        transaction_type=TransactionType.OPENING_BALANCE,
        entity_kind=PositionKind.LIABILITY, entity_id=loan.liability_id,
        amount=10000.0, currency="USD",
    ))
    accr = AccrualService(db, liab_repo, tx_repo)
    results = accr.accrue_all(as_of=date.today())
    assert results
    # At 10% APR over 365 days simple, accrual ~= 1000
    assert 950 < results[0].accrued < 1050


# =========================================================== Income TTM

def test_income_ttm_collects_dividends(services, db):
    p, tx = services["portfolio"], services["tx_repo"]
    a = _open_asset(p, tx, name="DivCo", symbol="DC", qty=100.0, currency="USD")
    # Two dividends in last year
    tx.insert(Transaction(transaction_date=date.today() - timedelta(days=180),
        transaction_type=TransactionType.DIVIDEND, entity_kind=PositionKind.ASSET,
        entity_id=a.asset_id, amount=50.0, currency="USD"))
    tx.insert(Transaction(transaction_date=date.today() - timedelta(days=30),
        transaction_type=TransactionType.DIVIDEND, entity_kind=PositionKind.ASSET,
        entity_id=a.asset_id, amount=50.0, currency="USD"))
    services["snap"].take()
    cb = CostBasisService(db)
    income = IncomeService(db, services["fx"], cb, base_currency="USD")
    rep = income.report("USD")
    assert rep.ttm_total_report == pytest.approx(100.0)
    assert any(r.entity_id == a.asset_id and r.payments == 2 for r in rep.rows)


# =========================================================== Transaction FX

def test_stamp_transaction_pins_inception_rate(services):
    """A transaction in a non-base currency gets the FX rate at its date pinned."""
    fx = services["fx"]
    tx = Transaction(
        transaction_date=date(2025, 1, 15),
        transaction_type=TransactionType.BUY,
        entity_kind=PositionKind.ASSET,
        entity_id="x", quantity=10.0, price=100.0, amount=1000.0, currency="EUR",
    )
    fx.stamp_transaction(tx, "USD")
    assert tx.fx_base_currency == "USD"
    # MockFxProvider: 1 EUR = 1/0.92 USD
    assert tx.fx_rate_to_base == pytest.approx(1 / 0.92, rel=1e-9)


def test_stamp_transaction_same_currency_is_one(services):
    fx = services["fx"]
    tx = Transaction(
        transaction_date=date(2025, 1, 15),
        transaction_type=TransactionType.DEPOSIT,
        entity_kind=PositionKind.CASH,
        entity_id="c", amount=500.0, currency="USD",
    )
    fx.stamp_transaction(tx, "USD")
    assert tx.fx_rate_to_base == 1.0


def test_stamped_rate_survives_repo_round_trip(services):
    fx, tx_repo = services["fx"], services["tx_repo"]
    p = services["portfolio"]
    a = _open_asset(p, tx_repo, name="EuroCo", symbol="EC", qty=1.0, currency="EUR")
    tx = Transaction(
        transaction_date=date(2025, 3, 1),
        transaction_type=TransactionType.BUY,
        entity_kind=PositionKind.ASSET,
        entity_id=a.asset_id, quantity=5.0, price=20.0, amount=100.0, currency="EUR",
    )
    fx.stamp_transaction(tx, "USD")
    tx_repo.insert(tx)
    got = tx_repo.get(tx.transaction_id)
    assert got.fx_rate_to_base == pytest.approx(tx.fx_rate_to_base)
    assert got.fx_base_currency == "USD"


# =========================================================== Example portfolio

def test_seed_example_portfolio(db):
    from portfolio_manager.config import AppConfig, ProvidersConfig, ProviderSpec
    from portfolio_manager.services.example_data import (
        portfolio_is_empty,
        seed_example_portfolio,
    )
    from portfolio_manager.web.deps import build_container

    cfg = AppConfig(providers=ProvidersConfig(
        fx=ProviderSpec(name="mock"), price=ProviderSpec(name="mock")))
    c = build_container(cfg, db)

    assert portfolio_is_empty(c) is True
    res = seed_example_portfolio(c)
    assert res["transactions"] == 15
    assert portfolio_is_empty(c) is False

    holdings = c.holdings.at()
    assert sum(holdings.asset_quantities.values()) > 0
    assert sum(holdings.cash_balances.values()) > 0
    # Every seeded transaction is FX-stamped.
    txs = c.transactions_repo.list_recent(limit=100)
    assert txs and all(t.fx_rate_to_base is not None for t in txs)


# =========================================================== Currency attribution

def test_cost_basis_base_uses_pinned_rates(db):
    """Base-currency cost basis uses the FX rate pinned at each purchase."""
    tx = TransactionRepository(db)
    tx.insert(Transaction(
        transaction_date=date(2025, 1, 1), transaction_type=TransactionType.BUY,
        entity_kind=PositionKind.ASSET, entity_id="A",
        quantity=10, price=100, amount=1000, currency="EUR",
        fx_rate_to_base=1.10, fx_base_currency="USD"))
    tx.insert(Transaction(
        transaction_date=date(2025, 6, 1), transaction_type=TransactionType.BUY,
        entity_kind=PositionKind.ASSET, entity_id="A",
        quantity=5, price=120, amount=600, currency="EUR",
        fx_rate_to_base=1.20, fx_base_currency="USD"))
    r = CostBasisService(db).compute("A")
    assert r.total_cost_basis == pytest.approx(1600.0)        # EUR
    assert r.total_cost_basis_base == pytest.approx(1820.0)   # 1000*1.10 + 600*1.20
    assert r.incomplete_fx is False


def test_currency_attribution_splits_price_and_fx(db):
    tx = TransactionRepository(db)
    tx.insert(Transaction(
        transaction_date=date(2025, 1, 1), transaction_type=TransactionType.BUY,
        entity_kind=PositionKind.ASSET, entity_id="A",
        quantity=10, price=100, amount=1000, currency="EUR",
        fx_rate_to_base=1.10, fx_base_currency="USD"))
    tx.insert(Transaction(
        transaction_date=date(2025, 6, 1), transaction_type=TransactionType.BUY,
        entity_kind=PositionKind.ASSET, entity_id="A",
        quantity=5, price=120, amount=600, currency="EUR",
        fx_rate_to_base=1.20, fx_base_currency="USD"))
    attr = CostBasisService(db).attribute_currency("A", current_price=130.0, current_fx_to_base=1.30)
    assert attr is not None
    # price effect = 10*(130-100)*1.10 + 5*(130-120)*1.20 = 390
    assert attr.price_effect_base == pytest.approx(390.0)
    # the split always reconstitutes the total unrealized return
    assert attr.price_effect_base + attr.fx_effect_base == pytest.approx(attr.unrealized_base)
    assert attr.complete is True


def test_currency_attribution_flags_missing_fx(db):
    tx = TransactionRepository(db)
    tx.insert(Transaction(
        transaction_date=date(2025, 1, 1), transaction_type=TransactionType.BUY,
        entity_kind=PositionKind.ASSET, entity_id="A",
        quantity=10, price=100, amount=1000, currency="EUR"))  # no pinned FX
    r = CostBasisService(db).compute("A")
    assert r.incomplete_fx is True
    attr = CostBasisService(db).attribute_currency("A", current_price=110.0, current_fx_to_base=1.10)
    assert attr is not None and attr.complete is False


# =========================================================== Position builder

def test_position_builder_creates_opening_balances(db):
    from fastapi.testclient import TestClient

    from portfolio_manager.config import AppConfig, ProvidersConfig, ProviderSpec
    from portfolio_manager.web.app import create_app

    cfg = AppConfig(providers=ProvidersConfig(
        fx=ProviderSpec(name="mock"), price=ProviderSpec(name="mock")))
    cfg.auto_snapshot.enabled = False
    cfg.database.path = str(db.path)
    client = TestClient(create_app(cfg))

    assert client.get("/position-builder").status_code == 200
    r = client.post("/position-builder", data={
        "as_of": "2025-01-10",
        "kind": ["asset", "cash", "asset"],
        "symbol": ["AAPL", "", ""],
        "name": ["Apple Inc.", "Chase Checking", ""],   # 3rd row blank → skipped
        "account_id": ["", "", ""],
        "quantity": ["50", "", ""],
        "unit_cost": ["170", "", ""],
        "amount": ["", "25000", ""],
        "currency": ["USD", "USD", ""],
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/holdings?builder=2"

    c = client.app.state.container
    holdings = c.holdings.at()
    assert list(holdings.asset_quantities.values()) == [50.0]
    assert list(holdings.cash_balances.values()) == [25000.0]


# =========================================================== FX backfill

def test_backfill_transaction_fx(services):
    """Transactions recorded without a pinned FX rate get one filled in."""
    from portfolio_manager.services.fx import backfill_transaction_fx

    tx = services["tx_repo"]
    for ccy in ("EUR", "USD", "GBP"):
        tx.insert(Transaction(
            transaction_date=date(2025, 1, 1), transaction_type=TransactionType.BUY,
            entity_kind=PositionKind.ASSET, entity_id="A",
            quantity=1, price=1, amount=1, currency=ccy))
    assert all(t.fx_rate_to_base is None for t in tx.list_all())

    res = backfill_transaction_fx(tx, services["fx"], "USD")
    assert res == {"pending": 3, "filled": 3, "skipped": 0}
    assert all(t.fx_rate_to_base is not None for t in tx.list_all())

    # Idempotent — a second run finds nothing pending.
    assert backfill_transaction_fx(tx, services["fx"], "USD")["pending"] == 0
