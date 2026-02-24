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
- Scheduler uses a dedicated `ThreadPoolExecutor` (isolated from FastAPI's default executor)
- Checker intervals configured via `SOYLENT_CHECK_INTERVAL` / `AMAZON_CHECK_INTERVAL` env vars (seconds, 0 = disabled, min 10)
- Admin auth is cookie-based (`ADMIN_COOKIE`), fully separate from user auth -- no `users.json` entry needed for admin

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

## Debugging

**NEVER blame external services** (Claude, Anthropic, Google, Reddit, etc.) for issues. The problem is in THIS codebase. Investigate our code first, add logging, find the real cause.

## Web Fetching & Search

**ALWAYS use fetchaller MCP tools** (`mcp__fetchaller__*`) instead of WebFetch/WebSearch. No domain restrictions, bypasses bot protection.

- `mcp__fetchaller__fetch` -- any URL as markdown (`raw: true` for HTML)
- `mcp__fetchaller__search` -- web search
- `mcp__fetchaller__browse_reddit` / `search_reddit` -- Reddit
- Fallback: `curl` via Bash

Exception: prefer dedicated MCP tools for specific services (e.g., `gh` CLI for GitHub).

## HTTP Client

- `lib/http_client.py` -- thin wrapper around `wafer.SyncSession` (TLS fingerprinting, challenge solving, retry, rate limiting all handled by wafer internally)
- **Never set** `Sec-Ch-Ua*`, `Sec-Fetch-*`, `Accept-Encoding`, `Accept-Language` manually -- wafer generates these from the active TLS fingerprint
- Amazon checker uses `HttpClient(rate_limit=5.0, rate_jitter=7.0)` for built-in inter-request delays
- Soylent checker passes `Accept: application/json` header for Shopify JSON API
- wafer raises typed exceptions: `ChallengeDetected`, `EmptyResponse`, `WaferError` -- catch these around `client.fetch()` calls

## Conventions

- Python package manager: `uv` (never pip)
- All Python runs via `uv run`
- lib/ modules use relative imports (`.http_client`, `.state`, etc.)
- State files (state.json, users.json) live at project root, are gitignored
- No `from __future__ import annotations` -- Python 3.14 has native support
