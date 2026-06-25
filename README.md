# QueueStorm Ticket Sorter

> bKash · SUST CSE Carnival 2026 — Codex Community Hackathon · **Mock Preliminary**

A small, fast web service that reads **one** customer support message and returns a
structured classification an agent can act on in two seconds:

1. **What kind of problem is this?** — `case_type`
2. **How serious is it?** — `severity`
3. **Which team should handle it?** — `department`
4. **A one-sentence summary** — `agent_summary`

It also raises **`human_review_required`** for phishing or critical cases so a human
reviews them immediately.

The classifier is **rules-based, deterministic, and dependency-light** — no LLM, no
GPU, no secrets, no external API calls. That makes it fast (sub-millisecond
classification), free to run, and 100% reproducible for the grader. It understands
**English, Bengali script, and romanized "banglish" / mixed** messages.

---

## Quick start (Docker — recommended)

The repo is designed to **clone → run** with zero manual configuration.

```bash
git clone <your-repo-url>
cd SUST_MOCK
docker compose up --build      # builds the image and starts the API on :8000
```

Then, in another terminal:

```bash
curl http://localhost:8000/health
```

> Opening the folder in **VS Code** with the *Dev Containers* extension? Just
> "Reopen in Container" — the `.devcontainer/` config builds and starts everything for you.

### Run without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Using the Makefile (shortcuts)

A `Makefile` wraps the common tasks. Run `make` (or `make help`) to list them.

| Command         | What it does                                                     |
|-----------------|------------------------------------------------------------------|
| `make install`  | Create `.venv` and install all dependencies                      |
| `make run`      | Run the API locally on `:8000`                                   |
| `make dev`      | Run locally with auto-reload                                     |
| `make test`     | Run the full test suite                                          |
| `make up`       | Build + start via Docker Compose (detached) and check health     |
| `make smoke`    | Hit `/health` and a sample `/sort-ticket` on a running server    |
| `make down`     | Stop and remove the Docker Compose stack                         |
| `make logs`     | Follow container logs                                            |
| `make clean`    | Remove Python/test caches                                        |

Override tunables on the CLI, e.g. `make run PORT=9000`.

---

## API

### `GET /health`
Liveness probe. Returns within milliseconds (well under the 10s limit).

```json
{ "status": "ok", "service": "queuestorm-ticket-sorter", "version": "1.0.0" }
```

### `POST /sort-ticket`
Classify one CRM ticket. Responds well under the 30s limit.

**Request**

| Field       | Type   | Required | Notes                                   |
|-------------|--------|----------|-----------------------------------------|
| `ticket_id` | string | Yes      | Echoed back verbatim in the response    |
| `message`   | string | Yes      | Free-text customer complaint            |
| `channel`   | string | No       | `app`, `sms`, `call_center`, `merchant_portal` |
| `locale`    | string | No       | `bn`, `en`, `mixed`                     |

> `channel` and `locale` are accepted leniently — an unexpected value never turns a
> valid ticket into an error.

**Response**

| Field                   | Type    | Notes                                         |
|-------------------------|---------|-----------------------------------------------|
| `ticket_id`             | string  | Matches the request                           |
| `case_type`             | enum    | `wrong_transfer`, `payment_failed`, `refund_request`, `phishing_or_social_engineering`, `other` |
| `severity`              | enum    | `low`, `medium`, `high`, `critical`           |
| `department`            | enum    | `customer_support`, `dispute_resolution`, `payments_ops`, `fraud_risk` |
| `agent_summary`         | string  | One neutral sentence                          |
| `human_review_required` | boolean | `true` for critical severity or phishing      |
| `confidence`            | number  | Float in `[0, 1]`                             |

**Example**

```bash
curl -X POST http://localhost:8000/sort-ticket \
  -H 'Content-Type: application/json' \
  -d '{
        "ticket_id": "T-001",
        "channel": "app",
        "locale": "en",
        "message": "I sent 5000 taka to a wrong number this morning, please help me get it back"
      }'
```

```json
{
  "ticket_id": "T-001",
  "case_type": "wrong_transfer",
  "severity": "high",
  "department": "dispute_resolution",
  "agent_summary": "Customer reports sending a transfer of 5,000 BDT to the wrong recipient and requests that the money be recovered.",
  "human_review_required": false,
  "confidence": 0.94
}
```

Interactive API docs (Swagger UI) are served at **`/docs`**.

---

## How classification works

Each candidate `case_type` accumulates a **weighted score** from keyword hits
(English + banglish + Bengali). The highest score wins; ties break by a
**safety-first priority order**: `phishing → wrong_transfer → payment_failed →
refund → other`.

- **Phishing** is only triggered when a credential word (OTP/PIN/password/CVV/card)
  co-occurs with a request/contact context (*"someone **called asking**…"*), **or** an
  explicit scam/fraud word appears. So *"I forgot my password"* is **not** flagged.
- **Severity** maps from case type: phishing → `critical`, wrong transfer & failed
  payment → `high`, refund → `low` (or `medium` when contested), other → `low`.
- **Department** follows the case type; a contested refund is routed to
  `dispute_resolution`.
- **`human_review_required`** is `true` for any phishing or critical case.
- **`confidence`** scales with the winning score and its margin over the runner-up.

