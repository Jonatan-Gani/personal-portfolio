from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/income")
def income_page(request: Request, currency: str | None = None):
    c = request.app.state.container
    ccy = (currency or c.config.reporting.base_currency).upper()
    report = c.income.report(ccy)
    return request.app.state.templates.TemplateResponse(
        request,
        "income.html",
        {
            "request": request,
            "report": report,
            "ccy": ccy,
        },
    )
