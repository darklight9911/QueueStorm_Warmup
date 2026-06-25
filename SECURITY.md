# Security Policy

## Supported versions

This is a hackathon project; security fixes are applied to the latest commit on
the `main` branch.

| Version            | Supported |
|--------------------|-----------|
| `main` (latest)    | ✅        |
| older commits/tags | ❌        |

## Reporting a vulnerability

If you find a security issue, please **do not open a public GitHub issue.**
Instead, report it privately:

- Use GitHub's **[Private vulnerability reporting](https://github.com/darklight9911/QueueStorm_Warmup/security/advisories/new)**
  (Repository → **Security** tab → *Report a vulnerability*), **or**
- Email the maintainer at the address on the GitHub profile.

Please include: a description of the issue, steps to reproduce, the affected
endpoint, and the potential impact. We aim to acknowledge reports within a few
days and will credit reporters who wish to be named once a fix is released.

## Security posture

This service is a stateless ticket classifier. It stores no data, uses no
database, and requires no secrets or API keys.

**Built-in protections**

- **No secrets in the repository.** Configuration is via environment variables
  only (see `.env.example`); real `.env` files are git-ignored.
- **Privacy-aware logging.** Logs record the classification *outcome* (ticket id,
  case type, severity) and **never the raw customer message**, which may contain
  personal data or the credentials (OTP/PIN/password) we are protecting.
- **Safety rule enforced in code.** The `agent_summary` is built from neutral
  templates and passed through `enforce_summary_safety()`, so a response can never
  instruct a customer to share a PIN, OTP, password, or card number.
- **Request limits.** Oversized bodies are rejected with `413`
  (`MAX_BODY_BYTES`, default 64 KB) and over-length fields with `422`
  (`MAX_MESSAGE_LENGTH`, default 10,000 chars) — basic abuse/DoS protection.
- **Rate limiting.** A per-client fixed-window limiter returns `429` + `Retry-After`
  (`RATE_LIMIT_*`). In-memory by default, or a shared **Redis** limit across instances
  when `REDIS_URL` is set. It **fails open**, so it can never take the API down, and
  `/health` is exempt.
- **Clean error contract.** Unhandled and validation errors return structured
  JSON, never a stack trace or internal details.
- **Security headers** on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- **Hardened container.** The Docker image runs as a non-root user with pinned
  dependencies.
- **Interactive docs** (`/docs`, `/openapi.json`) can be disabled in production
  with `ENABLE_DOCS=false`.

**Out of scope (deploy-time responsibilities)**

- **Transport security (HTTPS/TLS)** is provided by the hosting platform
  (Render / Railway / Fly) or a reverse proxy in front of the service.
- **WAF and volumetric DDoS protection** are best handled at the CDN/platform layer
  (e.g. Cloudflare) in front of the service. The app provides per-client rate
  limiting; edge filtering of large-scale attacks belongs upstream.
- **Authentication** is not implemented: the endpoint performs classification only
  and persists no user data.
