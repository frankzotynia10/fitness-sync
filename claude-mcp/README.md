# Claude MCP Service (`claude-mcp`)

This service exposes the fitness database to Claude using **MCP (Model Context Protocol)** over HTTP.

It is the **intelligence layer** of the project:

- connects to Postgres in **read-only** mode (write access via dedicated write user for proposal tools)
- exposes curated tools to Claude
- supports **OAuth / WorkOS AuthKit** for remote access
- runs behind a reverse proxy for external Claude connector access

---

## Recent tool additions
- `bulk_apply_weight_corrections` — batch lb-to-kg unit conversion, single DB transaction
- `bulk_propose_and_apply` — batch training change proposals with audit trail, single tool call
- `get_routine_summary` — all 4 routines in one call with lbs display
- `get_weekly_coaching_context` — single-call weekly planning context (recovery + load + rides + strength + nutrition)

---

## What this service does

The `claude-mcp` service sits between Claude and your Postgres database.

### Data sources exposed through tools
- Garmin daily recovery / readiness / body composition
- Strava activities, power summaries, and stream-derived analytics
- Hevy routines, workouts, progression, and fatigue analytics
- Nutrition and combined daily context

### Why MCP is useful here
Instead of pasting data into Claude manually, Claude can call tools like:

- `get_recent_garmin_daily`
- `get_recent_strava_activities`
- `get_recent_hevy_workouts`
- `get_weekly_strength_volume`
- `get_underfueling_signals`

This turns the project into a **live coaching / analysis system** instead of a static note-taking workflow.

---

## Core architecture

```text
Postgres
   ↓
FastMCP / app.py
   ↓
Reverse proxy / public URL
   ↓
Claude custom connector
```

Internally, the service:
- uses a **read-only database user** for all read tools
- uses a **write database user** for proposal / routine update tools
- exposes only the tools defined in `app.py`
- can also include controlled schema exploration / read-only SQL tools

---

## Authentication / OAuth setup

This project uses **WorkOS AuthKit** with FastMCP for authentication.

### In `app.py`
The MCP server is initialized with AuthKit when the required environment variables are present:

- `WORKOS_AUTHKIT_DOMAIN`
- `BASE_URL`

If those variables are missing, the service can still run without auth for local-only usage.

---

## Required environment variables

Create:

```text
claude-mcp/.env
```

Example:

```env
DB_HOST=your_db_host
DB_PORT=5432
DB_NAME=postgres
DB_USER=claude_reader
DB_PASSWORD=your_read_only_password

DB_WRITE_HOST=your_db_host
DB_WRITE_PORT=5432
DB_WRITE_NAME=postgres
DB_WRITE_USER=claude_writer
DB_WRITE_PASSWORD=your_write_password

WORKOS_AUTHKIT_DOMAIN=your-workos-authkit-domain
BASE_URL=https://your-public-mcp-domain
```

### Notes
- `DB_USER` should be your **read-only DB user** (for example `claude_reader`)
- `DB_WRITE_USER` should be your **write DB user** for proposal/routine tools
- `BASE_URL` should be the externally reachable base URL for the MCP service
  - example: `https://ai.example.com`

---

## Read-only database model

This service is designed to be **read-only** for data access tools.

### Safety model
- database session is opened with `readonly=True`
- SQL guard only permits `SELECT` / `WITH`
- destructive statements are blocked
- curated tools are preferred over raw SQL exploration

### Typical DB access model
Recommended pattern:
- create a dedicated user like `claude_reader`
- grant only `SELECT` on required tables/views
- expose higher-value views for derived analytics

---

## Important analytics / views expected by the service

Depending on which tools you enabled, `app.py` may expect views like:

### Garmin / recovery
- `garmin_daily`

### Strava
- `strava_activities`
- `strava_activity_best_efforts`
- `strava_power_curve_simple`
- `strava_activity_stream_points`
- `activity_recovery_daily`

### Hevy
- `hevy_routines`
- `hevy_routine_context`
- `hevy_workouts`
- `hevy_workout_context`
- `hevy_weekly_volume`
- `hevy_exercise_progression`
- `hevy_muscle_group_fatigue`

### Nutrition / combined
- `daily_nutrition`
- `nutrition_recovery_daily`
- `daily_training_nutrition_context`
- `daily_underfueling_signals`

If any of these do not exist yet, the corresponding MCP tool should return a helpful message instead of crashing.

---

## Typical tool categories in `app.py`

### Generic schema exploration
- `list_available_datasets`
- `describe_dataset`
- `preview_dataset`
- `query_readonly`
- `search_columns`

