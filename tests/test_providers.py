from __future__ import annotations

import pytest

from portfolio_manager.providers.ecb_fx import _parse_latest_obs
from portfolio_manager.providers.mock import MockFxProvider, MockPriceProvider
from portfolio_manager.providers.registry import build_fx_provider, build_price_provider


def test_mock_fx_cross_rates():
    p = MockFxProvider()
    assert p.get_rate("USD", "USD") == 1.0
    assert p.get_rate("USD", "EUR") == pytest.approx(0.92)
    assert p.get_rate("EUR", "USD") == pytest.approx(1 / 0.92, rel=1e-6)
    assert p.get_rate("EUR", "GBP") == pytest.approx(0.79 / 0.92, rel=1e-6)


def test_mock_price_provider():
    p = MockPriceProvider({"X": (10.0, "USD")})
    q = p.get_price("X")
    assert q.price == 10.0
    assert q.currency == "USD"


def test_registry_builds_known_providers():
    assert build_fx_provider("mock", {}).name == "mock"
    assert build_price_provider("mock", {}).name == "mock"
    assert build_fx_provider("ecb", {}).name == "ecb"
    assert build_price_provider("yfinance", {}).name == "yfinance"


def test_registry_unknown_raises():
    from portfolio_manager.domain.exceptions import ConfigError
    with pytest.raises(ConfigError):
        build_fx_provider("does-not-exist", {})


def test_ecb_csv_parser_picks_latest():
    sample = (
        "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE\n"
        "X,D,USD,EUR,SP00,A,2024-01-02,1.10\n"
        "X,D,USD,EUR,SP00,A,2024-01-03,1.12\n"
        "X,D,USD,EUR,SP00,A,2024-01-04,1.11\n"
    )
    assert _parse_latest_obs(sample) == 1.11
