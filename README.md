# Portfolio Manager

Personal portfolio manager. Tracks assets, liabilities, and cash across countries, currencies, and instrument
types. Snapshots the whole portfolio on every run so you can compare value in any reporting currency at
historical FX. Prices and FX come from swappable providers behind a single interface; everything else lives
locally in DuckDB.

## Quick start

One command — works on Linux, macOS and Windows:

```bash
python start.py
```

`start.py` is idempotent: it creates the `.venv`, installs dependencies,
copies `config/config.yaml` and `.env` from their examples, applies database
migrations, then launches the web app at <http://localhost:8000>. Re-running
it skips whatever is already done, so it doubles as the everyday "run" command.

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

On a locked-down Windows box where `.venv\Scripts\*.exe` is blocked, use
`python start.py --no-venv`, or run the CLI through the trusted launcher:
`py -3 -m pip install --user -e ".[dev]"` then `py -3 -m portfolio_manager.cli web`.

CLI ops:

```bash
portfolio init-db                # apply schema / migrations
portfolio snapshot               # take a snapshot now
portfolio snapshot --note "EOM"  # with note
portfolio list-snapshots
portfolio web --port 8000        # run FastAPI app
```

## Architecture

```
src/portfolio_manager/
├── config.py            # YAML + env, pydantic-settings
├── logging_setup.py     # structured logging
├── domain/              # enums, pydantic models, exceptions
├── db/                  # DuckDB connection, schema, migrations
├── repositories/        # CRUD per entity (no business logic)
├── providers/           # PriceProvider / FXProvider abstractions + impls
├── services/            # business logic (snapshot, exposure, returns, fx)
├── web/                 # FastAPI app, Jinja2 templates
└── cli.py               # Typer CLI
```

### Swapping a provider

`config/config.yaml`:

```yaml
providers:
  fx:
    name: ecb            # registered in providers/registry.py
    base_currency: USD
  price:
    name: yfinance
```

To add a provider, implement `providers/base.py` ABC and register it in `providers/registry.py`. No other code
changes.

#### Interactive Brokers prices

`ibkr` is a `PriceProvider` backed by a running TWS or IB Gateway (via `ib_insync`). It needs the extra
dependency and a gateway with the socket API enabled:

```bash
pip install -e ".[ibkr]"
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

Asset symbols are bare tickers (`AAPL`) or `TICKER:EXCHANGE:CURRENCY` (`VOD:LSE:GBP`) for non-US instruments.
`market_data_type: 3` (delayed) works without a paid market-data subscription.

### Snapshots

Every snapshot stores, per position, the value in every reporting currency at the FX rates observed at that
point in time. A "USD return on EUR assets" between two snapshots is therefore just SQL on
`snapshot_position_values`.
