from __future__ import annotations

import pytest

from portfolio_manager.domain.enums import AssetClass, InstrumentType, LiabilityType
from portfolio_manager.domain.exceptions import NotFoundError
from portfolio_manager.domain.models import Asset, CashHolding, Liability


def test_add_remove_reactivate_asset(services):
    p = services["portfolio"]
    a = p.add_asset(Asset(
        name="Test", instrument_type=InstrumentType.EQUITY, asset_class=AssetClass.EQUITY,
        currency="USD",
    ))
    assert a.asset_id
    assert any(x.asset_id == a.asset_id for x in p.list_assets())

    p.remove_asset(a.asset_id)
    assert not any(x.asset_id == a.asset_id for x in p.list_assets())
    assert any(x.asset_id == a.asset_id for x in p.list_assets(include_inactive=True))

    a2 = p.assets.get(a.asset_id)
    a2.is_active = True
    p.update_asset(a2)
    assert any(x.asset_id == a.asset_id for x in p.list_assets())


def test_remove_hard_deletes(services):
    p = services["portfolio"]
    a = p.add_asset(Asset(
        name="Test2", instrument_type=InstrumentType.EQUITY, asset_class=AssetClass.EQUITY,
        currency="USD",
    ))
    p.remove_asset(a.asset_id, hard=True)
    with pytest.raises(NotFoundError):
        p.assets.get(a.asset_id)


def test_cash_and_liability_lifecycles(services):
    p = services["portfolio"]
    c = p.add_cash(CashHolding(account_name="A", currency="USD"))
    l = p.add_liability(Liability(
        name="L", liability_type=LiabilityType.LOAN, currency="USD",
    ))
    assert c.cash_id and l.liability_id
    p.remove_cash(c.cash_id)
    p.remove_liability(l.liability_id)
    assert not any(x.cash_id == c.cash_id for x in p.list_cash())
    assert not any(x.liability_id == l.liability_id for x in p.list_liabilities())
