from __future__ import annotations

import importlib
from typing import Any, Callable

from ..domain.exceptions import ConfigError
from .base import FXProvider, PriceProvider

_FX_FACTORIES: dict[str, Callable[[dict[str, Any]], FXProvider]] = {}
_PRICE_FACTORIES: dict[str, Callable[[dict[str, Any]], PriceProvider]] = {}

# Map provider name → module that registers it. The module is imported the
# first time the provider is requested, so unrelated providers (and their
# heavy transitive deps — pandas, httpx, etc.) stay off the startup path.
_FX_MODULES: dict[str, str] = {
    "ecb": "portfolio_manager.providers.ecb_fx",
    "mock": "portfolio_manager.providers.mock",
}
_PRICE_MODULES: dict[str, str] = {
    "yfinance": "portfolio_manager.providers.yfinance_price",
    "mock": "portfolio_manager.providers.mock",
}


def register_fx(name: str):
    def deco(factory: Callable[[dict[str, Any]], FXProvider]):
        _FX_FACTORIES[name] = factory
        return factory
    return deco


def register_price(name: str):
    def deco(factory: Callable[[dict[str, Any]], PriceProvider]):
        _PRICE_FACTORIES[name] = factory
        return factory
    return deco


def build_fx_provider(name: str, options: dict[str, Any] | None = None) -> FXProvider:
    if name not in _FX_FACTORIES:
        module = _FX_MODULES.get(name)
        if module:
            importlib.import_module(module)
    if name not in _FX_FACTORIES:
        raise ConfigError(
            f"unknown FX provider {name!r}; registered: {sorted(_FX_FACTORIES)}, "
            f"known: {sorted(_FX_MODULES)}"
        )
    return _FX_FACTORIES[name](options or {})


def build_price_provider(name: str, options: dict[str, Any] | None = None) -> PriceProvider:
    if name not in _PRICE_FACTORIES:
        module = _PRICE_MODULES.get(name)
        if module:
            importlib.import_module(module)
    if name not in _PRICE_FACTORIES:
        raise ConfigError(
            f"unknown price provider {name!r}; registered: {sorted(_PRICE_FACTORIES)}, "
            f"known: {sorted(_PRICE_MODULES)}"
        )
    return _PRICE_FACTORIES[name](options or {})
