from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import load_config
from .db.connection import get_database, reset_database_singleton
from .logging_setup import configure_logging
from .web.deps import build_container

app = typer.Typer(help="Portfolio Manager CLI")
log = logging.getLogger(__name__)
console = Console()


def _container():
    cfg = load_config()
    configure_logging(cfg.logging.level, cfg.logging.json_format)
    db = get_database(cfg.database.path)
    return build_container(cfg, db), cfg


@app.command("init-db")
def init_db(skip_seed: bool = typer.Option(False, "--skip-seed", help="Don't seed default benchmarks")):
    """Apply schema / migrations and seed defaults (S&P 500 benchmark)."""
    cfg = load_config()
    configure_logging(cfg.logging.level, cfg.logging.json_format)
    db = get_database(cfg.database.path)
    console.print("[green]ok[/] schema up to date")
    if skip_seed:
        return
    container = build_container(cfg, db)
    seeded = container.benchmarks.seed_defaults_if_empty(
        backfill=cfg.auto_snapshot.backfill_benchmarks_on_seed
    )
    if seeded is not None:
        console.print(f"[green]seeded[/] benchmark: [cyan]{seeded.name}[/] ({seeded.symbol})")
    else:
        console.print("[dim]benchmarks already present — no seed needed[/]")


@app.command("snapshot")
def snapshot(note: str | None = typer.Option(None, "--note", "-n")):
    """Take a snapshot now."""
    container, _ = _container()
    meta = container.snapshot.take(notes=note)
    console.print(
        f"[green]snapshot[/] [cyan]{meta.snapshot_id}[/] · "
        f"net worth {meta.net_worth_base:,.2f} {meta.base_currency}"
    )


@app.command("list-snapshots")
def list_snapshots(limit: int = 25):
    container, _ = _container()
    snaps = container.snapshots_repo.list_snapshots(limit=limit)
    table = Table("Taken at", "ID", "Net worth", "Base", "Note")
    for s in snaps:
        table.add_row(
            str(s.taken_at), s.snapshot_id[:8],
            f"{s.net_worth_base:,.2f}", s.base_currency, s.notes or "",
        )
    console.print(table)


@app.command("list-assets")
def list_assets():
    container, _ = _container()
    holdings = container.holdings.at()
    table = Table("Name", "Symbol", "Type", "Class", "Ccy", "Qty", "Active")
    for a in container.portfolio.list_assets(include_inactive=True):
        qty = holdings.asset_quantities.get(a.asset_id, 0.0)
        table.add_row(
            a.name, a.symbol or "", a.instrument_type.value, a.asset_class.value,
            a.currency, f"{qty:,.4f}", str(a.is_active),
        )
    console.print(table)


@app.command("backup")
def backup(out: str | None = typer.Option(None, "--out", "-o", help="Destination path. Default: data/backups/portfolio-YYYYMMDD-HHMMSS.duckdb")):
    """Copy the live DuckDB file to a timestamped backup."""
    cfg = load_config()
    configure_logging(cfg.logging.level, cfg.logging.json_format)
    src = Path(cfg.database.path)
    if not src.exists():
        console.print(f"[red]error[/] DB file not found at {src}")
        raise typer.Exit(1)
    if out is None:
        backup_dir = src.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"{src.stem}-{ts}.duckdb"
    else:
        dest = Path(out)
        dest.parent.mkdir(parents=True, exist_ok=True)
    # Close any open singleton to flush, then copy.
    reset_database_singleton()
    shutil.copy2(src, dest)
    size_kb = dest.stat().st_size / 1024
    console.print(f"[green]backed up[/] [cyan]{dest}[/] · {size_kb:,.1f} KB")


@app.command("restore")
def restore(
    src: str = typer.Argument(..., help="Backup file to restore from"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Replace the live DB with a backup. Confirms first unless --yes."""
    cfg = load_config()
    configure_logging(cfg.logging.level, cfg.logging.json_format)
    src_path = Path(src)
    dest = Path(cfg.database.path)
    if not src_path.exists():
        console.print(f"[red]error[/] backup file {src_path} not found")
        raise typer.Exit(1)
    if not yes:
        console.print(f"[yellow]about to overwrite[/] {dest} with {src_path}")
        confirm = typer.confirm("Continue?")
        if not confirm:
            console.print("[dim]cancelled[/]")
            raise typer.Exit(0)
    reset_database_singleton()
    if dest.exists():
        # one safety net — keep the prior live DB as .bak before overwriting
        bak = dest.with_suffix(dest.suffix + ".bak")
        shutil.copy2(dest, bak)
        console.print(f"[dim]existing DB saved as {bak}[/]")
    shutil.copy2(src_path, dest)
    console.print(f"[green]restored[/] from {src_path}")


@app.command("info")
def info():
    """Show DB location, size, and row counts per table."""
    cfg = load_config()
    configure_logging(cfg.logging.level, cfg.logging.json_format)
    db_path = Path(cfg.database.path)
    if db_path.exists():
        st = db_path.stat()
        size = f"{st.st_size / 1024:,.1f} KB"
        modified = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    else:
        size = "(does not exist)"
        modified = "—"
    console.print(f"[bold]Database:[/] {db_path.absolute()}")
    console.print(f"[dim]size:[/] {size}    [dim]modified:[/] {modified}")
    if not db_path.exists():
        return
    db = get_database(db_path)
    table = Table("Table", "Rows")
    for tbl in ("assets", "cash_holdings", "liabilities", "transactions",
                "snapshots", "snapshot_positions", "snapshot_position_values",
                "benchmarks", "manual_price_overrides", "fx_rates_cache", "price_cache"):
        try:
            n = db.fetchone(f"SELECT COUNT(*) FROM {tbl}")[0]
        except Exception:  # noqa: BLE001
            n = "?"
        table.add_row(tbl, str(n))
    console.print(table)


@app.command("web")
def web(
    host: str | None = typer.Option(None),
    port: int | None = typer.Option(None),
    reload: bool = typer.Option(False),
):
    """Start the FastAPI web server."""
    import uvicorn

    cfg = load_config()
    configure_logging(cfg.logging.level, cfg.logging.json_format)
    uvicorn.run(
        "portfolio_manager.web.app:get_app",
        factory=True,
        host=host or cfg.web.host,
        port=port or cfg.web.port,
        reload=reload or cfg.web.reload,
    )


if __name__ == "__main__":
    app()
