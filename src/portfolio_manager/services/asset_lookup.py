"""Asset metadata lookup: given a ticker and/or ISIN, resolve and enrich a
security against several free financial-data sources, returning metadata that
pre-fills the add-asset form.

Sources, each authoritative for the fields it does best:

  - OpenFIGI  (api.openfigi.com) — Bloomberg's open symbology. Maps an ISIN to
                its ticker / exchange / security type. Free; an API key only
                raises the rate limit (set ``OPENFIGI_API_KEY``).
  - SEC EDGAR (sec.gov) — authoritative company names for US-listed tickers.
  - yfinance  — sector, country, currency, and a best-effort ISIN for a ticker.

Every source is optional and best-effort: if one is unreachable the lookup
still returns whatever the others produced. The result is never raised as an
exception — callers branch on ``ok``.

ISIN validation is structural only (ISO 6166 mod-10 checksum). A source ISIN
that conflicts with a caller-provided one is surfaced, not silently resolved.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)

_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
_EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# SEC asks API clients to send a descriptive User-Agent identifying the caller.
_EDGAR_USER_AGENT = "portfolio-manager (personal use; contact via project repo)"
_HTTP_TIMEOUT = 10.0


# yfinance.quoteType → our (instrument_type, asset_class). Best-effort.
_QUOTE_TYPE_MAP: dict[str, tuple[str, str]] = {
    "EQUITY":          ("equity",          "equity"),
    "ETF":             ("etf",             "equity"),
    "MUTUALFUND":      ("mutual_fund",     "equity"),
    "CRYPTOCURRENCY":  ("crypto",          "alternative"),
    "CURRENCY":        ("cash",            "cash"),
    "INDEX":           ("other",           "other"),
    "FUTURE":          ("commodity",       "real_asset"),
    "BOND":            ("government_bond", "fixed_income"),
    "OPTION":          ("other",           "alternative"),
}

# OpenFIGI marketSector → our asset_class.
_FIGI_SECTOR_CLASS: dict[str, str] = {
    "EQUITY":  "equity",
    "PFD":     "equity",
    "GOVT":    "fixed_income",
    "CORP":    "fixed_income",
    "MUNI":    "fixed_income",
    "MTGE":    "fixed_income",
    "COMDTY":  "real_asset",
    "CURNCY":  "cash",
    "INDEX":   "other",
}


def _figi_instrument_type(security_type: str, market_sector: str) -> str:
    """Map an OpenFIGI securityType / marketSector to our InstrumentType value."""
    st = (security_type or "").upper()
    if "ETP" in st or "ETF" in st or "EXCHANGE TRADED" in st:
        return "etf"
    if "MUTUAL FUND" in st or "OPEN-END FUND" in st or "FUND OF FUNDS" in st:
        return "mutual_fund"
    if "REIT" in st:
        return "real_estate"
    if "COMMON STOCK" in st or "ADR" in st or "DEPOSITARY" in st or "PREFERRED" in st:
        return "equity"
    ms = (market_sector or "").upper()
    return {
        "GOVT":   "government_bond",
        "CORP":   "corporate_bond",
        "COMDTY": "commodity",
        "CURNCY": "cash",
    }.get(ms, "other")


@dataclass
class AssetLookupResult:
    ok: bool
    source: str | None = None          # e.g. "openfigi+edgar+yfinance"
    symbol: str | None = None
    name: str | None = None
    currency: str | None = None
    isin: str | None = None
    sector: str | None = None
    country: str | None = None
    exchange: str | None = None
    instrument_type: str | None = None
    asset_class: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def is_valid_isin_format(isin: str) -> bool:
    """Structural format check + ISO 6166 mod-10 (Luhn-style) checksum.
    Returns False on any malformed input — does NOT raise."""
    if not isin:
        return False
    isin = isin.strip().upper()
    if not _ISIN_RE.match(isin):
        return False
    digits: list[int] = []
    for ch in isin:
        if ch.isdigit():
            digits.append(int(ch))
        else:
            n = ord(ch) - ord("A") + 10
            digits.append(n // 10)
            digits.append(n % 10)
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def parse_openfigi_record(data: dict) -> dict:
    """Pull the fields we care about out of one OpenFIGI mapping `data` record.
    Pure — no I/O — so it is unit-testable against captured responses."""
    security_type = data.get("securityType") or data.get("securityType2") or ""
    market_sector = data.get("marketSector") or ""
    return {
        "symbol": (data.get("ticker") or "").upper() or None,
        "name": data.get("name") or None,
        "exchange": data.get("exchCode") or None,
        "instrument_type": _figi_instrument_type(security_type, market_sector),
        "asset_class": _FIGI_SECTOR_CLASS.get(market_sector.upper(), "other"),
    }


class AssetLookupService:
    """Resolve enriched metadata for a ticker and/or ISIN by merging OpenFIGI,
    SEC EDGAR and yfinance. The OpenFIGI API key is optional — without one the
    public rate limit applies."""

    def __init__(self, openfigi_api_key: str | None = None, http_timeout: float = _HTTP_TIMEOUT):
        self.openfigi_api_key = openfigi_api_key or os.getenv("OPENFIGI_API_KEY") or None
        self.http_timeout = http_timeout
        self._cache: dict[tuple[str, str], AssetLookupResult] = {}
        self._edgar_index: dict[str, str] | None = None  # ticker → company title

    # ------------------------------------------------------------------ API
    def lookup(self, symbol: str | None = None, isin: str | None = None) -> AssetLookupResult:
        symbol = (symbol or "").strip().upper() or None
        isin = (isin or "").strip().upper() or None
        if not symbol and not isin:
            return AssetLookupResult(ok=False, error="provide a ticker or an ISIN")
        if isin and not is_valid_isin_format(isin):
            return AssetLookupResult(ok=False, isin=isin, error="ISIN format/checksum invalid")

        key = (symbol or "", isin or "")
        if key in self._cache:
            return self._cache[key]

        result = self._resolve(symbol, isin)
        self._cache[key] = result
        return result

    # -------------------------------------------------------------- resolve
    def _resolve(self, symbol: str | None, isin: str | None) -> AssetLookupResult:
        fields: dict[str, str | None] = {
            "symbol": symbol, "name": None, "currency": None, "isin": isin,
            "sector": None, "country": None, "exchange": None,
            "instrument_type": None, "asset_class": None,
        }
        sources: list[str] = []
        warnings: list[str] = []

        # 1. OpenFIGI — best for ISIN → ticker / exchange / security type.
        figi = self._openfigi(symbol, isin)
        if figi:
            sources.append("openfigi")
            for k in ("symbol", "name", "exchange", "instrument_type", "asset_class"):
                if figi.get(k) and not fields.get(k):
                    fields[k] = figi[k]

        # 2. SEC EDGAR — authoritative company name for US-listed tickers.
        ticker = fields.get("symbol")
        if ticker:
            edgar_name = self._edgar_name(ticker)
            if edgar_name:
                sources.append("edgar")
                fields["name"] = edgar_name  # EDGAR overrides — it is the cleanest US name

        # 3. yfinance — sector, country, currency, and a best-effort ISIN.
        yf = self._lookup_yfinance(fields.get("symbol") or symbol, isin)
        if yf.ok:
            sources.append("yfinance")
            for k in ("symbol", "name", "currency", "sector", "country",
                      "exchange", "instrument_type", "asset_class"):
                v = getattr(yf, k)
                if v and not fields.get(k):
                    fields[k] = v
            if yf.isin and not fields.get("isin"):
                fields["isin"] = yf.isin
            if yf.isin and isin and yf.isin != isin:
                warnings.append(f"ISIN mismatch: source has {yf.isin}, you provided {isin}")
        elif yf.error:
            warnings.append(yf.error)

        resolved = any(fields.get(k) for k in ("symbol", "name", "isin"))
        if not resolved:
            return AssetLookupResult(
                ok=False, symbol=symbol, isin=isin,
                error="no data found — fill in the fields manually",
            )
        return AssetLookupResult(
            ok=True,
            source="+".join(sources) or None,
            symbol=fields["symbol"],
            name=fields["name"],
            currency=(fields["currency"] or "").upper() or None,
            isin=fields["isin"],
            sector=fields["sector"],
            country=fields["country"],
            exchange=fields["exchange"],
            instrument_type=fields["instrument_type"],
            asset_class=fields["asset_class"],
            error="; ".join(warnings) or None,
        )

    # --------------------------------------------------------------- OpenFIGI
    def _openfigi(self, symbol: str | None, isin: str | None) -> dict | None:
        """Map an ISIN (preferred) or ticker via OpenFIGI. Returns parsed fields
        or None if the source produced nothing / was unreachable."""
        if isin:
            job = {"idType": "ID_ISIN", "idValue": isin}
        elif symbol:
            job = {"idType": "TICKER", "idValue": symbol}
        else:
            return None
        try:
            import httpx
        except ImportError:
            return None
        headers = {"Content-Type": "application/json"}
        if self.openfigi_api_key:
            headers["X-OPENFIGI-APIKEY"] = self.openfigi_api_key
        try:
            resp = httpx.post(
                _OPENFIGI_URL, json=[job], headers=headers, timeout=self.http_timeout
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            log.warning("OpenFIGI lookup failed for %s: %s", isin or symbol, e)
            return None
        if not isinstance(payload, list) or not payload:
            return None
        records = payload[0].get("data") if isinstance(payload[0], dict) else None
        if not records:
            return None
        return parse_openfigi_record(records[0])

    # ----------------------------------------------------------------- EDGAR
    def _edgar_name(self, ticker: str) -> str | None:
        index = self._edgar_load()
        return index.get(ticker.upper())

    def _edgar_load(self) -> dict[str, str]:
        """Lazily download and cache SEC EDGAR's ticker → company-name table.
        On failure returns {} without caching, so a later call can retry."""
        if self._edgar_index is not None:
            return self._edgar_index
        try:
            import httpx
        except ImportError:
            return {}
        try:
            resp = httpx.get(
                _EDGAR_TICKERS_URL,
                headers={"User-Agent": _EDGAR_USER_AGENT},
                timeout=self.http_timeout,
            )
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            log.warning("SEC EDGAR ticker list unavailable: %s", e)
            return {}
        index: dict[str, str] = {}
        # EDGAR shape: {"0": {"cik_str": .., "ticker": "AAPL", "title": "Apple Inc."}, ...}
        for row in (raw.values() if isinstance(raw, dict) else []):
            if isinstance(row, dict) and row.get("ticker") and row.get("title"):
                index[str(row["ticker"]).upper()] = str(row["title"])
        self._edgar_index = index
        return index

    # -------------------------------------------------------------- yfinance
    def _lookup_yfinance(self, symbol: str | None, isin: str | None) -> AssetLookupResult:
        try:
            import yfinance as yf
        except ImportError:
            return AssetLookupResult(
                ok=False, symbol=symbol, isin=isin,
                error="yfinance not installed — some fields unavailable",
            )
        query = symbol or isin
        if not query:
            return AssetLookupResult(ok=False, error="nothing to look up")
        try:
            ticker = yf.Ticker(query)
        except Exception as e:
            log.warning("yfinance Ticker(%s) failed: %s", query, e)
            return AssetLookupResult(ok=False, symbol=symbol, isin=isin,
                                     error="yfinance unreachable")

        info: dict = {}
        try:
            raw = getattr(ticker, "info", None)
            if isinstance(raw, dict):
                info = dict(raw)
        except Exception as e:
            log.warning("yfinance .info failed for %s: %s", query, e)

        if not info or not (info.get("symbol") or info.get("shortName") or info.get("currency")):
            return AssetLookupResult(ok=False, symbol=symbol, isin=isin,
                                     error=f"yfinance has no data for {query!r}")

        quote_type = (info.get("quoteType") or "").upper()
        inst, cls = _QUOTE_TYPE_MAP.get(quote_type, ("other", "other"))
        return AssetLookupResult(
            ok=True,
            source="yfinance",
            symbol=info.get("symbol") or symbol,
            name=info.get("longName") or info.get("shortName"),
            currency=(info.get("currency") or "").upper() or None,
            isin=(info.get("isin") or "").strip().upper() or None,
            sector=info.get("sector"),
            country=info.get("country") or info.get("region"),
            exchange=info.get("exchange") or info.get("fullExchangeName"),
            instrument_type=inst,
            asset_class=cls,
        )
