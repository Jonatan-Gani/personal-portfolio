from __future__ import annotations

from datetime import date

from portfolio_manager.domain.enums import (
    AssetClass,
    InstrumentType,
    LiabilityType,
    PositionKind,
    TransactionType,
)
from portfolio_manager.domain.models import Asset, CashHolding, Liability, Transaction


def _open_asset(portfolio, tx_repo, *, name, symbol, quantity, currency, country=None,
                instrument_type=InstrumentType.EQUITY, asset_class=AssetClass.EQUITY, tags=None):
    a = Asset(
        name=name, symbol=symbol,
        instrument_type=instrument_type, asset_class=asset_class,
        currency=currency, country=country, tags=tags or [],
    )
    portfolio.add_asset(a)
    tx_repo.insert(Transaction(
        transaction_date=date.today(),
        transaction_type=TransactionType.OPENING_BALANCE,
        entity_kind=PositionKind.ASSET,
        entity_id=a.asset_id,
        quantity=quantity,
        amount=0.0,
        currency=currency,
        notes="seed",
    ))
    return a


def _open_cash(portfolio, tx_repo, *, account_name, balance, currency, country=None):
    c = CashHolding(account_name=account_name, currency=currency, country=country)
    portfolio.add_cash(c)
    tx_repo.insert(Transaction(
        transaction_date=date.today(),
        transaction_type=TransactionType.OPENING_BALANCE,
        entity_kind=PositionKind.CASH,
        entity_id=c.cash_id,
        amount=balance,
        currency=currency,
        notes="seed",
    ))
    return c


def _seed(services):
    p, tx = services["portfolio"], services["tx_repo"]
    aapl = _open_asset(p, tx, name="Apple", symbol="AAPL", quantity=10.0, currency="USD", country="US")
    _open_asset(p, tx, name="Vanguard FTSE All-World", symbol="VWCE", quantity=20.0,
                currency="EUR", country="IE", instrument_type=InstrumentType.ETF, tags=["core"])
    _open_asset(p, tx, name="German Bund", symbol="BUND", quantity=5.0,
                currency="EUR", country="DE",
                instrument_type=InstrumentType.GOVERNMENT_BOND, asset_class=AssetClass.FIXED_INCOME)
    _open_cash(p, tx, account_name="Checking", balance=5000.0, currency="USD", country="US")
    _open_cash(p, tx, account_name="EU broker", balance=1000.0, currency="EUR")
    loan = p.add_liability(Liability(
        name="Student loan", liability_type=LiabilityType.LOAN,
        currency="USD", interest_rate=0.045,
    ))
    tx.insert(Transaction(
        transaction_date=date.today(),
        transaction_type=TransactionType.OPENING_BALANCE,
        entity_kind=PositionKind.LIABILITY,
        entity_id=loan.liability_id,
        amount=15000.0,
        currency="USD",
        notes="seed",
    ))
    return aapl


def test_take_snapshot_persists_values_in_all_currencies(services):
    _seed(services)
    meta = services["snap"].take(notes="t1")

    assert meta.snapshot_id
    assert set(meta.reporting_currencies) >= {"USD", "EUR", "GBP", "SEK", "ILS"}

    rows = services["snap_repo"].positions_with_values(meta.snapshot_id)
    assert rows
    for r in rows:
        v = r["values_by_currency"]
        assert isinstance(v, dict)
        assert "USD" in v and "EUR" in v and "ILS" in v


def test_exposure_by_currency_sums_to_assets_plus_cash(services):
    _seed(services)
    services["snap"].take()
    rows = services["exposure"].by_dimension(
        "currency", "USD", kinds=["asset", "cash"]
    )
    assert rows
    total = sum(r["value"] for r in rows)
    latest = services["snap_repo"].latest()
    assert abs(total - (latest.total_assets_base + latest.total_cash_base)) < 0.01


def test_period_return_handles_two_snapshots(services):
    aapl = _seed(services)
    s1 = services["snap"].take(notes="t1")

    # Buy 2 more AAPL — quantity now derived to 12
    services["tx_repo"].insert(Transaction(
        transaction_date=date.today(),
        transaction_type=TransactionType.BUY,
        entity_kind=PositionKind.ASSET,
        entity_id=aapl.asset_id,
        quantity=2.0,
        price=200.0,
        amount=400.0,
        currency="USD",
    ))
    s2 = services["snap"].take(notes="t2")

    res = services["returns"].period_return(
        from_snapshot_id=s1.snapshot_id,
        to_snapshot_id=s2.snapshot_id,
        report_currency="USD",
        kinds=["asset"],
    )
    assert res["from_value"] > 0
    assert res["to_value"] > res["from_value"]
    assert res["pct_change"] > 0


def test_eur_assets_in_usd_vs_in_eur(services):
    _seed(services)
    s = services["snap"].take()
    eur_in_usd = services["returns"].total_value(
        s.snapshot_id, report_currency="USD", asset_currency="EUR", kinds=["asset"]
    )
    eur_in_eur = services["returns"].total_value(
        s.snapshot_id, report_currency="EUR", asset_currency="EUR", kinds=["asset"]
    )
    assert eur_in_usd > 0 and eur_in_eur > 0
    # MockFxProvider: 1 USD = 0.92 EUR  =>  1 EUR ≈ 1.087 USD
    ratio = eur_in_usd / eur_in_eur
    assert 1.05 < ratio < 1.12


def test_holdings_derives_state_from_transactions(services):
    aapl = _seed(services)
    h = services["holdings"]
    state = h.at()
    # AAPL opens at 10; no further txns
    assert state.asset_quantities[aapl.asset_id] == 10.0

    # buy 2 more
    services["tx_repo"].insert(Transaction(
        transaction_date=date.today(),
        transaction_type=TransactionType.BUY,
        entity_kind=PositionKind.ASSET,
        entity_id=aapl.asset_id,
        quantity=2.0,
        price=200.0,
        amount=400.0,
        currency="USD",
    ))
    assert services["holdings"].at().asset_quantities[aapl.asset_id] == 12.0

    # sell 5
    services["tx_repo"].insert(Transaction(
        transaction_date=date.today(),
        transaction_type=TransactionType.SELL,
        entity_kind=PositionKind.ASSET,
        entity_id=aapl.asset_id,
        quantity=5.0,
        price=210.0,
        amount=1050.0,
        currency="USD",
    ))
    assert services["holdings"].at().asset_quantities[aapl.asset_id] == 7.0


def test_holdings_handles_split(services):
    aapl = _seed(services)
    # 2-for-1 split: quantity multiplies by 2
    services["tx_repo"].insert(Transaction(
        transaction_date=date.today(),
        transaction_type=TransactionType.SPLIT,
        entity_kind=PositionKind.ASSET,
        entity_id=aapl.asset_id,
        quantity=2.0,
        amount=0.0,
        currency="USD",
    ))
    assert services["holdings"].at().asset_quantities[aapl.asset_id] == 20.0
