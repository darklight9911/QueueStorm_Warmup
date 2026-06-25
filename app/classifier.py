"""Deterministic, rules-based ticket classifier.

Pure Python — no model, no network, no GPU, no secrets. Given a free-text
customer message (English, Bengali script, or romanized "banglish" / mixed),
it answers the four task questions:

  1. case_type  - what kind of problem is this?
  2. severity   - how serious is it?
  3. department - which team should own it?
  4. agent_summary - a neutral one-sentence brief for a human agent.

It also raises `human_review_required` for phishing or critical cases.

Design notes
------------
* Each candidate case type accumulates a weighted score from keyword hits.
  The highest score wins; ties break by a fixed safety-first priority order
  (phishing > wrong_transfer > payment_failed > refund > other).
* Phishing is only ever *triggered* when a credential word co-occurs with a
  request/contact context, OR when an explicit scam/fraud word is present.
  This stops lone words like "I forgot my password" from being flagged.
* The generated `agent_summary` is built from safe templates and then passed
  through `enforce_summary_safety`, so it can never instruct a customer to
  share a PIN/OTP/password/card number (Safety Rule, section 5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Enum-like constants
# --------------------------------------------------------------------------- #
CASE_WRONG_TRANSFER = "wrong_transfer"
CASE_PAYMENT_FAILED = "payment_failed"
CASE_REFUND = "refund_request"
CASE_PHISHING = "phishing_or_social_engineering"
CASE_OTHER = "other"

SEV_LOW = "low"
SEV_MEDIUM = "medium"
SEV_HIGH = "high"
SEV_CRITICAL = "critical"

DEPT_SUPPORT = "customer_support"
DEPT_DISPUTE = "dispute_resolution"
DEPT_PAYMENTS = "payments_ops"
DEPT_FRAUD = "fraud_risk"

DEPARTMENT_BY_CASE = {
    CASE_WRONG_TRANSFER: DEPT_DISPUTE,
    CASE_PAYMENT_FAILED: DEPT_PAYMENTS,
    CASE_REFUND: DEPT_SUPPORT,
    CASE_PHISHING: DEPT_FRAUD,
    CASE_OTHER: DEPT_SUPPORT,
}

# Safety-first tie-break order (lower index == higher priority).
PRIORITY = [
    CASE_PHISHING,
    CASE_WRONG_TRANSFER,
    CASE_PAYMENT_FAILED,
    CASE_REFUND,
    CASE_OTHER,
]

# --------------------------------------------------------------------------- #
# Keyword banks  (lowercase substrings; English + banglish + Bengali script)
# --------------------------------------------------------------------------- #

# Phishing is split into "credential" words and "context" words. A credential
# word alone is not enough; it must pair with context, or a scam word must fire.
_PHISH_CREDENTIAL = [
    "otp", "one time password", "one-time password", "verification code",
    "verification pin", "security code", "pin code", " pin", "pin number",
    "password", "passcode", "cvv", "card number", "full card", "card details",
    "ওটিপি", "পিন", "পাসওয়ার্ড", "ভেরিফিকেশন",
]
_PHISH_CONTEXT = [
    "asking", "asked", "ask for", "wants my", "want my", "share", "give me",
    "tell me", "provide", "send me your", "called", "calling", "phone call",
    "received a call", "received an sms", "text message", "click", "link",
    "verify your account", "verify now", "confirm your", "representative",
    "agent called", "customer care", "helpline called", "stranger",
    "unknown number", "chaitese", "chaiche", "chacche", "chacchen",
    "ফোন", "কল", "মেসেজ", "এসএমএস", "চাইছে", "চাচ্ছে",
]
# Strong, self-sufficient phishing/scam markers.
_PHISH_SCAM = [
    "phishing", "scam", "scammer", "fraud", "fraudster", "suspicious",
    "fake call", "fake sms", "fake bkash", "impersonat", "lottery",
    "you have won", "you won", "prize", "reward", "claim your",
    "account blocked", "account will be blocked", "account suspended",
    "protarok", "protarona", "protarona", "jaliyati", "vuya", "bhua",
    "প্রতারণা", "প্রতারক", "জালিয়াতি", "সন্দেহজনক", "লটারি", "পুরস্কার",
    "ভুয়া", "ভুয়", "ব্লক",
]

_WRONG_TRANSFER = [
    "wrong number", "wrong recipient", "wrong account", "wrong person",
    "wrong nun", "wrong no", "wrong mobile", "to the wrong", "to wrong",
    "sent to wrong", "sent it to wrong", "mistakenly sent", "sent by mistake",
    "by mistake", "accidentally sent", "accidentally transferred",
    "transferred to wrong", "sent money to the wrong", "wrong destination",
    "wrong recipent", "incorrect number", "incorrect account",
    "bhul number", "vul number", "bhul number e", "vul number e",
    "bhul jaygay", "bhul manush", "vul manush",
    "ভুল নম্বর", "ভুল নাম্বার", "ভুল একাউন্ট", "ভুল অ্যাকাউন্ট",
    "ভুল জায়গায়", "ভুল মানুষ",
]

_PAYMENT_FAILED = [
    "payment failed", "transaction failed", "failed payment", "failed transaction",
    "payment fail", "txn failed", "payment did not", "payment didn't",
    "payment didnt", "did not go through", "didn't go through", "didnt go through",
    "balance deducted", "balance was deducted", "money deducted", "amount deducted",
    "deducted but", "deducted from", "cut from my", "cash out failed",
    "cashout failed", "send money failed", "transaction declined", "declined",
    "payment stuck", "transaction stuck", "stuck transaction", "payment pending",
    "transaction pending", "not completed", "incomplete transaction",
    "taka kete", "taka kete niyeche", "kete niyeche", "kete nieche",
    "deduct hoye", "payment hoy nai", "payment hoyni", "transaction fail",
    "টাকা কেটে", "কেটে নিয়েছে", "কেটে নেওয়া", "লেনদেন ব্যর্থ", "পেমেন্ট ব্যর্থ",
    "ব্যর্থ", "কাটা হয়েছে",
]

_REFUND = [
    "refund", "money back", "reimburse", "reimbursement", "chargeback",
    "charge back", "return my payment", "return the payment", "return my money",
    "cancel my order", "cancel the order", "changed my mind", "change my mind",
    "i don't want", "no longer want", "want to cancel", "cancel and refund",
    "refund chai", "taka ferot", "ferot chai", "ferot dei", "fie chai",
    "রিফান্ড", "ফেরত", "ফেরত চাই", "টাকা ফেরত", "বাতিল",
]

# Words that turn a routine refund into a contested dispute.
_REFUND_CONTESTED = [
    "denied", "rejected", "refused", "not received", "haven't received",
    "havent received", "still waiting", "no refund yet", "didn't get my refund",
    "didnt get my refund", "second time", "again", "escalate", "complaint",
    "weeks ago", "days ago", "promised", "where is my refund",
]

_AMOUNT_RE = re.compile(r"(\d[\d,]{1,})")
_CURRENCY_HINT = re.compile(r"(tk|taka|bdt|৳|tk\.)", re.IGNORECASE)

# Catches imperative requests for secrets; used to sanitise the summary.
_FORBIDDEN_SUMMARY_RE = re.compile(
    r"(share|send|give|provide|tell|enter|confirm|reveal|disclose)\b[^.]*?\b"
    r"(pin|otp|one[\s-]*time[\s-]*password|password|passcode|cvv|"
    r"card\s*number|full\s*card)",
    re.IGNORECASE,
)


@dataclass
class Classification:
    case_type: str
    severity: str
    department: str
    agent_summary: str
    human_review_required: bool
    confidence: float


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _count_hits(text: str, terms: list[str]) -> int:
    return sum(1 for t in terms if t in text)


def _extract_amount(text: str) -> str | None:
    """Return a human-friendly amount like '5,000 BDT' if one is present."""
    match = _AMOUNT_RE.search(text)
    if not match:
        return None
    digits = match.group(1).replace(",", "")
    if not digits.isdigit():
        return None
    value = int(digits)
    # Ignore tiny stray numbers unless a currency hint is nearby.
    if value < 10 and not _CURRENCY_HINT.search(text):
        return None
    return f"{value:,} BDT"


def enforce_summary_safety(summary: str) -> str:
    """Guarantee the summary never instructs the customer to share secrets.

    Defence-in-depth: our templates already avoid this, but if a template ever
    changes we still neutralise any matching imperative before returning.
    """
    if _FORBIDDEN_SUMMARY_RE.search(summary):
        return _FORBIDDEN_SUMMARY_RE.sub("[sensitive credentials redacted]", summary)
    return summary


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _score(text: str) -> dict[str, float]:
    scores = {c: 0.0 for c in PRIORITY}

    # --- phishing -----------------------------------------------------------
    cred = _count_hits(text, _PHISH_CREDENTIAL)
    ctx = _count_hits(text, _PHISH_CONTEXT)
    scam = _count_hits(text, _PHISH_SCAM)
    if scam or (cred and ctx):
        scores[CASE_PHISHING] = scam * 3.0 + cred * 2.0 + ctx * 1.0

    # --- wrong transfer -----------------------------------------------------
    wt = _count_hits(text, _WRONG_TRANSFER)
    scores[CASE_WRONG_TRANSFER] = wt * 3.0

    # --- payment failed -----------------------------------------------------
    pf = _count_hits(text, _PAYMENT_FAILED)
    scores[CASE_PAYMENT_FAILED] = pf * 3.0

    # --- refund -------------------------------------------------------------
    rf = _count_hits(text, _REFUND)
    # "money back" / "ferot" also appears in wrong-transfer phrasing; only treat
    # as a refund signal when no wrong-transfer marker dominates.
    scores[CASE_REFUND] = rf * 2.5

    return scores


def _severity_for(case_type: str, text: str) -> str:
    if case_type == CASE_PHISHING:
        return SEV_CRITICAL
    if case_type == CASE_WRONG_TRANSFER:
        return SEV_HIGH
    if case_type == CASE_PAYMENT_FAILED:
        return SEV_HIGH
    if case_type == CASE_REFUND:
        if _count_hits(text, _REFUND_CONTESTED):
            return SEV_MEDIUM
        return SEV_LOW
    return SEV_LOW


def _department_for(case_type: str, severity: str, text: str) -> str:
    # A contested refund is owned by dispute resolution, not first-line support.
    if case_type == CASE_REFUND and severity == SEV_MEDIUM:
        return DEPT_DISPUTE
    return DEPARTMENT_BY_CASE[case_type]


def _summary_for(case_type: str, severity: str, text: str) -> str:
    amount = _extract_amount(text)
    amount_clause = f" of {amount}" if amount else ""

    if case_type == CASE_PHISHING:
        # Deliberately omits the words PIN/OTP/password/card so the output can
        # never be read as asking the customer for them.
        return (
            "Customer reports a suspicious party attempting to obtain their "
            "account security credentials; flagged as a possible phishing or "
            "social-engineering attempt."
        )
    if case_type == CASE_WRONG_TRANSFER:
        return (
            f"Customer reports sending a transfer{amount_clause} to the wrong "
            "recipient and requests that the money be recovered."
        )
    if case_type == CASE_PAYMENT_FAILED:
        deducted = any(
            k in text
            for k in ("deduct", "kete", "কেটে", "cut from", "balance")
        )
        tail = (
            " though the balance appears to have been deducted"
            if deducted
            else ""
        )
        return (
            f"Customer reports a failed payment or transaction{tail}; needs "
            "review by payments operations."
        )
    if case_type == CASE_REFUND:
        if severity == SEV_MEDIUM:
            return (
                "Customer is following up on an unresolved or contested refund "
                "request and is awaiting resolution."
            )
        return (
            "Customer is requesting a refund for a recent transaction."
        )
    return (
        "Customer reports a general issue that does not match a specific "
        "financial dispute category; routed to customer support for review."
    )


def _confidence_for(case_type: str, scores: dict[str, float]) -> float:
    if case_type == CASE_OTHER:
        return 0.4
    top = scores[case_type]
    # Margin over the runner-up makes us more confident in the pick.
    rest = [v for k, v in scores.items() if k != case_type]
    margin = top - (max(rest) if rest else 0.0)
    conf = 0.55 + 0.08 * top + 0.05 * margin
    return round(max(0.5, min(0.95, conf)), 2)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def classify(message: str, locale: str | None = None) -> Classification:
    """Classify a single customer message into the response contract."""
    text = (message or "").lower().strip()

    scores = _score(text)
    best = max(scores.values()) if scores else 0.0

    if best <= 0.0:
        case_type = CASE_OTHER
    else:
        # Highest score wins; ties resolved by safety-first PRIORITY order.
        case_type = min(
            (c for c in PRIORITY if scores[c] == best),
            key=PRIORITY.index,
        )

    severity = _severity_for(case_type, text)
    department = _department_for(case_type, severity, text)
    summary = enforce_summary_safety(_summary_for(case_type, severity, text))
    human_review = severity == SEV_CRITICAL or case_type == CASE_PHISHING
    confidence = _confidence_for(case_type, scores)

    return Classification(
        case_type=case_type,
        severity=severity,
        department=department,
        agent_summary=summary,
        human_review_required=human_review,
        confidence=confidence,
    )
