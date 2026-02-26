# PayTrack — Scope (MVP) [SQLite • LAN-only • Bi-weekly • In-app + Telegram]

## 1) Overview
PayTrack is a self-hosted payment tracking web app that organizes recurring and one-time payments by **bi-weekly pay cycles**, shows what’s **due vs paid per cycle**, supports quick actions (**Mark Paid**, **Skip this cycle**, **Paid Off**), keeps a complete history, and sends **in-app** and **Telegram** notifications.

- **Backend:** Python (FastAPI)
- **DB:** SQLite (persistent volume)
- **Frontend:** Server-rendered HTML (Jinja2) + HTMX (MVP)
- **Deployment:** Docker → Portainer (LAN-only)

---

## 2) Goals
- Load and manage a single shared set of payments (single-user app).
- Define a bi-weekly pay schedule and compute current/next cycles reliably.
- Show payments due by cycle with running totals (due/paid/remaining).
- Capture paid history with **amount paid** and **paid date**.
- Allow “Skip this cycle” and “Paid Off” to remove items from future cycle lists and notifications.
- Provide in-app and Telegram notifications for due-soon/overdue and daily summary.

---

## 3) Non-Goals (Out of Scope for MVP)
- Multi-user accounts, roles, sharing
- Partial payments / split payments
- Bank syncing (Plaid/OFX)
- Complex recurrence rules (business days, “2nd Friday”, etc.)
- Full budgeting/envelope system

---

## 4) Pay Schedule (MVP Defaults + Locked Cycle Rules)
### 4.1 Payday cadence
- Default payday anchor is **January 15, 2026**.
- Payday occurs **every 2 Thursdays** from the configured anchor date.
- Formula: `anchor_payday_date + 14*n days` for integer `n ≥ 0`.
- For MVP, cadence math is locked to a 14-day interval. The anchor date is configurable in Settings (default `2026-01-15`).

### 4.2 Cycle boundaries (inclusive)
- A pay cycle ends on payday (`cycle_end = payday`).
- `cycle_start = cycle_end - 13 days`
- A payment occurrence belongs to a cycle if:
  - `cycle_start <= due_date <= cycle_end`

### 4.3 Timezone
- Default timezone: `America/Los_Angeles`
- Store dates as **local dates** (not timestamps) wherever possible to avoid midnight drift.

---

## 5) Payments & Recurrence (MVP)
### 5.1 Payment fields
- Name
- Expected amount
- Initial due date
- Recurrence type:
  - One-time
  - Weekly
  - Bi-weekly
  - Monthly (day-of-month)
  - Yearly
- Priority (optional)
- Active flag (for Paid Off / archive)

### 5.2 Monthly recurrence rule (Locked)
- If the “desired day-of-month” does not exist in a given month (e.g., 31st in February),
  the due date becomes the **last day of that month**.
- The recurrence retains the desired DOM and returns to it in months where it exists.

---

## 6) Occurrence Model (Locked)
Use a **generate-ahead** model:
- The system generates scheduled occurrences **90 days ahead** for active payments.
- Generation runs:
  - On app startup, and
  - Once per day (scheduled) or via a “first request of the day” fallback.

This enables stable history and consistent “Skip” behavior.

---

## 7) Actions & Statuses
### 7.1 Occurrence statuses
- `scheduled`
- `completed`
- `skipped` (Skip this cycle)
- `canceled` (created by Paid Off)
- `overdue` is derived (recommended): `due_date < today AND status='scheduled'`

### 7.2 Mark Paid
- Defaults: `amount_paid = expected_amount`, `paid_date = today` (both editable)
- Sets `status = completed`
- Stores paid amount and paid date
- Supports edit + undo (revert to scheduled and clear paid fields)

### 7.3 Skip this cycle (Locked behavior)
- Sets `status = skipped`
- Clears the item from “Remaining Due” for this cycle
- Does **not** create a new occurrence for next payday
- The payment appears again only at its **next natural due date** per its recurrence rule

### 7.4 Paid Off (Archive)
- Sets the payment inactive (`is_active=false`) and stores `paid_off_date`
- Cancels future scheduled occurrences (`status=canceled`)
- Removes the payment from:
  - Current/future cycle lists
  - Preview lists
  - Notifications
- History remains visible

---

## 8) Totals & Reporting (Definitions)
On the Dashboard for a selected cycle:

- **Scheduled this cycle (original obligations)**  
  Sum of `expected_amount` for occurrences with `due_date` in cycle and `status in ('scheduled','completed','skipped')`
  (Exclude `canceled` occurrences.)

- **Paid this cycle**  
  Sum of `amount_paid` for occurrences with `paid_date` in cycle and `status='completed'`

- **Skipped this cycle**  
  Sum of `expected_amount` for occurrences with `due_date` in cycle and `status='skipped'`

- **Remaining this cycle**  
  Sum of `expected_amount` for occurrences with `due_date` in cycle and `status='scheduled'`

> Rationale: “Paid this cycle” reflects actual cash flow by paid_date, while “Scheduled/Remaining” reflect obligations by due_date within the selected cycle.

---