### Safety Rule (section 5) — enforced in code
The `agent_summary` is generated from neutral templates that **never** name a
PIN/OTP/password/card, and is then passed through `enforce_summary_safety()`, which
redacts any imperative that asks a customer to share a secret. This is covered by an
automated test (`test_safety_rule_summary_never_requests_secrets`).

---

## Reliability & security

The service is built to stay up and stay quiet about its internals:

- **Resilient endpoint** — if classification ever raises, the request degrades to
  a safe `other` / `low` response instead of a 500. One bad ticket can't take the
  service down.
- **Clean error contract** — unhandled errors and validation failures return
  structured JSON (`{"error": "...", ...}`), never a stack trace.
- **Request limits** — bodies over `MAX_BODY_BYTES` (64 KB) are rejected with
  `413`; an over-length `message` is rejected with `422`. Cheap protection against
  oversized / abusive payloads. Both are env-tunable.
- **Security headers** on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- **Privacy-aware logging** — logs record the classification *outcome* (ticket id,
  case, severity), **never the raw message**, which may contain personal data or the
  very credentials (OTP/PIN) we are protecting.
- **No secrets, non-root container, pinned dependencies.** `/docs` can be disabled
  in production with `ENABLE_DOCS=false`.

| Variable             | Default | Purpose                                   |
|----------------------|---------|-------------------------------------------|
| `MAX_BODY_BYTES`     | `65536` | Max request body size before `413`        |
| `MAX_MESSAGE_LENGTH` | `10000` | Max `message` characters before `422`     |
| `ENABLE_DOCS`        | `true`  | Set `false` to hide `/docs`, `/openapi.json` |
| `LOG_LEVEL`          | `INFO`  | Logging verbosity                         |

## Public sample cases — verified

| # | Message                                               | case_type                        | severity |
|---|-------------------------------------------------------|----------------------------------|----------|
| 1 | I sent 3000 to wrong number                           | `wrong_transfer`                 | high     |
| 2 | Payment failed but balance deducted                   | `payment_failed`                 | high     |
| 3 | Someone called asking my OTP, is that bKash?          | `phishing_or_social_engineering` | critical |
| 4 | Please refund my last transaction, I changed my mind  | `refund_request`                 | low      |
| 5 | App crashed when I opened it                          | `other`                          | low      |

All five are asserted in `tests/test_api.py`.

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest -q
```

20 tests cover all five sample cases, the safety rule, the `ticket_id` echo,
request validation, lenient channel/locale handling, and Bengali / banglish input.

---

## Deployment runbook

The single `Dockerfile` runs anywhere. It binds to `0.0.0.0` and reads `$PORT`, which
every managed platform injects automatically. Health-check path is **`/health`**.

### Render (Blueprint — included)
1. Push this repo to GitHub.
2. Render dashboard → **New + → Blueprint** → select the repo. `render.yaml` is
   detected automatically (Docker runtime, health check `/health`).
3. Deploy. Your base URL is `https://<service>.onrender.com`.

### Railway
1. **New Project → Deploy from GitHub repo.**
2. Railway detects the `Dockerfile`. No build config needed; `$PORT` is injected.
3. Deploy → **Settings → Networking → Generate Domain** for a public HTTPS URL.

### Fly.io
```bash
fly launch --no-deploy        # detects the Dockerfile; keep the generated fly.toml
fly deploy
```
Fly provides HTTPS on `https://<app>.fly.dev`. Ensure the internal port is `8000`.

### Plain VM / EC2 / Poridhi Lab (Docker)
```bash
git clone <your-repo-url> && cd SUST_MOCK
docker compose up --build -d
# Front it with Caddy/Nginx (or the platform's TLS) to get public HTTPS.
```

### Verify any deployment
```bash
curl https://<your-base-url>/health
curl -X POST https://<your-base-url>/sort-ticket \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id":"T-001","message":"I sent 3000 to wrong number"}'
```

---

## Configuration & secrets

| Variable | Default | Purpose                     |
|----------|---------|-----------------------------|
| `PORT`   | `8000`  | HTTP bind port              |

There are **no secrets** to manage. See `.env.example`; never commit a real `.env`
(it is git-ignored).

---

## Project layout

```
.
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app — GET /health, POST /sort-ticket
│   ├── models.py        # Pydantic request/response contracts
│   └── classifier.py    # Deterministic rules-based classification engine
├── tests/
│   ├── test_api.py        # HTTP contract + sample cases + safety rule
│   └── test_classifier.py # Unit / edge cases (Bengali, banglish, ...)
├── Dockerfile
├── docker-compose.yml
├── render.yaml
├── .devcontainer/devcontainer.json
├── requirements.txt           # runtime
├── requirements-dev.txt       # + tests
└── README.md
```

---

## Submission notes

- **LLM used:** No — fully rules-based and deterministic.
- **GPU:** None required.
- **Secrets in repo:** None.
- **Deployment platform:** Docker image (Render blueprint included; portable to Railway / Fly / EC2 / Poridhi Lab).
- **Known issues:** Classification is keyword-driven, so highly unusual phrasings may
  fall back to `other`; extending the keyword banks in `app/classifier.py` is trivial.
