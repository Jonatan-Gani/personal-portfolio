# Portfolio Manager

A personal, self-hosted portfolio tracker. It records every financial event you enter — buys, sells,
deposits, dividends, loan repayments — as an append-only **transaction log**, then derives everything
else (current holdings, cost basis, net worth, returns, exposures) from that log. Values are snapshotted
over time so you can see your portfolio in any reporting currency at the FX rates that were true on each
date. Prices and FX come from swappable providers; all your data lives locally in a single DuckDB file.

- **Local-first** — one DuckDB file, no cloud, no account.
- **Multi-currency** — hold assets in any currency, report net worth in several at once.
- **Reproducible history** — snapshots freeze the prices and FX rates they used.
- **Pluggable data sources** — yfinance, ECB, or Interactive Brokers, selected by config.

---

## Quick start

One command — works on Linux, macOS and Windows:

```bash
python start.py
```

`start.py` is idempotent: it creates the `.venv`, installs dependencies, copies `config/config.yaml`
and `.env` from their examples, applies database migrations, then launches the web app at
<http://localhost:8000>. Re-running it skips whatever is already done, so it doubles as the everyday
"run" command.

Flags:

```bash
python start.py --ibkr       # also install the Interactive Brokers extra
python start.py --reinstall  # force a dependency reinstall (after a git pull)
python start.py --no-venv    # install into the current Python, skip .venv —
                             # use if your environment blocks .venv executables
```

On Windows use `py start.py` if `python` is not on `PATH`.

### Manual setup

If you would rather run the steps yourself:

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp config/config.example.yaml config/config.yaml   # Windows: copy
cp .env.example .env                                # Windows: copy

portfolio init-db
portfolio web      # http://localhost:8000
```

On a locked-down Windows box where `.venv\Scripts\*.exe` is blocked, use `python start.py --no-venv`,
or run the CLI through the trusted launcher: `py -3 -m pip install --user -e ".[dev]"` then
`py -3 -m portfolio_manager.cli web`.

---

## Core concepts

The whole system rests on one idea: **transactions are the source of truth; everything else is
derived.**

```
            transactions  (append-only log — what you actually did)
                  │
                  ▼
   HoldingsService  →  current quantities / cash balances / loan principals
                  │
                  ▼
   SnapshotService.take()  →  prices each holding, converts to every
                              reporting currency, freezes the result
                  │
                  ▼
   snapshots / snapshot_positions / snapshot_position_values
   (immutable history — what Returns, Exposures, Compare read from)
```

- An **account** is *where* you hold things (a broker, a bank). It is just a container.
- A **position** (asset / cash account / liability) is *what* you hold inside an account. It carries
  only metadata — ticker, currency, classification. It never stores a quantity or balance.
- A **transaction** is a single event against one position. Quantities and balances are *computed*
  by summing the transaction log, never stored on the position row.

Because of this, recording a transaction is all you ever need to do — if the position does not exist
yet, it is created from what you typed. Correcting a transaction from years ago automatically flows
through every later calculation, while past snapshots stay frozen as the historical record.

### Snapshots

`SnapshotService.take()` reads current holdings, resolves a price for each one (a provider quote may
be in a different currency than the asset — it is converted via FX), and projects every position's
value into **every** reporting currency at the rates fetched once at the top of the snapshot. It then
persists `snapshots`, `snapshot_positions`, and `snapshot_position_values`.

Snapshots are append-only and self-contained: each stored position value carries the
`fx_rate_from_base` used. A "USD return on EUR assets" between two dates is therefore a plain SQL join
on `snapshot_position_values` — FX is never recomputed at read time.

---

## Using the web app

`portfolio web` serves the UI at <http://localhost:8000>.

| Page | What it shows |
|------|---------------|
| **Dashboard** (`/`) | Net worth, latest snapshot, headline figures. |
| **Transactions** (`/transactions`) | The event log. Record buys, sells, deposits, dividends, fees, splits, opening balances. The source of truth. |
| **Holdings** (`/holdings`) | Current asset and cash positions derived from the log. |
| **Debts** (`/liabilities`) | Outstanding liabilities and accrued interest. |
| **Accounts** (`/accounts`) | Accounts and account groups (households, strategies). |
| **Performance** (`/returns`) | Returns over time, against benchmarks. |
| **Compare** (`/compare`) | Diff two snapshots. |
| **Income** (`/income`) | Dividend and interest history. |
| **Targets** (`/targets`) | Target allocations and drift from them. |
| **Exposures** (`/exposures`) | Breakdowns by currency, country, asset class, sector. |
| **Settings** (`/settings`) | Base/reporting currencies, providers, auto-snapshot, UI, data export. |

Recording a transaction never requires setting up a position first: choose the type, type the symbol
or name, pick the account, enter the amounts. The matching position is found — or created — on submit.

---

## CLI reference

The `portfolio` command is installed with the package (`pyproject.toml` → `portfolio_manager.cli:app`).

| Command | Description |
|---------|-------------|
| `portfolio init-db` | Apply schema / migrations, seed the default benchmark. `--skip-seed` to skip seeding. |
| `portfolio snapshot` | Take a snapshot now. `--note "..."` to annotate it. |
| `portfolio list-snapshots` | List recent snapshots. `--limit N`. |
| `portfolio list-assets` | List registered assets. |
| `portfolio backup` | Copy the live DuckDB file to a timestamped backup. `--out PATH`. |
| `portfolio restore` | Restore the database from a backup file. |
| `portfolio info` | Show DB location, size, and row counts per table. |
| `portfolio web` | Run the FastAPI app. `--host`, `--port`, `--reload`. |

---

## Configuration

YAML lives at `config/config.yaml` (copy it from `config/config.example.yaml`). Environment variables
prefixed `PORTFOLIO_` — set via `.env` — override only `config` (path), `log_level`, and `db_path`.

```yaml
database:
  path: data/portfolio.duckdb     # the single local data file