### Garmin / recovery tools
- `get_recent_garmin_daily`
- `get_sleep_trend`
- `get_sleep_stage_trend`
- `get_hrv_trend`
- `get_vo2max_history`
- `get_training_load_history`
- `get_recovery_signals`
- `get_weekly_coaching_context` ← single-call weekly planning

### Strava tools
- `get_recent_strava_activities`
- `get_strava_activity_detail`
- `get_recent_ride_power_summary`
- `get_recent_power_curve`
- `get_activity_best_efforts`

### Hevy tools
- `get_hevy_routine_names`
- `get_hevy_routine_detail`
- `get_routine_summary` ← all routines in one call with lbs
- `get_recent_hevy_workouts`
- `get_hevy_workout_detail`
- `get_weekly_strength_volume`
- `get_strength_progression`
- `get_muscle_group_fatigue`

### Program change / write tools
- `create_program_change_proposal`
- `approve_and_apply_program_change_proposal`
- `approve_and_apply_program_change_proposals`
- `bulk_propose_and_apply` ← batch training changes, single call
- `bulk_apply_weight_corrections` ← batch unit conversion, single call

### Nutrition / combined context
- `get_nutrition_history`
- `get_nutrition_for_date`
- `get_recent_nutrition_recovery`
- `get_daily_training_nutrition_context`
- `get_underfueling_signals`

---

## Running locally

From the `claude-mcp/` directory:

```bash
python app.py
```

Default service binding:

```text
http://0.0.0.0:8000
```

---

## Docker usage

### Build image

```bash
docker build -t claude-mcp:latest .
```

### Run container

```bash
docker run --env-file .env -p 8000:8000 claude-mcp:latest
```

If you are using a different published host port, adjust accordingly.

Example:

```bash
docker run --env-file .env -p 8002:8000 claude-mcp:latest
```

---

## Reverse proxy / public access

For Claude Desktop or a remote MCP connector, this service is typically exposed through a reverse proxy.

Typical pattern:

```text
public domain / reverse proxy
   → claude-mcp container
   → internal port 8000
```

Example effective public endpoint:

```text
https://ai.example.com/mcp
```

Or if using a custom external port:

```text
https://ai.example.com:4443/mcp
```

---

## Claude connector setup

In Claude Desktop (or other MCP-compatible client), add a custom connector using your public MCP URL.

Example:

```text
https://ai.example.com/mcp
```

If using a custom public port:

```text
https://ai.example.com:4443/mcp
```

Claude will discover the tools exposed by `app.py` automatically.

---

## Expected deployment pattern

Typical deployment for this project:

1. Postgres is running and populated by Garmin / Strava / Hevy / Nutrition syncs
2. `claude-mcp` connects using read-only credentials
3. Reverse proxy exposes the MCP endpoint publicly
4. Claude connects through the custom connector
5. Claude calls curated MCP tools for analysis

---

## Example use cases

Once connected, Claude can answer questions like:

- "Am I recovered enough to push tomorrow?"
- "Did I underfuel my harder rides recently?"
- "How is my squat progressing?"
- "Which muscle groups are accumulating the most fatigue?"
- "Was my last ride actually steady aerobic work?"
- "What should I do next week?" ← answered with get_weekly_coaching_context

---

## Common issues

### MCP connector does not see tools
Check:
- the running container is using the latest `app.py`
- the container was rebuilt after edits
- the connector is pointed at the correct public URL
- the MCP server is reachable through the reverse proxy

### Claude says a view/table does not exist
This usually means:
- the underlying analytics view was not created yet
- the service is pointed at a different database than expected
- the DB user lacks `SELECT` on the required relation

### OAuth / auth problems
Check:
- `WORKOS_AUTHKIT_DOMAIN`
- `BASE_URL`
- reverse proxy callback / redirect configuration
- the public URL used by the connector matches the deployed base URL

### Database permission errors
Make sure the read-only user has `SELECT` on:
- base tables used directly by tools
- analytics views used by tools

---

## Git safety

Do **not** commit:
- `.env`
- any private callback URLs containing secrets
- auth credentials
- bearer tokens / secrets copied from logs

Recommended to commit:
- `.env.example`
- `app.py`
- README
- view definitions / migration SQL

---

## Suggested folder contents

```text
claude-mcp/
├── .env.example
├── Dockerfile
├── README.md
├── app.py
└── requirements.txt
```

---

## Role in the overall project

This service is what turns the system from:

```text
data pipeline
```

into:

```text
decision engine
```

The sync services collect data.
The analytics views derive signal.
The MCP service makes that signal usable by Claude.

---

## Recommended next steps

Typical follow-on improvements:
- keep analytics views versioned in Git
- add more Claude-facing tools only when a clear use case exists
- avoid exposing weak / noisy metrics as first-class coaching signals
- continue using curated tools instead of letting Claude rely on raw SQL for everything
