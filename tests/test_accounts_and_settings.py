from __future__ import annotations

from portfolio_manager.domain.models import Account, AccountGroup, Asset, CashHolding, Liability
from portfolio_manager.domain.enums import AssetClass, InstrumentType, LiabilityType
from portfolio_manager.repositories import (
    AccountGroupRepository,
    AccountRepository,
    AppSettingsRepository,
    AssetRepository,
    CashRepository,
    LiabilityRepository,
)


def test_account_group_and_account_crud(db):
    groups = AccountGroupRepository(db)
    accounts = AccountRepository(db)

    g = groups.upsert(AccountGroup(name="Household", kind="household"))
    assert groups.get(g.group_id).name == "Household"
    assert [x.name for x in groups.list_active()] == ["Household"]

    a = accounts.upsert(Account(
        name="IBKR taxable", group_id=g.group_id, broker="IBKR",
        account_type="taxable", currency="USD", country="US",
    ))
    fetched = accounts.get(a.account_id)
    assert fetched.broker == "IBKR"
    assert fetched.group_id == g.group_id

    # Holdings reference an account
    AssetRepository(db).upsert(Asset(
        name="VOO", symbol="VOO", instrument_type=InstrumentType.ETF,
        asset_class=AssetClass.EQUITY, currency="USD", account_id=a.account_id,
    ))
    CashRepository(db).upsert(CashHolding(
        account_name="Cash @ IBKR", currency="USD", account_id=a.account_id,
    ))
    LiabilityRepository(db).upsert(Liability(
        name="Margin loan", liability_type=LiabilityType.MARGIN,
        currency="USD", account_id=a.account_id,
    ))

    # Deleting the account detaches holdings rather than leaving dangling FK
    accounts.delete(a.account_id)
    assets = AssetRepository(db).list_active()
    cash = CashRepository(db).list_active()
    liabs = LiabilityRepository(db).list_active()
    assert all(x.account_id is None for x in assets)
    assert all(x.account_id is None for x in cash)
    assert all(x.account_id is None for x in liabs)


def test_app_settings_roundtrip(db):
    s = AppSettingsRepository(db)
    assert s.get("reporting.base_currency", "USD") == "USD"  # default fallback
    s.set("reporting.base_currency", "EUR")
    s.set("reporting.reporting_currencies", ["EUR", "USD", "GBP"])
    s.set("ui.privacy_mode", True)
    s.set("auto_snapshot.stale_after_minutes", 60)

    assert s.get("reporting.base_currency") == "EUR"
    assert s.get("reporting.reporting_currencies") == ["EUR", "USD", "GBP"]
    assert s.get("ui.privacy_mode") is True
    assert s.get("auto_snapshot.stale_after_minutes") == 60

    all_ = s.all()
    assert all_["reporting.base_currency"] == "EUR"

    s.delete("ui.privacy_mode")
    assert s.get("ui.privacy_mode", "default") == "default"
