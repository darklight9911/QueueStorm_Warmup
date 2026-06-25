"""FastAPI application exposing GET /health and POST /sort-ticket.

Run locally:
    uvicorn app.main:app --reload

The container entrypoint binds to 0.0.0.0 and honours the $PORT env var so the
same image works on Render, Railway, Fly, EC2, etc. with no code changes.

Hardening & scale
-----------------
* Resilient route: a classifier failure degrades to a safe `other` response
  instead of a 500, so one odd ticket can never take the endpoint down.
* Global handlers: unhandled errors and validation failures return clean JSON,
  never a stack trace.
* Request limits: oversized bodies are rejected early (413), and over-length
  fields are rejected at validation (422).
* Rate limiting: per-client fixed-window limiter (in-memory by default, Redis
  when REDIS_URL is set) returns 429 + Retry-After; fails open; /health exempt.
* Async routes + multi-worker serving for throughput; security headers and
  privacy-aware logging (the raw message is never logged).
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .classifier import (
    CASE_OTHER,
    DEPT_SUPPORT,
    SEV_LOW,
    Classification,
    classify,
)
from .models import HealthResponse, TicketRequest, TicketResponse
from .ratelimit import RateLimitResult, build_rate_limiter, get_client_ip

SERVICE_NAME = "queuestorm-ticket-sorter"

# --- Configuration (env-driven, no secrets) ---------------------------------
_ENABLE_DOCS = os.getenv("ENABLE_DOCS", "true").strip().lower() != "false"
_MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(64 * 1024)))  # 64 KB
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Rate limiting
_RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() != "false"
_RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "120"))
_RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
_TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "true").strip().lower() != "false"
_REDIS_URL = os.getenv("REDIS_URL") or None
# Probes and the landing page must never be throttled.
_RATE_LIMIT_EXEMPT = {"/health", "/"}

logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("queuestorm")

rate_limiter = build_rate_limiter(_RATE_LIMIT_REQUESTS, _RATE_LIMIT_WINDOW, _REDIS_URL)

app = FastAPI(
    title="QueueStorm Ticket Sorter",
    description=(
        "Reads one CRM ticket and returns a structured classification: "
        "case type, severity, owning department, a neutral agent summary, "
        "a human-review flag, and a confidence score. Rules-based, "
        "deterministic, no GPU, no secrets."
    ),
    version=__version__,
    docs_url="/docs" if _ENABLE_DOCS else None,
    redoc_url="/redoc" if _ENABLE_DOCS else None,
    openapi_url="/openapi.json" if _ENABLE_DOCS else None,
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _apply_security_headers(response) -> None:
    for key, value in _SECURITY_HEADERS.items():
        if key not in response.headers:
            response.headers[key] = value


def _apply_rate_headers(response, result: RateLimitResult) -> None:
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    response.headers["X-RateLimit-Reset"] = str(result.reset_after)


# --------------------------------------------------------------------------- #
# Middleware: body-size guard + rate limiting + security headers
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def guard_and_harden(request: Request, call_next):
    # 1) Reject oversized payloads before doing any work (cheap DoS protection).
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > _MAX_BODY_BYTES:
        response = JSONResponse(
            status_code=413,
            content={
                "error": "payload_too_large",
                "message": f"Request body exceeds the {_MAX_BODY_BYTES}-byte limit.",
            },
        )
        _apply_security_headers(response)
        return response

    # 2) Rate limit per client (skip health/landing probes).
    rate_result: RateLimitResult | None = None
    if _RATE_LIMIT_ENABLED and request.url.path not in _RATE_LIMIT_EXEMPT:
        client_ip = get_client_ip(request, _TRUST_PROXY_HEADERS)
        rate_result = await rate_limiter.hit(client_ip)
        if not rate_result.allowed:
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "message": "Too many requests. Please slow down and retry shortly.",
                },
            )
            _apply_rate_headers(response, rate_result)
            response.headers["Retry-After"] = str(rate_result.reset_after)
            _apply_security_headers(response)
            return response

    # 3) Process the request, then stamp headers on the way out.
    response = await call_next(request)
    _apply_security_headers(response)
    if rate_result is not None:
        _apply_rate_headers(response, rate_result)
    return response


# --------------------------------------------------------------------------- #
# Exception handlers: always return clean JSON, never a stack trace
# --------------------------------------------------------------------------- #
@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "detail": jsonable_encoder(exc.errors()),
        },
    )


@app.exception_handler(Exception)
async def on_unhandled_error(request: Request, exc: Exception):
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "An unexpected error occurred."},
    )


def _safe_fallback() -> Classification:
    """Conservative result used when the classifier itself raises."""
    return Classification(
        case_type=CASE_OTHER,
        severity=SEV_LOW,
        department=DEPT_SUPPORT,
        agent_summary=(
            "Customer message could not be automatically classified; routed to "
            "customer support for manual review."
        ),
        human_review_required=False,
        confidence=0.0,
    )


# --------------------------------------------------------------------------- #
# Routes (async to avoid threadpool overhead under high concurrency)
# --------------------------------------------------------------------------- #
@app.get("/", include_in_schema=False)
async def root() -> dict:
    """Friendly landing payload pointing at the real endpoints."""
    return {
        "service": SERVICE_NAME,
        "version": __version__,
        "endpoints": {
            "health": "GET /health",
            "sort_ticket": "POST /sort-ticket",
            "docs": "GET /docs" if _ENABLE_DOCS else "disabled",
        },
    }


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness/readiness probe. Returns immediately and is never rate limited."""
    return HealthResponse(status="ok", service=SERVICE_NAME, version=__version__)


@app.post("/sort-ticket", response_model=TicketResponse)
async def sort_ticket(ticket: TicketRequest) -> TicketResponse:
    """Classify one CRM ticket and return the structured response."""
    try:
        result = classify(ticket.message, locale=ticket.locale)
    except Exception:  # pragma: no cover - safety net; one ticket can't 500 us
        logger.exception("classification failed for ticket_id=%s", ticket.ticket_id)
        result = _safe_fallback()

    # Privacy: record the outcome only — never the raw message, which may
    # contain personal data or the very credentials we are protecting.
    logger.info(
        "sorted ticket_id=%s case=%s severity=%s review=%s",
        ticket.ticket_id,
        result.case_type,
        result.severity,
        result.human_review_required,
    )

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
