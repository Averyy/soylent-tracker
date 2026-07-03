# Rules

## Frontend Work

- **Always invoke the `frontend-design` skill** for any frontend/UI work (new pages, components, styling changes)
- **Follow `docs/branding-style-guide.md`** -- read it before any visual changes. It defines colors, typography, layout, component styles, and CSS/template architecture rules. **Update it** when introducing new shared classes, variables, or patterns
- HTMX for server interactions, Alpine.js for client-side -- no other JS frameworks
- No build step. CDN scripts only. CSS in `static/css/style.css`
- Partials in `templates/partials/` for HTMX endpoints

## Browser / Playwright

**NEVER use Playwright MCP tools** unless explicitly asked to manually review something in the browser. No screenshots, no navigation, no snapshots. Just open files with `open` if needed.

## Architecture

- **Single container** -- checkers run as background threads in the web process via `lib/scheduler.py`
- Scheduler uses a dedicated `ThreadPoolExecutor` (isolated from FastAPI's default executor), sized from the `_CHECKERS` registry (one worker per checker -- an infinite loop occupies its worker forever, so an undersized pool silently never starts the extra checkers)
- Checker intervals configured via `SOYLENT_CHECK_INTERVAL` / `AMAZON_CHECK_INTERVAL` env vars (seconds, 0 = disabled, min 10)
- **Two-tier health:** `/health` is a static 200 liveness probe (the Docker healthcheck). `/health/checkers` is a readiness probe returning 503 when a checker's newest `last_checked` is older than 3× its interval. Keep them separate -- wiring Docker's healthcheck to checker freshness would restart the web process on an upstream (soylent.ca/Amazon) outage
- Admin auth is cookie-based (`ADMIN_COOKIE`), fully separate from user auth -- no `users.json` entry needed for admin
- **`--forwarded-allow-ips "*"`** in Dockerfile is intentional -- container is only reachable via Docker network (Caddy) and localhost, never directly exposed. Do NOT restrict to `127.0.0.1` -- Caddy connects from Docker bridge (172.18.0.x), not localhost
- **`get_client_ip()` parses X-Forwarded-For rightmost-untrusted**, NOT leftmost (the trust of Docker-bridge/localhost peers stays). Leftmost is client-forgeable; taking it let an attacker spoof `X-Forwarded-For: 127.0.0.1` on a honeypot path to ban loopback/bridge = full-site DoS. `ban_middleware` also refuses to ban any trusted-proxy IP. Do NOT revert to leftmost
- **`stop_grace_period: 40s`** in both compose files is intentional -- above the app's 30s graceful checker-shutdown budget (`app.py` lifespan). Docker's 10s default would SIGKILL mid-check

## Testing & Guardrails

- After any Python changes: `uv run python -c "from app import app"` to verify imports
- After lib/ changes: `uv run python -m lib.soylent_checker` and `uv run python -m lib.amazon_checker` to verify checkers still run
- Dev server: `uv run uvicorn app:app --host 0.0.0.0 --port 8745 --reload`
- Dev login (requires `DEV_MODE=1`): phone `5555555555`, code `5555`
- Tests: `uv run pytest`

### What to test

- **Yes:** parsing logic, state transitions, auth guards, input validation, data pipelines with real inputs
- **No:** stdlib/library behavior (e.g. "HMAC is deterministic"), trivial getters, hardcoded-in hardcoded-out
- Tests must exercise **our code paths** and verify **outputs that could regress**
- Never send real SMS/notifications in tests -- mock or skip external services

## HTTP Client

- `lib/http_client.py` -- thin wrapper around `wafer.SyncSession` (TLS fingerprinting, challenge solving, retry, rate limiting all handled by wafer internally)
- **Never set** `Sec-Ch-Ua*`, `Sec-Fetch-*`, `Accept-Encoding`, `Accept-Language` manually -- wafer generates these from the active TLS fingerprint
- **wafer `timeout=` is the TOTAL call budget** (retries, rotations, AND rate-limit waits), not per-attempt. The session sets `timeout=60, attempt_timeout=10` -- attempt_timeout bounds each try so rotations actually fire. Don't pass a small per-request `timeout=` to `fetch()`; it would starve the Amazon rate wait (up to 12s)
- Amazon checker uses `HttpClient(rate_limit=5.0, rate_jitter=7.0)` for built-in inter-request delays
- Soylent checker passes `Accept: application/json` header for Shopify JSON API
- wafer raises typed exceptions: `ChallengeDetected`, `EmptyResponse`, `WaferError` -- catch these around `client.fetch()` calls

## Conventions

- lib/ modules use relative imports (`.http_client`, `.state`, etc.)
- State files (state.json, users.json) live at project root, are gitignored
- No `from __future__ import annotations` -- Python 3.14 has native support
