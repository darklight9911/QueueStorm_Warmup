"""Tests for error-handling and security hardening."""

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app

client = TestClient(app)


def test_security_headers_present_on_health():
    r = client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "no-referrer"


def test_security_headers_present_on_sort_ticket():
    r = client.post("/sort-ticket", json={"ticket_id": "T", "message": "hi"})
    assert r.headers.get("x-content-type-options") == "nosniff"


def test_oversized_body_rejected_413():
    big = "x" * 70_000  # body well over the 64 KB limit
    r = client.post("/sort-ticket", json={"ticket_id": "T", "message": big})
    assert r.status_code == 413
    assert r.json()["error"] == "payload_too_large"


def test_message_over_field_limit_422():
    msg = "x" * 11_000  # under 64 KB body, over the 10k character field cap
    r = client.post("/sort-ticket", json={"ticket_id": "T", "message": msg})
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_validation_error_envelope_is_clean():
    r = client.post("/sort-ticket", json={"ticket_id": "T"})  # missing message
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation_error"
    assert "detail" in body


def test_malformed_json_does_not_500():
    r = client.post(
        "/sort-ticket",
        content="{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code in (400, 422)


def test_route_is_resilient_to_classifier_failure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(main_module, "classify", boom)
    r = client.post(
        "/sort-ticket", json={"ticket_id": "T-RESIL", "message": "anything"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ticket_id"] == "T-RESIL"          # still echoes the id
    assert body["case_type"] == "other"            # safe fallback
    assert body["human_review_required"] is False
