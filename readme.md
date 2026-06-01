# Fitness Sync Stack

Personal fitness data platform that syncs and centralizes data from multiple sources into PostgreSQL/Supabase for analytics and AI access.

## CI/CD

Images are built and pushed to GHCR via GitHub Actions on every push to `main`. On successful build, a webhook triggers n8n to pull and redeploy all containers via SSH.

## What this stack includes

- **Supabase / PostgreSQL**
  - Central database for all synced data
  - Runs in Docker Compose
- **Garmin daily sync**
  - Pulls daily recovery/body metrics from Garmin Connect
  - Stores into `garmin_daily`
- **Strava sync**
  - Pulls recent activities from Strava
  - Stores into `strava_activities`
  - Pulls activity streams for rides
  - Computes and stores power best efforts into:
    - `activity_streams`
    - `activity_best_efforts`
- **Apple Health ingestion**
  - iOS Shortcuts push selected health metrics into the database
- **Hevy sync**
  - Pulls routines and/or workouts into PostgreSQL
- **Claude MCP**
  - Read-only MCP server exposing curated tools and SQL-safe dataset access

---

# Architecture overview

## Data flow

- Garmin daily sync -> `garmin_daily`
- Strava sync -> `strava_activities`
- Strava streams -> `strava_activity_streams` (raw) + `activity_streams` (normalized)
- Best effort computation -> `activity_best_efforts`
- Apple Health shortcut -> health tables/views in PostgreSQL
- Hevy sync -> workout/routine tables
- Claude MCP -> read-only query layer over the DB

## Design notes

- **MCP is read-only**
  - All writes happen in sync containers/scripts
- **Strava is currently the automated source for stream-derived power**
  - Best efforts are computed from Strava stream data
- **Garmin sync currently handles daily metrics only**
  - It is not currently the FIT/activity ingestion source
- **Power best efforts**
  - Currently persisted for:
    - 5 seconds
    - 1 minute
    - 5 minutes
    - 20 minutes

---

# Setup order

Recommended order for a fresh setup:

1. Bring up PostgreSQL / Supabase
2. Configure Strava sync
3. Configure Garmin sync
4. Configure Apple Health shortcut push
5. Configure Hevy sync
6. Configure Claude MCP
7. Run SQL grants for `claude_reader`
8. Validate tables / views / queries

---

# 1. Supabase / PostgreSQL setup

## Overview

This stack uses PostgreSQL as the central database. In this setup it is running via Docker Compose.

## What to document here

- Where the Compose file lives
- Which service/container is the database
- Persistent volume location
- Port mapping
- Database name
- Main DB users/roles:
  - admin / owner role
  - sync/write role(s)
  - `claude_reader` read-only role

## Important notes

- `claude_reader` needs `SELECT` on all tables Claude should access
- New tables created later may require grants unless default privileges are configured

## Required permissions for Claude MCP

Example grants:

```sql
grant usage on schema public to claude_reader;
grant select on all tables in schema public to claude_reader;

alter default privileges in schema public
grant select on tables to claude_reader;
