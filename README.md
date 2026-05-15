# Portfolio Manager

Personal portfolio manager. Tracks assets, liabilities, and cash across countries, currencies, and instrument
types. Snapshots the whole portfolio on every run so you can compare value in any reporting currency at
historical FX. Prices and FX come from swappable providers behind a single interface; everything else lives
locally in DuckDB.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp config/config.example.yaml config/config.yaml
cp .env.example .env

portfolio init-db
portfolio web      # http://localhost:8000
```

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

### Snapshots

Every snapshot stores, per position, the value in every reporting currency at the FX rates observed at that
point in time. A "USD return on EUR assets" between two snapshots is therefore just SQL on
`snapshot_position_values`.
