from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseModel):
    path: str = "data/portfolio.duckdb"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_format: bool = False


class ReportingConfig(BaseModel):
    base_currency: str = "USD"
    reporting_currencies: list[str] = Field(default_factory=lambda: ["USD", "SEK", "ILS", "EUR", "GBP"])


class ProviderSpec(BaseModel):
    name: str
    options: dict[str, Any] = Field(default_factory=dict)


class ProvidersConfig(BaseModel):
    fx: ProviderSpec = ProviderSpec(name="ecb")
    price: ProviderSpec = ProviderSpec(name="yfinance")


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False


class AutoSnapshotConfig(BaseModel):
    enabled: bool = True
    stale_after_minutes: int = 360  # 6 hours
    backfill_benchmarks_on_seed: bool = True


class HistoryConfig(BaseModel):
    # Which PriceHistoryStore backend to use. "duckdb" keeps end-of-day prices
    # in the local database; a custom backend can be registered and named here.
    backend: str = "duckdb"


class AppConfig(BaseModel):
    database: DatabaseConfig = DatabaseConfig()
    logging: LoggingConfig = LoggingConfig()
    reporting: ReportingConfig = ReportingConfig()
    providers: ProvidersConfig = ProvidersConfig()
    web: WebConfig = WebConfig()
    auto_snapshot: AutoSnapshotConfig = AutoSnapshotConfig()
    history: HistoryConfig = HistoryConfig()


class _EnvSettings(BaseSettings):
    """Env-var overrides; YAML is the primary source."""

    model_config = SettingsConfigDict(env_prefix="PORTFOLIO_", env_file=".env", extra="ignore")

    config: str = "config/config.yaml"
    log_level: str | None = None
    db_path: str | None = None


def load_config(config_path: str | Path | None = None) -> AppConfig:
    env = _EnvSettings()
    path = Path(config_path) if config_path else Path(env.config)
    if path.exists():
        with path.open() as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}
    cfg = AppConfig.model_validate(raw)
    if env.log_level:
        cfg.logging.level = env.log_level
    if env.db_path:
        cfg.database.path = env.db_path
    return cfg
