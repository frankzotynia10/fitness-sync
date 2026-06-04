# Fitness Sync Stack

Personal fitness data platform running on a self-hosted Docker stack. Syncs data from Garmin, Hevy, and Strava into PostgreSQL, exposes it via Claude MCP for AI-powered coaching and analytics.

---

## Architecture

```
Garmin Connect  ──► garmin-sync      ──► PostgreSQL
Hevy            ──► hevy-sync        ──► PostgreSQL
                    hevy2garmin      ──► Garmin Connect (enriches activities)
Strava          ◄── strava-sync      ◄── PostgreSQL
                ◄── strava-replace   ──► Strava (uploads enhanced FIT files)
iOS Shortcuts   ──► PostgREST API    ──► PostgreSQL (nutrition)
PostgreSQL      ──► claude-mcp       ──► Claude.ai
```

![Architecture](Fitness-sync-diagram.png)

### Data flow

1. **Garmin sync** pulls daily metrics (HRV, sleep, recovery, body composition) into `garmin_daily`
2. **Hevy sync** pulls workouts and routines into `hevy_workouts`, `hevy_routines`, etc.
3. **hevy2garmin** enriches Garmin strength activities with Hevy set/rep/weight data
4. **Strava FIT replace** downloads the enhanced FIT from Garmin, uploads to Strava — Strava receives enriched activities, not generic ones
5. **Strava sync** pulls activities and power streams from Strava into `strava_activities`
6. **Nutrition** is pushed from an iOS Shortcut via PostgREST into `daily_nutrition`
7. **Claude MCP** exposes all data via read-only tools for AI coaching

---

## Containers

| Container | Role |
|-----------|------|
| `garmin-sync` | Pulls Garmin daily metrics hourly |
| `hevy-sync` | Pulls Hevy workouts every 6 hours |
| `strava-sync` | Pulls Strava activities + power streams hourly |
| [hevy2garmin](https://github.com/frankzotynia10/hevy2garmin) | Enriches Garmin activities with Hevy data every 30 min |
| `claude-mcp` | Read-only MCP server for Claude |
| `supabase-db` | PostgreSQL database |
| `supabase-rest` | PostgREST API (used by iOS nutrition shortcut) |
| `supabase-studio` | Supabase Studio UI for DB inspection |

---

## Automation (n8n)

All orchestration runs in n8n on Apollo (10.10.0.11).

| Workflow | Trigger | What it does |
|----------|---------|---------------|
| Container Sync Schedules | Hourly / 30min / 6h | Runs all sync scripts in order |
| iOS Shortcuts Fitness Sync Trigger | Webhook (iOS Shortcut) | Full sync chain on demand |
| Fitness Stack Deploy | GitHub Actions webhook | Deploys updated containers + runs sync |
| Strava Webhook Handler | Strava activity create event | Runs strava-sync immediately on new activity |
| Container Watchdog | Hourly | Restarts down containers, alerts if still down |
| Sync Staleness Watchdog | Every 6 hours | Alerts if data sources go stale, checks Garmin token health |
| Postgres Weekly Backup | Sunday 23:30 | pg_dump with 30-day retention |
| Weekly Training Digest | Sunday 18:30 | Weekly summary via ntfy |
| Weekly PR Notification | Monday 08:00 | 1RM PR check via ntfy |

Workflow backups are in `n8n/workflows/`.

---

## Infrastructure

- **Andromeda** (10.10.0.10) — runs all containers except nginx
- **Apollo** (10.10.0.11) — runs nginx proxy manager and n8n
- **CI/CD** — GitHub Actions builds images on push to `main`, pushes to GHCR, triggers n8n deploy webhook
- **Monitoring** — ntfy notifications for sync failures, token expiry, container health

---

## Services

- [`garmin/`](garmin/) — Garmin daily sync
- [`strava/`](strava/) — Strava activity sync + FIT upload
- [`hevy/`](hevy/) — Hevy workout sync
- [`claude-mcp/`](claude-mcp/) — Claude MCP server
- [`n8n/`](n8n/) — n8n workflow backups

---

## Setup order (fresh install)

1. Bring up PostgreSQL (Supabase stack)
2. Run SQL grants for `claude_reader` role
3. Configure and start `garmin-sync` — run `login_once.py` for initial auth
4. Configure and start `hevy-sync`
5. Configure and start `strava-sync` — run OAuth flow for initial token
6. Deploy `hevy2garmin` container
7. Configure and start `claude-mcp`
8. Configure n8n workflows
9. Register Strava webhook subscription
10. Set up iOS Shortcuts for manual trigger and nutrition logging

---

## Git safety

Never commit:
- `.env` files
- `tokens/` directories
- `strava_tokens.json`
- `garmin_tokens.json`
