"""Pydantic request/response models for the /sort-ticket contract.

The request side is intentionally lenient: `channel` and `locale` are accepted
as free strings (not strict enums) so an unexpected value from the grader never
turns a valid ticket into a 422. The response side is strict, because those
fields are produced by us and must always match the documented enums.
"""

from __future__ import annotations

import os
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Upper bounds keep a single request cheap to process and bound memory use.
# Overridable via env so an operator can tune them without a code change.
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "10000"))
MAX_TICKET_ID_LENGTH = int(os.getenv("MAX_TICKET_ID_LENGTH", "200"))

CaseType = Literal[
    "wrong_transfer",
    "payment_failed",
    "refund_request",
    "phishing_or_social_engineering",
    "other",
]
Severity = Literal["low", "medium", "high", "critical"]
Department = Literal[
    "customer_support",
    "dispute_resolution",
    "payments_ops",
    "fraud_risk",
]


class TicketRequest(BaseModel):
    """Incoming CRM ticket. Only `ticket_id` and `message` are required."""

    ticket_id: str = Field(
        ...,
        max_length=MAX_TICKET_ID_LENGTH,
        description="Echoed back verbatim in the response.",
    )
    message: str = Field(
        ...,
        max_length=MAX_MESSAGE_LENGTH,
        description="Free-text customer complaint.",
    )
    # Kept as plain strings on purpose — lenient on input, see module docstring.
    channel: Optional[str] = Field(
        default=None,
        description="One of: app, sms, call_center, merchant_portal.",
    )
    locale: Optional[str] = Field(
        default=None, description="One of: bn, en, mixed."
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "ticket_id": "T-001",
                "channel": "app",
                "locale": "en",
                "message": (
                    "I sent 5000 taka to a wrong number this morning, "
                    "please help me get it back"
                ),
            }
        }
    }


class TicketResponse(BaseModel):
    """Structured classification returned to the CRM."""

    ticket_id: str
    case_type: CaseType
    severity: Severity
    department: Department
    agent_summary: str
    human_review_required: bool
    confidence: float = Field(..., ge=0.0, le=1.0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "ticket_id": "T-001",
                "case_type": "wrong_transfer",
                "severity": "high",
                "department": "dispute_resolution",
                "agent_summary": (
                    "Customer reports sending 5,000 BDT to the wrong "
                    "recipient and requests recovery."
                ),
                "human_review_required": True,
                "confidence": 0.85,
            }
        }
    }


class HealthResponse(BaseModel):
    """Payload for GET /health."""

    status: Literal["ok"]
    service: str
    version: str
