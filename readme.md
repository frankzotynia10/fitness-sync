# Personal Fitness Data Platform

This project is a self-hosted personal performance data platform that unifies:

- **Recovery / readiness** data from Garmin
- **Activity** data from Strava
- **Nutrition** data from MacroFactor via Apple Health + iPhone Shortcuts
- **Strength training structure** from Hevy

All data is normalized into a PostgreSQL database and exposed through a lightweight API layer for analysis, automation, and future AI-assisted coaching.

---

## High-Level Architecture

```text
                ┌──────────────────────────────┐
                │        iPhone Shortcut       │
                │ (Apple Health → Nutrition)  │
                └──────────────┬───────────────┘
                               │
                               ▼
             ┌────────────────────────────────────┐
             │  Internet / Cellular (Public DNS)  │
             │   api.mayfairlabs.net:4443         │
             └──────────────────┬─────────────────┘
                                │
                                ▼
             ┌──────────────────────────────┐
             │        UniFi Router          │
             │                              │
             │  4443 → Reverse Proxy        │
             │    80 → NPM (LetsEncrypt)    │
             └──────────────┬───────────────┘
                            │
                            ▼
        ┌──────────────────────────────────────────┐
        │  Nginx Proxy Manager (10.10.0.11)        │
        │                                          │
        │  HTTPS Termination (LetsEncrypt)         │
        │  Routes → internal services              │
        └──────────────┬───────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────────────────┐
        │     PostgREST (Supabase REST layer)      │
        │         10.10.0.10:3001                  │
        └──────────────┬───────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────────────────┐
        │          PostgreSQL Database             │
        │                                          │
        │  - daily_nutrition                       │
        │  - garmin_daily                          │
        │  - strava_activities                     │
        │  - hevy_routines                         │
        │  - hevy_routine_exercises                │
        │  - hevy_routine_sets                     │
        └──────────────────────────────────────────┘
```

---

## Data Ingestion Pipelines

### 1) Nutrition (MacroFactor → Apple Health → DB)

```text
MacroFactor
    ↓
Apple Health (iPhone)
    ↓
iPhone Shortcut (manual + scheduled at 11:58 PM daily)
    ↓
HTTPS POST → api.mayfairlabs.net:4443
    ↓
Reverse Proxy (Nginx Proxy Manager)
    ↓
PostgREST (/daily_nutrition?on_conflict=date)
    ↓
Postgres (UPSERT by date)
```

**Purpose:** store one row per day with calories, protein, carbs, fat, and timestamps.

---

### 2) Garmin (Recovery / Health Metrics)

```text
Garmin Connect API
    ↓
garmin-sync container
    ↓
Python sync script
    ↓
Postgres (garmin_daily)
```

**Current Garmin fields include:**
- steps
- calories
- resting heart rate
- sleep seconds
- sleep score
- body battery
- training readiness
- HRV
- VO2 max
- stress average / max
- endurance score
- heat acclimation
- weight
- body fat %
- body water
- bone mass
- muscle mass
- respiration metrics

---

### 3) Strava (Activities / Effort)

```text
Strava API
    ↓
strava-sync container
    ↓
Token refresh + activity fetch
    ↓
Postgres (strava_activities)
```

**Current Strava behavior:**
- refresh tokens persisted to file
- recent activities fetched on a schedule
- activities upserted by `strava_activity_id`

---

### 4) Hevy (Program Structure / Strength Training)

```text
Hevy API
    ↓
hevy-sync container
    ↓
Python sync script
    ↓
Postgres:
    - hevy_routines
    - hevy_routine_exercises
    - hevy_routine_sets
```

**Current Hevy scope:**
- routine templates
- exercises within routines
- set prescriptions (reps, weight, type, rest, etc.)

**Planned next Hevy scope:**
- completed workouts
- executed sets / RPE / performance deltas

---

## Network / Reverse Proxy Plumbing

### External Access

```text
Public Internet
    ↓
api.mayfairlabs.net:4443
    ↓
UniFi Port Forward
    4443 → 10.10.0.11:4443
      80 → 10.10.0.11:8081
```

### Reverse Proxy Container Mapping

```text
Host 8081 → NPM container 80
Host 4443 → NPM container 443
```

### Internal Routing

```text
NPM proxy host: api.mayfairlabs.net
    ↓
Forward Host: 10.10.0.10
Forward Port: 3001
```

