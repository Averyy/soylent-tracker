# Invitation System + SMS Consent Checkbox

## Context
Currently only admins can add users. We want to let users invite others, with admin-controlled per-user invite limits. Since this opens signup, new users must consent to SMS on first login.

## User Schema Addition
New fields on user objects in `users.json`:
- `invite_limit`: int, default 0. Admin-controlled max invites this user can send.
- `invited_by`: phone string. Set once at creation when invited by another user.
- `sms_consent`: bool. Set on first login via checkbox. Existing users and admin-added users are auto-set to `true`.

## Changes

### 1. `lib/users.py` — Add invite helpers

- `count_invites(phone) -> int` — counts users with `invited_by == phone` (read-only via `read_json_snapshot`)
- `add_invited_user(inviter_phone, invitee_phone, name) -> str | None` — returns `None` on success, or `"self_invite"` / `"already_exists"` / `"no_invites"` / `"name_required"`. Runs entirely inside `locked_users()`. Checks `invite_limit` vs `count_invites` under lock. New user gets `invite_limit: 0` (admin must grant). Name is required, max 15 chars.
- Update module docstring

### 2. `app.py` — New routes + consent flow

**`POST /invite`** — HTMX endpoint
- Session + CSRF checks
- Normalize phone, call `add_invited_user()`
- Return `partials/invite_section.html`

**`POST /admin/set-invites`** — Admin endpoint
- Accept `phone` and `invite_limit` (int, 0-99)
- Update user's `invite_limit` under `locked_users()`
- Return `partials/admin_users.html`

**Modify `dashboard()`** (line 345):
- Pass `invite_count`, `invite_limit`, `invites_remaining` to template

**Modify `POST /send-code`** (line 268):
- Accept `sms_consent: str = Form("")` param
- After `find_user(phone)` succeeds, check `user.get("sms_consent")` — if not yet consented, require `sms_consent == "on"` or reject with error
- On success, record `sms_consent: true` on the user under `locked_users()` before proceeding to OTP
- No changes needed to verify.html or otp_form.html — consent is handled before OTP

### 3. `templates/login.html`
- Add consent checkbox below the phone input, conditionally shown only for users who haven't consented yet
- Since we can't know if the phone is a new user before form submit, **always show the checkbox** — the server ignores it for already-consented users
- Text: "I agree to receive text messages for login codes and my selected stock update notifications. Msg & data rates may apply."
- Unchecked by default (user must actively check it)

### 4. `templates/dashboard.html`
- Add invite section after product groups, only if `invite_limit > 0`:
  ```html
  {% if invite_limit > 0 %}
  <div class="products-section" id="invite-section">
    <h2 class="section-label">Invite Friends</h2>
    <div id="invite-form-wrap">
      {% include "partials/invite_section.html" %}
    </div>
  </div>
  {% endif %}
  ```

### 5. `templates/partials/invite_section.html` (new)
- Shows "X of Y invites remaining"
- Phone input + first name input (required, max 15 chars) + "Invite" button via HTMX (`hx-post="/invite"` → `#invite-form-wrap`)
- If no invites remaining: "You've used all your invites"
- Inline error/success messages

### 6. `templates/partials/admin_users.html`
- Add invite badge next to existing badges (subs, notifs): shows "X invites"
- Small +/- buttons to adjust `invite_limit` via `hx-post="/admin/set-invites"`
- Only show for non-admin users
- Show user source badge: "Admin" for users without `invited_by`, or "via Jo" (inviter's first name) for invited users. Resolve inviter name by looking up `invited_by` phone in the users list.

### 7. `static/css/style.css`
- Invite section styles (`.invite-card`, `.invite-fields`, etc.)
- Consent checkbox styles (`.consent-box`, `.consent-label`)
- `.success-msg` class (green variant of `.error-msg`)
- Admin invite badge +/- button styles

### 8. Error handling
| Case | Message |
|---|---|
| Invalid phone | "Enter a valid 10-digit phone number" |
| Self-invite | "You can't invite yourself" |
| Already exists | "This person already has an account" |
| No invites left | "You've used all your invites" |
| Missing name | "First name is required" |
| No SMS consent | "Please agree to receive text messages to continue" |
| No auth | 403 + HX-Redirect |

Pre-existing users and admin-added users: `sms_consent` set to `true` at creation. `invite_limit` defaults to 0 (absent field treated as 0).

### 9. Other details
- **`/admin/add-user`**: Set `invite_limit: 0`, `invited_by: null`, `sms_consent: true` on new users. Name max 15 chars (down from 50).
- **`/admin/rename-user`**: Max 15 chars (down from 50).
- **Rate limiting on `/invite`**: Apply IP-based rate limiting to prevent spam
- **Inviter removed**: If admin removes the inviter, admin panel shows "via (removed)" instead of "via Jo"
- **Login checkbox always visible but optional for returning users**: Can't know if user is new before submit, so always show it. Server skips consent check if user already has `sms_consent: true`. New users who don't check it get an error.
- **Existing users missing new fields**: Treat missing `sms_consent` as `true` (grandfathered), missing `invite_limit` as `0` via `.get()` defaults. No migration needed.
- **Name field consistency**: All name inputs (admin add, admin rename, invite) capped at 15 chars. Truncate any existing names longer than 15 on next save.

## Verification
1. `uv run python -c "from app import app"`
2. `uv run pytest`
3. Manual test:
   - Admin panel: set invite_limit for dev user via +/- buttons
   - Dashboard: invite section appears, invite a phone number
   - Verify `users.json` has new user with `invited_by`
   - Use all invites, verify limit enforced
   - Log in as invited user — consent checkbox appears on verify page
   - Complete login, verify `sms_consent: true` in `users.json`
