"""Default market and sector indices for an asset.

Each asset's return is split against a broad market index and a sector index.
The app assigns sensible defaults on asset creation — the market index from the
asset's currency, the sector index from its sector — and the user can override
either on the asset's page. Index symbols are whatever the configured price
provider understands (yfinance tickers by default).
"""
from __future__ import annotations

# Broad market index by the asset's trading currency.
_MARKET_INDEX_BY_CURRENCY: dict[str, str] = {
    "USD": "^GSPC",     # S&P 500
    "CAD": "^GSPTSE",   # S&P/TSX Composite
    "GBP": "^FTSE",     # FTSE 100
    "EUR": "^STOXX",    # STOXX Europe 600
    "CHF": "^SSMI",     # Swiss Market Index
    "SEK": "^OMX",      # OMX Stockholm 30
    "JPY": "^N225",     # Nikkei 225
    "AUD": "^AXJO",     # S&P/ASX 200
    "HKD": "^HSI",      # Hang Seng
    "ILS": "^TA125.TA", # Tel Aviv 125
}
_MARKET_INDEX_FALLBACK = "^GSPC"

# Sector index (SPDR sector ETF) by the asset's sector. Sector strings come
# from the lookup providers, so match case-insensitively on substrings.
_SECTOR_INDEX_BY_KEYWORD: list[tuple[str, str]] = [
    ("information technology", "XLK"),
    ("technology", "XLK"),
    ("financial", "XLF"),
    ("health", "XLV"),
    ("energy", "XLE"),
    ("consumer discretionary", "XLY"),
    ("consumer cyclical", "XLY"),
    ("consumer staples", "XLP"),
    ("consumer defensive", "XLP"),
    ("industrial", "XLI"),
    ("materials", "XLB"),
    ("basic materials", "XLB"),
    ("utilities", "XLU"),
    ("real estate", "XLRE"),
    ("communication", "XLC"),
]


def default_market_index(currency: str | None) -> str:
    return _MARKET_INDEX_BY_CURRENCY.get((currency or "").upper(), _MARKET_INDEX_FALLBACK)


def default_sector_index(sector: str | None) -> str | None:
    s = (sector or "").strip().lower()
    if not s:
        return None
    for keyword, symbol in _SECTOR_INDEX_BY_KEYWORD:
        if keyword in s:
            return symbol
    return None


def assign_default_indices(asset) -> None:
    """Fill an asset's market/sector index symbols in place when they are unset.
    Leaves any value the user already chose untouched."""
    if not asset.market_index_symbol:
        asset.market_index_symbol = default_market_index(asset.currency)
    if not asset.sector_index_symbol:
        asset.sector_index_symbol = default_sector_index(asset.sector)
