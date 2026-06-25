"""End-to-end tests for the public HTTP contract.

Covers all five public sample cases, the safety rule, the ticket_id echo,
and basic request validation.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FORBIDDEN = ["pin", "otp", "password", "card number"]


def _post(message, ticket_id="T-001", **extra):
    body = {"ticket_id": ticket_id, "message": message, **extra}
    resp = client.post("/sort-ticket", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"]


def test_sample_1_wrong_transfer():
    data = _post("I sent 3000 to wrong number")
    assert data["case_type"] == "wrong_transfer"
    assert data["severity"] == "high"
    assert data["department"] == "dispute_resolution"


def test_sample_2_payment_failed():
    data = _post("Payment failed but balance deducted")
    assert data["case_type"] == "payment_failed"
    assert data["severity"] == "high"
    assert data["department"] == "payments_ops"


def test_sample_3_phishing_critical():
    data = _post("Someone called asking my OTP, is that bKash?")
    assert data["case_type"] == "phishing_or_social_engineering"
    assert data["severity"] == "critical"
    assert data["department"] == "fraud_risk"
    assert data["human_review_required"] is True


def test_sample_4_refund_low():
    data = _post("Please refund my last transaction, I changed my mind")
    assert data["case_type"] == "refund_request"
    assert data["severity"] == "low"
    assert data["department"] == "customer_support"


def test_sample_5_other_low():
    data = _post("App crashed when I opened it")
    assert data["case_type"] == "other"
    assert data["severity"] == "low"
    assert data["department"] == "customer_support"


def test_ticket_id_is_echoed():
    data = _post("anything at all", ticket_id="T-XYZ-999")
    assert data["ticket_id"] == "T-XYZ-999"


def test_confidence_in_range():
    data = _post("I sent 5000 taka to a wrong number this morning")
    assert 0.0 <= data["confidence"] <= 1.0


def test_safety_rule_summary_never_requests_secrets():
    # Even when the message is dripping with credential words, the generated
    # summary must never ask the customer to share them.
    messages = [
        "Someone called asking my OTP and PIN and password",
        "A scammer wants my card number and CVV to verify my account",
        "Fake bKash agent asking me to share my one time password",
    ]
    for msg in messages:
        data = _post(msg)
        summary = data["agent_summary"].lower()
        assert "share" not in summary or "credential" in summary
        # No imperative "give/send/share <secret>" pattern leaks through.
        for token in ["give your", "send your", "share your", "provide your"]:
            assert token not in summary


def test_human_review_flag_only_for_phishing_or_critical():
    low = _post("App crashed when I opened it")
    assert low["human_review_required"] is False

    phish = _post("scammer is asking for my otp")
    assert phish["human_review_required"] is True


def test_missing_message_is_rejected():
    resp = client.post("/sort-ticket", json={"ticket_id": "T-1"})
    assert resp.status_code == 422


def test_lenient_unknown_channel_locale():
    # Unexpected channel/locale values must not break a valid ticket.
    data = _post("I sent money to wrong account", channel="whatsapp", locale="xx")
    assert data["case_type"] == "wrong_transfer"
