from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter()


# Keys that live in app_settings (DB) and can be edited from the UI. Values fall
# back to config.yaml on first read. Anything not listed here stays in config.yaml
# (deploy-time concerns: db.path, web.host/port, logging.*).
SETTING_KEYS = {
    "reporting.base_currency",
    "reporting.reporting_currencies",
    "providers.fx.name",
    "providers.price.name",
    "auto_snapshot.enabled",
    "auto_snapshot.stale_after_minutes",
    "ui.theme",           # 'light' | 'dark' | 'system'
    "ui.density",         # 'comfortable' | 'compact'
    "ui.privacy_mode",    # bool: blur figures
    "ui.default_currency",
    "ui.locale",
}

ACCOUNT_TYPES = [
    "taxable", "ira", "roth", "k401", "hsa",
    "checking", "savings", "brokerage", "mortgage", "loan", "credit_card", "other",
]
GROUP_KINDS = ["household", "person", "institution", "strategy", "other"]


def _effective_settings(c) -> dict:
    """Settings as the app actually uses them: app_settings overrides config.yaml."""
    s = c.app_settings_repo.all()
    cfg = c.config
    return {
        "base_currency": s.get("reporting.base_currency", cfg.reporting.base_currency),
        "reporting_currencies": s.get("reporting.reporting_currencies", cfg.reporting.reporting_currencies),
        "fx_provider": s.get("providers.fx.name", cfg.providers.fx.name),
        "price_provider": s.get("providers.price.name", cfg.providers.price.name),
        "auto_snapshot_enabled": s.get("auto_snapshot.enabled", cfg.auto_snapshot.enabled),
        "auto_snapshot_stale_minutes": s.get("auto_snapshot.stale_after_minutes", cfg.auto_snapshot.stale_after_minutes),
        "theme": s.get("ui.theme", "light"),
        "density": s.get("ui.density", "comfortable"),
        "privacy_mode": s.get("ui.privacy_mode", False),
        "default_currency": s.get("ui.default_currency", cfg.reporting.base_currency),
        "locale": s.get("ui.locale", "en-US"),
    }


@router.get("/settings")
def settings_page(request: Request):
    c = request.app.state.container
    db_path = Path(c.config.database.path).absolute()
    info: dict = {"path": str(db_path)}
    if db_path.exists():
        st = db_path.stat()
        info["size_kb"] = round(st.st_size / 1024, 1)
        info["modified"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        info["exists"] = True
    else:
        info["size_kb"] = 0
        info["modified"] = None
        info["exists"] = False

    counts = {}
    for tbl in (
        "assets", "cash_holdings", "liabilities", "transactions", "snapshots",
        "benchmarks", "manual_price_overrides", "price_cache", "fx_rates_cache",
        "account_groups", "accounts",
    ):
        try:
            counts[tbl] = c.db.fetchone(f"SELECT COUNT(*) FROM {tbl}")[0]
        except Exception:  # noqa: BLE001
            counts[tbl] = "?"

    return request.app.state.templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "db": info,
            "counts": counts,
            "config": c.config,
            "settings": _effective_settings(c),
            "common_currencies": ["USD", "EUR", "GBP", "SEK", "ILS", "CHF", "JPY", "CAD", "AUD", "CNY"],
            "themes": ["light", "dark", "system"],
            "densities": ["comfortable", "compact"],
            "fx_providers": ["ecb", "mock"],
            "price_providers": ["yfinance", "mock"],
        },
    )


def _parse_bool(v: str | None) -> bool:
    return str(v).lower() in {"1", "true", "yes", "on"}


@router.post("/settings/reporting")
def update_reporting(
    request: Request,
    base_currency: str = Form(...),
    reporting_currencies: str = Form(""),
    default_currency: str = Form(""),
):
    c = request.app.state.container
    base = base_currency.strip().upper()
    if not base:
        raise HTTPException(400, "base_currency is required")
    ccys = [s.strip().upper() for s in reporting_currencies.split(",") if s.strip()]
    if base not in ccys:
        ccys.insert(0, base)
    c.app_settings_repo.set("reporting.base_currency", base)
    c.app_settings_repo.set("reporting.reporting_currencies", ccys)
    default = (default_currency or base).strip().upper()
    if default not in ccys:
        ccys.append(default)
        c.app_settings_repo.set("reporting.reporting_currencies", ccys)
    c.app_settings_repo.set("ui.default_currency", default)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/providers")
def update_providers(
    request: Request,
    fx_provider: str = Form(...),
    price_provider: str = Form(...),
):
    c = request.app.state.container
    c.app_settings_repo.set("providers.fx.name", fx_provider.strip())
    c.app_settings_repo.set("providers.price.name", price_provider.strip())
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/auto-snapshot")
def update_auto_snapshot(
    request: Request,
    enabled: str | None = Form(None),
    stale_after_minutes: int = Form(360),
):
    c = request.app.state.container
    c.app_settings_repo.set("auto_snapshot.enabled", _parse_bool(enabled))
    c.app_settings_repo.set("auto_snapshot.stale_after_minutes", max(1, int(stale_after_minutes)))
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/ui")
def update_ui(
    request: Request,
    theme: str = Form("light"),
    density: str = Form("comfortable"),
    privacy_mode: str | None = Form(None),
    locale: str = Form("en-US"),
):
    c = request.app.state.container
    c.app_settings_repo.set("ui.theme", theme)
    c.app_settings_repo.set("ui.density", density)
    c.app_settings_repo.set("ui.privacy_mode", _parse_bool(privacy_mode))
    c.app_settings_repo.set("ui.locale", locale)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/toggle-privacy")
def toggle_privacy(request: Request, next: str = Form("/")):
    c = request.app.state.container
    current = bool(c.app_settings_repo.get("ui.privacy_mode", False))
    c.app_settings_repo.set("ui.privacy_mode", not current)
    return RedirectResponse(next or "/", status_code=303)


@router.get("/settings/download-backup")
def download_backup(request: Request):
    c = request.app.state.container
    db_path = Path(c.config.database.path)
    if not db_path.exists():
        raise HTTPException(404, f"DB file not found at {db_path}")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    download_name = f"{db_path.stem}-{ts}.duckdb"
    return FileResponse(
        path=str(db_path),
        media_type="application/octet-stream",
        filename=download_name,
    )
