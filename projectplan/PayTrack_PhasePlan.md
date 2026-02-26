# PayTrack — Phase Plan (Implementation Guide)

Defaults used unless changed in Settings:
- **Due soon threshold:** 5 days
- **Daily summary time:** 07:00 (local)
- **Timezone:** America/Los_Angeles
- **Payday anchor:** 2026-01-15 (every 14 days)

---

## Phase 0 — Foundation & Tooling
**Outcome:** Local dev works, DB migrations work, and Docker/Portainer deploy is ready.

### Tasks
- Create repo structure (`app/`, `routes/`, `services/`, `models/`, `templates/`, `static/`, `tests/`).
- Add FastAPI + Jinja2 + HTMX baseline.
- Configure SQLAlchemy + Alembic migrations.
- SQLite tuning:
  - WAL mode (`PRAGMA journal_mode=WAL;`)
  - busy timeout
- Add Dockerfile + docker-compose.yml (Portainer stack) with `/data` volume.
- Add a simple health endpoint and basic logging.
- Add first-run seeding (create default `pay_schedule` and `app_settings` rows when DB is empty).

### Exit criteria
- `alembic upgrade head` succeeds locally.
- `docker compose up` runs and persists `/data/paytrack.db` across restarts.

---

## Phase 1 — Date Engine + Recurrence Engine (Test-first)
**Outcome:** Correct cycle math and recurrence math, proven by tests.

### Tasks
- Implement cycle engine:
  - Payday every 14 days from 2026-01-15
  - cycle_start = cycle_end - 13 days
  - inclusive membership rules
- Implement recurrence engine:
  - one_time, weekly, biweekly, monthly_dom (clamp to last day), yearly
- Create unit tests:
  - cycle boundary tests (on payday, day before, day after)
  - monthly clamping tests (31 → Feb last day)
  - long-range tests (future years)

### Exit criteria
- All date/recurrence tests pass.
- Cycle ranges match expected Thursdays cadence.

---

## Phase 2 — Database Models + Occurrence Generation (90-day horizon)
**Outcome:** Stable occurrences exist and can be queried by cycle.

### Tasks
- Create models + migrations:
  - pay_schedule, payments, occurrences
  - app_settings, notifications, notification_log
- Implement generate-ahead service (90 days):
  - idempotent generation (no duplicates)
  - only active payments generate occurrences
  - store expected_amount snapshot on occurrence
- Add “run generation” at startup and once daily (scheduler or daily guard).
- Add tests for generation idempotency and correctness.

### Additional required guardrails
- Add `job_runs` table to enforce idempotent daily jobs:
  - UNIQUE(job_name, run_date)
- Guard these jobs against restarts:
  - generate occurrences ahead
  - daily summary send
- Enforce occurrence uniqueness to prevent duplicate schedules:
  - at minimum UNIQUE(payment_id, due_date) for scheduled tracking

### Exit criteria
- Add payments → occurrences appear for next 90 days.
- Restart app → no duplicates created.

---

## Phase 3 — State Transitions (Actions) + Validation
**Outcome:** Mark Paid / Skip / Paid Off behave correctly.

### Tasks
- Mark Paid:
  - default amount_paid = expected_amount
  - paid_date default today; editable
  - status=completed
  - edit and undo supported
- Skip this cycle:
  - status=skipped
  - does not create a new occurrence
- Paid Off:
  - payment.is_active=false, paid_off_date=today
  - future scheduled occurrences → status=canceled
  - add Reactivate action (optional, low cost)
- Add input validation:
  - non-negative amounts
  - due_date must be a valid date
  - required fields present
- Add service/unit tests for totals semantics:
  - "Paid this cycle" uses `paid_date` (cash-flow view)
  - "Scheduled/Remaining" use `due_date` and status (obligation view)
  - overdue item paid in a later cycle updates both cycles correctly

### Locked behavior for Payment edits (Required)
- When a payment is edited (amount/recurrence/initial due date):
  - past and completed/skipped/canceled occurrences never change
  - future scheduled occurrences are rebuilt from today → today+90 days

### Exit criteria
- All actions update cycle lists and totals as defined.
- History retains completed/skipped/canceled records.

---

## Phase 4 — MVP UI (Pages + HTMX actions)
**Outcome:** Full end-to-end user experience works.

### Pages
- Dashboard (This Pay Cycle)
- Preview (Next Pay Cycle)
- History
- Payments (CRUD + Paid Off + archived toggle)
- Settings (pay schedule, notifications, Telegram)
- Notifications Center

### Tasks
- Build templates using the provided design mock as baseline.
- Dashboard totals:
  - Scheduled this cycle
  - Paid this cycle
  - Skipped this cycle
  - Remaining this cycle
- Implement HTMX endpoints for:
  - mark paid modal submit
  - skip confirm
  - edit/undo
- Implement search/filter on History.
- Implement “Show archived” toggle in Payments list.

### Exit criteria
- A user can add payments, see dashboard totals, mark paid, skip, and view history.

---

## Phase 5 — Notifications (In-app + Telegram) with Anti-dup
**Outcome:** Helpful notifications without spam.

### Tasks
- In-app notifications:
  - Due soon (<= 5 days)
  - Overdue (due_date < today, scheduled)
  - Daily summary record (optional notification type)
- Telegram:
  - Settings: bot token + chat id
  - “Send test message” button
  - Daily summary at 07:00 local
  - Overdue and due-soon notifications (dedup by notification_log)
- Verify outbound connectivity from Portainer host.

### Reliability requirements
- Use `notification_log` + `job_runs` so Telegram messages do not duplicate after restarts.
- On Telegram send failure:
  - log the error
  - do not tight-loop retry
  - surface a warning in Settings UI

### Exit criteria
- Telegram test works.
- Daily summary sends once per day.
- Due-soon/overdue messages do not duplicate on restart.

---

## Phase 6 — Hardening & Ops Quality
**Outcome:** Safe, maintainable long-running service.

### Tasks
- Add optional simple auth gate (LAN still benefits).
- Add backup guidance:
  - stop container → copy `/data/paytrack.db`
  - optional “export backup” endpoint later
- Add structured logging for:
  - generation runs
  - notification runs
  - action audit events
- Add basic error pages and form validation UX.
- Add performance improvements if needed:
  - indexes on occurrences(due_date,status,payment_id), notifications(is_read,created_at)

### Exit criteria
- App survives restarts, upgrades, and schema changes cleanly.
- Data persists and is easy to back up.

---

## Suggested Build Order (do not skip)
1) Phase 0 (foundation/tooling + migrations + deploy baseline)  
2) Phase 1 (date + recurrence tests)  
3) Phase 2 (models + generate-ahead)  
4) Phase 3 (actions: paid/skip/paid off + totals validation)  
5) Phase 4 (UI wiring)  
6) Phase 5 (notifications)  
7) Phase 6 (hardening)

---

## Definition of Done (MVP)
- Cycle math anchored on 2026-01-15 and matches “every 2 Thursdays”.
- Dashboard totals match definitions.
- Mark Paid / Skip / Paid Off work and update views instantly.
- Occurrences generated 90 days ahead without duplication.
- In-app + Telegram notifications work with deduping.
- Runs locally and deploys in Portainer with persistent SQLite volume.
