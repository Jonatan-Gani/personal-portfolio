from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request):
    c = request.app.state.container
    db_path = Path(c.config.database.path).absolute()
    info = {"path": str(db_path)}
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
    for tbl in ("assets", "cash_holdings", "liabilities", "transactions", "snapshots",
                "benchmarks", "manual_price_overrides", "price_cache", "fx_rates_cache"):
        try:
            counts[tbl] = c.db.fetchone(f"SELECT COUNT(*) FROM {tbl}")[0]
        except Exception:  # noqa: BLE001
            counts[tbl] = "?"

    return request.app.state.templates.TemplateResponse(
        request,
        "settings.html",
        {"request": request, "db": info, "counts": counts, "config": c.config},
    )


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