logging:
  level: INFO
  json_format: false

reporting:
  base_currency: USD              # always included in reporting_currencies
  reporting_currencies: [USD, SEK, ILS, EUR, GBP]

providers:
  fx:
    name: ecb                     # ecb | mock
    options: { timeout_seconds: 15, cache_ttl_hours: 12 }
  price:
    name: yfinance                # yfinance | ibkr | mock
    options: { timeout_seconds: 15, cache_ttl_hours: 12 }

web:
  host: 127.0.0.1
  port: 8000
  reload: false

auto_snapshot:
  enabled: true
  stale_after_minutes: 360        # take a fresh snapshot if the latest is older than this
  backfill_benchmarks_on_seed: true
```

Some of these (currencies, provider names, auto-snapshot, UI preferences) can also be edited from the
Settings page, which stores them in the database and falls back to `config.yaml`.

---

## Data providers

Providers are resolved by name against a registry (`providers/registry.py`). Switching one is a config
change — nothing else.

| Kind | Name | Notes |
|------|------|-------|
| FX | `ecb` | European Central Bank reference rates. |
| FX | `mock` | Deterministic offline rates (tests). |
| Price | `yfinance` | Yahoo Finance quotes and history. |
| Price | `ibkr` | Interactive Brokers via a running TWS / IB Gateway. |
| Price | `mock` | Deterministic offline prices (tests). |

To add a provider, implement the `PriceProvider` / `FXProvider` ABC in `providers/base.py`, register it
with the `@register_price` / `@register_fx` decorator, and add it to the module map in
`providers/registry.py`. No other code changes.

### Interactive Brokers prices

`ibkr` is a `PriceProvider` backed by a running TWS or IB Gateway (via `ib_insync`). It needs the extra
dependency and a gateway with the socket API enabled (TWS → Global Configuration → API → Settings →
"Enable ActiveX and Socket Clients"):

```bash
pip install -e ".[ibkr]"      # or: python start.py --ibkr
```

```yaml
providers:
  price:
    name: ibkr
    options:
      host: 127.0.0.1
      port: 7497          # 7497 TWS paper · 7496 TWS live · 4002/4001 Gateway
      client_id: 17
      exchange: SMART     # default routing exchange
      currency: USD       # default instrument currency
      market_data_type: 3 # 1 live · 2 frozen · 3 delayed · 4 delayed-frozen
```

Asset symbols are bare tickers (`AAPL`) or `TICKER:EXCHANGE:CURRENCY` (`VOD:LSE:GBP`) for non-US
instruments. `market_data_type: 3` (delayed) works without a paid market-data subscription.

---

## Architecture

Strict layering — dependencies only point downward: `web/cli → services → repositories + providers →
db/domain`. The whole graph is wired through one `Container` (`web/deps.py`), used by both the CLI and
the FastAPI app.

```
src/portfolio_manager/
├── config.py            # YAML + env config (pydantic-settings)
├── logging_setup.py     # structured logging
├── cli.py               # Typer CLI
├── domain/              # pydantic models, enums, exceptions — no I/O
├── db/                  # DuckDB connection, schema, migrations
├── repositories/        # one class per entity, CRUD only, no business logic
├── providers/           # PriceProvider / FXProvider ABCs + implementations
├── services/            # business logic (snapshot, returns, exposure, fx, …)
└── web/                 # FastAPI app, routes, Jinja2 templates
```

Key invariants:

- **Snapshots are append-only and reproducible.** Every position value in every reporting currency is
  materialized, with the FX rate used. Cross-currency returns are SQL joins, never recomputed.
- **The base currency is always present in `reporting_currencies`** — prepended if missing.
- **Naive UTC timestamps everywhere** — DuckDB `TIMESTAMP` comparisons rely on them.
- **Currency codes are uppercase**, normalized on input.
- **DB access is serialized** — a single DuckDB connection guarded by a lock.

Schema changes are append-only migrations in `db/schema.py` (`MIGRATIONS: list[(version, sql)]`),
applied by `db/migrations.py`. Never edit a past migration; add a new tuple.

---

## Development

```bash
pytest                                          # full test suite
pytest tests/test_snapshot_flow.py              # one file
pytest tests/test_snapshot_flow.py::test_name   # single test

ruff check src tests                            # lint
ruff format src tests                           # format
mypy src                                        # type-check
```

`tests/conftest.py` provides a `db` fixture (a per-test temporary DuckDB with migrations applied) and a
`services` fixture wiring the full service graph with the mock providers. Prefer the mocks over patching
network calls when testing service-level behavior.

---

## Backups

Your portfolio is one DuckDB file. `portfolio backup` copies it to a timestamped file under
`data/backups/`; `portfolio restore` puts one back. The `data/` directory is git-ignored — it holds
balances and is never committed.
