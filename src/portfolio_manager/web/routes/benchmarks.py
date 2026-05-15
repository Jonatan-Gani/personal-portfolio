from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...domain.exceptions import NotFoundError
from ...domain.models import Benchmark

router = APIRouter()


@router.get("/benchmarks")
def list_benchmarks(request: Request):
    c = request.app.state.container
    items = c.benchmarks.list_all()
    return request.app.state.templates.TemplateResponse(
        request,
        "benchmarks.html",
        {"request": request, "items": items},
    )


@router.post("/benchmarks")
def create_benchmark(
    request: Request,
    name: str = Form(...),
    symbol: str = Form(...),
    currency: str = Form(...),
    country: str | None = Form(None),
    price_provider: str | None = Form(None),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    b = Benchmark(
        name=name,
        symbol=symbol,
        currency=currency,
        country=country or None,
        price_provider=price_provider or None,
        notes=notes,
    )
    c.benchmarks.add(b, backfill_days=365)
    return RedirectResponse("/benchmarks", status_code=303)


@router.post("/benchmarks/{benchmark_id}/update")
def update_benchmark(
    request: Request,
    benchmark_id: str,
    name: str = Form(...),
    symbol: str = Form(...),
    currency: str = Form(...),
    country: str | None = Form(None),
    price_provider: str | None = Form(None),
    notes: str | None = Form(None),
):
    c = request.app.state.container
    try:
        existing = c.benchmarks.get(benchmark_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    existing.name = name
    existing.symbol = symbol
    existing.currency = currency
    existing.country = country or None
    existing.price_provider = price_provider or None
    existing.notes = notes
    c.benchmarks.update(existing)
    return RedirectResponse("/benchmarks", status_code=303)


@router.post("/benchmarks/{benchmark_id}/delete")
def delete_benchmark(request: Request, benchmark_id: str, hard: bool = False):
    c = request.app.state.container
    if hard:
        c.benchmarks.delete(benchmark_id)
    else:
        c.benchmarks.deactivate(benchmark_id)
    return RedirectResponse("/benchmarks", status_code=303)


@router.post("/benchmarks/{benchmark_id}/backfill")
def backfill_benchmark(request: Request, benchmark_id: str, days: int = 365):
    c = request.app.state.container
    try:
        b = c.benchmarks.get(benchmark_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    c.benchmarks.backfill_history(b, days=days)
    return RedirectResponse("/benchmarks", status_code=303)
