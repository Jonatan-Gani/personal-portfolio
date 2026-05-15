from __future__ import annotations

from portfolio_manager.domain.enums import AssetClass, InstrumentType, PositionKind, TransactionType
from portfolio_manager.domain.models import (
    Account, AccountGroup, Asset, CashHolding, Transaction,
)
from portfolio_manager.repositories import (
    AccountGroupRepository, AccountRepository, AppSettingsRepository,
)
from portfolio_manager.services.scope import parse_scope, scope_filter_sql


class _StubContainer:
    """Minimal stand-in for the FastAPI container in scope tests."""
    def __init__(self, accounts_repo, groups_repo):
        self.accounts_repo = accounts_repo
        self.account_groups_repo = groups_repo
        self.app_settings_repo = None


def test_scope_filter_sql_variants():
    # No filter — pass-through
    sql, params = scope_filter_sql(None)
    assert sql == "" and params == []

    # Unassigned sentinel
    sql, params = scope_filter_sql(["__unassigned__"])
    assert "IS NULL" in sql and params == []

    # Specific ids
    sql, params = scope_filter_sql(["a", "b", "c"])
    assert "IN (?,?,?)" in sql
    assert params == ["a", "b", "c"]

    # Forced-empty
    sql, params = scope_filter_sql([])
    assert sql == "AND 1=0"


def test_parse_scope_resolves_each_kind(db):
    accounts = AccountRepository(db)
    groups = AccountGroupRepository(db)

    g = groups.upsert(AccountGroup(name="House", kind="household"))
    a = accounts.upsert(Account(name="IBKR", group_id=g.group_id, broker="IBKR", account_type="taxable"))
    c = _StubContainer(accounts, groups)

    label, ids, kind = parse_scope("all", c)
    assert kind == "all" and ids is None

    label, ids, kind = parse_scope("unassigned", c)
    assert kind == "unassigned" and ids == ["__unassigned__"]

    label, ids, kind = parse_scope(f"group:{g.group_id}", c)
    assert kind == "group"
    assert ids == [a.account_id]
    assert "House" in label

    label, ids, kind = parse_scope(f"account:{a.account_id}", c)
    assert kind == "account" and ids == [a.account_id]
    assert "IBKR" in label


def test_exposure_by_dimension_scoped_to_account(services, db):
    """Two accounts, two assets — exposure scoped to one account should
    only see that account's positions."""
    groups = AccountGroupRepository(db)
    accounts = AccountRepository(db)
    g = groups.upsert(AccountGroup(name="House"))
    a1 = accounts.upsert(Account(name="IBKR", group_id=g.group_id, broker="IBKR"))
    a2 = accounts.upsert(Account(name="Schwab", group_id=g.group_id, broker="Schwab"))

    portfolio = services["portfolio"]
    asset1 = portfolio.add_asset(Asset(
        name="AAPL", symbol="AAPL", instrument_type=InstrumentType.EQUITY,
        asset_class=AssetClass.EQUITY, currency="USD", account_id=a1.account_id,
    ))
    asset2 = portfolio.add_asset(Asset(
        name="BUND", symbol="BUND", instrument_type=InstrumentType.GOVERNMENT_BOND,
        asset_class=AssetClass.FIXED_INCOME, currency="EUR", account_id=a2.account_id,
    ))
    # Opening balances so quantities are non-zero
    from datetime import date
    services["tx_repo"].insert(Transaction(
        transaction_date=date(2024, 1, 1), transaction_type=TransactionType.OPENING_BALANCE,
        entity_kind=PositionKind.ASSET, entity_id=asset1.asset_id,
        quantity=10, amount=0, currency="USD",
    ))
    services["tx_repo"].insert(Transaction(
        transaction_date=date(2024, 1, 1), transaction_type=TransactionType.OPENING_BALANCE,
        entity_kind=PositionKind.ASSET, entity_id=asset2.asset_id,
        quantity=5, amount=0, currency="EUR",
    ))
    services["snap"].take()

    # Unscoped: both asset classes appear
    rows_all = services["exposure"].by_dimension("asset_class", "USD", kinds=["asset"])
    classes_all = {r["bucket"] for r in rows_all}
    assert "equity" in classes_all and "fixed_income" in classes_all

    # Scoped to a1 (only AAPL — equity)
    rows_a1 = services["exposure"].by_dimension(
        "asset_class", "USD", kinds=["asset"], account_ids=[a1.account_id]
    )
    classes_a1 = {r["bucket"] for r in rows_a1}
    assert classes_a1 == {"equity"}

    # Scoped to unassigned: nothing (both assets are assigned)
    rows_u = services["exposure"].by_dimension(
        "asset_class", "USD", kinds=["asset"], account_ids=["__unassigned__"]
    )
    assert rows_u == []


def test_exposure_latest_totals_scope(services, db):
    """latest_totals respects account scope."""
    groups = AccountGroupRepository(db)
    accounts = AccountRepository(db)
    g = groups.upsert(AccountGroup(name="X"))
    a = accounts.upsert(Account(name="acct", group_id=g.group_id))
    portfolio = services["portfolio"]
    cash = portfolio.add_cash(CashHolding(account_name="c1", currency="USD", account_id=a.account_id))
    from datetime import date
    services["tx_repo"].insert(Transaction(
        transaction_date=date(2024, 1, 1), transaction_type=TransactionType.OPENING_BALANCE,
        entity_kind=PositionKind.CASH, entity_id=cash.cash_id,
        amount=1234.0, currency="USD",
    ))
    services["snap"].take()

    totals = services["exposure"].latest_totals("USD", account_ids=[a.account_id])
    assert totals["cash"] == 1234.0
    assert totals["net_worth"] == 1234.0

    totals_u = services["exposure"].latest_totals("USD", account_ids=["__unassigned__"])
    assert totals_u["cash"] == 0.0
