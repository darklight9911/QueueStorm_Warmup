"""Unit tests for the classifier's edge cases and Bengali / banglish support."""

from app.classifier import classify, enforce_summary_safety


def test_bengali_wrong_transfer():
    res = classify("আমি ভুল নম্বরে টাকা পাঠিয়েছি")
    assert res.case_type == "wrong_transfer"


def test_banglish_payment_failed():
    res = classify("payment fail but taka kete niyeche")
    assert res.case_type == "payment_failed"


def test_lone_password_word_is_not_phishing():
    # "forgot my password" has a credential word but no request/scam context.
    res = classify("I forgot my password for the app login")
    assert res.case_type != "phishing_or_social_engineering"


def test_scam_word_alone_triggers_phishing():
    res = classify("I think this is a scam message")
    assert res.case_type == "phishing_or_social_engineering"
    assert res.severity == "critical"


def test_contested_refund_goes_to_dispute():
    res = classify("I asked for a refund a week ago but it was rejected")
    assert res.case_type == "refund_request"
    assert res.severity == "medium"
    assert res.department == "dispute_resolution"


def test_empty_message_is_other():
    res = classify("")
    assert res.case_type == "other"
    assert res.severity == "low"


def test_summary_safety_redacts_imperative():
    bad = "Please ask the customer to share their OTP and PIN."
    cleaned = enforce_summary_safety(bad)
    assert "otp" not in cleaned.lower()
    assert "redacted" in cleaned.lower()


def test_amount_appears_in_wrong_transfer_summary():
    res = classify("I sent 5000 taka to a wrong number")
    assert "5,000 BDT" in res.agent_summary
