# Calendar Sync Service

Pulls events from published ICS feeds (O365, Google, iCloud) into PostgreSQL for the wall display dashboard.

---

## What it syncs

| Table | Contents |
|-------|----------|
| `calendar_events` | Normalized events: title, start/end, all-day flag, location, source calendar |

Recurring events are expanded into individual occurrences within a rolling window (past 7 days / future 90 days by default) rather than stored as raw RRULEs — simplest thing that works for a dashboard that just needs "what's happening now/soon."

All writes use upsert on `(source_cal, event_uid)` — safe to re-run. Recurring instances get a `_<epoch>` suffix appended to their UID since ICS reuses one UID across all occurrences of a series.

---

## Configuration

```env
CALENDAR_FEEDS=fz_o365=https://outlook.office365.com/.../calendar.ics,wife_o365=https://outlook.office365.com/.../calendar.ics,google_frank=https://calendar.google.com/.../basic.ics

DB_HOST=10.10.0.10
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_password

WINDOW_PAST_DAYS=7
WINDOW_FUTURE_DAYS=90
```

`CALENDAR_FEEDS` is a comma-separated list of `name=url` pairs. Add or remove feeds here — no code changes needed to add a calendar.

**Current POC feeds:** `fz_o365`, `wife_o365`, `google_frank`. iCloud (x2) and Google (wife, x1) to be added once those ICS/webcal links are pulled.

---

## Schema

Lives in its own `calendar` schema — kept separate from the fitness tables (`hevy_*`, `garmin_*`, `strava_*`) in `public` so the fitness MCP read-only layer never surfaces family calendar data, and so access control (e.g. wife having write access to calendar but not fitness data) can be scoped per-schema later if needed.

Run once against Supabase before first sync:

```sql
create schema if not exists calendar;

create table if not exists calendar.calendar_events (
    id bigserial primary key,
    source_cal text not null,
    event_uid text not null,
    title text,
    start_time timestamptz not null,
    end_time timestamptz,
    all_day boolean default false,
    location text,
    raw_ics text,
    last_synced timestamptz default now(),
    unique (source_cal, event_uid)
);

create index if not exists idx_calendar_events_start on calendar.calendar_events (start_time);
create index if not exists idx_calendar_events_source on calendar.calendar_events (source_cal);
```

---

## Schedule

Runs via n8n cron, same pattern as Garmin-every-hour. Calendars don't need tight polling — every 15-30 min is plenty. No webhook option for O365/Google/iCloud ICS publish links, so polling is the only option (same situation as Hevy).

---

## Dashboard query

Since this table lives in the `calendar` schema (not `public`), it won't be exposed via PostgREST unless `calendar` is added to Supabase's `db-schemas` config (Settings → API → Exposed schemas). Simplest path for a single wall-display page: connect directly via `psycopg2`/`asyncpg` instead of fighting PostgREST schema exposure for one small use case.

Direct query example:

```sql
select * from calendar.calendar_events
where start_time >= current_date
order by start_time asc;
```

Color-code by `source_cal` client-side.

---

## Files

| File | Purpose |
|------|---------|
| `calendar_sync.py` | Main sync script |
| `Dockerfile` | Container definition |
| `requirements.txt` | Python deps |

---

## Git safety

Do not commit: `.env`, ICS URLs (they're unauthenticated — anyone with the link can read the calendar)
