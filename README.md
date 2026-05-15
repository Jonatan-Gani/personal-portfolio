# Portfolio Manager

Personal portfolio manager. Tracks assets, liabilities, and cash across countries, currencies, and instrument
types. Snapshots the whole portfolio on every run so you can compare value in any reporting currency at
historical FX. Prices and FX come from swappable providers behind a single interface; everything else lives
locally in DuckDB.

## Quick start

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp config/config.example.yaml config/config.yaml
cp .env.example .env

portfolio init-db
portfolio web      # http://localhost:8000
```

### Windows (cmd.exe)

If your environment blocks `.bat` files (so `activate.bat` is off-limits),
skip activation entirely and call the venv's tools by their full path
inside `.venv\Scripts\`:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"

copy config\config.example.yaml config\config.yaml
copy .env.example .env

.venv\Scripts\portfolio.exe init-db
.venv\Scripts\portfolio.exe web
```

If `.bat` activation *is* allowed, the short form works too — run
`.venv\Scripts\activate.bat` once, then just `portfolio init-db` /
`portfolio web` for the rest of the session.

#### Windows with AppLocker / "blocked by group policy"

Some corporate machines block executing any `.exe` from user-writable
folders (Downloads, Desktop, the venv's `Scripts\` directory). In that
case skip the venv entirely and use the system `py.exe` launcher
(`C:\Windows\py.exe` is trusted). Install into your user site-packages
and run the CLI as a module:

```bat
py -3 -m pip install --user -e ".[dev]"

copy config\config.example.yaml config\config.yaml
copy .env.example .env

py -3 -m portfolio_manager.cli init-db
py -3 -m portfolio_manager.cli web
```

If `py -3` is also unavailable, ask IT to add `py.exe` or your project
folder to the AppLocker allow-list — there is no further user-mode
workaround.

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

Copy-Item config\config.example.yaml config\config.yaml
Copy-Item .env.example .env

portfolio init-db
portfolio web
```

If PowerShell blocks the activation script, allow it for the current user once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

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
