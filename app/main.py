"""FastAPI application exposing GET /health and POST /sort-ticket.

Run locally:
    uvicorn app.main:app --reload

The container entrypoint binds to 0.0.0.0 and honours the $PORT env var so the
same image works on Render, Railway, Fly, EC2, etc. with no code changes.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__
from .classifier import classify
from .models import HealthResponse, TicketRequest, TicketResponse

SERVICE_NAME = "queuestorm-ticket-sorter"

app = FastAPI(
    title="QueueStorm Ticket Sorter",
    description=(
        "Reads one CRM ticket and returns a structured classification: "
        "case type, severity, owning department, a neutral agent summary, "
        "a human-review flag, and a confidence score. Rules-based, "
        "deterministic, no GPU, no secrets."
    ),
    version=__version__,
)


@app.get("/", include_in_schema=False)
def root() -> dict:
    """Friendly landing payload pointing at the real endpoints."""
    return {
        "service": SERVICE_NAME,
        "version": __version__,
        "endpoints": {
            "health": "GET /health",
            "sort_ticket": "POST /sort-ticket",
            "docs": "GET /docs",
        },
    }


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness/readiness probe. Returns immediately."""
    return HealthResponse(status="ok", service=SERVICE_NAME, version=__version__)


@app.post("/sort-ticket", response_model=TicketResponse)
def sort_ticket(ticket: TicketRequest) -> TicketResponse:
    """Classify one CRM ticket and return the structured response."""
    result = classify(ticket.message, locale=ticket.locale)
    return TicketResponse(
        ticket_id=ticket.ticket_id,
        case_type=result.case_type,
        severity=result.severity,
        department=result.department,
        agent_summary=result.agent_summary,
        human_review_required=result.human_review_required,
        confidence=result.confidence,
    )


# Local convenience runner: `python -m app.main`
if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
