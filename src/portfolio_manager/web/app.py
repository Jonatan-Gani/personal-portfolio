from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import AppConfig, load_config
from ..db.connection import Database, get_database
from ..logging_setup import configure_logging
from .deps import Container, build_container

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or load_config()
    configure_logging(cfg.logging.level, cfg.logging.json_format)

    db: Database = get_database(cfg.database.path)
    container: Container = build_container(cfg, db)

    app = FastAPI(title="Portfolio Manager", version="0.1.0")
    app.state.container = container

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.globals["base_currency"] = cfg.reporting.base_currency
    templates.env.globals["reporting_currencies"] = cfg.reporting.reporting_currencies
    app.state.templates = templates

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from .routes import (
        api, assets, benchmarks, cash, exposures, exports, holdings, imports as imports_route,
        income, liabilities, manual_prices, pages, returns_route, settings, snapshots,
        snapshot_diff, targets, transactions,
    )
    app.include_router(pages.router)
    app.include_router(holdings.router)
    app.include_router(assets.router)
    app.include_router(liabilities.router)
    app.include_router(cash.router)
    app.include_router(transactions.router)
    app.include_router(manual_prices.router)
    # snapshot_diff registers /snapshots/{from_id}/diff/{to_id}; mount BEFORE
    # snapshots.router so its /snapshots/{snapshot_id} catch-all doesn't shadow it.
    app.include_router(snapshot_diff.router)
    app.include_router(snapshots.router)
    app.include_router(benchmarks.router)
    app.include_router(exposures.router)
    app.include_router(returns_route.router)
    app.include_router(targets.router)
    app.include_router(income.router)
    app.include_router(imports_route.router)
    app.include_router(settings.router)
    app.include_router(exports.router)
    app.include_router(api.router)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": str(exc)}, status_code=500)
        return HTMLResponse(
            f"<h1>Server error</h1><pre>{type(exc).__name__}: {exc}</pre>",
            status_code=500,
        )

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return HTMLResponse(
            f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>",
            status_code=exc.status_code,
        )

    return app


app = None


def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app
