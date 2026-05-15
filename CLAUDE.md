# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & common commands

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

CLI (the `portfolio` script is registered via `pyproject.toml` → `portfolio_manager.cli:app`):

```bash
portfolio init-db                # apply schema/migrations
portfolio snapshot [--note "X"]  # take a snapshot
portfolio list-snapshots [--limit N]
portfolio list-assets
portfolio web [--host H --port P --reload]
```

Tests / lint / typecheck:

```bash
pytest                                        # full suite (testpaths=tests, addopts=-ra -q)
pytest tests/test_snapshot_flow.py            # one file
pytest tests/test_snapshot_flow.py::test_name # single test
ruff check src tests
ruff format src tests
mypy src
```

The dev-server (`portfolio web --reload`) imports `portfolio_manager.web.app:get_app` as a factory.

## Architecture

This is a **layered, provider-pluggable** portfolio tracker. The whole system is wired through one
`Container` (`src/portfolio_manager/web/deps.py`) used by both the CLI and FastAPI app — there is no
global service locator beyond it and the DB singleton.

Layers (strict direction `web/cli → services → repositories + providers → db/domain`):

- `domain/` — pydantic models (`Asset`, `Liability`, `CashHolding`, `Transaction`, `SnapshotMeta`,
  `SnapshotPosition`, `FXRate`, `Price`), enums, exceptions. **No I/O.**
- `db/` — `Database` is a thin DuckDB wrapper holding a single connection guarded by an `RLock`
  (DuckDB connections are not thread-safe). `get_database(path)` is a process-wide singleton —
  remember to call `reset_database_singleton()` if you need to swap DBs in-process. Migrations
  live in `db/schema.py` as `MIGRATIONS: list[(version, sql)]` and are applied by
  `db/migrations.py`. **Add new schema by appending a new tuple, never editing past entries.**
- `repositories/` — one class per entity, CRUD only, no business logic. `prices.py` hosts the
  `FXRateCache` and `PriceCache` tables.
- `providers/` — `PriceProvider` / `FXProvider` ABCs in `base.py`; concrete implementations
  (`ecb_fx`, `yfinance_price`, `mock`) self-register via decorators in `registry.py`. `_ensure_loaded()`
  imports the modules for their side effects, so a new provider must (a) implement the ABC and
  (b) be added to the import list in `registry._ensure_loaded()` — nothing else changes.
- `services/` — business logic. Important pieces:
  - `FXService` caches into `fx_rates_cache` keyed on `(rate_date, base, quote, provider)`; falls
    back to any-age cache if the provider raises `FXRateUnavailable`. Stays reproducible because
    snapshots persist the rates they used.
  - `SnapshotService.take()` is the core flow: pull assets/liabilities/cash → resolve a price per
    asset (provider quote can be in a different currency than the asset; it's converted via FX) →
    project each position's `value_local` into **every** reporting currency at the rates fetched
    once at the top of the snapshot → persist `snapshots`, `snapshot_positions`, and
    `snapshot_position_values`.
  - `ReturnsService` / `ExposureService` operate purely on the snapshot tables in SQL — that's
    the whole point of storing per-currency values per position.
- `web/` — `app.py` builds the app, mounts static files, attaches `Container` to `app.state`,
  and registers global exception handlers that branch on `/api/` prefix (JSON) vs HTML. Routes
  live in `web/routes/*.py`; templates in `web/templates/`.
- `cli.py` — Typer commands; `_container()` rebuilds config + DB + container per invocation.

### Key invariants

- **Snapshots are append-only and reproducible.** Every position value in every reporting currency
  is materialized into `snapshot_position_values` along with the `fx_rate_from_base` used. Cross-
  currency returns are SQL joins on this table — do not recompute FX at read time.
- **Base currency is always present in `reporting_currencies`.** `SnapshotService.__init__`
  prepends it if missing; respect the same when building configs.
- **Naive UTC timestamps everywhere.** Use `_clock.utcnow()` (drops tzinfo) — DuckDB `TIMESTAMP`
  comparisons rely on naive datetimes. Don't introduce tz-aware datetimes into rows.
- **Currency strings are uppercase.** The `CurrencyStr` annotation in `domain/models.py` normalizes
  on input; service code also `.upper()`s defensively. Keep that pattern.
- **DB access is serialized.** All DuckDB calls go through `Database.execute/fetchone/...` which
  hold an `RLock`. Don't reach into `Database.conn()` from background threads without it.

### Configuration

YAML at `config/config.yaml` (loaded by `config.py`); env vars prefixed `PORTFOLIO_` via `.env`
override only `config_path`, `log_level`, `db_path`. Provider names are strings resolved against
the registries — change `providers.fx.name` / `providers.price.name` to swap implementations.

## Tests

`tests/conftest.py` provides a `db` fixture (per-test tmpdir DuckDB with migrations applied) and a
`services` fixture that wires the full service graph using `MockFxProvider` / `MockPriceProvider`
from `providers/mock.py`. Prefer those mocks over patching network calls when adding tests for
service-level behavior.