---

## Core Components

### Infrastructure
- **Unraid** host for containers and storage
- **PostgreSQL** as central datastore
- **PostgREST** as lightweight REST API layer
- **Nginx Proxy Manager** for TLS + public ingress
- **UniFi** router for port forwarding and WAN access
- **DDNS / DNS** for `api.mayfairlabs.net`

### Sync Services
- `garmin-sync`
- `strava-sync`
- `hevy-sync`
- Apple Health data via iPhone Shortcut

---

## Database Model (Current)

```text
                ┌──────────────┐
                │ garmin_daily │
                └──────┬───────┘
                       │
                       ▼
┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐
│ daily_nutrition  │  │ strava_activities│  │   hevy_routines    │
└──────────────────┘  └──────────────────┘  └─────────┬──────────┘
                                                       ▼
                                            ┌───────────────────────┐
                                            │ hevy_routine_exercises│
                                            └─────────┬─────────────┘
                                                      ▼
                                            ┌───────────────────────┐
                                            │   hevy_routine_sets   │
                                            └───────────────────────┘
```

### Key join ideas
- `date` joins `daily_nutrition` ↔ `garmin_daily`
- `activity_date::date` joins `strava_activities` ↔ daily tables
- `hevy_*` tables provide program/routine context for lifting

---

## Scheduling

### iPhone Shortcut
- scheduled for **11:58 PM daily**
- also runnable manually during the day to validate updates
- uses UPSERT so the same day can be updated repeatedly

### Containers
- `garmin-sync` → scheduled loop
- `strava-sync` → scheduled loop
- `hevy-sync` → scheduled loop (longer interval is fine for routines)

---

## Why This Exists

The goal is to create a unified personal performance datastore that can answer questions like:

- How did recovery line up with this week's work?
- Did I underfuel today’s ride?
- What changed before a bad session?
- What is the best exercise substitution that fits the current block?
- Should tomorrow be heavy, moderate, or easy based on recent data?

---

## Planned AI Layer (Claude)

The future Claude layer is intended to support **three roles**:

### 1. Analyst
Examples:
- “How did my recovery line up with this week’s work?”
- “Did I underfuel today’s ride?”
- “What changed before my bad session?”

### 2. Program Editor
Examples:
- “Swap hanging leg raises for something that fits this block.”
- “Reduce lower-body fatigue without changing the goal of the day.”
- “Adjust the next upper day based on recent soreness/load.”

### 3. Coach
Examples:
- “What should I train tomorrow?”
- “Should I keep intensity or back off?”
- “What’s the smartest adjustment this week?”

### Recommended rollout
1. **Analyst** first
2. **Program Editor** second
3. **Coach** last

This keeps the system useful without becoming chaotic too early.

---

## Example Query Ideas

### Nutrition vs Recovery
```sql
select
  gd.date,
  gd.training_readiness,
  gd.hrv,
  gd.sleep_score,
  dn.calories,
  dn.protein_g,
  dn.carbs_g,
  dn.fat_g
from garmin_daily gd
left join daily_nutrition dn on gd.date = dn.date
order by gd.date desc;
```

### Activity vs Recovery
```sql
select
  sa.activity_date,
  sa.name,
  sa.sport_type,
  sa.kilojoules,
  sa.average_heartrate,
  gd.training_readiness,
  gd.hrv,
  gd.sleep_score
from strava_activities sa
left join garmin_daily gd
  on gd.date = (sa.activity_date at time zone 'UTC')::date
order by sa.activity_date desc;
```

---

## Operational Notes

- PostgREST is currently exposed internally and proxied publicly through NPM.
- Apple Health ingestion was tested both locally and externally.
- Nutrition ingestion uses UPSERT on `date`.
- Strava tokens are persisted to file and refreshed automatically.
- Hevy routines are currently easier to ingest than completed workouts because routines already exist before exercise sessions are logged.

---

## Next Steps

- add completed Hevy workouts + executed set data
- create Claude-friendly SQL views
- formalize prompt templates for Analyst / Editor / Coach
- optionally tighten API auth in front of PostgREST

---

## Repository Purpose

This repository is the plumbing and data foundation for a self-hosted, AI-assisted personal coaching platform.

It is **not** meant to replace Garmin, Strava, MacroFactor, or Hevy.
Instead, it creates a **single source of truth** that makes all of them more useful together.
