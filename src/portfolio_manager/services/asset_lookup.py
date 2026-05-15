"""Asset metadata lookup: given a ticker (and optionally an ISIN), try to verify
the security and return enriched metadata that can pre-fill the add-asset form.

Currently backed by yfinance (the same library used for prices). The provider
is intentionally optional — if it's not installed or the network is unavailable,
we return `AssetLookupResult(ok=False, error=...)` rather than raising.

Result schema:
    ok:              bool — True if at least the ticker resolved
    source:          str  — 'yfinance' | None
    symbol, name, currency, isin, sector, country, exchange,
    instrument_type, asset_class    (best-effort mappings to our enums)
    error:           str  — when ok=False

ISIN validation is purely structural: 12 chars, ISO 6166 checksum. We do NOT
trust the source's ISIN if the caller passed a conflicting one; we surface
both and let the user pick.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from typing import Optional

log = logging.getLogger(__name__)


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


@dataclass
class AssetLookupResult:
    ok: bool
    source: Optional[str] = None
    symbol: Optional[str] = None
    name: Optional[str] = None
    currency: Optional[str] = None
    isin: Optional[str] = None
    sector: Optional[str] = None
    country: Optional[str] = None
    exchange: Optional[str] = None
    instrument_type: Optional[str] = None
    asset_class: Optional[str] = None
    error: Optional[str] = None

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
    # Convert letters A-Z to numbers 10-35, then concatenate digits.
    digits: list[int] = []
    for ch in isin:
        if ch.isdigit():
            digits.append(int(ch))
        else:
            n = ord(ch) - ord("A") + 10
            digits.append(n // 10)
            digits.append(n % 10)
    # Luhn-style: double every second digit from the right (skipping the check
    # digit itself), sum the digits of each result, and the total mod 10 must be 0.
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class AssetLookupService:
    """Looks up enriched metadata for a ticker. Optionally cross-checks against
    a user-provided ISIN — if both source-ISIN and input-ISIN are present and
    they disagree, the result still resolves but the response carries both so
    the UI can warn."""

    def __init__(self):
        # Tiny in-process cache so repeated lookups during one form session are cheap.
        self._cache: dict[tuple[str, str], AssetLookupResult] = {}

    def lookup(self, symbol: str | None = None, isin: str | None = None) -> AssetLookupResult:
        symbol = (symbol or "").strip().upper() or None
        isin = (isin or "").strip().upper() or None
        if not symbol and not isin:
            return AssetLookupResult(ok=False, error="provide a ticker or an ISIN")

        # Validate ISIN format up front (cheap, no network).
        if isin and not is_valid_isin_format(isin):
            return AssetLookupResult(ok=False, isin=isin, error="ISIN format/checksum invalid")

        key = (symbol or "", isin or "")
        if key in self._cache:
            return self._cache[key]

        # Try yfinance. If it isn't installed or the call fails, return ok=False
        # but include any structural info the caller already gave us.
        result = self._lookup_yfinance(symbol, isin)
        self._cache[key] = result
        return result

    def _lookup_yfinance(self, symbol: str | None, isin: str | None) -> AssetLookupResult:
        try:
            import yfinance as yf
        except ImportError:
            return AssetLookupResult(
                ok=False, symbol=symbol, isin=isin,
                error="yfinance not installed — fill in fields manually",
            )

        # yfinance can resolve a ticker directly. If only an ISIN was given, we
        # try Ticker(isin) — sometimes works for European listings but not always.
        query = symbol or isin
        if not query:
            return AssetLookupResult(ok=False, error="nothing to look up")
        try:
            ticker = yf.Ticker(query)
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance Ticker(%s) failed: %s", query, e)
            return AssetLookupResult(
                ok=False, symbol=symbol, isin=isin,
                error="lookup provider unreachable — fill in fields manually",
            )

        info: dict = {}
        try:
            raw = getattr(ticker, "info", None)
            if isinstance(raw, dict):
                info = dict(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance .info failed for %s: %s", query, e)

        if not info:
            try:
                fi = getattr(ticker, "fast_info", None)
                if fi is not None:
                    # fast_info supports .get on most yfinance versions; fall back to attr access.
                    def _fi(key):
                        try:
                            return fi.get(key) if hasattr(fi, "get") else getattr(fi, key, None)
                        except Exception:  # noqa: BLE001
                            return None
                    info = {
                        "currency": _fi("currency"),
                        "exchange": _fi("exchange"),
                        "quoteType": _fi("quoteType"),
                    }
            except Exception:  # noqa: BLE001
                info = {}

        if not info or not (info.get("symbol") or info.get("shortName") or info.get("currency")):
            return AssetLookupResult(
                ok=False, symbol=symbol, isin=isin,
                error=f"no data found for {query!r}",
            )

        try:

            quote_type = (info.get("quoteType") or "").upper()
            inst, cls = _QUOTE_TYPE_MAP.get(quote_type, ("other", "other"))

            src_isin = (info.get("isin") or "").strip().upper() or None
            if src_isin and isin and src_isin != isin:
                # Surface the mismatch; ok still True so the form pre-fills.
                err = f"ISIN mismatch: source has {src_isin}, you provided {isin}"
            else:
                err = None

            return AssetLookupResult(
                ok=True,
                source="yfinance",
                symbol=info.get("symbol") or symbol,
                name=info.get("longName") or info.get("shortName"),
                currency=(info.get("currency") or "").upper() or None,
                isin=src_isin or isin,
                sector=info.get("sector"),
                country=info.get("country") or info.get("region"),
                exchange=info.get("exchange") or info.get("fullExchangeName"),
                instrument_type=inst,
                asset_class=cls,
                error=err,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance lookup failed for %s: %s", query, e)
            return AssetLookupResult(
                ok=False, symbol=symbol, isin=isin,
                error=f"lookup failed: {e}",
            )