## 9) Screens / Pages (MVP)
- **Dashboard (This Pay Cycle)**
  - Due this cycle list (scheduled/overdue)
  - Paid this cycle list
  - Skipped this cycle list
  - Summary totals (scheduled/paid/skipped/remaining)
  - Actions: mark paid, skip, edit/undo

- **Preview (Next Pay Cycle)**
  - Next cycle range + expected outflow + list of due items

- **History**
  - Occurrence table with filters: status, date range, search

- **Payments**
  - CRUD for payments
  - Paid Off + Show archived + Reactivate

- **Settings**
  - Payday anchor (default 2026-01-15) and timezone
  - Notification settings (due soon days, daily summary time)
  - Telegram config + “Send test message”

- **Notifications Center**
  - In-app list, unread badge, mark read

---

## 10) Notifications (MVP)
### 10.1 Types
- **Due soon:** due within **5 days** (default)
- **Overdue:** due_date < today and not completed/skipped/canceled
- **Daily summary:** sent at **07:00 local time** (default)

### 10.2 Channels
- In-app notifications persisted in DB
- Telegram messages via bot token + chat id

### 10.3 Anti-spam
- Maintain `notification_log` to prevent duplicate sends.
- Use a dedup key that works for both occurrence-based and summary notifications.
  - Recommended fields: `(type, channel, bucket_date, occurrence_id NULLABLE, dedup_key TEXT)`
  - Enforce uniqueness with a key that does not rely on nullable `occurrence_id` alone (e.g., `UNIQUE(type, channel, bucket_date, dedup_key)`)
  - Example `dedup_key` values:
    - due-soon/overdue: `occ:{occurrence_id}`
    - daily-summary: `daily-summary`

---

## 11) Tech & Deployment (Portainer)
- SQLite database stored at: `/data/paytrack.db`
- Docker volume mounted to `/data`
- Environment variables:
  - `DATABASE_URL=sqlite:////data/paytrack.db`
  - `TZ=America/Los_Angeles`
  - `DUE_SOON_DAYS=5`
  - `DAILY_SUMMARY_TIME=07:00`
  - `TELEGRAM_BOT_TOKEN=...`
  - `TELEGRAM_CHAT_ID=...`

---

## 12) Operational Guardrails (Idempotency & First-Run Defaults)
### 13.1 Job idempotency (Required)
To prevent duplicates after restarts, scheduled jobs **must be idempotent**.

Add `job_runs` table (or equivalent guard) with a uniqueness constraint:
- `job_name` (TEXT)
- `run_date` (DATE)
- `created_at` (DATETIME)
- **UNIQUE(job_name, run_date)**

Jobs that must be guarded:
- `generate_occurrences_ahead` (daily)
- `send_daily_summary` (daily)

### 13.2 First-run defaults (Required)
On an empty database, the app must automatically create default rows so the UI is usable immediately:
- `pay_schedule` with:
  - anchor_payday_date = **2026-01-15**
  - timezone = **America/Los_Angeles**
- `app_settings` with:
  - due_soon_days = **5**
  - daily_summary_time = **07:00**
  - telegram_enabled = false until configured

Dashboard should display an empty-state prompt:
- “No payments yet — Add your first payment.”

### 13.3 Payment edits after occurrences exist (Locked behavior)
When a Payment is edited (amount, recurrence, or initial due date):
- **Past occurrences never change**
- `completed`, `skipped`, and `canceled` occurrences never change
- **Future scheduled occurrences are rebuilt**:
  - delete or cancel future `scheduled` occurrences for that payment
  - regenerate from **today → today+90 days** using the updated rule

### 13.4 Overdue completion behavior (Locked)
If an occurrence is overdue (derived condition) and the user marks it paid:
- set `status = completed`
- store `paid_date` and `amount_paid`
- remove it from overdue/due lists immediately
- include it in **Paid this cycle** totals based on `paid_date`

### 13.5 Uniqueness constraint for occurrences (Required)
Occurrence generation must not create duplicates.
Enforce uniqueness for active/scheduled tracking:
- Recommended: **UNIQUE(payment_id, due_date)** for occurrences
- If you later need multi-status duplicates, keep uniqueness for `scheduled` occurrences at minimum.

## 13) Acceptance Criteria (MVP)
- Payday/cycle calculation matches “every 2 Thursdays from 2026-01-15”.
- Recurrence engine correctly handles monthly 29/30/31 clamping.
- Occurrences are generated 90 days ahead without duplicates.
- Mark Paid records amount and date; totals update correctly.
- Dashboard totals definitions are internally consistent and never produce a negative "Remaining this cycle" from normal state transitions.
- Skip this cycle removes item from remaining due and does not reschedule.
- Paid Off archives payment, cancels future occurrences, and hides it from cycles.
- Telegram test message works; due-soon/overdue/daily summary respect anti-dup rules.
- Notification dedup works for both occurrence-based notifications and daily summary notifications.
- Daily jobs (occurrence generation and daily summary) are idempotent and do not duplicate after restarts.
- Editing a payment rebuilds only future scheduled occurrences (past/completed/skipped/canceled stay unchanged).
- First-run defaults are auto-seeded (pay_schedule + app_settings) and empty-state UI is shown when no payments exist.
- Occurrence uniqueness is enforced to prevent duplicate schedules.
