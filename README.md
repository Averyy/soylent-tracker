# Soylent Stock Tracker

Stock tracker for Soylent products on soylent.ca and Amazon.ca. Sends SMS notifications via Twilio when products come back in stock.

## Tech Stack

Python 3.14, uv, FastAPI, Jinja2, HTMX + Alpine.js, curl_cffi, Twilio SMS, JSON files for state, Docker Compose for deployment.

## Project Structure

```
app.py                      FastAPI web app (routes)
lib/
  auth.py                    sessions, CSRF, OTP, rate limiting
  products.py                product grouping + sort logic
  config.py                  centralized config + env var overrides
  http_client.py             curl_cffi with TLS fingerprint rotation
  notifications.py           Twilio SMS sending + stock alert formatting
  state.py                   state.json read/write with file locking
  users.py                   users.json management
  soylent_checker.py         Shopify stock checker (soylent.ca)
  amazon_checker.py          Amazon.ca stock checker
  registry.py                product classification + display names
  products.json              product registry (manual overrides)
  history.py                 stock change history
  helpers.py                 shared utilities
  file_lock.py               file-based locking
templates/                   Jinja2 templates
  partials/                  HTMX fragments
static/
  css/style.css              all styles
  js/wallpaper.js            animated bottle wallpaper
  js/otp.js                  OTP input handler
```

## Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Login page |
| POST | `/send-code` | Send SMS verification code |
| POST | `/verify` | Submit verification code |
| GET | `/tracker` | Stock status + subscription toggles |
| GET | `/buy` | Where to buy links |
| POST | `/subscribe` | Toggle product subscription (HTMX) |
| POST | `/toggle-notifications` | Toggle notifications (HTMX) |
| POST | `/logout` | Clear session |
| GET | `/admin` | Admin login page |
| GET | `/admin/dashboard` | Admin dashboard (users, SMS stats, history) |
| POST | `/admin/add-user` | Add user (HTMX) |
| POST | `/admin/remove-user` | Remove user (HTMX) |
| POST | `/admin/rename-user` | Rename user (HTMX) |
| POST | `/admin/test-sms` | Send test SMS to admin |
| POST | `/admin/test-notify` | Send test notification to admin |
| GET | `/health` | Health check |

## Commands

```bash
uv sync                                                       # install deps
uv run uvicorn app:app --host 0.0.0.0 --port 8745 --reload   # dev server
uv run python -m lib.soylent_checker                          # run shopify checker
uv run python -m lib.amazon_checker                           # run amazon checker
uv run pytest                                                 # run tests
```

Dev login bypass (requires `DEV_MODE=1`): phone `5555555555`, code `5555`.

## Deployment

Docker Compose runs three containers: web server, soylent checker (every 60s), and amazon checker (every 20min). CI/CD via GitHub Actions builds and deploys on push to main.

```bash
docker compose up -d        # start all services
docker compose logs -f      # follow logs
docker compose pull && docker compose up -d   # update
```

Requires a `.env` with `ADMIN_PHONE`, `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`, `TWILIO_API_SECRET`, and `TWILIO_FROM`.

### Security Hardening

**.env permissions** -- restrict access to the secrets file:
```bash
chmod 600 .env
```

**SSH deploy keys** -- if using SSH-based deployment, restrict the deploy key to only the commands it needs. In `~/.ssh/authorized_keys` on your VPS:
```
command="cd /opt/soylent-tracker && docker compose pull && docker compose up -d",no-port-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA... deploy@ci
```

**Data files** live on a Docker volume mounted at `/app/data`. Back up `state.json`, `users.json`, and `history.json` regularly.
