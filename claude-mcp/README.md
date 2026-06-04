# Claude MCP Server

Read-only MCP (Model Context Protocol) server that exposes the fitness database to Claude. Provides curated tools and SQL-safe dataset access for AI coaching and analytics.

---

## What it exposes

The MCP server gives Claude access to:

- **Garmin daily data** — HRV, sleep, recovery, training load, body composition
- **Strava activities** — rides, strength sessions, walks; power streams and best efforts
- **Hevy workouts** — completed sets, weights, RPE, volume by muscle group
- **Nutrition** — daily macro and calorie data from iOS Shortcut logging
- **Computed views** — weekly volume, fatigue scores, power curve, VO2 max trends, body composition trends

All access is read-only. Writes happen exclusively in sync containers.

---

## Architecture

```
Claude.ai (claude.ai/mcp) ──► MCP server (claude-mcp container) ──► PostgreSQL
```

The MCP server connects to Postgres using a `claude_reader` role with SELECT-only permissions.

---

## Configuration

```env
DB_HOST=10.10.0.10
DB_PORT=5432
DB_NAME=postgres
DB_USER=claude_reader
DB_PASSWORD=your_reader_password

HEVY_API_KEY=your_hevy_api_key
```

---

## Database permissions

The `claude_reader` role needs SELECT on all tables Claude should access:

```sql
GRANT USAGE ON SCHEMA public TO claude_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT ON TABLES TO claude_reader;
```

Run this after adding new tables.

---

## Connecting to Claude.ai

The MCP server is exposed externally via nginx proxy manager. Add it to Claude.ai under Settings → Integrations using the public URL.

The server runs on SSE transport — Claude.ai connects and maintains a persistent session.

---

## Schedule

The container runs continuously (`tail -f /dev/null` keeps it alive). The MCP server process starts on connection from Claude. Deployed and restarted via n8n Fitness Stack Deploy workflow on every push to `main`.

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | MCP server entrypoint |
| `config.py` | DB config and env loading |
| `db.py` | Database connection and query helpers |
| `hevy_api.py` | Hevy API client (used for live routine data) |
| `utils.py` | Shared utilities |
| `tools/` | MCP tool definitions |
| `services/` | Business logic layer |
| `Dockerfile` | Container definition |
| `requirements.txt` | Python deps |

---

## Git safety

Do not commit: `.env`
