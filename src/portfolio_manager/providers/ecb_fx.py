from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Any

from ..domain.exceptions import FXRateUnavailable
from .base import FXProvider
from .registry import register_fx

log = logging.getLogger(__name__)

ECB_BASE = "https://data-api.ecb.europa.eu/service/data/EXR"
# Series key: D.{quote}.EUR.SP00.A — daily, average, spot. Returns "X quote per 1 EUR".


class ECBFxProvider(FXProvider):
    """ECB Statistical Data Warehouse. Native base is EUR; we cross-rate to any caller-chosen base."""

    name = "ecb"

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds
        self._client = None  # lazily created — keeps httpx out of the import path

    def _client_or_create(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(
                timeout=self.timeout_seconds, headers={"Accept": "text/csv"}
            )
        return self._client

    def get_rate(self, base: str, quote: str, as_of: date | None = None) -> float:
        base = base.upper()
        quote = quote.upper()
        if base == quote:
            return 1.0
        eur_to_base = self._eur_to(base, as_of)
        eur_to_quote = self._eur_to(quote, as_of)
        if eur_to_base == 0:
            raise FXRateUnavailable(f"ECB returned zero EUR->{base}")
        return eur_to_quote / eur_to_base

    def _eur_to(self, currency: str, as_of: date | None) -> float:
        if currency == "EUR":
            return 1.0
        series = f"D.{currency}.EUR.SP00.A"
        url = f"{ECB_BASE}/{series}"
        params: dict[str, Any] = {"format": "csvdata"}
        if as_of is None:
            params["lastNObservations"] = 1
        else:
            params["startPeriod"] = (as_of.replace(day=max(as_of.day - 10, 1))).isoformat()
            params["endPeriod"] = as_of.isoformat()
        log.debug("ECB GET %s params=%s", url, params)
        import httpx  # local import — see __init__
        try:
            r = self._client_or_create().get(url, params=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise FXRateUnavailable(f"ECB request failed for {currency}: {e}") from e
        rate = _parse_latest_obs(r.text)
        if rate is None:
            raise FXRateUnavailable(f"ECB returned no observations for EUR->{currency}")
        return rate


def _parse_latest_obs(csv_text: str) -> float | None:
    reader = csv.DictReader(io.StringIO(csv_text))
    latest_date: str | None = None
    latest_value: float | None = None
    for row in reader:
        try:
            d = row.get("TIME_PERIOD") or row.get("TIME PERIOD")
            v = row.get("OBS_VALUE") or row.get("OBS VALUE")
            if not d or v in (None, ""):
                continue
            value = float(v)
        except (TypeError, ValueError):
            continue
        if latest_date is None or d > latest_date:
            latest_date = d
            latest_value = value
    return latest_value


@register_fx("ecb")
def _factory(opts: dict[str, Any]) -> FXProvider:
    return ECBFxProvider(timeout_seconds=int(opts.get("timeout_seconds", 15)))
