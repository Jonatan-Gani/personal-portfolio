from __future__ import annotations

from datetime import date, timedelta

import pytest

from portfolio_manager.domain.exceptions import PriceUnavailable
from portfolio_manager.domain.models import Price
from portfolio_manager.providers.base import PriceProvider, PriceQuote
from portfolio_manager.repositories.prices import PriceCache
from portfolio_manager.services.asset_lookup import (
    AssetLookupService,
    is_valid_isin_format,
    parse_openfigi_record,
)
from portfolio_manager.services.markets import MarketsService


# ---------------------------------------------------------------- ISIN format

@pytest.mark.parametrize("code,expected", [
    ("US0378331005", True),   # AAPL
    ("US9229083632", True),   # VWO
    ("DE000BASF111", True),   # BASF
    ("US0378331004", False),  # bad checksum
    ("US0378331006", False),
    ("US037833100",  False),  # too short
    ("US03783310055", False), # too long
    ("FOO",          False),
    ("",             False),
    ("us0378331005", True),   # lower-case still works (normalised)
])
def test_isin_format(code, expected):
    assert is_valid_isin_format(code) is expected


def test_asset_lookup_format_check_short_circuits():
    """ISIN with bad format must NOT hit any network."""
    s = AssetLookupService()
    r = s.lookup(isin="US0378331004")  # bad checksum
    assert r.ok is False
    assert "ISIN" in (r.error or "")


def test_asset_lookup_requires_input():
    s = AssetLookupService()
    r = s.lookup()
    assert r.ok is False


# ---------------------------------------------------------------- OpenFIGI parse

def test_parse_openfigi_common_stock():
    rec = {
        "figi": "BBG000B9XRY4", "name": "APPLE INC", "ticker": "AAPL",
        "exchCode": "US", "securityType": "Common Stock", "marketSector": "Equity",
    }
    out = parse_openfigi_record(rec)
    assert out["symbol"] == "AAPL"
    assert out["name"] == "APPLE INC"
    assert out["exchange"] == "US"
    assert out["instrument_type"] == "equity"
    assert out["asset_class"] == "equity"


def test_parse_openfigi_etf_and_bond():
    etf = parse_openfigi_record({"ticker": "vwce", "securityType": "ETP", "marketSector": "Equity"})
    assert etf["instrument_type"] == "etf"
    assert etf["symbol"] == "VWCE"          # normalised upper-case

    bond = parse_openfigi_record({"ticker": "T", "securityType": "", "marketSector": "Govt"})
    assert bond["asset_class"] == "fixed_income"
    assert bond["instrument_type"] == "government_bond"


def test_openfigi_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENFIGI_API_KEY", "test-key-123")
    assert AssetLookupService().openfigi_api_key == "test-key-123"
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    assert AssetLookupService().openfigi_api_key is None


# ---------------------------------------------------------------- Markets

class _FakePriceProvider(PriceProvider):
    """Provider stub for offline testing of MarketsService."""
    name = "fake"

    def __init__(self, history: dict[str, list[tuple[date, float, str]]] | None = None,
                 fail: set[str] | None = None):
        self._history = history or {}
        self._fail = fail or set()

    def get_price(self, symbol: str, as_of: date | None = None) -> PriceQuote:
        if symbol in self._fail:
            raise PriceUnavailable(f"forced fail {symbol}")
        hist = self._history.get(symbol, [])
        if not hist:
            raise PriceUnavailable("no data")
        d, p, c = hist[-1]
        return PriceQuote(symbol=symbol, price=p, currency=c, as_of=d)

    def get_history(self, symbol, start, end=None):
        if symbol in self._fail:
            raise PriceUnavailable(f"forced fail {symbol}")
        return [
            PriceQuote(symbol=symbol, price=p, currency=c, as_of=d)
            for (d, p, c) in self._history.get(symbol, [])
        ]


def test_markets_quote_computes_change(db):
    today = date.today()
    yesterday = today - timedelta(days=1)
    cache = PriceCache(db)
    provider = _FakePriceProvider(history={
        "^GSPC": [(yesterday, 5000.0, "USD"), (today, 5100.0, "USD")],
    })
    svc = MarketsService(provider=provider, cache=cache)
    q = svc.quote("^GSPC", label="S&P 500")
    assert q.price == 5100.0
    assert q.prev_price == 5000.0
    assert q.change_abs == pytest.approx(100.0)
    assert q.change_pct == pytest.approx(0.02)
    assert q.error is None


def test_markets_quote_uses_cache_on_subsequent_call(db):
    today = date.today()
    yesterday = today - timedelta(days=1)
    cache = PriceCache(db)
    provider = _FakePriceProvider(history={
        "BTC-USD": [(yesterday, 60000.0, "USD"), (today, 61500.0, "USD")],
    })
    svc = MarketsService(provider=provider, cache=cache)
    q1 = svc.quote("BTC-USD")
    assert q1.price == 61500.0
    # The cache should now have today's price persisted
    hit = cache.get_fresh(today, "BTC-USD", "fake", ttl_hours=24)
    assert hit is not None
    assert hit[0] == 61500.0


def test_markets_quote_graceful_on_provider_failure(db):
    provider = _FakePriceProvider(fail={"^GSPC"})
    svc = MarketsService(provider=provider, cache=PriceCache(db))
    q = svc.quote("^GSPC", "S&P 500")
    assert q.price is None
    assert q.error is not None


def test_markets_watchlist_iterates_items(db):
    today = date.today()
    yesterday = today - timedelta(days=1)
    provider = _FakePriceProvider(history={
        "A": [(yesterday, 1.0, "USD"), (today, 1.1, "USD")],
        "B": [(yesterday, 200.0, "EUR"), (today, 198.0, "EUR")],
    })
    svc = MarketsService(provider=provider, cache=PriceCache(db))
    out = svc.watchlist([{"symbol": "A", "label": "A-label"}, {"symbol": "B"}])
    assert [q.symbol for q in out] == ["A", "B"]
    assert out[0].label == "A-label"
    assert out[1].label == "B"
    assert out[0].change_pct == pytest.approx(0.1)
    assert out[1].change_pct == pytest.approx(-0.01)


# ---------------------------------------------------------------- transactions friendly form

def test_transaction_route_split_entity_parser():
    from portfolio_manager.web.routes.transactions import _split_entity
    from portfolio_manager.domain.enums import PositionKind
    kind, eid = _split_entity("asset:abc-123")
    assert kind is PositionKind.ASSET and eid == "abc-123"
    kind, eid = _split_entity("cash:c1")
    assert kind is PositionKind.CASH
    kind, eid = _split_entity("liability:l1")
    assert kind is PositionKind.LIABILITY
